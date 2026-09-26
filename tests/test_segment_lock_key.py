#!/usr/bin/env python3
"""What the segment lock's key folds, checked in process with no bridge.

The key must put two names on one stripe whenever they could be one
filesystem entry, and two names on different stripes whenever they are
genuinely different. Both halves are here because neither needs a running
bridge: `seg_lock_for` binds no configuration, so the module is imported
by path and asked directly. The controls that need the bridge — proving a
hold is taken, and that two writes to one job stay atomic — are in
`test_segment_lock_stripes.py`, which carries the injected seams.

The stripe is hashed from a per-process secret, so two unrelated names land
on one stripe about one time in sixty-four. A control asserting that two
names DIFFER therefore proves nothing on its own, and every control here
first establishes a pair that can differ.
"""
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_store():
    """Import segment_store the way the bridge does, with no config set."""
    return _util.load(_util.ROOT / 'daedalus_bridge' / 'segment_store.py',
                      name='segkey_store')


def _require_a_discriminating_pair(store, name):
    """Fail unless some name is on a different stripe from `name`.

    A hashed stripe table makes two unrelated names collide occasionally, so
    "these two names differ" proves nothing on its own. What the fold
    controls need is a pair that CAN differ, which is what this establishes
    before they assert that a folded pair does not.
    """
    for attempt in range(256):
        other = f'unrelated-{attempt}'
        if store.seg_lock_for(name) is not store.seg_lock_for(other):
            return
    raise AssertionError(
        'no unrelated name landed on a different stripe, so a fold control '
        'here could not distinguish the fold from a hash collision')


def test_a_dotted_name_and_its_own_name_take_one_stripe(tmp):
    """`a` and `a.json` own one filesystem entry, so they take one lock.

    The flat namespace is what makes this true: the job named `a` keeps its
    record at `<job>.json`, which is the very directory the job named
    `a.json` keeps its segments in. Stripping every trailing `.json` names
    the chain those names belong to, and the whole chain then shares a
    stripe — the two writes that touch one path cannot interleave.
    """
    store = _load_store()
    for base in ('a', 'relay-1', 'x.y', '.hidden', '.json', 'seg'):
        first = store.seg_lock_for(base)
        assert first is store.seg_lock_for(base), base
        assert first is store.seg_lock_for(base + '.json'), base
        assert first is store.seg_lock_for(base + '.json.json'), base
        assert first is store.seg_lock_for(base + '.json.json.json'), base
    # The strip is on the record affix and not on any dot: `a.b` keeps its
    # own name. The bookkeeping names a job also spends are refused at mint
    # (see `test_the_dirty_bookkeeping_name_is_refused_beside_its_job`), so
    # no other name can reach a path this chain does not already name.
    assert (store.seg_lock_for('a.b')
            is store.seg_lock_for('a.b.json')), 'a.b'
    unrelated = 'relay-2'
    for attempt in range(256):
        unrelated = f'relay-2-{attempt}'
        if store.seg_lock_for(unrelated) is not store.seg_lock_for('relay-1'):
            break
    assert store.seg_lock_for(unrelated) is not store.seg_lock_for(
        'relay-1'), (unrelated, 'two unrelated jobs share one stripe')


def test_job_stripes_come_from_a_table_that_never_grows(tmp):
    """Ten thousand caller-chosen job names reuse one fixed table.

    A job name is authenticated but arbitrary, so a table keyed by name
    would be a leak; the stripe index is what makes the table finite.
    """
    store = _load_store()
    table = tuple(store.seg_locks)
    assert len(table) == store.SEGMENT_LOCK_STRIPES
    for index in range(10_000):
        lock = store.seg_lock_for(f'job-{index}')
        assert any(lock is original for original in table), index
    assert len(store.seg_locks) == len(table), 'the lock table grew'


def test_case_spellings_take_one_stripe(tmp):
    """`Foo` and `foo` are one directory on a case-insensitive parent.

    The single lock this branch replaced held them together whatever the
    filesystem did with case. A key that folded only the record affix
    would split them, and two writes to one directory would interleave the
    usage read, the quota check and the record write that `store_segment`
    holds as one.
    """
    store = _load_store()
    # The stripe is hashed from a per-process secret, so two unrelated names
    # land on one stripe about one time in sixty-four. A control that simply
    # asserted "these two differ" would therefore pass a key that folds
    # nothing, once in sixty-four runs — and it did, 19/20 rather than 20/20.
    # The name it compares against is searched for instead, so the assertion
    # is about the fold and never about a collision.
    _require_a_discriminating_pair(store, 'Foo')
    assert (store.seg_lock_for('Foo')
            is store.seg_lock_for('foo')), 'case spellings took two stripes'
    assert (store.seg_lock_for('Foo.json')
            is store.seg_lock_for('foo.JSON')), (
                'case spellings split the record chain')


def test_normalisation_spellings_take_one_stripe(tmp):
    """A composed and a decomposed spelling of one name take one stripe.

    Both spellings are written out rather than derived from one another: a
    control that built the second from the first would be testing the
    construction, and would pass against a key that folds nothing. They are
    spelled as escapes so the two stay two spellings in the file — an editor
    or a checkout that normalises source to NFC merges two raw literals into
    one, which is how this control silently stops testing anything. The
    first two assertions are the backstop for that.
    """
    composed = 'caf\u00e9'
    decomposed = 'cafe\u0301'
    assert composed != decomposed, 'the two literals are one spelling'
    assert unicodedata.is_normalized('NFC', composed), composed
    assert unicodedata.is_normalized('NFD', decomposed), decomposed
    store = _load_store()
    _require_a_discriminating_pair(store, composed)
    assert (store.seg_lock_for(composed)
            is store.seg_lock_for(decomposed)), (
                'the two spellings of one name took two stripes')
    assert (store.seg_lock_for(f'{composed}.json')
            is store.seg_lock_for(f'{decomposed}.JSON')), (
                'the spellings split the record chain')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='seglockkey_')


if __name__ == '__main__':
    raise SystemExit(main())
