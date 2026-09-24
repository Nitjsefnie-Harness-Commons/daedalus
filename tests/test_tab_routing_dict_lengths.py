#!/usr/bin/env python3
"""Readers of a container's length fail closed on an unknown count."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_dict_stores import (  # noqa: E402
    _PRE, _PRE_CLEAN, _body, _verdict)


_LENGTH_READERS = {
    'known_star_unknown_slot': (
        'lst = [ordinary, ordinary]\nlst[int(args.flag)] = relay()\n'
        'x = [*lst, ordinary]', 'x[1]()'),
    'known_star_unknown_slot_tuple': (
        'lst = [ordinary, ordinary]\nlst[int(args.flag)] = relay()\n'
        'x = (*lst, ordinary)', 'x[1]()'),
    'pair_of_unknown_length_update': (
        'd = {}\nd.update([(*args.values[:1], relay())])', 'd[1]()'),
    'pair_of_unknown_length_dict': (
        'd = dict([(*args.values[:1], relay())])', 'd[1]()'),
}


def _reader(tmp, name, prefix=_PRE):
    store, invoke = _LENGTH_READERS[name]
    return _verdict(tmp, _body(store, invoke, prefix=prefix))


def test_length_reader_known_star_unknown_slot_reports(tmp):
    assert _reader(tmp, 'known_star_unknown_slot') == (1, 1)


def test_length_reader_known_star_unknown_slot_stays_clean(tmp):
    assert _reader(tmp, 'known_star_unknown_slot', _PRE_CLEAN) == (0, 0)


def test_length_reader_known_star_unknown_slot_tuple_reports(tmp):
    assert _reader(tmp, 'known_star_unknown_slot_tuple') == (1, 1)


def test_length_reader_known_star_unknown_slot_tuple_stays_clean(tmp):
    assert _reader(tmp, 'known_star_unknown_slot_tuple', _PRE_CLEAN) == (0, 0)


def test_length_reader_pair_of_unknown_length_update_reports(tmp):
    assert _reader(tmp, 'pair_of_unknown_length_update') == (1, 1)


def test_length_reader_pair_of_unknown_length_update_stays_clean(tmp):
    assert _reader(tmp, 'pair_of_unknown_length_update', _PRE_CLEAN) == (0, 0)


def test_length_reader_pair_of_unknown_length_dict_reports(tmp):
    assert _reader(tmp, 'pair_of_unknown_length_dict') == (1, 1)


def test_length_reader_pair_of_unknown_length_dict_stays_clean(tmp):
    assert _reader(tmp, 'pair_of_unknown_length_dict', _PRE_CLEAN) == (0, 0)


_UNREADABLE_STORES = {
    'R1_update_zip': ('d = {}\nd.update(zip(["a", "b"], [1, 2]))\n'
                      'x = [*d, relay()]', 'x[2]()'),
    'R2_update_vars': ('d = {}\nd.update(vars(args))\nx = [*d, relay()]',
                       'x[3]()'),
    'R3_ior_vars': ('d = {}\nd |= vars(args)\nx = [*d, relay()]', 'x[3]()'),
    'R4_update_call_kw': ('d = {}\nd.update(k=len("ab"))\n'
                          'x = [*d, relay()]', 'x[1]()'),
    'R5_setdefault_call': ('d = {}\nd.setdefault("k", len("ab"))\n'
                           'x = [*d, relay()]', 'x[1]()'),
}


def _unreadable(tmp, name, prefix=_PRE):
    store, invoke = _UNREADABLE_STORES[name]
    return _verdict(tmp, _body(store, invoke, prefix=prefix))


def test_unreadable_store_R1_update_zip_reports(tmp):
    assert _unreadable(tmp, 'R1_update_zip') == (1, 1)


def test_unreadable_store_R1_update_zip_stays_clean(tmp):
    assert _unreadable(tmp, 'R1_update_zip', _PRE_CLEAN) == (0, 0)


def test_unreadable_store_R2_update_vars_reports(tmp):
    assert _unreadable(tmp, 'R2_update_vars') == (1, 1)


def test_unreadable_store_R2_update_vars_stays_clean(tmp):
    assert _unreadable(tmp, 'R2_update_vars', _PRE_CLEAN) == (0, 0)


def test_unreadable_store_R3_ior_vars_reports(tmp):
    assert _unreadable(tmp, 'R3_ior_vars') == (1, 1)


def test_unreadable_store_R3_ior_vars_stays_clean(tmp):
    assert _unreadable(tmp, 'R3_ior_vars', _PRE_CLEAN) == (0, 0)


def test_unreadable_store_R4_update_call_kw_reports(tmp):
    assert _unreadable(tmp, 'R4_update_call_kw') == (1, 1)


def test_unreadable_store_R4_update_call_kw_stays_clean(tmp):
    assert _unreadable(tmp, 'R4_update_call_kw', _PRE_CLEAN) == (0, 0)


def test_unreadable_store_R5_setdefault_call_reports(tmp):
    assert _unreadable(tmp, 'R5_setdefault_call') == (1, 1)


def test_unreadable_store_R5_setdefault_call_stays_clean(tmp):
    assert _unreadable(tmp, 'R5_setdefault_call', _PRE_CLEAN) == (0, 0)


_T = '("_focus", "focus-tab", tab=args.chrome_tab)'
_UNCOUNTED = 'd = {"a": 1}\ndel d[str(args.flag) and "a"]\n'
_SEND_LAST = '\nsend = ext_cmd\nreturn '
_SURVIVOR_SHAPES = {
    'M01_opaque_star_sender_after': (
        'send = ext_cmd\nargs.box = [send]\nx = [relay(), *args.box]\n'
        'return x[1]' + _T),
    'M09_or_uncounted': (
        _UNCOUNTED + 'x = [*(d | {}), relay()]' + _SEND_LAST + 'x[0]()'),
    'M10_call_uncounted': (
        _UNCOUNTED + 'x = [*dict(d), relay()]' + _SEND_LAST + 'x[0]()'),
    'M10_call_kw_uncounted': (
        _UNCOUNTED + 'x = [*dict(**d), relay()]' + _SEND_LAST + 'x[0]()'),
    'M11_update_unread_pair': (
        'd = {}\nd.update([("a", 1), tuple(["b", 2])])\nx = [*d, relay()]'
        + _SEND_LAST + 'x[2]()'),
    'M11_dict_unread_pair': (
        'x = [*dict([("a", 1), tuple(["b", 2])]), relay()]'
        + _SEND_LAST + 'x[2]()'),
    'M12_store_after_uncounted': (
        _UNCOUNTED + 'd["b"] = 1\nx = [*d, relay()]' + _SEND_LAST
        + 'x[1]()'),
    'M12_pop_after_uncounted': (
        'd = {"a": 1, "b": 2}\ndel d[str(args.flag) and "a"]\nd.pop("b")\n'
        'x = [*d, relay()]' + _SEND_LAST + 'x[0]()'),
    'M22_fromkeys': (
        'd = dict.fromkeys(["a", "b"], relay())\nx = [*d, relay()]'
        + _SEND_LAST + 'x[2]()'),
}


def _survivor(tmp, name, clean=False):
    shape = _SURVIVOR_SHAPES[name]
    if clean:
        shape = _PRE_CLEAN + shape.replace('args.box = [send]',
                                           'args.box = [ordinary]')
    else:
        shape = _PRE + shape
    return _verdict(tmp, shape)


def test_survivor_M01_opaque_star_sender_after_reports(tmp):
    assert _survivor(tmp, 'M01_opaque_star_sender_after') == (1, 1)


def test_survivor_M01_opaque_star_sender_after_is_unprovable(tmp):
    assert _survivor(tmp, 'M01_opaque_star_sender_after', clean=True) == (0, 1)


def test_survivor_M09_or_uncounted_reports(tmp):
    assert _survivor(tmp, 'M09_or_uncounted') == (1, 1)


def test_survivor_M09_or_uncounted_stays_clean(tmp):
    assert _survivor(tmp, 'M09_or_uncounted', clean=True) == (0, 0)


def test_survivor_M10_call_uncounted_reports(tmp):
    assert _survivor(tmp, 'M10_call_uncounted') == (1, 1)


def test_survivor_M10_call_uncounted_stays_clean(tmp):
    assert _survivor(tmp, 'M10_call_uncounted', clean=True) == (0, 0)


def test_survivor_M10_call_kw_uncounted_reports(tmp):
    assert _survivor(tmp, 'M10_call_kw_uncounted') == (1, 1)


def test_survivor_M10_call_kw_uncounted_stays_clean(tmp):
    assert _survivor(tmp, 'M10_call_kw_uncounted', clean=True) == (0, 0)


def test_survivor_M11_update_unread_pair_reports(tmp):
    assert _survivor(tmp, 'M11_update_unread_pair') == (1, 1)


def test_survivor_M11_update_unread_pair_stays_clean(tmp):
    assert _survivor(tmp, 'M11_update_unread_pair', clean=True) == (0, 0)


def test_survivor_M11_dict_unread_pair_reports(tmp):
    assert _survivor(tmp, 'M11_dict_unread_pair') == (1, 1)


def test_survivor_M11_dict_unread_pair_stays_clean(tmp):
    assert _survivor(tmp, 'M11_dict_unread_pair', clean=True) == (0, 0)


def test_survivor_M12_store_after_uncounted_reports(tmp):
    assert _survivor(tmp, 'M12_store_after_uncounted') == (1, 1)


def test_survivor_M12_store_after_uncounted_stays_clean(tmp):
    assert _survivor(tmp, 'M12_store_after_uncounted', clean=True) == (0, 0)


def test_survivor_M12_pop_after_uncounted_reports(tmp):
    assert _survivor(tmp, 'M12_pop_after_uncounted') == (1, 1)


def test_survivor_M12_pop_after_uncounted_stays_clean(tmp):
    assert _survivor(tmp, 'M12_pop_after_uncounted', clean=True) == (0, 0)


def test_survivor_M22_fromkeys_reports(tmp):
    assert _survivor(tmp, 'M22_fromkeys') == (1, 1)


def test_survivor_M22_fromkeys_stays_clean(tmp):
    assert _survivor(tmp, 'M22_fromkeys', clean=True) == (0, 0)


_DYN = '{str(args.chrome_tab): 1}'


def test_one_dynamic_entry_display_counts_one_key(tmp):
    assert _verdict(tmp, _PRE + f'x = [*{_DYN}, relay(), ordinary]\n'
                    'send = ext_cmd\nreturn x[2]' + _T) == (0, 0)


def test_one_dynamic_entry_display_reports_after_it(tmp):
    assert _verdict(tmp, _body(
        f'x = [*{_DYN}, relay(), ordinary]', 'x[1]()')) == (1, 1)


def test_one_dynamic_entry_display_stays_clean(tmp):
    assert _verdict(tmp, _body(
        f'x = [*{_DYN}, relay(), ordinary]', 'x[1]()',
        prefix=_PRE_CLEAN)) == (0, 0)


_TWO_DYNAMIC = ('x = [*{str(args.chrome_tab): 1, str(args.chrome_tab): 2}, '
                'relay()]')


def test_two_dynamic_entry_display_count_stays_unknown(tmp):
    assert _verdict(tmp, _body(_TWO_DYNAMIC, 'x[1]()')) == (1, 1)


def test_two_dynamic_entry_display_stays_clean(tmp):
    assert _verdict(tmp, _body(
        _TWO_DYNAMIC, 'x[1]()', prefix=_PRE_CLEAN)) == (0, 0)


def test_tuple_after_counted_dict_star_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = (*{"a": relay()}, ordinary)', 'x[1]' + _T)) == (0, 0)


def test_tuple_after_counted_dict_star_reports(tmp):
    assert _verdict(tmp, _body('x = (*{"a": 1}, relay())', 'x[1]()')) \
        == (1, 1)


_DYN2 = '{str(args.chrome_tab): 1, str(args.chrome_tab): 2}'
_UNPACKS = {
    'U1_list_dyn': (f'a, b = [*{_DYN}, relay()]', 'b()'),
    'U1_list_two_dyn': (f'a, b = [*{_DYN2}, relay()]', 'b()'),
    'U3_star_target': (f'a, *b = [*{_DYN}, relay()]', 'b[0]()'),
    'U3_star_target_two_dyn': (f'a, *b = [*{_DYN2}, relay()]', 'b[0]()'),
    'U4_opaque': ('a, b, c = [*args.values, relay()]', 'c()'),
    'U5_stored_dyn': ('d = {}\nd[str(args.chrome_tab)] = 1\n'
                      'a, b = [*d, relay()]', 'b()'),
    'U6_fromkeys': ('a, b = [*dict.fromkeys(["z"], 1), relay()]', 'b()'),
    'U10_dict_or_dyn': ('d = {}\nd[str(args.chrome_tab)] = 1\n'
                        'a, b = [*(d | {}), relay()]', 'b()'),
    'U12_pop_dyn': ('d = {"z": 1, "y": 2}\nd.pop(str(args.flag) and "z")\n'
                    'a, b = [*d, relay()]', 'b()'),
}


def _unpack(tmp, name, prefix=_PRE):
    store, invoke = _UNPACKS[name]
    return _verdict(tmp, _body(store, invoke, prefix=prefix))


def test_unpack_U1_list_dyn_reports(tmp):
    assert _unpack(tmp, 'U1_list_dyn') == (1, 1)


def test_unpack_U1_list_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'U1_list_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_U1_list_two_dyn_reports(tmp):
    assert _unpack(tmp, 'U1_list_two_dyn') == (1, 1)


def test_unpack_U1_list_two_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'U1_list_two_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_U3_star_target_reports(tmp):
    assert _unpack(tmp, 'U3_star_target') == (1, 1)


def test_unpack_U3_star_target_stays_clean(tmp):
    assert _unpack(tmp, 'U3_star_target', _PRE_CLEAN) == (0, 0)


def test_unpack_U3_star_target_two_dyn_reports(tmp):
    assert _unpack(tmp, 'U3_star_target_two_dyn') == (1, 1)


def test_unpack_U3_star_target_two_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'U3_star_target_two_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_U4_opaque_reports(tmp):
    assert _unpack(tmp, 'U4_opaque') == (1, 1)


def test_unpack_U4_opaque_stays_clean(tmp):
    assert _unpack(tmp, 'U4_opaque', _PRE_CLEAN) == (0, 0)


def test_unpack_U5_stored_dyn_reports(tmp):
    assert _unpack(tmp, 'U5_stored_dyn') == (1, 1)


def test_unpack_U5_stored_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'U5_stored_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_U6_fromkeys_reports(tmp):
    assert _unpack(tmp, 'U6_fromkeys') == (1, 1)


def test_unpack_U6_fromkeys_stays_clean(tmp):
    assert _unpack(tmp, 'U6_fromkeys', _PRE_CLEAN) == (0, 0)


def test_unpack_U10_dict_or_dyn_reports(tmp):
    assert _unpack(tmp, 'U10_dict_or_dyn') == (1, 1)


def test_unpack_U10_dict_or_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'U10_dict_or_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_U12_pop_dyn_reports(tmp):
    assert _unpack(tmp, 'U12_pop_dyn') == (1, 1)


def test_unpack_U12_pop_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'U12_pop_dyn', _PRE_CLEAN) == (0, 0)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dictlengths_')


if __name__ == '__main__':
    raise SystemExit(main())
