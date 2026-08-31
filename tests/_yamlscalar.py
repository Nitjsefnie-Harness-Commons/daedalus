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
    split_mapping_field,
)


__all__ = (
    'YAMLReadError',
    '_strip_inline_comment',
    'decode_inline_scalar',
    'split_flow_collection',
    'split_flow_items',
    'split_mapping_field',
)


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
