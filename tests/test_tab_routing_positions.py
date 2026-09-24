#!/usr/bin/env python3
"""Sequence positions: an integer key is an exact position from the start,
the unknown-key slot may sit at any position, and a length may be unknown.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_dict_stores import (  # noqa: E402
    _PRE, _PRE_CLEAN, _verdict)

_T = '("_focus", "focus-tab", tab=args.chrome_tab)'
_SL = '\nsend = ext_cmd\nreturn '
_K2 = '{str(args.chrome_tab): 1, str(args.chrome_tab) + "": 1}'
_LST = 'lst = [ordinary, ordinary]\nlst[int(args.flag)] = relay()\n'
_PREFIX = 'x = [relay(), ordinary, *' + _K2 + ']\na, b, *c = x'
_STORED = ('d = {}\nd[str(args.chrome_tab)] = 1\n'
           'first, second, *names = [relay(), ordinary, *d]')
_INLINE = 'a, b, *c = [relay(), ordinary, *' + _K2 + ']'
_SUFFIX = 'a, b, c = [*' + _K2 + ', ordinary, relay()]'
_STAR_REST = 'a, *b = [*args.values[:1], ordinary, relay()]'


def _run(tmp, shape, clean=False):
    return _verdict(tmp, (_PRE_CLEAN if clean else _PRE) + shape)


def test_prefix_target_of_unknown_length_reads_its_position(tmp):
    assert _run(tmp, _PREFIX + _SL + 'b' + _T) == (0, 0)


def test_prefix_target_of_unknown_length_reports_its_relay(tmp):
    assert _run(tmp, _PREFIX + _SL + 'a()') == (1, 1)


def test_prefix_target_of_unknown_length_stays_clean(tmp):
    assert _run(tmp, _PREFIX + _SL + 'a()', clean=True) == (0, 0)


def test_prefix_before_stored_dict_star_reads_its_position(tmp):
    assert _run(tmp, _STORED + _SL + 'second' + _T) == (0, 0)


def test_prefix_before_stored_dict_star_reports_its_relay(tmp):
    assert _run(tmp, _STORED + _SL + 'first()') == (1, 1)


def test_prefix_before_stored_dict_star_stays_clean(tmp):
    assert _run(tmp, _STORED + _SL + 'first()', clean=True) == (0, 0)


def test_inline_prefix_target_reads_its_position(tmp):
    assert _run(tmp, _INLINE + _SL + 'b' + _T) == (0, 0)


def test_inline_prefix_target_reports_its_relay(tmp):
    assert _run(tmp, _INLINE + _SL + 'a()') == (1, 1)


def test_inline_prefix_target_stays_clean(tmp):
    assert _run(tmp, _INLINE + _SL + 'a()', clean=True) == (0, 0)


def test_suffix_after_unknown_star_may_be_unprovable(tmp):
    # A position counted from the end cannot be placed from the start.
    assert _run(tmp, _SUFFIX + _SL + 'b' + _T) == (0, 1)


def test_suffix_after_unknown_star_reports_its_relay(tmp):
    assert _run(tmp, _SUFFIX + _SL + 'c()') == (1, 1)


def test_suffix_after_unknown_star_stays_clean(tmp):
    assert _run(tmp, _SUFFIX + _SL + 'c()', clean=True) == (0, 0)


def test_star_target_of_unknown_length_reports_its_tail(tmp):
    assert _run(tmp, _STAR_REST + _SL + 'b[-1]()') == (1, 1)


def test_star_target_of_unknown_length_stays_clean(tmp):
    assert _run(tmp, _STAR_REST + _SL + 'b[-1]()', clean=True) == (0, 0)


def test_known_star_slot_stays_inside_the_star_in_list(tmp):
    assert _run(tmp, _LST + 'x = [*lst, ordinary]' + _SL + 'x[2]' + _T) \
        == (0, 0)


def test_known_star_slot_stays_inside_the_star_in_tuple(tmp):
    assert _run(tmp, _LST + 'x = (*lst, ordinary)' + _SL + 'x[2]' + _T) \
        == (0, 0)


def test_known_star_slot_reports_inside_the_star(tmp):
    assert _run(tmp, _LST + 'x = [*lst, ordinary]' + _SL + 'x[1]()') \
        == (1, 1)


def test_known_star_slot_reports_inside_the_star_in_tuple(tmp):
    assert _run(tmp, _LST + 'x = (*lst, ordinary)' + _SL + 'x[1]()') \
        == (1, 1)


def test_known_star_slot_stays_clean(tmp):
    assert _run(tmp, _LST + 'x = [*lst, ordinary]' + _SL + 'x[1]()',
                clean=True) == (0, 0)


def test_known_length_unpack_reads_the_unknown_slot(tmp):
    assert _run(tmp, _LST + 'a, b = lst' + _SL + 'b()') == (1, 1)


def test_known_length_star_target_reads_the_unknown_slot(tmp):
    assert _run(tmp, _LST + 'a, *b = lst' + _SL + 'b[0]()') == (1, 1)


def test_known_length_unpack_slot_stays_clean(tmp):
    assert _run(tmp, _LST + 'a, b = lst' + _SL + 'b()', clean=True) \
        == (0, 0)


def test_known_length_slot_stays_inside_its_star_when_unpacked(tmp):
    assert _run(tmp, _LST + 'a, b, c = [*lst, ordinary]' + _SL + 'c' + _T) \
        == (0, 0)


_LITERAL_STARS = {
    'empty_tuple_in_tuple': ('x = (*(), {})', 0),
    'empty_tuple_in_list': ('x = [*(), {}]', 0),
    'string_in_tuple': ('x = (*"ab", {})', 2),
    'constant_set_in_tuple': ('x = (*{{1, 2}}, {})', 2),
    'constant_set_in_list': ('x = [*{{1, 2}}, {}]', 2),
    'constant_tuple_in_tuple': ('x = (*(1, 2), {})', 2),
    'duplicate_constant_set': ('x = (*{{1, 1.0, True}}, {})', 1),
    'bytes_in_list': ('x = [*b"ab", {}]', 2),
    'plain_tuple_in_list': ('x = [*(ordinary, ordinary), {}]', 2),
}


def _literal(tmp, name, element, call, clean=False):
    shape, index = _LITERAL_STARS[name]
    return _run(tmp, shape.format(element) + _SL + f'x[{index}]' + call,
                clean)


def test_star_empty_tuple_in_tuple_counts_exactly(tmp):
    assert _literal(tmp, 'empty_tuple_in_tuple', 'ordinary', _T) == (0, 0)


def test_star_empty_tuple_in_tuple_relay_reports(tmp):
    assert _literal(tmp, 'empty_tuple_in_tuple', 'relay()', '()') == (1, 1)


def test_star_empty_tuple_in_tuple_relay_stays_clean(tmp):
    assert _literal(tmp, 'empty_tuple_in_tuple', 'relay()', '()',
                    clean=True) == (0, 0)


def test_star_empty_tuple_in_list_counts_exactly(tmp):
    assert _literal(tmp, 'empty_tuple_in_list', 'ordinary', _T) == (0, 0)


def test_star_empty_tuple_in_list_relay_reports(tmp):
    assert _literal(tmp, 'empty_tuple_in_list', 'relay()', '()') == (1, 1)


def test_star_empty_tuple_in_list_relay_stays_clean(tmp):
    assert _literal(tmp, 'empty_tuple_in_list', 'relay()', '()',
                    clean=True) == (0, 0)


def test_star_string_in_tuple_counts_exactly(tmp):
    assert _literal(tmp, 'string_in_tuple', 'ordinary', _T) == (0, 0)


def test_star_string_in_tuple_relay_reports(tmp):
    assert _literal(tmp, 'string_in_tuple', 'relay()', '()') == (1, 1)


def test_star_string_in_tuple_relay_stays_clean(tmp):
    assert _literal(tmp, 'string_in_tuple', 'relay()', '()',
                    clean=True) == (0, 0)


def test_star_constant_set_in_tuple_counts_exactly(tmp):
    assert _literal(tmp, 'constant_set_in_tuple', 'ordinary', _T) == (0, 0)


def test_star_constant_set_in_tuple_relay_reports(tmp):
    assert _literal(tmp, 'constant_set_in_tuple', 'relay()', '()') == (1, 1)


def test_star_constant_set_in_tuple_relay_stays_clean(tmp):
    assert _literal(tmp, 'constant_set_in_tuple', 'relay()', '()',
                    clean=True) == (0, 0)


def test_star_constant_set_in_list_counts_exactly(tmp):
    assert _literal(tmp, 'constant_set_in_list', 'ordinary', _T) == (0, 0)


def test_star_constant_set_in_list_relay_reports(tmp):
    assert _literal(tmp, 'constant_set_in_list', 'relay()', '()') == (1, 1)


def test_star_constant_set_in_list_relay_stays_clean(tmp):
    assert _literal(tmp, 'constant_set_in_list', 'relay()', '()',
                    clean=True) == (0, 0)


def test_star_constant_tuple_in_tuple_counts_exactly(tmp):
    assert _literal(tmp, 'constant_tuple_in_tuple', 'ordinary', _T) == (0, 0)


def test_star_constant_tuple_in_tuple_relay_reports(tmp):
    assert _literal(tmp, 'constant_tuple_in_tuple', 'relay()', '()') == (1, 1)


def test_star_constant_tuple_in_tuple_relay_stays_clean(tmp):
    assert _literal(tmp, 'constant_tuple_in_tuple', 'relay()', '()',
                    clean=True) == (0, 0)


def test_star_duplicate_constant_set_counts_exactly(tmp):
    assert _literal(tmp, 'duplicate_constant_set', 'ordinary', _T) == (0, 0)


def test_star_duplicate_constant_set_relay_reports(tmp):
    assert _literal(tmp, 'duplicate_constant_set', 'relay()', '()') == (1, 1)


def test_star_duplicate_constant_set_relay_stays_clean(tmp):
    assert _literal(tmp, 'duplicate_constant_set', 'relay()', '()',
                    clean=True) == (0, 0)


def test_star_bytes_in_list_counts_exactly(tmp):
    assert _literal(tmp, 'bytes_in_list', 'ordinary', _T) == (0, 0)


def test_star_bytes_in_list_relay_reports(tmp):
    assert _literal(tmp, 'bytes_in_list', 'relay()', '()') == (1, 1)


def test_star_bytes_in_list_relay_stays_clean(tmp):
    assert _literal(tmp, 'bytes_in_list', 'relay()', '()',
                    clean=True) == (0, 0)


def test_star_plain_tuple_in_list_counts_exactly(tmp):
    assert _literal(tmp, 'plain_tuple_in_list', 'ordinary', _T) == (0, 0)


def test_star_plain_tuple_in_list_relay_reports(tmp):
    assert _literal(tmp, 'plain_tuple_in_list', 'relay()', '()') == (1, 1)


def test_star_plain_tuple_in_list_relay_stays_clean(tmp):
    assert _literal(tmp, 'plain_tuple_in_list', 'relay()', '()',
                    clean=True) == (0, 0)


def test_star_of_a_set_holding_a_name_stays_opaque(tmp):
    assert _run(tmp, 'x = [*{args.flag, 2}, relay()]' + _SL + 'x[2]()') \
        == (1, 1)


_PAIR_SLOT = 'p = ["k", ordinary]\np[int(args.flag)] = relay()\n'
_SURVIVORS = {
    'X3a_pair_dyn_slot': _PAIR_SLOT + 'd = dict([p])' + _SL + 'd["k"]()',
    'X3a_pair_dyn_slot_update': (_PAIR_SLOT + 'd = {}\nd.update([p])'
                                 + _SL + 'd["k"]()'),
    'X5b_starstar_single': ('d = {"a": 1, "b": 2}\nx = [*{**d}, relay()]'
                            + _SL + 'x[2]()'),
    'X7c_rest_restar': ('a, *b = [*' + _K2 + ', ordinary]\n'
                        'x = [*b, relay()]' + _SL + 'x[1]()'),
}


def test_survivor_X3a_pair_dyn_slot_reports(tmp):
    assert _run(tmp, _SURVIVORS['X3a_pair_dyn_slot']) == (1, 1)


def test_survivor_X3a_pair_dyn_slot_stays_clean(tmp):
    assert _run(tmp, _SURVIVORS['X3a_pair_dyn_slot'],
                clean=True) == (0, 0)


def test_survivor_X3a_pair_dyn_slot_update_reports(tmp):
    assert _run(tmp, _SURVIVORS['X3a_pair_dyn_slot_update']) == (1, 1)


def test_survivor_X3a_pair_dyn_slot_update_stays_clean(tmp):
    assert _run(tmp, _SURVIVORS['X3a_pair_dyn_slot_update'],
                clean=True) == (0, 0)


def test_survivor_X5b_starstar_single_reports(tmp):
    assert _run(tmp, _SURVIVORS['X5b_starstar_single']) == (1, 1)


def test_survivor_X5b_starstar_single_stays_clean(tmp):
    assert _run(tmp, _SURVIVORS['X5b_starstar_single'],
                clean=True) == (0, 0)


def test_survivor_X7c_rest_restar_reports(tmp):
    assert _run(tmp, _SURVIVORS['X7c_rest_restar']) == (1, 1)


def test_survivor_X7c_rest_restar_stays_clean(tmp):
    assert _run(tmp, _SURVIVORS['X7c_rest_restar'],
                clean=True) == (0, 0)


def test_callable_key_in_a_pair_of_unknown_length_does_not_crash(tmp):
    # A callable used as a dict key is not modelled: issue 1012.
    assert _run(tmp, 'd = dict([(relay(), *' + _K2 + ')])\nx = [*d]'
                + _SL + 'x[0]()') == (1, 0)


_CALLABLE_KEYS = {
    'tuple_pair': 'd = dict([(relay(), 1)])\nx = [*d]',
    'list_pair': 'd = dict([[relay(), ordinary]])\nx = [*d]',
    'update_pair': 'd = {}\nd.update([(relay(), 1)])\nx = [*d]',
}


def _callable_key(tmp, name, clean=False):
    return _run(tmp, _CALLABLE_KEYS[name] + _SL + 'x[0]()', clean)


def test_callable_key_in_a_tuple_pair_does_not_crash(tmp):
    # A callable used as a dict key is not modelled: issue 1012.
    assert _callable_key(tmp, 'tuple_pair') == (1, 0)


def test_callable_key_in_a_tuple_pair_stays_clean(tmp):
    assert _callable_key(tmp, 'tuple_pair', clean=True) == (0, 0)


def test_callable_key_in_a_list_pair_does_not_crash(tmp):
    # A callable used as a dict key is not modelled: issue 1012.
    assert _callable_key(tmp, 'list_pair') == (1, 0)


def test_callable_key_in_a_list_pair_stays_clean(tmp):
    assert _callable_key(tmp, 'list_pair', clean=True) == (0, 0)


def test_callable_key_in_a_update_pair_does_not_crash(tmp):
    # A callable used as a dict key is not modelled: issue 1012.
    assert _callable_key(tmp, 'update_pair') == (1, 0)


def test_callable_key_in_a_update_pair_stays_clean(tmp):
    assert _callable_key(tmp, 'update_pair', clean=True) == (0, 0)


_QUIET = 'def quiet(): return lambda *a, **k: ordinary()\nq = quiet()\n'
_SETS = {
    'star_x0': 'x = [*{relay(), ordinary}, ordinary]' + _SL + 'x[0]()',
    'star_x1': 'x = [*{relay(), ordinary}, ordinary]' + _SL + 'x[1]()',
    'unpack_b': 's = {relay(), ordinary}\na, b = s' + _SL + 'b()',
    'var_star_x1': 's = {relay(), ordinary}\nx = [*s]' + _SL + 'x[1]()',
    'clean_first_star_x0': (_QUIET + 's = {q, relay()}\nx = [*s]'
                            + _SL + 'x[0]()'),
    'clean_first_display_x0': (_QUIET + 'x = [*{q, relay()}, ordinary]'
                               + _SL + 'x[0]()'),
}


def _set_guard(tmp, name, clean=False):
    """The guard's count, three times in one process: the runtime half
    follows the set's hash order, so it is 0 or 1 by run."""
    verdicts = [_run(tmp, _SETS[name], clean) for _ in range(3)]
    assert all(runtime in (0, 1) for runtime, _ in verdicts), verdicts
    assert len({guard for _, guard in verdicts}) == 1, verdicts
    return verdicts[0][1]


def test_set_display_star_x0_reports_any_position(tmp):
    assert _set_guard(tmp, 'star_x0') == 1


def test_set_display_star_x0_stays_clean(tmp):
    assert _set_guard(tmp, 'star_x0', clean=True) == 0


def test_set_display_star_x1_reports_any_position(tmp):
    assert _set_guard(tmp, 'star_x1') == 1


def test_set_display_star_x1_stays_clean(tmp):
    assert _set_guard(tmp, 'star_x1', clean=True) == 0


def test_set_display_unpack_b_reports_any_position(tmp):
    assert _set_guard(tmp, 'unpack_b') == 1


def test_set_display_unpack_b_stays_clean(tmp):
    assert _set_guard(tmp, 'unpack_b', clean=True) == 0


def test_set_display_var_star_x1_reports_any_position(tmp):
    assert _set_guard(tmp, 'var_star_x1') == 1


def test_set_display_var_star_x1_stays_clean(tmp):
    assert _set_guard(tmp, 'var_star_x1', clean=True) == 0


def test_set_display_clean_first_star_x0_reports_any_position(tmp):
    assert _set_guard(tmp, 'clean_first_star_x0') == 1


def test_set_display_clean_first_star_x0_stays_clean(tmp):
    assert _set_guard(tmp, 'clean_first_star_x0', clean=True) == 0


def test_set_display_clean_first_display_x0_reports_any_position(tmp):
    assert _set_guard(tmp, 'clean_first_display_x0') == 1


def test_set_display_clean_first_display_x0_stays_clean(tmp):
    assert _set_guard(tmp, 'clean_first_display_x0', clean=True) == 0


_SET_LATER = 'x = [*{relay(), ordinary}, ordinary]' + _SL + 'x[2]' + _T
_SET_DEDUP = ('def quiet(): return lambda: ordinary()\nq = quiet()\n'
              'x = [*{q, q}, relay()]' + _SL + 'x[1]()')


def test_set_display_position_after_it_may_be_unprovable(tmp):
    # Equal elements collapse at runtime, so the count is not known.
    assert _run(tmp, _SET_LATER) == (0, 1)


def test_set_display_that_collapses_reports_what_follows(tmp):
    assert _run(tmp, _SET_DEDUP) == (1, 1)


def test_set_display_that_collapses_stays_clean(tmp):
    assert _run(tmp, _SET_DEDUP, clean=True) == (0, 0)


_PINS = {
    'set_of_equal_values': ('x = [*{int(args.flag), 1}, relay()]'
                            + _SL + 'x[1]()'),
    'suffix_after_star_target': ('d = dict(zip([], []))\n'
                                 'a, *b, c = [ordinary, relay(), *d]'
                                 + _SL + 'c()'),
    'literal_after_opaque_star': ('x = [*args.values, *"ab", relay()]'
                                  + _SL + 'x[4]()'),
}


def test_set_of_equal_values_reports(tmp):
    assert _run(tmp, _PINS['set_of_equal_values']) == (1, 1)


def test_set_of_equal_values_stays_clean(tmp):
    assert _run(tmp, _PINS['set_of_equal_values'], clean=True) == (0, 0)


def test_suffix_after_star_target_reports(tmp):
    assert _run(tmp, _PINS['suffix_after_star_target']) == (1, 1)


def test_suffix_after_star_target_stays_clean(tmp):
    assert _run(tmp, _PINS['suffix_after_star_target'], clean=True) == (0, 0)


def test_literal_after_opaque_star_reports(tmp):
    assert _run(tmp, _PINS['literal_after_opaque_star']) == (1, 1)


def test_literal_after_opaque_star_stays_clean(tmp):
    assert _run(tmp, _PINS['literal_after_opaque_star'], clean=True) == (0, 0)


def test_star_target_after_exact_prefix_stays_clean(tmp):
    assert _run(tmp, 'a, *b = [relay(), ordinary, *' + _K2 + ']'
                + _SL + 'b[0]' + _T) == (0, 0)


_STARRED_SETS = {
    'display': 'x = [*{*(1, 1)}, relay()]' + _SL + 'x[1]()',
    'name': 's = {*(1, 1)}\nx = [*s, relay()]' + _SL + 'x[1]()',
}


def test_set_of_starred_literals_display_has_no_count(tmp):
    assert _run(tmp, _STARRED_SETS['display']) == (1, 1)


def test_set_of_starred_literals_display_stays_clean(tmp):
    assert _run(tmp, _STARRED_SETS['display'], clean=True) == (0, 0)


def test_set_of_starred_literals_name_has_no_count(tmp):
    assert _run(tmp, _STARRED_SETS['name']) == (1, 1)


def test_set_of_starred_literals_name_stays_clean(tmp):
    assert _run(tmp, _STARRED_SETS['name'], clean=True) == (0, 0)


_SET_SOURCES = {
    'display': 'd = {}\nd.update({("k", relay())})' + _SL + 'd["k"]()',
    'name': ('s = {("k", relay())}\nd = {}\nd.update(s)'
             + _SL + 'd["k"]()'),
    'two_pairs': (_QUIET + 'd = {}\nd.update({("j", q), ("k", relay())})'
                  + _SL + 'd["k"]()'),
}


def test_update_from_a_set_of_pairs_display_reports(tmp):
    assert _run(tmp, _SET_SOURCES['display']) == (1, 1)


def test_update_from_a_set_of_pairs_display_stays_clean(tmp):
    assert _run(tmp, _SET_SOURCES['display'], clean=True) == (0, 0)


def test_update_from_a_set_of_pairs_name_reports(tmp):
    assert _run(tmp, _SET_SOURCES['name']) == (1, 1)


def test_update_from_a_set_of_pairs_name_stays_clean(tmp):
    assert _run(tmp, _SET_SOURCES['name'], clean=True) == (0, 0)


def test_update_from_a_set_of_pairs_two_pairs_reports(tmp):
    assert _run(tmp, _SET_SOURCES['two_pairs']) == (1, 1)


def test_update_from_a_set_of_pairs_two_pairs_stays_clean(tmp):
    assert _run(tmp, _SET_SOURCES['two_pairs'], clean=True) == (0, 0)


def test_pair_of_unknown_length_joins_its_key_position(tmp):
    # The key position may hold a callable; the conservative join keeps it.
    assert _run(tmp, _QUIET + 'e = dict(zip([], []))\nd = {}\n'
                'd.update([(relay(), q, *e)])' + _SL + 'd.get("k", q)'
                + _T) == (0, 1)


_UNKNOWN_PAIRS = {
    'first': 'e = dict([("k", relay(), *d), ("j", q)])',
    'second': 'e = dict([("j", q), ("k", relay(), *d)])',
}


def test_dict_of_an_unknown_length_pair_first_reports(tmp):
    assert _run(tmp, 'd = dict(zip([], []))\n' + _QUIET
                + _UNKNOWN_PAIRS['first'] + _SL + 'e["k"]()') == (1, 1)


def test_dict_of_an_unknown_length_pair_first_stays_clean(tmp):
    assert _run(tmp, 'd = dict(zip([], []))\n' + _QUIET
                + _UNKNOWN_PAIRS['first'] + _SL + 'e["k"]()',
                clean=True) == (0, 0)


def test_dict_of_an_unknown_length_pair_second_reports(tmp):
    assert _run(tmp, 'd = dict(zip([], []))\n' + _QUIET
                + _UNKNOWN_PAIRS['second'] + _SL + 'e["k"]()') == (1, 1)


def test_dict_of_an_unknown_length_pair_second_stays_clean(tmp):
    assert _run(tmp, 'd = dict(zip([], []))\n' + _QUIET
                + _UNKNOWN_PAIRS['second'] + _SL + 'e["k"]()',
                clean=True) == (0, 0)


_STARRED_VALUE = ('d = dict(zip([], []))\n' + _QUIET
                  + 'e = dict([("k", *d, relay()), ("j", q)])'
                  + _SL + 'e["k"]()')


def test_unknown_length_pair_joins_values_after_its_star(tmp):
    assert _run(tmp, _STARRED_VALUE) == (1, 1)


def test_unknown_length_pair_joins_values_after_its_star_stays_clean(tmp):
    assert _run(tmp, _STARRED_VALUE, clean=True) == (0, 0)


def test_unknown_length_pair_does_not_trust_its_first_item_as_key(tmp):
    assert _run(tmp, 'd = dict(zip([], []))\n' + _QUIET
                + 'e = dict([(q, *d, relay())])' + _SL
                + 'e.get("k", q)()') == (0, 1)


_LAST_PREFIX = ('x = [ordinary, relay(), *' + _K2 + ']\na, b, *c = x'
                + _SL + 'b()')
_UNREAD_PAIR_KEY = '[("a", 1), (str(args.chrome_tab), 2)]'
_UNREAD_PAIRS = {
    'dict': 'd = dict(' + _UNREAD_PAIR_KEY + ')',
    'update': 'd = {}\nd.update(' + _UNREAD_PAIR_KEY + ')',
}


def test_last_target_before_the_star_reads_its_own_position(tmp):
    assert _run(tmp, _LAST_PREFIX) == (1, 1)


def test_last_target_before_the_star_stays_clean(tmp):
    assert _run(tmp, _LAST_PREFIX, clean=True) == (0, 0)


def _unread_pair(tmp, name, clean=False):
    return _run(tmp, _UNREAD_PAIRS[name] + '\nx = [*d, relay()]' + _SL
                + 'x[2]()', clean)


def test_pair_with_an_unread_key_leaves_the_count_unknown(tmp):
    assert [_unread_pair(tmp, name) for name in _UNREAD_PAIRS] \
        == [(1, 1)] * len(_UNREAD_PAIRS)


def test_pair_with_an_unread_key_stays_clean(tmp):
    assert [_unread_pair(tmp, name, True) for name in _UNREAD_PAIRS] \
        == [(0, 0)] * len(_UNREAD_PAIRS)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='positions_')


if __name__ == '__main__':
    raise SystemExit(main())
