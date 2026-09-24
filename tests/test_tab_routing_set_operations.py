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
            'def quiet(): return lambda: ordinary()\n'
            'def make(): return {relay()}\n')
_FOR = '[f() for f in s]'
_NEXT = 'next(iter(s))()'


def _verdict(tmp, stores, invoke=_FOR):
    return _tracked_focus_verdict(
        tmp, _PRELUDE + ''.join(f'{store}\n' for store in stores.splitlines())
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


def test_union_keeps_the_element_only_the_right_holds(tmp):
    assert _verdict(tmp, 's = {quiet()} | {relay()}') == (1, 1)


def test_symmetric_difference_keeps_the_element_only_the_right_holds(tmp):
    assert _verdict(tmp, 's = {quiet()} ^ {relay()}') == (1, 1)


def test_difference_drops_what_only_the_right_operand_holds(tmp):
    # `A - B` is a subset of `A`, so the fold keeps the left operand alone
    # and the quiet element leaves nothing to reach. The second row is the
    # oracle, and the one
    # test_difference_keeps_the_element_only_the_left_holds carries alone:
    # the same shape flags once the routing element is on the left, so a
    # fold that kept the right operand too would read (0, 1) here.
    assert _verdict(tmp, 's = {quiet()} - {relay()}') == (0, 0)
    assert _verdict(tmp, 's = {relay()} - {quiet()}') == (1, 1)


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


def test_an_empty_intersection_still_reads_the_left_operand(tmp):
    # `A & B` is a subset of `A | B`, which is the tightest guarantee the
    # model can make: it cannot see contents, so the fold keeps both sides
    # and an empty intersection stays conservatively reachable.
    assert _verdict(tmp, 'f = relay()\ns = {f} & set()') == (0, 1)


def test_a_rebound_set_reads_back_through_a_loop_chain(tmp):
    stores = 's = {quiet()}\ns |= set()\ns |= {relay()}'
    assert _verdict(tmp, stores) == (1, 1)
    assert _verdict(tmp, stores, _NEXT) == (1, 1)


def test_an_unresolvable_operand_fails_closed(tmp):
    assert _verdict(tmp, 's = {quiet()} | make()') == (1, 1)


def test_the_unresolvable_operand_oracle_is_live(tmp):
    # The same shape with a resolvable empty set proves the (1, 1) above is
    # the uncertainty token reaching the consumer, not the quiet element.
    assert _verdict(tmp, 's = {quiet()} | set()') == (0, 0)


def test_a_dict_union_keeps_its_mapping_value(tmp):
    stores = 's = {"a": relay()} | {"b": quiet()}'
    assert _verdict(tmp, stores, 's["a"]()') == (1, 1)


def test_a_dict_augmented_union_keeps_its_mapping_value(tmp):
    stores = 'd = {"a": quiet()}\nd |= {"b": relay()}'
    assert _verdict(tmp, stores, 'd["b"]()') == (1, 1)


def test_an_int_union_holds_no_deferred_callable(tmp):
    # Neither operand is a set or a dict, so the fold yields nothing and an
    # int stays an int; iterating it would raise at runtime.
    assert _verdict(tmp, 's = 1 | 2', 'ordinary()') == (0, 0)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='tab_routing_set_operations_')


if __name__ == '__main__':
    raise SystemExit(main())
