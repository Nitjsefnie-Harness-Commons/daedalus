"""Resolve deferred expression values against live flow state."""
import ast

from _pyroute_mapping import (_selected_values, apply_assignment_bindings)
from _pyroute_values import (_known_value, deferred_expression_value,
                             is_deferred_value, merge_yielded, sender_value)

_LIVE_UNRESOLVED = object()


def seed_selection_value(value, state):
    """Seed the evaluated cache so a selection's alias never reads clean.

    A constant-name getattr resolves like the attribute it names, and an
    expression whose walk found only a sender keeps the deferred values it
    carries beside that sender; without the seed the alias records the
    sender string alone and a later tab-less call of it reads clean."""
    cached = state.evaluated.get(id(value))
    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == 'getattr' and len(value.args) in (2, 3)
            and isinstance(value.args[1], ast.Constant)
            and isinstance(value.args[1].value, str)):
        owner = _known_value(value.args[0], state)
        selected = merge_yielded(
            _selected_values(owner, value.args[1].value, attribute=True))
        if selected is not None:
            state.evaluated[id(value)] = selected
            cached = selected
    if (cached is not None and not is_deferred_value(cached)
            and sender_value(cached) is not None):
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
