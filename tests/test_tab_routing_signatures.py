#!/usr/bin/env python3
"""`stored_signature` pins the occupancy-aware dedupe signatures.

Equality follows contents plus the container's program-point identity, so
two objects built at one program point sign equal and two program points
never merge, whatever their contents. Occupancy trims top-level None items
only: nested containers, instance attributes and alternatives keep every
item, so one join level cannot hide a None the other path lacks.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_values import (  # noqa: E402
    DeferredAlternatives, DeferredClass, DeferredContainer,
    DeferredGenerator, DeferredInstance, deferred_signature,
    is_clean_container, stored_signature, value_signature)


def _pair(left_items, right_items, length=2, kind='list'):
    identity = object()
    return (DeferredContainer(left_items, length, kind, identity),
            DeferredContainer(right_items, length, kind, identity))


def test_container_contents_decide_the_signature(tmp):
    left, right = _pair({0: 'x', 1: 'y'}, {0: 'x', 1: 'y'})
    assert stored_signature(left) == stored_signature(right)


def test_container_identity_separates_program_points(tmp):
    left, right = _pair({0: 'x', 1: 'y'}, {0: 'x', 1: 'y'})
    other = DeferredContainer({0: 'x', 1: 'y'}, 2, 'list', object())
    assert stored_signature(left) == stored_signature(right)
    assert stored_signature(left) != stored_signature(other)


def test_occupancy_trims_top_level_none_items_only(tmp):
    occupied, plain = _pair({0: None, 1: 'x'}, {1: 'x'})
    assert stored_signature(occupied, True) \
        != stored_signature(plain, True)
    assert stored_signature(occupied, False) \
        == stored_signature(plain, False)


def test_instance_signs_attributes_at_one_level(tmp):
    identity = object()
    left = DeferredInstance({'a': 'x'}, identity)
    right = DeferredInstance({'a': 'x'}, identity)
    other = DeferredInstance({'a': 'x'}, object())
    assert stored_signature(left) == stored_signature(right)
    assert stored_signature(left) != stored_signature(other)
    assert stored_signature(left, False) == stored_signature(left, True)


def test_alternatives_ignore_the_occupancy_flag(tmp):
    inner, _ = _pair({0: None, 1: 'x'}, {0: None, 1: 'x'})
    alternatives = DeferredAlternatives((inner,))
    assert stored_signature(alternatives, True) \
        == stored_signature(alternatives, False)


def test_plain_and_deferred_values_round_trip(tmp):
    marker = DeferredClass({'m': None})
    assert stored_signature('extension') == (
        'plain', 'extension', None, None, None)
    assert stored_signature(None) == ('plain', None, None, None, None)
    assert stored_signature(marker) == (
        'deferred', id(marker), None, None, None)


def test_is_clean_container_classifies_by_items(tmp):
    clean, dirty = _pair({0: None, 1: None}, {0: 'x', 1: None})
    assert is_clean_container('extension') is False
    assert is_clean_container(clean) is True
    assert is_clean_container(dirty) is False


def test_value_signature_arms(tmp):
    expression = ast.parse('(x for a in b)').body[0].value
    generator = DeferredGenerator(expression, 3, True)
    marker = DeferredClass({'m': None})
    assert value_signature(generator) == (
        'generator', expression.lineno, expression.col_offset, 3, True)
    assert value_signature(marker) == deferred_signature(marker)
    assert value_signature('extension') == 'extension'


def test_nested_none_items_survive_top_level_trimming(tmp):
    nested_identity = object()
    occupied = DeferredContainer({0: None, 1: 'x'}, 2, 'list',
                                 nested_identity)
    rebuilt = DeferredContainer({1: 'x'}, 2, 'list', nested_identity)
    left, right = _pair({0: occupied}, {0: rebuilt}, length=1)
    assert stored_signature(left, False) != stored_signature(right, False)
    assert stored_signature(left, True) != stored_signature(right, True)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='tabrouting_signatures_')


if __name__ == '__main__':
    raise SystemExit(main())
