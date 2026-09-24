"""The routing field's own key readers, shared by both write directions.

A `tab` that reaches a typed command send is the same key whether a
literal, a name bound to a literal, or a concatenation of literals spells
it, and a key position no reader can name leaves its object unprovable
rather than clean. The two directions differ only in where the key sits:
a property in an object literal, or a bracket on a tracked name.

A bracket write names its object by the expression before the brackets, so
`o.p['tab']` writes what `o` holds under `p` and `A.b.c['tab']` writes what
`A.b.c` holds. `_write_target` walks that expression by kind — from the base
name through each property the model can follow — so the chain spelling and
the flat one reach the same name. Where the walk ends on something that is not
a name: an object literal, a call, a receiver nothing is known about, or a
base that is itself a property — the expression names no object the model
follows, and the write is outside it rather than attributed to the last name
in the chain. Attributing it to that name anyway is what makes `q.p['tab']`
look like a write to `p`, and what would make `A.b.c['tab']` look like a write
to the `c` a standalone `b` happens to hold.
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
    """The key one property position carries, or the unresolved marker."""
    key = static_key(text, left, right, context['computed_key'])
    return UNRESOLVED if key is None else key


def computed_writes(mask, text):
    """Bracket writes as (name match, key span, value offset).

    Found in the raw text because the mask blanks string contents, making
    `p['tab']` unreadable there; a match that begins inside a blanked span
    is a mention in a string or comment, not code. The mask preserves
    positions, so the two diverge at the very first character of the name
    exactly when the mention is not real code.
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
    """The names a bracket write's receiver names, base name first, or
    None when the leftmost one is a property rather than a name.

    `A.b.c` is three names; the leftmost is the base and the rest are
    properties of what precedes them. Nothing before the base means the
    base is a name, at the first offset of a file included.
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
    """What an object holds under `key`: a name, the source of an object
    literal, or None when the model does not follow it. A shorthand entry
    holds the name the key itself spells."""
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
    """The name a bracket write retargets, or None when its receiver names
    no object the model follows.

    The receiver is walked by kind, from the base name through every
    property the model can follow, so the chain spelling and the flat one
    reach the same name — and a chain that lands on an object literal, a
    call, or a receiver nothing is known about names nothing here. There
    is no object to retire in that case, so the write is not attributed
    rather than attributed to the last name in the chain.
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
    """Apply one property write to the named-object state table.

    The dotted spelling names `tab` outright; the bracket spelling is read
    with the same reader an object-literal key uses, so `p['tab']` and
    `p[k]` are one write rather than two spellings, and the object they
    retarget is the one the receiver provably holds. A write to another
    key changes nothing, and one whose key cannot be named retires the
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
