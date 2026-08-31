"""Resolve deferred expression values against live flow state."""
import ast

from _pyroute_values import deferred_expression_value

_LIVE_UNRESOLVED = object()


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
    return value if resolved else cached
