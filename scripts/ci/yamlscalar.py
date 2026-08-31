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


def _unquoted(text, owner='', flow=False):
    """Yield `(index, char, depth)` for every character outside a quote.

    YAML opens a quoted node only at node start -- the start of the text, or
    after `,`, `[`, `{`, or a `: ` value indicator -- so a quote reached
    anywhere else is plain content and `[a, 5 o'clock]` is two items.  In
    `flow` mode the rest of the flow grammar is enforced with it: an
    unquoted flow scalar carries none of `,[]{}`, and every bracket closes.
    """
    quote = None
    depth = 0
    node_start = True
    index = 0
    while index < len(text):
        char = text[index]
        if quote == "'":
            if text[index:index + 2] == "''":
                index += 2
                continue
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == '\\':
                index += 2
                continue
            if char == '"':
                quote = None
            index += 1
            continue
        if node_start and char in ('"', "'"):
            quote = char
        elif char in '[{':
            if flow and not node_start:
                raise YAMLReadError(
                    f'{owner} has a flow indicator in a plain scalar')
            depth += 1
        elif char in ']}':
            depth -= 1
            if flow and depth < 0:
                raise YAMLReadError(
                    f'{owner} has an unbalanced flow collection')
        yield index, char, depth
        if char in '[{,':
            node_start = True
        elif char == ':' and text[index + 1:index + 2] in ('', ' ', '\t'):
            node_start = True
        elif char not in ' \t':
            node_start = False
        index += 1
    if flow and (quote is not None or depth):
        raise YAMLReadError(f'{owner} has an unbalanced flow collection')


def split_mapping_field(text, owner, allow_tabs=False):
    """Split one inline mapping field without mistaking quoted colons."""
    if '\t' in text and not allow_tabs:
        raise YAMLReadError(f'{owner} contains an unsupported tab')
    separation = ' \t' if allow_tabs else ' '
    for index, char, _depth in _unquoted(text, owner):
        if char != ':' or text[index + 1:index + 2] not in ('', *separation):
            continue
        key = text[:index].strip(separation)
        if not key:
            raise YAMLReadError(f'{owner} has an empty mapping key')
        return key, text[index + 1:]
    raise YAMLReadError(f'{owner} has an unsupported mapping field')


def split_flow_collection(value, owner):
    """Split one flow collection into its bracket and body, or return None."""
    opening = value[:1]
    if opening not in ('[', '{'):
        return None
    closing = ']' if opening == '[' else '}'
    if len(value) < 2 or not value.endswith(closing):
        raise YAMLReadError(f'{owner} has an unbalanced flow collection')
    return opening, value[1:-1]


def split_flow_items(body, owner):
    """Split a nonempty flow collection body on its top-level commas.

    A trailing comma is the separator that terminates the last entry, so it
    yields the collection without it; an interior empty item is malformed.
    """
    items = []
    start = 0
    for index, char, depth in _unquoted(body, owner, flow=True):
        if char == ',' and depth == 0:
            items.append(body[start:index])
            start = index + 1
    items.append(body[start:])
    if len(items) > 1 and not items[-1].strip(' '):
        items.pop()
    if any(not item.strip(' ') for item in items):
        raise YAMLReadError(f'{owner} has an empty flow item')
    return items


def _strip_inline_comment(value):
    """Remove one YAML comment outside quoted scalar content."""
    for index, char, _depth in _unquoted(value):
        if char == '#' and (index == 0 or value[index - 1] == ' '):
            return value[:index].rstrip(' ')
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
