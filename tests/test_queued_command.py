#!/usr/bin/env python3
"""Controls for the queue-entry read the suite driving a CLI shares.

`_queueread.queued_command` is what stands between a suite and a bridge's
command queue: a refusal that clears while the bridge publishes is
absorbed, one that survives the bounded wait is raised as itself, a queue
that never fills fails as a timeout, the oldest entry is the one parsed,
and an entry that is gone is not retried.
"""
import ast
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _queueread  # noqa: E402
import _util  # noqa: E402


def _denying_read(entry, times):
    """A Path.read_text that refuses `entry` for the first `times` reads.

    The refusal list lets a test pin that the seam retried rather than
    failing at once.
    """
    original = Path.read_text
    refusals = []

    def wrapper(candidate, *args, **kwargs):
        if candidate == entry and len(refusals) < times:
            refusals.append(candidate)
            raise PermissionError(13, 'Permission denied', str(candidate))
        return original(candidate, *args, **kwargs)

    return wrapper, refusals


def _vanishing_read(entry, attempts):
    """A Path.read_text whose first read of `entry` finds it gone.

    `attempts` records every read of `entry`, so a test can pin that a
    vanished entry reached the caller after exactly one read.
    """
    original = Path.read_text

    def wrapper(candidate, *args, **kwargs):
        if candidate == entry:
            attempts.append(candidate)
            if len(attempts) == 1:
                raise FileNotFoundError(2, 'No such file or directory',
                                        str(candidate))
        return original(candidate, *args, **kwargs)

    return wrapper


def test_a_queue_read_retries_a_refusal_and_then_reads(tmp):
    """The queue-entry read survives the refusal Windows reports on publish.

    An open that lands while the bridge is renaming its temp over the final
    name is denied and then works; failing a correct run on it is issue 301.
    """
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    entry = qdir / '1700000000000_000001.json'
    entry.write_text(json.dumps({'id': '_ss', '_did': '1700000000000_1'}),
                     encoding='utf-8')
    wrapper, refusals = _denying_read(entry, times=1)
    with mock.patch.object(Path, 'read_text', wrapper):
        command = _queueread.queued_command(qdir, 'the pinned queue read')
    assert command['id'] == '_ss', command
    assert len(refusals) >= 1, refusals


def test_a_queue_read_denied_for_good_fails_as_the_denial_it_is(tmp):
    """A refusal that survives the bounded wait is raised, not swallowed.

    A reader that swallowed it would report a queue that never filled, and
    the failure has to name the file the bridge never made readable.
    """
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    entry = qdir / '1700000000000_000001.json'
    entry.write_text(json.dumps({'id': '_ss'}), encoding='utf-8')
    wrapper, refusals = _denying_read(entry, times=100)
    failure = None
    with mock.patch.object(Path, 'read_text', wrapper):
        try:
            _queueread.queued_command(qdir, 'the pinned queue read',
                                      timeout=0.2)
        except PermissionError as denied:
            failure = denied
    if failure is None:
        raise AssertionError('the persistent-denial control needs a denial')
    assert len(refusals) > 1, refusals
    assert failure.filename == str(entry), failure


def test_a_queue_read_returns_the_oldest_entry(tmp):
    """Of several entries the oldest is the one the read parses.

    Queue names sort oldest first, so a reader that took the last entry
    would hand the caller a command that was superseded before it was
    answered.
    """
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    for name, identifier in (('1700000000000_000001.json', 'older'),
                             ('1700000000001_000002.json', 'newer')):
        entry = qdir / name
        entry.write_text(json.dumps({'id': identifier}), encoding='utf-8')
    command = _queueread.queued_command(qdir, 'the pinned queue read')
    assert command['id'] == 'older', command


def test_a_queue_read_skips_excluded_names_and_retries_refusal(tmp):
    """The oldest entry outside the handled-name set is read safely.

    A stale entry must not win merely because it sorts first, and the
    eligible entry still needs the Windows denial retry.
    """
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    stale = qdir / '1700000000000_000001.json'
    entry = qdir / '1700000000001_000002.json'
    stale.write_text('not json', encoding='utf-8')
    entry.write_text(json.dumps({'id': 'current'}), encoding='utf-8')
    wrapper, refusals = _denying_read(entry, times=1)
    with mock.patch.object(Path, 'read_text', wrapper):
        command = _queueread.queued_command(
            qdir, 'the pinned queue read', exclude={stale.name})
    assert command['id'] == 'current', command
    assert len(refusals) == 1, refusals


def test_a_multi_queue_read_retries_a_refusal_and_reads_all(tmp):
    """A plural queue read retries a transient denial before its full set."""
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    entries = [qdir / f'170000000000{i}_00000{i}.json' for i in (1, 2)]
    for entry, identifier in zip(entries, ('first', 'second')):
        entry.write_text(json.dumps({'id': identifier}), encoding='utf-8')
    wrapper, refusals = _denying_read(entries[0], times=1)
    with mock.patch.object(Path, 'read_text', wrapper):
        commands = _queueread.queued_commands(
            qdir, 'the pinned queue reads', len(entries))
    assert [command['id'] for command in commands] == ['first', 'second'], (
        commands)
    assert len(refusals) == 1, refusals


def test_a_multi_queue_read_denied_for_good_fails_as_the_denial_it_is(tmp):
    """A plural read raises a persistent denial instead of timing out."""
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    entry = qdir / '1700000000000_000001.json'
    entry.write_text(json.dumps({'id': 'only'}), encoding='utf-8')
    wrapper, refusals = _denying_read(entry, times=100)
    failure = None
    with mock.patch.object(Path, 'read_text', wrapper):
        try:
            _queueread.queued_commands(
                qdir, 'the pinned queue reads', 1, timeout=0.2)
        except PermissionError as denied:
            failure = denied
    if failure is None:
        raise AssertionError('the persistent plural denial needs a denial')
    assert len(refusals) > 1, refusals
    assert failure.filename == str(entry), failure


def test_mcp_suite_routes_queue_reads_through_the_bounded_reader(tmp):
    """The MCP suite has no direct JSON parse of a queue `read_text` call.

    This catches the unprotected `json.loads(...)` applied to a
    `.read_text(...)` queue entry. It deliberately does not reject a future
    non-queue `read_text` use, which would be a different defect.
    """
    del tmp
    source_path = Path(__file__).with_name('test_mcp_server.py')
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    violations = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'loads'
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'json'):
            continue
        if any(isinstance(child, ast.Call)
               and isinstance(child.func, ast.Attribute)
               and child.func.attr == 'read_text'
               for argument in node.args for child in ast.walk(argument)):
            violations.append(node.lineno)
    assert not violations, (
        'MCP queue entries must use queued_command(s), not direct reads: '
        f'{violations}')


def test_a_queue_read_times_out_on_a_queue_that_never_fills(tmp):
    """A queue that never fills fails as a timeout naming what it wanted.

    Returning nothing instead would read as a successful wait that found no
    command, and the caller would carry on as if the bridge had refused to
    queue anything at all.
    """
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    failure = None
    try:
        _queueread.queued_command(qdir, 'the never-filled queue',
                                  timeout=0.2)
    except AssertionError as timeout:
        failure = timeout
    if failure is None:
        raise AssertionError('the empty queue was not reported')
    assert str(failure) == 'timed out waiting for the never-filled queue', (
        failure)


def test_a_queue_read_does_not_retry_a_vanished_entry(tmp):
    """A vanished entry is not the refusal the read retries.

    The tolerance is for a Windows denial while the bridge publishes; an
    entry that is gone is a different failure and reaches the caller
    unsuppressed, after exactly one read.
    """
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    entry = qdir / '1700000000000_000001.json'
    entry.write_text(json.dumps({'id': '_ss'}), encoding='utf-8')
    attempts = []
    wrapper = _vanishing_read(entry, attempts)
    failure = None
    with mock.patch.object(Path, 'read_text', wrapper):
        try:
            _queueread.queued_command(qdir, 'the pinned queue read')
        except FileNotFoundError as vanished:
            failure = vanished
    if failure is None:
        raise AssertionError('the vanished entry was swallowed')
    assert len(attempts) == 1, attempts
    assert failure.filename == str(entry), failure


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
