"""Mapping stores, assignment binders and expression resolution for deferred
routing values."""
import ast

from _pyroute_storage import replace_deferred_storage
from _pyroute_containers import SpreadContainer, iterated_key
from _pyroute_indexing import reversed_read, static_slice_read
from _pyroute_keys import (_UNRESOLVED_KEY, _UNSAFE_LITERAL, _literal_key,
                           _literal_value, _unhashable_key_sender,
                           literal_iterable_cardinality)
from _pyroute_positions import (alias_target_pairs, at_position,
                                from_position, sequence_method_value)
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,
                             DeferredAlternatives, DeferredClass,
                             DeferredContainer, DeferredGenerator,
                             DeferredInstance, _known_value, is_deferred_value,
                             mapping_lookup_owner, merge_yielded,
                             sender_value, sync_cells)


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
        if starred and not isinstance(value, DeferredContainer):
            count = _literal_count(item.value)
            if count is None:
                index = None
                _fold_dynamic(items, UNPROVABLE_SENDER)
            elif index is not None:
                index += count
        elif starred and value.kind == 'dict':
            # Unpacking a dict yields its keys, never the modelled values.
            index = (None if index is None or value.length is None
                     else index + value.length)
        elif starred and (index is None or value.length is None):
            index = None
            _fold_dynamic(items, from_position(value, 0))
        elif starred:
            for offset in range(value.length):
                nested = merge_yielded(at_position(value, offset))
                if nested is not None:
                    items[index + offset] = nested
            index += value.length
        elif index is None:
            _fold_dynamic(items, value)
        else:
            if value is not None:
                items[index] = value
            index += 1
    starred_any = any(isinstance(item, ast.Starred) for item in node.elts)
    if isinstance(node, ast.Set) and (items or starred_any):
        # A set has no positions; equal elements collapse at runtime.
        joined = merge_yielded(items.values())
        return DeferredContainer(
            {} if joined is None else {DYNAMIC_KEY: joined}, None, 'set')
    if items or isinstance(node, ast.List) or starred_any:
        return DeferredContainer(items, index, type(node).__name__.lower())
    return None


def _literal_count(node):
    """The exact element count of a string literal or of a display the
    literal evaluator can count; None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(
            node.value, (str, bytes)):
        return len(node.value)
    return literal_iterable_cardinality(node)


def _fold_dynamic(target, value):
    """Merge a value into the DYNAMIC_KEY slot, joining on repeat."""
    target[DYNAMIC_KEY] = merge_yielded(
        (target.get(DYNAMIC_KEY), value))


def _fold_items(target, source):
    """Fold source mapping items into target, joining DYNAMIC_KEY."""
    for key, value in source.items():
        if key is DYNAMIC_KEY:
            _fold_dynamic(target, value)
        else:
            target[key] = value


def _dict_length(items, counted=True):
    """A dict's key count while every key is known, otherwise None."""
    return len(items) if counted and DYNAMIC_KEY not in items else None


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
                _fold_dynamic(items, value)
            continue
        value = (_dict_value(item, state) if isinstance(item, ast.Dict)
                 else _known_value(item, state))
        if isinstance(value, DeferredContainer) and value.kind == 'dict':
            _fold_items(items, value.items)
            counted = counted and value.length is not None
        else:
            _fold_dynamic(items, UNPROVABLE_SENDER)
    if len(node.keys) == 1 and node.keys[0] is not None:
        return DeferredContainer(items, 1, 'dict', node)  # one key, unread
    return DeferredContainer(items, _dict_length(items, counted), 'dict', node)


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
            _fold_dynamic(items, UNPROVABLE_SENDER)
    if not items:
        return None
    return DeferredContainer(items, _dict_length(items, counted), 'dict', node)


def _dict_call_value(node, state):
    """Mapping value of a builtin dict() call, or None when untracked."""
    items = {}
    counted = True
    sources = node.args[:1] + [
        keyword.value for keyword in node.keywords if keyword.arg is None]
    for source in sources:
        known = _source_items(source, state)
        if known is None:
            _fold_dynamic(items, UNPROVABLE_SENDER)
        else:
            _fold_items(items, known[0])
            counted = counted and known[1]
    for keyword in node.keywords:
        if keyword.arg is not None:
            items[keyword.arg] = _known_value(keyword.value, state)
    if not items:
        return None
    return DeferredContainer(items, _dict_length(items, counted), 'dict', node)


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


def _unknown_lookup_default(node, state):
    """A known default of a get or pop on an owner the model cannot read."""
    if not isinstance(node.func, ast.Attribute) \
            or node.func.attr not in ('get', 'pop') or len(node.args) < 2:
        return None
    return _known_value(node.args[1], state)


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
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _merge_or_value(node, state)
    if isinstance(node, ast.Attribute):
        owner = _known_value(node.value, state)
        value = merge_yielded(
            _selected_values(owner, node.attr, attribute=True))
        if value is not None:
            return value
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
    keys.

    The deferred container for a literal list carries the display length but
    not its items, so occupancy is recovered from the source text; every
    entry the pair loop folded is left as it found it, and a key the shared
    resolver folds is stored under that key the way every other store
    writes one. A pair value that is an unresolvable call is stored
    unprovable, as a keyword store does.
    """
    if not isinstance(source, (ast.List, ast.Tuple, ast.Set)):
        return {}
    items = {}
    for pair in source.elts:
        if not isinstance(pair, (ast.Tuple, ast.List, ast.Set)) \
                or len(pair.elts) != 2:
            continue
        key = _literal_key(pair.elts[0], state)
        if key is _UNRESOLVED_KEY:
            continue
        value = _known_value(pair.elts[1], state)
        if value is None and isinstance(pair.elts[1], ast.Call):
            value = UNPROVABLE_SENDER
        items[key] = value
    return items


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
    # A pair at an unknown position (every pair of a set) may be any of
    # the alternatives that slot joins.
    pairs = [candidate for value in known.items.values()
             for candidate in (value.values if isinstance(
                 value, DeferredAlternatives) else (value,))]
    for pair in pairs:
        if not isinstance(pair, DeferredContainer):
            return None
        if pair.kind not in ('list', 'tuple'):
            return None
        if pair.length != 2 or DYNAMIC_KEY in pair.items:
            _fold_dynamic(items, from_position(pair, 0))
        else:
            # A modelled key is a callable or a sender, never a key value.
            _fold_dynamic(items, pair.items.get(1))
    for key, value in _literal_pair_items(source, state).items():
        items.setdefault(key, value)
    return items, len(known.items) == known.length


def _mark_unprovable(state, owner_name):
    state.aliases[owner_name] = UNPROVABLE_SENDER
    owner = state.callables.get(owner_name)
    if isinstance(owner, DeferredContainer) and owner.kind == 'dict':
        _replace_container(state, owner_name, owner, dict(owner.items),
                           unknown_length=True)


def _container_copy(owner, items, unknown_length=False):
    """A copy of owner holding items; a dict's length is recounted."""
    length = owner.length
    if owner.kind == 'dict':
        length = _dict_length(
            items, owner.length is not None and not unknown_length)
    return DeferredContainer(items, length, owner.kind, owner.identity)


def _replace_container(state, owner_name, owner, items,
                       unknown_length=False):
    replace_deferred_storage(
        state, owner, _container_copy(owner, items, unknown_length))
    sync_cells(state, {owner_name})


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
        _replace_container(state, owner_name, owner, combined,
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
            _replace_container(state, owner_name, owner,
                               {**owner.items, literal: default})
        return
    if isinstance(owner, DeferredContainer):
        value = merge_yielded((owner.items.get(DYNAMIC_KEY), default))
        _replace_container(state, owner_name, owner,
                           {**owner.items, DYNAMIC_KEY: value})
        return
    _mark_unprovable(state, owner_name)


def _pop_key(call, state):
    """The literal key one pop names, or _UNRESOLVED_KEY."""
    return _literal_key(call.args[0], state) if call.args \
        else _UNRESOLVED_KEY


def _apply_pop(state, call):
    owner = mapping_lookup_owner(call, state)
    if owner is None or call.func.attr != 'pop' or not call.args:
        return
    key = _pop_key(call, state)
    items = dict(owner.items)
    if key is not _UNRESOLVED_KEY:
        items.pop(key, None)
    elif owner.kind != 'dict':
        return
    replace_deferred_storage(state, owner, _container_copy(
        owner, items, key is _UNRESOLVED_KEY))


def apply_deferred_store(statement, state):
    if isinstance(statement, ast.Expr) \
            and isinstance(statement.value, ast.Call):
        call = statement.value
        if not isinstance(call.func, ast.Attribute):
            return
        if call.func.attr == 'pop':
            _apply_pop(state, call)
            return
        if not isinstance(call.func.value, ast.Name):
            return
        owner_name = call.func.value.id
        owner = state.callables.get(owner_name)
        if call.func.attr == 'clear' and isinstance(owner, DeferredContainer):
            replacement = DeferredContainer(
                {}, 0, owner.kind, owner.identity)
            replace_deferred_storage(state, owner, replacement)
            sync_cells(state, {owner_name})
        elif call.func.attr == 'update':
            _apply_mapping_store(
                state, owner_name, call.args, {
                    keyword.arg: keyword.value for keyword in call.keywords
                    if keyword.arg is not None}, call)
        elif call.func.attr == 'setdefault':
            _apply_setdefault(state, call, owner_name)
        return
    if isinstance(statement, ast.AugAssign):
        if isinstance(statement.op, ast.BitOr) \
                and isinstance(statement.target, ast.Name):
            # The rebinding already dropped the name; merge into its dict.
            held = _known_value(statement.target, state)
            if isinstance(held, DeferredContainer) and held.kind == 'dict':
                state.callables[statement.target.id] = held
                sync_cells(state, {statement.target.id})
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


def store_deferred_target(target, value, state, removing=False,
                          unknown_call=False):
    owner_name = getattr(getattr(target, 'value', None), 'id', None)
    owner = state.callables.get(owner_name)
    if isinstance(target, ast.Attribute) \
            and isinstance(owner, DeferredInstance):
        attributes = dict(owner.attributes)
        if value is None:
            attributes.pop(target.attr, None)
        else:
            attributes[target.attr] = value
        replacement = DeferredInstance(attributes, owner.identity)
        replace_deferred_storage(state, owner, replacement)
        sync_cells(state, {owner_name})
    elif isinstance(target, ast.Subscript) \
            and isinstance(target.value, ast.Name):
        literal = _literal_key(target.slice, state)
        dynamic = literal is _UNRESOLVED_KEY
        if owner is None:
            if value is None and (removing or not unknown_call):
                return
            owner = DeferredContainer({}, None, 'dict', target)
            state.callables[owner_name] = owner
        elif not isinstance(owner, DeferredContainer):
            return
        items = dict(owner.items)
        mapping = owner.kind == 'dict'
        if dynamic and not removing:
            if value is not None or unknown_call:
                _fold_dynamic(items, UNPROVABLE_SENDER
                              if value is None else value)
            elif not mapping:
                return
        elif value is not None:
            items[literal] = value
        elif unknown_call:
            if items.get(literal) is None:
                items[literal] = UNPROVABLE_SENDER
        elif dynamic:
            if not mapping:
                return
        elif removing:
            items.pop(literal, None)
        else:
            items[literal] = None
        # A computed key may add or remove an entry: the count is unknown.
        _replace_container(state, owner_name, owner, items,
                           unknown_length=dynamic and mapping)
