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


def head_left(mask, text, cursor):
    """Start of the blanked or whitespace run ending at `cursor`."""
    start = cursor
    while start > 0 and (mask[start - 1] != text[start - 1]
                         or text[start - 1].isspace()):
        start -= 1
    return start


def _word_at(mask, start, right):
    end = start
    while end < right and (mask[end].isalnum() or mask[end] in '_$'):
        end += 1
    return mask[start:end], end


def _skip_space(mask, position, right=None):
    if right is None:
        right = len(mask)
    while position < right and mask[position].isspace():
        position += 1
    return position


def _name_at(mask, text, cursor, right, pair_end, computed_key):
    """Key and past-name offset of one name form at `cursor`, or None."""
    text_char = text[cursor:cursor + 1]
    if text_char in ('"', "'"):
        quoted = re.match(_QUOTED, text[cursor:right])
        if quoted is None:
            return None
        return decode_string_literal(quoted.group(0)), cursor + quoted.end()
    mask_char = mask[cursor:cursor + 1]
    if mask_char == '\\':
        escaped = re.match(
            r'\\u\{([0-9A-Fa-f]{1,6})\}|\\u([0-9A-Fa-f]{4})',
            text[cursor:right])
        if escaped is None:
            return None
        value = int(escaped.group(1) or escaped.group(2), 16)
        if value > 0x10ffff:
            return None
        word, word_end = _word_at(mask, cursor + escaped.end(), right)
        return chr(value) + word, word_end
    if mask_char == '[':
        name_end = pair_end.get(cursor)
        if name_end is None:
            return None
        return source_key(text, cursor, name_end, computed_key), name_end
    if re.fullmatch(r'[\w$]', mask_char):
        word, end = _word_at(mask, cursor, right)
        return word, end
    if mask_char == '#':
        word, end = _word_at(mask, cursor + 1, right)
        if not word:
            return None
        # A tuple key cannot collide with a bracket lookup.
        return ('private', word), end
    return None


def head_name(mask, text, cursor, right, pair_end, computed_key):
    """Key and past-name offset of a member name at `cursor`, or None.

    The key is None when a computed key is not statically known.
    """
    found = _name_at(mask, text, cursor, right, pair_end, computed_key)
    if found is not None:
        return found
    start = head_left(mask, text, cursor)
    if start == cursor:
        return None
    while start < right and text[start].isspace():
        start += 1
    return _name_at(mask, text, start, right, pair_end, computed_key)


def entry_head(mask, text, left, right, pair_end, computed_key,
               static=False):
    """(key, past-name offset, form, is_static) of one member head.

    `key` is None when a computed key is not statically known.
    """
    cursor = left
    form = 'method'
    is_static = False
    while cursor < right:
        quoted = re.match(r'\s*(["\'])', text[cursor:right])
        if quoted is not None:
            cursor += quoted.end(1)
            break
        cursor = _skip_space(mask, cursor)
        if cursor >= right:
            break
        char = mask[cursor:cursor + 1]
        if char == '*':
            form = 'generator'
            cursor += 1
            continue
        word, word_end = _word_at(mask, cursor, right)
        if not word:
            break
        heads = ('get', 'set', 'async', 'static') if static else (
            'get', 'set', 'async')
        following = mask[_skip_space(mask, word_end):_skip_space(
            mask, word_end) + 1]
        if word in heads and (re.match(r'\s*["\']', text[word_end:right])
                              or following not in '(=;}'):
            if word == 'static':
                is_static = True
            if word in ('get', 'set'):
                form = word
            cursor = word_end
            continue
        break
    found = head_name(mask, text, cursor, right, pair_end, computed_key)
    if found is None:
        return None
    return found[0], found[1], form, is_static


def class_accessor(receiver, name, key, wanted, static=False):
    """(status, body, form) for `key` on the named class, walked in full.

    'absent' proves none of the read members matches; 'opaque' means a
    member was left unread; 'none' means no such class declaration.
    """
    matches = _class_declarations(receiver, name)
    if not matches:
        return 'none', None, None
    if len(matches) > 1:
        # Same-named classes cannot share a scope: undecidable.
        return 'opaque', None, None
    extends, opening = matches[0]
    close = receiver.pair_end.get(opening)
    if close is None:
        return 'opaque', None, None
    seen = {name}
    chain = []
    while True:
        chain.append((extends, opening, close))
        if not extends:
            break
        if not re.fullmatch(r'[\w$]+', extends) or extends in seen:
            return 'opaque', None, None
        seen.add(extends)
        base_matches = _class_declarations(receiver, extends)
        if len(base_matches) != 1:
            return 'opaque', None, None
        extends, opening = base_matches[0]
        close = receiver.pair_end.get(opening)
        if close is None:
            return 'opaque', None, None
    members_per_class = [
        _class_members(receiver, cls_opening, cls_close)
        for _, cls_opening, cls_close in chain]
    if any(members is None for members in members_per_class):
        return 'opaque', None, None
    # An instance bare field shadows from any chain class; a static one
    # only from the class itself, since each constructor holds its own.
    if any(member_form == 'data' and member_key == key
           for index, members in enumerate(members_per_class)
           for member_static, member_form, member_key, _ in members
           if member_static == static
           and (index == 0 or not static)):
        return 'known', None, 'data'
    if key is None:
        # Absence needs no member of the wanted form to possibly match.
        if any(member_form == wanted and not isinstance(member_key, tuple)
               for members in members_per_class
               for _, member_form, member_key, _ in members):
            return 'opaque', None, None
        return 'absent', None, None
    for members in members_per_class:
        for member_static, form, member_key, body in reversed(members):
            if member_static != static or member_key != key:
                continue
            if wanted is not None and wanted != form:
                if form in ('get', 'set'):
                    continue
                break
            return 'known', body, form
    return 'absent', None, None


def _class_declarations(receiver, name):
    """(extends, opening brace) per declaration of the named class."""
    declarations = receiver.__dict__.get('_class_declarations')
    if declarations is None:
        declarations = []
        for match in re.finditer(
                r'\bclass\s+([\w$]+)(?:\s+extends\s+([^\{\}]+))?\s*\{',
                receiver.mask):
            declarations.append(
                (match.group(1),
                 match.group(2).strip() if match.group(2) else None,
                 match.end() - 1))
        receiver.__dict__['_class_declarations'] = declarations
    return [decl[1:] for decl in declarations if decl[0] == name]


def _class_members(receiver, opening, close):
    """Members of one class body, or None when one cannot be read.

    Member tuples are (is_static, form, key, body); a bare field is
    recorded as (side, 'data', key, None).
    """
    mask = receiver.mask
    text = receiver.text
    cursor = opening + 1
    members = []
    while cursor < close - 1:
        cursor = _skip_space(mask, cursor, close - 1)
        if cursor >= close - 1:
            break
        found = entry_head(mask, text, cursor, close - 1,
                           receiver.pair_end, receiver.computed_key,
                           static=True)
        if found is None:
            return None
        entry_key, cursor, form, is_static = found
        if entry_key is None:
            return None
        if entry_key == 'constructor':
            # Runs at construction and may define instance members.
            return None
        following = mask[_skip_space(mask, cursor, close):_skip_space(
            mask, cursor, close) + 1]
        if following == '{' and is_static:
            return None  # runs code at class definition
        if following == '(':
            params_close = receiver.pair_end.get(_skip_space(mask, cursor))
            if params_close is None:
                return None
            body_open = _skip_space(mask, params_close)
            if (mask[body_open:body_open + 1] != '{'
                    or receiver.pair_end.get(body_open) is None):
                return None
            members.append((is_static, form, entry_key,
                            ('block', body_open,
                             receiver.pair_end[body_open])))
            cursor = receiver.pair_end[body_open]
            continue
        if following == '=':
            return None  # initializer runs at construction
        if following == ';':
            members.append((is_static, 'data', entry_key, None))
            cursor = _skip_space(mask, cursor) + 1
            continue
        # A bare field ends at the next member's head (ASI).
        members.append((is_static, 'data', entry_key, None))
        cursor = _skip_space(mask, cursor)
    return members
