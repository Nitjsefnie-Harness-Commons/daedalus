#!/usr/bin/env python3
"""Fault controls for test-side command queue readers."""
import contextlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import test_cli  # noqa: E402
from _cmdqueue import clear_command_queue, wait_for_command  # noqa: E402


@contextlib.contextmanager
def _refuse_path_operation(path, operation, failures):
    original = getattr(Path, operation)
    remaining = [failures]

    def refused(candidate, *args, **kwargs):
        if candidate == path and remaining[0]:
            remaining[0] -= 1
            raise PermissionError(32, 'injected sharing violation')
        return original(candidate, *args, **kwargs)

    setattr(Path, operation, refused)
    try:
        yield
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


def test_a_transient_read_refusal_returns_the_queued_command(tmp):
    queue, queued = _queued_file(tmp)
    with _refuse_path_operation(queued, 'read_text', 1):
        command = wait_for_command(queue, timeout=1)
    assert command == {'id': 'queued', 'type': 'reload'}, command


def test_a_present_queue_file_outlives_its_finished_producer(tmp):
    queue, queued = _queued_file(tmp)
    with _refuse_path_operation(queued, 'read_text', 1):
        command = wait_for_command(
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
        command = wait_for_command(queue, timeout=1)
    finally:
        Path.read_text = original
    assert command == {'id': 'queued', 'type': 'reload'}, command


def test_a_transient_removal_refusal_still_clears_the_queue(tmp):
    queue, queued = _queued_file(tmp)
    with _refuse_path_operation(queued, 'unlink', 1):
        clear_command_queue(queue)
    assert list(queue.glob('*.json')) == []


def test_a_queue_file_already_gone_during_clear_is_not_an_error(tmp):
    queue, queued = _queued_file(tmp)
    with _vanish_during_unlink(queued):
        clear_command_queue(queue)
    assert list(queue.glob('*.json')) == []


def test_a_permanent_read_refusal_is_bounded(tmp):
    queue, queued = _queued_file(tmp)
    started = time.monotonic()
    with _refuse_path_operation(queued, 'read_text', 1000):
        command = wait_for_command(queue, timeout=0.1)
    elapsed = time.monotonic() - started
    assert command is None, command
    assert elapsed < 1, elapsed


def test_a_permanent_removal_refusal_raises_without_hanging(tmp):
    queue, queued = _queued_file(tmp)
    started = time.monotonic()
    try:
        with _refuse_path_operation(queued, 'unlink', 1000):
            clear_command_queue(queue)
    except PermissionError as failure:
        assert str(queued) in str(failure) or failure.errno == 32, failure
    else:
        raise AssertionError('a permanently refused queue entry was swallowed')
    assert time.monotonic() - started < 2
    assert queued.is_file()


def test_wait_returns_none_when_the_timeout_expires(tmp):
    queue = Path(tmp) / 'missing-queue'
    assert wait_for_command(queue, timeout=0.01) is None


def test_wait_ends_early_when_the_producer_is_gone(tmp):
    queue = Path(tmp) / 'missing-queue'
    started = time.monotonic()
    command = wait_for_command(queue, timeout=5, producer_alive=lambda: False)
    assert command is None, command
    assert time.monotonic() - started < 1


def test_the_cli_answer_helper_survives_a_transient_queue_read_refusal(tmp):
    with _util.bridge(tmp) as (base, docroot):
        env = test_cli.cli_env(DAEDALUS_URL=base,
                               DAEDALUS_TOKEN=test_cli.TOK)
        queue = (Path(docroot) / 'commands'
                 / f'{test_cli.TOK}_extension')
        with _refuse_first_queue_read(queue):
            code, out, err, queued = test_cli._answer_one_ext_command(
                base, docroot, ['ext-reload'], {}, env)
    assert code == 0, (code, out, err)
    assert queued['type'] == 'reload', queued


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals())),
                          tmp_prefix='cmdqueue_'))
