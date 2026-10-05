"""JavaScript string-literal decoding for the console-argument policy."""
import re


_ESCAPES = {
    'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t',
    'v': '\v'}


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
        if char == raw[0]:
            # The quote closes here: `'h' + 'ook'` is a concatenation.
            return None
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
        if escaped in '01234567':
            # The runtime's own digit counts: `\0` to `\3` take two further
            # digits and `\4` to `\7` one, so `\123` is `S` and `\477` is
            # `'7`. A greedy read of either is a different character.
            digits = escaped
            room = 2 if escaped in '0123' else 1
            while (len(digits) <= room
                   and raw[cursor:cursor + 1] in '01234567'):
                digits += raw[cursor]
                cursor += 1
            values.append(int(digits, 8))
            continue
        if escaped in '89':
            # Neither an octal digit nor a single escape, so a key spelling
            # one is unresolvable rather than read as the digit.
            return None
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
