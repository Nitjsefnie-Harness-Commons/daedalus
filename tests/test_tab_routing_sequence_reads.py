#!/usr/bin/env python3
"""Every reader of a sequence container fails closed: an unknown length, the
unknown-key slot or a position it cannot name exactly joins every item."""
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='seqreads_')


if __name__ == '__main__':
    raise SystemExit(main())
