"""Per-delivery result storage: independent consumability, dedup, eviction."""
import json
import os
import threading
import time
import urllib.parse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bridge import BRIDGE_ENV, TOK, _patch_env, _util  # noqa: E402


def test_distinct_delivery_results_are_independently_consumable(tmp):
    """Each delivery id keeps its own result instead of sharing the slot."""
    first_did = '1700000000000_000001'
    second_did = '1700000000001_000002'
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
        'import pathlib\n'
        'import sys;sys.path.insert(0,".");'
        'from daedalus_bridge import result_store\n'
        'import os\n'
        'import threading\n'
        'import time\n'
        'gate = pathlib.Path(os.environ["SPIN_GATE_DIR"])\n'
        'def install():\n'
        '    while not hasattr(result_store, "read_result_file"):\n'
        '        time.sleep(0.001)\n'
        '    real_read = result_store.read_result_file\n'
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
        '    result_store.read_result_file = spinning_read\n'
        '    (gate / "ready").write_text("ready", encoding="utf-8")\n'
        'threading.Thread(target=install, daemon=True).start()\n',
        encoding='utf-8')
    env = _patch_env(patch_dir, SPIN_GATE_DIR=str(patch_gate))
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
        'import pathlib\n'
        'import sys;sys.path.insert(0,".");'
        'from daedalus_bridge import result_store\n'
        'import os\n'
        'import threading\n'
        'import time\n'
        'gate = pathlib.Path(os.environ["SPIN_GATE_DIR"])\n'
        'def install():\n'
        '    while not hasattr(result_store, "read_result_file"):\n'
        '        time.sleep(0.001)\n'
        '    real_read = result_store.read_result_file\n'
        '    state = {"reads": 0}\n'
        '    def spinning_read(path, consume, expected):\n'
        '        state["reads"] += 1\n'
        '        if not consume:\n'
        '            return {"deliveryId": "churn-" + str(state["reads"]),\n'
        '                    "resultGeneration": "churn-generation"}, ""\n'
        '        return real_read(path, consume, expected)\n'
        '    result_store.read_result_file = spinning_read\n'
        '    (gate / "ready").write_text("ready", encoding="utf-8")\n'
        'threading.Thread(target=install, daemon=True).start()\n',
        encoding='utf-8')
    env = _patch_env(patch_dir, SPIN_GATE_DIR=str(patch_gate))
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
    """The per-tab delivery store retains only its configured newest
    results."""
    dids = [f'170000000000{i}_00000{i}' for i in (1, 2, 3)]
    env = {**BRIDGE_ENV, 'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}
    with _util.bridge(tmp, env=env) as (base, docroot):
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
    env = {**BRIDGE_ENV, 'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}
    with _util.bridge(tmp, env=env) as (base, docroot):
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
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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
        '        (gate / "cleanup-read").write_text('
        '"read", encoding="utf-8")\n'
        '        while not (gate / "release-cleanup").exists():\n'
        '            time.sleep(0.01)\n'
        '    return real_unlink(path, *args, **kwargs)\n'
        'pathlib.Path.unlink = gated_unlink\n',
        encoding='utf-8')

    env = _patch_env(patch_dir, CLEANUP_GATE_DIR=str(gate_dir))
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


def test_delivery_stamp_survives_restart_with_an_earlier_wall_clock(tmp):
    """A persisted future stamp keeps a new post from immediate eviction."""
    tab = 'restart-clock'
    dids = ('old-a', 'old-b')
    env = {**BRIDGE_ENV, 'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}
    with _util.bridge(tmp, env=env) as (base, docroot):
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

    env = {**BRIDGE_ENV, 'DAEDALUS_MAX_DELIVERY_RESULTS': '2'}
    with _util.bridge(tmp, env=env) as (base, _docroot):
        status, body = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': tab, 'id': 'new-after-restart',
            'result': 'new-after-restart', 'error': None, 'ts': 2,
            '_did': 'new-after-restart'})
        assert status == 200 and body == {'ok': True}, (status, body)
        params = {'token': TOK, 'tab': tab,
                  'delivery': 'new-after-restart'}
        query = base + '/result?' + urllib.parse.urlencode(params)
        status, result = _util.get_json(query)
        assert status == 200 and result.get('id') == 'new-after-restart', (
            result)


def test_failed_delivery_stamp_skips_eviction_with_distinct_stamps(tmp):
    """A failed stamp leaves distinct persisted mtimes untouched."""
    patch_dir = Path(tmp) / 'utime-patch'
    patch_dir.mkdir()
    patch_gate = Path(tmp) / 'utime-gate'
    patch_gate.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        'import os\n'
        'import pathlib\n'
        'import sys;sys.path.insert(0,".");'
        'from daedalus_bridge import result_store\n'
        'import threading\n'
        'import time\n'
        'gate = pathlib.Path(os.environ["UTIME_GATE_DIR"])\n'
        'def install():\n'
        '    while not hasattr(result_store, "mark_delivery_result"):\n'
        '        time.sleep(0.001)\n'
        '    def failed_utime(*_args, **_kwargs):\n'
        '        raise OSError("injected utime failure")\n'
        '    result_store.os.utime = failed_utime\n'
        '    (gate / "ready").write_text("ready", encoding="utf-8")\n'
        'threading.Thread(target=install, daemon=True).start()\n',
        encoding='utf-8')
    tab = 'utime-distinct'
    dids = ('zzzz-oldest', 'normal-middle', 'normal-newest')
    env = _patch_env(patch_dir, UTIME_GATE_DIR=str(patch_gate),
                     DAEDALUS_MAX_DELIVERY_RESULTS='2')
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
        '    if path.name == "zzzz-oldest.json" '
        'and "deliveries" in path.parts:\n'
        '        raise PermissionError("injected eviction unlink failure")\n'
        '    return real_unlink(path, *args, **kwargs)\n'
        'pathlib.Path.unlink = fail_oldest\n',
        encoding='utf-8')
    env = _patch_env(patch_dir, DAEDALUS_MAX_DELIVERY_RESULTS='2')
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
            raise AssertionError(
                'eviction failure dropped the response') from exc
        assert status == 200 and body == {'ok': True}, (status, body)


def test_compatibility_consume_removes_the_same_delivery_result(tmp):
    """Consuming a slot does not leave its delivery copy behind."""
    did = '1700000000000_000007'
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
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


def test_a_retried_result_never_replaces_a_newer_one(tmp):
    """A lost 200 makes the extension re-POST; that must not undo the next
    result.

    background.js retries a result POST up to three times on a transient
    failure, and a response lost after the server stored it looks exactly like
    one that never arrived. The retry carries the same delivery id, so the
    bridge can tell a repeat from a fresh result and leave both slots alone.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
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


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
