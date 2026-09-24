#!/usr/bin/env python3
"""The delivery stripe's contract: an entry, or nothing to lock on.

`result_store.delivery_lock_for` is keyed on the target directory's own
entry, and there are three states that question can be in -- the entry
exists, the entry does not exist, and the entry exists on a filesystem that
reports no inode number -- plus the two rules the routes owe each of them.
They are here rather than in `test_result_routes.py` because together they
are one contract, and because the routes suite is at the repository's
700-line test ceiling.

The properties are host-independent by construction: every fixture here
runs on a case-sensitive filesystem, and the folding-parent half of the same
contract is pinned against a real case-folding parent in
`test_result_routes.test_a_folded_target_is_one_directory_and_one_stripe`.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

# Above any delivery count these fixtures store, so eviction never fires.
DELIVERY_CAP = 8


def _load(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'result_routes.py', name)


def _roots(tmp):
    """A results root and a commands root of this test's own."""
    res_dir = Path(tmp) / 'results'
    cmd_dir = Path(tmp) / 'commands'
    res_dir.mkdir(parents=True, exist_ok=True)
    cmd_dir.mkdir(parents=True, exist_ok=True)
    return res_dir, cmd_dir


def _slot(res_dir, token, tab=''):
    return res_dir / (f'{token}_{tab}.json' if tab else f'{token}.json')


def _delivery(res_dir, token, tab, did):
    return res_dir / 'deliveries' / f'{token}_{tab}' / f'{did}.json'


def test_a_consume_never_takes_a_stripe_the_writer_will_not(tmp):
    """A consume that finds no entry must not touch the delivery files.

    The transition a folding parent is not needed to reach: a consume for a
    target whose directory does not exist yet, arriving while a POST is
    publishing into it. A stripe keyed on the name of a directory that is
    not there is a stripe the writer will never take -- it creates the entry
    first, and then keys on the entry -- so the two would be inside their
    critical sections at once, on different locks, and the consume would take
    the delivery the POST reported as stored. With the `_did` already in the
    accepted-delivery record, the extension's retry is answered
    `duplicate: True` and never re-publishes.

    The interleaving is forced rather than raced: the reader's own `stat` of
    the directory is made to report the entry absent, and the writer runs to
    completion inside that call. That is the ordering the reader cannot
    detect, so the only correct answer is not to write at all -- and the
    injection marker is asserted, because a green that never fired the
    injection would prove nothing.
    """
    routes = _load('stripe_transition_consume')
    res_dir, cmd_dir = _roots(tmp)
    token, did = 'transitiontok', '1700000000000_t'
    target = res_dir / 'deliveries' / f'{token}_tab'
    delivery_file = _delivery(res_dir, token, 'tab', did)
    assert not target.exists(), 'the fixture needs a target that is not there'
    real_stat = os.stat
    fired = {}
    published = []

    def absent_then_publish(path, *args, **kwargs):
        """The reader's stat, before the writer that makes it true."""
        if os.fspath(path) == os.fspath(target) and not fired:
            fired['reader'] = True
            published.append(routes.accept_result(
                res_dir, cmd_dir, token,
                {'tabId': 'tab', 'id': 'one', '_did': did}, DELIVERY_CAP))
            raise FileNotFoundError(2, 'injected: the entry is not there yet')
        return real_stat(path, *args, **kwargs)

    os.stat = absent_then_publish
    try:
        answer = routes.fetch_result(
            res_dir, token, {'tab': ['tab'], 'delivery': [did],
                             'consume': ['1']})
    finally:
        os.stat = real_stat
    assert fired.get('reader'), 'the injection never fired'
    assert published == [(200, {'ok': True})], published
    # The POST said it stored this, so the file has to still be there for
    # the consumer that is entitled to it.
    assert delivery_file.exists(), 'a consume took a stored delivery'
    assert answer == (200, {'pending': True}), answer
    # And the retry of that _did reads the delivery, because it is still
    # there to read.
    retry = routes.fetch_result(
        res_dir, token, {'tab': ['tab'], 'delivery': [did]})
    assert retry[0] == 200 and retry[1].get('id') == 'one', retry


def test_a_compat_consume_of_an_absent_delivery_takes_no_stripe(tmp):
    """The same refusal on the path that finds its own owner by scanning.

    A consume with no tab finds the owner by scanning the delivery
    directories, and it can find nothing: the delivery has been evicted, or
    the process that stored it ran elsewhere, and the slot still names it.
    There is then no entry to stripe on, and the slot consume -- which
    carries its own generation check and runs under the result lock -- is the
    whole of the work left. This is the second read path, and the one whose
    refusal branch is otherwise reachable from no fixture at all.
    """
    routes = _load('stripe_transition_absent_compat')
    store = routes.result_store
    res_dir, cmd_dir = _roots(tmp)
    token, did = 'absentcompatok', '1700000000000_ac'
    stored = routes.accept_result(
        res_dir, cmd_dir, token,
        {'tabId': 'tab', 'id': 'one', '_did': did}, DELIVERY_CAP)
    delivery_file = _delivery(res_dir, token, 'tab', did)
    assert delivery_file.is_file(), delivery_file
    stripes = []
    real_lock_for = store.delivery_lock_for

    def recording_lock_for(target_dir):
        lock = real_lock_for(target_dir)
        stripes.append((os.path.basename(os.fspath(target_dir)),
                        'refused' if lock is None else id(lock)))
        return lock

    store.delivery_lock_for = recording_lock_for
    try:
        delivery_file.unlink()
        answer = routes.fetch_result(res_dir, token, {'consume': ['1']})
    finally:
        store.delivery_lock_for = real_lock_for
    assert stored == (200, {'ok': True}), stored
    assert answer[0] == 200, answer
    # A consume with no tab consumes the token's own slot -- that is what the
    # caller asked for -- and the delivery copy it named is not there to be
    # touched.
    assert not _slot(res_dir, token).exists(), 'the slot was not consumed'
    assert not delivery_file.exists()
    # The scan found no owner, so the directory it hands the stripe is the
    # token's own broadcast target -- also not there, also refused.
    assert stripes == [(token, 'refused')], stripes


def test_the_post_creates_the_directory_before_it_asks_for_the_stripe(tmp):
    """The ordering, recorded rather than inferred from a consequence.

    `accept_result` has to create the target directory before it asks for
    the stripe: there is no stripe for a directory that is not there, so a
    POST that asked first would be refused one and the first delivery for a
    target would have no mutual exclusion against the second. On a
    case-sensitive parent the consequences of the wrong order are not
    observable, so this records the order itself: the directory did not exist
    before the POST, and it existed at the moment the key was taken.
    """
    routes = _load('stripe_transition_post_ordering')
    store = routes.result_store
    res_dir, cmd_dir = _roots(tmp)
    token, did = 'ordertok', '1700000000000_o'
    target = res_dir / 'deliveries' / f'{token}_tab'
    assert not target.exists(), 'the fixture needs a target that is not there'
    real_lock_for = store.delivery_lock_for
    seen = []

    def recording_lock_for(target_dir):
        existed = target_dir.is_dir()
        seen.append((os.fspath(target_dir), existed))
        return real_lock_for(target_dir)

    store.delivery_lock_for = recording_lock_for
    try:
        answer = routes.accept_result(
            res_dir, cmd_dir, token,
            {'tabId': 'tab', 'id': 'one', '_did': did}, DELIVERY_CAP)
    finally:
        store.delivery_lock_for = real_lock_for
    assert answer == (200, {'ok': True}), answer
    assert len(seen) == 1, seen
    named, existed = seen[0]
    assert named == str(target), (named, target)
    assert existed, 'the stripe was asked for before the directory existed'
    assert target.is_dir()


def test_the_post_hands_the_stripe_a_directory_not_a_name(tmp):
    """What it hands the selector is the directory, on any filesystem.

    The stripe is keyed on the directory, so the argument has to be the
    directory and not a name a route derived from it. This is the call shape
    a case-sensitive host can see; the folded pair is pinned against a real
    case-folding parent in `test_result_routes`.
    """
    routes = _load('stripe_transition_call_shape')
    store = routes.result_store
    res_dir, cmd_dir = _roots(tmp)
    token = 'shapetok'
    seen = []
    real_lock_for = store.delivery_lock_for

    def recording_lock_for(target_dir):
        seen.append(target_dir)
        return real_lock_for(target_dir)

    store.delivery_lock_for = recording_lock_for
    try:
        stored = routes.accept_result(
            res_dir, cmd_dir, token,
            {'tabId': 'shapetab', 'id': 'one', '_did': '1700000000000_c'},
            DELIVERY_CAP)
    finally:
        store.delivery_lock_for = real_lock_for
    assert stored == (200, {'ok': True}), stored
    assert len(seen) == 1, seen
    assert isinstance(seen[0], Path), seen
    assert seen[0].name == f'{token}_shapetab', seen
    assert seen[0].is_dir(), seen
    keys = {store.delivery_stripe_key(seen[0])}
    assert len(keys) == 1 and None not in keys, keys


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='resultstripe_')


if __name__ == '__main__':
    raise SystemExit(main())
