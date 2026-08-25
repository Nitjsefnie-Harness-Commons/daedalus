#!/usr/bin/env python3
"""Delivery stripes stay stable while target names cannot steer them."""
import itertools
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402


_SERVER = None


def _load_stripes(name):
    return _util.load(_util.ROOT / 'delivery_stripes.py', name=name)


def _load_server(tmp):
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
            _util.ROOT / 'server.py', name='delivery_stripes_server')
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


def test_same_key_is_stable_and_server_reuses_lock(tmp):
    stripes = _load_stripes('delivery_stripes_stable')
    key = os.fsencode('stripe-token_tab-01')
    assert stripes.stripe_index(key, 64) == stripes.stripe_index(key, 64)

    server = _load_server(tmp)
    target_key = server._result_key('stripe-token', 'tab-01')
    assert (server._delivery_lock_for(target_key)
            is server._delivery_lock_for(target_key))


def test_server_wiring_rejects_crc_collisions(tmp):
    server = _load_server(tmp)
    token = 'stripe-token'
    target = zlib.crc32(b'crc-collision-seed') & 63
    tabs = []
    for number in itertools.count():
        tab = f'crc-collision-{number:06d}'
        key = server._result_key(token, tab)
        if zlib.crc32(key.encode()) & 63 == target:
            tabs.append(tab)
            if len(tabs) == 128:
                break

    crc_stripes = {
        zlib.crc32(server._result_key(token, tab).encode()) & 63
        for tab in tabs
    }
    assert len(crc_stripes) == 1
    locks = [
        server._delivery_lock_for(server._result_key(token, tab))
        for tab in tabs
    ]
    # With 64 stripes and 128 names, an accidental one-stripe result is
    # impossible in practice; the server must use the keyed mapping.
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
