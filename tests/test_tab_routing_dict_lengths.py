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
    'update_zip': ('d = {}\nd.update(zip(["a", "b"], [1, 2]))\n'
                   'x = [*d, relay()]', 'x[2]()'),
    'update_vars': ('d = {}\nd.update(vars(args))\nx = [*d, relay()]',
                    'x[3]()'),
    'ior_vars': ('d = {}\nd |= vars(args)\nx = [*d, relay()]', 'x[3]()'),
    'update_call_kw': ('d = {}\nd.update(k=len("ab"))\n'
                       'x = [*d, relay()]', 'x[1]()'),
    'setdefault_call': ('d = {}\nd.setdefault("k", len("ab"))\n'
                        'x = [*d, relay()]', 'x[1]()'),
}


def _unreadable(tmp, name, prefix=_PRE):
    store, invoke = _UNREADABLE_STORES[name]
    return _verdict(tmp, _body(store, invoke, prefix=prefix))


def test_unreadable_store_update_zip_reports(tmp):
    assert _unreadable(tmp, 'update_zip') == (1, 1)


def test_unreadable_store_update_zip_stays_clean(tmp):
    assert _unreadable(tmp, 'update_zip', _PRE_CLEAN) == (0, 0)


def test_unreadable_store_update_vars_reports(tmp):
    assert _unreadable(tmp, 'update_vars') == (1, 1)


def test_unreadable_store_update_vars_stays_clean(tmp):
    assert _unreadable(tmp, 'update_vars', _PRE_CLEAN) == (0, 0)


def test_unreadable_store_ior_vars_reports(tmp):
    assert _unreadable(tmp, 'ior_vars') == (1, 1)


def test_unreadable_store_ior_vars_stays_clean(tmp):
    assert _unreadable(tmp, 'ior_vars', _PRE_CLEAN) == (0, 0)


def test_unreadable_store_update_call_kw_reports(tmp):
    assert _unreadable(tmp, 'update_call_kw') == (1, 1)


def test_unreadable_store_update_call_kw_stays_clean(tmp):
    assert _unreadable(tmp, 'update_call_kw', _PRE_CLEAN) == (0, 0)


def test_unreadable_store_setdefault_call_reports(tmp):
    assert _unreadable(tmp, 'setdefault_call') == (1, 1)


def test_unreadable_store_setdefault_call_stays_clean(tmp):
    assert _unreadable(tmp, 'setdefault_call', _PRE_CLEAN) == (0, 0)


_T = '("_focus", "focus-tab", tab=args.chrome_tab)'
_UNCOUNTED = 'd = {"a": 1}\ndel d[str(args.flag) and "a"]\n'
_SEND_LAST = '\nsend = ext_cmd\nreturn '
_STAR_COUNT_SHAPES = {
    'opaque_star_sender_after': (
        'send = ext_cmd\nargs.box = [send]\nx = [relay(), *args.box]\n'
        'return x[1]' + _T),
    'or_uncounted': (
        _UNCOUNTED + 'x = [*(d | {}), relay()]' + _SEND_LAST + 'x[0]()'),
    'call_uncounted': (
        _UNCOUNTED + 'x = [*dict(d), relay()]' + _SEND_LAST + 'x[0]()'),
    'call_kw_uncounted': (
        _UNCOUNTED + 'x = [*dict(**d), relay()]' + _SEND_LAST + 'x[0]()'),
    'update_unread_pair': (
        'd = {}\nd.update([("a", 1), tuple(["b", 2])])\nx = [*d, relay()]'
        + _SEND_LAST + 'x[2]()'),
    'dict_unread_pair': (
        'x = [*dict([("a", 1), tuple(["b", 2])]), relay()]'
        + _SEND_LAST + 'x[2]()'),
    'store_after_uncounted': (
        _UNCOUNTED + 'd["b"] = 1\nx = [*d, relay()]' + _SEND_LAST
        + 'x[1]()'),
    'pop_after_uncounted': (
        'd = {"a": 1, "b": 2}\ndel d[str(args.flag) and "a"]\nd.pop("b")\n'
        'x = [*d, relay()]' + _SEND_LAST + 'x[0]()'),
    'fromkeys_star': (
        'd = dict.fromkeys(["a", "b"], relay())\nx = [*d, relay()]'
        + _SEND_LAST + 'x[2]()'),
}


def _star_count(tmp, name, clean=False):
    shape = _STAR_COUNT_SHAPES[name]
    if clean:
        shape = _PRE_CLEAN + shape.replace('args.box = [send]',
                                           'args.box = [ordinary]')
    else:
        shape = _PRE + shape
    return _verdict(tmp, shape)


def test_star_count_opaque_star_sender_after_reports(tmp):
    assert _star_count(tmp, 'opaque_star_sender_after') == (1, 1)


def test_star_count_opaque_star_sender_after_is_unprovable(tmp):
    assert _star_count(tmp, 'opaque_star_sender_after', clean=True) == (0, 1)


def test_star_count_or_uncounted_reports(tmp):
    assert _star_count(tmp, 'or_uncounted') == (1, 1)


def test_star_count_or_uncounted_stays_clean(tmp):
    assert _star_count(tmp, 'or_uncounted', clean=True) == (0, 0)


def test_star_count_call_uncounted_reports(tmp):
    assert _star_count(tmp, 'call_uncounted') == (1, 1)


def test_star_count_call_uncounted_stays_clean(tmp):
    assert _star_count(tmp, 'call_uncounted', clean=True) == (0, 0)


def test_star_count_call_kw_uncounted_reports(tmp):
    assert _star_count(tmp, 'call_kw_uncounted') == (1, 1)


def test_star_count_call_kw_uncounted_stays_clean(tmp):
    assert _star_count(tmp, 'call_kw_uncounted', clean=True) == (0, 0)


def test_star_count_update_unread_pair_reports(tmp):
    assert _star_count(tmp, 'update_unread_pair') == (1, 1)


def test_star_count_update_unread_pair_stays_clean(tmp):
    assert _star_count(tmp, 'update_unread_pair', clean=True) == (0, 0)


def test_star_count_dict_unread_pair_reports(tmp):
    assert _star_count(tmp, 'dict_unread_pair') == (1, 1)


def test_star_count_dict_unread_pair_stays_clean(tmp):
    assert _star_count(tmp, 'dict_unread_pair', clean=True) == (0, 0)


def test_star_count_store_after_uncounted_reports(tmp):
    assert _star_count(tmp, 'store_after_uncounted') == (1, 1)


def test_star_count_store_after_uncounted_stays_clean(tmp):
    assert _star_count(tmp, 'store_after_uncounted', clean=True) == (0, 0)


def test_star_count_pop_after_uncounted_reports(tmp):
    assert _star_count(tmp, 'pop_after_uncounted') == (1, 1)


def test_star_count_pop_after_uncounted_stays_clean(tmp):
    assert _star_count(tmp, 'pop_after_uncounted', clean=True) == (0, 0)


def test_star_count_fromkeys_star_reports(tmp):
    assert _star_count(tmp, 'fromkeys_star') == (1, 1)


def test_star_count_fromkeys_star_stays_clean(tmp):
    assert _star_count(tmp, 'fromkeys_star', clean=True) == (0, 0)


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
    'list_dyn': (f'a, b = [*{_DYN}, relay()]', 'b()'),
    'list_two_dyn': (f'a, b = [*{_DYN2}, relay()]', 'b()'),
    'star_target': (f'a, *b = [*{_DYN}, relay()]', 'b[0]()'),
    'star_target_two_dyn': (f'a, *b = [*{_DYN2}, relay()]', 'b[0]()'),
    'opaque': ('a, b, c = [*args.values, relay()]', 'c()'),
    'stored_dyn': ('d = {}\nd[str(args.chrome_tab)] = 1\n'
                   'a, b = [*d, relay()]', 'b()'),
    'fromkeys': ('a, b = [*dict.fromkeys(["z"], 1), relay()]', 'b()'),
    'dict_or_dyn': ('d = {}\nd[str(args.chrome_tab)] = 1\n'
                    'a, b = [*(d | {}), relay()]', 'b()'),
    'pop_dyn': ('d = {"z": 1, "y": 2}\nd.pop(str(args.flag) and "z")\n'
                'a, b = [*d, relay()]', 'b()'),
}


def _unpack(tmp, name, prefix=_PRE):
    store, invoke = _UNPACKS[name]
    return _verdict(tmp, _body(store, invoke, prefix=prefix))


def test_unpack_list_dyn_reports(tmp):
    assert _unpack(tmp, 'list_dyn') == (1, 1)


def test_unpack_list_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'list_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_list_two_dyn_reports(tmp):
    assert _unpack(tmp, 'list_two_dyn') == (1, 1)


def test_unpack_list_two_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'list_two_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_star_target_reports(tmp):
    assert _unpack(tmp, 'star_target') == (1, 1)


def test_unpack_star_target_stays_clean(tmp):
    assert _unpack(tmp, 'star_target', _PRE_CLEAN) == (0, 0)


def test_unpack_star_target_two_dyn_reports(tmp):
    assert _unpack(tmp, 'star_target_two_dyn') == (1, 1)


def test_unpack_star_target_two_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'star_target_two_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_opaque_reports(tmp):
    assert _unpack(tmp, 'opaque') == (1, 1)


def test_unpack_opaque_stays_clean(tmp):
    assert _unpack(tmp, 'opaque', _PRE_CLEAN) == (0, 0)


def test_unpack_stored_dyn_reports(tmp):
    assert _unpack(tmp, 'stored_dyn') == (1, 1)


def test_unpack_stored_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'stored_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_fromkeys_reports(tmp):
    assert _unpack(tmp, 'fromkeys') == (1, 1)


def test_unpack_fromkeys_stays_clean(tmp):
    assert _unpack(tmp, 'fromkeys', _PRE_CLEAN) == (0, 0)


def test_unpack_dict_or_dyn_reports(tmp):
    assert _unpack(tmp, 'dict_or_dyn') == (1, 1)


def test_unpack_dict_or_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'dict_or_dyn', _PRE_CLEAN) == (0, 0)


def test_unpack_pop_dyn_reports(tmp):
    assert _unpack(tmp, 'pop_dyn') == (1, 1)


def test_unpack_pop_dyn_stays_clean(tmp):
    assert _unpack(tmp, 'pop_dyn', _PRE_CLEAN) == (0, 0)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dictlengths_')


if __name__ == '__main__':
    raise SystemExit(main())
