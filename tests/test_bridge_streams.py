#!/usr/bin/env python3
"""Commands into the queue, and the SSE stream that drains it.

`PUT /command` writes a file into a per-target directory queue and the stream
delivers it exactly once, so these tests drive both halves together: what the
enqueue writes, what the stream frames, what it does with an entry it cannot
read, and what the queue does with one nobody claimed.
"""
import http.client
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _util  # noqa: E402
from _bridge import (BRIDGE_ENV, TOK,  # noqa: E402
                     _wait_for_delivery_health,
                     assert_oversize_stream_matches_enqueue,
                     framer, next_stream_data, put_command, queue_files,
                     read_stream_data, stream_response)
from _gc_handshake import (  # noqa: E402
    _GC_DONE, _GC_PREFIX, _GC_REFUSED_CHILD, _GC_TRIGGER,
    _expected_refusal_counts, _on_demand_command_gc, _refusal_report,
    _refuse_marker_operations, _routed_marker_operations)
from daedalus_bridge import atomic_file  # noqa: E402


def test_put_command_broadcast_writes_queue_file(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, body = put_command(
            base, {'token': TOK, 'id': 'c1', 'code': '1+1'})
        assert status == 200, (status, body)
        body = json.loads(body)
        assert body['ok'] is True
        assert body['target'] == 'broadcast', body
        files = queue_files(docroot, TOK)
        assert len(files) == 1, files
        data = json.loads(files[0].read_text(encoding='utf-8'))
        assert data['id'] == 'c1' and data['code'] == '1+1'
        assert data['_did'] == body['did'], (data, body)
        assert 'token' not in data  # routing fields stay out of the payload


def test_put_command_per_tab_goes_to_tab_queue(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, body = put_command(
            base, {'token': TOK, 'id': 'c2', 'code': '2+2', 'tab': 'tab1'})
        assert status == 200, (status, body)
        body = json.loads(body)
        assert body['target'] == 'tab=tab1', body
        files = queue_files(docroot, f'{TOK}_tab1')
        assert len(files) == 1, files
        data = json.loads(files[0].read_text(encoding='utf-8'))
        assert data['id'] == 'c2'
        assert 'tab' not in data and 'token' not in data  # routing-only fields
        # Nothing landed in the broadcast queue.
        assert queue_files(docroot, TOK) == []


def test_put_command_derived_queue_name_byte_boundary(tmp):
    """Derived command queue names honor the component byte ceiling."""
    token = '123e4567-e89b-12d3-a456-426614174000'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token}) as (
                base, docroot):
        boundary_tab = 't' * 203
        status, body = put_command(
            base, {'token': token, 'tab': boundary_tab,
                   'id': 'boundary', 'code': '1'})
        assert status == 200, (status, body)
        assert len(queue_files(docroot, f'{token}_{boundary_tab}')) == 1

        status, body = put_command(
            base, {'token': token, 'tab': 't' * 240,
                   'id': 'overflow', 'code': '2'})
        assert status == 400, (status, body)
        assert json.loads(body)['error'] == 'invalid path component', body

        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


def test_put_command_fifo_order(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        for i in range(3):
            status, _ = put_command(
                base, {'token': TOK, 'id': f'c{i}', 'code': str(i)})
            assert status == 200, status
        files = queue_files(docroot, TOK)
        assert len(files) == 3, files
        # Lexical filename order is enqueue order (ms + counter stem), and the
        # delivery ids sort the same way.
        ids = [json.loads(f.read_text(encoding='utf-8'))['id'] for f in files]
        assert ids == ['c0', 'c1', 'c2'], ids
        dids = [json.loads(f.read_text(encoding='utf-8'))['_did']
                for f in files]
        assert dids == sorted(dids), dids


def test_put_command_validation(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, _ = put_command(base, {'token': TOK, 'code': '1'})  # no id
        assert status == 400, status
        status, _ = put_command(
            base, {'token': TOK, 'id': 'x'})  # no code/type
        assert status == 400, status
        # A type alone is a valid (extension) command.
        status, _ = put_command(
            base, {'token': TOK, 'id': 'x', 'type': 'screenshot'})
        assert status == 200, status
        for bad in ('a/b', 'a.b', '..', ''):
            status, _ = put_command(
                base, {'token': bad, 'id': 'x', 'code': '1'})
            assert status == 400, (bad, status)
        # The rejected tokens created no queue directories.
        names = [p.name for p in (Path(docroot) / 'commands').iterdir()]
        assert names == [TOK], names


def test_unencodable_command_body_names_the_body_not_the_path(tmp):
    """A surrogate in a queued command's body is an encoding failure, not a
    path one; the refused enqueue leaves no artifact, hidden temp included."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, raw = put_command(
            base, {'token': TOK, 'id': 'enc', 'code': '\ud800'})
        assert status == 400, (status, raw)
        assert json.loads(raw)['error'] == 'command is not encodable', raw
        qdir = Path(docroot) / 'commands' / TOK
        entries = list(qdir.iterdir()) if qdir.is_dir() else []
        assert entries == [], entries
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_command_enqueue_and_dashboard_read_errors_are_answered(tmp):
    """Pre-response command and dashboard failures return storage errors."""
    fault_dir = Path(tmp) / 'boundary-read-write-faults'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_mkdir = pathlib.Path.mkdir\n'
        '_real_read_bytes = pathlib.Path.read_bytes\n'
        'def _fail_command_mkdir(path, *args, **kwargs):\n'
        '    if path.parent.name == "commands":\n'
        '        raise OSError("injected command enqueue failure")\n'
        '    return _real_mkdir(path, *args, **kwargs)\n'
        'def _fail_dashboard_read(path):\n'
        '    if path.parent.name == "dashboard":\n'
        '        raise OSError("injected dashboard read failure")\n'
        '    return _real_read_bytes(path)\n'
        'pathlib.Path.mkdir = _fail_command_mkdir\n'
        'pathlib.Path.read_bytes = _fail_dashboard_read\n',
        encoding='utf-8')
    with _util.bridge(
            tmp, env={**BRIDGE_ENV,
                      'PYTHONPATH': str(fault_dir)}) as (base, _docroot):
        try:
            command_status, command_raw = put_command(
                base, {'token': TOK, 'id': 'fault', 'code': '1'})
        except http.client.RemoteDisconnected as exc:
            raise AssertionError('a command storage error ended PUT') from exc
        assert command_status == 500, (command_status, command_raw)
        assert json.loads(command_raw) == {'error': 'command storage failure'}

        try:
            dashboard_status, dashboard_raw = _util.get(base + '/dashboard')
        except http.client.RemoteDisconnected as exc:
            raise AssertionError('a dashboard read error ended GET') from exc
        assert dashboard_status == 500, (dashboard_status, dashboard_raw)
        assert json.loads(dashboard_raw) == {
            'error': 'dashboard storage failure'}

        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)
        status, body = _util.post_json(
            base + '/sync-tabs', {'token': TOK, 'tabs': []})
        assert status == 200 and body == {'ok': True, 'count': 0}, (
            status, body)


# How far outside the TTL window each arm stamps, on opposite sides. A stall
# in the handshake has to exceed this before it can move either verdict, so
# the shipped comparison is settled by the sign of its difference.
_STAMP_LEASH = 3600


def _sweep(command_root, served):
    """Run one sweep in the bridge child and return the root it left.

    The list is the sweep's own record, written by the sweep after it ran,
    so a trigger answered early cannot stand in for one that finished.
    """
    done = command_root / _GC_DONE
    if done.exists():
        atomic_file.unlink_retrying(done)
    atomic_file.write_text_retrying(command_root / _GC_TRIGGER, '')
    deadline = time.time() + 10
    while not done.exists() and time.time() < deadline:
        time.sleep(0.01)
    assert done.exists(), (
        'the controlled command sweep did not finish: ' + ''.join(served))
    return atomic_file.read_text_retrying(
        done, encoding='utf-8').split()


def _root_names(command_root):
    """Every entry in the command root but the fixture's sweep markers."""
    return sorted(p.name for p in command_root.iterdir()
                  if not p.name.startswith(_GC_PREFIX))


def _stamp(queues, when):
    """Put every queued command's own mtime at `when`.

    The collector decides by comparing that mtime's distance from its own
    clock to the TTL, so an age inside the window is a boundary assertion
    that a stall the size of the window crosses.
    """
    for queue in queues:
        for command_file in queue.iterdir():
            os.utime(command_file, (when, when))


def test_expired_command_namespaces_are_collected_without_a_consumer(tmp):
    """The command TTL applies even when no SSE stream ever drains a queue."""
    served = []
    env = {**BRIDGE_ENV, 'DAEDALUS_CMD_TTL': '1',
           'PYTHONPATH': _on_demand_command_gc(
               Path(tmp) / 'on-demand-command-gc')}
    with _util.bridge(tmp, env=env, output=served) as (base, docroot):
        tabs = [f'abandoned{index}' for index in range(4)]
        for index, tab in enumerate(tabs):
            status, body = put_command(
                base, {'token': TOK, 'tab': tab,
                       'id': f'c{index}', 'code': '1'})
            assert status == 200, (status, body)
        # A second command in one namespace, so a one-child sweep shows.
        status, body = put_command(
            base, {'token': TOK, 'tab': tabs[0], 'id': 'c4', 'code': '1'})
        assert status == 200, (status, body)

        command_root = Path(docroot) / 'commands'
        queues = [command_root / f'{TOK}_{tab}' for tab in tabs]
        assert _root_names(command_root) == sorted(q.name for q in queues), (
            _root_names(command_root), ''.join(served))

        now = time.time()
        _stamp(queues, now + _STAMP_LEASH)
        kept = _sweep(command_root, served)
        # The root beside the record says which route a failure took: four
        # namespaces still there is the record read early, none of them is
        # the sweep having reaped work inside the TTL.
        assert kept == sorted(q.name for q in queues), (
            kept, _root_names(command_root), ''.join(served))

        _stamp(queues, now - _STAMP_LEASH)
        taken = _sweep(command_root, served)
        assert taken == [], (taken, ''.join(served))
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_collector_thread_uses_configured_ttl_for_one_sweep(tmp):
    """The collector keeps fresh work and expires work past the exact TTL."""
    fault_dir = Path(tmp) / 'controlled-command-gc'
    env = {**BRIDGE_ENV, 'DAEDALUS_CMD_TTL': '10',
           'PYTHONPATH': _on_demand_command_gc(fault_dir)}
    served = []
    with _util.bridge(tmp, env=env, output=served) as (_base, docroot):
        command_root = Path(docroot) / 'commands'
        queue = command_root / TOK
        queue.mkdir()
        fresh = queue / 'fresh.json'
        expired = queue / 'expired.json'
        fresh.write_text('{"id":"fresh"}', encoding='utf-8')
        expired.write_text('{"id":"expired"}', encoding='utf-8')
        now = time.time()
        os.utime(fresh, (now + _STAMP_LEASH, now + _STAMP_LEASH))
        os.utime(expired, (now - 15, now - 15))
        _sweep(command_root, served)
        assert fresh.exists(), 'configured TTL expired a fresh command'
        assert not expired.exists(), expired


def test_collector_sweep_survives_a_transient_sharing_violation(tmp):
    """The controlled sweep completes while both sides are refused."""
    # A count of 2 clears the retry budget with attempts still in hand, while
    # an unfixed read or replace raises on attempt one and reaches no second.
    refusals = 2
    sweeps = 2
    fault_dir = Path(tmp) / 'refusing-command-gc'
    env = {**BRIDGE_ENV, 'DAEDALUS_CMD_TTL': '10',
           'PYTHONPATH': _on_demand_command_gc(fault_dir, refusals=refusals)}
    served = []
    with _util.bridge(tmp, env=env, output=served) as (_base, docroot):
        command_root = Path(docroot) / 'commands'
        queue = command_root / TOK
        queue.mkdir()
        fresh = queue / 'fresh.json'
        expired = queue / 'expired.json'
        fresh.write_text('{"id":"fresh"}', encoding='utf-8')
        expired.write_text('{"id":"expired"}', encoding='utf-8')
        now = time.time()
        os.utime(fresh, (now + _STAMP_LEASH, now + _STAMP_LEASH))
        os.utime(expired, (now - 15, now - 15))
        with _refuse_marker_operations(command_root, refusals) as parent_log:
            # More than one, so a later sweep finds a record and the parent's
            # unlink of it runs at all.
            for _ in range(sweeps):
                _sweep(command_root, served)
        assert fresh.exists(), 'configured TTL expired a fresh command'
        assert not expired.exists(), expired
    # A wrong set means a routing lost its refusal or one fired where the
    # handshake does not: non-emptiness per side would read either as a pass.
    # The counts carry what the set cannot -- a set is filled by the first
    # sweep alone, so a sweep that stopped being refused would not show.
    observed, counts = _refusal_report(
        parent_log, command_root / _GC_REFUSED_CHILD)
    assert observed == _routed_marker_operations(), sorted(observed)
    assert counts == _expected_refusal_counts(refusals, sweeps), counts


def test_stream_derived_queue_name_matches_command_enqueue(tmp):
    token = '123e4567-e89b-12d3-a456-426614174000'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token}) as (
                base, _docroot):
        boundary_tab = 't' * 203
        status, body = put_command(
            base, {'token': token, 'tab': boundary_tab,
                   'id': 'boundary-stream', 'code': '1'})
        assert status == 200, (status, body)
        streamed = read_stream_data(base, token, boundary_tab)
        assert streamed['id'] == 'boundary-stream', streamed

        assert_oversize_stream_matches_enqueue(base)


def test_stream_modes_deliver_end_to_end(tmp):
    tab_token = 'stream-tab'
    with _util.bridge(
            Path(tmp) / 'tab',
            env={'TOKEN': '', 'DAEDALUS_TOKEN': tab_token}) as (
                base, _docroot):
        status, body = put_command(
            base, {'token': tab_token, 'tab': 'tab1',
                   'id': 'tab', 'code': '1'})
        assert status == 200, (status, body)
        assert read_stream_data(base, tab_token, 'tab1')['id'] == 'tab'

    dashboard_token = 'stream-dashboard'
    env = {'TOKEN': '', 'DAEDALUS_TOKEN': dashboard_token}
    with _util.bridge(Path(tmp) / 'dashboard', env=env) as (base, _docroot):
        status, body = _util.post_json(base + '/result', {
            'token': dashboard_token, 'tabId': '7', 'id': 'dashboard-result',
            'result': 'FORGED', 'error': None, 'world': 'page:cdp',
        })
        assert status == 200, (status, body)
        dashboard = read_stream_data(base, dashboard_token, 'dashboard')
        assert (dashboard['kind'] == 'event'
                and dashboard['type'] == 'result'), \
            dashboard
        assert dashboard['world'] == 'page:cdp', dashboard

    extension_token = 'stream-extension'
    env = {'TOKEN': '', 'DAEDALUS_TOKEN': extension_token}
    with _util.bridge(Path(tmp) / 'extension', env=env) as (base, _docroot):
        status, body = put_command(
            base, {'token': extension_token, 'tab': 'extension',
                   'id': 'extension', 'type': 'screenshot'})
        assert status == 200, (status, body)
        assert read_stream_data(
            base, extension_token, 'extension')['id'] == 'extension'

    broadcast_token = 'stream-broadcast'
    env = {'TOKEN': '', 'DAEDALUS_TOKEN': broadcast_token}
    with _util.bridge(Path(tmp) / 'broadcast', env=env) as (base, _docroot):
        status, body = put_command(
            base, {'token': broadcast_token, 'id': 'broadcast', 'code': '2'})
        assert status == 200, (status, body)
        assert read_stream_data(base, broadcast_token)['id'] == 'broadcast'


def test_a_lost_command_ends_the_read_instead_of_riding_keepalives(tmp):
    """An undelivered command must fail its reader, not outlive the suite.

    The stream's keepalives reset the connection's socket timeout and arrive
    more often than it, so a reader bounded only by the socket waits exactly
    as long as the bridge stays healthy: a lost command hangs, undiagnosed.
    """
    outcome = []
    with _util.bridge(
            tmp, env={**BRIDGE_ENV,
                      'DAEDALUS_STREAM_KEEPALIVE': '1'}) as (base, _docroot):
        def read():
            try:
                read_stream_data(base, TOK, 'nothing-is-sent-here', timeout=3)
                outcome.append('a data frame arrived on an idle stream')
            except AssertionError as failure:
                outcome.append(str(failure))

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout=20)
        assert not reader.is_alive(), 'the read is still riding keepalives'
    assert outcome and 'within 3 seconds' in outcome[0], outcome


def test_stream_drops_a_non_object_queue_entry(tmp):
    """A JSON value without command fields cannot terminate queue draining."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        qdir = Path(docroot) / 'commands' / TOK
        qdir.mkdir()
        malformed = qdir / '0000000000000_000000.json'
        malformed.write_text('[]', encoding='utf-8')
        status, body = put_command(
            base, {'token': TOK, 'id': 'after-malformed', 'code': '1'})
        assert status == 200, (status, body)

        delivered = read_stream_data(base, TOK)
        assert delivered['id'] == 'after-malformed', delivered
        assert not malformed.exists(), (
            'the non-object queue entry was not dropped')


def test_stream_survives_a_surrogate_id_in_a_queued_command(tmp):
    """A queued command whose id holds a lone surrogate must not kill the
    stream.

    The SSE frame escapes the surrogate (json.dumps defaults); the DELIVERED
    log line then raised UnicodeEncodeError and tore the stream down.
    """
    served = []
    with _util.bridge(tmp, output=served, env=BRIDGE_ENV) as (base, docroot):
        conn, response = stream_response(base, TOK, tab='extension')
        frame = framer(response, served)
        try:
            assert response.status == 200, response.status
            qdir = Path(docroot) / 'commands' / TOK
            qdir.mkdir(parents=True)
            (qdir / '0000000000001_000001.json').write_bytes(
                b'{"id":"\\ud800","code":"1"}')
            first = frame('the queued command with a surrogate id')
            assert first.get('code') == '1', first
            status, _ = put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            second = frame('the command enqueued afterwards')
            assert second.get('id') == 'after', second
        finally:
            response.close()
            conn.close()
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_stream_survives_an_undecodable_byte_in_a_dropped_name(tmp):
    """A raw-dropped NAME with an undecodable byte must not kill the stream.

    iterdir() decodes filesystem bytes with surrogateescape, so a dropped
    file or queue directory named with a raw byte arrives as '\\udcff…';
    where sys.stdout.errors is strict (PYTHONIOENCODING=utf-8:strict forces
    it here, because this box's C.UTF-8 stdio would mask it), the DELIVERED
    log line used to raise UnicodeEncodeError and tear the stream down.
    """
    _util.require_undecodable_names(tmp)
    strict = {**BRIDGE_ENV, 'PYTHONIOENCODING': 'utf-8:strict'}
    # The bridge's own log goes into every failure here, and its DELIVERED
    # lines are the only direct evidence of whether the drain saw the file.
    served = []
    with _util.bridge(tmp, env=strict, output=served) as (base, docroot):
        conn, response = stream_response(base, TOK, tab='extension')
        frame = framer(response, served)
        try:
            assert response.status == 200, response.status
            commands = os.fsencode(Path(docroot) / 'commands')
            # A legacy raw-write file whose own name carries the raw byte.
            with open(commands + b'/' + os.fsencode(TOK) + b'_\xfftab.json',
                      'wb') as handle:
                handle.write(b'{"id":"legacybad","code":"1"}')
            first = frame('the legacy dropped file')
            assert first.get('id') == 'legacybad', first
            # A queue directory whose name carries the raw byte.
            bad_dir = commands + b'/' + os.fsencode(TOK) + b'_\xffdir'
            os.mkdir(bad_dir)
            with open(bad_dir + b'/0000000000001_000001.json', 'wb') as handle:
                handle.write(b'{"id":"qbad","code":"1"}')
            second = frame('the queued command under a dropped name')
            assert second.get('id') == 'qbad', second
            status, _ = put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            third = frame('the command enqueued afterwards')
            assert third.get('id') == 'after', third
        finally:
            response.close()
            conn.close()
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_stream_survives_an_undecodable_byte_in_an_expired_queue_entry(tmp):
    """The TTL-DROP log line takes the same raw name and must not kill the
    stream."""
    _util.require_undecodable_names(tmp)
    strict = {**BRIDGE_ENV, 'PYTHONIOENCODING': 'utf-8:strict'}
    served = []
    with _util.bridge(tmp, env=strict, output=served) as (base, docroot):
        qdir = Path(docroot) / 'commands' / TOK
        qdir.mkdir(parents=True)
        stale = os.fsencode(qdir) + b'/\xffexpired.json'
        with open(stale, 'wb') as handle:
            handle.write(b'{"id":"stale"}')
        os.utime(stale, (0, 0))  # far past CMD_TTL; the GC's first pass is
        # 30s after bridge start, so the stream's first drain sees this file.
        conn, response = stream_response(base, TOK, tab='extension')
        read = framer(response, served)
        try:
            assert response.status == 200, response.status
            status, _ = put_command(
                base, {'token': TOK, 'id': 'after', 'code': '2'})
            assert status == 200, status
            delivered = read('the command enqueued after the expired entry')
            assert delivered.get('id') == 'after', delivered
        finally:
            response.close()
            conn.close()
        deadline = time.time() + 5
        while os.path.exists(stale) and time.time() < deadline:
            time.sleep(0.01)
        assert not os.path.exists(stale), 'the expired entry was not dropped'
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_queue_publication_never_deletes_an_in_progress_write(tmp):
    """A queue scan must not unlink the pathname from under its writer."""
    served = []
    with _util.bridge(tmp, output=served, env=BRIDGE_ENV) as (base, docroot):
        queue = Path(docroot) / 'commands' / TOK
        queue.mkdir(parents=True)
        partial = queue / '0000000000001_000001.json'
        ready = queue / '0000000000002_000002.json'
        writer = open(partial, 'w', encoding='utf-8')
        conn = response = None
        try:
            writer.write('{"id":"held-open"')
            writer.flush()
            os.fsync(writer.fileno())
            ready.write_text(
                '{"id":"scan-witness","code":"first"}', encoding='utf-8')

            conn, response = stream_response(base, TOK, tab='extension')
            assert response.status == 200, response.status
            frame = framer(response, served)
            witness = frame('the complete file behind the partial one')
            assert witness.get('id') == 'scan-witness', witness
            assert partial.exists(), 'the queue scan unlinked a visible file' \
                ' while its writer was open'

            writer.write(',"code":"second"}')
            writer.flush()
            os.fsync(writer.fileno())
            writer.close()
            delivered = frame('the completed formerly-partial file', timeout=5)
            assert delivered.get('id') == 'held-open', delivered
        finally:
            if not writer.closed:
                writer.close()
            if response is not None:
                response.close()
            if conn is not None:
                conn.close()


def test_a_stream_timeout_carries_the_bridges_own_log(tmp):
    """The diagnostic has to fire, or it reads as absent evidence.

    #23's fifth sighting produced nothing but "no data frame arrived" because
    the capture existed on one test in the family and not its siblings. A
    capture that is wired but silent looks the same from a failure report,
    so this drives a real timeout and reads what comes out.
    """
    served = []
    with _util.bridge(tmp, output=served, env=BRIDGE_ENV) as (base, _docroot):
        conn, response = stream_response(base, TOK, tab='extension')
        read = framer(response, served)
        message = ''
        try:
            try:
                read('a command nobody ever sent', timeout=1)
            except AssertionError as failure:
                message = str(failure)
        finally:
            response.close()
            conn.close()
    assert message, 'the read returned instead of timing out'
    assert 'a command nobody ever sent' in message, message
    assert 'no data frame arrived within 1 seconds' in message, message
    # The fixture cannot return before the announcement it reads the port
    # from, so this line is in `served` whatever else the bridge has logged.
    assert 'the bridge said: ' in message, message
    assert '[Daedalus] Listening on 127.0.0.1:' in message, message


def test_a_replaced_stream_reports_its_own_end(tmp):
    """The closure diagnosis survives the cleanup that runs after it."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        first_conn, first = stream_response(base, TOK, tab='samet')
        second_conn, second = stream_response(base, TOK, tab='samet')
        try:
            next_stream_data(first)
        except AssertionError as failure:
            assert ('the stream closed before the next frame' in str(failure)
                    or 'the stream died before the next frame'
                    in str(failure)), failure
        else:
            raise AssertionError('the closure went undiagnosed')
        finally:
            second.close()
            second_conn.close()
            first.close()
            first_conn.close()


def test_queue_delivery_updates_the_health_clock(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        conn, response = stream_response(base, TOK, tab='extension')
        try:
            status, health = _util.get_json(base + '/health')
            assert status == 200, (status, health)
            assert health['last_delivery_s_ago'] is None, health

            status, body = put_command(
                base, {'token': TOK, 'tab': 'extension',
                       'id': 'queue-clock', 'code': '1'})
            assert status == 200, (status, body)
            delivered = next_stream_data(response)
            assert delivered['id'] == 'queue-clock', delivered

            _wait_for_delivery_health(base)
        finally:
            response.close()
            conn.close()


def test_health_counts_a_stream_that_named_no_tab(tmp):
    """A stream with no tab selector is still a stream.

    It got no entry at all, so it served commands and held a request worker
    while /health reported zero — and the count it reported was the number of
    distinct tab NAMES, so two streams sharing a name counted once.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['active_streams'] == 0, health

        tabless_conn, tabless = stream_response(base, TOK)
        try:
            assert tabless.status == 200, tabless.status
            _util.request(base + '/command', 'PUT',
                          body={'token': TOK, 'id': 'wake', 'code': '1'})
            assert next_stream_data(tabless).get('id') == 'wake'
            status, health = _util.get_json(base + '/health')
            assert status == 200 and health['active_streams'] == 1, health
            assert health['stream_tabs'] == [''], health

            named_conn, named = stream_response(base, TOK, tab='extension')
            try:
                assert named.status == 200, named.status
                deadline = time.time() + 10
                while True:
                    status, health = _util.get_json(base + '/health')
                    if health['active_streams'] == 2:
                        break
                    assert time.time() < deadline, health
                assert health['stream_tabs'] == ['', 'extension'], health
            finally:
                named.close()
                named_conn.close()
        finally:
            tabless.close()
            tabless_conn.close()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='bridgestreams_')


if __name__ == '__main__':
    raise SystemExit(main())
