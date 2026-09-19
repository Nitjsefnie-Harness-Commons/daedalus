#!/usr/bin/env python3
"""Regression pin for the atomic publication behind bridge storage writes."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


_MODULE = _util.ROOT / 'daedalus_bridge' / 'atomic_file.py'


def _fresh_module():
    """A private instance per test, so an os stand-in never leaks."""
    return _util.load(_MODULE, name='atomic_file_under_test')


class _PublishSpy:
    """`replace` records what it was handed; other names pass through to os,
    so a mutated publication fails on the pins, not on an AttributeError."""

    def __init__(self, publish):
        self._publish = publish
        self.calls = []
        self.states = []

    def replace(self, src, dst):
        self.states.append(dst.read_bytes() if dst.exists() else None)
        self.calls.append((src, dst))
        self._publish(src, dst)

    def __getattr__(self, name):
        return getattr(os, name)


# Mutation directions these pins kill: a copy-then-unlink publication never
# reaches `replace`, so `calls` and `states` come back empty; rewritten or
# swapped arguments show up in `calls`; a destination written progressively
# shows a partial `states` entry at the flip; a widened retry `except`
# retries the OSError probe instead of surfacing it on attempt one.
def test_publish_goes_through_os_replace_with_the_callers_own_arguments(tmp):
    module = _fresh_module()
    src = Path(tmp) / 'incoming'
    dst = Path(tmp) / 'live.json'
    payload = b'complete publication bytes'
    src.write_bytes(payload)
    spy = _PublishSpy(os.replace)

    module.os = spy
    try:
        module.replace_atomically(src, dst)
    finally:
        module.os = os

    assert spy.calls == [(src, dst)], spy.calls
    assert dst.read_bytes() == payload
    assert not src.exists()


def test_publish_moment_sees_no_partial_destination(tmp):
    module = _fresh_module()
    src = Path(tmp) / 'incoming'
    dst = Path(tmp) / 'live.json'
    payload = b'complete publication bytes'
    src.write_bytes(payload)
    already_there = b'bytes the destination already held'
    dst.write_bytes(already_there)
    spy = _PublishSpy(os.replace)

    module.os = spy
    try:
        module.replace_atomically(src, dst)
    finally:
        module.os = os

    assert spy.states == [already_there], spy.states
    assert dst.read_bytes() == payload
    assert not src.exists()


def test_transient_sharing_violation_is_retried_until_it_clears(tmp):
    module = _fresh_module()
    src = Path(tmp) / 'incoming'
    dst = Path(tmp) / 'live.json'
    payload = b'published once the sharing violation clears'
    src.write_bytes(payload)
    attempts = []

    def sharing_violation_then_success(src, dst):
        attempts.append((src, dst))
        if len(attempts) < 3:
            raise PermissionError(13, 'injected sharing violation')
        os.replace(src, dst)

    module.os = _PublishSpy(sharing_violation_then_success)
    try:
        module.replace_atomically(src, dst)
    finally:
        module.os = os

    assert attempts == [(src, dst)] * 3, attempts
    assert dst.read_bytes() == payload


def test_errors_retrying_cannot_fix_surface_on_the_first_attempt(tmp):
    module = _fresh_module()
    src = Path(tmp) / 'incoming'
    dst = Path(tmp) / 'live.json'
    payload = b'never published through a read-only refusal'
    src.write_bytes(payload)
    attempts = []

    def read_only(src, dst):
        attempts.append((src, dst))
        raise OSError(30, 'injected read-only refusal')

    module.os = _PublishSpy(read_only)
    try:
        module.replace_atomically(src, dst)
    except OSError as error:
        raised = error
    else:
        raised = None
    finally:
        module.os = os

    assert isinstance(raised, OSError), raised
    assert attempts == [(src, dst)], attempts
    assert not dst.exists()


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
