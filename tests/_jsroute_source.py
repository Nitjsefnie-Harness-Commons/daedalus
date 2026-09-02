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


def record_work(work, name, amount=1):
    """Add deterministic scanner work to an optional counter."""
    if work is not None:
        work[name] = work.get(name, 0) + amount


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

    def __init__(self, text, mask, body_reader, work=None):
        self.text = text
        self.mask = mask
        self._body_reader = body_reader
        self.work = work
        record_work(work, 'source_characters', len(text))
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
        record_work(self.work, 'source_body_queries')
        if start not in self._bodies:
            record_work(self.work, 'source_body_misses')
            self._bodies[start] = self._body_reader(self.mask, start)
        return self._bodies[start]

    def line_of(self, position):
        record_work(self.work, 'source_line_queries')
        return bisect_right(self._line_starts, position)

    def configure_ranges(self, scopes, braces):
        record_work(
            self.work, 'source_range_entries', len(scopes) + len(braces))
        self._scopes = scopes
        self._scope = _Intervals(
            ((scope['param_start'], scope['end'], index)
             for index, scope in enumerate(scopes)), 0)
        self._brace = _Intervals(
            ((start, end, (start, end)) for start, end in braces),
            (0, len(self.mask)))

    def scope_at(self, position):
        record_work(self.work, 'source_scope_queries')
        return self._scope.at(position)

    def lexical_range(self, position, function_only=False):
        record_work(self.work, 'source_range_queries')
        if function_only:
            scope = self._scopes[self.scope_at(position)]
            return scope['start'], scope['end']
        return self._brace.at(position)


def top_level(mask, start, end, char):
    """Offset of the first `char` at bracket depth 0 in the span, or None."""
    depth = 0
    for i in range(start, end):
        c = mask[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth < 0:
                return None
        elif c == char and depth == 0:
            return i
    return None


def previous_nonspace(mask, position):
    position -= 1
    while position >= 0 and mask[position].isspace():
        position -= 1
    return position


def word_before(mask, position):
    position = previous_nonspace(mask, position) + 1
    end = position
    while (position > 0
           and (mask[position - 1].isalnum()
                or mask[position - 1] in '_$')):
        position -= 1
    return mask[position:end]


def method_head_start(mask, name_start, pair_start):
    """Offset of the token a method shorthand's head must follow.

    Walks back over `*`, the `get`/`set`/`async` heads and a computed key's
    bracket pair, so a method body inside a literal is recognised whatever
    its head spelling is. `name_start` is the offset of the method's name.
    """
    position = previous_nonspace(mask, name_start)
    while position >= 0:
        char = mask[position:position + 1]
        if char in ('#', '*', ']', '='):
            opening = pair_start.get(position)
            if char == ']' and opening is not None:
                position = previous_nonspace(mask, opening)
                continue
            if char == '=':
                break
            position = previous_nonspace(mask, position)
            continue
        word_start = position
        while (word_start > 0
               and (mask[word_start - 1].isalnum()
                    or mask[word_start - 1] in '_$')):
            word_start -= 1
        if mask[word_start:position + 1] in ('get', 'set', 'async', 'static'):
            position = previous_nonspace(mask, word_start)
            continue
        break
    return position


def _brace_depth(mask):
    """Whether each offset sits inside a brace pair."""
    found = bytearray(len(mask) + 1)
    depth = 0
    for position, char in enumerate(mask):
        if char == '}' and depth:
            depth -= 1
        found[position] = 1 if depth else 0
        if char == '{':
            depth += 1
    return found


def method_scope_openings(mask, text, pair_end, pair_start):
    """Parameter, body and declaration offsets for method shorthand."""
    candidates = []
    names = re.compile(
        r'(?<![\w$#\\])#?((?:\\u(?:\{[0-9A-Fa-f]+\}'
        r'|[0-9A-Fa-f]{4})|[\w$])+)\s*\(')
    for match in names.finditer(mask):
        head = match.start(1)
        marker = head + (mask[head:head + 1] == '\\')
        candidates.append((head, mask.index(
            '(', head, match.end()), marker))
    numbers = re.compile(
        r'(?<![\w$.])(?:\d[\d_]*\.\d[\d_]*'
        r'(?:[eE][+-]?\d[\d_]*)?|\.\d[\d_]*'
        r'(?:[eE][+-]?\d[\d_]*)?|\d[\d_]*[eE][+-]?\d[\d_]*)\s*\(')
    for match in numbers.finditer(mask):
        opening = mask.index('(', match.start(), match.end())
        marker = previous_nonspace(mask, opening)
        while (marker > match.start()
               and (mask[marker - 1].isalnum()
                    or mask[marker - 1] in '_$')):
            marker -= 1
        candidates.append((match.start(), opening, marker))
    quoted = re.compile(
        r'(["\'])(?:\\[\s\S]|(?!\1)[^\\])*\1\s*\(')
    for match in quoted.finditer(text):
        opening = text.rfind('(', match.start(), match.end())
        candidates.append((match.start(), opening, opening))
    computed = re.compile(
        r'(?<![\w$])\[(?:[^\[\]]|\[[^\[\]]*\])*\]\s*\(')
    for match in computed.finditer(mask):
        opening = pair_end.get(match.start(), match.start())
        candidates.append((match.start(), opening, opening))
    inside = _brace_depth(mask)
    for head, opening, marker in candidates:
        if mask[head:opening].strip() in {
                'catch', 'for', 'if', 'switch', 'while', 'with'}:
            continue
        # A shorthand stands in an object literal or a class body, never
        # at the top level: there a name, a call and a block are three
        # statements, and reading them as one head loses the call.
        if not inside[head]:
            continue
        close = pair_end.get(opening, len(mask))
        body_open = close
        while body_open < len(mask) and mask[body_open].isspace():
            body_open += 1
        previous = method_head_start(mask, head, pair_start)
        if (mask[body_open:body_open + 1] == '{'
                and word_before(mask, head) != 'function'
                and mask[previous:previous + 1] in '{,;}'):
            yield opening, body_open, marker
