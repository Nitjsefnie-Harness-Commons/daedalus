#!/usr/bin/env python3
"""Delivery result lock stripes and discovered-owner serialization."""
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import BRIDGE_ENV, TOK  # noqa: E402
from test_bridge_results import _patch_env  # noqa: E402


_STRIPE_SITE_CUSTOMIZE = r'''
import os
import pathlib
import sys;sys.path.insert(0,'.');from daedalus_bridge import result_store
import threading
import time
import traceback
gate = pathlib.Path(os.environ["STRIPE_GATE_DIR"])
held_tab = os.environ["STRIPE_HELD_TAB"]
lock_calls_lock = threading.Lock()
def record_lock_call(target_key, lock):
    with lock_calls_lock:
        with (gate / "lock-calls").open("a", encoding="utf-8") as handle:
            handle.write(f"{target_key}\t{id(lock)}\n")
def install():
    try:
        while not all(hasattr(result_store, name) for name in (
                "delivery_lock_for", "delivery_result_paths")):
            time.sleep(0.001)
        real_lock_for = result_store.delivery_lock_for
        def recording_lock_for(target_key):
            lock = real_lock_for(target_key)
            record_lock_call(target_key, lock)
            return lock
        result_store.delivery_lock_for = recording_lock_for
        target_key = result_store.result_key(
            os.environ["DAEDALUS_TOKEN"], held_tab)
        target_lock = result_store.delivery_lock_for(target_key)
        (gate / "holder-lock").write_text(
            f"{target_key}\t{id(target_lock)}\n", encoding="utf-8")
        with target_lock:
            (gate / "holding").write_text("y", encoding="utf-8")
            try:
                (gate / "held").write_text("held", encoding="utf-8")
                while not (gate / "release").exists():
                    time.sleep(0.01)
            finally:
                (gate / "holding").unlink()
    except BaseException:
        (gate / "holder-error").write_text(
            traceback.format_exc(), encoding="utf-8")
threading.Thread(target=install, daemon=True).start()
'''


def _stripe_holder_setup(tmp, held_tab):
    """Install the in-process holder used by the delivery stripe tests."""
    patch_dir = Path(tmp) / 'stripe-patch'
    patch_dir.mkdir()
    gate_dir = Path(tmp) / 'stripe-gate'
    gate_dir.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        _STRIPE_SITE_CUSTOMIZE.lstrip(), encoding='utf-8')
    return gate_dir, _patch_env(
        patch_dir, STRIPE_GATE_DIR=str(gate_dir), STRIPE_HELD_TAB=held_tab)


def _stripe_lock_calls(gate_dir):
    path = gate_dir / 'lock-calls'
    if not path.is_file():
        return []
    return [tuple(line.split('\t', 1))
            for line in path.read_text(encoding='utf-8').splitlines()
            if line]


def _stripe_holder_lock(gate_dir):
    path = gate_dir / 'holder-lock'
    if not path.is_file():
        return None
    return tuple(path.read_text(encoding='utf-8').strip().split('\t', 1))


def test_delivery_post_waits_for_its_target_stripe_only(tmp):
    """A held target stripe blocks that target's delivery, nothing else.

    The stripe is held inside the bridge process; the test only observes what
    that does to real requests. The injected patch records the lock selected by
    the holder and every caller, so a failure can distinguish a holder error
    from a request that took a different lock.
    """
    held_tab = 'stripe-held'
    other_tab = 'stripe-other'
    gate_dir, env = _stripe_holder_setup(tmp, held_tab)
    # The stripe is keyed on the logical target, so comparing what the holder
    # and the request locked is plain equality — there is no spelling left to
    # normalise, which is the point of keying it this way.
    target_key = f'{TOK}_{held_tab}'

    def failure_message():
        calls = _stripe_lock_calls(gate_dir)
        target_calls = [entry for entry in calls
                        if len(entry) == 2
                        and entry[0] == target_key]
        held = _stripe_holder_lock(gate_dir)
        error_path = gate_dir / 'holder-error'
        if error_path.is_file():
            return (
                'target POST completed before release; cause: holder failed '
                'and released the stripe\n'
                'holder traceback:\n'
                f'{error_path.read_text(encoding="utf-8")}\n'
                f'holder-lock: {held!r}\n'
                f'held target lock calls: {target_calls!r}\n'
                f'lock-calls: {calls!r}')
        holder_id = held[1] if held and len(held) == 2 else '<missing>'
        target_ids = [entry[1] for entry in target_calls]
        return (
            'target POST completed before release; cause: request used a '
            'different lock object\n'
            f'held target lock id: {target_ids!r}; '
            f'holder lock id: {holder_id}\n'
            f'holder-lock: {held!r}\n'
            f'lock-calls: {calls!r}')

    with _util.bridge(tmp, env=env) as (base, _docroot):
        deadline = time.time() + 20
        while not (gate_dir / 'held').exists():
            assert time.time() < deadline, 'target stripe was not held'
            time.sleep(0.01)

        target_box = {}

        def post_target():
            try:
                target_box['value'] = _util.post_json(base + '/result', {
                    'token': TOK, 'tabId': held_tab, 'id': 'held',
                    'result': 'held', 'error': None, 'ts': 1,
                    '_did': 'stripe-did'})
            except Exception as exc:  # pylint: disable=broad-except
                target_box['error'] = exc

        target_thread = threading.Thread(target=post_target)
        target_thread.start()

        # Unrelated result traffic takes the result lock and no stripe, so it
        # must complete while the target's delivery POST is still waiting.
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': other_tab, 'id': 'other',
            'result': 'other', 'error': None, 'ts': 1})
        assert status == 200 and body == {'ok': True}, (status, body)
        # `holder-error` outranks the marker. The marker is removed by the
        # holder's own `finally`, and that unlink is itself fallible: a holder
        # that died AND failed to clean up leaves the marker behind, and
        # trusting it would call a run that held nothing a passing one.
        holder_failed = (gate_dir / 'holder-error').is_file()
        still_holding = (gate_dir / 'holding').exists() and not holder_failed
        if not still_holding:
            # The holder let the stripe go before the request reached it, so
            # nothing was serialized and this run never exercised the
            # property. Passing here would be a false green — the assertion
            # below would be satisfied by a request nothing was blocking.
            _util.skip(
                'the injected holder released the target stripe before the '
                'request reached it, so the property was never exercised: '
                + ((gate_dir / 'holder-error').read_text(encoding='utf-8')
                   if (gate_dir / 'holder-error').is_file()
                   else 'the holder exited without recording an error'))
        if not target_thread.is_alive():
            # The stripe was still held a moment ago and the request finished
            # anyway. That is the real defect this test exists to catch, so it
            # is a failure rather than a skip, and the message names which of
            # the two mechanisms produced it.
            raise AssertionError(failure_message())

        (gate_dir / 'release').write_text('release', encoding='utf-8')
        target_thread.join(timeout=20)
        assert not target_thread.is_alive(), target_box
        assert target_box.get('error') is None, target_box
        assert target_box.get('value') == (200, {'ok': True}), target_box

        # Re-checked after the wait: the holder can die during the window
        # between the sample above and the release below, and a run whose
        # stripe owner disappeared partway proves nothing either way.
        if (gate_dir / 'holder-error').is_file():
            _util.skip(
                'the injected holder failed while the request was waiting, so '
                'the property was never exercised end to end: '
                + (gate_dir / 'holder-error').read_text(encoding='utf-8'))
        held = _stripe_holder_lock(gate_dir)
        calls = _stripe_lock_calls(gate_dir)
        assert held and len(held) == 2, (held, calls)
        held_dir, held_lock_id = held
        target_calls = [entry for entry in calls
                        if len(entry) == 2
                        and entry[0] == target_key]
        assert held_dir == target_key, (held, target_key, calls)
        assert len(target_calls) >= 2, (held, calls)
        assert all(lock_id == held_lock_id
                   for _delivery_dir, lock_id in target_calls), (
                       held, target_calls, calls)


def _seed_delivery(tmp, tab, did):
    """Create one delivery before starting the in-process stripe holder."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tab, 'id': 'seed', 'result': 'seed',
            'error': None, 'ts': 1, '_did': did})
        assert status == 200 and body == {'ok': True}, (status, body)


def _wait_for_stripe_request(gate_dir, thread):
    """Wait until the request has selected a stripe, then require holding."""
    deadline = time.time() + 10
    while len(_stripe_lock_calls(gate_dir)) < 2:
        assert time.time() < deadline, (
            'request never selected a delivery stripe: '
            f'{_stripe_lock_calls(gate_dir)!r}')
        time.sleep(0.01)
    holder_failed = (gate_dir / 'holder-error').is_file()
    still_holding = (gate_dir / 'holding').exists() and not holder_failed
    if not still_holding:
        _util.skip(
            'the injected holder released the target stripe before the '
            'request reached it, so the property was never exercised: '
            + ((gate_dir / 'holder-error').read_text(encoding='utf-8')
               if (gate_dir / 'holder-error').is_file()
               else 'the holder exited without recording an error'))
    assert thread.is_alive(), (
        'request completed while the discovered-owner stripe was held: '
        f'{_stripe_lock_calls(gate_dir)!r}')


def test_a_stale_holding_marker_with_a_failed_holder_skips(tmp):
    """A failed holder makes a stale marker an environment skip."""
    gate_dir = Path(tmp) / 'stale-stripe-gate'
    gate_dir.mkdir()
    (gate_dir / 'lock-calls').write_text(
        'target\tlock-a\nother\tlock-b\n', encoding='utf-8')
    (gate_dir / 'holding').write_text('holding', encoding='utf-8')
    error_text = 'Traceback: injected holder failed'
    (gate_dir / 'holder-error').write_text(error_text, encoding='utf-8')
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait)
    thread.start()
    try:
        try:
            _wait_for_stripe_request(gate_dir, thread)
        except _util.Skipped as exc:
            assert error_text in str(exc), exc
        else:
            raise AssertionError('stale holder marker was trusted')
    finally:
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive()


def _require_holder_survived(gate_dir):
    """Require the injected holder to survive the request wait."""
    if (gate_dir / 'holder-error').is_file():
        _util.skip(
            'the injected holder failed while the request was waiting, so '
            'the property was never exercised end to end: '
            + (gate_dir / 'holder-error').read_text(encoding='utf-8'))


def _assert_discovered_owner_lock(gate_dir, owner):
    """The scan-discovered owner must be the lock key used by the request."""
    holder = _stripe_holder_lock(gate_dir)
    calls = _stripe_lock_calls(gate_dir)
    owner_key = f'{TOK}_{owner}'
    owner_calls = [entry for entry in calls
                   if len(entry) == 2 and entry[0] == owner_key]
    assert holder and len(holder) == 2 and holder[0] == owner_key, (
        holder, calls)
    assert len(owner_calls) >= 2, (holder, calls)
    assert all(lock_id == holder[1] for _key, lock_id in owner_calls), (
        holder, owner_calls, calls)


def test_delivery_lookup_without_tab_waits_for_discovered_owner_stripe(tmp):
    """A no-tab delivery lookup locks the tab found by the directory scan."""
    owner = 'scan-owner'
    unrelated = 'scan-unrelated'
    did = 'scan-delivery'
    _seed_delivery(tmp, owner, did)
    gate_dir, env = _stripe_holder_setup(tmp, owner)
    with _util.bridge(tmp, env=env) as (base, _docroot):
        deadline = time.time() + 20
        while not (gate_dir / 'held').exists():
            assert time.time() < deadline, 'target stripe was not held'
            time.sleep(0.01)

        result_box = {}

        def lookup_delivery():
            query = urllib.parse.urlencode({'token': TOK, 'delivery': did})
            result_box['value'] = _util.get_json(base + '/result?' + query)

        lookup_thread = threading.Thread(target=lookup_delivery)
        lookup_thread.start()
        _wait_for_stripe_request(gate_dir, lookup_thread)
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': unrelated, 'id': 'unrelated',
            'result': 'unrelated', 'error': None, 'ts': 2})
        assert status == 200 and body == {'ok': True}, (status, body)
        assert lookup_thread.is_alive(), 'unrelated result blocked the lookup'

        (gate_dir / 'release').write_text('release', encoding='utf-8')
        lookup_thread.join(timeout=20)
        assert not lookup_thread.is_alive(), result_box
        _require_holder_survived(gate_dir)
        status, result = result_box.get('value', (None, {}))
        assert status == 200 and result['id'] == 'seed', result_box
        assert result['deliveryId'] == did, result
        _assert_discovered_owner_lock(gate_dir, owner)


def test_compatibility_consume_without_tab_waits_for_discovered_owner_stripe(
        tmp):
    """A no-tab consume locks the owner found from the delivery id."""
    owner = 'consume-scan-owner'
    unrelated = 'consume-scan-unrelated'
    did = 'consume-scan-delivery'
    _seed_delivery(tmp, owner, did)
    gate_dir, env = _stripe_holder_setup(tmp, owner)
    with _util.bridge(tmp, env=env) as (base, _docroot):
        deadline = time.time() + 20
        while not (gate_dir / 'held').exists():
            assert time.time() < deadline, 'target stripe was not held'
            time.sleep(0.01)

        result_box = {}

        def consume_delivery():
            query = urllib.parse.urlencode({'token': TOK, 'consume': '1'})
            result_box['value'] = _util.get_json(base + '/result?' + query)

        consume_thread = threading.Thread(target=consume_delivery)
        consume_thread.start()
        _wait_for_stripe_request(gate_dir, consume_thread)
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': unrelated, 'id': 'unrelated',
            'result': 'unrelated', 'error': None, 'ts': 2})
        assert status == 200 and body == {'ok': True}, (status, body)
        assert consume_thread.is_alive(), (
            'unrelated result blocked the compatibility consume')

        (gate_dir / 'release').write_text('release', encoding='utf-8')
        consume_thread.join(timeout=20)
        assert not consume_thread.is_alive(), result_box
        _require_holder_survived(gate_dir)
        assert result_box.get('value', (None, {}))[0] == 200, result_box
        _assert_discovered_owner_lock(gate_dir, owner)


def test_absent_delivery_lookups_use_fixed_lock_stripes(tmp):
    """Absent target lookups reuse the fixed delivery lock stripe set."""
    docroot = Path(tmp) / 'stripe-docroot'
    saved = {name: os.environ.get(name) for name in (
        'DAEDALUS_DIR', 'DAEDALUS_PORT', 'DAEDALUS_MCP_PORT', 'TOKEN',
        'DAEDALUS_TOKEN')}
    os.environ.update({
        'DAEDALUS_DIR': str(docroot), 'DAEDALUS_PORT': '0',
        'DAEDALUS_MCP_PORT': '0', 'TOKEN': '', 'DAEDALUS_TOKEN': TOK})
    try:
        repo = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(repo))
        try:
            result_store = _util.load(
                repo / 'daedalus_bridge' / 'result_store.py', name='rs')
        finally:
            sys.path.pop(0)
        (docroot / 'results').mkdir(parents=True)
        original_locks = tuple(result_store.delivery_locks)
        initial = len(original_locks)
        returned_locks = []
        for index in range(10_000):
            _dir, delivery_file, tab = result_store.find_delivery_result(
                docroot / 'results', TOK, f'absent-{index}', 'missing-did')
            assert not delivery_file.exists()
            returned_locks.append(result_store.delivery_lock_for(
                result_store.result_key(TOK, tab)))
        assert initial == result_store.DELIVERY_LOCK_STRIPES
        assert len(result_store.delivery_locks) == initial
        assert all(any(lock is original for original in original_locks)
                   for lock in returned_locks)
        assert len({id(lock) for lock in returned_locks}) <= (
            result_store.DELIVERY_LOCK_STRIPES)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='bridgeresultstripes_')


if __name__ == '__main__':
    raise SystemExit(main())
