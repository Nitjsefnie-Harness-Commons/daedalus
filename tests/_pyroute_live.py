"""Resolve deferred expression values against live flow state."""
import ast

from _pyroute_mapping import (_selected_values, apply_assignment_bindings)
from _pyroute_values import (UNPROVABLE_SENDER, DeferredAlternatives,
                             DeferredClass, DeferredInstance, _known_value,
                             deferred_expression_value, is_deferred_value,
                             merge_yielded)

_LIVE_UNRESOLVED = object()


def _plain_getattr(value, state):
    """The unbound getattr call of arity 2 or 3 without keywords, or None."""
    if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == 'getattr'):
        return None
    if value.keywords or len(value.args) not in (2, 3):
        return None
    return None if value.func.id in state.bound else value


def _constant_getattr(value, state):
    """The constant attribute name of a plain getattr call, or None."""
    call = _plain_getattr(value, state)
    if call is None or not isinstance(call.args[1], ast.Constant):
        return None
    return call.args[1].value if isinstance(call.args[1].value,
                                            str) else None


def _attribute_values(owner):
    """Every deferred value an owner carries, for a name that cannot be
    resolved to one attribute."""
    if isinstance(owner, DeferredAlternatives):
        return [item for value in owner.values
                for item in _attribute_values(value)]
    if isinstance(owner, DeferredInstance):
        return list(owner.attributes.values())
    if isinstance(owner, DeferredClass):
        return list(owner.methods.values())
    return []


def _selection_value(value, state):
    """The value a plain getattr call selects: the named attribute when the
    owner carries it, else the default, and every value the owner carries
    when the name is not a provable string constant. A non-string constant
    name and an absent name without a default raise before any call, so they
    select nothing."""
    call = _plain_getattr(value, state)
    if call is None:
        return None
    owner = _known_value(call.args[0], state)
    name = _constant_getattr(value, state)
    if name is not None:
        selected = merge_yielded(_selected_values(owner, name, attribute=True))
        if selected is not None:
            return selected
        if len(call.args) < 3:
            return None
    elif not isinstance(call.args[1], ast.Constant):
        selected = merge_yielded(_attribute_values(owner))
    else:
        return None
    return merge_yielded(
        (selected, _known_value(call.args[2], state))) \
        if len(call.args) == 3 else selected


def seed_selection_value(value, state):
    """Seed the evaluated cache so an unprovable selection never reads
    clean: a getattr call resolves to the value it selects, and a
    maybe-sender keeps the deferred values it carries."""
    cached = state.evaluated.get(id(value))
    selected = _selection_value(value, state)
    if selected is not None:
        state.evaluated[id(value)] = selected
        cached = selected
    if cached == UNPROVABLE_SENDER:
        parts = [item for item in (_known_value(child, state)
                                   for child in ast.walk(value))
                 if is_deferred_value(item)]
        if parts:
            state.evaluated[id(value)] = merge_yielded([cached, *parts])


def bind_alias_statement(node, state, binder):
    """Resolve a selection's value first, then bind the statement's aliases."""
    seed_selection_value(node.value, state)
    targets = (node.targets if isinstance(node, ast.Assign)
               else [node.target])
    apply_assignment_bindings(targets, node.value, state, binder)


def clear_expression_cache(node, states):
    """Clear an expression tree's cache and return its root."""
    keys = {id(item) for item in ast.walk(node)}
    for state in states:
        for key in keys:
            state.evaluated.pop(key, None)
    return node


def live_expression_value(node, state, lambda_factory, captured):
    evaluated = state.evaluated
    cached = evaluated.get(id(node), _LIVE_UNRESOLVED)
    state.evaluated = {key: value for key, value in evaluated.items()
                       if key != id(node)}
    try:
        value = deferred_expression_value(
            node, state, lambda item, current:
            lambda_factory(item, current, captured))
    finally:
        state.evaluated = evaluated
    resolved = (value is not None or isinstance(node, ast.Constant)
                or (isinstance(node, ast.Name) and node.id in state.bound
                    and node.id not in state.aliases))
    if resolved: evaluated[id(node)] = value
    elif cached is not _LIVE_UNRESOLVED: evaluated[id(node)] = cached
    else: evaluated.pop(id(node), None)
    return value if resolved else evaluated.get(id(node), _LIVE_UNRESOLVED)
