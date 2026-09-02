"""JavaScript property-key values shared by routing analysis."""
import re


_ESCAPES = {
    'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t',
    'v': '\v', '0': '\0'}
_QUOTED = (
    r'(?:"(?:\\[\s\S]|[^"\\])*"|'
    r"'(?:\\[\s\S]|[^'\\])*')")


def decode_string_literal(raw):
    """Decode one quoted JavaScript string literal, or return None."""
    raw = raw.strip()
    if len(raw) < 2 or raw[0] not in "'\"" or raw[-1] != raw[0]:
        return None
    values = []
    cursor = 1
    while cursor < len(raw) - 1:
        char = raw[cursor]
        cursor += 1
        if char != '\\':
            values.append(ord(char))
            continue
        if cursor >= len(raw) - 1:
            return None
        escaped = raw[cursor]
        cursor += 1
        if escaped in '\n\r':
            if escaped == '\r' and raw[cursor:cursor + 1] == '\n':
                cursor += 1
            continue
        if escaped == 'x':
            digits = raw[cursor:cursor + 2]
            if not re.fullmatch(r'[0-9A-Fa-f]{2}', digits):
                return None
            cursor += 2
            values.append(int(digits, 16))
            continue
        if escaped == 'u':
            if raw[cursor:cursor + 1] == '{':
                close = raw.find('}', cursor + 1, len(raw) - 1)
                digits = raw[cursor + 1:close] if close >= 0 else ''
                if not re.fullmatch(r'[0-9A-Fa-f]{1,6}', digits):
                    return None
                cursor = close + 1
            else:
                digits = raw[cursor:cursor + 4]
                if not re.fullmatch(r'[0-9A-Fa-f]{4}', digits):
                    return None
                cursor += 4
            value = int(digits, 16)
            if value > 0x10ffff:
                return None
            values.append(value)
            continue
        values.append(ord(_ESCAPES.get(escaped, escaped)))
    merged = []
    cursor = 0
    while cursor < len(values):
        value = values[cursor]
        if (0xd800 <= value <= 0xdbff and cursor + 1 < len(values)
                and 0xdc00 <= values[cursor + 1] <= 0xdfff):
            value = (0x10000 + ((value - 0xd800) << 10)
                     + values[cursor + 1] - 0xdc00)
            cursor += 1
        merged.append(chr(value))
        cursor += 1
    return ''.join(merged)


def source_key(text, left, right, computed_key=None):
    """Decoded property key in one source span, if statically known."""
    raw = text[left:right].strip()
    decoded = decode_string_literal(raw)
    if decoded is not None:
        return decoded
    if raw.startswith('[') and raw.endswith(']'):
        decoded = decode_string_literal(raw[1:-1])
        if decoded is not None:
            return decoded
        dynamic = re.fullmatch(r'\s*([\w$]+)\s*', raw[1:-1])
        if dynamic is not None and computed_key is not None:
            return computed_key(
                ('computed', dynamic.group(1), left))
        return None
    return raw if re.fullmatch(r'[\w$]+', raw) else None


def class_accessor(receiver, name, key, wanted, position):
    """Whether a named class exists and its matching public accessor body."""
    classes = list(re.finditer(
        rf'\bclass\s+{re.escape(name)}'
        rf'(?:\s+extends\s+[^{{}}]+)?\s*\{{',
        receiver.mask[:position]))
    if not classes:
        return False, None
    opening = receiver.mask.rfind(
        '{', classes[-1].start(), classes[-1].end())
    close = receiver.pair_end.get(opening)
    if close is None:
        return True, None
    pattern = re.compile(
        rf'(?<![\w$])(?:(static)\s+)?(get|set)\s+({_QUOTED})\s*\(')
    for match in pattern.finditer(receiver.text, opening + 1, close - 1):
        if receiver.mask[match.start()] != receiver.text[match.start()]:
            continue
        decoded = decode_string_literal(match.group(3))
        if (match.group(1) is not None or match.group(2) != wanted
                or (key is not None and decoded != key)):
            continue
        paren = receiver.text.rfind('(', match.start(), match.end())
        body_open = receiver.pair_end.get(paren)
        if body_open is None:
            return True, None
        while (body_open < len(receiver.mask)
               and receiver.mask[body_open].isspace()):
            body_open += 1
        body_close = receiver.pair_end.get(body_open)
        if body_close is None:
            return True, None
        return True, ('block', body_open, body_close)
    return True, None
