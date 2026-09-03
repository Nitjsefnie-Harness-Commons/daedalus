"""Decode the YAML scalar subset used by the workflow checkout reader."""

import re


_DOUBLE_ESCAPES = {
    '0': '\0', 'a': '\a', 'b': '\b', 't': '\t',
    'n': '\n', 'v': '\v', 'f': '\f', 'r': '\r',
    'e': '\x1b', ' ': ' ', '"': '"', '/': '/',
    '\\': '\\', 'N': '\u0085', '_': '\u00a0',
    'L': '\u2028', 'P': '\u2029',
}

_SCHEMA_NON_STRING_WORDS = frozenset((
    'yes', 'Yes', 'YES', 'no', 'No', 'NO',
    'true', 'True', 'TRUE', 'false', 'False', 'FALSE',
    'on', 'On', 'ON', 'off', 'Off', 'OFF',
    'null', 'Null', 'NULL', '~',
    '<<', '=', '!', '&', '*',
))
_PLAIN_FORBIDDEN_INITIAL = frozenset(',[]{}#&*!|>\'"%@`')
_SCHEMA_TYPED_NUMBER_OR_TIME = re.compile(
    r'(?:'
    r'[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?'
    r'|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?'
    r'|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*'
    r'|[-+]?\.(?:inf|Inf|INF)'
    r'|\.(?:nan|NaN|NAN)'
    r'|[-+]?0b[0-1_]+'
    r'|[-+]?0[0-7_]+'
    r'|[-+]?(?:0|[1-9][0-9_]*)'
    r'|[-+]?0x[0-9a-fA-F_]+'
    r'|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+'
    r'|[0-9]{4}-[0-9]{2}-[0-9]{2}'
    r'|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}'
    r'(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}'
    r'(?:\.[0-9]*)?'
    r'(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?'
    r')')


class _ScalarReaderMixin:
    """Decode scalar values for the structural workflow reader."""

    def _scalar_blank(self, index):
        return not self.lines[index].strip(' ')

    def _scalar_indent(self, index):
        line = self.lines[index]
        return len(line) - len(line.lstrip(' '))

    def _strip_comment(self, text):
        quote = None
        escaped = False
        doubled_quote = False
        for index, char in enumerate(text):
            if doubled_quote:
                doubled_quote = False
                continue
            if quote == '"' and escaped:
                escaped = False
                continue
            if quote == '"' and char == '\\':
                escaped = True
                continue
            if char in ('"', "'"):
                if quote is None:
                    prefix = text[:index].rstrip(' \t')
                    if not prefix or prefix[-1] in '[{,:?-':
                        quote = char
                elif quote == char:
                    if quote == "'" and index + 1 < len(text) \
                            and text[index + 1] == "'":
                        doubled_quote = True
                        continue
                    quote = None
                continue
            if char == '#' and quote is None \
                    and (index == 0 or text[index - 1] in ' \t'):
                return text[:index].rstrip(' \t')
        return text.rstrip(' \t')

    def _mapping_colon_position(self, body):
        """The position of the entry colon in `body`, or None when it has
        none.

        Quote-aware, so a colon inside a quoted key or a quoted value's
        prefix is not the entry colon. This is the one scan of the entry
        text: `_mapping_colon` raises on the None this returns, and the
        value-span checks read the position directly.
        """
        quote = None
        escaped = False
        doubled_quote = False
        for position, char in enumerate(body):
            if doubled_quote:
                doubled_quote = False
                continue
            if quote == '"' and escaped:
                escaped = False
                continue
            if quote == '"' and char == '\\':
                escaped = True
                continue
            if quote is None and char in ('"', "'"):
                quote = char
                continue
            if quote is not None:
                if char == quote:
                    if quote == "'" and position + 1 < len(body) \
                            and body[position + 1] == "'":
                        doubled_quote = True
                        continue
                    quote = None
                continue
            if (char == ':' and (position + 1 == len(body)
                                 or body[position + 1] in ' \t')):
                return position
        return None

    def _mapping_colon(self, body, index, context):
        position = self._mapping_colon_position(body)
        if position is None:
            self._refuse('mapping entry without a colon', index, context)
        return position

    def _mapping_parts(self, index, context, body=None,
                       require_string_key=False):
        if body is None:
            body = self.lines[index].lstrip(' ')
        body = self._strip_comment(body).strip(' \t')
        if body.startswith('?') and (len(body) == 1 or body[1] in ' \t'):
            self._refuse('explicit key', index, context)
        position = self._mapping_colon(body, index, context)
        key_source = body[:position].strip(' \t')
        if not key_source:
            self._refuse('empty mapping key', index, context)
        value = body[position + 1:].strip(' \t')
        key = self._decode_scalar(key_source, index, f'{context} key')
        if require_string_key and not key_source.startswith(("'", '"')):
            self._require_plain_string(key, index, f'{context} key')
        return key, value

    def _decode_scalar(self, text, index, context):
        value = self._strip_comment(text).strip(' \t')
        if not value:
            return ''
        self._reject_prefix(value, index, context)
        if value.startswith("'"):
            return self._single_quoted(value, index, context)
        if value.startswith('"'):
            return self._double_quoted(value, index, context)
        return value

    def _single_quoted(self, value, index, context):
        position = 1
        result = []
        while position < len(value):
            char = value[position]
            if char in ' \t':
                end = position + 1
                while end < len(value) and value[end] in ' \t':
                    end += 1
                if end < len(value) and value[end] == '\n':
                    position = end
                    continue
                result.append(value[position:end])
                position = end
                continue
            if char == '\n':
                folded, position = self._quoted_break(value, position)
                result.append(folded)
                continue
            if char != "'":
                result.append(char)
                position += 1
                continue
            if position + 1 < len(value) and value[position + 1] == "'":
                result.append("'")
                position += 2
                continue
            tail = self._strip_comment(value[position + 1:]).strip(' \t')
            if tail:
                self._refuse('trailing text after single-quoted scalar',
                             index, context)
            return ''.join(result)
        self._refuse('unterminated single-quoted scalar', index, context)
        return None

    def _double_quoted(self, value, index, context):
        position = 1
        result = []
        while position < len(value):
            char = value[position]
            if char == '"':
                tail = self._strip_comment(value[position + 1:]).strip(' \t')
                if tail:
                    self._refuse('trailing text after double-quoted scalar',
                                 index, context)
                return ''.join(result)
            if char in ' \t':
                end = position + 1
                while end < len(value) and value[end] in ' \t':
                    end += 1
                if end < len(value) and value[end] == '\n':
                    position = end
                    continue
                result.append(value[position:end])
                position = end
                continue
            if char == '\n':
                folded, position = self._quoted_break(value, position)
                result.append(folded)
                continue
            if char != '\\':
                result.append(char)
                position += 1
                continue
            if position + 1 >= len(value):
                self._refuse('unterminated double-quoted escape', index,
                             context)
            escaped = value[position + 1]
            if escaped == '\n':
                position += 2
                while position < len(value) and value[position] in ' \t':
                    position += 1
                continue
            if escaped in _DOUBLE_ESCAPES:
                result.append(_DOUBLE_ESCAPES[escaped])
                position += 2
                continue
            lengths = {'x': 2, 'u': 4, 'U': 8}
            if escaped in lengths:
                width = lengths[escaped]
                digits = value[position + 2:position + 2 + width]
                if len(digits) != width or not re.fullmatch(
                        r'[0-9A-Fa-f]+', digits):
                    self._refuse('invalid double-quoted escape', index,
                                 context)
                if int(digits, 16) > 0x10ffff:
                    self._refuse('invalid double-quoted escape', index,
                                 context)
                result.append(chr(int(digits, 16)))
                position += 2 + width
                continue
            self._refuse('unknown double-quote escape', index, context)
        self._refuse('unterminated double-quoted scalar', index, context)
        return None

    def _quoted_break(self, value, position):
        breaks = 0
        while position < len(value) and value[position] == '\n':
            breaks += 1
            position += 1
            while position < len(value) and value[position] in ' \t':
                position += 1
        if breaks == 1:
            return ' ', position
        return '\n' * (breaks - 1), position

    def _quoted_scalar(self, value, index, value_end, context):
        raw_line = self.lines[index]
        trailing = raw_line[len(raw_line.rstrip(' \t')):]
        physical_lines = [value + trailing]
        physical_lines.extend(self.lines[index + 1:value_end])
        scalar = '\n'.join(physical_lines)
        if value.startswith("'"):
            return self._single_quoted(scalar, index, context)
        return self._double_quoted(scalar, index, context)

    def _quoted_scalar_end(self, value, index, end, context):
        value = self._strip_comment(value).strip(' \t')
        if not value.startswith(("'", '"')):
            return None
        quote = value[0]
        kind = 'single' if quote == "'" else 'double'
        start = index
        line = index
        position = 1
        while True:
            while position < len(value):
                char = value[position]
                if quote == '"' and char == '\\':
                    position += 2
                    continue
                if char != quote:
                    position += 1
                    continue
                if quote == "'" and position + 1 < len(value) \
                        and value[position + 1] == "'":
                    position += 2
                    continue
                tail = self._strip_comment(
                    value[position + 1:]).strip(' \t')
                if tail:
                    self._refuse(f'trailing text after {kind}-quoted scalar',
                                 line, context)
                return line + 1
            line += 1
            if line >= end:
                self._refuse(f'unterminated {kind}-quoted scalar', start,
                             context)
            value = self.lines[line]
            position = 0

    def _mapping_value_end(self, value, index, end, key_indent, context,
                           check_tabs=False):
        value = self._strip_comment(value).strip(' \t')
        flow_end = self._flow_value_end(value, index, end, context)
        if flow_end is not None:
            start = flow_end
        elif value.startswith(('|', '>')):
            start = self._block_scalar_range(
                value, index, end, key_indent, context)[3]
        else:
            quoted_end = self._quoted_scalar_end(value, index, end, context)
            start = index + 1 if quoted_end is None else quoted_end
        return self._value_end(start, end, key_indent, context, check_tabs)

    def _flow_value_end(self, value, index, end, context):
        value = self._strip_comment(value).strip(' \t')
        if not value.startswith(('[', '{')):
            return None
        closing = {']': '[', '}': '{'}
        stack = []
        quote = None
        line = index
        source = value
        position = 0
        while line < end:
            while position < len(source):
                char = source[position]
                if quote == '"':
                    if char == '\\':
                        position += 2
                        continue
                    if char == '"':
                        quote = None
                    position += 1
                    continue
                if quote == "'":
                    if char == "'":
                        if position + 1 < len(source) \
                                and source[position + 1] == "'":
                            position += 2
                            continue
                        quote = None
                    position += 1
                    continue
                if char in ('"', "'") and (
                        position == 0 or source[position - 1] in ' \t[{,:'):
                    quote = char
                elif char == '#' and (
                        position == 0 or source[position - 1] in ' \t'):
                    break
                elif char in '[{':
                    stack.append(char)
                elif char in ']}':
                    if not stack or stack[-1] != closing[char]:
                        self._refuse('unbalanced flow value', line, context)
                    stack.pop()
                    if not stack:
                        tail = self._strip_comment(
                            source[position + 1:]).strip(' \t')
                        if tail:
                            self._refuse(
                                'trailing text after flow value', line,
                                context)
                        return line + 1
                position += 1
            line += 1
            if line < end:
                source = self.lines[line]
                position = 0
        self._refuse('unterminated flow value', index, context)
        return None

    def _block_scalar_range(self, header, index, end, parent_indent, context):
        header = self._strip_comment(header).strip(' \t')
        match = re.fullmatch(r'([|>])([1-9]?)([+-]?)([1-9]?)', header)
        if not match or (match.group(2) and match.group(4)):
            self._refuse('unsupported block scalar header', index, context)
        style, first_indent, chomping, second_indent = match.groups()
        indent_indicator = first_indent or second_indent
        body_start = index + 1
        body_end = body_start
        content_indent = (parent_indent + int(indent_indicator)
                          if indent_indicator else None)
        while body_end < end:
            if self._scalar_blank(body_end):
                body_end += 1
                continue
            indent = self._scalar_indent(body_end)
            if indent <= parent_indent:
                break
            if content_indent is not None and indent < content_indent:
                if self.lines[body_end][indent:].startswith('#'):
                    follower = self._next_nonblank(
                        body_end + 1, end, context)
                    if follower is not None and self._indent(
                            follower, context) > parent_indent:
                        self._refuse('content after block scalar', follower,
                                     context)
                    break
                self._refuse('inconsistent block scalar indentation',
                             body_end, context)
            if content_indent is None:
                content_indent = indent
            body_end += 1
        if content_indent is None:
            content_indent = parent_indent + 1
        return style, chomping, body_start, body_end, content_indent

    def _line_scalar_end(self, index, end, context):
        indent = self._scalar_indent(index)
        body = self.lines[index][indent:]
        key_indent = indent
        if body.startswith('-') and (len(body) == 1 or body[1] in ' \t'):
            first = body[1:].lstrip(' \t')
            if not first or first.startswith('#'):
                return None
            key_indent += body.index(first)
            body = first
        position = self._mapping_colon_position(body)
        if position is None:
            return None
        value = self._strip_comment(body[position + 1:]).strip(' \t')
        quoted_end = self._quoted_scalar_end(value, index, end, context)
        if quoted_end is not None:
            return quoted_end
        if value.startswith(('|', '>')):
            return self._block_scalar_range(
                value, index, end, key_indent, context)[3]
        return self._flow_value_end(value, index, end, context)

    def _block_scalar(self, header, index, parent_indent, context):
        style, chomping, body_start, body_end, content_indent = \
            self._block_scalar_range(
                header, index, len(self.lines), parent_indent, context)
        parts = []
        for line in range(body_start, body_end):
            raw = self.lines[line]
            if self._scalar_blank(line):
                parts.append('')
                continue
            parts.append(raw[content_indent:])
        if not parts:
            return ''
        if not any(parts):
            return '\n' * len(parts) if chomping == '+' else ''
        has_terminal_break = (body_end < len(self.lines)
                              or self.source_ends_with_line_break)
        if style == '|':
            text = '\n'.join(parts)
        else:
            text = ''
            line = 0
            while line < len(parts):
                if parts[line]:
                    if line and parts[line - 1]:
                        text += ('\n' if parts[line - 1][0] in ' \t'
                                 or parts[line][0] in ' \t' else ' ')
                    text += parts[line]
                    line += 1
                    continue
                blank = line
                while line < len(parts) and not parts[line]:
                    line += 1
                break_count = line - blank
                if blank and line < len(parts) and (
                        parts[blank - 1][0] in ' \t'
                        or parts[line][0] in ' \t'):
                    break_count += 1
                text += '\n' * break_count
        if has_terminal_break:
            text += '\n'
        if chomping == '-':
            return text.rstrip('\n')
        if chomping == '+':
            return text
        return text.rstrip('\n') + ('\n' if text.endswith('\n') else '')

    def _plain_scalar(self, value, index, key_indent, value_end, context):
        text = self._decode_scalar(value, index, context)
        blank_lines = 0
        line = index + 1
        while line < value_end:
            if self._blank(line):
                self._indent(line, context)
                if self._scalar_blank(line):
                    blank_lines += 1
                else:
                    follower = self._next_nonblank(
                        line + 1, value_end, context)
                    if follower is not None and self._indent(
                            follower, context) > key_indent:
                        self._refuse(
                            'content after plain scalar comment', follower,
                            context)
                    break
                line += 1
                continue
            indent = self._indent(line, context)
            if indent <= key_indent:
                break
            continuation = self._strip_comment(self.lines[line].strip(' \t'))
            if continuation:
                text += ('\n' * blank_lines if blank_lines else ' ')
                text += continuation
                blank_lines = 0
            line += 1
        if '\t' in text:
            self._refuse('tab in plain scalar', index, context)
        return self._require_plain_string(text, index, context)

    def _require_plain_string(self, value, index, context):
        invalid_indicator = value and (
            value[0] in _PLAIN_FORBIDDEN_INITIAL
            or (value[0] in '-?:'
                and (len(value) == 1 or value[1] in ' \t')))
        if (not value or value in _SCHEMA_NON_STRING_WORDS
                or _SCHEMA_TYPED_NUMBER_OR_TIME.fullmatch(value)
                or invalid_indicator or re.search(r':(?:[ \t]|$)', value)):
            self._refuse(
                'plain scalar is not provably a string', index, context)
        return value

    def _scalar_value(self, value, index, key_indent, value_end, context):
        value = self._strip_comment(value).strip(' \t')
        if value.startswith('|') or value.startswith('>'):
            return self._block_scalar(value, index, key_indent, context)
        if value:
            self._reject_scalar_shape(value, index, context)
            if value.startswith(("'", '"')):
                quoted_end = self._quoted_scalar_end(
                    value, index, value_end, context)
                return self._quoted_scalar(
                    value, index, quoted_end, context)
            return self._plain_scalar(value, index, key_indent, value_end,
                                      context)
        nested = self._next_nonblank(index + 1, value_end, context)
        if nested is None:
            return self._require_plain_string('', index, context)
        indent = self._indent(nested, context)
        nested_value = self._strip_comment(
            self.lines[nested][indent:]).strip(' \t')
        if nested_value.startswith('|') or nested_value.startswith('>'):
            return self._block_scalar(nested_value, nested, indent, context)
        self._reject_scalar_shape(nested_value, nested, context)
        # Not subsumed by `_decode_scalar`'s guard: the mapping probe below
        # refuses a `{key: value}`-shaped nested value first, and naming the
        # flow mapping is the accurate refusal.
        self._reject_prefix(nested_value, nested, context)
        if self._looks_like_mapping(nested_value):
            self._refuse('mapping where scalar was required', nested, context)
        if nested_value.startswith(("'", '"')):
            quoted_end = self._quoted_scalar_end(
                nested_value, nested, value_end, context)
            return self._quoted_scalar(
                nested_value, nested, quoted_end, context)
        return self._plain_scalar(nested_value, nested, indent, value_end,
                                  context)
