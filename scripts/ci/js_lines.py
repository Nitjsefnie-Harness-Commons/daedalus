#!/usr/bin/env python3
"""Identify physical JavaScript lines that contain non-comment code."""

_LINE_ENDS = '\n\r\u2028\u2029'
_EXPRESSION_KEYWORDS = {'false', 'null', 'super', 'this', 'true'}
_REGEX_KEYWORDS = {
    'await', 'case', 'delete', 'do', 'else', 'in', 'instanceof', 'new',
    'of', 'return', 'throw', 'typeof', 'void', 'yield',
}
_OTHER_KEYWORDS = {
    'break', 'catch', 'class', 'const', 'continue', 'debugger', 'default',
    'enum', 'export', 'extends', 'finally', 'for', 'function', 'if',
    'implements', 'import', 'interface', 'let', 'package', 'private',
    'protected', 'public', 'static', 'switch', 'try', 'var',
    'while', 'with',
}
_REGEX_PUNCTUATORS = set('(,=: [!&|?+-*/%<>^~;{'.replace(' ', ''))
_EXPRESSION_PUNCTUATORS = set(')]')


def _identifier_start(char):
    return char in '_$' or char.isalpha() or ord(char) >= 128


def _identifier_part(char):
    return _identifier_start(char) or char.isdigit()


def _javascript_whitespace(char):
    return char == '\ufeff' or char.isspace()


class _Scanner:
    """One forward cursor over JavaScript source."""

    def __init__(self, source, path):
        self.source = source
        self.path = path
        self.index = 0
        self.line = 1
        self.lines = set()

    def scan(self):
        self._code()
        return self.lines

    def _peek(self, distance=0):
        index = self.index + distance
        return self.source[index] if index < len(self.source) else ''

    def _advance(self, code=False):
        char = self._peek()
        if not char:
            return
        if char == '\r':
            self.index += 1
            if self._peek() == '\n':
                self.index += 1
            self.line += 1
            return
        if char in '\n\u2028\u2029':
            self.index += 1
            self.line += 1
            return
        if code and not _javascript_whitespace(char):
            self.lines.add(self.line)
        self.index += 1

    def _error(self, shape, line=None):
        line = self.line if line is None else line
        raise ValueError(f'{self.path}: line {line}: {shape}')

    def _code(self, template_line=None):
        previous = 'start' if template_line is None else 'prefix'
        brace_depth = 0
        while self.index < len(self.source):
            char = self._peek()
            following = self._peek(1)
            if _javascript_whitespace(char):
                self._advance()
                continue
            if char == '/' and following == '/':
                self._line_comment()
                continue
            if char == '/' and following == '*':
                self._block_comment()
                continue
            if char in "'\"":
                self._string(char)
                previous = 'expression'
                continue
            if char == '`':
                self._template()
                previous = 'expression'
                continue
            if char == '?' and following == '.' and previous == 'expression':
                self._advance(code=True)
                self._advance(code=True)
                previous = 'property'
                continue
            if char == '.' and previous == 'expression':
                self._advance(code=True)
                previous = 'property'
                continue
            if char == '/':
                if previous in ('start', 'prefix'):
                    self._regex()
                    previous = 'expression'
                elif previous == 'expression':
                    self._advance(code=True)
                    previous = 'prefix'
                elif previous == 'brace':
                    self._error('slash after } cannot be classified')
                else:
                    self._error('unclassified slash')
                continue
            if char in '+-' and following == char:
                self._advance(code=True)
                self._advance(code=True)
                previous = 'expression'
                continue
            if char == '\\':
                self._error('escaped identifier is not modelled')
            if _identifier_start(char):
                token = self._identifier()
                if previous == 'property':
                    previous = 'expression'
                elif token in _REGEX_KEYWORDS:
                    previous = 'prefix'
                elif token in _EXPRESSION_KEYWORDS:
                    previous = 'expression'
                elif token in _OTHER_KEYWORDS:
                    previous = 'unknown'
                else:
                    previous = 'expression'
                continue
            if char.isdigit():
                self._number()
                previous = 'expression'
                continue
            self._advance(code=True)
            if char == '{':
                brace_depth += 1
                previous = 'prefix'
            elif char == '}':
                if template_line is not None and brace_depth == 0:
                    return
                brace_depth -= 1
                previous = 'brace'
            elif char in _EXPRESSION_PUNCTUATORS:
                previous = 'expression'
            elif char in _REGEX_PUNCTUATORS:
                previous = 'prefix'
            else:
                previous = 'unknown'
        if template_line is not None:
            self._error('unterminated template', template_line)

    def _identifier(self):
        start = self.index
        while self._peek() and _identifier_part(self._peek()):
            self._advance(code=True)
        return self.source[start:self.index]

    def _number(self):
        previous = ''
        while self._peek():
            char = self._peek()
            if char.isalnum() or char in '._':
                previous = char
                self._advance(code=True)
                continue
            if char in '+-' and previous in 'eE':
                previous = char
                self._advance(code=True)
                continue
            return

    def _line_comment(self):
        self._advance()
        self._advance()
        while self._peek() and self._peek() not in _LINE_ENDS:
            self._advance()

    def _block_comment(self):
        opening_line = self.line
        self._advance()
        self._advance()
        while self.index < len(self.source):
            if self._peek() == '*' and self._peek(1) == '/':
                self._advance()
                self._advance()
                return
            self._advance()
        self._error('unterminated block comment', opening_line)

    def _string(self, quote):
        opening_line = self.line
        self._advance(code=True)
        while self.index < len(self.source):
            char = self._peek()
            if char == quote:
                self._advance(code=True)
                return
            if char in _LINE_ENDS:
                self._error('unterminated string', opening_line)
            if char == '\\':
                self._advance(code=True)
                if self.index == len(self.source):
                    break
                self._advance(code=True)
                continue
            self._advance(code=True)
        self._error('unterminated string', opening_line)

    def _template(self):
        opening_line = self.line
        self._advance(code=True)
        while self.index < len(self.source):
            char = self._peek()
            if char == '`':
                self._advance(code=True)
                return
            if char == '\\':
                self._advance(code=True)
                if self.index == len(self.source):
                    break
                self._advance(code=True)
                continue
            if char == '$' and self._peek(1) == '{':
                self._advance(code=True)
                self._advance(code=True)
                self._code(template_line=opening_line)
                continue
            self._advance(code=True)
        self._error('unterminated template', opening_line)

    def _regex(self):
        opening_line = self.line
        in_class = False
        self._advance(code=True)
        while self.index < len(self.source):
            char = self._peek()
            if char in _LINE_ENDS:
                self._error('unterminated regex', opening_line)
            if char == '\\':
                self._advance(code=True)
                if self.index == len(self.source):
                    break
                if self._peek() in _LINE_ENDS:
                    self._error('unterminated regex', opening_line)
                self._advance(code=True)
                continue
            if char == '[':
                in_class = True
            elif char == ']':
                in_class = False
            elif char == '/' and not in_class:
                self._advance(code=True)
                while self._peek() and _identifier_part(self._peek()):
                    self._advance(code=True)
                return
            self._advance(code=True)
        self._error('unterminated regex', opening_line)


def code_lines(source, path='<unknown>'):
    """Return 1-based physical lines containing non-comment JavaScript."""
    return _Scanner(source, path).scan()
