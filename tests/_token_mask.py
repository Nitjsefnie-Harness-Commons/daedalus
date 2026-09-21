"""Conservative lexical mask for the token policy, indexed by UTF-8 byte."""
from dataclasses import dataclass
import re


_TERMINATORS = '\n\r\u2028\u2029'
_REGEX_WORDS = frozenset(
    ('case default delete do else extends in instanceof new return '
     'throw typeof void').split())
_CONTEXT_WORDS = frozenset(('await yield of').split())
_CONTROL_WORDS = frozenset(('if while for with').split())
_WORD = re.compile(r'[\w$]+')
_OPERATOR = re.compile(
    r'\.\.\.|\?\.|\+\+|--|=>|\*\*=?|&&=?|\|\|=?|\?\?=?|'
    r'===?|!==?|[<>]=?|[+*%&|^!~?:,;=.-]')


@dataclass
class TokenMask:
    text: str
    unresolved: tuple

    def lines(self, source):
        offset = 0
        for line, mask in zip(source.split('\n'), self.text.split('\n')):
            end = offset + len(mask)
            refused = any(start <= end and stop > offset
                          for start, stop in self.unresolved)
            yield line, mask, refused
            offset = end + 1


class _Lexer:
    def __init__(self, source):
        self.source = source
        self.cells = ['\n' if char == '\n' else None for char in source]
        self.pos = 0
        self.unresolved = []

    def refuse(self, start):
        self.unresolved.append((start, len(self.source)))
        self.pos = len(self.source)

    def copy(self, end):
        self.cells[self.pos:end] = self.source[self.pos:end]
        self.pos = end

    def quoted(self):
        start = self.pos
        quote = self.source[start]
        self.pos += 1
        while self.pos < len(self.source):
            char = self.source[self.pos]
            self.pos += 1
            if char == quote:
                return
            if char == '\\':
                if self.source[self.pos:self.pos + 2] == '\r\n':
                    self.pos += 2
                else:
                    self.pos += 1
            elif char in '\r\n':
                break
        self.refuse(start)

    def regex(self):
        start = self.pos
        self.pos += 1
        in_class = False
        while self.pos < len(self.source):
            char = self.source[self.pos]
            self.pos += 1
            if char in _TERMINATORS:
                break
            if char == '\\':
                if (self.pos == len(self.source)
                        or self.source[self.pos] in _TERMINATORS):
                    break
                self.pos += 1
            elif char == '[':
                in_class = True
            elif char == ']':
                in_class = False
            elif char == '/' and not in_class:
                flags = _WORD.match(self.source, self.pos)
                if flags:
                    self.pos = flags.end()
                return
        self.refuse(start)

    def template(self, tagged):
        start = self.pos
        if tagged:
            self.cells[start] = '`'
        self.pos += 1
        while self.pos < len(self.source):
            char = self.source[self.pos]
            if char == '`':
                if tagged:
                    self.cells[self.pos] = '`'
                self.pos += 1
                return
            if char == '\\':
                self.pos += 2
            elif self.source[self.pos:self.pos + 2] == '${':
                self.copy(self.pos + 2)
                self.code(interpolation=True)
            else:
                self.pos += 1
        self.refuse(start)

    def at_line_start(self):
        for char, cell in zip(reversed(self.source[:self.pos]),
                              reversed(self.cells[:self.pos])):
            if char in _TERMINATORS:
                return True
            if cell is not None and not cell.isspace():
                return False
        return True

    def code(self, interpolation=False):
        # True requires an operand, False follows one, None is unresolved.
        goal, previous = True, ''
        parens, braces = [], 0
        line_sensitive = False
        while self.pos < len(self.source):
            char = self.source[self.pos]
            two = self.source[self.pos:self.pos + 2]
            if char.isspace() or char == '\ufeff':
                if char in _TERMINATORS and line_sensitive:
                    goal = None
                self.pos += 1
            elif two == '//':
                while (self.pos < len(self.source)
                       and self.source[self.pos] not in _TERMINATORS):
                    self.pos += 1
            elif two == '/*':
                end = self.source.find('*/', self.pos + 2)
                if end < 0:
                    self.refuse(self.pos)
                else:
                    if line_sensitive and any(
                            c in _TERMINATORS
                            for c in self.source[self.pos:end + 2]):
                        goal = None
                    self.pos = end + 2
            elif (self.source.startswith('<!--', self.pos)
                  or (self.source.startswith('-->', self.pos)
                      and self.at_line_start())):
                self.refuse(self.pos)
            elif char in '\'"':
                self.quoted()
                goal = None if previous in ('import', 'from') else False
                previous, line_sensitive = 'literal', False
            elif char == '`':
                self.template(tagged=goal is not True)
                goal, previous = False, 'literal'
                line_sensitive = False
            elif char == '/':
                if goal is None:
                    self.refuse(self.pos)
                elif goal:
                    self.regex()
                    goal, previous = False, 'literal'
                else:
                    self.copy(self.pos + (2 if two == '/=' else 1))
                    goal, previous = True, '/'
            elif char == '(':
                parens.append(previous in _CONTROL_WORDS)
                self.copy(self.pos + 1)
                goal, previous = True, '('
            elif char == ')':
                goal = parens.pop() if parens else None
                self.copy(self.pos + 1)
                previous, line_sensitive = ')', False
            elif char in '{}[]':
                self.copy(self.pos + 1)
                if char == '{':
                    braces += 1
                elif char == '}':
                    if interpolation and braces == 0:
                        return
                    braces -= 1
                goal = None if char == '}' else char in '[{'
                previous, line_sensitive = char, False
            else:
                word = _WORD.match(self.source, self.pos)
                operator = _OPERATOR.match(self.source, self.pos)
                if word:
                    value = word.group()
                    member = previous in ('.', '?.')
                    goal = (False if member else None
                            if value in _CONTEXT_WORDS
                            else value in _REGEX_WORDS)
                    line_sensitive = (goal is False and not member
                                      and not value.isdecimal())
                    previous = ('member' if member else 'for'
                                if previous == 'for' and value == 'await'
                                else value)
                    self.copy(word.end())
                elif operator:
                    value = operator.group()
                    goal = value not in ('++', '--', '.', '?.')
                    previous, line_sensitive = value, False
                    self.copy(operator.end())
                else:
                    self.refuse(self.pos)
        if interpolation:
            self.refuse(self.pos)


def token_mask(source):
    lexer = _Lexer(source)
    lexer.code()
    offsets, mask = [0], []
    for char, cell in zip(source, lexer.cells):
        encoded = char.encode('utf-8')
        offsets.append(offsets[-1] + len(encoded))
        mask.append(' ' * len(encoded) if cell is None
                    else cell.encode('utf-8').decode('latin1'))
    return TokenMask(''.join(mask), tuple(
        (offsets[start], offsets[end]) for start, end in lexer.unresolved))
