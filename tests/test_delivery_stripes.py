#!/usr/bin/env python3
"""Delivery stripes stay stable while target names cannot steer them."""
import itertools
import os
import sys
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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


def test_two_entries_differing_only_in_case_keep_two_keys(tmp):
    """The control: on a case-sensitive parent they are two entries.

    The same two names with the host's own filesystem, which keeps them
    apart: two inodes, two keys, and no merging of two tabs' results. The
    folded verdict above and this one together are what make the difference
    the parent's rather than the assertion's.
    """
    result_store = _load_result_store(tmp)
    deliveries = Path(tmp) / 'results' / 'deliveries'
    upper = deliveries / 'stripe-token_Foo'
    lower = deliveries / 'stripe-token_foo'
    upper.mkdir(parents=True)
    lower.mkdir(parents=True)
    assert (result_store.delivery_stripe_key(upper)
            != result_store.delivery_stripe_key(lower))
    assert os.stat(upper).st_ino != os.stat(lower).st_ino


def test_server_wiring_rejects_crc_collisions(tmp):
    result_store = _load_result_store(tmp)
    token = 'stripe-token'
    target = zlib.crc32(b'crc-collision-seed') & 63
    tabs = []
    for number in itertools.count():
        tab = f'crc-collision-{number:06d}'
        key = result_store.result_key(token, tab)
        if zlib.crc32(key.encode()) & 63 == target:
            tabs.append(tab)
            if len(tabs) == 128:
                break

    crc_stripes = {
        zlib.crc32(result_store.result_key(token, tab).encode()) & 63
        for tab in tabs
    }
    assert len(crc_stripes) == 1
    # These targets do not exist, so each is striped on the name it would be
    # created under; with 64 stripes and 128 names an accidental one-stripe
    # result is impossible in practice, so the keyed mapping is what is
    # being measured.
    locks = [
        result_store.delivery_lock_for(
            result_store.result_key(token, tab))
        for tab in tabs
    ]
    assert any(lock is not locks[0] for lock in locks[1:])


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


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='deliverystripes_')


if __name__ == '__main__':
    raise SystemExit(main())
