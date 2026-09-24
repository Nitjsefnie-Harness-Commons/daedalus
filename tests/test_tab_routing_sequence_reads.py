#!/usr/bin/env python3
"""Every reader of a sequence container fails closed: an unknown length, the
unknown-key slot or a position it cannot name exactly joins every item."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_dict_stores import (  # noqa: E402
    _PRE, _PRE_CLEAN, _verdict)

_SL = '\nsend = ext_cmd\nreturn '
_DISPLAYS = {
    'opaque_star': 'x = [*args.values, relay()]',
    'string_star': 'x = [*"ab", relay()]',
    'bytes_star': 'x = [*b"ab", relay()]',
    'tuple_star': 'x = [*(1, 2), relay()]',
    'set_star': 'x = [*{1, 2}, relay()]',
    'known': 'x = [ordinary, ordinary, relay()]',
    'unknown_slot': ('x = [ordinary, ordinary, ordinary]\n'
                     'x[len(args.values)] = relay()'),
}
_READERS = {
    'pop': ('', 'x.pop()()'),
    'pop_last': ('', 'x.pop(-1)()'),
    'pop_index': ('', 'x.pop(2)()'),
    'copy': ('', 'x.copy()[2]()'),
    'getitem': ('', 'x.__getitem__(2)()'),
    'list': ('', 'list(x)[2]()'),
    'list_last': ('', 'list(x)[-1]()'),
    'tuple': ('', 'tuple(x)[2]()'),
    'reversed': ('', 'next(reversed(x))()'),
    'reversed_list': ('', 'list(reversed(x))[0]()'),
    'reverse_slice': ('', 'x[::-1][0]()'),
    'tail_slice': ('', 'x[-1:][0]()'),
    'last': ('', 'x[-1]()'),
    'computed': ('', 'x[len(x) - 1]()'),
    'sorted': ('', 'sorted(x, key=lambda f: 0)[2]()'),
    'star_argument': ('', '(lambda a, b, c: c)(*x)()'),
    'star_after_argument': ('', '(lambda p, a, b, c: c)(0, *x)()'),
    'comprehension_target': ('', '[c for a, b, c in [x]][0]()'),
    'generator_target': ('', 'next(c for a, b, c in [x])()'),
    'starred_target': ('', '[b[1] for a, *b in [x]][0]()'),
    'enumerate': ('', 'list(enumerate(x))[2][1]()'),
    'map': ('', 'list(map(lambda f: f, x))[2]()'),
    'alias_pop': ('y = x', 'y.pop()()'),
    'bound_last': ('f = x[-1]', 'f()'),
}
_TUPLES = {
    'string_star_tuple': ('x = (*"ab", relay())', 'x[-1]()'),
    'string_star_inline': ('', '(*"ab", relay())[-1]()'),
}


def _shapes():
    for display, store in _DISPLAYS.items():
        for reader, (bind, invoke) in _READERS.items():
            lines = store + ('\n' + bind if bind else '')
            yield display + '/' + reader, lines + _SL + invoke
    for name, (store, invoke) in _TUPLES.items():
        yield name, store + _SL + invoke


def _mismatches(tmp, prefix, expected):
    return [(name, verdict) for name, body in _shapes()
            if (verdict := _verdict(tmp, prefix + body)) != expected]


def test_every_sequence_reader_reports_a_relay_it_may_return(tmp):
    missed = _mismatches(tmp, _PRE, (1, 1))
    assert not missed, missed


def test_every_sequence_reader_stays_clean_without_a_relay(tmp):
    flagged = _mismatches(tmp, _PRE_CLEAN, (0, 0))
    assert not flagged, flagged


_QUIET = 'def quiet(): return lambda *a, **k: ordinary()\n'
_PLACED = {
    'before_opaque_star': ('x = [quiet(), *args.values, relay()]', 'x[0]()'),
    'tuple_before_star': ('x = (quiet(), quiet(), *args.values, relay())',
                          'x[1]()'),
    'unpacked_before_star': ('x = [quiet(), *args.values, relay()]\n'
                             'a, *rest = x', 'a()'),
    'second_unpacked_before_star': (
        'x = [quiet(), quiet(), *args.values, relay()]\na, b, *c = x', 'b()'),
    'bound_before_star': ('x = [quiet(), *args.values, relay()]\nf = x[0]',
                          'f()'),
    'nested_before_star': ('d = {"k": [quiet(), *args.values, relay()]}',
                           'd["k"][0]()'),
    'before_generator_star': ('x = [quiet(), *(i for i in [1]), relay()]',
                              'x[0]()'),
    'after_single_set': ('x = [*{relay()}, quiet()]', 'x[1]()'),
    'single_set_name': ('y = {quiet()}\nx = [*y, relay()]', 'x[0]()'),
    'single_set_inline': ('', '[*{quiet()}, relay()][0]()'),
    'last_after_single_set': ('x = [relay(), *{quiet()}]', 'x[-1]()'),
}


def _placed(tmp, twin):
    """Each shape reads a quiet callable the guard can place; its twin swaps
    the two, so the same position holds the relay."""
    swap = {'relay': 'quiet', 'quiet': 'relay'}
    verdicts = {}
    for name, (store, invoke) in _PLACED.items():
        if twin:
            store, invoke = (re.sub(r'relay|quiet', lambda m: swap[m[0]],
                                    part) for part in (store, invoke))
        verdicts[name] = _verdict(tmp, _PRE + _QUIET + store + _SL + invoke)
    return verdicts


def test_a_placed_quiet_callable_stays_clean(tmp):
    flagged = {name: verdict for name, verdict in _placed(tmp, False).items()
               if verdict != (0, 0)}
    assert not flagged, flagged


def test_a_placed_relay_reports(tmp):
    missed = {name: verdict for name, verdict in _placed(tmp, True).items()
              if verdict != (1, 1)}
    assert not missed, missed


_STARRED_SET = 'x = [*{*args.values}, relay()]' + _SL + 'x[2]()'


def test_a_set_with_one_starred_element_has_no_count(tmp):
    assert _verdict(tmp, _PRE + _STARRED_SET) == (1, 1)


def test_a_set_with_one_starred_element_stays_clean(tmp):
    assert _verdict(tmp, _PRE_CLEAN + _STARRED_SET) == (0, 0)


_REPLACED_FIRST = ('f = relay()\nx = [quiet(), *args.values, f]\n'
                   'if args.flag:\n    x[int(args.flag) - 1] = f\n')


def test_an_unknown_key_store_reopens_the_exact_prefix(tmp):
    assert _verdict(tmp, _PRE + _QUIET + _REPLACED_FIRST + _SL
                    + 'x[0]()') == (1, 1)


_UNTAKEN_KEY_STORES = {
    'string_key_restarred': ("x[\'k\'] = relay()", 'y = [*x]'),
    'string_key_unpacked': ("x[\'k\'] = relay()", 'a, *b = x'),
    'none_key_restarred': ('x[None] = relay()', 'y = [ordinary, *x]'),
}


def test_a_non_int_key_on_a_sequence_does_not_crash(tmp):
    """The store raises at runtime, so only an untaken path holds it."""
    verdicts = {name: _verdict(tmp, _PRE + 'x = [*args.values]\n'
                               'if args.flag is None:\n    ' + store + '\n'
                               + read + _SL + '0')
                for name, (store, read) in _UNTAKEN_KEY_STORES.items()}
    assert verdicts == dict.fromkeys(_UNTAKEN_KEY_STORES, (0, 0)), verdicts


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='seqreads_')


if __name__ == '__main__':
    raise SystemExit(main())
