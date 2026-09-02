"""What running a construction or a call can hand back: the questions
`_jsroute_promote`'s promotion by mention cannot answer.
"""
import re

from _jsroute_keys import _class_declarations

_RETURNED = re.compile(r'(?<![\w$])return(?![\w$])')
_CLASS_EXPRESSION = re.compile(
    r'class\b[^{]*?(?:extends\s+([\w$]+)\s*)?\{')
_UNKEYED_WRITE = re.compile(
    r'(?<![\w$])([\w$]+)\s*\[\s*(?![\'"])[^\]\n]*\]'
    r'\s*=(?!=|>)')
_CONSTRUCTION_HEAD = re.compile(
    r'(?<![\w$])(?:constructor\s*\(|static\s*\{)')
_FIELD_INITIALISER = re.compile(
    r'(?<![\w$.])[\w$#]+\s*=(?!=|>)')


def _hands_back(reader, text):
    """True when the text yields a sender, not a call of one."""
    blanked = re.sub(
        reader.pattern.pattern + r'(\s*\()', r' \1', text)
    return bool(reader.pattern.search(blanked))


def returns_promoting(reader, body):
    mask = reader.mask
    if body[0] != 'block':
        return _hands_back(reader, mask[body[1]:body[2]])
    for found in _RETURNED.finditer(mask[body[1]:body[2]]):
        start = body[1] + found.end()
        end = reader.receivers.expression_end(mask, start)
        if _hands_back(reader, mask[start:end]):
            return True
    return False


def constructor_promotes(reader, name, seen=frozenset()):
    key = ('ctor', name)
    if key in reader.memo:
        return reader.memo[key]
    reader.memo[key] = False
    found = False
    for extends, opening in _class_spans(reader, name):
        close = reader.receivers.pair_end.get(opening)
        if close is None:
            found = True
            break
        if _construction_runs(reader, opening, close):
            found = True
            break
        if (extends and extends not in seen
                and re.fullmatch(r'[\w$]+', extends)
                and constructor_promotes(reader, extends,
                                         seen | {name})):
            found = True
            break
    reader.memo[key] = found
    return found


def _class_spans(reader, name):
    """(extends, body opening) per class the name can construct.

    A class expression is bound rather than declared, so the binding's
    own value is read when no declaration carries the name.
    """
    found = list(_class_declarations(reader.receivers, name))
    if found:
        return found
    binding = reader.receivers.visible_binding(
        name, max(0, len(reader.mask) - 1))
    for _when, span in reader.receivers.values.get(binding, ()):
        if span is None:
            continue
        text = reader.mask[span[0]:span[1]]
        head = _CLASS_EXPRESSION.match(text.strip())
        if head is None:
            continue
        opening = reader.mask.index('{', span[0], span[1])
        found.append((head.group(1), opening))
    return found


def _construction_runs(reader, opening, close):
    """Only members at the class body's own level run on `new`."""
    mask = reader.mask
    for match in _CONSTRUCTION_HEAD.finditer(mask, opening, close):
        if reader.enclosing[match.start()] != opening:
            continue
        start = mask.find('{', match.end() - 1)
        body = reader.receivers.pair_end.get(start)
        if start < 0 or body is None:
            return True
        if reader.holds_sender(start, body):
            return True
    for match in _FIELD_INITIALISER.finditer(mask, opening, close):
        if reader.enclosing[match.start()] != opening:
            continue
        end = reader.receivers.expression_end(mask, match.end())
        if reader.holds_sender(match.end(), end):
            return True
    return False


def unkeyed_writes(reader):
    """Receivers written under a key the member index cannot record."""
    mask = reader.mask
    found = {}
    for match in _UNKEYED_WRITE.finditer(mask):
        end = reader.receivers.expression_end(mask, match.end())
        if reader.holds_sender(match.end(), end):
            found.setdefault(match.group(1), match.start())
    return found
