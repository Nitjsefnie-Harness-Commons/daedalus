"""Literal evaluation and the mapping keys one lookup can name.

`_literal_value` folds the literal forms a program writes. `_usable_key`
keeps only what can also be a dict key, `_literal_key` names the one a
lookup resolves, and `payload_literal_key` names the one a tracked payload's
key position resolves. `_unhashable_key_sender` is the discriminator for the
one unresolved key the runtime cannot hash at all: a literal list, a tuple
containing one, a dict or a set, spelled bound to a name or written inline
at the call. Every other key the evaluator cannot fold is hashable and the
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


def payload_literal_key(node, literals):
    """The string key one payload key position names, or None.

    The payload model's twin of `_literal_key`, over the same
    name-to-literal table: a string constant names itself, a name
    carries the literal it was bound to, a walrus names what it binds,
    and any other expression is read through the same pure-AST fold.
    The walrus is read here rather than in `_literal_value` so that
    folding one stays a fact about the payload model rather than a
    change to every reader of the shared evaluator.

    None is an answer, not a refusal, and the three ways to reach it are
    three different runtimes. A non-string literal is provably not
    `'tab'`. An expression the fold will not resolve names no tracked key
    here, because a program whose key is a call did not mean `'tab'`
    either — but that is a claim about the position, not about the
    runtime: CPython folds adjacent literals, a constant `+` and a
    field-free f-string at compile time, so such an expression can name
    `'tab'` at runtime while reading None here. The boundary is the
    parser's own folding, and widening the fold onto the shapes it
    misses is #1352. So None means this position names no tracked key,
    and a caller must not read it as an opaque one.
    """
    if isinstance(node, ast.Name):
        value = (literals or {}).get(node.id, _UNSAFE_LITERAL)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    elif isinstance(node, ast.NamedExpr):
        return payload_literal_key(node.value, literals)
    else:
        value = _literal_value(node)
    return value if isinstance(value, str) else None


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
