"""The routing field's own key readers, shared by both write directions.

A `tab` that reaches a typed command send is the same key whether a
literal, a name bound to a literal, or a concatenation of literals spells
it, and a key position no reader can name leaves its object unprovable
rather than clean.

A bracket write names its object by the expression before the brackets, so
`o.p['tab']` writes what `o` holds under `p` and `A.b.c['tab']` writes what
`A.b.c` holds. That expression is walked by kind, so the chain spelling and
the flat one reach the same name.
"""
import re

from _jsread import js_bracket_end, js_mask, js_object_entries
from _jsroute_keys import static_key
from _jsroute_source import identifier_before, previous_nonspace


# A name whose object exists but whose contents this scanner cannot prove.
# Distinct from an unknown name (a parameter, an import): unknown is
# silence, unprovable is a claim that something happened here and was not
# followed.
UNPROVABLE = object()

# A key position no reader can name. The object carrying it is unprovable
# too: a `tab` spelled through a binding the reader cannot follow is a
# `tab` the guard must answer for, not a key it may skip.
UNRESOLVED = object()

_BRACKET_WRITE = re.compile(r'(?<![\w$])([\w$]+)\s*\[')


def is_extension_literal(value):
    return value is not None and re.fullmatch(
        r'["\']extension["\']', value)


def tab_key(text, left, right, context):
    key = static_key(text, left, right, context['computed_key'])
    return UNRESOLVED if key is None else key


def computed_writes(mask, text):
    """(name match, key span, value offset) for each bracket write.

    Found in the raw text because the mask blanks string contents, making
    `p['tab']` unreadable there; a match beginning inside a blanked span is
    a mention in a string or comment, and the two diverge at the first
    character of the name exactly when it is.
    """
    found = []
    for match in _BRACKET_WRITE.finditer(text):
        if mask[match.start()] != text[match.start()]:
            continue
        key_left = match.end() - 1
        end = js_bracket_end(mask, key_left)
        equals = end
        while equals < len(mask) and mask[equals].isspace():
            equals += 1
        if (mask[equals:equals + 1] != '='
                or mask[equals + 1:equals + 2] == '='):
            continue
        found.append((match, key_left, end, equals))
    return found


def _receiver_chain(match, mask):
    """The receiver's names, base first, or None when the leftmost is a
    property. Nothing before the base means the base is a name, at the
    first offset of a file included.
    """
    parts = [(match.group(1), match.start())]
    cursor = match.start()
    while True:
        dot = previous_nonspace(mask, cursor)
        if dot < 0 or mask[dot] != '.':
            break
        name, start, _ = identifier_before(mask, dot)
        if not name:
            break
        parts.append((name, start))
        cursor = start
    parts.reverse()
    before = previous_nonspace(mask, parts[0][1])
    if before >= 0 and mask[before] in '.]':
        return None
    return [name for name, _ in parts]


def _property_owner(owner, key, named):
    """A name, an object literal's source, or None when the model does not
    follow it. A shorthand entry holds the name the key itself spells."""
    if re.fullmatch(r'[\w$]+', owner):
        state = named.get(owner)
        if not isinstance(state, dict):
            return None
        held = state.get(key)
        if held is None:
            return None
        return key if held[1] is None else held[1].strip()
    if owner.startswith('{') and owner.endswith('}'):
        for found, entry, _ in js_object_entries(js_mask(owner), owner, 0):
            if found == key:
                return key if entry is None else entry.strip()
    return None


def _write_target(match, named, mask):
    """The name the write retargets, or None when its receiver names no
    object the model follows.

    A walk that ends on an object literal, a call, or a receiver nothing
    is known about names nothing here, and there is no object to retire in
    that case — so the write is not attributed rather than attributed to
    the last name in the chain. Attributing it to that name anyway is what
    would make `q.p['tab']` a write to `p`, and `A.b.c['tab']` a write to the
    `c` a standalone `b` happens to hold.
    """
    chain = _receiver_chain(match, mask)
    if not chain:
        return None
    owner = chain[0]
    for key in chain[1:]:
        owner = _property_owner(owner, key, named)
        if owner is None:
            return None
    return owner if re.fullmatch(r'[\w$]+', owner) else None


def tab_write(named, kind, match, context):
    """`p['tab']` and `p[k]` are one write, not two spellings, and the
    object they retarget is the one the receiver provably holds. A write to
    another key changes nothing; one whose key cannot be named retires the
    object rather than trusting the keys that happened to be readable.
    """
    mask, text = context['mask'], context['text']
    if kind == 'prop':
        name, at = match.group(1), match.start()
        equals = mask.index('=', at)
        key = 'tab'
    else:
        found, key_left, key_right, equals = match
        at = found.start()
        name = _write_target(found, named, mask)
        if name is None:
            return
        key = static_key(text, key_left, key_right, context['computed_key'])
    state = named.get(name)
    if state is context['unprovable']:
        return
    if key is None:
        named[name] = context['unprovable']
        return
    if key != 'tab':
        return
    semi = mask.find(';', equals)
    semi = len(mask) if semi == -1 else semi
    if state is None:
        state = {}
    state['tab'] = (context['line_of'](at), text[equals + 1:semi].strip())
    named[name] = state
