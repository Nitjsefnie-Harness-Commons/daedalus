"""Unresolvable-owner defaults for get and pop lookups."""
import ast

from _pyroute_values import _known_value


def _unknown_lookup_default(node, state):
    """A known default of a get or pop on an owner the model cannot read."""
    if not isinstance(node.func, ast.Attribute) \
            or node.func.attr not in ('get', 'pop') or len(node.args) < 2:
        return None
    return _known_value(node.args[1], state)
