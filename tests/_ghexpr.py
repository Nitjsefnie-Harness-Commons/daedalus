"""Read the small GitHub Actions expression grammar used by these workflows.

The grammar admits ``&&``, ``||``, ``!``, ``==``, ``!=``, parentheses,
single-quoted strings (with doubled quotes for an embedded quote), the boolean
literals ``true`` and ``false``, dotted context lookups, and the zero-argument
status functions ``always()``, ``success()``, ``failure()``, and
``cancelled()``.  An expression may optionally be wrapped in ``${{ ... }}``.
Status results come from ``context['status']`` and every context path must
exist.  Actions' scalar truthiness and loose equality are implemented for
``None``, booleans, numbers, and strings; mappings and sequences are truthy but
refused in comparisons.  Other syntax, operators, functions, values, or
ambiguous comparison shapes raise ``ExpressionError`` instead of being
treated as false.
"""
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass


class ExpressionError(ValueError):
    """The expression or supplied context is outside the admitted grammar."""


@dataclass(frozen=True)
class _Token:
    """One token from the expression source."""

    kind: str
    value: str = ''


_NUMBER = re.compile(
    r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')
_STATUS_FUNCTIONS = frozenset(('always', 'success', 'failure', 'cancelled'))
_NAN = object()


def evaluate(expression, context):
    """Evaluate one condition to an Actions-style boolean result."""
    if not isinstance(expression, str):
        raise ExpressionError('expression must be a string')
    source = _unwrap(expression)
    if not isinstance(context, Mapping):
        raise ExpressionError('context must be a mapping')
    parser = _Parser(_tokenize(source), context)
    return _truthy(parser.parse())


def _unwrap(expression):
    """Remove one complete Actions expression wrapper, if present."""
    source = expression.strip()
    starts = source.startswith('${{')
    ends = source.endswith('}}')
    if starts != ends:
        raise ExpressionError('incomplete expression wrapper')
    if starts:
        source = source[3:-2].strip()
    if not source:
        raise ExpressionError('empty expression')
    if '${{' in source or '}}' in source:
        raise ExpressionError('nested expression wrapper')
    return source


def _tokenize(source):
    """Tokenize only operators and literals this reader understands."""
    tokens = []
    index = 0
    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
            continue
        pair = source[index:index + 2]
        if pair in ('&&', '||', '==', '!='):
            tokens.append(_Token(pair))
            index += 2
            continue
        if char in '!().':
            tokens.append(_Token(char))
            index += 1
            continue
        if char == "'":
            value, index = _string_token(source, index)
            tokens.append(_Token('STRING', value))
            continue
        if char.isalpha() or char == '_':
            end = index + 1
            while end < len(source):
                current = source[end]
                if not (current.isalnum() or current in '_-'):
                    break
                end += 1
            value = source[index:end]
            kind = 'BOOL' if value in ('true', 'false') else 'IDENT'
            tokens.append(_Token(kind, value))
            index = end
            continue
        raise ExpressionError(
            f'unsupported syntax at character {index}: {char!r}')
    tokens.append(_Token('EOF'))
    return tokens


def _string_token(source, start):
    """Read an Actions single-quoted string and its following index."""
    index = start + 1
    chars = []
    while index < len(source):
        char = source[index]
        if char == "'":
            if index + 1 < len(source) and source[index + 1] == "'":
                chars.append("'")
                index += 2
                continue
            return ''.join(chars), index + 1
        if char in '\r\n':
            raise ExpressionError('newline in string literal')
        chars.append(char)
        index += 1
    raise ExpressionError('unterminated string literal')


class _Parser:
    """Recursive-descent parser and evaluator for the admitted grammar."""

    def __init__(self, tokens, context):
        self._tokens = tokens
        self._context = context
        self._index = 0

    def parse(self):
        value = self._parse_or()
        if self._peek().kind != 'EOF':
            raise ExpressionError(
                f'unexpected token: {self._peek().value or self._peek().kind}')
        return value

    def _parse_or(self):
        value = self._parse_and()
        while self._accept('||'):
            right = self._parse_and()
            value = value if _truthy(value) else right
        return value

    def _parse_and(self):
        value = self._parse_equality()
        while self._accept('&&'):
            right = self._parse_equality()
            value = right if _truthy(value) else value
        return value

    def _parse_equality(self):
        value = self._parse_unary()
        operator = self._peek().kind
        if operator not in ('==', '!='):
            return value
        self._index += 1
        right = self._parse_unary()
        if self._peek().kind in ('==', '!='):
            raise ExpressionError('comparison chaining is unsupported')
        return _compare(value, right, operator)

    def _parse_unary(self):
        if self._accept('!'):
            return not _truthy(self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self):
        token = self._peek()
        if token.kind == 'STRING':
            self._index += 1
            return token.value
        if token.kind == 'BOOL':
            self._index += 1
            return token.value == 'true'
        if token.kind == '(':
            self._index += 1
            value = self._parse_or()
            self._expect(')')
            return value
        if token.kind == 'IDENT':
            return self._parse_identifier()
        raise ExpressionError(
            f'expected a value, found {token.value or token.kind}')

    def _parse_identifier(self):
        name = self._take('IDENT').value
        if self._accept('('):
            if not self._accept(')'):
                raise ExpressionError('status functions take no arguments')
            if name not in _STATUS_FUNCTIONS:
                raise ExpressionError(f'unsupported function: {name}')
            return self._status_value(name)
        path = [name]
        while self._accept('.'):
            path.append(self._take('IDENT').value)
        return self._lookup(path)

    def _status_value(self, name):
        status = self._context.get('status')
        if not isinstance(status, Mapping):
            raise ExpressionError('context.status must be a mapping')
        if name not in status:
            raise ExpressionError(f'missing status value: {name}')
        return _admit(status[name])

    def _lookup(self, path):
        value = self._context
        for part in path:
            if not isinstance(value, Mapping) or part not in value:
                raise ExpressionError(
                    'missing context path: ' + '.'.join(path))
            value = value[part]
        return _admit(value)

    def _peek(self):
        return self._tokens[self._index]

    def _accept(self, kind):
        if self._peek().kind != kind:
            return False
        self._index += 1
        return True

    def _expect(self, kind):
        if not self._accept(kind):
            token = self._peek()
            raise ExpressionError(
                f'expected {kind}, found {token.value or token.kind}')

    def _take(self, kind):
        token = self._peek()
        if token.kind != kind:
            raise ExpressionError(
                f'expected {kind}, found {token.value or token.kind}')
        self._index += 1
        return token


def _admit(value):
    """Reject context values whose Actions behavior is not known."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (Mapping, list, tuple)):
        return value
    raise ExpressionError(f'unsupported context value: {type(value).__name__}')


def _truthy(value):
    """Apply Actions' admitted falsey values; arrays and objects are truthy."""
    _admit(value)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0 and not (
            isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ''
    return True


def _compare(left, right, operator):
    """Apply Actions' case-insensitive, loose equality for admitted scalars."""
    _admit(left)
    _admit(right)
    if isinstance(left, (Mapping, list, tuple)) or isinstance(
            right, (Mapping, list, tuple)):
        raise ExpressionError('object and array comparisons are unsupported')

    if _same_scalar_type(left, right):
        equal = _same_type_equal(left, right)
    else:
        left_number = _to_number(left)
        right_number = _to_number(right)
        if left_number is _NAN or right_number is _NAN:
            equal = False
        else:
            equal = left_number == right_number
    return equal if operator == '==' else not equal


def _same_scalar_type(left, right):
    """Return whether Actions compares these operands without coercion."""
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool)
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        return (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
        )
    return isinstance(left, str) and isinstance(right, str)


def _same_type_equal(left, right):
    """Compare operands whose types already match Actions' scalar classes."""
    if isinstance(left, str):
        return left.casefold() == right.casefold()
    return left == right


def _to_number(value):
    """Coerce a scalar as Actions does for mixed-type equality."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if value == '':
            return 0.0
        if not _NUMBER.fullmatch(value):
            return _NAN
        number = float(value)
        return number if math.isfinite(number) else _NAN
    return _NAN
