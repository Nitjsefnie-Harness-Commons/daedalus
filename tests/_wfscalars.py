"""Decode the YAML scalar subset used by the workflow checkout reader."""

import re


_DOUBLE_ESCAPES = {
    '0': '\0', 'a': '\a', 'b': '\b', 't': '\t',
    'n': '\n', 'v': '\v', 'f': '\f', 'r': '\r',
    'e': '\x1b', ' ': ' ', '"': '"', '/': '/',
    '\\': '\\', 'N': '\u0085', '_': '\u00a0',
    'L': '\u2028', 'P': '\u2029',
}


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

    def _mapping_colon(self, body, index, context):
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
        self._refuse('mapping entry without a colon', index, context)
        return None

    def _mapping_parts(self, index, context, body=None):
        if body is None:
            body = self.lines[index].lstrip(' ')
        body = self._strip_comment(body).strip(' \t')
        if body.startswith('?') and (len(body) == 1 or body[1] in ' \t'):
            self._refuse('explicit key', index, context)
        position = self._mapping_colon(body, index, context)
        key = body[:position].strip(' \t')
        if not key:
            self._refuse('empty mapping key', index, context)
        value = body[position + 1:].strip(' \t')
        return self._decode_scalar(key, index, f'{context} key'), value

    def _decode_scalar(self, text, index, context):
        value = self._strip_comment(text).strip(' \t')
        if not value:
            return ''
        self._reject_prefix(value, index, context)
        if value.startswith("'"):
            return self._single_quoted(value, index, context)
        if value.startswith('"'):
            return self._double_quoted(value, index, context)
        if re.search(r'(^|\s)&\S+', value):
            self._refuse('anchor', index, context)
        if re.search(r'(^|\s)\*\S+', value):
            self._refuse('alias', index, context)
        return value

    def _single_quoted(self, value, index, context):
        position = 1
        result = []
        while position < len(value):
            char = value[position]
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
            if char != '\\':
                result.append(char)
                position += 1
                continue
            if position + 1 >= len(value):
                self._refuse('unterminated double-quoted escape', index,
                             context)
            escaped = value[position + 1]
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
            if escaped in '\n\r':
                position += 2
                continue
            self._refuse('unknown double-quote escape', index, context)
        self._refuse('unterminated double-quoted scalar', index, context)
        return None

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
        if value.startswith(('|', '>')):
            start = self._block_scalar_range(
                value, index, end, key_indent, context)[3]
        else:
            quoted_end = self._quoted_scalar_end(value, index, end, context)
            start = index + 1 if quoted_end is None else quoted_end
        return self._value_end(start, end, key_indent, context, check_tabs)

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
                self._refuse('inconsistent block scalar indentation', index,
                             context)
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
        if not self._looks_like_mapping(body):
            return None
        position = self._mapping_colon(body, index, context)
        value = self._strip_comment(body[position + 1:]).strip(' \t')
        quoted_end = self._quoted_scalar_end(value, index, end, context)
        if quoted_end is not None:
            return quoted_end
        if value.startswith(('|', '>')):
            return self._block_scalar_range(
                value, index, end, key_indent, context)[3]
        return None

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
        if style == '|':
            text = '\n'.join(parts) + '\n'
        else:
            text = ''
            line = 0
            while line < len(parts):
                if parts[line]:
                    if line and parts[line - 1]:
                        text += ('\n' if parts[line - 1].startswith(' ')
                                 or parts[line].startswith(' ') else ' ')
                    text += parts[line]
                    line += 1
                    continue
                blank = line
                while line < len(parts) and not parts[line]:
                    line += 1
                text += '\n' * (line - blank)
            text += '\n'
        if chomping == '-':
            return text.rstrip('\n')
        if chomping == '+':
            return text
        return text.rstrip('\n') + '\n'

    def _plain_scalar(self, value, index, key_indent, value_end, context):
        text = self._decode_scalar(value, index, context)
        blank_lines = 0
        line = index + 1
        while line < value_end:
            if self._blank(line):
                self._indent(line, context)
                if self._scalar_blank(line):
                    blank_lines += 1
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
        return text

    def _scalar_value(self, value, index, key_indent, value_end, context):
        value = self._strip_comment(value).strip(' \t')
        if value.startswith('|') or value.startswith('>'):
            return self._block_scalar(value, index, key_indent, context)
        if value:
            self._reject_scalar_shape(value, index, context)
            self._reject_prefix(value, index, context)
            if value.startswith("'"):
                return self._single_quoted(value, index, context)
            if value.startswith('"'):
                return self._double_quoted(value, index, context)
            return self._plain_scalar(value, index, key_indent, value_end,
                                      context)
        nested = self._next_nonblank(index + 1, value_end, context)
        if nested is None:
            return ''
        indent = self._indent(nested, context)
        nested_value = self._strip_comment(
            self.lines[nested][indent:]).strip(' \t')
        if nested_value.startswith('|') or nested_value.startswith('>'):
            return self._block_scalar(nested_value, nested, indent, context)
        self._reject_scalar_shape(nested_value, nested, context)
        self._reject_prefix(nested_value, nested, context)
        if self._looks_like_mapping(nested_value):
            self._refuse('mapping where scalar was required', nested, context)
        return self._plain_scalar(nested_value, nested, indent, value_end,
                                  context)
