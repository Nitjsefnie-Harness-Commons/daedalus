#!/usr/bin/env python3
"""Fault controls for test-side command queue readers."""
import asyncio
import contextlib
import json
import math
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402
import _bridge  # noqa: E402
import _cmdqueue  # noqa: E402
from _cmdqueue_faults import (  # noqa: E402
    _disappear_on_first_open,
    _queued_file,
    _refuse_first_queue_read,
    _refuse_path_operation,
    _rewrite_on_first_read,
    _vanish_during_read,
    _vanish_during_unlink,
    _virtual_cmdqueue_clock,
)
import _overlap  # noqa: E402
import test_cli  # noqa: E402
import test_mcp_server  # noqa: E402


def test_a_transient_read_refusal_returns_the_queued_command(tmp):
    queue, queued = _queued_file(tmp)
    with _refuse_path_operation(queued, 'open', 1):
        command = _cmdqueue.wait_for_command(queue, timeout=1)
    assert command == {'id': 'queued', 'type': 'reload'}, command


def test_a_present_queue_file_outlives_its_finished_producer(tmp):
    queue, queued = _queued_file(tmp)
    with _refuse_path_operation(queued, 'open', 1):
        command = _cmdqueue.wait_for_command(
            queue, timeout=1, producer_alive=lambda: False)
    assert command == {'id': 'queued', 'type': 'reload'}, command


def test_observed_file_or_queue_loss_keeps_dead_producer_wait_bounded(tmp):
    timeout = 2.5 * _cmdqueue.POLL_DELAY

    def observed_wait(remove_queue):
        queue, queued = _queued_file(tmp)
        with _virtual_cmdqueue_clock() as (clock, _, origin):
            with _vanish_during_read(queued, clock, remove_queue):
                command = _cmdqueue.wait_for_command(
                    queue, timeout=timeout, producer_alive=lambda: False)
        assert command is None, command
        assert not remove_queue or not queue.exists(), queue
        return queue, clock.monotonic(), origin

    queue, observed_end, origin = observed_wait(False)
    with _virtual_cmdqueue_clock() as (clock, _, base):
        baseline = _cmdqueue.wait_for_command(queue, timeout=timeout)
    base_end = clock.monotonic()
    _queue, queue_end, queue_origin = observed_wait(True)
    assert baseline is None, baseline
    # The wait must reach its deadline and must not sleep an unclamped
    # polling delay past it. The tolerance is virtual-clock time, not wall
    # time, so it is deterministic: it sits far above the float drift a wait
    # that splits its final delay accumulates, and far below the shortfall an
    # unclamped final sleep leaves.
    tolerance = _cmdqueue.POLL_DELAY / 100
    endpoints = ((observed_end, origin), (base_end, base),
                 (queue_end, queue_origin))
    for end, start in endpoints:
        assert start + timeout <= end <= start + timeout + tolerance, (
            'wait did not end within its deadline', end, start, timeout)


def test_an_existing_empty_queue_lets_a_dead_producer_end_the_wait(tmp):
    timeout = 2.5 * _cmdqueue.POLL_DELAY
    queue = Path(tmp) / 'empty-queue'
    queue.mkdir()
    with _virtual_cmdqueue_clock() as (clock, _events, origin):
        command = _cmdqueue.wait_for_command(
            queue, timeout=timeout, producer_alive=lambda: False)
    end = clock.monotonic()
    assert command is None, 'genuinely empty queue returned a command'
    assert end < origin + timeout, (
        'genuinely empty queue ignored the dead producer',
        end, origin, timeout)


def test_an_ignored_only_queue_lets_a_dead_producer_end_the_wait(tmp):
    timeout = 2.5 * _cmdqueue.POLL_DELAY
    queue, ignored = _queued_file(tmp)
    with _virtual_cmdqueue_clock() as (clock, _events, origin):
        command = _cmdqueue.wait_for_command(
            queue, timeout=timeout, producer_alive=lambda: False,
            ignored_names={ignored.name})
    end = clock.monotonic()
    assert command is None, 'ignored-only queue returned a command'
    assert end < origin + timeout, (
        'ignored-only queue counted as an observed queue file',
        end, origin, timeout)


def test_eligible_file_wins_over_ignored_stale_with_dead_producer(tmp):
    queue, stale = _queued_file(tmp, '0000000000000_000000.json')
    current = queue / '1700000000000_000001.json'
    current.write_text(json.dumps({'id': 'current', 'type': 'reload'}),
                       encoding='utf-8')
    command = _cmdqueue.wait_for_command(
        queue, timeout=1, producer_alive=lambda: False,
        ignored_names={stale.name})
    assert command == {'id': 'current', 'type': 'reload'}, (
        'eligible command was hidden by stale-ignore/dead-producer state',
        command)


def test_a_queue_file_that_disappears_during_read_is_retried(tmp):
    queue, queued = _queued_file(tmp)
    with _disappear_on_first_open(queued):
        command = _cmdqueue.wait_for_command(queue, timeout=1)
    assert command == {'id': 'queued', 'type': 'reload'}, command


def test_wait_ignores_a_surviving_leftover_by_filename(tmp):
    queue, stale = _queued_file(tmp, '0000000000000_000000.json')
    current = queue / '1700000000000_000001.json'
    current.write_text(json.dumps({'id': 'current', 'type': 'reload'}),
                       encoding='utf-8')
    command = _cmdqueue.wait_for_command(
        queue, timeout=1, ignored_names={stale.name})
    assert command == {'id': 'current', 'type': 'reload'}, command


def test_a_transient_removal_refusal_still_clears_the_queue(tmp):
    queue, queued = _queued_file(tmp)
    with _refuse_path_operation(queued, 'unlink', 1):
        survivors = _cmdqueue.clear_command_queue(queue)
    assert survivors == set(), survivors
    assert list(queue.glob('*.json')) == []


def test_a_queue_file_already_gone_during_clear_is_not_an_error(tmp):
    queue, queued = _queued_file(tmp)
    with _vanish_during_unlink(queued):
        survivors = _cmdqueue.clear_command_queue(queue)
    assert survivors == set(), survivors
    assert list(queue.glob('*.json')) == []


def test_a_permanent_read_refusal_is_bounded(tmp):
    # A whole-multiple timeout keeps a second read per pass inside the bound.
    timeout = 2 * _cmdqueue.POLL_DELAY
    queue, queued = _queued_file(tmp)
    with _virtual_cmdqueue_clock() as (clock, events, origin):
        with _refuse_path_operation(queued, 'open', 1000, clock=clock):
            command = _cmdqueue.wait_for_command(
                queue, timeout=timeout)
    kinds = [kind for kind, _ in events]
    assert kinds and kinds[0] == 'read', ('no leading read', events)
    assert all(a != 'read' or b != 'read'
               for a, b in zip(kinds, kinds[1:])), events
    sleep_groups = []
    for kind, duration in events:
        if kind == 'read':
            sleep_groups.append([])
        else:
            assert kind == 'sleep' and sleep_groups, events
            assert duration <= _cmdqueue.POLL_DELAY, (duration, events)
            sleep_groups[-1].append(duration)
    assert sleep_groups and any(sleep_groups), events
    sleeps = [sum(group) for group in sleep_groups if group]
    assert command is None, command
    assert all(abs(duration - _cmdqueue.POLL_DELAY)
               <= math.ulp(_cmdqueue.POLL_DELAY)
               for duration in sleeps[:-1]), (sleeps, events)
    # An exact multiple can make the final sleep equal the polling delay.
    assert sleeps[-1] <= _cmdqueue.POLL_DELAY, (sleeps, events)
    deadline = origin + timeout
    actual_end = clock.monotonic()
    end = actual_end
    if events and events[-1][0] == 'read':
        # A final read may straddle the deadline after a decomposed delay;
        # pin the scheduling endpoint after proving it was already in flight.
        before_last_read = actual_end - events[-1][1]
        assert before_last_read <= deadline <= actual_end, (
            'final read did not straddle the deadline', actual_end, deadline,
            events)
        end = deadline
    # A wait may take its deadline as origin + timeout or as that value's
    # next representable successor; it must stop at whichever it chose.
    assert deadline <= end <= math.nextafter(deadline, math.inf), (
        end, origin, timeout, events)
    elapsed = end - origin
    assert abs(elapsed - timeout) <= math.ulp(deadline), (
        elapsed, timeout, origin, events)


def test_a_permanent_removal_refusal_returns_the_survivor(tmp):
    queue, queued = _queued_file(tmp)
    with _refuse_path_operation(queued, 'unlink', 1000) as calls:
        survivors = _cmdqueue.clear_command_queue(queue)
    assert calls[0] == _cmdqueue.UNLINK_ATTEMPTS, calls
    assert survivors == {queued.name}, survivors
    assert queued.is_file()


def test_wait_returns_none_when_the_timeout_expires(tmp):
    queue = Path(tmp) / 'missing-queue'
    assert _cmdqueue.wait_for_command(queue, timeout=0.01) is None


def test_wait_ends_early_when_the_producer_is_gone(tmp):
    queue = Path(tmp) / 'missing-queue'
    producer_calls = []

    def producer_alive():
        producer_calls.append(True)
        return False

    with _virtual_cmdqueue_clock(0) as (_clock, events, _origin):
        command = _cmdqueue.wait_for_command(
            queue, timeout=10, producer_alive=producer_alive)
    assert command is None, command
    assert producer_calls, producer_calls
    assert events == [], events


def test_the_cli_answer_helper_survives_a_transient_queue_read_refusal(tmp):
    bridge_env = {'DAEDALUS_TOKEN': test_cli.TOK, 'TOKEN': ''}
    with _util.bridge(tmp, env=bridge_env) as (base, docroot):
        env = test_cli.cli_env(DAEDALUS_URL=base,
                               DAEDALUS_TOKEN=test_cli.TOK)
        queue = (Path(docroot) / 'commands'
                 / f'{test_cli.TOK}_extension')
        with _refuse_first_queue_read(queue):
            code, out, err, queued = test_cli._answer_one_ext_command(
                base, docroot, ['ext-reload'], {}, env)
    assert code == 0, (code, out, err)
    assert queued['type'] == 'reload', queued


def test_the_mcp_answer_helper_survives_a_transient_queue_read_refusal(tmp):
    test_mcp_server._need_deps()
    bridge_env = {'DAEDALUS_TOKEN': test_mcp_server.TOK, 'TOKEN': '',
                  'DAEDALUS_MCP_PORT': '0'}
    with _util.bridge(tmp, env=bridge_env) as (base, docroot):
        mod = test_mcp_server._load_mcp(base)
        queue = (Path(docroot) / 'commands'
                 / f'{test_mcp_server.TOK}_extension')
        with _refuse_first_queue_read(queue):
            _value, queued = test_mcp_server._answer_mcp_command(
                base, docroot, mod, mod.ext_reload, {})
    assert queued['type'] == 'reload', queued


def test_a_refused_leftover_coalesces_the_identical_cli_retry(tmp):
    bridge_env = {'DAEDALUS_TOKEN': test_cli.TOK, 'TOKEN': ''}
    with _util.bridge(tmp, env=bridge_env) as (base, docroot):
        env = test_cli.cli_env(DAEDALUS_URL=base,
                               DAEDALUS_TOKEN=test_cli.TOK)
        # The unanswered first run leaves a live leftover carrying the exact
        # payload the retry sends; constructed leftovers repeatedly let
        # reader changes evade the real sender's shape.
        first = test_cli.run_cli(['ext-reload'], env)
        assert first.returncode != 0, (first.returncode, first.stdout)
        files = _bridge.queue_files(docroot, f'{test_cli.TOK}_extension')
        assert len(files) == 1, files
        stale = files[0]
        leftover = json.loads(stale.read_text(encoding='utf-8'))
        queue = stale.parent
        with _refuse_path_operation(stale, 'unlink', 1000) as calls:
            assert _cmdqueue.clear_command_queue(queue) == {stale.name}
            # The identical retry coalesces onto the live leftover instead
            # of enqueueing a second delivery, and completes through that
            # delivery's result: the waiter polls delivery=<leftover did>,
            # so a second delivery could never satisfy it.
            proc = subprocess.Popen(
                test_cli.CLI + ['ext-reload'], cwd=str(_util.ROOT),
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8')
            try:
                status, _ = _util.post_json(base + '/result', {
                    'token': test_cli.TOK, 'tabId': 'extension',
                    'id': leftover['id'], 'result': {},
                    'error': None, 'ts': 1, '_did': leftover['_did']})
                assert status == 200, status
                out, err = proc.communicate(timeout=60)
            finally:
                _drain.kill_and_drain(proc)
    assert calls[0] == _cmdqueue.UNLINK_ATTEMPTS, calls
    assert proc.returncode == 0, (proc.returncode, out, err)
    survivors = _bridge.queue_files(docroot, f'{test_cli.TOK}_extension')
    assert [path.name for path in survivors] == [stale.name], survivors


def test_a_refused_leftover_coalesces_the_identical_mcp_retry(tmp):
    test_mcp_server._need_deps()
    bridge_env = {'DAEDALUS_TOKEN': test_mcp_server.TOK, 'TOKEN': '',
                  'DAEDALUS_MCP_PORT': '0'}
    with _util.bridge(tmp, env=bridge_env) as (base, docroot):
        mod = test_mcp_server._load_mcp(base)
        qdir = Path(docroot) / 'commands' / f'{test_mcp_server.TOK}_extension'

        def first_call():
            # The token is a ContextVar, and a thread starts with a fresh
            # context (see _answer_mcp_command).
            mod._token.set(test_mcp_server.TOK)
            try:
                box1['error'] = asyncio.run(mod.ext_reload())
            except Exception as exc:
                box1['error'] = exc

        box1 = {}
        first = threading.Thread(target=first_call)
        first.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not list(qdir.glob('*.json')):
            time.sleep(0.02)
        files = _bridge.queue_files(
            docroot, f'{test_mcp_server.TOK}_extension')
        assert len(files) == 1, files
        first.join(30)
        assert isinstance(box1['error'], TimeoutError), box1
        stale = files[0]
        leftover = json.loads(stale.read_text(encoding='utf-8'))
        queue = stale.parent
        with _refuse_path_operation(stale, 'unlink', 1000) as calls:
            assert _cmdqueue.clear_command_queue(queue) == {stale.name}
            # The identical retry coalesces onto the live leftover: the
            # tool's poll pins expect_delivery to the leftover's did, so
            # only a coalesced PUT can be satisfied by the answer below.
            box = {}

            def retry():
                # The token is a ContextVar, and a thread starts with a
                # fresh context (see _answer_mcp_command).
                mod._token.set(test_mcp_server.TOK)
                try:
                    box['value'] = asyncio.run(mod.ext_reload())
                except Exception as exc:
                    box['error'] = exc

            worker = threading.Thread(target=retry)
            worker.start()
            try:
                status, _ = _util.post_json(base + '/result', {
                    'token': test_mcp_server.TOK, 'tabId': 'extension',
                    'id': leftover['id'], 'result': 'reloaded',
                    'error': None, 'ts': 1, '_did': leftover['_did']})
                assert status == 200, status
            finally:
                worker.join(30)
    assert calls[0] == _cmdqueue.UNLINK_ATTEMPTS, calls
    assert 'error' not in box, box
    survivors = _bridge.queue_files(
        docroot, f'{test_mcp_server.TOK}_extension')
    assert [path.name for path in survivors] == [stale.name], survivors


def test_a_transient_read_refusal_returns_every_queued_command(tmp):
    queue, first = _queued_file(tmp)
    second = queue / '1700000000001_000002.json'
    second.write_text(json.dumps({'id': 'second', 'type': 'reload'}),
                      encoding='utf-8')
    refusals = 2
    expected = [{'id': 'queued', 'type': 'reload'},
                {'id': 'second', 'type': 'reload'}]
    for refused_file in (first, second):
        with _refuse_path_operation(
                refused_file, 'read_text', refusals) as calls:
            commands = _cmdqueue.wait_for_commands(queue, 2, timeout=1)
        # Refusals are counted before any read succeeds, so a count past
        # them proves the refused path was read again after its refusals;
        # the freshness controls are the whole-set witness.
        assert calls[0] > refusals, (refusals, calls)
        assert commands == expected, commands


def test_the_overlap_command_wait_times_out_with_a_diagnostic(tmp):
    queue, _queued = _queued_file(tmp)
    original = _overlap._CLIENT_COMMAND_WAIT_S
    _overlap._CLIENT_COMMAND_WAIT_S = 0.1
    try:
        message = None
        try:
            _overlap._wait_for_client_commands(queue, 2)
        except AssertionError as failure:
            message = str(failure)
    finally:
        _overlap._CLIENT_COMMAND_WAIT_S = original
    assert message == 'timed out waiting for both same-id client commands', \
        message


def test_the_overlap_command_wait_survives_a_transient_read_refusal(tmp):
    queue, first = _queued_file(tmp)
    second = queue / '1700000000001_000002.json'
    second.write_text(json.dumps({'id': 'second', 'type': 'reload'}),
                      encoding='utf-8')
    refusals = 1
    expected = [{'id': 'queued', 'type': 'reload'},
                {'id': 'second', 'type': 'reload'}]
    for refused_file in (first, second):
        with _refuse_path_operation(
                refused_file, 'read_text', refusals) as calls:
            commands = _overlap._wait_for_client_commands(queue, 2)
        # Same bound as the multi-command control above.
        assert calls[0] > refusals, (refusals, calls)
        assert commands == expected, commands


def test_a_permanent_read_refusal_bounds_the_multi_command_wait(tmp):
    queue, first = _queued_file(tmp)
    second = queue / '1700000000001_000002.json'
    second.write_text(json.dumps({'id': 'second', 'type': 'reload'}),
                      encoding='utf-8')
    for refused_file in (first, second):
        with _refuse_path_operation(refused_file, 'read_text', 1000):
            commands = _cmdqueue.wait_for_commands(queue, 2, timeout=0.1)
        assert commands is None, commands


def _whole_set_retry_returns_the_rewrite(tmp, error):
    queue, first = _queued_file(tmp)
    second = queue / '1700000000001_000002.json'
    files = (first, second)
    stale = [{'id': 'stale-first', 'type': 'reload'},
             {'id': 'stale-second', 'type': 'reload'}]
    # Refusing each file in turn, with both rewritten at that moment, proves
    # the whole-set retry for either read order; per-round fresh ids stop a
    # set kept from an earlier call from passing.
    for refused_file in files:
        fresh = [{'id': f'fresh-first-{refused_file.name}', 'type': 'reload'},
                 {'id': f'fresh-second-{refused_file.name}',
                  'type': 'reload'}]
        for queued, command in zip(files, stale):
            queued.write_text(json.dumps(command), encoding='utf-8')
        with _rewrite_on_first_read(
                refused_file, error(refused_file), list(zip(files, fresh))):
            commands = _cmdqueue.wait_for_commands(queue, 2, timeout=1)
        assert commands == fresh, (refused_file.name, commands)


def test_a_transient_file_not_found_read_retries_the_whole_set(tmp):
    _whole_set_retry_returns_the_rewrite(
        tmp, lambda path: FileNotFoundError(
            2, 'injected transient read error', str(path)))


def test_a_transient_read_refusal_retries_the_whole_set(tmp):
    _whole_set_retry_returns_the_rewrite(
        tmp, lambda path: PermissionError(
            32, 'injected sharing violation', str(path)))


def test_the_multi_command_wait_honors_a_count_other_than_two(tmp):
    queue, _queued = _queued_file(tmp)
    commands = _cmdqueue.wait_for_commands(queue, 1, timeout=1)
    assert commands == [{'id': 'queued', 'type': 'reload'}], commands


def test_the_multi_command_wait_refuses_a_superset(tmp):
    queue, _first = _queued_file(tmp)
    for name in ('1700000000001_000002.json', '1700000000002_000003.json'):
        (queue / name).write_text(
            json.dumps({'id': name, 'type': 'reload'}), encoding='utf-8')
    commands = _cmdqueue.wait_for_commands(queue, 2, timeout=0.1)
    assert commands is None, commands


def test_the_overlap_caller_reads_no_queue_file_after_the_wait(tmp):
    token = 'overlap-caller-token'
    docroot = Path(tmp) / 'docroot'
    queue = docroot / 'commands' / f'{token}_extension'
    queue.mkdir(parents=True)
    for name, domain in (('1700000000000_000001.json', 'owner-a'),
                         ('1700000000001_000002.json', 'owner-b')):
        (queue / name).write_text(json.dumps({'domain': domain}),
                                  encoding='utf-8')

    @contextlib.contextmanager
    def fake_bridge(bridge_tmp, env=None, output=None):
        yield 'http://bridge.test', str(docroot)

    original_bridge = _overlap._util.bridge
    original_overlap = _overlap.run_background_overlap
    original_wait = _cmdqueue.wait_for_commands
    original_open = Path.open
    waits = [0]
    fired = [0]

    def refused(candidate, *args, **kwargs):
        if candidate.parent == queue and candidate.suffix == '.json':
            fired[0] += 1
            raise PermissionError(32, 'injected sharing violation')
        return original_open(candidate, *args, **kwargs)

    def wait_then_arm(directory, count, timeout):
        waits[0] += 1
        commands = original_wait(directory, count, timeout)
        # From here the caller must hold the parsed commands; any later
        # queue read is the untolerated read this branch removed.
        Path.open = refused
        return commands

    def failing_overlap(*args, **kwargs):
        raise AssertionError('injected overlap failure')

    def client_argv(owner):
        return [sys.executable, '-c', 'pass']

    message = None
    _overlap._util.bridge = fake_bridge
    _overlap.run_background_overlap = failing_overlap
    _cmdqueue.wait_for_commands = wait_then_arm
    try:
        try:
            _overlap.run_same_id_client_overlap(
                tmp, ['owner-a', 'owner-b'], client_argv, {}, token,
                'unused-background')
        except AssertionError as failure:
            message = str(failure)
    finally:
        _overlap._util.bridge = original_bridge
        _overlap.run_background_overlap = original_overlap
        _cmdqueue.wait_for_commands = original_wait
        Path.open = original_open
    assert waits == [1], waits
    assert message is not None, 'the injected overlap failure was accepted'
    assert 'injected overlap failure' in message, message
    assert fired == [0], fired


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cmdqueue_'))
