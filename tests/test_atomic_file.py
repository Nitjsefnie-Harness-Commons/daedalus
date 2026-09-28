#!/usr/bin/env python3
"""Regression pin for replace_atomically's publication."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


_MODULE = _util.ROOT / 'daedalus_bridge' / 'atomic_file.py'


def _fresh_module():
    """A private instance per test; stand-ins cannot leak."""
    return _util.load(_MODULE, name='atomic_file_under_test')


class _PublishSpy:
    """`replace` records what it was handed; other names pass through to os.
    A bypass dies on the pins; a wrong-typed argument on this probe."""

    def __init__(self, publish):
        self._publish = publish
        self.calls = []
        self.states = []

    def replace(self, *args, **kwargs):
        handed = dict(zip(('src', 'dst'), args), **kwargs)
        self.states.append(
            handed['dst'].read_bytes() if handed['dst'].exists() else None)
        self.calls.append((handed['src'], handed['dst']))
        self._publish(*args, **kwargs)

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
    module.replace_atomically(src, dst)

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
    module.replace_atomically(src, dst)

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
    raised = None
    try:
        module.replace_atomically(src, dst)
    except PermissionError as error:
        raised = error

    assert raised is None, raised
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
    raised = None
    try:
        module.replace_atomically(src, dst)
    except OSError as error:
        raised = error

    assert isinstance(raised, OSError), raised
    assert attempts == [(src, dst)], attempts
    assert not dst.exists()


def test_the_recorder_accepts_the_keyword_call_form(tmp):
    spy = _PublishSpy(os.replace)
    src = Path(tmp) / 'incoming'
    dst = Path(tmp) / 'live.json'
    payload = b'published through the keyword call form'
    src.write_bytes(payload)

    spy.replace(src, dst=dst)

    assert spy.calls == [(src, dst)], spy.calls
    assert dst.read_bytes() == payload


def test_a_refusal_that_never_clears_costs_the_whole_attempt_budget(tmp):
    module = _fresh_module()
    src = Path(tmp) / 'incoming'
    dst = Path(tmp) / 'live.json'
    src.write_bytes(b'never published')
    attempts = []

    def always_refused(src, dst):
        attempts.append((src, dst))
        raise PermissionError(13, 'injected sharing violation')

    module.os = _PublishSpy(always_refused)
    raised = None
    try:
        module.replace_atomically(src, dst)
    except PermissionError as error:
        raised = error

    assert isinstance(raised, PermissionError), raised
    assert len(attempts) == module._RETRY_ATTEMPTS, (
        len(attempts), module._RETRY_ATTEMPTS)
    assert not dst.exists()


def test_unlink_retries_a_transient_refusal_then_removes_the_file(tmp):
    module = _fresh_module()
    victim = Path(tmp) / 'stale.json'
    victim.write_text('about to be collected', encoding='utf-8')
    real_unlink = Path.unlink
    attempts = []

    def refuse_then_unlink(self, *args, **kwargs):
        attempts.append(self)
        if len(attempts) < 3:
            raise PermissionError(13, 'injected sharing violation')
        return real_unlink(self, *args, **kwargs)

    Path.unlink = refuse_then_unlink
    try:
        module.unlink_retrying(victim)
    finally:
        Path.unlink = real_unlink

    assert attempts == [victim, victim, victim], attempts
    assert not victim.exists()


def test_unlink_of_a_name_already_gone_is_not_a_refusal(tmp):
    module = _fresh_module()
    module.unlink_retrying(Path(tmp) / 'never-existed.json')
    assert not (Path(tmp) / 'never-existed.json').exists()


def test_read_retries_a_transient_refusal_then_returns_the_text(tmp):
    module = _fresh_module()
    record = Path(tmp) / 'record.txt'
    payload = 'the record the sweep published'
    record.write_text(payload, encoding='utf-8')
    real_read_text = Path.read_text
    attempts = []

    def refuse_then_read(self, *args, **kwargs):
        attempts.append(self)
        if len(attempts) < 3:
            raise PermissionError(13, 'injected sharing violation')
        return real_read_text(self, *args, **kwargs)

    Path.read_text = refuse_then_read
    try:
        text = module.read_text_retrying(record, 'utf-8')
    finally:
        Path.read_text = real_read_text

    assert attempts == [record, record, record], attempts
    assert text == payload, text


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(globals())))
