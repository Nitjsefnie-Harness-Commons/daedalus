#!/usr/bin/env python3
"""Standalone publication and lifecycle guarantees for the command queue."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_queue(name):
    return _util.load(_util.ROOT / 'command_queue.py', name=name)


def test_command_queue_imports_without_daedalus_configuration(_tmp):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('DAEDALUS_') and key != 'TOKEN'}
    loaded = subprocess.run(
        [sys.executable, '-c', 'import command_queue'],
        cwd=str(_util.ROOT), env=env, stderr=subprocess.PIPE, text=True)
    assert loaded.returncode == 0, loaded.stderr


def test_queue_naming_contract_is_pinned(tmp):
    del tmp
    queue = _load_queue('command_queue_names')

    assert queue.command_target_names('tok', 'tab') == (
        'tok_tab', 'tok_tab.json')


def test_enqueue_waits_for_the_shared_filesystem_lock(tmp):
    queue = _load_queue('command_queue_filesystem_lock')
    cmd_dir = Path(tmp) / 'commands'
    finished = threading.Event()
    delivery_ids = []
    failures = []

    class ObservedLock:
        def __init__(self):
            self._lock = threading.Lock()
            self.attempted = threading.Event()

        def acquire(self):
            self.attempted.set()
            return self._lock.acquire()

        def release(self):
            self._lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, _kind, _value, _traceback):
            self.release()

    observed = ObservedLock()
    queue.command_fs_lock = observed
    observed.acquire()
    observed.attempted.clear()

    def publish():
        try:
            delivery_ids.append(
                queue.enqueue(cmd_dir, 'tok', 'tab', {'id': 'queued'}))
        except Exception as error:  # preserve a worker assertion failure
            failures.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=publish)
    worker.start()
    try:
        assert observed.attempted.wait(5), (
            'enqueue never attempted the shared command filesystem lock')
        assert not (cmd_dir / 'tok_tab').exists()
    finally:
        observed.release()
    assert finished.wait(5), (
        'enqueue stayed blocked after the lock was released')
    worker.join()
    assert failures == []
    delivery_id, = delivery_ids
    assert (cmd_dir / 'tok_tab' / f'{delivery_id}.json').exists()


def test_notify_dashboard_publishes_an_event_and_wakes_the_token(tmp):
    queue = _load_queue('command_queue_dashboard_notify')
    cmd_dir = Path(tmp) / 'commands'
    queue.notify_dashboard(cmd_dir, 'tok', {'type': 'tabs-synced'})
    published, = (cmd_dir / 'tok_dashboard').iterdir()
    assert json.loads(published.read_text(encoding='utf-8')) == {
        'id': published.stem, 'kind': 'event', 'type': 'tabs-synced'}
    assert queue.event('tok').is_set()


def test_next_seq_is_lexically_increasing_and_well_formed(_tmp):
    queue = _load_queue('command_queue_next_seq')
    first, second = queue.next_seq(), queue.next_seq()
    assert first < second, (first, second)
    for value in (first, second):
        assert (tuple(map(len, value.split('_'))) == (13, 6)
                and value.replace('_', '').isdigit()), value


def test_event_reuses_one_wake_event_per_token(_tmp):
    queue = _load_queue('command_queue_event')
    assert queue.event('tok') is queue.event('tok') is not queue.event('other')


def test_enqueue_atomically_publishes_a_waking_delivery(tmp):
    queue = _load_queue('command_queue_enqueue')
    cmd_dir = Path(tmp) / 'commands'
    delivery_id = queue.enqueue(cmd_dir, 'tok', 'tab', {'id': 'queued'})
    destination = cmd_dir / 'tok_tab' / f'{delivery_id}.json'
    assert json.loads(destination.read_text(encoding='utf-8')) == {
        'id': 'queued', '_did': delivery_id}
    assert queue.event('tok').is_set()


def test_collect_expired_removes_old_commands_and_empty_queues(tmp):
    queue = _load_queue('command_queue_collect_expired')
    cmd_dir = Path(tmp) / 'commands'
    queued = cmd_dir / 'tok' / 'old.json'
    legacy = cmd_dir / 'tok.json'
    queued.parent.mkdir(parents=True)
    queued.write_text('{"id":"queued"}', encoding='utf-8')
    legacy.write_text('{"id":"legacy"}', encoding='utf-8')
    os.utime(queued, (0, 0))
    os.utime(legacy, (0, 0))
    queue.collect_expired(cmd_dir, 1)
    assert not queued.exists(), queued
    assert not queued.parent.exists(), queued.parent
    assert not legacy.exists(), legacy


def test_gc_loop_forwards_directory_and_ttl_after_sleep(_tmp):
    queue = _load_queue('command_queue_gc_loop')
    cmd_dir = Path('commands')
    calls = []

    def sleep(interval):
        calls.append(('sleep', interval))

    def collect(directory, ttl):
        calls.append(('collect', directory, ttl))
        raise RuntimeError('one iteration complete')

    queue.time = type('Clock', (), {'sleep': staticmethod(sleep)})
    queue.collect_expired = collect
    try:
        queue.gc_loop(cmd_dir, 45)
    except RuntimeError as error:
        assert error.args == ('one iteration complete',)
    assert calls == [
        ('sleep', 30.0),
        ('collect', cmd_dir, 45),
    ]


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
