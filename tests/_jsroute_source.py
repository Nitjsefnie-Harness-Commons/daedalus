"""Position indexes shared by the JavaScript routing guard."""
from bisect import bisect_right
import re

from _jsread import js_bracket_end, js_expression_end


_ASYNC = re.compile(r'async\b\s*')
_FUNCTION = re.compile(r'function(?:\s+[\w$]+)?\s*\(')
_ARROW = re.compile(r'\s*=>\s*')
_PARAMETER_ARROW = re.compile(r'[\w$]+\s*=>\s*')

BUILTIN_CHAINS = {
    'array': {'concat', 'filter', 'flat', 'flatMap', 'map', 'slice'},
    'function': {'bind'},
    'string': {
        'endsWith', 'includes', 'slice', 'startsWith', 'substring', 'trim'}}


def statement_end(mask, position):
    """Return a statement's semicolon, ASI newline, or source end."""
    depth = 0
    for index in range(position, len(mask)):
        char = mask[index]
        if char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ';' and depth == 0:
            return index
        elif (char == '\n' and depth == 0
              and mask[position:index].strip()):
            return index
    return len(mask)


def function_body_at(mask, start):
    """Return a function value's block or expression body."""
    while start < len(mask) and mask[start].isspace():
        start += 1
    async_prefix = _ASYNC.match(mask, start)
    if async_prefix:
        start = async_prefix.end()
    function = _FUNCTION.match(mask, start)
    if function:
        paren = function.end() - 1
        after = js_bracket_end(mask, paren)
        brace = mask.find('{', after)
        if brace != -1:
            return ('block', brace, js_bracket_end(mask, brace))
        return None
    if mask[start:start + 1] == '(':
        after = js_bracket_end(mask, start)
        arrow = _ARROW.match(mask, after)
        if not arrow:
            return None
        body = arrow.end()
    else:
        parameter = _PARAMETER_ARROW.match(mask, start)
        if not parameter:
            return None
        body = parameter.end()
    if mask[body:body + 1] == '{':
        return ('block', body, js_bracket_end(mask, body))
    if mask[body:body + 1] == '(':
        return ('expr', body + 1, js_bracket_end(mask, body) - 1)
    return ('expr', body, js_expression_end(mask, body))


class _Intervals:
    def __init__(self, ranges, default):
        events = {}
        for start, end, value in ranges:
            if start >= end:
                continue
            events.setdefault(start, [[], []])[1].append(
                (end - start, value))
            events.setdefault(end, [[], []])[0].append(
                (end - start, value))
        active = []
        self.starts = []
        self.values = []
        for position in sorted(events):
            ending, starting = events[position]
            for item in ending:
                if item in active:
                    active.remove(item)
            active.extend(starting)
            self.starts.append(position)
            self.values.append(
                min(active, default=(0, default))[1])
        self.default = default

    def at(self, position):
        index = bisect_right(self.starts, position) - 1
        return self.values[index] if index >= 0 else self.default


class SourceIndex:
    """Cache source positions, function bodies, and lexical ownership."""

    def __init__(self, text, mask, body_reader):
        self.text = text
        self.mask = mask
        self._body_reader = body_reader
        self._bodies = {}
        self._line_starts = [0]
        self._line_starts.extend(
            position + 1 for position, char in enumerate(text)
            if char == '\n')
        self._scope = None
        self._brace = None
        self._scopes = None

    def body_at(self, mask, start):
        del mask
        if start not in self._bodies:
            self._bodies[start] = self._body_reader(self.mask, start)
        return self._bodies[start]

    def line_of(self, position):
        return bisect_right(self._line_starts, position)

    def configure_ranges(self, scopes, braces):
        self._scopes = scopes
        self._scope = _Intervals(
            ((scope['param_start'], scope['end'], index)
             for index, scope in enumerate(scopes)), 0)
        self._brace = _Intervals(
            ((start, end, (start, end)) for start, end in braces),
            (0, len(self.mask)))

    def scope_at(self, position):
        return self._scope.at(position)

    def lexical_range(self, position, function_only=False):
        if function_only:
            scope = self._scopes[self.scope_at(position)]
            return scope['start'], scope['end']
        return self._brace.at(position)
