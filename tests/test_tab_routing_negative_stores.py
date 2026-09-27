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
from _tabroute_focus import _tracked_focus_verdict  # noqa: E402

_CALL = 'send("_focus", "focus-tab", tab=args.chrome_tab)'
_PRE = ('send = ordinary\n'
        'args = _args\n'
        'def maker():\n'
        f'    return lambda: {_CALL}\n'
        'def relay(): return maker()\n')
_PRE_CLEAN = ('send = ordinary\n'
              'args = _args\n'
              'def maker():\n'
              '    return lambda: ordinary()\n'
              'def relay(): return maker()\n')
_SL = '\nsend = ext_cmd\nreturn '
_QUIET = 'def quiet(): return lambda *a, **k: ordinary()\n'


def _run(tmp, shape, clean=False):
    return _tracked_focus_verdict(
        tmp, (_PRE_CLEAN if clean else _PRE) + shape, counts=True)


# Each row is (store key, length, the position that key names).
# `zero_of_three` is the non-negative spelling of the same shape, so a
# regression on the non-negative path fails it.
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
# -L is the last key a list of length L can name and one below it is a
# store the runtime raises on; the pair straddles the boundary.
_BOUNDARY = {2: (-2, -3), 3: (-3, -4)}
# A kind with no item assignment refuses every subscript store, in either
# sign. Each row is (seed, name, key, the lines before the read, the read):
# a set is read by spreading it and a tuple by subscripting it.
_NO_ITEM_ASSIGNMENT = {
    'tuple_negative': ('t = (quiet(), quiet())\n', 't', -1, '', 't[1]()'),
    'tuple_positive': ('t = (quiet(), quiet())\n', 't', 1, '', 't[1]()'),
    'set_negative': ('s = {quiet(), quiet()}\n', 's', -1, 'x = [*s]\n',
                     'x[0]()'),
    'set_positive': ('s = {quiet(), quiet()}\n', 's', 0, 'x = [*s]\n',
                     'x[0]()'),
    'frozenset': ('v = frozenset([quiet(), quiet()])\n', 'v', 0,
                  'w = [*v]\n', 'w[0]()'),
}

# The delete sign of the same property. `del` and `=` are different
# statements, so the row names its own. A tuple, because a subscript
# delete on a set is unreachable from a subscript context and a tuple is
# where the sign is likeliest to be re-opened.
_NO_ITEM_ASSIGNMENT_DELETE = (
    't = (relay(), quiet())\n', 't', 0, '', 't[1]()', 'del t[0]')


def _key(k, spelling):
    return {'literal': f'x[{k}]', 'name': 'x[i]',
            'parens': f'x[({k})]'}[spelling]


def _bind(k, spelling):
    return f'i = {k}\n' if spelling == 'name' else ''


def _elements(length, relay_at):
    return ', '.join('relay()' if index == relay_at else 'quiet()'
                     for index in range(length))


def _pair(length, relay_at):
    return f'x = [{_elements(length, relay_at)}]\n'


def _clean(length):
    return f'x = [{", ".join(["quiet()"] * length)}]\n'


def _store_shape(k, spelling, list_shape, value, read):
    return _QUIET + _bind(k, spelling) + list_shape + _key(k, spelling) \
        + f' = {value}' + _SL + read


def _refused_shape(k, spelling, list_shape, value, read):
    """The same shape, with the store wrapped in the `try` a program needs
    to reach the read at all: a key past the start raises at the store, so
    the read after it is one the runtime would never run uncaught. Either
    way the position is unreachable and the verdict is clean."""
    return _QUIET + _bind(k, spelling) + list_shape \
        + f'try:\n    {_key(k, spelling)} = {value}\n' \
        + 'except IndexError:\n    pass\n' + _SL + read


def _no_assignment_shape(seed, name, k, prelude, read, statement=None):
    """A store into a kind with no item assignment. The store raises
    TypeError, so the `try` is the program's own: without it the read has
    nowhere to run from."""
    store = statement or f'{name}[{k}] = relay()'
    return _QUIET + seed + f'try:\n    {store}\n' \
        + 'except TypeError:\n    pass\n' + prelude + _SL + read


def _negative_store_verdicts(tmp, row):
    """The two directions of one (key, length, position) row: a clean list
    where the store is the only relay, and a list whose relay the store
    overwrites."""
    k, length, position = row
    for spelling in _SPELLINGS:
        carried = _run(tmp, _store_shape(
            k, spelling, _clean(length), 'relay()', f'x[{position}]()'))
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


def test_the_last_reachable_key_is_not_the_first_refused_one(tmp):
    # Both halves of the boundary, and the two fail opposite errors: drop
    # the refused leg and a guard that resolved every negative key against
    # the length reports a store the runtime never made; drop the
    # reachable leg and a guard that refused every negative key loses one
    # the runtime makes.
    wrong = []
    for length, (reachable, refused) in _BOUNDARY.items():
        for spelling in _SPELLINGS:
            # Two reads, because the two ways a wrong resolution of a
            # past-the-start key hides are different: a read at the key
            # itself catches one that lands on the key it was spelled as,
            # and a read at position 0 catches one that clamps the
            # out-of-range position to 0 instead of declining it.
            for read in ('x[-1]()', 'x[0]()'):
                dropped = _run(tmp, _refused_shape(
                    refused, spelling, _clean(length), 'relay()', read))
                if dropped != (0, 0):
                    wrong.append((length, spelling, 'refused', read,
                                  dropped))
            named = _run(tmp, _store_shape(
                reachable, spelling, _clean(length), 'relay()',
                f'x[{length + reachable}]()'))
            if named != (1, 1):
                wrong.append((length, spelling, 'reachable', named))
    assert not wrong, wrong


def test_a_kind_with_no_item_assignment_refuses_every_store(tmp):
    # Neither sign of the key makes the store happen, so gating only the
    # negative one would leave the positive twin reporting a false
    # positive that predates this branch.
    #
    # The last leg is what makes the others mean anything: a list, which
    # does take item assignment, where the same negative store must still
    # be recorded. A gate that declined every store rather than every
    # store into a kind without one would pass all five rows and fail it.
    wrong = []
    for name, row in _NO_ITEM_ASSIGNMENT.items():
        verdict = _run(tmp, _no_assignment_shape(*row))
        if verdict != (0, 0):
            wrong.append((name, verdict))
    recorded = _run(tmp, _store_shape(
        -1, 'literal', _clean(2), 'relay()', 'x[1]()'))
    if recorded != (1, 1):
        wrong.append(('list_negative', recorded))
    # The gate's second sign. (0, 1) is a FALSE POSITIVE against a runtime
    # of (0, 0) - a tuple delete always raises, so the container is
    # provably unchanged - and it is one of the three named in the pull
    # request, tracked as #1220. It is the conservative answer for a
    # subscript delete inside a branch, which fires on the statement
    # rather than on the modelled effect. The family pre-dates this branch
    # and is visible on a list at `main`, AND this branch adds the tuple
    # position to it: the same shape on a tuple reads (0, 0) on `main` and
    # (0, 1) here, so the tuple row is not one that was always reporting.
    # Pinned because it is the verdict, not because it is right.
    deleted = _run(tmp, _no_assignment_shape(*_NO_ITEM_ASSIGNMENT_DELETE))
    if deleted != (0, 1):
        wrong.append(('tuple_delete', deleted))
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
