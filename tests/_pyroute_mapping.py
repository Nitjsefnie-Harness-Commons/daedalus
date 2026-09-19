"""Mapping stores and expression resolution for deferred routing values."""
import ast

from _pyroute_storage import replace_deferred_storage
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,
                             DeferredAlternatives, DeferredClass,
                             DeferredContainer, DeferredGenerator,
                             DeferredInstance, _known_value, is_deferred_value,
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
        return [value.items.get(key)] + [
            item for name, item in value.items.items()
            if name is DYNAMIC_KEY]
    return []


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
            if isinstance(key, ast.Constant) and value is not None:
                items[key.value] = value
            continue
        if isinstance(item, ast.Dict):
            nested = _dict_value(item, state)
            if nested is not None:
                items.update(nested.items)
            continue
        value = _known_value(item, state)
        if isinstance(item, ast.Call) and (
                value is None
                or not isinstance(value, DeferredContainer)
                or value.kind != 'dict'):
            return DeferredContainer(
                {DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'dict')
        if value is None:
            continue
        if not isinstance(value, DeferredContainer) or value.kind != 'dict':
            continue
        items.update(value.items)
    return DeferredContainer(
        items, len(node.values), 'dict') if items else None


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
            return UNPROVABLE_SENDER
        if isinstance(known, DeferredContainer) and known.kind == 'dict':
            items.update(known.items)
    if not items: return None
    return DeferredContainer(items, None, 'dict')


def _dict_call_value(node, state):
    """Mapping value of a builtin dict() call, or None when untracked."""
    if len(node.args) == 1 and not node.keywords:
        known = _known_value(node.args[0], state)
        if isinstance(known, DeferredContainer) and known.kind == 'dict':
            return DeferredContainer(
                dict(known.items), known.length, 'dict')
        return None
    if node.args:
        return UNPROVABLE_SENDER
    items = {}
    for keyword in node.keywords:
        if keyword.arg is None:
            return UNPROVABLE_SENDER
        known = _known_value(keyword.value, state)
        if known is not None: items[keyword.arg] = known
    if not items: return None
    return DeferredContainer(items, len(node.keywords), 'dict')


def _setdefault_value(node, state):
    """The stored-or-existing item one setdefault call evaluates to."""
    owner = _known_value(node.func.value, state)
    key = node.args[0] if node.args else None
    if (not isinstance(owner, DeferredContainer)
            or not isinstance(key, ast.Constant)
            or not isinstance(key.value, str)):
        default = _known_value(node.args[1], state) if len(node.args) > 1 \
            else None
        return UNPROVABLE_SENDER if default is not None else None
    item = owner.items.get(key.value)
    if item is not None: return item
    return _known_value(node.args[1], state) if len(node.args) > 1 else None


def resolve_expression_value(node, state, generator_factory, sender_resolver,
                             unprovable_sender):
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
            return DeferredContainer({DYNAMIC_KEY: value}, None, 'dict')
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
        if isinstance(node.func, ast.Attribute) \
                and node.func.attr == 'fromkeys' \
                and isinstance(node.func.value, ast.Name):
            static_dict = (node.func.value.id == 'dict'
                           and 'dict' not in state.builtin_globals
                           and 'dict' not in state.builtin_locals)
            known = _known_value(node.args[1], state) \
                if len(node.args) > 1 else None
            if static_dict and known is not None:
                return DeferredContainer({DYNAMIC_KEY: known}, 1, 'dict')
    return sender_resolver(node, state.aliases)


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
    return items


def _mark_unprovable(state, owner_name):
    state.aliases[owner_name] = UNPROVABLE_SENDER


def _replace_container(state, owner_name, owner, items):
    replacement = DeferredContainer(
        items, owner.length, owner.kind, owner.identity)
    replace_deferred_storage(state, owner, replacement)
    sync_cells(state, {owner_name})


def _apply_mapping_store(state, owner_name, sources, keywords):
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
            items.pop(key, None)
        elif isinstance(value, ast.Call):
            _mark_unprovable(state, owner_name)
            return
        else:
            items.pop(key, None)
    owner = state.callables.get(owner_name)
    if items:
        if owner is None:
            owner = DeferredContainer({}, None, 'dict')
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
    if default is None: return
    if (isinstance(key, ast.Constant) and isinstance(key.value, str)
            and (owner is None or isinstance(owner, DeferredContainer))):
        if owner is None:
            owner = DeferredContainer({}, None, 'dict')
            state.callables[owner_name] = owner
        if key.value not in owner.items:
            _replace_container(state, owner_name, owner,
                               {**owner.items, key.value: default})
        return
    _mark_unprovable(state, owner_name)


def apply_deferred_store(statement, state):
    if isinstance(statement, ast.Expr) \
            and isinstance(statement.value, ast.Call):
        call = statement.value
        if not isinstance(call.func, ast.Attribute) \
                or not isinstance(call.func.value, ast.Name):
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
                    if keyword.arg is not None})
        elif call.func.attr == 'setdefault':
            _apply_setdefault(state, call, owner_name)
        return
    if isinstance(statement, ast.AugAssign):
        if isinstance(statement.op, ast.BitOr) \
                and isinstance(statement.target, ast.Name):
            _apply_mapping_store(
                state, statement.target.id, [statement.value], {})
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
    for target in targets:
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
            removing = isinstance(statement, ast.Delete)
            unknown_call = (value is None and raw is None
                            and isinstance(statement, ast.Assign)
                            and isinstance(statement.value, ast.Call))
            if owner is None:
                if value is None and (removing or not unknown_call):
                    continue
                owner = DeferredContainer({}, None, 'dict')
                state.callables[owner_name] = owner
            elif not isinstance(owner, DeferredContainer):
                continue
            items = dict(owner.items)
            if value is not None:
                items[DYNAMIC_KEY if dynamic else target.slice.value] = value
            elif unknown_call:
                if dynamic:
                    items.setdefault(DYNAMIC_KEY, UNPROVABLE_SENDER)
                elif items.get(target.slice.value) is None:
                    items[target.slice.value] = UNPROVABLE_SENDER
            elif dynamic:
                continue
            else:
                items.pop(target.slice.value, None)
            _replace_container(state, owner_name, owner, items)
