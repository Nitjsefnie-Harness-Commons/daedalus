"""The tests-side half of the workflow YAML scalar decoder.

`scripts/ci/yamlscalar.py` carries what CI's own scripts import and states
the admitted subset. Flow collections are read only by the test-side workflow
readers, so their splitters live here rather than shipping in `scripts/ci/`.
"""
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'ci'))
from yamlscalar import (  # noqa: E402
    YAMLReadError,
    _strip_inline_comment,
    _unquoted,
    decode_inline_scalar,
    find_mapping_field,
    split_mapping_field,
)


__all__ = (
    'YAMLReadError',
    '_strip_inline_comment',
    'check_plain_scalar',
    'decode_inline_scalar',
    'find_mapping_field',
    'flow_depth',
    'split_flow_collection',
    'split_flow_items',
    'split_mapping_field',
)


# Indicators YAML forbids a plain scalar from opening with.
_PLAIN_LEADERS = ('&', '*', '!', '@', '`', ':', '- ')


def check_plain_scalar(value, owner):
    """Return a plain scalar, or refuse it as outside the admitted set.

    Both workflow readers apply this, so an anchor, an alias, a tag, a
    mapping separator or a control character is a refusal wherever a plain
    scalar is read -- never a value one reader returns raw while the other
    rejects it.
    """
    if value == '-' or value.startswith(_PLAIN_LEADERS):
        raise YAMLReadError(f'{owner} has an unsupported plain scalar')
    if ': ' in value or '\t' in value or any(
            ord(char) < 0x20 or 0xd800 <= ord(char) <= 0xdfff
            for char in value):
        raise YAMLReadError(f'{owner} has an unsupported plain scalar')
    return value


def flow_depth(text):
    """Return the flow-bracket depth one line of content leaves open.

    A flow collection spans exactly the lines its brackets hold open, so
    this is the one measurement every reader bounds a spanning value by.
    """
    depth = 0
    for _index, _char, current in _unquoted(text):
        depth = current
    return depth


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
