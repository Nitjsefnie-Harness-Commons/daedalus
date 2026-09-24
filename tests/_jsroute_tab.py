"""The routing field's own key readers, shared by both write directions.

A `tab` that reaches a typed command send is the same key whether a
literal, a name bound to a literal, or a concatenation of literals spells
it, and a key position no reader can name leaves its object unprovable
rather than clean. The two directions differ only in where the key sits:
a property in an object literal, or a bracket on a tracked name.
"""
import re

from _jsread import js_bracket_end
from _jsroute_keys import static_key


# A name whose object exists but whose contents this scanner cannot prove.
# Distinct from an unknown name (a parameter, an import): unknown is
# silence, unprovable is a claim that something happened here and was not
# followed.
UNPROVABLE = object()

# A key position no reader can name. The object carrying it is unprovable
# too: a `tab` spelled through a binding the reader cannot follow is a
# `tab` the guard must answer for, not a key it may skip.
UNRESOLVED = object()

_BRACKET_WRITE = re.compile(r'(?<![\w$.])([\w$]+)\s*\[')


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


def tab_write(named, kind, match, context):
    """Apply one property write to the named-object state table.

    The dotted spelling names `tab` outright; the bracket spelling is read
    with the same reader an object-literal key uses, so `p['tab']` and
    `p[k]` are one write rather than two spellings. A write to another
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
        name, at = found.group(1), found.start()
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
