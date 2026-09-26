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


def _require_a_distinct_chain_root(store, name):
    """Fail unless some name has a chain root different from `name`'s.

    The chain-root twin of `_require_a_discriminating_pair`, for the same
    reason on the pre-hash key: "these two roots are equal" says nothing
    unless equal roots are the normal case and unequal ones are not.
    """
    for attempt in range(256):
        other = f'unrelated-{attempt}'
        if store._job_chain_root(name) != store._job_chain_root(other):
            return
    raise AssertionError(
        f'no unrelated name landed on a different chain root from {name!r}')


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
    # One pair per assertion, because one pair does not cover both. The
    # second assertion compares `Foo.json` with `foo.JSON`, and a key that
    # casefolds but strips the affix case-sensitively sends `foo.JSON` down
    # a different chain root — which lands on a different stripe about one
    # time in sixty-four, so the assertion passes a broken fold. The pair
    # that protects the first assertion says nothing about the second.
    _require_a_discriminating_pair(store, 'Foo')
    assert (store.seg_lock_for('Foo')
            is store.seg_lock_for('foo')), 'case spellings took two stripes'
    _require_a_discriminating_pair(store, 'Foo.json')
    assert (store.seg_lock_for('Foo.json')
            is store.seg_lock_for('foo.JSON')), (
                'case spellings split the record chain')
    # Both, and the second is the sound one. Two names agreeing on a STRIPE
    # can coincide by hash — with the casefold removed they land on different
    # roots and then share a stripe about once in sixty-four, which is the
    # escape this control was measured missing. Two names agreeing on a CHAIN
    # ROOT is exact, and the root is what the stripe is derived from, so
    # there is nothing left to coincide. The stripe assertions stay because
    # they are what the callers observe; the root assertions are what cannot
    # pass by accident.
    _require_a_distinct_chain_root(store, 'Foo')
    assert store._job_chain_root('Foo') == store._job_chain_root('foo')
    _require_a_distinct_chain_root(store, 'Foo.json')
    assert (store._job_chain_root('Foo.json')
            == store._job_chain_root('foo.JSON'))


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
    # The same two assertions with the same two pairs, for the same reason:
    # the chain assertion is its own coin-flip and needs its own partner.
    _require_a_discriminating_pair(store, composed)
    assert (store.seg_lock_for(composed)
            is store.seg_lock_for(decomposed)), (
                'the two spellings of one name took two stripes')
    _require_a_discriminating_pair(store, f'{composed}.json')
    assert (store.seg_lock_for(f'{composed}.json')
            is store.seg_lock_for(f'{decomposed}.JSON')), (
                'the spellings split the record chain')
    # The same two assertions a second time, on the chain root rather than
    # the stripe, for the same reason as the case control: a pair of names on
    # the same stripe can coincide by hash, and one on the same chain root
    # cannot.
    _require_a_distinct_chain_root(store, composed)
    assert store._job_chain_root(composed) == store._job_chain_root(
        decomposed)
    _require_a_distinct_chain_root(store, f'{composed}.json')
    assert (store._job_chain_root(f'{composed}.json')
            == store._job_chain_root(f'{decomposed}.JSON'))


def test_the_chain_root_is_a_fixpoint(tmp):
    """Folding the root again changes nothing, over adversarial names.

    `_job_chain_root` normalises, casefolds, strips the record affix and
    normalises once more. This pins the property that makes the last of
    those redundant rather than load-bearing: the first normalise leaves a
    string that is already decomposed and lowercased, and the strip only
    removes a trailing ASCII affix from it, so no adjacency is created for a
    second normalise to collapse. If that ever stopped being true — a strip
    that removed a prefix, or a normalisation that composed — the root
    would stop being a fixpoint and this fails.
    """
    store = _load_store()
    names = [f'relay{i}' for i in range(2000)]
    names += ['a.b.c', '', '.', '..json', 'json', '.json', 'A.JSON',
              'straße', 'ﬁle', 'x' * 200, 'relay' + chr(0x301), 'ＦＯＯ',
              'ǅungla', 'İstanbul', 'ǰ.json', 'e' + chr(0x301) + '.json']
    for name in names:
        root = store._job_chain_root(name)
        again = unicodedata.normalize('NFKD', root).casefold()
        assert root == again, (name, root, again)
        # And the fold does not depend on how the name was spelled.
        assert store.seg_lock_for(name) is store.seg_lock_for(root), name


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='seglockkey_')


if __name__ == '__main__':
    raise SystemExit(main())
