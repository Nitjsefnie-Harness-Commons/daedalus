"""Positional reads for the Python routing guard.

A wholly static slice and an unshadowed `reversed()` both name positions in an
ordered operand, so both are recognised here and both hand that operand to
`ordered_container` the way an order-preserving consumer does.
"""
import ast

from _pyroute_containers import ordered_container
from _pyroute_values import _known_value


_DYNAMIC_BOUND = object()


def _static_bound(node):
    """A slice bound that is statically an integer, else the sentinel."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(
            node.operand, ast.Constant) \
            and isinstance(node.operand.value, int) \
            and not isinstance(node.operand.value, bool):
        if isinstance(node.op, ast.USub):
            return -node.operand.value
        if isinstance(node.op, ast.UAdd):
            return node.operand.value
    return _DYNAMIC_BOUND


def _static_slice(node):
    """A python slice for a wholly static ast.Slice, else None."""
    lower = _static_bound(node.lower)
    upper = _static_bound(node.upper)
    step = _static_bound(node.step)
    if _DYNAMIC_BOUND in (lower, upper, step):
        return None
    return slice(lower, upper, step)


def _unshadowed_single_arg(node, name, state):
    """Whether node is an unshadowed call of the builtin `name` with a single
    positional argument and no keywords."""
    if not isinstance(node.func, ast.Name) or node.func.id != name:
        return False
    if name in state.builtin_globals | state.builtin_locals:
        return False
    return len(node.args) == 1 and not node.keywords


def static_slice_read(node, owner):
    """The value a wholly static slice of a known ordered owner yields, or
    None."""
    if not isinstance(node.slice, ast.Slice):
        return None
    bounds = _static_slice(node.slice)
    if bounds is None:
        return None
    return ordered_container(owner, 'list',
                             lambda length: range(length)[bounds])


def reversed_read(node, state):
    """The value an unshadowed `reversed()` of a known ordered operand
    yields, or None."""
    if not _unshadowed_single_arg(node, 'reversed', state):
        return None
    return ordered_container(
        _known_value(node.args[0], state), 'list',
        lambda length: range(length - 1, -1, -1))
