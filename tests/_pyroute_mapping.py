"""Mapping stores and assignment binders for deferred routing values."""
import ast

from _pyroute_invalidation import CONTAINER_MUTATORS, invalidate_unmodelled
from _pyroute_keys import (_UNRESOLVED_KEY, _UNSAFE_LITERAL, _literal_key,
                           _literal_value)
from _pyroute_positions import alias_target_pairs, drop_shifted_positions
from _pyroute_reads import (_apply_pop, _fold_items, _literal_pair_items,
                            _source_items)
from _pyroute_setops import fold_set_operation, set_operands
from _pyroute_storage import container_copy, replace_deferred_storage
from _pyroute_stores import replace_container, store_deferred_target
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,
                             DeferredContainer, DeferredMethod, _known_value,
                             is_deferred_value, merge_yielded, sync_cells)


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


def bound_value(expression, state):
    """What a binding takes from an expression.

    A method taken off a tracked container names that container, so a name
    bound to it still says what a call through the name mutates. Only the
    binding reads that: a bare `x.pop` read for its value needs nothing, and
    resolving it everywhere would split every state a bare method reference
    appears in for a fact no other reader wants."""
    value = _known_value(expression, state)
    if value is None and isinstance(expression, ast.Attribute) \
            and expression.attr in CONTAINER_MUTATORS:
        owner = _known_value(expression.value, state)
        if isinstance(owner, DeferredContainer):
            return DeferredMethod(owner, expression.attr)
    return value


def apply_assignment_bindings(targets, value, state, binder):
    entries = [entry for target in targets
               if isinstance(target, (ast.Name, ast.Tuple, ast.List))
               for entry in _assignment_values(target, value, state)]
    aliases = {}
    # Pending RHS references must follow storage mutations, but not rebindings.
    for target, expression in entries:
        state.evaluated[id(target)] = bound_value(expression, state) \
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


def _mark_unprovable(state, owner_name):
    state.aliases[owner_name] = UNPROVABLE_SENDER
    owner = state.callables.get(owner_name)
    if isinstance(owner, DeferredContainer) and owner.kind == 'dict':
        replace_container(state, owner_name, owner,
                          dict(owner.items), unknown_length=True)


def _apply_mapping_store(state, owner_name, sources, keywords, node):
    """Merge provable items into the owner; unknown sources fail closed.

    A `**mapping` argument is a source like any other, and claiming the
    call without folding it would leave the owner holding a key set the
    model never computed, which is the shape a later constant-key read
    cannot account for."""
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
            items = dict(owner.items)
            items[literal] = default
            replace_container(state, owner_name, owner, items)
        return
    if isinstance(owner, DeferredContainer):
        value = merge_yielded((owner.items.get(DYNAMIC_KEY), default))
        replace_container(state, owner_name, owner,
                          {**owner.items, DYNAMIC_KEY: value})
        return
    _mark_unprovable(state, owner_name)


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
                state, owner_name,
                [*call.args, *(keyword.value for keyword in call.keywords
                               if keyword.arg is None)], {
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
