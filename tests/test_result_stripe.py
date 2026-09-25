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
import errno
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bridge import BRIDGE_ENV, TOK  # noqa: E402
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
        if os.fsdecode(path) == os.fsdecode(target) and not fired:
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
        # `is_dir()` is a stat, from inside the wrapper. That is the order
        # this fixture exists to read, so it has to be asked here; a recorder
        # that took a second filesystem reading for its own convenience would
        # move the moment it is recording. Anything else recorded here has to
        # be state the wrapper already has.
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


def _stat_failure_routes(routes, tmp, errno, exc):
    """Drive a stat failure that is not absence through all three routes.

    The three routes owe three different answers to the same event, so they
    are driven together: a POST has work to refuse, and a read has none. The
    failing stat is injected at `os.stat`, which is the boundary the program
    asks its question at, and the injection is asserted to have fired.
    """
    res_dir, cmd_dir = _roots(tmp)
    # The accepted-delivery record is process-wide, so each case needs its own
    # delivery id or the second case's setup POST reads as a duplicate.
    token, did = f'statfail{errno}tok', f'1700000000000_s{errno}'
    stored = routes.accept_result(
        res_dir, cmd_dir, token,
        {'tabId': 'tab', 'id': 'one', '_did': did}, DELIVERY_CAP)
    delivery_file = _delivery(res_dir, token, 'tab', did)
    assert stored == (200, {'ok': True}), stored
    real_stat = os.stat
    fired = []

    def failing_stat(path, *args, **kwargs):
        # Every delivery target is one level under the deliveries root, so
        # that is what the injection has to answer for: the POST's own new
        # target, the read's, and the broadcast directory the compat path
        # falls back to.
        if os.fsdecode(path).split(os.sep)[-2:-1] == ['deliveries']:
            fired.append(os.fsdecode(path))
            raise exc(errno, 'injected: the parent will not answer')
        return real_stat(path, *args, **kwargs)

    os.stat = failing_stat
    try:
        posted = routes.accept_result(
            res_dir, cmd_dir, token,
            {'tabId': 'other', 'id': 'two',
             '_did': f'1700000000001_s{errno}'},
            DELIVERY_CAP)
        read = routes.fetch_result(
            res_dir, token, {'tab': ['tab'], 'delivery': [did],
                             'consume': ['1']})
        kept = routes.fetch_result(
            res_dir, token, {'tab': ['tab'], 'consume': ['1']})
    finally:
        os.stat = real_stat
    assert fired, 'the injection never fired'
    return posted, read, kept, delivery_file


def test_a_stat_failure_is_answered_not_raised_on_every_route(tmp):
    """A stat that fails for any reason is a 500, a pending and a pending.

    `except OSError: return None` in the key function cannot tell a stat that
    failed from a stat that found nothing, and the three routes must live
    with that: a POST has just created the directory, so for it the same
    answer means a storage failure and a 500, and a read has nothing to do
    either way, so `pending` is the honest one. Pinned for three error
    numbers, because a permission error, a path component that is not a
    directory and a symlink loop are all reachable and none of them is
    absence.
    """
    cases = [(errno.EACCES, PermissionError),
             (errno.ENOTDIR, NotADirectoryError),
             (errno.ELOOP, OSError)]
    for index, (number, exc) in enumerate(cases):
        routes = _load(f'stripe_stat_failure_{number}')
        res_dir = Path(tmp) / f'case{index}'
        res_dir.mkdir()
        posted, read, kept, delivery_file = _stat_failure_routes(
            routes, res_dir, number, exc)
        assert posted == (500, {'error': 'result storage failure'}), (
            number, posted)
        assert read == (200, {'pending': True}), (number, read)
        assert kept[0] == 200, (number, kept)
        # The read was asked to consume and did not, so the delivery is
        # still there -- the failure refused the work rather than half
        # doing it.
        assert delivery_file.exists(), number
        assert not _slot(res_dir, f'statfail{number}tok', 'tab').exists() or (
            kept[0] == 200), number


# A stat that fails for any reason other than absence, inside the real
# bridge process: every delivery target is one level under the deliveries
# root, so that is what the patch refuses. Injected at `os.stat`, which is
# the boundary the key function asks its question at.
_STAT_FAILURE_PATCH = """
import os

_real_stat = os.stat


def _failing_stat(path, *args, **kwargs):
    # Only a path is rewritten: `os.scandir` entries are stat'd against a
    # directory descriptor, which is an int and has no spelling to refuse.
    if isinstance(path, (str, bytes, os.PathLike)):
        text = os.fspath(path)
        if os.sep + 'deliveries' + os.sep in text + os.sep:
            raise PermissionError(13, 'injected: the parent will not answer')
    return _real_stat(path, *args, **kwargs)


os.stat = _failing_stat
"""


def test_a_no_tab_read_under_a_stat_failure_answers_pending(tmp):
    """The read with no tab, which has to look for the file to be told.

    A `delivery=` read that names no tab asks whether the file is there
    before it scans, and `Path.exists()` re-raises a stat that failed for a
    reason other than absence -- a permission error among the three this
    repository can raise on purpose. The scan's own probe has the same shape.
    Both are now answered as "not there", which is what a read can say
    without one: the delivery is still there, and the next read that can stat
    will find it. Before, the exception left `do_GET` and the client with a
    closed connection -- measured against the real handler on EACCES, with
    ENOTDIR and ELOOP already absorbed by `Path.exists()`'s own list.
    """
    routes = _load('stripe_stat_failure_no_tab')
    res_dir, cmd_dir = _roots(tmp)
    token, did = 'notabstatok', '1700000000000_n'
    stored = routes.accept_result(
        res_dir, cmd_dir, token,
        {'tabId': 'tab', 'id': 'one', '_did': did}, DELIVERY_CAP)
    delivery_file = _delivery(res_dir, token, 'tab', did)
    assert stored == (200, {'ok': True}), stored
    assert delivery_file.is_file(), delivery_file
    real_stat = os.stat
    fired = []

    def failing_stat(path, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)):
            text = os.fsdecode(path)
            if os.sep + 'deliveries' + os.sep in text + os.sep:
                fired.append(text)
                raise PermissionError(errno.EACCES, 'injected: no answer')
        return real_stat(path, *args, **kwargs)

    os.stat = failing_stat
    try:
        # The first probe is the direct one; the rest come from the scan the
        # no-tab read falls into once the direct probe says "not there".
        first = routes.fetch_result(res_dir, token, {'delivery': [did]})
        second = routes.fetch_result(
            res_dir, token, {'delivery': [did], 'consume': ['1']})
    finally:
        os.stat = real_stat
    assert fired, 'the injection never fired'
    assert first == (200, {'pending': True}), first
    assert second == (200, {'pending': True}), second
    assert delivery_file.exists(), 'a refused read still consumed'


def test_a_stat_failure_answers_a_client_instead_of_dropping_it(tmp):
    """The POST owes a client a response even when the stripe is refused.

    The route-level fixtures above call `accept_result` directly. This one
    goes through the real `BaseHTTPRequestHandler` the bridge derives from,
    because the difference the route-level call cannot see is exactly the one
    that matters: an exception the route does not catch escapes `do_POST`,
    which has no catch-all, and the client gets a closed connection instead
    of the 500 the contract promises. `_util.post_json` raises on a dropped
    connection, so this fixture cannot pass by accident.
    """
    patch_dir = Path(tmp) / 'patch'
    patch_dir.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        _STAT_FAILURE_PATCH, encoding='utf-8')
    env = {**BRIDGE_ENV, 'PYTHONPATH': str(patch_dir)}
    with _util.bridge(tmp, env=env) as (base, _docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'tab', 'id': 'one', 'result': 'x',
            'error': None, 'ts': 1, '_did': 'stat-failure-1'})
    assert status == 500, status
    assert body == {'error': 'result storage failure'}, body


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='resultstripe_')


if __name__ == '__main__':
    raise SystemExit(main())
