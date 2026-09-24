"""Literal evaluation and the mapping keys one lookup can name.

`_literal_value` folds the literal forms a program writes. `_usable_key`
keeps only what can also be a dict key, and `_literal_key` names the one a
lookup resolves. `_unhashable_key_sender` is the discriminator for the one
unresolved key the runtime cannot hash at all: a literal list, a tuple of
one, a dict or a set, spelled bound to a name or written inline at the
call. Every other key the evaluator cannot fold is hashable and the
program routes it fine, so those stay unresolved and silent.
"""
import ast
import operator
from collections.abc import Sized
from typing import cast

from _pyroute_values import UNPROVABLE_SENDER

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
        # A set or a dict literal only ever folds to a set or a dict, so
        # anything that is not the sentinel has a length.
        return None if value is _UNSAFE_LITERAL \
            else len(cast(Sized, value))
    return None


def literal_truth(expr):
    value = _literal_value(expr)
    return None if value is _UNSAFE_LITERAL else bool(value)


_UNRESOLVED_KEY = object()


def _usable_key(value):
    """The value when it can also be a dict key, else _UNRESOLVED_KEY."""
    if value is _UNSAFE_LITERAL:
        return _UNRESOLVED_KEY
    try:
        hash(value)
    except TypeError:
        return _UNRESOLVED_KEY
    return value


def _literal_key(node, state):
    """The literal key one mapping lookup names, or _UNRESOLVED_KEY.

    A name carries the literal it was bound to and any other expression is
    read through the same literal evaluator, so a constant, a tuple and a
    unary-minus literal are all their own keys. An f-string, a
    concatenation and every other expression the evaluator cannot fold, a
    name bound to no literal, and a literal that cannot be a dict key all
    stay unresolved.
    """
    if isinstance(node, ast.Name):
        return _usable_key(state.literals.get(node.id, _UNSAFE_LITERAL))
    return _usable_key(_literal_value(node))


def _unhashable_key_sender(node, state):
    """UNPROVABLE_SENDER when the key is a literal the runtime cannot hash.

    A key the runtime cannot hash raises before the call returns, so
    the call is unprovable; one only too complex to fold names every
    stored item, as a plain read of the same shape does.
    """
    # `probe is not None` is a choice, not a requirement: a name bound
    # to no literal probes as None, hashable, and fails the test beside
    # it anyway; the `_UNSAFE_LITERAL` conjunct is the load-bearing one.
    probe = state.literals.get(node.id) if isinstance(
        node, ast.Name) else _literal_value(node)
    return UNPROVABLE_SENDER if probe is not None \
        and probe is not _UNSAFE_LITERAL \
        and _usable_key(probe) is _UNRESOLVED_KEY else None
