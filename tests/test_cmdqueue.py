#!/usr/bin/env python3
"""Fault controls for test-side command queue readers."""
import contextlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _bridge  # noqa: E402
import _cmdqueue  # noqa: E402
from _cmdqueue_faults import (  # noqa: E402
    _disappear_on_first_open,
    _queued_file,
    _refuse_first_queue_read,
    _refuse_path_operation,
    _vanish_during_read,
    _vanish_during_unlink,
    _virtual_cmdqueue_clock,
)
import _overlap  # noqa: E402
import test_cli  # noqa: E402
import test_mcp_server  # noqa: E402


@contextlib.contextmanager
def _redirect_stale_answer(queue, stale, stale_command):
    """Redirect a stale result so the current producer can finish."""
    original = _util.post_json

    def redirected(url, body, **kwargs):
        if (url.endswith('/result') and body.get('id') == stale_command['id']
                and body.get('_did') == stale_command['_did']):
            current = _cmdqueue.wait_for_command(
                queue, timeout=1, ignored_names={stale.name})
            assert current is not None, 'the current command never appeared'
            body = dict(body, id=current['id'], _did=current['_did'])
        return original(url, body, **kwargs)

    _util.post_json = redirected
    try:
        yield
    finally:
        _util.post_json = original


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
    with _virtual_cmdqueue_clock(10) as (clock, events, origin):
        with _refuse_path_operation(queued, 'open', 1000, clock=clock):
            command = _cmdqueue.wait_for_command(
                queue, timeout=timeout)
    kinds = [kind for kind, _ in events]
    sleeps = [duration for kind, duration in events if kind == 'sleep']
    assert kinds and kinds[0] == 'read', ('no leading read', events)
    assert kinds[-1] == 'sleep', ('no final sleep', events)
    assert all(a != 'sleep' or b == 'read'
               for a, b in zip(kinds, kinds[1:])), events
    assert command is None, command
    assert sleeps[:-1] == [_cmdqueue.POLL_DELAY] * (len(sleeps) - 1), (
        sleeps, events)
    # An exact multiple can make the final sleep equal the polling delay.
    assert sleeps[-1] <= _cmdqueue.POLL_DELAY, (sleeps, events)
    end = clock.monotonic()
    deadline = origin + timeout
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


def test_the_cli_answer_helper_ignores_a_refused_leftover(tmp):
    bridge_env = {'DAEDALUS_TOKEN': test_cli.TOK, 'TOKEN': ''}
    with _util.bridge(tmp, env=bridge_env) as (base, docroot):
        env = test_cli.cli_env(DAEDALUS_URL=base,
                               DAEDALUS_TOKEN=test_cli.TOK)
        # The real helper preserves exact payload and delivery-id shape;
        # constructed leftovers repeatedly let reader changes evade it.
        first_code, first_out, first_err, stale_command = (
            test_cli._answer_one_ext_command(
                base, docroot, ['ext-reload'], {}, env))
        assert first_code == 0, (first_code, first_out, first_err)
        files = _bridge.queue_files(
            docroot, f'{test_cli.TOK}_extension')
        assert len(files) == 1, files
        stale = files[0]
        queue = stale.parent
        with _redirect_stale_answer(queue, stale, stale_command):
            with _refuse_path_operation(stale, 'unlink', 1000) as calls:
                code, out, err, queued = test_cli._answer_one_ext_command(
                    base, docroot, ['ext-reload'], {}, env)
    assert calls[0] == _cmdqueue.UNLINK_ATTEMPTS, calls
    assert code == 0, (code, out, err)
    assert queued['_did'] != stale_command['_did'], queued
    assert queued['type'] == 'reload', (queue, queued)


def test_the_mcp_answer_helper_ignores_a_refused_leftover(tmp):
    test_mcp_server._need_deps()
    bridge_env = {'DAEDALUS_TOKEN': test_mcp_server.TOK, 'TOKEN': '',
                  'DAEDALUS_MCP_PORT': '0'}
    with _util.bridge(tmp, env=bridge_env) as (base, docroot):
        mod = test_mcp_server._load_mcp(base)
        # The real helper preserves exact payload and delivery-id shape;
        # constructed leftovers repeatedly let reader changes evade it.
        _first_value, stale_command = test_mcp_server._answer_mcp_command(
            base, docroot, mod, mod.ext_reload, {})
        files = _bridge.queue_files(
            docroot, f'{test_mcp_server.TOK}_extension')
        assert len(files) == 1, files
        stale = files[0]
        queue = stale.parent
        with _redirect_stale_answer(queue, stale, stale_command):
            with _refuse_path_operation(stale, 'unlink', 1000) as calls:
                _value, queued = test_mcp_server._answer_mcp_command(
                    base, docroot, mod, mod.ext_reload, {})
    assert calls[0] == _cmdqueue.UNLINK_ATTEMPTS, calls
    assert queued['_did'] != stale_command['_did'], queued
    assert queued['type'] == 'reload', (queue, queued)


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
        # Each refusal costs one polling pass; the next pass reads the file.
        assert calls[0] == refusals + 1, calls
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
        assert calls[0] == refusals + 1, calls
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


def test_a_transient_file_not_found_read_retries_the_whole_set(tmp):
    queue, first = _queued_file(tmp)
    second = queue / '1700000000001_000002.json'
    second.write_text(json.dumps({'id': 'second', 'type': 'reload'}),
                      encoding='utf-8')
    original = Path.read_text
    files = (first, second)
    stale = [{'id': 'stale-first', 'type': 'reload'},
             {'id': 'stale-second', 'type': 'reload'}]
    fresh = [{'id': 'fresh-first', 'type': 'reload'},
             {'id': 'fresh-second', 'type': 'reload'}]
    missing_file = [None]
    armed = [False]

    def missing(candidate, *args, **kwargs):
        if candidate == missing_file[0] and armed[0]:
            armed[0] = False
            # Rewrite both so freshness proves a retry for either read order.
            for queued, command in zip(files, fresh):
                queued.write_text(json.dumps(command), encoding='utf-8')
            raise FileNotFoundError(
                2, 'injected transient read error', str(missing_file[0]))
        return original(candidate, *args, **kwargs)

    for refused_file in (first, second):
        for queued, command in zip(files, stale):
            queued.write_text(json.dumps(command), encoding='utf-8')
        missing_file[0] = refused_file
        armed[0] = True
        Path.read_text = missing
        try:
            commands = _cmdqueue.wait_for_commands(queue, 2, timeout=1)
        finally:
            Path.read_text = original
        assert commands == fresh, commands


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
