"""Read the bounded expression subset used by these two workflows.

The reader admits ``&&``, ``||``, ``!``, ``==``, ``!=``, parentheses,
single-quoted strings with doubled quotes, ``true``, ``false``, ``null``,
dotted context lookups, and the zero-argument status functions
``always()``, ``success()``, ``failure()``, and ``cancelled()``.  An optional
outer ``${{ ... }}`` wrapper is accepted.  Comparisons admit ASCII-only
strings, and a string may be compared with a boolean or null by its canonical
ASCII spelling; booleans and null compare only to the same type.  Mappings and
sequences are admitted only for truthiness.  Numeric literals and numeric
context values are refused, as are non-ASCII comparison strings.  Source
length, token count, and nesting depth are explicitly bounded.  Everything
outside this domain raises ``ExpressionError`` rather than approximating
GitHub Actions semantics.
"""
from collections.abc import Mapping
from dataclasses import dataclass


class ExpressionError(ValueError):
    """The expression or context is outside the admitted bounded domain."""


MAX_SOURCE_LENGTH = 4096
"""Maximum source characters, including an optional outer wrapper."""

MAX_TOKEN_COUNT = 256
"""Maximum tokens, including the terminal EOF token."""

MAX_NESTING_DEPTH = 64
"""Maximum nested unary operators and parenthesis groups."""


@dataclass(frozen=True)
class _Token:
    """One token from the expression source."""

    kind: str
    value: str = ''


_STATUS_FUNCTIONS = frozenset(('always', 'success', 'failure', 'cancelled'))


def evaluate(expression, context):
    """Evaluate one condition to a boolean result in the admitted subset."""
    if not isinstance(expression, str):
        raise ExpressionError('expression must be a string')
    if len(expression) > MAX_SOURCE_LENGTH:
        raise ExpressionError(
            f'source length exceeds limit {MAX_SOURCE_LENGTH}')
    source = _unwrap(expression)
    if len(source) > MAX_SOURCE_LENGTH:
        raise ExpressionError(
            f'source length exceeds limit {MAX_SOURCE_LENGTH}')
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


def _identifier_start(char):
    """Return whether a character is an Actions ASCII identifier starter."""
    return ('A' <= char <= 'Z' or 'a' <= char <= 'z' or char == '_')


def _identifier_part(char):
    """Return whether a character is an Actions ASCII identifier part."""
    return (_identifier_start(char) or '0' <= char <= '9' or char == '-')


def _tokenize(source):
    """Tokenize only operators and literals this reader understands."""
    tokens = []

    def add(kind, value=''):
        tokens.append(_Token(kind, value))
        if len(tokens) > MAX_TOKEN_COUNT:
            raise ExpressionError(
                f'token count exceeds limit {MAX_TOKEN_COUNT}')

    index = 0
    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
            continue
        pair = source[index:index + 2]
        if pair in ('&&', '||', '==', '!='):
            add(pair)
            index += 2
            continue
        if char in '!().':
            add(char)
            index += 1
            continue
        if char == "'":
            value, index = _string_token(source, index)
            add('STRING', value)
            continue
        if _identifier_start(char):
            end = index + 1
            while end < len(source) and _identifier_part(source[end]):
                end += 1
            value = source[index:end]
            if value in ('true', 'false'):
                kind = 'BOOL'
            elif value == 'null':
                kind = 'NULL'
            else:
                kind = 'IDENT'
            add(kind, value)
            index = end
            continue
        if char.isdigit() or (
                char in '+-' and index + 1 < len(source)
                and source[index + 1].isdigit()):
            raise ExpressionError(
                'numeric literal is not admitted; numeric comparison is '
                'refused')
        if char.isalpha() or char.isalnum() or ord(char) > 127:
            raise ExpressionError(
                'identifier must use ASCII identifier characters')
        raise ExpressionError(
            f'unsupported syntax at character {index}: {char!r}')
    add('EOF')
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
        self._depth = 0

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
            self._enter_nesting()
            try:
                return not _truthy(self._parse_unary())
            finally:
                self._leave_nesting()
        return self._parse_primary()

    def _parse_primary(self):
        token = self._peek()
        if token.kind == 'STRING':
            self._index += 1
            return token.value
        if token.kind == 'BOOL':
            self._index += 1
            return token.value == 'true'
        if token.kind == 'NULL':
            self._index += 1
            return None
        if token.kind == '(':
            self._index += 1
            self._enter_nesting()
            try:
                value = self._parse_or()
                self._expect(')')
                return value
            finally:
                self._leave_nesting()
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
        return _admit(status[name], 'numeric context value')

    def _lookup(self, path):
        value = self._context
        for part in path:
            if not isinstance(value, Mapping) or part not in value:
                raise ExpressionError(
                    'missing context path: ' + '.'.join(path))
            value = value[part]
        return _admit(value, 'numeric context value')

    def _enter_nesting(self):
        self._depth += 1
        if self._depth > MAX_NESTING_DEPTH:
            self._depth -= 1
            raise ExpressionError(
                f'nesting depth exceeds limit {MAX_NESTING_DEPTH}')

    def _leave_nesting(self):
        self._depth -= 1

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


def _admit(value, reason):
    """Reject values whose semantics are outside this reader's contract."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raise ExpressionError(
            f'{reason} {value!r} is refused; numeric semantics are not '
            'admitted')
    if isinstance(value, (Mapping, list, tuple)):
        return value
    raise ExpressionError(f'unsupported context value: {type(value).__name__}')


def _truthy(value):
    """Apply the admitted falsey values; arrays and objects are truthy."""
    _admit(value, 'numeric context value')
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value != ''
    return True


def _ascii_string(value):
    """Require a string operand to stay in the unambiguous ASCII domain."""
    if any(ord(char) > 127 for char in value):
        raise ExpressionError(
            'non-ASCII string comparison is refused; only ASCII strings '
            'are admitted')
    return value


def _comparison_text(value):
    """Return the admitted textual spelling of a scalar comparison value."""
    if isinstance(value, str):
        return _ascii_string(value)
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if value is None:
        return 'null'
    return None


def _compare(left, right, operator):
    """Compare only the explicitly admitted non-numeric scalar shapes."""
    _admit(left, 'numeric comparison')
    _admit(right, 'numeric comparison')
    if isinstance(left, (Mapping, list, tuple)) or isinstance(
            right, (Mapping, list, tuple)):
        raise ExpressionError('object and array comparisons are unsupported')
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        raise ExpressionError(
            'numeric comparison is refused; numeric semantics are not '
            'admitted')

    left_text = _comparison_text(left)
    right_text = _comparison_text(right)
    if isinstance(left, str) or isinstance(right, str):
        equal = left_text.lower() == right_text.lower()
    else:
        equal = left is right
    return equal if operator == '==' else not equal
