"""Decode the bounded inline YAML scalar subset used by workflow policy."""
import re


class YAMLReadError(ValueError):
    """The requested YAML value is malformed or outside the reader's scope."""


_PLAIN_INLINE = re.compile(r'^[A-Za-z0-9_.][-A-Za-z0-9_.@/+$()]*$')
_DOUBLE_ESCAPES = {
    '0': '\0', 'a': '\a', 'b': '\b', 't': '\t', 'n': '\n',
    'v': '\v', 'f': '\f', 'r': '\r', 'e': '\x1b', ' ': ' ',
    '"': '"', '/': '/', '\\': '\\', 'N': '\x85', '_': '\xa0',
    'L': '\u2028', 'P': '\u2029',
}


def split_mapping_field(text, owner, allow_tabs=False):
    """Split one inline mapping field without mistaking quoted colons."""
    if '\t' in text and not allow_tabs:
        raise YAMLReadError(f'{owner} contains an unsupported tab')
    separation = ' \t' if allow_tabs else ' '
    quote = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote == "'":
            if char == "'" and text[index:index + 2] == "''":
                index += 2
                continue
            if char == "'":
                quote = None
        elif quote == '"':
            if char == '\\':
                index += 2
                continue
            if char == '"':
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == ':' and (
                index + 1 == len(text)
                or text[index + 1] in separation):
            key = text[:index].strip(separation)
            if not key:
                raise YAMLReadError(f'{owner} has an empty mapping key')
            return key, text[index + 1:]
        index += 1
    raise YAMLReadError(f'{owner} has an unsupported mapping field')


def _strip_inline_comment(value):
    """Remove one YAML comment outside quoted scalar content."""
    quote = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote == "'":
            if char == "'" and value[index:index + 2] == "''":
                index += 2
                continue
            if char == "'":
                quote = None
        elif quote == '"':
            if char == '\\':
                index += 2
                continue
            if char == '"':
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == '#' and (
                index == 0 or value[index - 1] == ' '):
            return value[:index].rstrip(' ')
        index += 1
    return value.rstrip(' ')


def _decode_single_quoted(value, owner):
    if len(value) < 2 or not value.endswith("'"):
        raise YAMLReadError(f'{owner} has an incomplete quoted scalar')
    body = value[1:-1]
    decoded = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "'":
            if body[index:index + 2] != "''":
                raise YAMLReadError(
                    f'{owner} has an unsupported quoted scalar')
            decoded.append("'")
            index += 2
            continue
        if ord(char) < 0x20 or 0xd800 <= ord(char) <= 0xdfff:
            raise YAMLReadError(f'{owner} contains an unsupported character')
        decoded.append(char)
        index += 1
    return ''.join(decoded)


def _decode_double_quoted(value, owner):
    if len(value) < 2 or not value.endswith('"'):
        raise YAMLReadError(f'{owner} has an incomplete quoted scalar')
    body = value[1:-1]
    decoded = []
    index = 0
    widths = {'x': 2, 'u': 4, 'U': 8}
    while index < len(body):
        char = body[index]
        if char == '"':
            raise YAMLReadError(f'{owner} has an unsupported quoted scalar')
        if char != '\\':
            if ord(char) < 0x20 or 0xd800 <= ord(char) <= 0xdfff:
                raise YAMLReadError(
                    f'{owner} contains an unsupported character')
            decoded.append(char)
            index += 1
            continue
        if index + 1 == len(body):
            raise YAMLReadError(f'{owner} has an incomplete YAML escape')
        escaped = body[index + 1]
        if escaped in _DOUBLE_ESCAPES:
            decoded.append(_DOUBLE_ESCAPES[escaped])
            index += 2
            continue
        width = widths.get(escaped)
        digits = body[index + 2:index + 2 + (width or 0)]
        if (width is None or len(digits) != width
                or not all(char in '0123456789abcdefABCDEF'
                           for char in digits)):
            raise YAMLReadError(f'{owner} has an unsupported YAML escape')
        codepoint = int(digits, 16)
        if codepoint > 0x10ffff or 0xd800 <= codepoint <= 0xdfff:
            raise YAMLReadError(f'{owner} has an invalid Unicode escape')
        decoded.append(chr(codepoint))
        index += 2 + width
    return ''.join(decoded)


def decode_inline_scalar(value, owner):
    """Decode the bounded one-line scalar subset used by policy maps."""
    value = _strip_inline_comment(value.strip(' '))
    if not value:
        raise YAMLReadError(f'{owner} has no scalar value')
    if value.startswith("'"):
        return _decode_single_quoted(value, owner)
    if value.startswith('"'):
        return _decode_double_quoted(value, owner)
    if not _PLAIN_INLINE.fullmatch(value):
        raise YAMLReadError(f'{owner} has an unsupported plain scalar')
    return value
