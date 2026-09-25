#!/usr/bin/env python3
"""Delivery stripes stay stable while target names cannot steer them."""
import itertools
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _case_fold  # noqa: E402
import _util  # noqa: E402


_SERVER = None


def _load_stripes(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'delivery_stripes.py', name=name)


def _load_result_store(tmp):
    global _SERVER
    if _SERVER is not None:
        return _SERVER
    saved = {name: os.environ.get(name) for name in (
        'DAEDALUS_DIR', 'DAEDALUS_PORT', 'DAEDALUS_MCP_PORT', 'TOKEN',
        'DAEDALUS_TOKEN')}
    os.environ.update({
        'DAEDALUS_DIR': str(tmp), 'DAEDALUS_PORT': '0',
        'DAEDALUS_MCP_PORT': '0', 'TOKEN': '',
        'DAEDALUS_TOKEN': 'stripe-token'})
    sys.path.insert(0, str(_util.ROOT))
    try:
        _SERVER = _util.load(
            _util.ROOT / 'daedalus_bridge' / 'result_store.py',
            name='delivery_stripes_store')
    finally:
        sys.path.pop(0)
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return _SERVER


def _crc_collisions(count):
    target = zlib.crc32(b'crc-collision-seed') & 63
    names = []
    for number in itertools.count():
        name = f'crc-collision-{number:06d}'
        if zlib.crc32(name.encode()) & 63 == target:
            names.append(name)
            if len(names) == count:
                return names
    raise AssertionError('unreachable')


def _names(count):
    return [f'candidate-{number:06d}' for number in range(count)]


def test_same_directory_is_stable_and_server_reuses_lock(tmp):
    stripes = _load_stripes('delivery_stripes_stable')
    key = os.fsencode('stripe-token_tab-01')
    assert stripes.stripe_index(key, 64) == stripes.stripe_index(key, 64)

    result_store = _load_result_store(tmp)
    target = Path(tmp) / 'results' / 'deliveries' / 'stripe-token_tab-01'
    target.mkdir(parents=True)
    assert (result_store.delivery_lock_for(target)
            is result_store.delivery_lock_for(target))


def test_two_names_for_one_entry_take_one_stripe(tmp):
    """One entry is one stripe, whichever path names it.

    The property the stripe exists on, pinned where a case-insensitive
    parent is not available: two paths that reach the same entry take the
    same lock. A symlink is the case-sensitive host's own way of putting two
    names on one entry, and it is the shape the fold fixtures reach by
    another route -- `os.stat` follows it, as it follows a parent's case
    folding, so the entry is what both names reach.
    """
    result_store = _load_result_store(tmp)
    deliveries = Path(tmp) / 'results' / 'deliveries'
    real = deliveries / 'stripe-token_real'
    real.mkdir(parents=True)
    alias = deliveries / 'stripe-token_alias'
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError) as why:
        _util.skip(f'this filesystem will not hold a symlink: {why}')
    assert (result_store.delivery_lock_for(real)
            is result_store.delivery_lock_for(alias))


def test_an_entry_reporting_no_inode_falls_back_to_its_name(tmp):
    """A parent that reports no inode number keys that entry on its name.

    Every target would key on `b'0:0'` if the zero went into the identity
    instead of the fallback, and the whole bridge would serialise its
    delivery writes behind one stripe. The fallback is the pre-round
    behaviour for such a filesystem, so this pins the branch that keeps it:
    the name is the key, and it is the name of that entry and not of every
    other one.
    """
    result_store = _load_result_store(tmp)
    deliveries = Path(tmp) / 'results' / 'deliveries'
    first = deliveries / 'stripe-token_first'
    second = deliveries / 'stripe-token_second'
    first.mkdir(parents=True)
    second.mkdir()
    real_stat = os.stat

    class _NoInode:
        """A stat that reports a device and no inode number."""

        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        @property
        def st_ino(self):
            return 0

    os.stat = lambda path, *a, **k: _NoInode(real_stat(path, *a, **k))
    try:
        keyed = result_store.delivery_stripe_key(first)
        other = result_store.delivery_stripe_key(second)
    finally:
        os.stat = real_stat
    assert keyed == b'stripe-token_first', keyed
    assert other == b'stripe-token_second', other


def test_two_entries_differing_only_in_case_keep_two_keys(tmp):

    """The control: on a case-sensitive parent they are two entries.

    The same two names with the host's own filesystem, which keeps them
    apart: two inodes, two keys, and no merging of two tabs' results. The
    folded verdict above and this one together are what make the difference
    the parent's rather than the assertion's.
    """
    _case_fold.require_case_sensitive(
        Path(tmp), 'two entries differing only in case keep two keys',
        'test_case_fold_parent.test_a_folded_target_is_one_directory_and_'
        'one_stripe')
    result_store = _load_result_store(tmp)
    deliveries = Path(tmp) / 'results' / 'deliveries'
    upper = deliveries / 'stripe-token_Foo'
    lower = deliveries / 'stripe-token_foo'
    upper.mkdir(parents=True)
    lower.mkdir(parents=True)
    assert (result_store.delivery_stripe_key(upper)
            != result_store.delivery_stripe_key(lower))
    assert os.stat(upper).st_ino != os.stat(lower).st_ino


def test_the_store_selects_through_the_seeded_mapping(tmp):
    """The store asks the keyed mapping, and takes the lock it names.

    The per-process secret in `delivery_stripes` exists so a caller cannot
    compute or steer which targets share a stripe, and that is a statement
    about the *wiring* as much as about the mapping: a store that hashed its
    own key with something public would satisfy every property the mapping
    has on its own and none of the acceptance. This is the pin that says
    which of the two the store does -- the store's key goes to
    `stripe_index`, and the lock it hands back is the one that mapping chose
    for that key.
    """
    result_store = _load_result_store(tmp)
    target = Path(tmp) / 'results' / 'deliveries' / 'stripe-token_wiring'
    target.mkdir(parents=True)
    asked = []
    real_stripe_index = result_store.stripe_index

    def recording_stripe_index(key, stripes):
        index = real_stripe_index(key, stripes)
        asked.append((key, index, stripes))
        return index

    setattr(result_store, 'stripe_index', recording_stripe_index)
    try:
        lock = result_store.delivery_lock_for(target)
    finally:
        setattr(result_store, 'stripe_index', real_stripe_index)
    assert asked, 'the store did not select through the keyed mapping'
    key, index, stripes = asked[0]
    assert key == result_store.delivery_stripe_key(target), (key, target)
    assert stripes == result_store.DELIVERY_LOCK_STRIPES, stripes
    assert lock is result_store.delivery_locks[index], (index, lock)


def test_crc_colliding_names_do_not_share_a_stripe_at_the_wiring(tmp):
    """A public hash of the name would put a colliding set on one stripe.

    This is the property the old pin had, re-expressible: a real target's key
    is its entry, so a set of *names* sharing a CRC32 bucket only reaches
    the selector through the one path that keys on a name -- a filesystem that
    reports no inode number, which the fallback exists for. Under that
    fallback the store must still refuse to put 128 names the keyed mapping
    separates onto one stripe, which is the observable effect of the
    per-process secret at the wiring. A raw `crc32` in `delivery_lock_for`
    answers this the other way: the names share a bucket by construction, so
    they all land on one lock.
    """
    result_store = _load_result_store(tmp)
    deliveries = Path(tmp) / 'results' / 'deliveries'
    deliveries.mkdir(parents=True)
    names = _crc_collisions(128)
    for name in names:
        (deliveries / name).mkdir()
    real_stat = os.stat

    class _NoInode:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        @property
        def st_ino(self):
            return 0

    os.stat = lambda path, *a, **k: _NoInode(real_stat(path, *a, **k))
    try:
        locks = [
            result_store.delivery_lock_for(deliveries / name)
            for name in names
        ]
    finally:
        os.stat = real_stat
    assert all(lock is not None for lock in locks), 'a live target had no lock'
    assert any(lock is not locks[0] for lock in locks[1:]), (
        'every CRC-colliding name took one stripe, so the mapping is public')


def test_server_wiring_spreads_real_targets_across_the_table(tmp):
    """The store draws its locks from the keyed table, not from one lock.

    This used to pin that 128 names sharing a CRC32 bucket do not share a
    lock, which was the keyed mapping's defence against a steerable
    collision. A real target's key is its directory's entry, not its name,
    so the CRC bucket describes no real target any more; the CRC-collision
    property is `delivery_stripes`' own, pinned there. What is left for the
    wiring to prove is that it selects through the keyed table at all, and
    with 128 real directories spread over 64 stripes an accidental
    one-stripe result is impossible in practice.
    """
    result_store = _load_result_store(tmp)
    deliveries = Path(tmp) / 'results' / 'deliveries'
    deliveries.mkdir(parents=True)
    locks = []
    for number in range(128):
        target = deliveries / f'stripe-token_tab-{number:06d}'
        target.mkdir()
        locks.append(result_store.delivery_lock_for(target))
    assert all(lock is not None for lock in locks), 'a live target had no lock'
    assert any(lock is not locks[0] for lock in locks[1:]), (
        'every target took one stripe, so the table is not in use')


def test_crc_collisions_do_not_collide_under_keyed_mapping(tmp):
    del tmp
    stripes = _load_stripes('delivery_stripes_crc_bucket')
    names = _crc_collisions(128)
    live = {stripes.stripe_index(os.fsencode(name), 64) for name in names}
    # With 64 stripes and 100+ names, an accidental one-stripe result is
    # impossible in practice; the keyed seed must break the public CRC bucket.
    assert len(live) > 1


def test_reported_crc_collisions_share_one_crc32_stripe(tmp):
    del tmp
    names = ['steer-0006', 'steer-0078', 'steer-0120',
             'steer-0224', 'steer-0302']
    crc = {zlib.crc32(name.encode()) & 63 for name in names}
    # Keyed dispersion is pinned by the generated 128-name tests, avoiding a
    # probabilistic assertion for this five-name reported set.
    assert len(crc) == 1


def test_independently_loaded_modules_have_independent_seeds(tmp):
    del tmp
    first = _load_stripes('delivery_stripes_seed_one')
    second = _load_stripes('delivery_stripes_seed_two')
    names = _names(128)
    first_indices = [first.stripe_index(os.fsencode(name), 64)
                     for name in names]
    second_indices = [second.stripe_index(os.fsencode(name), 64)
                      for name in names]
    assert any(left != right
               for left, right in zip(first_indices, second_indices))


def test_mapping_differs_from_crc32(tmp):
    del tmp
    stripes = _load_stripes('delivery_stripes_not_crc')
    names = _names(128)
    assert any(
        stripes.stripe_index(os.fsencode(name), 64)
        != zlib.crc32(name.encode()) & 63
        for name in names)


_STRIPE_PROBE = r"""
import json
import os
import sys
from pathlib import Path

from daedalus_bridge import result_store

root = Path(sys.argv[1]) / 'deliveries'
root.mkdir(parents=True)
(root / 'tok_real').mkdir()

# The stripe is keyed on the entry, so the key is not the name: two targets
# whose names differ must not key alike, and a target whose name happens to
# collide with another's must not either.
present = result_store.delivery_stripe_key(root / 'tok_real')
absent = result_store.delivery_stripe_key(root / 'tok_absent')
same_lock = (result_store.delivery_lock_for(root / 'tok_real')
             is result_store.delivery_lock_for(root / 'tok_real'))
# The present key is the entry's identity, so it carries the device and the
# inode rather than the name a caller would have spelled.
identity_form = b':' in present
# A target that is not there has no entry to stripe on at all. Naming a
# stripe for a directory that is not there is the mistake this issue is
# about: a writer creates the entry first and then keys on the entry, so it
# would never take the name's stripe.
absent_refused = absent is None and result_store.delivery_lock_for(
    root / 'tok_absent') is None

# The mistake that shipped as the original bug was a caller handing the
# selector a bare name, and a name is silently resolvable against the
# process working directory -- it would take a stripe for a directory nobody
# in the request ever named. Refused instead. Both spellings are asserted:
# the one that actually shipped was a str, so refusing only path OBJECTS
# would leave the original bug uncaught.
refused_name = False
try:
    result_store.delivery_lock_for('tok_real')
except TypeError:
    refused_name = True
refused_path = False
try:
    result_store.delivery_lock_for(os.path.join(str(root), 'tok_real'))
except TypeError:
    refused_path = True

print('STRIPE ' + json.dumps({
    'same_lock': same_lock,
    'absent_refused': absent_refused,
    'present_is_not_the_name': present != b'tok_real',
    'present_is_an_identity': identity_form,
    'refused_bare_name': refused_name,
    'refused_str_path': refused_path,
}))
"""


def test_the_selector_key_is_the_entry_and_takes_no_name(tmp):
    """The stripe is the entry's, asked of the filesystem, not spelled.

    `result_store.delivery_lock_for` takes a delivery directory and keys on
    what the filesystem reports about it, so the property it can promise is
    the one an identity can: one entry, one stripe, and an entry nobody can
    merge with another by spelling its name differently. It cannot promise
    that the key is a name at all -- on a case-insensitive parent two names
    reach one entry, and the only string both callers agree on is the
    entry's own identity, which is what this selector now asks for. The
    folded pair itself is pinned where a folding parent is available
    (`test_case_fold_parent`'s
     `test_a_folded_target_is_one_directory_and_one_stripe`)
    and the two-names-one-entry case on any host
    (`test_delivery_stripes.test_two_names_for_one_entry_take_one_stripe`).

    A directory is what it takes, and a bare string is refused: a string is
    silently resolvable against the working directory, so a caller that
    passed a name would take a stripe for a directory nobody in the request
    named -- the shape that shipped as the original bug. The absent-target
    half is the same question: a stripe for an entry, and not for a name.
    """
    docroot = Path(tmp) / 'docroot'
    docroot.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(docroot),
        'DAEDALUS_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    proc = subprocess.run(
        [sys.executable, '-c', _STRIPE_PROBE, str(docroot)],
        cwd=_util.ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    marked = [line for line in proc.stdout.splitlines()
              if line.startswith('STRIPE ')]
    assert len(marked) == 1, (proc.stdout, proc.stderr)
    answer = json.loads(marked[0][len('STRIPE '):])
    assert answer['same_lock'] is True, answer
    assert answer['absent_refused'] is True, answer
    assert answer['present_is_not_the_name'] is True, answer
    assert answer['present_is_an_identity'] is True, answer
    assert answer['refused_bare_name'] is True, answer
    assert answer['refused_str_path'] is True, answer


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='deliverystripes_')


if __name__ == '__main__':
    raise SystemExit(main())
