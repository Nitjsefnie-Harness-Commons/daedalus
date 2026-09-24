#!/usr/bin/env python3
"""Set operands folded by `|`, `&`, `^` and `-`, bare and augmented."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402

_CALL = 'send("_focus", "focus-tab", tab=args.chrome_tab)'
_PRELUDE = ('send = ordinary\n'
            'def maker():\n'
            f'    return lambda: {_CALL}\n'
            'def relay(): return maker()\n'
            'def quiet(): return lambda: ordinary()\n')
# A set the model cannot read through a class attribute, and a callable it
# cannot read at all.
_OPAQUE = ('import functools\n'
           'class K:\n'
           '    s = {relay()}\n'
           'p = functools.partial(relay())\n')
_FOR = '[f() for f in s]'
_NEXT = 'next(iter(s))()'
# `next(iter(...))` reads whichever element a set yields first, so every
# `_NEXT` body leaves exactly one element at runtime.


def _verdict(tmp, stores, invoke=_FOR, prelude=_PRELUDE):
    return _tracked_focus_verdict(
        tmp, prelude + ''.join(f'{store}\n' for store in stores.splitlines())
        + 'send = ext_cmd\n' + f'return {invoke}', counts=True)


def test_a_plain_set_keeps_its_own_element(tmp):
    assert _verdict(tmp, 's = {relay()}') == (1, 1)
    assert _verdict(tmp, 's = {relay()}', _NEXT) == (1, 1)


def test_union_reads_both_elements_of_a_set_pair(tmp):
    assert _verdict(tmp, 's = {relay()} | {quiet()}') == (1, 1)
    assert _verdict(tmp, 's = {relay()} | set()', _NEXT) == (1, 1)


def test_intersection_reads_the_element_the_pair_shares(tmp):
    assert _verdict(tmp, 'f = relay()\ns = {f} & {f}') == (1, 1)
    assert _verdict(tmp, 'f = relay()\ns = {f} & {f}', _NEXT) == (1, 1)


def test_symmetric_difference_reads_both_elements_of_a_pair(tmp):
    assert _verdict(tmp, 's = {relay()} ^ {quiet()}') == (1, 1)
    assert _verdict(tmp, 's = {relay()} ^ set()', _NEXT) == (1, 1)


def test_difference_reads_the_element_the_left_holds(tmp):
    assert _verdict(tmp, 's = {relay()} - {quiet()}') == (1, 1)
    assert _verdict(tmp, 's = {relay()} - set()', _NEXT) == (1, 1)


def test_an_empty_right_operand_leaves_the_left_readable(tmp):
    for operator in ('|', '^', '-'):
        assert _verdict(tmp, f's = {{relay()}} {operator} set()') == (1, 1), \
            operator
        assert _verdict(tmp, f's = {{relay()}} {operator} set()', _NEXT) \
            == (1, 1), operator


# `set()` and `frozenset()` name a set the model holds nothing for, so the
# follow-on statement is what tells the result's type: iterating a mapping
# reads its keys, not the elements.
_EMPTY_FACTORY = [
    ('set-union', 's = set() | K.s\ns |= {relay()}'),
    ('set-symdiff', 's = set() | K.s\ns ^= {relay()}'),
    ('frozenset-union', 's = frozenset() | K.s\ns |= {relay()}'),
    ('right-set-union', 's = K.s | set()\ns |= {relay()}'),
]


def test_an_empty_set_factory_types_its_result_as_a_set(tmp):
    for label, stores in _EMPTY_FACTORY:
        assert _verdict(tmp, stores, _FOR, _PRELUDE + _OPAQUE) == (2, 1), \
            label


def test_an_empty_set_factory_adds_no_false_positive(tmp):
    for label, stores in [('union', 's = {quiet()} | set()'),
                          ('symdiff', 's = {quiet()} ^ frozenset()')]:
        assert _verdict(tmp, stores, _FOR, _PRELUDE + _OPAQUE) == (0, 0), \
            label


def test_a_mapping_operand_keeps_the_dict_merge(tmp):
    assert _verdict(tmp, 's = {"a": relay()} | {"b": quiet()}',
                    's["a"]()') == (1, 1)


def test_union_keeps_the_element_only_the_right_holds(tmp):
    assert _verdict(tmp, 's = {quiet()} | {relay()}') == (1, 1)


def test_symmetric_difference_keeps_the_element_only_the_right_holds(tmp):
    assert _verdict(tmp, 's = {quiet()} ^ {relay()}') == (1, 1)


def test_difference_drops_what_only_the_right_operand_holds(tmp):
    # `A - B` is a subset of `A`, so the fold keeps the left operand alone.
    # A fold keeping the right operand too would read (0, 1) here; the
    # second row is the oracle, and test_difference_reads_the_element_only_
    # the_left_holds carries the same pair on its own.
    assert _verdict(tmp, 's = {quiet()} - {relay()}') == (0, 0)
    assert _verdict(tmp, 's = {relay()} - {quiet()}') == (1, 1)


def test_intersection_keeps_the_element_only_the_right_holds(tmp):
    # `A & B` is a subset of `A | B`, so the fold keeps both sides. The
    # intersection is `{q}` at runtime, so a left-only fold reads (0, 0).
    assert _verdict(tmp, 'q = quiet()\nf = relay()\ns = {q} & {q, f}',
                    '[g() for g in s]') == (0, 1)
    assert _verdict(tmp, 'f = relay()\ns = {f} & {f}') == (1, 1)


def test_augmented_union_reads_the_rebound_target(tmp):
    assert _verdict(tmp, 's = {relay()}\ns |= {quiet()}') == (1, 1)
    assert _verdict(tmp, 's = {relay()}\ns |= set()', _NEXT) == (1, 1)


def test_augmented_intersection_reads_the_rebound_target(tmp):
    stores = 'f = relay()\ns = {f}\ns &= {f}'
    assert _verdict(tmp, stores) == (1, 1)
    assert _verdict(tmp, stores, _NEXT) == (1, 1)


def test_augmented_symmetric_difference_reads_the_rebound_target(tmp):
    assert _verdict(tmp, 's = {relay()}\ns ^= {quiet()}') == (1, 1)
    assert _verdict(tmp, 's = {relay()}\ns ^= set()', _NEXT) == (1, 1)


def test_augmented_difference_reads_the_rebound_target(tmp):
    assert _verdict(tmp, 's = {relay()}\ns -= set()') == (1, 1)
    assert _verdict(tmp, 's = {relay()}\ns -= {quiet()}', _NEXT) == (1, 1)


def test_a_rebound_set_reads_back_through_a_loop_chain(tmp):
    stores = 's = set()\ns |= set()\ns |= {relay()}'
    assert _verdict(tmp, stores) == (1, 1)
    assert _verdict(tmp, stores, _NEXT) == (1, 1)


# The rebind replaces the pre-rebind container, so every name bound to it
# reads the new elements -- which a row reading the rebound name cannot see.
_ALIASED = [
    ('name', 's = {quiet()}\nt = s\ns |= {relay()}', '[f() for f in t]'),
    ('subscript', 'box = [{quiet()}]\ns = box[0]\ns |= {relay()}',
     '[f() for f in box[0]]'),
    ('dict', 'd = {"a": quiet()}\nt = d\nd |= {"z": relay()}', 't["z"]()'),
]


def test_a_rebound_set_reaches_every_name_bound_to_it(tmp):
    for label, stores, invoke in _ALIASED:
        assert _verdict(tmp, stores, invoke) == (1, 1), label


# An operand the model cannot read at all. The fold marks it with the
# uncertainty token and no consumer reports it: the display row holds the
# same unreadable callable with no operator on the path. Issue 1064.
_UNREADABLE = [
    ('fold', 's = {quiet()} | K.s', _FOR),
    ('display', 'x = {p}', '[f() for f in x]'),
]


def test_an_unreadable_operand_is_marked_but_still_unread(tmp):
    for label, stores, invoke in _UNREADABLE:
        assert _verdict(tmp, stores, invoke, _PRELUDE + _OPAQUE) == (1, 0), \
            label


def test_an_unreadable_operand_does_not_hide_the_readable_one(tmp):
    assert _verdict(tmp, 's = {relay()} | K.s', _FOR, _PRELUDE + _OPAQUE) \
        == (2, 1)
    assert _verdict(tmp, 's = {relay()} - K.s', _FOR, _PRELUDE + _OPAQUE) \
        == (1, 1)


def test_an_int_union_holds_no_deferred_callable(tmp):
    # Neither operand is a set or a dict, and `s` is an int at runtime.
    assert _verdict(tmp, 's = 1 | 2', 'ordinary()') == (0, 0)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='tab_routing_set_operations_')


if __name__ == '__main__':
    raise SystemExit(main())
