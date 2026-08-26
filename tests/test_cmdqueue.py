#!/usr/bin/env python3
"""Fault controls for test-side command queue readers."""
import contextlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _cmdqueue  # noqa: E402
import test_cli  # noqa: E402
import test_mcp_server  # noqa: E402


@contextlib.contextmanager
def _refuse_path_operation(path, operation, failures):
    original = getattr(Path, operation)
    remaining = [failures]
    calls = [0]

    def refused(candidate, *args, **kwargs):
        if candidate == path:
            calls[0] += 1
            if remaining[0]:
                remaining[0] -= 1
                raise PermissionError(32, 'injected sharing violation')
        return original(candidate, *args, **kwargs)

    setattr(Path, operation, refused)
    try:
        yield calls
    finally:
        setattr(Path, operation, original)


@contextlib.contextmanager
def _vanish_during_unlink(path):
    original = Path.unlink
    armed = [True]

    def vanished(candidate, *args, **kwargs):
        if candidate == path and armed[0]:
            armed[0] = False
            original(candidate, *args, **kwargs)
            raise FileNotFoundError(2, 'injected disappearance', str(path))
        return original(candidate, *args, **kwargs)

    Path.unlink = vanished
    try:
        yield
    finally:
        Path.unlink = original


@contextlib.contextmanager
def _refuse_first_queue_read(queue):
    original = Path.read_text
    refused_path = [None]

    def refused(candidate, *args, **kwargs):
        if (refused_path[0] is None and candidate.parent == queue
                and candidate.suffix == '.json'):
            refused_path[0] = candidate
        if candidate == refused_path[0]:
            refused_path[0] = False
            raise PermissionError(32, 'injected sharing violation')
        return original(candidate, *args, **kwargs)

    Path.read_text = refused
    try:
        yield
    finally:
        Path.read_text = original


def _queued_file(tmp, name='1700000000000_000001.json'):
    queue = Path(tmp) / 'queue'
    queue.mkdir(exist_ok=True)
    queued = queue / name
    queued.write_text(json.dumps({'id': 'queued', 'type': 'reload'}),
                      encoding='utf-8')
    return queue, queued


def _stale_queue(docroot, token):
    queue = Path(docroot) / 'commands' / f'{token}_extension'
    queue.mkdir(parents=True, exist_ok=True)
    stale = queue / '0000000000000_000000.json'
    command = {
        'id': 'stale-command', '_did': 'stale-delivery', 'type': 'reload'}
    stale.write_text(json.dumps(command), encoding='utf-8')
    return queue, stale, command


@contextlib.contextmanager
def _redirect_stale_answer(queue, stale, stale_command):
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
    with _refuse_path_operation(queued, 'read_text', 1):
        command = _cmdqueue.wait_for_command(queue, timeout=1)
    assert command == {'id': 'queued', 'type': 'reload'}, command


def test_a_present_queue_file_outlives_its_finished_producer(tmp):
    queue, queued = _queued_file(tmp)
    with _refuse_path_operation(queued, 'read_text', 1):
        command = _cmdqueue.wait_for_command(
            queue, timeout=1, producer_alive=lambda: False)
    assert command == {'id': 'queued', 'type': 'reload'}, command


def test_a_queue_file_that_disappears_during_read_is_retried(tmp):
    queue, queued = _queued_file(tmp)
    original = Path.read_text
    armed = [True]

    def missing(candidate, *args, **kwargs):
        if candidate == queued and armed[0]:
            armed[0] = False
            raise FileNotFoundError(2, 'injected disappearance', str(queued))
        return original(candidate, *args, **kwargs)

    Path.read_text = missing
    try:
        command = _cmdqueue.wait_for_command(queue, timeout=1)
    finally:
        Path.read_text = original
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
    queue, queued = _queued_file(tmp)
    started = time.monotonic()
    with _refuse_path_operation(queued, 'read_text', 1000):
        command = _cmdqueue.wait_for_command(queue, timeout=0.1)
    elapsed = time.monotonic() - started
    assert command is None, command
    assert elapsed < 1, elapsed


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
    started = time.monotonic()
    command = _cmdqueue.wait_for_command(
        queue, timeout=10, producer_alive=lambda: False)
    assert command is None, command
    assert time.monotonic() - started < 1


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
        queue, stale, stale_command = _stale_queue(docroot, test_cli.TOK)
        env = test_cli.cli_env(DAEDALUS_URL=base,
                               DAEDALUS_TOKEN=test_cli.TOK)
        with _redirect_stale_answer(queue, stale, stale_command):
            with _refuse_path_operation(stale, 'unlink', 1000) as calls:
                code, out, err, queued = test_cli._answer_one_ext_command(
                    base, docroot, ['ext-reload'], {}, env)
    assert calls[0] == _cmdqueue.UNLINK_ATTEMPTS, calls
    assert code == 0, (code, out, err)
    assert queued['id'] != stale_command['id'], queued
    assert queued['_did'] != stale_command['_did'], queued
    assert queued['type'] == 'reload', (queue, queued)


def test_the_mcp_answer_helper_ignores_a_refused_leftover(tmp):
    test_mcp_server._need_deps()
    bridge_env = {'DAEDALUS_TOKEN': test_mcp_server.TOK, 'TOKEN': '',
                  'DAEDALUS_MCP_PORT': '0'}
    with _util.bridge(tmp, env=bridge_env) as (base, docroot):
        mod = test_mcp_server._load_mcp(base)
        queue, stale, stale_command = _stale_queue(
            docroot, test_mcp_server.TOK)
        with _redirect_stale_answer(queue, stale, stale_command):
            with _refuse_path_operation(stale, 'unlink', 1000) as calls:
                _value, queued = test_mcp_server._answer_mcp_command(
                    base, docroot, mod, mod.ext_reload, {})
    assert calls[0] == _cmdqueue.UNLINK_ATTEMPTS, calls
    assert queued['id'] != stale_command['id'], queued
    assert queued['_did'] != stale_command['_did'], queued
    assert queued['type'] == 'reload', (queue, queued)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cmdqueue_'))
