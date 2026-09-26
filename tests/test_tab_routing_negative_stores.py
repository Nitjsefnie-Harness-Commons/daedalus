#!/usr/bin/env python3
"""A negative list index names a position counted from the end, and a
store at that key writes the position a read at it consults.

The read path resolves the arithmetic and the store path is what has to
agree with it. Every row below derives its expectation from that
arithmetic rather than from a hand-listed pair, and every key runs
through all three spellings `_literal_key` reaches by different routes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing_dict_stores import (  # noqa: E402
    _PRE, _PRE_CLEAN, _verdict)

_SL = '\nsend = ext_cmd\nreturn '
_QUIET = 'def quiet(): return lambda *a, **k: ordinary()\n'


def _run(tmp, shape, clean=False):
    return _verdict(tmp, (_PRE_CLEAN if clean else _PRE) + shape)


# A negative key names a position counted from the end, so a store at `-1`
# writes the position `L - 1` and one at `-L` writes position 0. Each row
# below names that position and derives its expectation from the runtime's
# own arithmetic, and every row runs through all three spellings
# `_literal_key` reaches by different routes, so no spelling can diverge
# from the arithmetic the other two follow.
_NEGATIVE_STORES = {
    'minus_one_of_two': (-1, 2, 1),
    'minus_length': (-2, 2, 0),
    'minus_one_of_three': (-1, 3, 2),
    'zero_of_three': (0, 3, 0),
}
_SPELLINGS = ('literal', 'name', 'parens')
# A star over a set display: the model cannot count what a set holds, so
# the list built from it has no length and no exact position.
_OPEN_LENGTH = 'x = [*{quiet(), quiet()}, quiet()]\n'


def _key(k, spelling):
    return {'literal': f'x[{k}]', 'name': 'x[i]',
            'parens': f'x[({k})]'}[spelling]


def _bind(k, spelling):
    return f'i = {k}\n' if spelling == 'name' else ''


def _pair(length, relay_at):
    return 'x = [%s]\n' % ', '.join(
        'relay()' if index == relay_at else 'quiet()'
        for index in range(length))


def _store_shape(k, spelling, list_shape, value, read):
    return _QUIET + _bind(k, spelling) + list_shape + _key(k, spelling) \
        + f' = {value}' + _SL + read


def _negative_store_verdicts(tmp, row):
    """The two directions of one (key, length, position) row: a clean list
    where the store is the only relay, and a list whose relay the store
    overwrites. A store that lands anywhere but the named position fails
    one and not the other."""
    k, length, position = row
    for spelling in _SPELLINGS:
        carried = _run(tmp, _store_shape(
            k, spelling, 'x = [%s]\n' % ', '.join(['quiet()'] * length),
            'relay()', f'x[{position}]()'))
        overwritten = _run(tmp, _store_shape(
            k, spelling, _pair(length, position), 'quiet()',
            f'x[{position}]()'))
        yield spelling, carried, overwritten


def test_a_store_at_a_negative_key_writes_the_position_it_names(tmp):
    # The relay the store carries is reachable at exactly the position the
    # key names; anywhere else the guard loses a value the runtime calls.
    wrong = [(name, spelling, carried) for name, row
             in _NEGATIVE_STORES.items()
             for spelling, carried, _ in _negative_store_verdicts(tmp, row)
             if carried != (1, 1)]
    assert not wrong, wrong


def test_a_store_that_reaches_its_own_position_replaces_the_relay(tmp):
    # The face a fix that simply reported every negative store would break:
    # the store overwrites the relay it names, so the read is clean.
    wrong = [(name, spelling, overwritten) for name, row
             in _NEGATIVE_STORES.items()
             for spelling, _, overwritten
             in _negative_store_verdicts(tmp, row)
             if overwritten != (0, 0)]
    assert not wrong, wrong


def test_negative_store_on_an_unknown_length_fails_closed(tmp):
    # A star over a set the model cannot count leaves no length, so there
    # is no position to write. The stored relay must reach the
    # unknown-key slot every positional read consults, or it is invisible
    # to EVERY later read - a non-negative one included, which consults
    # that slot and its own index and nothing else.
    wrong = []
    for spelling in _SPELLINGS:
        for read, expected in (('x[0]()', (0, 1)), ('x[2]()', (1, 1))):
            body = _store_shape(-1, spelling, _OPEN_LENGTH, 'relay()', read)
            verdict = _run(tmp, body)
            if verdict != expected:
                wrong.append((spelling, read, verdict))
    assert not wrong, wrong


def test_negative_store_on_an_unknown_length_stays_clean(tmp):
    body = _store_shape(-1, 'literal', _OPEN_LENGTH, 'quiet()', 'x[1]()')
    assert _run(tmp, body) == (0, 0)
    assert _run(tmp, body, clean=True) == (0, 0)


def test_subscript_delete_at_a_negative_key_removes_that_position(tmp):
    # `del x[-1]` removes the last element, not a key named `-1`.
    body = _QUIET + 'x = [quiet(), quiet(), relay()]\ndel x[-1]' \
        + _SL + 'x[-1]()'
    assert _run(tmp, body) == (0, 0)
    assert _run(tmp, body.replace('relay()', 'quiet()')) == (0, 0)


def test_a_mapping_keyed_at_a_negative_index_is_untouched(tmp):
    # `d[-1]` is a genuine key: no length, no position, nothing to resolve.
    body = 'd = {}\nd[-1] = relay()' + _SL + 'd[-1]()'
    assert _run(tmp, body) == (1, 1)
    assert _run(tmp, body, clean=True) == (0, 0)


def test_a_read_at_a_negative_index_reports_its_own_position(tmp):
    body = _QUIET + 'x = [quiet(), relay()]' + _SL + 'x[-1]()'
    assert _run(tmp, body) == (1, 1)
    assert _run(tmp, body, clean=True) == (0, 0)

def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='negstore_')


if __name__ == '__main__':
    raise SystemExit(main())
