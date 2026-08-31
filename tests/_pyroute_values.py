"""Deferred values and expression helpers for Python routing flow."""
import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _pyroute_state import FlowState


@dataclass(frozen=True)
class DeferredGenerator:
    """A generator expression and its known remaining yield count."""

    expression: ast.GeneratorExp
    remaining: int | None
    evaluate_zero: bool = False


@dataclass(frozen=True)
class DeferredCallable:
    """A callable body and its definition-time lexical state."""

    scope: ast.AST
    state: 'FlowState'
    locals: frozenset
    captured: frozenset = frozenset()


@dataclass(frozen=True)
class DeferredClass:
    """Methods belonging to one statically known class object."""

    methods: dict


@dataclass(frozen=True)
class DeferredAlternatives:
    """Deferred values selected by one unresolved expression."""

    values: tuple


@dataclass(frozen=True)
class DeferredContainer:
    """Statically known deferred values stored by key or index."""

    items: dict
    length: int | None = None


@dataclass(frozen=True)
class DeferredInstance:
    """Deferred attributes on one locally constructed instance."""

    attributes: dict


DEFERRED_VALUES = (DeferredCallable, DeferredClass, DeferredAlternatives,
                   DeferredContainer, DeferredInstance)


def is_deferred_value(value):
    return isinstance(value, DEFERRED_VALUES)


def merge_deferred_values(values):
    merged = []
    for value in values:
        nested = (value.values if isinstance(value, DeferredAlternatives)
                  else (value,))
        for item in nested:
            if is_deferred_value(item) and all(
                    item is not found for found in merged):
                merged.append(item)
    if not merged:
        return None
    return merged[0] if len(merged) == 1 else DeferredAlternatives(
        tuple(merged))


def callable_candidates(value):
    if isinstance(value, DeferredCallable):
        return (value,)
    if isinstance(value, DeferredAlternatives):
        return tuple(candidate for item in value.values
                     for candidate in callable_candidates(item))
    return ()


def contains_callable(value, target):
    if value is target:
        return True
    if isinstance(value, DeferredAlternatives):
        return any(contains_callable(item, target) for item in value.values)
    if isinstance(value, DeferredContainer):
        return any(contains_callable(item, target)
                   for item in value.items.values())
    if isinstance(value, DeferredInstance):
        return any(contains_callable(item, target)
                   for item in value.attributes.values())
    if isinstance(value, DeferredClass):
        return any(contains_callable(item, target)
                   for item in value.methods.values())
    return False


def _known_value(node, state):
    value = state.evaluated.get(id(node))
    if value is None and isinstance(node, ast.Name):
        value = state.callables.get(node.id)
    return value if is_deferred_value(value) else None


def _selected_values(value, key, attribute=False):
    if isinstance(value, DeferredAlternatives):
        return [_selected_values(item, key, attribute)
                for item in value.values]
    if attribute and isinstance(value, DeferredInstance):
        return [value.attributes.get(key)]
    if attribute and isinstance(value, DeferredClass):
        return [value.methods.get(key)]
    if not attribute and isinstance(value, DeferredContainer):
        return [value.items.get(key)]
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
                return merge_deferred_values(items.values())
        else:
            if value is not None:
                items[index] = value
            index += 1
    if items or isinstance(node, ast.List):
        return DeferredContainer(items, index)
    return None


def _dict_value(node, state):
    items = {}
    for key, item in zip(node.keys, node.values):
        value = _known_value(item, state)
        if key is not None and isinstance(key, ast.Constant) \
                and value is not None:
            items[key.value] = value
    return DeferredContainer(items, len(node.values)) if items else None


def expression_value(node, state, generator_factory, sender_resolver,
                     unprovable_sender):
    known = _known_value(node, state)
    if known is not None:
        return known
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
        value = merge_deferred_values(
            state.evaluated.get(id(child))
            for child in ast.iter_child_nodes(node))
        if value is not None:
            return value
    if isinstance(node, (ast.Tuple, ast.List)):
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
            return DeferredContainer({0: value}, 1)
    if isinstance(node, ast.Subscript):
        owner = _known_value(node.value, state)
        key = (node.slice.value
               if isinstance(node.slice, ast.Constant) else None)
        value = merge_deferred_values(_selected_values(owner, key))
        if value is not None:
            return value
    if isinstance(node, ast.Attribute):
        owner = _known_value(node.value, state)
        value = merge_deferred_values(
            _selected_values(owner, node.attr, attribute=True))
        if value is not None:
            return value
    if isinstance(node, ast.Call):
        owner = _known_value(node.func, state)
        if isinstance(owner, DeferredClass):
            return DeferredInstance(dict(owner.methods))
    return sender_resolver(node, state.aliases)


def expression_callables(node, state):
    values = [_known_value(child, state) for child in ast.walk(node)]
    return tuple(candidate for value in values if value is not None
                 for candidate in callable_candidates(value))


def follow_callable_call(candidates, arguments, states, call, analyze,
                         copy_states, dedupe_states):
    returned = []
    if candidates:
        invoked = []
        for candidate in candidates:
            branch, value = analyze(candidate, copy_states(states), True, call)
            invoked.extend(branch)
            if value is not None:
                returned.append(value)
        return dedupe_states(invoked), merge_deferred_values(returned)
    callbacks = {id(candidate): candidate
                 for argument in arguments for state in states
                 for candidate in expression_callables(argument, state)}
    for callback in callbacks.values():
        states, _ = analyze(callback, states, True)
    return states, None


def bind_call_arguments(deferred, call, caller, entry, sender_resolver):
    args = deferred.scope.args
    positional = [*args.posonlyargs, *args.args]
    if isinstance(call.func, ast.Attribute) and positional:
        positional = positional[1:]
    expressions = list(zip(positional, call.args))
    by_name = {parameter.arg: parameter
               for parameter in [*positional, *args.kwonlyargs]}
    expressions.extend((by_name[keyword.arg], keyword.value)
                       for keyword in call.keywords
                       if keyword.arg in by_name)
    for parameter, expression in expressions:
        name = parameter.arg
        value = _known_value(expression, caller)
        if value is not None:
            entry.callables[name] = value
        sender = sender_resolver(expression, caller.aliases)
        if sender is not None:
            entry.aliases[name] = sender


def store_deferred_value(statement, state):
    if type(statement) not in (ast.Assign, ast.AnnAssign):
        return
    value = _known_value(statement.value, state)
    if value is None:
        return
    targets = (statement.targets if isinstance(statement, ast.Assign)
               else [statement.target])
    for target in targets:
        owner_name = getattr(getattr(target, 'value', None), 'id', None)
        owner = state.callables.get(owner_name)
        if isinstance(target, ast.Attribute) \
                and isinstance(owner, DeferredInstance):
            attributes = dict(owner.attributes)
            attributes[target.attr] = value
            state.callables[owner_name] = DeferredInstance(attributes)
        elif isinstance(target, ast.Subscript) \
                and isinstance(owner, DeferredContainer) \
                and isinstance(target.slice, ast.Constant):
            items = dict(owner.items)
            items[target.slice.value] = value
            state.callables[owner_name] = DeferredContainer(
                items, owner.length)


def payload_key(dicts):
    return tuple(sorted(
        (name, frozenset((key, value[0], id(value[1]))
                         for key, value in keys.items()))
        for name, keys in dicts.items()))
