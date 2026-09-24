"""Set-operand folds for the Python routing guard's binary operators."""
import ast

from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER, DeferredContainer,
                             _known_value, merge_yielded)

SET_OPERATORS = (ast.BitOr, ast.BitAnd, ast.BitXor, ast.Sub)
# `set()` and `frozenset()` name a set the model holds nothing for, so the
# container kind alone would not classify an operation over one.
SET_FACTORIES = ('frozenset', 'set')


def _names_a_set(side, value, state):
    if isinstance(value, DeferredContainer):
        return value.kind == 'set'
    return (isinstance(side, ast.Call) and not side.keywords
            and isinstance(side.func, ast.Name)
            and side.func.id in SET_FACTORIES
            and side.func.id not in state.builtin_globals
            and side.func.id not in state.builtin_locals)


def _names_a_mapping(side, value):
    return isinstance(side, ast.Dict) or (
        isinstance(value, DeferredContainer) and value.kind == 'dict')


def set_operands(operator, left, right, state):
    """The two operand values when this is a set operation, else None.

    A mapping operand keeps the merge that already handles it; an operation
    over a set is one whatever the other side proves itself to be.
    """
    if not isinstance(operator, SET_OPERATORS):
        return None
    sides = (left, right)
    operands = tuple(_known_value(side, state) for side in sides)
    if not any(_names_a_set(*pair, state) for pair in zip(sides, operands)):
        return None
    if any(_names_a_mapping(*pair) for pair in zip(sides, operands)):
        return None
    return operands


def _elements(value):
    """The elements one operand contributes. An operand the model cannot
    resolve contributes the uncertainty token instead of nothing: dropping
    it would let the result read cleaner than the code."""
    if isinstance(value, DeferredContainer):
        return list(value.items.values())
    return [UNPROVABLE_SENDER]


def fold_set_operation(operator, operands, node):
    """The set a set operation's result must still hold.

    `A | B`, `A ^ B` and `A & B` are subsets of `A | B` and `A - B` a subset
    of `A`, so this joins both operands for the first three and the left
    alone for the last -- the most precise each rule permits. A set has no
    positions and equal elements collapse at runtime, so the elements join
    one dynamic slot.
    """
    sides = operands[:1] if isinstance(operator, ast.Sub) else operands
    joined = merge_yielded(
        element for value in sides for element in _elements(value))
    return DeferredContainer(
        {} if joined is None else {DYNAMIC_KEY: joined}, None, 'set', node)
