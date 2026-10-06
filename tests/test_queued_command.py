#!/usr/bin/env python3
"""Controls for one-or-exact-set queue reads shared by CLI-driving suites.

`_queueread` stands between suites and bridge command queues: transient
refusals are absorbed, surviving refusals are raised as themselves, an
unfilled queue times out, one read selects the oldest eligible entry, an exact
set requires its full count, and a vanished entry is not retried.
"""
import ast
import json
import math
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _queueread  # noqa: E402
import _util  # noqa: E402
from _cmdqueue_faults import (  # noqa: E402
    _assert_slept_its_attempt_budget,
    _scope_map,
    _bounded_polls,
    _poll_budget,
    _virtual_cmdqueue_clock,
)


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
        with _bounded_polls(_poll_budget(_queueread.DEFAULT_TIMEOUT)):
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
    with _virtual_cmdqueue_clock(wall_budget=None):
        with mock.patch.object(Path, 'read_text', wrapper):
            with _bounded_polls(_poll_budget(0.2)):
                try:
                    _queueread.queued_command(
                        qdir, 'the pinned queue read', timeout=0.2)
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
    with _bounded_polls(_poll_budget(_queueread.DEFAULT_TIMEOUT)):
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
        with _bounded_polls(_poll_budget(_queueread.DEFAULT_TIMEOUT)):
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
    for refused_entry in entries:
        wrapper, refusals = _denying_read(refused_entry, times=1)
        with mock.patch.object(Path, 'read_text', wrapper):
            with _bounded_polls(
                    _poll_budget(_queueread.DEFAULT_TIMEOUT)):
                commands = _queueread.queued_commands(
                    qdir, 'the pinned queue reads', len(entries))
        assert [command['id'] for command in commands] == [
            'first', 'second'], commands
        assert len(refusals) == 1, (refused_entry, refusals)


def test_a_multi_queue_read_requires_exact_count(tmp):
    """Under-filled and over-filled queues return no partial command set.

    The interval waited is pinned as well as the message: a reader that
    stops sleeping still rejects at its attempt budget, so the message
    alone passes a reader that never waited between its polls.
    """
    timeout = 2.5 * _queueread.POLL_DELAY
    attempts = math.ceil(timeout / _queueread.POLL_DELAY)
    for available in (1, 3):
        qdir = Path(tmp) / f'commands-{available}'
        qdir.mkdir()
        for index in range(available):
            entry = qdir / f'170000000000{index}_00000{index}.json'
            entry.write_text(json.dumps({'id': str(index)}), encoding='utf-8')
        failure = None
        with _bounded_polls(_poll_budget(timeout)):
            with _virtual_cmdqueue_clock(
                    wall_budget=None) as (_clock, events, _origin):
                try:
                    _queueread.queued_commands(
                        qdir, 'exactly two commands', 2, timeout=timeout)
                except AssertionError as timeout_error:
                    failure = timeout_error
        assert str(failure) == 'timed out waiting for exactly two commands', (
            available, failure)
        _assert_slept_its_attempt_budget(
            events, attempts, _queueread.POLL_DELAY)


def test_a_multi_queue_read_does_not_retry_a_vanished_entry(tmp):
    """A vanished plural entry reaches the caller after its first read."""
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    entries = [qdir / f'170000000000{i}_00000{i}.json' for i in (1, 2)]
    for entry, identifier in zip(entries, ('first', 'second')):
        entry.write_text(json.dumps({'id': identifier}), encoding='utf-8')
    attempts = []
    wrapper = _vanishing_read(entries[1], attempts)
    failure = None
    with _virtual_cmdqueue_clock():
        with mock.patch.object(Path, 'read_text', wrapper):
            with _bounded_polls(_poll_budget(_queueread.DEFAULT_TIMEOUT)):
                try:
                    _queueread.queued_commands(
                        qdir, 'the pinned queue reads', len(entries))
                except FileNotFoundError as vanished:
                    failure = vanished
    assert attempts == [entries[1]], attempts
    assert failure is not None and failure.filename == str(entries[1]), failure


def test_a_multi_queue_read_denied_for_good_fails_as_the_denial_it_is(tmp):
    """A plural read raises a persistent denial instead of timing out."""
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    entry = qdir / '1700000000000_000001.json'
    entry.write_text(json.dumps({'id': 'only'}), encoding='utf-8')
    wrapper, refusals = _denying_read(entry, times=100)
    failure = None
    with _virtual_cmdqueue_clock(wall_budget=None):
        with mock.patch.object(Path, 'read_text', wrapper):
            with _bounded_polls(_poll_budget(0.2)):
                try:
                    _queueread.queued_commands(
                        qdir, 'the pinned queue reads', 1, timeout=0.2)
                except PermissionError as denied:
                    failure = denied
    if failure is None:
        raise AssertionError('the persistent plural denial needs a denial')
    assert len(refusals) > 1, refusals
    assert failure.filename == str(entry), failure


def test_mcp_suite_has_no_json_loads_of_read_text_results(tmp):
    """Reject nested or assigned `read_text` results passed to `json.loads`.

    This is a module-wide syntactic rule, not queue-path analysis. Assigned
    names are matched within their lexical scope;
    `json.load(path.open())` remains outside its scope.
    """
    del tmp
    successors = (
        'test_mcp_server_load.py',
        'test_mcp_server_serve.py',
        'test_mcp_path_authority.py',
        'test_mcp_auth.py',
        'test_mcp_live_tools.py',
        'test_mcp_transport_close.py',
    )
    violations = []
    for name in successors:
        source_path = Path(__file__).with_name(name)
        tree = ast.parse(source_path.read_text(encoding='utf-8'))
        scope_by_node = _scope_map(tree)
        assigned_reads = {
            (scope_by_node[assignment], target.id)
            for assignment in ast.walk(tree)
            if (isinstance(assignment, ast.Assign)
                and isinstance(assignment.value, ast.Call)
                and isinstance(assignment.value.func, ast.Attribute)
                and assignment.value.func.attr == 'read_text')
            for target in assignment.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'loads'
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'json'):
                continue
            nested_read = any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == 'read_text'
                for argument in node.args for child in ast.walk(argument))
            assigned_read = any(
                isinstance(argument, ast.Name)
                and (scope_by_node[node], argument.id) in assigned_reads
                for argument in node.args)
            if nested_read or assigned_read:
                violations.append((name, node.lineno))
    assert not violations, (
        'MCP suite json.loads calls must not consume read_text results; '
        'json.load(path.open()) is outside this syntactic rule: '
        f'{violations}')


def test_a_queue_read_spends_one_poll_delay_per_attempt(tmp):
    """The wait's budget is poll attempts, not elapsed time (issue 925).

    A wall deadline spends the queue reader under a starved runner: the
    reader polls `ceil(timeout / POLL_DELAY)` times and rejects at its
    attempt budget with the pinned message whatever the wall clock did
    in between. This pins that the whole wait spends (attempts - 1)
    polling intervals, which is the property the name describes; the
    per-interval cadence is the helper's, below.

    What this pins is the TOTAL the attempt budget spends, and not the
    cadence across passes; `_assert_slept_its_attempt_budget` carries the
    record of what that gives up.
    """
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    for timeout, attempts in ((0.2, 4),
                              (2.5 * _queueread.POLL_DELAY, 3)):
        failure = None
        with _bounded_polls(_poll_budget(timeout)):
            with _virtual_cmdqueue_clock(
                    wall_budget=None) as (_clock, events, _origin):
                try:
                    _queueread.queued_command(
                        qdir, 'the never-filled queue', timeout=timeout)
                except AssertionError as timeout_error:
                    failure = timeout_error
        if failure is None:
            raise AssertionError('the empty queue was not reported')
        assert str(failure) == (
            'timed out waiting for the never-filled queue'), failure
        _assert_slept_its_attempt_budget(
            events, attempts, _queueread.POLL_DELAY)


def test_a_queue_read_times_out_on_a_queue_that_never_fills(tmp):
    """A queue that never fills fails as a timeout naming what it wanted.

    Returning nothing instead would read as a successful wait that found no
    command, and the caller would carry on as if the bridge had refused to
    queue anything at all.
    """
    qdir = Path(tmp) / 'commands' / 'tok_extension'
    qdir.mkdir(parents=True)
    timeout = 0.2
    failure = None
    with _bounded_polls(_poll_budget(timeout)):
        try:
            _queueread.queued_command(qdir, 'the never-filled queue',
                                      timeout=timeout)
        except AssertionError as timed_out:
            failure = timed_out
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
        with _bounded_polls(_poll_budget(_queueread.DEFAULT_TIMEOUT)):
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
