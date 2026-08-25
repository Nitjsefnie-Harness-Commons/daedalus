#!/usr/bin/env python3
"""Command-file expiry stays independent from the bridge server."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _load_queue(name):
    return _util.load(_util.ROOT / 'command_queue.py', name=name)


def _set_mtime(path, stamp):
    os.utime(path, (stamp, stamp))


def test_remove_expired_removes_an_expired_json(tmp):
    queue = _load_queue('command_queue_expired')
    path = Path(tmp) / 'queued.json'
    path.write_text('{"id":"expired"}', encoding='utf-8')
    now = time.time()
    _set_mtime(path, now - 91)

    queue.remove_expired(path, now, 90)

    assert not path.exists(), path


def test_remove_expired_leaves_a_fresh_json(tmp):
    queue = _load_queue('command_queue_fresh')
    path = Path(tmp) / 'queued.json'
    path.write_text('{"id":"fresh"}', encoding='utf-8')
    now = time.time()
    _set_mtime(path, now - 1)

    queue.remove_expired(path, now, 90)

    assert path.exists(), path


def test_remove_expired_leaves_a_malformed_legacy_file(tmp):
    queue = _load_queue('command_queue_malformed')
    path = Path(tmp) / 'legacy.json'
    path.write_text('{not-json', encoding='utf-8')
    now = time.time()
    _set_mtime(path, now - 91)

    queue.remove_expired(path, now, 90, legacy=True)

    assert path.exists(), path


def test_remove_expired_does_not_follow_a_symlink(tmp):
    queue = _load_queue('command_queue_symlink')
    target = Path(tmp) / 'target.json'
    path = Path(tmp) / 'link.json'
    target.write_text('{"id":"target"}', encoding='utf-8')
    now = time.time()
    _set_mtime(target, now - 91)
    try:
        path.symlink_to(target)
        os.utime(path, (now, now), follow_symlinks=False)
    except (OSError, NotImplementedError):
        _util.skip('this platform cannot create or timestamp symlinks')

    queue.remove_expired(path, now, 90)

    assert path.is_symlink(), path
    assert target.exists(), target


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
