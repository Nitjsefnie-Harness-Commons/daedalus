#!/usr/bin/env python3
"""The shared result slot: what replaces it, and what may consume it.

A result arrives through `POST /result` and lands in two slots at once, and
the generation it is given is what lets a conditional consume tell its own
answer from a newer one. These tests drive that lifecycle, the delivery-id
dedup a retry depends on, and the storage failures each write can meet.
"""
import http.client
import json
import os
import time
import urllib.parse
import threading
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import TOK  # noqa: E402


def test_result_roundtrip_and_consume(tmp):
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and body == {'pending': True}, (status, body)

        res = {'token': TOK, 'id': 'r1', 'result': {'v': 1}, 'error': None, 'ts': 1}
        status, body = _util.post_json(base + '/result', res)
        assert status == 200 and body == {'ok': True}, (status, body)
        assert (Path(docroot) / 'results' / f'{TOK}.json').is_file()

        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and body['id'] == 'r1' and body['result'] == {'v': 1}
        # Not consumed: a second read returns the same result.
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert body['id'] == 'r1'

        status, body = _util.get_json(base + f'/result?token={TOK}&consume=1')
        assert body['id'] == 'r1'
        assert not (Path(docroot) / 'results' / f'{TOK}.json').exists()
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert body == {'pending': True}, body


def test_result_per_tab_and_broadcast_files(tmp):
    with _util.bridge(tmp) as (base, docroot):
        res = {'token': TOK, 'tabId': 'tab1', 'id': 'r2',
               'result': 'x', 'error': None, 'ts': 1}
        status, _ = _util.post_json(base + '/result', res)
        assert status == 200, status
        res_dir = Path(docroot) / 'results'
        # Per-tab result AND the token-only back-compat file are both written.
        assert (res_dir / f'{TOK}_tab1.json').is_file()
        assert (res_dir / f'{TOK}.json').is_file()

        status, body = _util.get_json(base + f'/result?token={TOK}&tab=tab1')
        assert body['id'] == 'r2', body
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert body['id'] == 'r2', body

        # Consuming the per-tab result leaves the token-only file readable.
        status, body = _util.get_json(base + f'/result?token={TOK}&tab=tab1&consume=1')
        assert body['id'] == 'r2'
        assert not (res_dir / f'{TOK}_tab1.json').exists()
        status, body = _util.get_json(base + f'/result?token={TOK}&tab=tab1')
        assert body == {'pending': True}, body
        status, body = _util.get_json(base + f'/result?token={TOK}')
        assert body['id'] == 'r2', body


def test_distinct_delivery_results_are_independently_consumable(tmp):
    """Each delivery id keeps its own result instead of sharing the slot."""
    first_did = '1700000000000_000001'
    second_did = '1700000000001_000002'
    with _util.bridge(tmp) as (base, docroot):
        for did, result_id, value in (
                (first_did, 'first', 1), (second_did, 'second', 2)):
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'shared', 'id': result_id,
                'result': value, 'error': None, 'ts': value, '_did': did})
            assert status == 200 and body == {'ok': True}, (status, body)

        result_dir = (Path(docroot) / 'results' / 'deliveries'
                      / f'{TOK}_shared')
        assert (result_dir / f'{first_did}.json').is_file()
        assert (result_dir / f'{second_did}.json').is_file()

        def delivery_url(did, **extra):
            query = {'token': TOK, 'tab': 'shared', 'delivery': did}
            query.update(extra)
            return base + '/result?' + urllib.parse.urlencode(query)

        status, first = _util.get_json(delivery_url(first_did))
        assert status == 200 and first['id'] == 'first', first
        status, first_without_tab = _util.get_json(
            base + '/result?' + urllib.parse.urlencode({
                'token': TOK, 'delivery': first_did}))
        assert status == 200 and first_without_tab['id'] == 'first', (
            status, first_without_tab)
        status, second = _util.get_json(delivery_url(second_did))
        assert status == 200 and second['id'] == 'second', second

        first_generation = first['resultGeneration']
        status, consumed = _util.get_json(
            delivery_url(first_did, consume='1', expected=first_generation))
        assert status == 200 and consumed == {
            'consumed': True, 'resultGeneration': first_generation}, consumed
        status, pending = _util.get_json(delivery_url(first_did))
        assert status == 200 and pending == {'pending': True}, pending
        status, slot = _util.get_json(
            base + '/result?' + urllib.parse.urlencode({
                'token': TOK, 'tab': 'shared'}))
        assert status == 200 and slot['id'] == 'second', slot

        status, remaining = _util.get_json(delivery_url(second_did))
        assert status == 200 and remaining['id'] == 'second', remaining
        second_generation = remaining['resultGeneration']
        status, consumed = _util.get_json(
            delivery_url(second_did, consume='1', expected=second_generation))
        assert status == 200 and consumed == {
            'consumed': True, 'resultGeneration': second_generation}, consumed
        status, pending = _util.get_json(
            base + '/result?' + urllib.parse.urlencode({
                'token': TOK, 'tab': 'shared'}))
        assert status == 200 and pending == {'pending': True}, pending


def test_delivery_namespace_cannot_collide_with_a_compatibility_slot(tmp):
    """A dotted tab target cannot turn a delivery directory into a slot."""
    did = '1700000000000_000099'
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'foo', 'id': 'slot-owner',
            'result': 'slot', 'error': None, 'ts': 1})
        assert status == 200 and body == {'ok': True}, (status, body)

        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'foo.json', 'id': 'delivery-owner',
            'result': 'delivery', 'error': None, 'ts': 2, '_did': did})
        assert status == 200 and body == {'ok': True}, (status, body)

        status, result = _util.get_json(
            base + '/result?' + urllib.parse.urlencode({
                'token': TOK, 'tab': 'foo.json', 'delivery': did}))
        assert status == 200 and result['id'] == 'delivery-owner', result
        delivery_file = (Path(docroot) / 'results' / 'deliveries'
                         / f'{TOK}_foo.json' / f'{did}.json')
        assert delivery_file.is_file(), delivery_file


def test_compatibility_consume_ignores_invalid_legacy_delivery_metadata(tmp):
    """An old invalid delivery id cannot turn a successful consume into 500."""
    result = {
        'token': TOK, 'tabId': 'legacy-invalid', 'id': 'legacy-result',
        'result': 'kept', 'error': None, 'ts': 1,
        'resultGeneration': 'g-old', 'deliveryId': '../old'}
    with _util.bridge(tmp) as (base, docroot):
        slot = Path(docroot) / 'results' / f'{TOK}_legacy-invalid.json'
        shared = Path(docroot) / 'results' / f'{TOK}.json'
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_text(json.dumps(result), encoding='utf-8')
        shared.write_text(json.dumps(result), encoding='utf-8')
        status, consumed = _util.get_json(
            base + '/result?' + urllib.parse.urlencode({
                'token': TOK, 'tab': 'legacy-invalid', 'consume': '1',
                'expected': 'g-old'}))
        assert status == 200 and consumed == {
            'consumed': True, 'resultGeneration': 'g-old'}, consumed
        assert not slot.exists()


def test_compatibility_consume_retries_are_bounded(tmp):
    """A changing delivery id cannot hold a result worker indefinitely."""
    patch_dir = Path(tmp) / 'spin-patch'
    patch_dir.mkdir()
    patch_gate = Path(tmp) / 'spin-gate'
    patch_gate.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        'import __main__\n'
        'import pathlib\n'
        'import os\n'
        'import threading\n'
        'import time\n'
        'gate = pathlib.Path(os.environ["SPIN_GATE_DIR"])\n'
        'def install():\n'
        '    while not hasattr(__main__, "_read_result_file"):\n'
        '        time.sleep(0.001)\n'
        '    real_read = __main__._read_result_file\n'
        '    state = {"reads": 0}\n'
        '    churn_reads = 200000\n'
        '    def spinning_read(path, consume, expected):\n'
        '        state["reads"] += 1\n'
        '        reads = state["reads"]\n'
        '        if not consume and reads <= churn_reads:\n'
        '            return {"deliveryId": "churn-" + str(reads),\n'
        '                    "resultGeneration": "churn-generation"}, ""\n'
        '        response, delivery = real_read(path, consume, expected)\n'
        '        if consume and isinstance(response, dict):\n'
        '            response["probeReads"] = reads\n'
        '        return response, delivery\n'
        '    __main__._read_result_file = spinning_read\n'
        '    (gate / "ready").write_text("ready", encoding="utf-8")\n'
        'threading.Thread(target=install, daemon=True).start()\n',
        encoding='utf-8')
    env = {'PYTHONPATH': str(patch_dir), 'SPIN_GATE_DIR': str(patch_gate)}
    did = 'spin-delivery'
    tab = 'spin-target'
    with _util.bridge(tmp, env=env) as (base, docroot):
        deadline = time.time() + 10
        while not (patch_gate / 'ready').exists():
            assert time.time() < deadline, 'spin patch was not installed'
            time.sleep(0.01)
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tab, 'id': 'spin-result',
            'result': 'spin-result', 'error': None, 'ts': 1, '_did': did})
        assert status == 200 and body == {'ok': True}, (status, body)
        query = base + '/result?' + urllib.parse.urlencode({
            'token': TOK, 'tab': tab, 'consume': '1'})
        status, consumed = _util.get_json(query)
        assert status == 200 and consumed['id'] == 'spin-result', consumed
        assert consumed['probeReads'] <= 2 * 8 + 1, consumed
        delivery_file = (Path(docroot) / 'results' / 'deliveries'
                         / f'{TOK}_{tab}' / f'{did}.json')
        assert delivery_file.is_file(), delivery_file


def test_bounded_consume_fallback_still_honours_expected(tmp):
    """Exhausting the retries must not discard the caller's precondition.

    Every retry consumes with the caller's `expected` generation. The path
    taken once the retries run out has to do the same: a conditional consume
    that names a generation no longer in the slot must leave that slot alone,
    or the caller that owns the newer result loses it — which is the failure
    this whole feature exists to remove.
    """
    patch_dir = Path(tmp) / 'spin-patch'
    patch_dir.mkdir()
    patch_gate = Path(tmp) / 'spin-gate'
    patch_gate.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        'import __main__\n'
        'import pathlib\n'
        'import os\n'
        'import threading\n'
        'import time\n'
        'gate = pathlib.Path(os.environ["SPIN_GATE_DIR"])\n'
        'def install():\n'
        '    while not hasattr(__main__, "_read_result_file"):\n'
        '        time.sleep(0.001)\n'
        '    real_read = __main__._read_result_file\n'
        '    state = {"reads": 0}\n'
        '    def spinning_read(path, consume, expected):\n'
        '        state["reads"] += 1\n'
        '        if not consume:\n'
        '            return {"deliveryId": "churn-" + str(state["reads"]),\n'
        '                    "resultGeneration": "churn-generation"}, ""\n'
        '        return real_read(path, consume, expected)\n'
        '    __main__._read_result_file = spinning_read\n'
        '    (gate / "ready").write_text("ready", encoding="utf-8")\n'
        'threading.Thread(target=install, daemon=True).start()\n',
        encoding='utf-8')
    env = {'PYTHONPATH': str(patch_dir), 'SPIN_GATE_DIR': str(patch_gate)}
    tab = 'expected-target'
    with _util.bridge(tmp, env=env) as (base, docroot):
        deadline = time.time() + 10
        while not (patch_gate / 'ready').exists():
            assert time.time() < deadline, 'spin patch was not installed'
            time.sleep(0.01)
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tab, 'id': 'owned-result',
            'result': 'owned-result', 'error': None, 'ts': 1})
        assert status == 200 and body == {'ok': True}, (status, body)

        slot = Path(docroot) / 'results' / f'{TOK}_{tab}.json'
        assert slot.is_file(), slot

        status, answer = _util.get_json(base + '/result?' + (
            urllib.parse.urlencode({
                'token': TOK, 'tab': tab, 'consume': '1',
                'expected': 'a-generation-that-is-not-there'})))
        assert status == 200, (status, answer)
        assert answer == {'consumed': False}, answer
        assert slot.is_file(), 'the slot was consumed despite the mismatch'


def test_delivery_results_evict_oldest_per_tab(tmp):
    """The per-tab delivery store retains only its configured newest results."""
    dids = [f'170000000000{i}_00000{i}' for i in (1, 2, 3)]
    with _util.bridge(
            tmp, env={'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}) as (base, docroot):
        for index, did in enumerate(dids, 1):
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'bounded', 'id': f'r{index}',
                'result': index, 'error': None, 'ts': index, '_did': did})
            assert status == 200 and body == {'ok': True}, (status, body)

        def delivery_url(did):
            query = urllib.parse.urlencode({
                'token': TOK, 'tab': 'bounded', 'delivery': did})
            return base + '/result?' + query

        status, oldest = _util.get_json(delivery_url(dids[0]))
        assert status == 200 and oldest == {'pending': True}, oldest
        status, middle = _util.get_json(delivery_url(dids[1]))
        assert status == 200 and middle['id'] == 'r2', middle
        status, newest = _util.get_json(delivery_url(dids[2]))
        assert status == 200 and newest['id'] == 'r3', newest

        result_dir = (Path(docroot) / 'results' / 'deliveries'
                      / f'{TOK}_bounded')
        assert sorted(path.name for path in result_dir.glob('*.json')) == [
            f'{dids[1]}.json', f'{dids[2]}.json']


def test_delivery_results_evict_by_acceptance_order_not_filename(tmp):
    """A non-sortable delivery id still ages out in its actual order."""
    dids = ('zzzz-oldest', '1700000000001_000001', '1700000000002_000002')
    with _util.bridge(
            tmp, env={'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}) as (base, docroot):
        for index, did in enumerate(dids, 1):
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'ordered', 'id': f'r{index}',
                'result': index, 'error': None, 'ts': index, '_did': did})
            assert status == 200 and body == {'ok': True}, (status, body)

        def delivery_url(did):
            return base + '/result?' + urllib.parse.urlencode({
                'token': TOK, 'tab': 'ordered', 'delivery': did})

        status, oldest = _util.get_json(delivery_url(dids[0]))
        assert status == 200 and oldest == {'pending': True}, oldest
        for did, result_id in zip(dids[1:], ('r2', 'r3')):
            status, body = _util.get_json(delivery_url(did))
            assert status == 200 and body['id'] == result_id, body

        result_dir = (Path(docroot) / 'results' / 'deliveries'
                      / f'{TOK}_ordered')
        assert sorted(path.name for path in result_dir.glob('*.json')) == [
            f'{dids[1]}.json', f'{dids[2]}.json']


def test_delivery_write_cannot_race_compatibility_cleanup(tmp):
    """A retry cannot be unlinked by cleanup of the replaced slot."""
    did = 'retry-after-restart'
    tab = 'cleanup-race'
    gate_dir = Path(tmp) / 'cleanup-gate'
    gate_dir.mkdir()
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tab, 'id': 'old-result',
            'result': 'old-result', 'error': None, 'ts': 1, '_did': did})
        assert status == 200 and body == {'ok': True}, (status, body)
        delivery_file = (Path(docroot) / 'results' / 'deliveries'
                         / f'{TOK}_{tab}' / f'{did}.json')
        old_generation = json.loads(
            delivery_file.read_text(encoding='utf-8'))['resultGeneration']

    patch_dir = Path(tmp) / 'cleanup-patch'
    patch_dir.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        'import os\n'
        'import time\n'
        'gate = pathlib.Path(os.environ["CLEANUP_GATE_DIR"])\n'
        'real_unlink = pathlib.Path.unlink\n'
        'def gated_unlink(path, *args, **kwargs):\n'
        '    if "deliveries" in path.parts and path.suffix == ".json":\n'
        '        (gate / "cleanup-read").write_text("read", encoding="utf-8")\n'
        '        while not (gate / "release-cleanup").exists():\n'
        '            time.sleep(0.01)\n'
        '    return real_unlink(path, *args, **kwargs)\n'
        'pathlib.Path.unlink = gated_unlink\n',
        encoding='utf-8')

    env = {'PYTHONPATH': str(patch_dir),
           'CLEANUP_GATE_DIR': str(gate_dir)}
    with _util.bridge(tmp, env=env) as (base, _docroot):
        consume_box = {}
        post_box = {}

        def consume_old_slot():
            params = {'token': TOK, 'tab': tab, 'consume': '1',
                      'expected': old_generation}
            query = base + '/result?' + urllib.parse.urlencode(params)
            consume_box['value'] = _util.get_json(query)

        def retry_post():
            post_box['value'] = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': tab, 'id': 'retried-result',
                'result': 'retried-result', 'error': None, 'ts': 2,
                '_did': did})

        consume_thread = threading.Thread(target=consume_old_slot)
        consume_thread.start()
        deadline = time.time() + 10
        while not (gate_dir / 'cleanup-read').exists():
            assert time.time() < deadline, 'cleanup never reached gated read'
            time.sleep(0.01)
        post_thread = threading.Thread(target=retry_post)
        post_thread.start()
        time.sleep(0.1)
        (gate_dir / 'release-cleanup').write_text('release', encoding='utf-8')
        consume_thread.join(timeout=10)
        post_thread.join(timeout=10)
        assert not consume_thread.is_alive() and not post_thread.is_alive()
        assert post_box['value'] == (200, {'ok': True}), post_box
        assert consume_box['value'] == (
            200, {'consumed': True, 'resultGeneration': old_generation})
        params = {'token': TOK, 'tab': tab, 'delivery': did}
        query = base + '/result?' + urllib.parse.urlencode(params)
        status, result = _util.get_json(query)
        assert status == 200 and result.get('id') == 'retried-result', result


def test_delivery_post_waits_for_its_target_stripe_only(tmp):
    """A held target stripe blocks that target's delivery, nothing else.

    The stripe is held inside the bridge process; the test only observes what
    that does to real requests. The injected patch records the lock selected by
    the holder and every caller, so a failure can distinguish a holder error
    from a request that took a different lock.
    """
    held_tab = 'stripe-held'
    other_tab = 'stripe-other'
    patch_dir = Path(tmp) / 'stripe-patch'
    patch_dir.mkdir()
    gate_dir = Path(tmp) / 'stripe-gate'
    gate_dir.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        'import __main__\n'
        'import os\n'
        'import pathlib\n'
        'import threading\n'
        'import time\n'
        'import traceback\n'
        'gate = pathlib.Path(os.environ["STRIPE_GATE_DIR"])\n'
        'held_tab = os.environ["STRIPE_HELD_TAB"]\n'
        'lock_calls_lock = threading.Lock()\n'
        'def record_lock_call(target_key, lock):\n'
        '    with lock_calls_lock:\n'
        '        with (gate / "lock-calls").open("a", encoding="utf-8") as handle:\n'
        '            handle.write(f"{target_key}\\t{id(lock)}\\n")\n'
        'def install():\n'
        '    try:\n'
        '        while not all(hasattr(__main__, name) for name in (\n'
        '                "_delivery_lock_for", "_delivery_result_paths")):\n'
        '            time.sleep(0.001)\n'
        '        real_lock_for = __main__._delivery_lock_for\n'
        '        def recording_lock_for(target_key):\n'
        '            lock = real_lock_for(target_key)\n'
        '            record_lock_call(target_key, lock)\n'
        '            return lock\n'
        '        __main__._delivery_lock_for = recording_lock_for\n'
        '        target_key = __main__._result_key(\n'
        '            os.environ["DAEDALUS_TOKEN"], held_tab)\n'
        '        target_lock = __main__._delivery_lock_for(target_key)\n'
        '        (gate / "holder-lock").write_text(\n'
        '            f"{target_key}\\t{id(target_lock)}\\n", encoding="utf-8")\n'
        '        with target_lock:\n'
        '            (gate / "holding").write_text("y", encoding="utf-8")\n'
        '            try:\n'
        '                (gate / "held").write_text("held", encoding="utf-8")\n'
        '                while not (gate / "release").exists():\n'
        '                    time.sleep(0.01)\n'
        '            finally:\n'
        '                (gate / "holding").unlink()\n'
        '    except BaseException:\n'
        '        (gate / "holder-error").write_text(\n'
        '            traceback.format_exc(), encoding="utf-8")\n'
        'threading.Thread(target=install, daemon=True).start()\n',
        encoding='utf-8')
    env = {
        'PYTHONPATH': str(patch_dir),
        'STRIPE_GATE_DIR': str(gate_dir),
        'STRIPE_HELD_TAB': held_tab,
    }
    # The stripe is keyed on the logical target, so comparing what the holder
    # and the request locked is plain equality — there is no spelling left to
    # normalise, which is the point of keying it this way.
    target_key = f'{TOK}_{held_tab}'

    def lock_calls():
        path = gate_dir / 'lock-calls'
        if not path.is_file():
            return []
        return [tuple(line.split('\t', 1))
                for line in path.read_text(encoding='utf-8').splitlines()
                if line]

    def holder_lock():
        path = gate_dir / 'holder-lock'
        if not path.is_file():
            return None
        return tuple(path.read_text(encoding='utf-8').strip().split('\t', 1))

    def failure_message():
        calls = lock_calls()
        target_calls = [entry for entry in calls
                        if len(entry) == 2
                        and entry[0] == target_key]
        held = holder_lock()
        error_path = gate_dir / 'holder-error'
        if error_path.is_file():
            return (
                'target POST completed before release; cause: holder failed '
                'and released the stripe\n'
                f'holder traceback:\n{error_path.read_text(encoding="utf-8")}\n'
                f'holder-lock: {held!r}\n'
                f'held target lock calls: {target_calls!r}\n'
                f'lock-calls: {calls!r}')
        holder_id = held[1] if held and len(held) == 2 else '<missing>'
        target_ids = [entry[1] for entry in target_calls]
        return (
            'target POST completed before release; cause: request used a '
            'different lock object\n'
            f'held target lock id: {target_ids!r}; holder lock id: {holder_id}\n'
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
        held = holder_lock()
        calls = lock_calls()
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


def test_delivery_stamp_survives_restart_with_an_earlier_wall_clock(tmp):
    """A persisted future stamp keeps a new post from immediate eviction."""
    tab = 'restart-clock'
    dids = ('old-a', 'old-b')
    with _util.bridge(
            tmp, env={'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}) as (base, docroot):
        for did in dids:
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': tab, 'id': did, 'result': did,
                'error': None, 'ts': 1, '_did': did})
            assert status == 200 and body == {'ok': True}, (status, body)
        directory = (Path(docroot) / 'results' / 'deliveries'
                     / f'{TOK}_{tab}')
        future = time.time_ns() + 1_000_000_000_000
        for index, path in enumerate(sorted(directory.glob('*.json'))):
            os.utime(path, ns=(future + index, future + index))

    with _util.bridge(
            tmp, env={'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}) as (base, _docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tab, 'id': 'new-after-restart',
            'result': 'new-after-restart', 'error': None, 'ts': 2,
            '_did': 'new-after-restart'})
        assert status == 200 and body == {'ok': True}, (status, body)
        params = {'token': TOK, 'tab': tab,
                  'delivery': 'new-after-restart'}
        query = base + '/result?' + urllib.parse.urlencode(params)
        status, result = _util.get_json(query)
        assert status == 200 and result.get('id') == 'new-after-restart', result


def test_failed_delivery_stamp_skips_eviction_with_distinct_stamps(tmp):
    """A failed stamp leaves distinct persisted mtimes untouched."""
    patch_dir = Path(tmp) / 'utime-patch'
    patch_dir.mkdir()
    patch_gate = Path(tmp) / 'utime-gate'
    patch_gate.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        'import __main__\n'
        'import os\n'
        'import pathlib\n'
        'import threading\n'
        'import time\n'
        'gate = pathlib.Path(os.environ["UTIME_GATE_DIR"])\n'
        'def install():\n'
        '    while not hasattr(__main__, "_mark_delivery_result"):\n'
        '        time.sleep(0.001)\n'
        '    def failed_utime(*_args, **_kwargs):\n'
        '        raise OSError("injected utime failure")\n'
        '    __main__.os.utime = failed_utime\n'
        '    (gate / "ready").write_text("ready", encoding="utf-8")\n'
        'threading.Thread(target=install, daemon=True).start()\n',
        encoding='utf-8')
    tab = 'utime-distinct'
    dids = ('zzzz-oldest', 'normal-middle', 'normal-newest')
    env = {'PYTHONPATH': str(patch_dir),
           'UTIME_GATE_DIR': str(patch_gate),
           'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}
    with _util.bridge(tmp, env=env) as (base, docroot):
        deadline = time.time() + 10
        while not (patch_gate / 'ready').exists():
            assert time.time() < deadline, 'utime patch was not installed'
            time.sleep(0.01)
        for did in dids[:2]:
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': tab, 'id': did, 'result': did,
                'error': None, 'ts': 1, '_did': did})
            assert status == 200 and body == {'ok': True}, (status, body)
        directory = (Path(docroot) / 'results' / 'deliveries'
                     / f'{TOK}_{tab}')
        persisted = time.time_ns() - 1_000_000_000
        for index, path in enumerate(sorted(directory.glob('*.json'))):
            os.utime(path, ns=(persisted + index, persisted + index))
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tab, 'id': dids[2], 'result': dids[2],
            'error': None, 'ts': 2, '_did': dids[2]})
        assert status == 200 and body == {'ok': True}, (status, body)
        for did in dids:
            params = {'token': TOK, 'tab': tab, 'delivery': did}
            query = base + '/result?' + urllib.parse.urlencode(params)
            status, result = _util.get_json(query)
            assert status == 200 and result.get('id') == did, (did, result)

        tie_tab = 'utime-tie'
        tie_dids = ('tie-zzzz-oldest', 'tie-normal-middle',
                    'tie-normal-newest')
        for did in tie_dids[:2]:
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': tie_tab, 'id': did, 'result': did,
                'error': None, 'ts': 1, '_did': did})
            assert status == 200 and body == {'ok': True}, (status, body)
        tie_directory = (Path(docroot) / 'results' / 'deliveries'
                         / f'{TOK}_{tie_tab}')
        tied = time.time_ns() - 1_000_000_000
        for path in tie_directory.glob('*.json'):
            os.utime(path, ns=(tied, tied))
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tie_tab, 'id': tie_dids[2],
            'result': tie_dids[2], 'error': None, 'ts': 2,
            '_did': tie_dids[2]})
        assert status == 200 and body == {'ok': True}, (status, body)
        for did in tie_dids:
            params = {'token': TOK, 'tab': tie_tab, 'delivery': did}
            query = base + '/result?' + urllib.parse.urlencode(params)
            status, result = _util.get_json(query)
            assert status == 200 and result.get('id') == did, (did, result)


def test_delivery_eviction_failure_still_returns_success(tmp):
    """A failed trim cannot drop the already stored POST response."""
    patch_dir = Path(tmp) / 'eviction-patch'
    patch_dir.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        'real_unlink = pathlib.Path.unlink\n'
        'def fail_oldest(path, *args, **kwargs):\n'
        '    if path.name == "zzzz-oldest.json" and "deliveries" in path.parts:\n'
        '        raise PermissionError("injected eviction unlink failure")\n'
        '    return real_unlink(path, *args, **kwargs)\n'
        'pathlib.Path.unlink = fail_oldest\n',
        encoding='utf-8')
    env = {'PYTHONPATH': str(patch_dir),
           'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}
    with _util.bridge(tmp, env=env) as (base, _docroot):
        for did in ('zzzz-oldest', 'middle'):
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'eviction-failure', 'id': did,
                'result': did, 'error': None, 'ts': 1, '_did': did})
            assert status == 200 and body == {'ok': True}, (status, body)
        try:
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'eviction-failure', 'id': 'newest',
                'result': 'newest', 'error': None, 'ts': 2,
                '_did': 'newest'})
        except (ConnectionError, OSError) as exc:
            raise AssertionError('eviction failure dropped the response') from exc
        assert status == 200 and body == {'ok': True}, (status, body)


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
            server = _util.load(repo / 'server.py', name='stripe_server')
        finally:
            sys.path.pop(0)
        server.RES_DIR.mkdir(parents=True)
        initial = len(server._delivery_locks)
        for index in range(10_000):
            _dir, delivery_file, tab = server._find_delivery_result(
                TOK, f'absent-{index}', 'missing-did')
            assert not delivery_file.exists()
            server._delivery_lock_for(server._result_key(TOK, tab))
        assert initial == server._DELIVERY_LOCK_STRIPES
        assert len(server._delivery_locks) == initial
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_result_without_delivery_id_keeps_both_compatibility_slots(tmp):
    """Legacy results still write and consume the shared compatibility slots."""
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'legacy', 'id': 'legacy-result',
            'result': 'kept', 'error': None, 'ts': 1})
        assert status == 200 and body == {'ok': True}, (status, body)
        result_dir = Path(docroot) / 'results'
        assert (result_dir / f'{TOK}_legacy.json').is_file()
        assert (result_dir / f'{TOK}.json').is_file()
        assert not (result_dir / f'{TOK}_legacy').exists()

        status, consumed = _util.get_json(
            base + f'/result?token={TOK}&tab=legacy&consume=1')
        assert status == 200 and consumed['id'] == 'legacy-result', consumed
        assert not (result_dir / f'{TOK}_legacy.json').exists()
        status, shared = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and shared['id'] == 'legacy-result', shared


def test_compatibility_consume_removes_the_same_delivery_result(tmp):
    """Consuming a slot does not leave its delivery copy behind."""
    did = '1700000000000_000007'
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'sync', 'id': 'synced',
            'result': 'value', 'error': None, 'ts': 1, '_did': did})
        assert status == 200 and body == {'ok': True}, (status, body)
        status, slot = _util.get_json(
            base + '/result?' + urllib.parse.urlencode({
                'token': TOK, 'tab': 'sync'}))
        generation = slot['resultGeneration']
        status, consumed = _util.get_json(
            base + '/result?' + urllib.parse.urlencode({
                'token': TOK, 'tab': 'sync', 'consume': '1',
                'expected': generation}))
        assert status == 200 and consumed == {
            'consumed': True, 'resultGeneration': generation}, consumed
        status, pending = _util.get_json(
            base + '/result?' + urllib.parse.urlencode({
                'token': TOK, 'tab': 'sync', 'delivery': did}))
        assert status == 200 and pending == {'pending': True}, pending


def test_result_did_becomes_roundtrip_ms(tmp):
    with _util.bridge(tmp) as (base, docroot):
        did = f'{int(time.time() * 1000) - 50}_000001'
        res = {'token': TOK, 'id': 'r3', 'result': 1, 'error': None,
               'ts': 1, '_did': did}
        status, _ = _util.post_json(base + '/result', res)
        assert status == 200, status
        stored = json.loads((Path(docroot) / 'results' / f'{TOK}.json').read_text(encoding='utf-8'))
        assert '_did' not in stored, stored
        assert stored['deliveryId'] == did, stored
        assert isinstance(stored['roundtrip_ms'], int) and stored['roundtrip_ms'] >= 0


def test_conditional_consume_preserves_a_newer_waiters_result(tmp):
    """A waiter may consume only the exact result generation it peeked."""
    with _util.bridge(tmp) as (base, _docroot):
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'shared', 'id': 'waiter-a',
            'result': 'first', 'resultGeneration': 'generation-a'})
        assert status == 200, status
        status, peeked = _util.get_json(
            base + f'/result?token={TOK}&tab=shared')
        assert status == 200 and peeked['id'] == 'waiter-a', (status, peeked)
        expected = peeked['resultGeneration']

        # Waiter B's result replaces A after A peeked but before A consumes.
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'shared', 'id': 'waiter-b',
            'result': 'second', 'resultGeneration': 'generation-b'})
        assert status == 200, status
        status, consume = _util.get_json(
            base + f'/result?token={TOK}&tab=shared&consume=1&expected={expected}')
        assert status == 200, (status, consume)

        # A failed conditional consume must leave B's result for waiter B.
        status, owner = _util.get_json(
            base + f'/result?token={TOK}&tab=shared')
        assert status == 200 and owner.get('id') == 'waiter-b', (consume, owner)
        assert consume.get('consumed') is False, consume

        generation = owner['resultGeneration']
        status, consume = _util.get_json(
            base + f'/result?token={TOK}&tab=shared&consume=1&expected={generation}')
        assert status == 200 and consume == {
            'consumed': True, 'resultGeneration': generation}, consume


def test_a_retried_result_never_replaces_a_newer_one(tmp):
    """A lost 200 makes the extension re-POST; that must not undo the next result.

    background.js retries a result POST up to three times on a transient
    failure, and a response lost after the server stored it looks exactly like
    one that never arrived. The retry carries the same delivery id, so the
    bridge can tell a repeat from a fresh result and leave both slots alone.
    """
    with _util.bridge(tmp) as (base, docroot):
        first = {'token': TOK, 'tabId': 'extension', 'id': 'a',
                 'result': 'first', 'error': None, 'ts': 1,
                 '_did': '1700000000000_000001'}
        status, _ = _util.post_json(base + '/result', first)
        assert status == 200, status
        status, peeked = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert status == 200 and peeked['id'] == 'a', (status, peeked)
        first_generation = peeked['resultGeneration']

        # The same delivery id twice is one result, whatever else has landed.
        status, body = _util.post_json(base + '/result', first)
        assert status == 200 and body == {'ok': True, 'duplicate': True}, (
            status, body)
        status, peeked = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert peeked['resultGeneration'] == first_generation, peeked

        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': 'b',
            'result': 'second', 'error': None, 'ts': 2,
            '_did': '1700000000001_000002'})
        assert status == 200, status

        status, body = _util.post_json(base + '/result', first)
        assert status == 200 and body == {'ok': True, 'duplicate': True}, (
            status, body)
        # Both slots still hold B: its waiter is still able to read it.
        status, owner = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert status == 200 and owner['id'] == 'b', (status, owner)
        status, shared = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and shared['id'] == 'b', (status, shared)
        stored = json.loads((docroot / 'results' / f'{TOK}_extension.json')
                            .read_text(encoding='utf-8'))
        assert stored['deliveryId'] == '1700000000001_000002', stored


def test_a_result_without_a_delivery_id_still_replaces_the_slot(tmp):
    """Dedup keys on the delivery id, so a result that has none is never one."""
    with _util.bridge(tmp) as (base, _docroot):
        for value in ('first', 'second'):
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': 'no-did',
                'result': value, 'error': None, 'ts': 1})
            assert status == 200 and body == {'ok': True}, (status, body)
        status, latest = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert status == 200 and latest['result'] == 'second', latest


def test_result_path_component_byte_boundaries(tmp):
    """Result names honor both the component and derived-filename budgets."""
    token = 'lengthtoken'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token}) as (base, docroot):
        # This 239-byte tab makes a 256-byte derived filename for this token,
        # one byte beyond filesystems with a 255-byte component limit.
        status, body = _util.post_json(
            base + '/result',
            {'token': token, 'tabId': 'x' * 239, 'id': 'long', 'result': 'x'})
        assert status == 400, (status, body)

        # token + underscore + tab + ".json" is exactly 240 UTF-8 bytes.
        boundary_tab = 't' * 223
        status, body = _util.post_json(
            base + '/result',
            {'token': token, 'tabId': boundary_tab, 'id': 'edge',
             'result': 'kept'})
        assert status == 200 and body == {'ok': True}, (status, body)
        stored = docroot / 'results' / f'{token}_{boundary_tab}.json'
        assert json.loads(stored.read_text(encoding='utf-8'))['result'] == 'kept'
        status, body = _util.get_json(
            base + f'/result?token={token}&tab={boundary_tab}')
        assert status == 200 and body['result'] == 'kept', (status, body)

        over_boundary = 't' * 224
        status, body = _util.post_json(
            base + '/result',
            {'token': token, 'tabId': over_boundary, 'id': 'over',
             'result': 'rejected'})
        assert status == 400, (status, body)
        status, body = _util.get(
            base + f'/result?token={token}&tab={over_boundary}')
        assert status == 400, (status, body)


def test_malformed_result_slot_returns_a_storage_error(tmp):
    """Malformed local result data must answer without ending the request."""
    with _util.bridge(tmp) as (base, docroot):
        result_file = Path(docroot) / 'results' / f'{TOK}.json'
        for stored in ('[]', '{not json'):
            result_file.write_text(stored, encoding='utf-8')
            try:
                status, raw = _util.get(base + f'/result?token={TOK}')
            except http.client.RemoteDisconnected as exc:
                raise AssertionError(
                    f'malformed result {stored!r} ended the request') from exc
            assert status == 500, (stored, status, raw)
            assert json.loads(raw) == {'error': 'result storage failure'}, raw
            assert result_file.read_text(encoding='utf-8') == stored

        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_unencodable_result_is_refused_without_poisoning_the_existing_slot(tmp):
    """A result that cannot become UTF-8 must not truncate the current slot."""
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'id': 'kept', 'result': 'safe',
        })
        assert status == 200 and body == {'ok': True}, (status, body)
        result_file = Path(docroot) / 'results' / f'{TOK}.json'
        original = result_file.read_bytes()

        raw_result = (
            b'{"token":"httptok","id":"rejected","result":"\\ud800"}')
        try:
            status, raw = _util.request(
                base + '/result', 'POST', body=raw_result,
                headers={'Content-Type': 'application/json'})
            error = json.loads(raw).get('error')
        except http.client.RemoteDisconnected:
            status, error = 'dropped', None

        assert (status, error) == (400, 'result is not encodable'), (status, error)
        assert result_file.read_bytes() == original
        assert sorted(path.name for path in result_file.parent.iterdir()) == [
            result_file.name
        ]
        status, stored = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and stored['id'] == 'kept', (status, stored)
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_result_with_a_surrogate_id_is_answered_and_the_bridge_survives(tmp):
    """A lone surrogate in a result's id must not drop the request unanswered.

    json.loads accepts "\\ud800"; the [RESULT] log line used to raise
    UnicodeEncodeError at the stdout encode, killing the request thread before
    any HTTP answer. The line now logs the value escaped, and the existing
    encoding guard below it answers 400.
    """
    with _util.bridge(tmp) as (base, docroot):
        raw_result = b'{"token":"httptok","id":"\\ud800","result":1}'
        try:
            status, raw = _util.request(
                base + '/result', 'POST', body=raw_result,
                headers={'Content-Type': 'application/json'})
            error = json.loads(raw).get('error')
        except http.client.RemoteDisconnected:
            status, error = 'dropped', None
        assert (status, error) == (400, 'result is not encodable'), (
            status, error)
        assert list((Path(docroot) / 'results').iterdir()) == []
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_a_result_survives_a_data_root_read_under_any_locale(tmp):
    """Stored JSON is UTF-8 on both sides, not whatever the machine prefers.

    Results are written as `json.dumps(..., ensure_ascii=False).encode()` —
    UTF-8 — and were read back with `Path.read_text()`, which decodes with
    the process locale. The two agree only where that locale is UTF-8. Under
    a C locale the read raises and the fetch answers 500; under a Windows
    code page it does not raise at all and quietly returns a DIFFERENT id,
    so a caller waiting for its own result waits until it times out.

    Forcing the child's locale reproduces the platform difference here
    rather than only on the runner that has it.
    """
    ascii_locale = {'LC_ALL': 'C', 'LANG': 'C', 'PYTHONCOERCECLOCALE': '0',
                    'PYTHONUTF8': '0'}
    wanted = 'shot&branch#caf\u00e9 \u4e16\u754c'
    with _util.bridge(tmp, env=ascii_locale) as (base, _docroot):
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': wanted,
            'result': wanted, 'error': None, 'ts': 1})
        assert status == 200, status
        status, got = _util.get_json(
            base + '/result?' + urllib.parse.urlencode(
                {'token': TOK, 'tab': 'extension'}))
        assert status == 200, (status, got)
        assert got['id'] == wanted, (repr(got.get('id')), repr(wanted))
        assert got['result'] == wanted, (repr(got.get('result')), repr(wanted))


def test_result_partial_temp_write_preserves_the_existing_slot(tmp):
    """A failed result write may dirty its temp file, never the live slot."""
    fault_dir = Path(tmp) / 'result-write-fault'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_write_bytes = pathlib.Path.write_bytes\n'
        'def _partial_result_write(path, data):\n'
        '    if path.parent.name == "results":\n'
        '        with path.open("wb") as handle:\n'
        '            handle.write(b\'{"partial":\')\n'
        '        raise OSError("injected partial result write")\n'
        '    return _real_write_bytes(path, data)\n'
        'pathlib.Path.write_bytes = _partial_result_write\n',
        encoding='utf-8')

    with _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)}) as (base, docroot):
        result_dir = Path(docroot) / 'results'
        result_file = result_dir / f'{TOK}.json'
        original = json.dumps({
            'token': TOK,
            'id': 'kept',
            'result': 'safe',
            'resultGeneration': 'kept-generation',
        }).encode()
        result_file.write_bytes(original)

        status, raw = _util.request(
            base + '/result', 'POST',
            body={'token': TOK, 'id': 'replacement', 'result': 'new'})
        assert status == 500, (status, raw)
        assert json.loads(raw) == {'error': 'result storage failure'}, raw
        assert result_file.read_bytes() == original
        assert sorted(path.name for path in result_dir.iterdir()) == [result_file.name]
        status, stored = _util.get_json(base + f'/result?token={TOK}')
        assert status == 200 and stored['id'] == 'kept', (status, stored)
        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def _replace_fault(tmp, name, failures):
    """A bridge whose os.replace refuses result-slot publishes `failures` times.

    Windows refuses a replace while any handle is open on the target, and the
    handle need not belong to the bridge. That cannot be produced on demand on
    a POSIX runner, so the sharing violation it raises is injected instead:
    what is under test is what the bridge does about it, not the platform.
    """
    fault_dir = Path(tmp) / name
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import os\n'
        '_real_replace = os.replace\n'
        '_left = [' + str(failures) + ']\n'
        'def _sharing_violation(src, dst, **kw):\n'
        '    if os.path.basename(str(dst)).startswith("' + TOK + '"):\n'
        '        if _left[0]:\n'
        '            _left[0] -= 1\n'
        '            raise PermissionError(32, "injected sharing violation")\n'
        '    return _real_replace(src, dst, **kw)\n'
        'os.replace = _sharing_violation\n',
        encoding='utf-8')
    return {'PYTHONPATH': str(fault_dir)}


def test_a_result_write_survives_a_transient_replace_failure(tmp):
    """A replace that fails once and then works must not lose the result.

    The slot is published by replacing it, and on Windows that replace fails
    for as long as anything else holds the target open. Answering 500 for a
    write that was about to succeed discards a result the extension already
    computed and reported.
    """
    env = _replace_fault(tmp, 'transient-replace-fault', 1)
    with _util.bridge(tmp, env=env) as (base, docroot):
        status, payload = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'retried', 'id': 'kept', 'result': 'value'})
        assert status == 200 and payload == {'ok': True}, (status, payload)
        slot = Path(docroot) / 'results' / f'{TOK}_retried.json'
        assert slot.is_file(), sorted((Path(docroot) / 'results').iterdir())
        stored = json.loads(slot.read_text(encoding='utf-8'))
        assert stored['result'] == 'value', stored


def test_a_replace_that_never_clears_is_still_answered(tmp):
    """The retry is bounded: a violation that never clears is not waited on.

    A refusal that is permanent - a read-only volume, a full disk - is not
    going to start working, and a bridge that kept retrying would hold the
    result lock instead of answering.
    """
    env = _replace_fault(tmp, 'permanent-replace-fault', 1000)
    with _util.bridge(tmp, env=env) as (base, _docroot):
        status, payload = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'refused', 'id': 'lost', 'result': 'value'})
        assert status == 500, (status, payload)
        assert payload == {'error': 'result storage failure'}, payload


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='bridgeresults_')


if __name__ == '__main__':
    raise SystemExit(main())
