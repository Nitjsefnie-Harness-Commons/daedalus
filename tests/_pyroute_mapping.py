"""Mapping stores, assignment binders and expression resolution for deferred
routing values."""
import ast
import operator

from _pyroute_storage import replace_deferred_storage
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
        return [value.items.get(key)] + [
            item for name, item in value.items.items()
            if name is DYNAMIC_KEY]
    return []


_UNSAFE_LITERAL = object()
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg,
                    ast.Invert: operator.invert}


def _literal_value(expr):
    if isinstance(expr, ast.Constant):
        return expr.value
    if isinstance(expr, ast.UnaryOp) and type(expr.op) in _UNARY_OPERATORS:
        value = _literal_value(expr.operand)
        if (value is _UNSAFE_LITERAL
                or type(value) not in (int, float, complex)):
            return _UNSAFE_LITERAL
        try:
            return _UNARY_OPERATORS[type(expr.op)](value)
        except (ArithmeticError, TypeError, ValueError):
            return _UNSAFE_LITERAL
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        values = []
        for item in expr.elts:
            value = _literal_value(item.value if isinstance(item, ast.Starred)
                                   else item)
            if value is _UNSAFE_LITERAL:
                return value
            try:
                values.extend(value) if isinstance(item, ast.Starred) \
                    else values.append(value)
            except TypeError:
                return _UNSAFE_LITERAL
        try:
            return (tuple(values) if isinstance(expr, ast.Tuple) else
                    values if isinstance(expr, ast.List) else set(values))
        except (TypeError, ValueError):
            return _UNSAFE_LITERAL
    if isinstance(expr, ast.Dict):
        value = {}
        for key, item in zip(expr.keys, expr.values):
            item_value = _literal_value(item)
            key_value = _literal_value(key) if key is not None else None
            if item_value is _UNSAFE_LITERAL or key_value is _UNSAFE_LITERAL:
                return _UNSAFE_LITERAL
            try:
                if key is None:
                    if not isinstance(item_value, dict):
                        return _UNSAFE_LITERAL
                    value.update(item_value)
                else:
                    value[key_value] = item_value
            except (TypeError, ValueError):
                return _UNSAFE_LITERAL
        return value
    return _UNSAFE_LITERAL


def literal_iterable_cardinality(expr):
    """Return an exact literal-display length when it is provable."""
    if isinstance(expr, (ast.Tuple, ast.List)):
        counts = [literal_iterable_cardinality(item.value)
                  if isinstance(item, ast.Starred) else 1
                  for item in expr.elts]
        return None if any(count is None for count in counts) else sum(counts)
    if isinstance(expr, (ast.Set, ast.Dict)):
        value = _literal_value(expr)
        return None if value is _UNSAFE_LITERAL else len(value)
    return None


def literal_truth(expr):
    value = _literal_value(expr)
    return None if value is _UNSAFE_LITERAL else bool(value)


def alias_target_pairs(target, value):
    if isinstance(value, DeferredAlternatives):
        branches = [alias_target_pairs(target, item) for item in value.values]
        unknown = [UNPROVABLE_SENDER] if None in branches else []
        branches = [branch for branch in branches if branch is not None]
        if not branches:
            return None
        paired = []
        for index, (nested, _) in enumerate(branches[0]):
            values = [branch[index][1] for branch in branches]
            paired.append((nested, merge_yielded(values + unknown)))
        return paired
    if isinstance(value, DeferredContainer):
        if value.kind not in ('tuple', 'list') or value.length is None:
            return None
        items = [value.items.get(index) for index in range(value.length)]
    elif isinstance(value, (ast.Tuple, ast.List)):
        items = value.elts
    else:
        return None
    stars = [index for index, item in enumerate(target.elts)
             if isinstance(item, ast.Starred)]
    if not stars:
        return list(zip(target.elts, items)) \
            if len(target.elts) == len(items) else None
    if len(stars) != 1 or len(items) < len(target.elts) - 1:
        return None
    star = stars[0]
    suffix = len(target.elts) - star - 1
    end = len(items) - suffix
    pairs = [*zip(target.elts[:star], items[:star])]
    if isinstance(value, DeferredContainer):
        rest = items[star:end]
        pairs.append((target.elts[star].value, DeferredContainer(
            dict(enumerate(rest)), len(rest), 'list')))
    pairs.extend(zip(target.elts[star + 1:], items[end:]))
    return pairs


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
    items = {}
    index = 0
    for item in node.elts:
        value = _known_value(item.value if isinstance(item, ast.Starred)
                             else item, state)
        if isinstance(item, ast.Starred):
            if isinstance(value, DeferredContainer):
                for nested_index in range(value.length or 0):
                    nested = value.items.get(nested_index)
                    if nested is not None:
                        items[index + nested_index] = nested
                index += value.length or 0
            else:
                return merge_yielded(items.values())
        else:
            if value is not None:
                items[index] = value
            index += 1
    if items or isinstance(node, ast.List):
        return DeferredContainer(items, index, type(node).__name__.lower())
    return None


def _dict_value(node, state):
    items = {}
    for key, item in zip(node.keys, node.values):
        if key is not None:
            value = _known_value(item, state)
            if isinstance(key, ast.Constant):
                items[key.value] = value
            continue
        if isinstance(item, ast.Dict):
            nested = _dict_value(item, state)
            if nested is not None:
                items.update(nested.items)
            continue
        value = _known_value(item, state)
        if value is None or not isinstance(value, DeferredContainer) \
                or value.kind != 'dict':
            return DeferredContainer(
                {DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'dict', node)
        items.update(value.items)
    return DeferredContainer(
        items, len(node.values), 'dict', node)


def _merge_or_value(node, state):
    """Mapping value of `left | right` from the provable dict sides."""
    items = {}
    for side in (node.left, node.right):
        if isinstance(side, ast.Dict):
            known = _dict_value(side, state)
        else:
            known = _known_value(side, state)
            if known is None and isinstance(side, ast.Call):
                return DeferredContainer(
                    {DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'dict')
        if isinstance(known, str) and sender_value(known) is not None:
            return DeferredContainer(
                {DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'dict', node)
        if isinstance(known, DeferredContainer) and known.kind == 'dict':
            items.update(known.items)
    if not items: return None
    return DeferredContainer(items, None, 'dict', node)


def _dict_call_value(node, state):
    """Mapping value of a builtin dict() call, or None when untracked."""
    if len(node.args) == 1 and not node.keywords:
        known = _known_value(node.args[0], state)
        if isinstance(known, DeferredContainer) and known.kind == 'dict':
            return DeferredContainer(
                dict(known.items), known.length, 'dict', node)
        if known is None and isinstance(node.args[0], ast.Call):
            return DeferredContainer(
                {DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'dict', node)
        return None
    if node.args:
        return DeferredContainer(
            {DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'dict', node)
    items = {}
    for keyword in node.keywords:
        if keyword.arg is None:
            return DeferredContainer(
                {DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'dict', node)
        known = _known_value(keyword.value, state)
        items[keyword.arg] = known
    return DeferredContainer(items, len(node.keywords), 'dict', node)


_UNRESOLVED_KEY = object()


def _literal_key(node, state):
    """The literal key one mapping lookup names, or _UNRESOLVED_KEY.

    A constant is its own key and a name carries the literal it was bound
    to; anything else, and any name bound to no literal, stays unresolved.
    """
    if isinstance(node, ast.Constant):
        key = node.value
    elif isinstance(node, ast.Name):
        key = state.literals.get(node.id, _UNRESOLVED_KEY)
    else:
        return _UNRESOLVED_KEY
    try:
        hash(key)
    except TypeError:
        return _UNRESOLVED_KEY
    return key


def _setdefault_value(node, state):
    """The stored-or-existing item one setdefault call evaluates to."""
    owner = _known_value(node.func.value, state)
    key = _literal_key(node.args[0], state) if node.args else _UNRESOLVED_KEY
    if (not isinstance(owner, DeferredContainer)
            or key is _UNRESOLVED_KEY):
        default = _known_value(node.args[1], state) if len(node.args) > 1 \
            else None
        return merge_yielded((default, UNPROVABLE_SENDER)) \
            if default is not None else None
    if key in owner.items: return owner.items[key]
    return _known_value(node.args[1], state) if len(node.args) > 1 else None


def _unknown_lookup_default(node, state):
    """A known default of a get or pop on an owner the model cannot read."""
    if not isinstance(node.func, ast.Attribute) \
            or node.func.attr not in ('get', 'pop') or len(node.args) < 2:
        return None
    return _known_value(node.args[1], state)


def _mapping_item_value(node, owner, state):
    default = _known_value(node.args[1], state) if len(node.args) > 1 else None
    key = node.args[0] if node.args else None
    if isinstance(key, ast.Constant):
        if DYNAMIC_KEY not in owner.items:
            return owner.items.get(key.value, default)
        return merge_yielded((*_selected_values(owner, key.value), default))
    return merge_yielded((*owner.items.values(), default))


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
        if is_deferred_value(value):
            return DeferredContainer(
                {0: value}, 1, type(node).__name__[:-4].lower())
    if isinstance(node, ast.DictComp):
        value = state.evaluated.get(id(node.value))
        if is_deferred_value(value) or sender_value(value) is not None:
            return DeferredContainer(
                {DYNAMIC_KEY: value}, None, 'dict', node)
    if isinstance(node, ast.Subscript):
        owner = _known_value(node.value, state)
        key = (node.slice.value
               if isinstance(node.slice, ast.Constant) else None)
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
                    {DYNAMIC_KEY: known}, 1, 'dict', node)
    return sender_resolver(node, state.aliases)


def _literal_pair_items(source, state):
    """Occupancy a syntactic sequence of pairs contributes for its literal
    keys.

    The deferred container for a literal list carries the display length but
    not its items, so occupancy is recovered from the source text; every
    entry the pair loop folded is left as it found it. A pair value that is
    an unresolvable call is stored unprovable, as a keyword store does.
    """
    if not isinstance(source, (ast.List, ast.Tuple, ast.Set)):
        return {}
    items = {}
    for pair in source.elts:
        if not isinstance(pair, (ast.Tuple, ast.List, ast.Set)) \
                or len(pair.elts) != 2:
            continue
        key = pair.elts[0]
        if not isinstance(key, ast.Constant) or key.value is None:
            continue
        value = _known_value(pair.elts[1], state)
        if value is None and isinstance(pair.elts[1], ast.Call):
            value = UNPROVABLE_SENDER
        items[key.value] = value
    return items


def _source_items(source, state):
    """Items one mapping store contributes; None marks unknown contents."""
    if isinstance(source, ast.Dict):
        container = _dict_value(source, state)
        if container is None:
            return {}
        if not isinstance(container, DeferredContainer): return None
        return container.items
    known = _known_value(source, state)
    if not isinstance(known, DeferredContainer):
        return None
    if known.kind == 'dict':
        return known.items
    if known.kind not in ('list', 'tuple'): return None
    items = {}
    for pair in known.items.values():
        if not isinstance(pair, DeferredContainer):
            return None
        if pair.kind not in ('list', 'tuple'):
            return None
        key = pair.items.get(0)
        items[DYNAMIC_KEY if key is None else key] = pair.items.get(1)
    for key, value in _literal_pair_items(source, state).items():
        items.setdefault(key, value)
    return items


def _mark_unprovable(state, owner_name):
    state.aliases[owner_name] = UNPROVABLE_SENDER


def _replace_container(state, owner_name, owner, items):
    replacement = DeferredContainer(
        items, owner.length, owner.kind, owner.identity)
    replace_deferred_storage(state, owner, replacement)
    sync_cells(state, {owner_name})


def _apply_mapping_store(state, owner_name, sources, keywords, node):
    """Merge provable items into the owner; unknown sources fail closed."""
    items = {}
    for source in sources:
        merged = _source_items(source, state)
        if merged is None:
            _mark_unprovable(state, owner_name)
            return
        items.update(merged)
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
    if items:
        if owner is None:
            owner = DeferredContainer({}, None, 'dict', node)
            state.callables[owner_name] = owner
        elif not isinstance(owner, DeferredContainer):
            return
        _replace_container(state, owner_name, owner, {
            **owner.items, **items})


def _apply_setdefault(state, call, owner_name):
    owner = state.callables.get(owner_name)
    key = call.args[0] if call.args else None
    default = _known_value(call.args[1], state) if len(call.args) > 1 \
        else None
    if default is None and len(call.args) > 1 \
            and isinstance(call.args[1], ast.Call):
        _mark_unprovable(state, owner_name)
        return
    if (isinstance(key, ast.Constant)
            and (owner is None or isinstance(owner, DeferredContainer))):
        if owner is None:
            owner = DeferredContainer({}, None, 'dict', call)
            state.callables[owner_name] = owner
        if key.value not in owner.items:
            _replace_container(state, owner_name, owner,
                               {**owner.items, key.value: default})
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
    if owner is None or call.func.attr != 'pop':
        return
    key = _pop_key(call, state)
    if key is _UNRESOLVED_KEY:
        return
    items = dict(owner.items)
    items.pop(key, None)
    replacement = DeferredContainer(
        items, owner.length, owner.kind, owner.identity)
    replace_deferred_storage(state, owner, replacement)


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
        dynamic = not isinstance(target.slice, ast.Constant)
        if owner is None:
            if value is None and (removing or not unknown_call):
                return
            owner = DeferredContainer({}, None, 'dict', target)
            state.callables[owner_name] = owner
        elif not isinstance(owner, DeferredContainer):
            return
        items = dict(owner.items)
        if value is not None:
            items[DYNAMIC_KEY if dynamic else target.slice.value] = value
        elif unknown_call:
            if dynamic:
                items.setdefault(DYNAMIC_KEY, UNPROVABLE_SENDER)
            elif items.get(target.slice.value) is None:
                items[target.slice.value] = UNPROVABLE_SENDER
        elif dynamic:
            return
        elif removing:
            items.pop(target.slice.value, None)
        else:
            items[target.slice.value] = None
        _replace_container(state, owner_name, owner, items)
