#!/usr/bin/env python3
"""Standalone publication and lifecycle guarantees for the command queue."""
import json
import os
import subprocess
import sys
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


def test_command_filesystem_lock_is_public(_tmp):
    queue = _load_queue('command_queue_filesystem_lock')
    assert callable(queue.command_fs_lock.acquire)


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
    queued = Path(tmp) / 'commands' / 'tok' / 'old.json'
    queued.parent.mkdir(parents=True)
    queued.write_text('{"id":"queued"}', encoding='utf-8')
    os.utime(queued, (0, 0))
    queue.collect_expired(queued.parents[1], 1)
    assert not queued.exists() and not queued.parent.exists()


def test_gc_loop_derives_its_sleep_interval(_tmp):
    queue = _load_queue('command_queue_gc_loop')

    def sleep(interval):
        raise RuntimeError(interval)
    queue.time = type('Clock', (), {'sleep': staticmethod(sleep)})
    try:
        queue.gc_loop(Path('unused'), 45)
    except RuntimeError as error:
        assert error.args == (30.0,)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
