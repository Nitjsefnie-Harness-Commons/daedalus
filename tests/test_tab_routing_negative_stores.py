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


def _verdict(tmp, shape, clean=False):
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

# The slice sign, which is the one store a plain index never reaches:
# `x[0:1] = v` rewrites a run of positions and `x[0] = v` writes one, so
# they take different paths through the model and each needs its own gate.
# The list row is the control: it takes a slice store, the run it writes
# has a length the model cannot resolve, and the read must stay reported.
_NO_ITEM_ASSIGNMENT_SLICE = {
    'tuple': ('t = (relay(), quiet())\n', 't', '0:1', '', 't[1]()',
              't[0:1] = [quiet()]'),
    'list': ('t = [relay(), quiet(), quiet()]\n', 't', '0:1', '', 't[1]()',
             't[0:1] = [quiet()]'),
}


def _spelling(k, spelling):
    return {'literal': f'x[{k}]', 'name': 'x[i]',
            'parens': f'x[({k})]'}[spelling]


def _bind(k, spelling):
    return f'i = {k}\n' if spelling == 'name' else ''


def _pair(length, relay_at):
    body = ', '.join('relay()' if index == relay_at else 'quiet()'
                     for index in range(length))
    return f'x = [{body}]\n'


def _clean(length):
    return f'x = [{", ".join(["quiet()"] * length)}]\n'


def _store_shape(k, spelling, list_shape, value, read):
    return _QUIET + _bind(k, spelling) + list_shape + _spelling(k, spelling) \
        + f' = {value}' + _SL + read


def _refused_shape(k, spelling, list_shape, value, read):
    """The same shape, with the store wrapped in the `try` a program needs
    to reach the read at all: a key past the start raises at the store, so
    the read after it is one the runtime would never run uncaught. Either
    way the position is unreachable and the verdict is clean."""
    return _QUIET + _bind(k, spelling) + list_shape \
        + f'try:\n    {_spelling(k, spelling)} = {value}\n' \
        + 'except IndexError:\n    pass\n' + _SL + read


def _no_assignment_shape(seed, name, k, prelude, read, statement=None):
    """A store into a kind with no item assignment. The store raises
    TypeError, so the `try` is the program's own: without it the read has
    nowhere to run from."""
    store = statement or f'{name}[{k}] = relay()'
    return _QUIET + seed + f'try:\n    {store}\n' \
        + 'except TypeError:\n    pass\n' + prelude + _SL + read


# The delete sign, one row per kind that refuses `del x[k]`. The refusal is
# the kind's, not the key's: a tuple takes no item deletion at any key, and
# a slice refuses with the same TypeError an index does, so both spellings
# run against every row. `prelude` is what a kind whose element is not
# callable needs to reach a position that holds one.
_NO_ITEM_DELETION = {
    'tuple': ('t = (relay(), quiet())\n', 't', '', 't[1]()'),
}
# A kind the model records no positional container for, so the delete gate
# has no kind to decline and the row is clean whichever way the gate reads.
# A set, a frozenset, a str, a bytes and a range are all here: their
# elements are not addressable by position, so a delete can never move a
# recorded fact. These rows do not discriminate today, and they say so
# rather than pretending otherwise -- they pin that a narrowing does not
# begin tracking such a kind in order to invalidate it.
_NO_ITEM_DELETION_UNTRACKED = {
    'set': ('s = {relay(), quiet()}\n', 's', 'x = [quiet() for _ in s]\n',
            'x[0]()'),
    'frozenset': ('v = frozenset([relay(), quiet()])\n', 'v',
                  'x = [quiet() for _ in v]\n', 'x[0]()'),
    'str': ('s = "ab"\n', 's', 'x = [quiet() for _ in s]\n', 'x[0]()'),
    'bytes': ('s = b"ab"\n', 's', 'x = [quiet() for _ in s]\n', 'x[0]()'),
    'range': ('s = range(3)\n', 's', 'x = [quiet() for _ in s]\n', 'x[0]()'),
}
# The kinds the gate must keep claiming. A list takes item deletion at both
# keys, and a mapping's delete is followed precisely. Each row keeps its own
# exact answer: an index delete shifts one position and the model follows
# the shift, a slice delete leaves a length it cannot resolve and the read
# fails closed, and the mapping's two rows are the routed twin and its clean
# twin. A narrowing of the delete's KIND would move the slice row; a
# narrowing that dropped the arm altogether would move the routed one.
_ITEM_DELETION = {
    'list_index': ('x = [relay(), quiet()]\n', 'x', '0', 'x[0]()', (0, 0)),
    'list_slice': ('x = [relay(), quiet()]\n', 'x', '0:1', 'x[0]()', (0, 1)),
    'list_routed': ('x = [ordinary, relay()]\n', 'x', '0', 'x[0]()', (1, 1)),
    'dict_routed': ('d = {"a": 1, "b": relay()}\n', 'd', '"a"', 'd["b"]()',
                    (1, 1)),
    'dict_twin': ('d = {"a": 1, "b": quiet()}\n', 'd', '"a"', 'd["b"]()',
                  (0, 0)),
}


def _no_deletion_shape(seed, name, prelude, read, key, alias=False):
    """A subscript delete into a kind that takes no item deletion. The
    `try` is the program's own, for the reason the store shape's is: the
    delete raises, so the read has nowhere to run from without it. The
    aliased spelling binds the receiver to a second name and deletes
    through that, so the gate is reached the way an alias reaches it."""
    body = _QUIET + seed + (f'y = {name}\n' if alias else '')
    body += f'try:\n    del {"y" if alias else name}[{key}]\n'
    return body + 'except TypeError:\n    pass\n' + prelude + _SL + read


def _item_deletion_shape(seed, name, key, read, prelude=''):
    """The same shape on a kind that does take item deletion."""
    return _QUIET + seed + f'del {name}[{key}]\n' + prelude + _SL + read


def _negative_store_verdicts(tmp, row):
    """The two directions of one (key, length, position) row: a clean list
    where the store is the only relay, and a list whose relay the store
    overwrites."""
    k, length, position = row
    for spelling in _SPELLINGS:
        carried = _verdict(tmp, _store_shape(
            k, spelling, _clean(length), 'relay()', f'x[{position}]()'))
        overwritten = _verdict(tmp, _store_shape(
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
                dropped = _verdict(tmp, _refused_shape(
                    refused, spelling, _clean(length), 'relay()', read))
                if dropped != (0, 0):
                    wrong.append((length, spelling, 'refused', read,
                                  dropped))
            named = _verdict(tmp, _store_shape(
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
        verdict = _verdict(tmp, _no_assignment_shape(*row))
        if verdict != (0, 0):
            wrong.append((name, verdict))
    recorded = _verdict(tmp, _store_shape(
        -1, 'literal', _clean(2), 'relay()', 'x[1]()'))
    if recorded != (1, 1):
        wrong.append(('list_negative', recorded))
    # The gate's second sign. A tuple takes no item deletion, so the delete
    # raises, the container is provably unchanged, and a read of a position
    # it still holds is provable: the verdict is clean. The gate keys on
    # the receiver's kind, so it declines the delete here for the same
    # reason it declines the store above rather than for the key's sign.
    deleted = _verdict(tmp, _no_assignment_shape(*_NO_ITEM_ASSIGNMENT_DELETE))
    if deleted != (0, 0):
        wrong.append(('tuple_delete', deleted))
    assert not wrong, wrong


def test_a_kind_with_no_item_deletion_refuses_every_delete(tmp):
    # A kind that takes no item deletion drops nothing, so every position
    # the model recorded still holds what it held and a read of one is
    # provable. Each kind runs both key spellings and both receiver
    # spellings: the refusal is the kind's, and the gate has to reach the
    # delete the same way through an alias as through the name itself.
    wrong = []
    for name, row in list(_NO_ITEM_DELETION.items()) + list(
            _NO_ITEM_DELETION_UNTRACKED.items()):
        seed, receiver, prelude, read = row
        for key in ('0', '0:1'):
            for alias in (False, True):
                verdict = _verdict(tmp, _no_deletion_shape(
                    seed, receiver, prelude, read, key, alias))
                if verdict != (0, 0):
                    wrong.append((name, key, alias, verdict))
    assert not wrong, wrong


def test_a_kind_that_takes_item_deletion_keeps_reporting(tmp):
    # The control the narrowing above would break if it were a narrowing of
    # the store path rather than of the delete's kind, and the one that
    # fails if the arm is dropped altogether. A list and a mapping both
    # take `del x[k]`, so neither may be answered from the no-deletion gate.
    wrong = []
    for name, row in _ITEM_DELETION.items():
        seed, receiver, key, read, expected = row
        verdict = _verdict(tmp, _item_deletion_shape(
            seed, receiver, key, read))
        if verdict != expected:
            wrong.append((name, verdict, expected))
    assert not wrong, wrong


def test_a_kind_with_no_item_assignment_refuses_every_slice_store(tmp):
    # The other store sign, and the one a plain index never reaches. A
    # tuple rewrites no run of positions, so the model's recorded ones all
    # stand and the read is provable. The list row is the control: a list
    # does take a slice store, the run it writes has a length the model
    # cannot resolve, and the read stays reported. A gate that declined
    # every store into a kind without one passes the tuple row and fails it.
    wrong = []
    for name, row in _NO_ITEM_ASSIGNMENT_SLICE.items():
        verdict = _verdict(tmp, _no_assignment_shape(*row))
        if verdict != (0, 0 if name == 'tuple' else 1):
            wrong.append((name, verdict))
    assert not wrong, wrong


def test_a_delete_the_model_cannot_resolve_stays_fail_closed(tmp):
    # The third arm: a receiver whose kind the model never decided. The
    # delete gate declines it because there is no kind to decline, which
    # is not a reason to read a later call clean - the receiver may be a
    # list, and a list's delete moves what the model recorded. The row
    # reaches the receiver through a call the model does not fold, so no
    # spelling of the narrowing can answer for it, and a gate that
    # answered anyway would be trading a disclosure for a silence.
    shape = ('def pick(a, b): return a\n'
             'x = pick([relay(), quiet()], [quiet()])\n')
    wrong = []
    for alias in (False, True):
        body = _QUIET + shape
        body += 'y = x\n' if alias else ''
        body += f'try:\n    del {"y" if alias else "x"}[0]\n'
        body += 'except TypeError:\n    pass\n'
        verdict = _verdict(tmp, body + _SL + 'x[0]()')
        if verdict != (0, 1):
            wrong.append((alias, verdict))
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
            verdict = _verdict(tmp, body)
            if verdict != expected:
                wrong.append((spelling, read, verdict))
    assert not wrong, wrong


def test_negative_store_on_an_unknown_length_stays_clean(tmp):
    body = _store_shape(-1, 'literal', _OPEN_LENGTH, 'quiet()', 'x[1]()')
    assert _verdict(tmp, body) == (0, 0)
    assert _verdict(tmp, body, clean=True) == (0, 0)


def test_subscript_delete_at_a_negative_key_removes_that_position(tmp):
    # `del x[-1]` removes the last element, not a key named `-1`.
    body = _QUIET + 'x = [quiet(), quiet(), relay()]\ndel x[-1]' \
        + _SL + 'x[-1]()'
    assert _verdict(tmp, body) == (0, 0)
    assert _verdict(tmp, body.replace('relay()', 'quiet()')) == (0, 0)


def test_a_mapping_keyed_at_a_negative_index_is_untouched(tmp):
    # `d[-1]` is a genuine key: no length, no position, nothing to resolve.
    body = 'd = {}\nd[-1] = relay()' + _SL + 'd[-1]()'
    assert _verdict(tmp, body) == (1, 1)
    assert _verdict(tmp, body, clean=True) == (0, 0)


def test_a_read_at_a_negative_index_reports_its_own_position(tmp):
    body = _QUIET + 'x = [quiet(), relay()]' + _SL + 'x[-1]()'
    assert _verdict(tmp, body) == (1, 1)
    assert _verdict(tmp, body, clean=True) == (0, 0)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='negstore_')


if __name__ == '__main__':
    raise SystemExit(main())
