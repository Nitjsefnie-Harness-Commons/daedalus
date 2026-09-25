"""Mapping stores, assignment binders and expression resolution for deferred
routing values."""
import ast

from _pyroute_storage import replace_deferred_storage
from _pyroute_stores import (container_copy, dict_length,
                             fold_dynamic, replace_container,
                             store_deferred_target)
from _pyroute_containers import SpreadContainer, iterated_key
from _pyroute_indexing import reversed_read, static_slice_read
from _pyroute_invalidation import CONTAINER_MUTATORS, invalidate_unmodelled
from _pyroute_keys import (_UNRESOLVED_KEY, _UNSAFE_LITERAL, _literal_key,
                           _literal_value, _unhashable_key_sender)
from _pyroute_pop import _unknown_lookup_default
from _pyroute_positions import (alias_target_pairs, at_position,
                                drop_shifted_positions, from_position,
                                sequence_method_value)
from _pyroute_setops import fold_set_operation, set_operands
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,
                             DeferredAlternatives, DeferredClass,
                             DeferredContainer, DeferredGenerator,
                             DeferredInstance, DeferredMethod, _known_value,
                             is_deferred_value, mapping_lookup_owner,
                             merge_yielded, sender_value, sync_cells)


def _selected_values(value, key, attribute=False):
    if isinstance(value, DeferredAlternatives):
        return [selected for item in value.values
                for selected in _selected_values(item, key, attribute)]
    if attribute and isinstance(value, DeferredInstance):
        return [value.attributes.get(key)]
    if attribute and isinstance(value, DeferredClass):
        return [value.methods.get(key)]
    if not attribute and isinstance(value, DeferredContainer):
        return at_position(value, key)
    return []


def _assignment_values(target, value, state):
    resolved = _known_value(value, state) if isinstance(value, ast.AST) \
        else value
    if isinstance(target, (ast.Tuple, ast.List)):
        pairs = alias_target_pairs(target, resolved if is_deferred_value(
            resolved) else value)
        if pairs is not None:
            for nested, item in pairs:
                yield from _assignment_values(nested, item, state)
            return
    yield target, value


def apply_assignment_bindings(targets, value, state, binder):
    entries = [entry for target in targets
               if isinstance(target, (ast.Name, ast.Tuple, ast.List))
               for entry in _assignment_values(target, value, state)]
    aliases = {}
    # Pending RHS references must follow storage mutations, but not rebindings.
    for target, expression in entries:
        state.evaluated[id(target)] = _known_value(expression, state) \
            if isinstance(expression, ast.AST) else expression
        if isinstance(target, ast.Name):
            bindings = ({}, {}, {})
            binder(target, expression, state, bindings)
            aliases[id(target)] = bindings[0]
    for target, _ in entries:
        bindings = ({}, {}, {})
        binder(target, state.evaluated[id(target)], state, bindings)
        bindings[0].update(aliases.get(id(target), {}))
        names = {node.id for node in ast.walk(target)
                 if isinstance(node, ast.Name)
                 and isinstance(node.ctx, ast.Store)}
        for values, updates in zip(
                (state.aliases, state.generators, state.callables), bindings):
            for name in names:
                values.pop(name, None)
            values.update(updates)
        sync_cells(state, names)
    for target, _ in entries:
        state.evaluated.pop(id(target), None)


def _display_value(node, state):
    """Positions after a star of unknown length share the DYNAMIC_KEY slot."""
    items = {}
    index = 0
    for item in node.elts:
        starred = isinstance(item, ast.Starred)
        value = _known_value(item.value if starred else item, state)
        if not starred:
            if index is None:
                fold_dynamic(items, value)
            else:
                if value is not None:
                    items[index] = value
                index += 1
        elif not isinstance(value, DeferredContainer):
            index = None
            fold_dynamic(items, UNPROVABLE_SENDER)
        elif value.kind == 'dict':
            # Unpacking a dict yields its keys, never the modelled values.
            index = (None if index is None or value.length is None
                     else index + value.length)
        elif index is None or value.length is None:
            index = None
            fold_dynamic(items, from_position(value, 0))
        else:
            for offset in range(value.length):
                nested = merge_yielded(at_position(value, offset))
                if nested is not None:
                    items[index + offset] = nested
            index += value.length
    starred_any = any(isinstance(item, ast.Starred) for item in node.elts)
    if isinstance(node, ast.Set) and (items or starred_any):
        # A set has no positions; equal elements collapse at runtime.
        joined = merge_yielded(items.values())
        return DeferredContainer(
            {} if joined is None else {DYNAMIC_KEY: joined}, None, 'set')
    if items or isinstance(node, ast.List) or starred_any:
        return DeferredContainer(items, index, type(node).__name__.lower(),
                                 star_display=index is None)
    return None


def _fold_items(target, source):
    for key, value in source.items():
        if key is DYNAMIC_KEY:
            fold_dynamic(target, value)
        else:
            target[key] = value


def _dict_value(node, state):
    items = {}
    counted = True
    for key, item in zip(node.keys, node.values):
        if key is not None:
            value = _known_value(item, state)
            literal = _literal_key(key, state)
            if literal is not _UNRESOLVED_KEY:
                items[literal] = value
            else:
                fold_dynamic(items, value)
            continue
        value = (_dict_value(item, state) if isinstance(item, ast.Dict)
                 else _known_value(item, state))
        if isinstance(value, DeferredContainer) and value.kind == 'dict':
            _fold_items(items, value.items)
            counted = counted and value.length is not None
        else:
            fold_dynamic(items, UNPROVABLE_SENDER)
    if len(node.keys) == 1 and node.keys[0] is not None:
        return DeferredContainer(items, 1, 'dict', node)  # one entry, one key
    return DeferredContainer(items, dict_length(items, counted), 'dict', node)


def _merge_or_value(node, state):
    """Mapping value of `left | right` from the provable dict sides."""
    items = {}
    counted = True
    for side in (node.left, node.right):
        if isinstance(side, ast.Dict):
            known = _dict_value(side, state)
        else:
            known = _known_value(side, state)
        if isinstance(known, DeferredContainer) and known.kind == 'dict':
            _fold_items(items, known.items)
            counted = counted and known.length is not None
        else:
            fold_dynamic(items, UNPROVABLE_SENDER)
    if not items:
        return None
    return DeferredContainer(items, dict_length(items, counted), 'dict', node)


def _dict_call_value(node, state):
    """Mapping value of a builtin dict() call, or None when untracked."""
    items = {}
    counted = True
    sources = node.args[:1] + [
        keyword.value for keyword in node.keywords if keyword.arg is None]
    for source in sources:
        known = _source_items(source, state)
        if known is None:
            fold_dynamic(items, UNPROVABLE_SENDER)
        else:
            _fold_items(items, known[0])
            counted = counted and known[1]
    for keyword in node.keywords:
        if keyword.arg is not None:
            items[keyword.arg] = _known_value(keyword.value, state)
    if not items:
        return None
    return DeferredContainer(items, dict_length(items, counted), 'dict', node)


def _setdefault_value(node, state):
    """The stored-or-existing item one setdefault call evaluates to."""
    owner = _known_value(node.func.value, state)
    key = _literal_key(node.args[0], state) if node.args else _UNRESOLVED_KEY
    default = _known_value(node.args[1], state) if len(node.args) > 1 else None
    if (not isinstance(owner, DeferredContainer)
            or (key is _UNRESOLVED_KEY and owner.kind != 'dict')):
        return merge_yielded((default, UNPROVABLE_SENDER)) \
            if default is not None else None
    if key is _UNRESOLVED_KEY:
        return merge_yielded((*owner.items.values(), default,
                              _unhashable_key_sender(
                                  node.args[0] if node.args else None,
                                  state)))
    return _mapping_lookup(owner, key, default)


def _mapping_lookup(owner, key, default):
    if DYNAMIC_KEY not in owner.items: return owner.items.get(key, default)
    return merge_yielded((*_selected_values(owner, key), default))


def _mapping_item_value(node, owner, state):
    default = _known_value(node.args[1], state) if len(node.args) > 1 else None
    key = _literal_key(node.args[0], state) if node.args else _UNRESOLVED_KEY
    if key is not _UNRESOLVED_KEY: return _mapping_lookup(owner, key, default)
    return merge_yielded((*owner.items.values(), default,
                          _unhashable_key_sender(
                              node.args[0] if node.args else None, state)))


def resolve_expression_value(node, state, generator_factory, sender_resolver,
                             unprovable_sender):
    """A pop's removal is applied here, at evaluation, once per node per
    state; every other resolution leaves the state as it found it."""
    known = _known_value(node, state)
    if known is not None: return known
    if isinstance(node, ast.GeneratorExp):
        return generator_factory(node)
    if isinstance(node, ast.Name) and node.id in state.generators:
        return state.generators[node.id]
    if isinstance(node, ast.Name) and node.id in state.callables:
        return state.callables[node.id]
    if isinstance(node, (ast.NamedExpr, ast.Starred)):
        value = state.evaluated.get(id(node.value))
        if value is not None:
            return value
    if isinstance(node, (ast.IfExp, ast.BoolOp)):
        value = merge_yielded((sender_resolver(node, state.aliases),
                               *(state.evaluated.get(id(child))
                                 for child in ast.iter_child_nodes(node))))
        if value is not None:
            return value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        value = _display_value(node, state)
        if value is not None:
            return value
        values = [state.evaluated.get(id(item)) for item in node.elts]
        if any(item is not None and not isinstance(item, DeferredGenerator)
               for item in values):
            return unprovable_sender
    if isinstance(node, ast.Dict):
        return _dict_value(node, state) or sender_resolver(
            node, state.aliases)
    if isinstance(node, (ast.ListComp, ast.SetComp)):
        value = state.evaluated.get(id(node.elt))
        iterated = state.evaluated.get(iterated_key(node))
        if is_deferred_value(value) or iterated is not None:
            return SpreadContainer(
                {0: value}, 1, type(node).__name__[:-4].lower(),
                iterated=iterated)
    if isinstance(node, ast.DictComp):
        value = state.evaluated.get(id(node.value))
        if is_deferred_value(value) or sender_value(value) is not None:
            return SpreadContainer({DYNAMIC_KEY: value}, None, 'dict', node)
    if isinstance(node, ast.Subscript):
        owner = _known_value(node.value, state)
        value = static_slice_read(node, owner)
        if value is not None:
            return value
        key = _literal_key(node.slice, state)
        # A dict read the guard cannot resolve has no position to name, so
        # every item is a candidate; every other owner keeps the lookup that
        # recurses into alternatives and reads a list by position.
        if key is _UNRESOLVED_KEY and isinstance(owner, DeferredContainer) \
                and owner.kind == 'dict':
            value = merge_yielded((*owner.items.values(),
                                  _unhashable_key_sender(node.slice, state)))
        else:
            value = merge_yielded(_selected_values(owner, key))
        if value is not None:
            return value
    if isinstance(node, ast.BinOp):
        operands = set_operands(node.op, node.left, node.right, state)
        if operands is not None:
            return fold_set_operation(node.op, operands, node)
        if isinstance(node.op, ast.BitOr):
            return _merge_or_value(node, state)
    if isinstance(node, ast.Attribute):
        owner = _known_value(node.value, state)
        value = merge_yielded(
            _selected_values(owner, node.attr, attribute=True))
        if value is not None:
            return value
        if isinstance(owner, DeferredContainer) \
                and node.attr in CONTAINER_MUTATORS:
            # A method taken off a tracked container names the container, so a
            # binding of it (`f = x.pop`) still says what `f(0)` mutates.
            return DeferredMethod(owner, node.attr)
    if isinstance(node, ast.Call):
        owner = _known_value(node.func, state)
        if isinstance(owner, DeferredClass):
            return DeferredInstance(dict(owner.methods))
        if isinstance(node.func, ast.Name) and node.func.id == 'dict' \
                and 'dict' not in state.builtin_globals \
                and 'dict' not in state.builtin_locals:
            return _dict_call_value(node, state)
        value = reversed_read(node, state) or sequence_method_value(
            node, state)
        if value is not None:
            return value
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr == 'setdefault'
                and isinstance(node.func.value, ast.Name)):
            return _setdefault_value(node, state)
        owner = mapping_lookup_owner(node, state)
        if owner is not None:
            value = _mapping_item_value(node, owner, state)
            _apply_pop(state, node)
            return value
        default = _unknown_lookup_default(node, state)
        if default is not None:
            return merge_yielded((default, UNPROVABLE_SENDER))
        if isinstance(node.func, ast.Attribute) \
                and node.func.attr == 'fromkeys' \
                and isinstance(node.func.value, ast.Name):
            static_dict = (node.func.value.id == 'dict'
                           and 'dict' not in state.builtin_globals
                           and 'dict' not in state.builtin_locals)
            known = _known_value(node.args[1], state) \
                if len(node.args) > 1 else None
            if static_dict and known is not None:
                return DeferredContainer(
                    {DYNAMIC_KEY: known}, None, 'dict', node)
    return sender_resolver(node, state.aliases)


def _literal_pair_items(source, state):
    """Occupancy a syntactic sequence of pairs contributes for its literal
    keys, by position, and whether a modelled position is that pair's own.

    The deferred container for a literal list carries the display length but
    not its items, so occupancy is recovered from the source text; a pair
    the loop could name no key for still joins the unknown-key slot. A pair
    value that is an unresolvable call is stored unprovable, as a keyword
    store does. A star splices the display's positions, so the entries fold
    beside the dynamic slot but no position names the pair it sits at.
    """
    entries, aligned = {}, True
    if not isinstance(source, (ast.List, ast.Tuple, ast.Set)):
        return entries, False
    for position, pair in enumerate(source.elts):
        if isinstance(pair, ast.Starred):
            aligned = False
            continue
        if not isinstance(pair, (ast.Tuple, ast.List, ast.Set)) \
                or len(pair.elts) != 2:
            continue
        key = _literal_key(pair.elts[0], state)
        if key is _UNRESOLVED_KEY:
            continue
        value = _known_value(pair.elts[1], state)
        if value is None and isinstance(pair.elts[1], ast.Call):
            value = UNPROVABLE_SENDER
        entries[position] = (key, value)
    return entries, aligned


def literal_pair_keys(source, states, consumer):
    """The keys the pairs an iterable argument yields fold to, in yield
    order, or an empty tuple when there is no key to name: the projection
    reads no keys, the yield cannot be matched to the text, or the source
    models no pair. Only the dict call files them.

    A generator's element is one pair in every state. A literal sequence
    qualifies when every state models the same container, so the order
    the pairs are yielded in is the order the display holds them, and
    when every pair the display modelled folds a key: a pair the display
    modelled nothing is absent from the yield, and one whose key the fold
    cannot resolve has no place in the key list, so either passes no key
    at all and the store keeps every pair in the unknown-key slot. A star
    splices the display's positions and never qualifies.
    """
    if consumer != 'dict':
        return ()
    element = isinstance(source, ast.GeneratorExp)
    if element:
        source = source.elt
    else:
        modelled = _known_value(source, states[0]) if states else None
        if not isinstance(modelled, DeferredContainer) or any(
                _known_value(source, state) is not modelled
                for state in states):
            return ()
    if not isinstance(source, (ast.List, ast.Tuple, ast.Set)):
        return ()
    keys = ()
    for state in states:
        entries, aligned = _literal_pair_items(source, state)
        if not aligned:
            return ()
        if element:
            if 0 not in entries:
                return ()
            folded = (entries[0][0],)
        else:
            if any(position not in entries for position in modelled.items):
                return ()
            folded = tuple(entries[position][0]
                           for position in modelled.items)
        if keys and folded != keys:
            return ()
        keys = folded
    return keys


def _source_items(source, state):
    """Items one mapping store contributes and whether their keys are all
    counted; None marks unknown contents."""
    known = (_dict_value(source, state) if isinstance(source, ast.Dict)
             else _known_value(source, state))
    if not isinstance(known, DeferredContainer):
        return None
    if known.kind == 'dict':
        return known.items, known.length is not None
    if known.kind not in ('list', 'tuple', 'set'): return None
    items = {}
    entries, aligned = _literal_pair_items(source, state)
    for position, value in known.items.items():
        # A slot holding alternatives may hold any one of them as its pair.
        for pair in (value.values if isinstance(
                value, DeferredAlternatives) else (value,)):
            if not isinstance(pair, DeferredContainer):
                return None
            if pair.kind not in ('list', 'tuple'):
                return None
            if aligned and position in entries \
                    and pair.length == 2 and DYNAMIC_KEY not in pair.items:
                # A pair whose key the source folds is stored under that
                # key below, so its value is not an unknown-key entry.
                continue
            if pair.length != 2 or DYNAMIC_KEY in pair.items:
                fold_dynamic(items, from_position(pair, 0))
            else:
                # A modelled key is a callable or a sender, never a key.
                fold_dynamic(items, pair.items.get(1))
    # The last pair written at a key is the one the dict holds (`1` and
    # `True` one key there), and only the dynamic slot precedes it.
    for key, value in entries.values():
        items[key] = value
    return items, len(known.items) == known.length


def _mark_unprovable(state, owner_name):
    state.aliases[owner_name] = UNPROVABLE_SENDER
    owner = state.callables.get(owner_name)
    if isinstance(owner, DeferredContainer) and owner.kind == 'dict':
        replace_container(state, owner_name, owner,
                          dict(owner.items), unknown_length=True)


def _apply_mapping_store(state, owner_name, sources, keywords, node):
    """Merge provable items into the owner; unknown sources fail closed."""
    items = {}
    counted = True
    for source in sources:
        merged = _source_items(source, state)
        if merged is None:
            _mark_unprovable(state, owner_name)
            return
        _fold_items(items, merged[0])
        counted = counted and merged[1]
    for key, value in keywords.items():
        known = _known_value(value, state)
        if known is not None:
            items[key] = known
        elif state.evaluated.get(id(value)) is not None:
            items[key] = None
        elif isinstance(value, ast.Call):
            _mark_unprovable(state, owner_name)
            return
        else:
            items[key] = None
    owner = state.callables.get(owner_name)
    if items or not counted:
        if owner is None:
            owner = DeferredContainer({}, None, 'dict', node)
            state.callables[owner_name] = owner
        elif not isinstance(owner, DeferredContainer):
            return
        combined = dict(owner.items)
        _fold_items(combined, items)
        replace_container(state, owner_name, owner, combined,
                          unknown_length=not counted)


def _apply_setdefault(state, call, owner_name):
    owner = state.callables.get(owner_name)
    default = _known_value(call.args[1], state) if len(call.args) > 1 \
        else None
    if default is None and len(call.args) > 1 \
            and isinstance(call.args[1], ast.Call):
        _mark_unprovable(state, owner_name)
        return
    literal = _literal_key(call.args[0], state) if call.args \
        else _UNRESOLVED_KEY
    if (literal is not _UNRESOLVED_KEY
            and (owner is None or isinstance(owner, DeferredContainer))):
        if owner is None:
            owner = DeferredContainer({}, None, 'dict', call)
            state.callables[owner_name] = owner
        if literal not in owner.items:
            replace_container(state, owner_name, owner,
                              {**owner.items, literal: default})
        return
    if isinstance(owner, DeferredContainer):
        value = merge_yielded((owner.items.get(DYNAMIC_KEY), default))
        replace_container(state, owner_name, owner,
                          {**owner.items, DYNAMIC_KEY: value})
        return
    _mark_unprovable(state, owner_name)


def _pop_key(call, state):
    """The literal key one pop names, or _UNRESOLVED_KEY."""
    return _literal_key(call.args[0], state) if call.args \
        else _UNRESOLVED_KEY


def _apply_pop(state, call):
    """The mapping one pop call removes a key from, or None when this call is
    not a mutation the model follows: no tracked mapping behind it, so the
    general invalidation is what answers for it."""
    owner = mapping_lookup_owner(call, state)
    if owner is None or call.func.attr != 'pop':
        return None
    key = _pop_key(call, state)
    items = dict(owner.items)
    if key is not _UNRESOLVED_KEY:
        items.pop(key, None)
    elif owner.kind != 'dict':
        return None
    replace_deferred_storage(state, owner, container_copy(
        owner, items, key is _UNRESOLVED_KEY))
    return owner


def _apply_set_store(state, name, operator, operands, node):
    """Bind the name an augmented set operation rebinds.

    The rebinding already dropped the name. The fold replaces the
    pre-rebind container in place, so every other name bound to the same
    object reads the new elements too.
    """
    folded = fold_set_operation(operator, operands, node)
    previous = operands[0]
    if isinstance(previous, DeferredContainer):
        folded = container_copy(previous, folded.items)
        replace_deferred_storage(state, previous, folded)
    state.callables[name] = folded
    sync_cells(state, {name})


def apply_deferred_store(statement, state):
    """Apply the statement's own stores, then drop what an in-place mutation
    it did not model made stale."""
    claimed = set()
    _apply_modelled_store(statement, state, claimed)
    invalidate_unmodelled(statement, state, claimed)


def _apply_modelled_store(statement, state, claimed):
    drop_shifted_positions(statement, state)
    if isinstance(statement, ast.Expr) \
            and isinstance(statement.value, ast.Call):
        call = statement.value
        if not isinstance(call.func, ast.Attribute):
            return
        if call.func.attr == 'pop':
            if _apply_pop(state, call) is not None:
                claimed.add(id(call))
            return
        if not isinstance(call.func.value, ast.Name):
            return
        owner_name = call.func.value.id
        owner = state.callables.get(owner_name)
        # `update` and `setdefault` are followed for a mapping and only for a
        # mapping: a set's shares the names but not the effect, and claiming
        # a name alone would drop a mutation the model never applied. `clear`
        # empties whatever it is called on, so the model follows it for every
        # kind.
        mapping = owner is None or (isinstance(owner, DeferredContainer)
                                    and owner.kind == 'dict')
        if call.func.attr == 'clear' and isinstance(owner, DeferredContainer):
            replacement = DeferredContainer(
                {}, 0, owner.kind, owner.identity)
            replace_deferred_storage(state, owner, replacement)
            sync_cells(state, {owner_name})
            claimed.add(id(call))
        elif call.func.attr == 'update' and mapping:
            _apply_mapping_store(
                state, owner_name, call.args, {
                    keyword.arg: keyword.value for keyword in call.keywords
                    if keyword.arg is not None}, call)
            claimed.add(id(call))
        elif call.func.attr == 'setdefault' and mapping:
            _apply_setdefault(state, call, owner_name)
            claimed.add(id(call))
        return
    if isinstance(statement, ast.AugAssign):
        if isinstance(statement.target, ast.Name):
            operands = set_operands(statement.op, statement.target,
                                    statement.value, state)
            if operands is not None:
                # The fold is this store path's own answer, so the claim keeps
                # the general invalidation from joining a set it just folded.
                _apply_set_store(state, statement.target.id, statement.op,
                                 operands, statement)
                claimed.add(id(statement))
                return
        held = _known_value(statement.target, state)
        if isinstance(statement.op, ast.BitOr) \
                and isinstance(statement.target, ast.Name) \
                and isinstance(held, DeferredContainer) \
                and held.kind == 'dict':
            # The rebinding already dropped the name; merge into its dict.
            state.callables[statement.target.id] = held
            sync_cells(state, {statement.target.id})
            claimed.add(id(statement))
            _apply_mapping_store(
                state, statement.target.id, [statement.value], {},
                statement)
        return
    if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Delete)):
        return
    value = (_known_value(statement.value, state)
             if not isinstance(statement, ast.Delete) else None)
    raw = (state.evaluated.get(id(statement.value))
           if not isinstance(statement, ast.Delete) else None)
    targets = (statement.targets if isinstance(statement, ast.Assign)
               else [statement.target] if not isinstance(
                   statement, ast.Delete) else statement.targets)
    literal = _literal_value(getattr(statement, 'value', None))
    for target in targets:
        if isinstance(target, ast.Name):
            if literal is _UNSAFE_LITERAL:
                state.literals.pop(target.id, None)
            else:
                state.literals[target.id] = literal
        store_deferred_target(
            target, value, state, isinstance(statement, ast.Delete),
            value is None and raw is None
            and isinstance(statement, (ast.Assign, ast.AnnAssign))
            and isinstance(statement.value, ast.Call))
