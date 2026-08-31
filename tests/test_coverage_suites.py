#!/usr/bin/env python3
"""coverage_suites.py: concurrent measurement without mixed suite output."""
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_suite_fixture import (  # noqa: E402
    SYNTHETIC_PROCESS_START, coverage_tree)
from _repo import ROOT  # noqa: E402


_FAILURE_MARKER = '(suite did not pass; its coverage still counts)'


_RENDEZVOUS_SUITE = r"""import os, time
from pathlib import Path

marks = Path(__file__).resolve().parent / 'marks'
marks.mkdir(exist_ok=True)
(marks / Path(__file__).name).touch()
deadline = time.monotonic() + 30
while len(list(marks.iterdir())) < 2 and time.monotonic() < deadline:
    time.sleep(0.05)
if len(list(marks.iterdir())) < 2:
    raise AssertionError('no sibling suite was running concurrently')
"""


_ACQUIRE_SOURCE = r"""def acquire():
    while True:
        try:
            lock.mkdir(); return
        except (FileExistsError, PermissionError):
            # Windows delete-pending window makes PermissionError transient.
            time.sleep(0.01)
"""


_RETRY_SOURCE = r"""def append_event(marker):
    while True:
        try:
            with events_path.open('a', encoding='utf-8') as events:
                events.write(f'{marker}{Path(__file__).name}\n')
            return
        except PermissionError:
            time.sleep(0.01)
def release():
    while True:
        try:
            lock.rmdir()
            return
        except PermissionError:
            time.sleep(0.01)
"""


_CONCURRENCY_EVENT_SUITE = r"""import time; from pathlib import Path
root = Path(__file__).resolve().parent
lock = root / 'concurrency.lock'; events_path = root / 'events.log'
""" + _ACQUIRE_SOURCE + _RETRY_SOURCE + r"""
acquire()
try:
    append_event('+')
finally:
    release()
time.sleep(0.5)
acquire()
try:
    append_event('-')
finally:
    release()
"""


_DYING_CONCURRENCY_SUITE = r"""import os, time; from pathlib import Path
root = Path(__file__).resolve().parent
lock = root / 'concurrency.lock'; events_path = root / 'events.log'
""" + _ACQUIRE_SOURCE + _RETRY_SOURCE + r"""
acquire()
try:
    append_event('+')
finally:
    release()
os._exit(1)
"""


def _interleaving_suite(label):
    return f"""import time
print('{label} first', flush=True)
time.sleep(0.2)
print('{label} second', flush=True)
"""


_coverage_tree = coverage_tree


def _group(stdout, name):
    start = stdout.index(f'::group::tests/{name}\n')
    end = stdout.index('::endgroup::\n', start) + len('::endgroup::\n')
    return stdout[start:end]


def _fake_time(moments=()):
    moments = iter(moments)
    sleeps = []
    return SimpleNamespace(monotonic=lambda: next(moments, 0),
                           sleep=sleeps.append), sleeps


def _retry_target(path, outcomes, operation):
    target = MagicMock()
    target.__str__.return_value = path
    if operation == 'open':
        writer = MagicMock()
        writer.__enter__.return_value = writer
        outcomes = [writer if item is None else item for item in outcomes]
    getattr(target, operation).side_effect = outcomes
    return target


def _retry_call(kind, fake_time, target):
    if kind == 'read_events':
        return lambda: _read_events(target, monotonic=fake_time.monotonic,
                                    sleep=fake_time.sleep)
    fallback = MagicMock()
    namespace = {'time': fake_time, 'Path': Path,
                 'events_path': target if kind == 'append_event' else fallback,
                 'lock': (target if kind in ('acquire', 'release')
                          else fallback),
                 '__file__': '/fake/test_retry.py'}
    exec(  # pylint: disable=exec-used
        _ACQUIRE_SOURCE if kind == 'acquire' else _RETRY_SOURCE, namespace)
    if kind == 'append_event':
        return lambda: namespace[kind]('+')
    return namespace[kind]


def _read_events(path, *, monotonic=time.monotonic, sleep=time.sleep):
    deadline = monotonic() + 30
    while True:
        try:
            return path.read_text(encoding='utf-8').splitlines(keepends=True)
        except PermissionError as exc:
            if monotonic() >= deadline:
                raise AssertionError(
                    f'timed out reading event log {path}') from exc
            sleep(0.01)


def _replay_events(lines):
    lines = list(lines)
    if lines and not lines[-1].endswith('\n'):
        lines.pop()

    events = []
    for line_number, line in enumerate(lines, start=1):
        if not line.endswith('\n'):
            raise AssertionError(
                f'unterminated event log line {line_number}: {line!r}')
        event = line[:-1]
        if len(event) < 2 or event[0] not in '+-':
            raise AssertionError(
                f'invalid event log line {line_number}: {line!r}')
        events.append((event[0], event[1:]))

    opened = {}
    paired_indices = set()
    paired_names = set()
    orphans = set()
    for index, (marker, name) in enumerate(events):
        if marker == '+':
            if name in opened:
                raise AssertionError(
                    f'duplicate open event log line {index + 1}: '
                    f'{lines[index]!r}')
            opened[name] = index
        elif name in opened:
            paired_indices.update((opened.pop(name), index))
            paired_names.add(name)
        else:
            orphans.add(name)

    active = set()
    peak = 0
    for index, (marker, name) in enumerate(events):
        if index not in paired_indices:
            continue
        if marker == '+':
            active.add(name)
            peak = max(peak, len(active))
        else:
            active.remove(name)
    return peak, paired_names, set(opened), orphans


def test_every_suite_is_measured_in_its_own_process(tmp):
    """Dropping a suite or process isolation must fail."""
    suites = {'test_alpha.py': "print('alpha')\n",
              'test_beta.py': "print('beta')\n",
              'test_gamma.py': "print('gamma')\n"}
    result, invocations = _coverage_tree(tmp, suites)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(invocations) == len(suites), invocations
    process_ids = {invocation['pid'] for invocation in invocations}
    assert len(process_ids) == len(suites), invocations
    tree = Path(tmp) / 'tree'
    expected = {(tree / 'tests' / name).resolve() for name in suites}
    measured = {(tree / item['argv'][-1]).resolve() for item in invocations}
    assert measured == expected, invocations


def test_concurrent_measurements_write_distinct_coverage_files(tmp):
    """Concurrent suites must not overwrite another suite's data."""
    suites = {'test_alpha.py': "print('alpha')\n",
              'test_beta.py': "print('beta')\n",
              'test_gamma.py': "print('gamma')\n"}
    result, _invocations = _coverage_tree(
        tmp, suites, real_coverage=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    data_files = list((Path(tmp) / 'tree').glob('.coverage.*'))
    assert len(data_files) == len(suites), data_files
    assert len({path.name for path in data_files}) == len(suites), data_files


def test_measured_children_keep_coverage_start_but_not_runner_stdin(tmp):
    """Child setup must preserve tracing without inheriting readable stdin."""
    suites = {'test_subprocess_contract.py': "print('contract observed')\n"}
    result, invocations = _coverage_tree(tmp, suites)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(invocations) == 1, invocations
    assert (invocations[0]['coverage_process_start']
            == SYNTHETIC_PROCESS_START), invocations
    assert invocations[0]['stdin_byte'] == '', invocations


def test_suites_run_concurrently(tmp):
    """Each suite must observe its sibling while both are still running."""
    if (os.cpu_count() or 1) < 2:
        _util.skip('parallel coverage test requires at least two CPUs')
    suites = {'test_rendezvous_a.py': _RENDEZVOUS_SUITE,
              'test_rendezvous_b.py': _RENDEZVOUS_SUITE}
    result, _invocations = _coverage_tree(tmp, suites)
    assert result.returncode == 0, (result.stdout, result.stderr)


def _assert_worker_pool_record(stdout, expected_names, lines):
    peak, paired_names, unpaired, orphans = _replay_events(lines)
    assert paired_names <= expected_names, (
        f'unknown paired workers {paired_names - expected_names}: {lines!r}')
    assert not orphans, f'orphan worker closes {orphans}: {lines!r}'
    absent_names = expected_names - paired_names
    assert unpaired <= absent_names, f'unpaired outside absent: {unpaired}'
    for name in expected_names:
        group = _group(stdout, name)
        case = ('absent-and-reported' if name in absent_names
                else 'paired-and-healthy')
        has_marker = _FAILURE_MARKER in group
        assert has_marker == (name in absent_names), (
            f'{case} worker {name}: {group!r}')
    assert peak == 2, (
        f'paired peak {peak}; reaching worker cap 2 is mandatory: {lines!r}')
    return peak, paired_names, unpaired, orphans


def _worker_pool_outcome(tmp, unlaunchable=()):
    suites = {f'test_worker_{number}.py': _CONCURRENCY_EVENT_SUITE
              for number in range(4)}
    result, _invocations = _coverage_tree(tmp, suites, unlaunchable, 2)
    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = _read_events(Path(tmp) / 'tree' / 'tests' / 'events.log')
    replay = _assert_worker_pool_record(result.stdout, set(suites), lines)
    return result, lines, replay


def test_worker_pool_reaches_but_never_exceeds_cpu_count(tmp):
    """Four suites on two reported CPUs must reach a peak of exactly two."""
    _worker_pool_outcome(tmp)


def test_a_worker_that_never_runs_is_reported_not_counted(tmp):
    result, lines, replay = _worker_pool_outcome(
        tmp, unlaunchable=('test_worker_2.py',))
    peak, paired_names, unpaired, orphans = replay
    launchable = {f'test_worker_{number}.py' for number in (0, 1, 3)}
    assert paired_names == launchable, lines
    assert not (unpaired or orphans), (unpaired, orphans, lines)
    assert peak == 2, f'launchable peak {peak}, expected 2: {lines!r}'
    failed_group = _group(result.stdout, 'test_worker_2.py')
    assert 'LAUNCH FAILED:' in failed_group, failed_group
    without_marker = result.stdout.replace(_FAILURE_MARKER, '', 1)
    expected = launchable | {'test_worker_2.py'}
    try:
        _assert_worker_pool_record(without_marker, expected, lines)
    except AssertionError as exc:
        assert 'absent-and-reported' in str(exc), exc
    else:
        raise AssertionError(f'absent marker not required: {failed_group!r}')


def test_replay_exposes_a_three_suite_peak(_tmp):
    """Three paired open windows must remain visible as a cap breach."""
    lines = ['+test_a.py\n', '+test_b.py\n', '+test_c.py\n',
             '-test_a.py\n', '-test_b.py\n', '-test_c.py\n']
    peak, paired_names, unpaired, orphans = _replay_events(lines)
    assert peak == 3, f'peak lost from event log: {lines!r}'
    expected = {'test_a.py', 'test_b.py', 'test_c.py'}
    assert paired_names == expected, f'paired suites lost: {lines!r}'
    assert not unpaired, f'unpaired suite in complete log: {unpaired}'
    assert not orphans, f'orphan close in complete log: {orphans}'


def test_replay_reports_a_close_without_an_open(_tmp):
    """A close event without a matching open must name its suite."""
    line = '-test_orphan.py\n'
    peak, paired_names, unpaired, orphans = _replay_events([line])
    assert peak == 0, f'orphan log line changed peak: {line!r}'
    assert not paired_names, f'orphan log line paired a suite: {line!r}'
    assert not unpaired, f'orphan log line opened a suite: {line!r}'
    assert orphans == {'test_orphan.py'}, f'orphan not reported: {line!r}'


def test_replay_ignores_a_torn_trailing_line(_tmp):
    """A final event without its newline must not enter the replay."""
    lines = ['+test_complete.py\n', '-test_complete.py\n', '+test_torn.py']
    peak, paired_names, unpaired, orphans = _replay_events(lines)
    assert peak == 1, f'torn log tail changed peak: {lines[-1]!r}'
    assert paired_names == {'test_complete.py'}, (
        f'torn log tail changed paired suites: {lines[-1]!r}')
    assert not unpaired, f'torn log tail opened a suite: {lines[-1]!r}'
    assert not orphans, f'torn log tail closed a suite: {lines[-1]!r}'


def test_replay_finds_two_well_formed_overlapping_windows(_tmp):
    """Two complete overlapping windows must replay to a peak of two."""
    lines = ['+test_a.py\n', '+test_b.py\n',
             '-test_a.py\n', '-test_b.py\n']
    peak, paired_names, unpaired, orphans = _replay_events(lines)
    assert peak == 2, f'paired event peak was not two: {lines!r}'
    assert paired_names == {'test_a.py', 'test_b.py'}, (
        f'paired suites missing from complete log: {lines!r}')
    assert not unpaired, f'unpaired suite in complete log: {unpaired}'
    assert not orphans, f'orphan close in complete log: {orphans}'


def test_replay_excludes_an_unpaired_open_from_the_peak(_tmp):
    """A dead suite's open event must not inflate paired concurrency."""
    lines = ['+test_dead.py\n', '+test_a.py\n', '+test_b.py\n',
             '-test_a.py\n', '-test_b.py\n']
    peak, paired_names, unpaired, orphans = _replay_events(lines)
    assert peak == 2, f'unpaired open changed paired peak: {lines!r}'
    assert paired_names == {'test_a.py', 'test_b.py'}, (
        f'paired suites missing beside dead suite: {lines!r}')
    assert unpaired == {'test_dead.py'}, (
        f'dead suite was not unpaired: {lines!r}')
    assert not orphans, f'orphan close beside dead suite: {lines!r}'


def test_replay_rejects_an_invalid_complete_line(_tmp):
    """A complete event with an invalid marker must name its log line."""
    line = 'xtest_a.py\n'
    try:
        _replay_events([line])
    except AssertionError as exc:
        assert 'line 1' in str(exc), f'invalid line not named: {exc}'
        assert repr(line) in str(exc), f'invalid log text not named: {exc}'
    else:
        raise AssertionError(f'invalid event log line accepted: {line!r}')


def test_replay_rejects_a_duplicate_open(_tmp):
    """A second open for one suite must name its duplicate log line."""
    line = '+test_a.py\n'
    lines = [line, line]
    try:
        _replay_events(lines)
    except AssertionError as exc:
        assert 'line 2' in str(exc), f'duplicate line not named: {exc}'
        assert repr(line) in str(exc), f'duplicate log text not named: {exc}'
    else:
        raise AssertionError(f'duplicate event log line accepted: {lines!r}')


def _assert_retry_contract(kind):
    op, path, success = {
        'acquire': ('mkdir', '/fake/concurrency.lock', None),
        'append_event': ('open', '/fake/events.log', None),
        'release': ('rmdir', '/fake/concurrency.lock', None),
        'read_events': ('read_text', '/fake/events.log', '+test_a.py\n'),
    }[kind]
    expected_result = ['+test_a.py\n'] if success else None
    fake_time, sleeps = _fake_time()
    transient = _retry_target(
        path, [PermissionError('first'), PermissionError('second'), success],
        op)
    assert _retry_call(kind, fake_time, transient)() == expected_result
    assert sleeps == [0.01, 0.01], sleeps
    errors = ((PermissionError, FileExistsError) if kind == 'acquire'
              else (PermissionError,))
    for error in errors:
        fake_time, sleeps = _fake_time()
        tolerant = _retry_target(
            path, [error(f'failure {i}') for i in range(50)]
            + [success], op)
        assert _retry_call(kind, fake_time, tolerant)() == expected_result
        assert sleeps == [0.01] * 50, sleeps
    if kind == 'read_events':
        fake_time, _sleeps = _fake_time([0, 30])
        denied = PermissionError('denied')
        exhausted = _retry_target(path, [denied], op)
        try:
            _retry_call(kind, fake_time, exhausted)()
        except AssertionError as exc:
            assert 'reading event log' in str(exc) and path in str(exc), exc
            assert exc.__cause__ is denied, exc.__cause__
        else:
            raise AssertionError(f'exhausted {kind} retry returned silently')
    rejected = {
        'acquire': (), 'append_event': (FileExistsError,),
        'release': (FileExistsError,),
        'read_events': (FileExistsError,)}[kind]
    for error_type in rejected + (OSError,):
        fake_time, sleeps = _fake_time()
        error = error_type('not tolerated')
        try:
            _retry_call(kind, fake_time, _retry_target(path, [error], op))()
        except OSError as exc:
            assert exc is error and not sleeps, (exc, error, sleeps)
        else:
            raise AssertionError(f'rejected {kind} error was swallowed')
    fake_time, sleeps = _fake_time()
    immediate = _retry_target(path, [success], op)
    assert _retry_call(kind, fake_time, immediate)() == expected_result
    assert not sleeps, sleeps


def test_suite_strings_embed_the_retry_source_verbatim(_tmp):
    assert _CONCURRENCY_EVENT_SUITE.count(_ACQUIRE_SOURCE) == 1
    assert _DYING_CONCURRENCY_SUITE.count(_ACQUIRE_SOURCE) == 1
    assert _CONCURRENCY_EVENT_SUITE.count(_RETRY_SOURCE) == 1
    assert _DYING_CONCURRENCY_SUITE.count(_RETRY_SOURCE) == 1


def test_acquire_retry_contract(_tmp):
    _assert_retry_contract('acquire')


def test_append_event_retry_contract(_tmp):
    _assert_retry_contract('append_event')


def test_release_retry_contract(_tmp):
    _assert_retry_contract('release')


def test_read_events_retry_contract(_tmp):
    _assert_retry_contract('read_events')


def _assert_dying_worker_outcome(tmp, unlaunchable=()):
    dying_suite = 'test_worker_dies.py'
    healthy_sources = {f'test_worker_{number}.py': _CONCURRENCY_EVENT_SUITE
                       for number in range(3)}
    suites = {dying_suite: _DYING_CONCURRENCY_SUITE, **healthy_sources}
    result, _invocations = _coverage_tree(
        tmp, suites, unlaunchable=unlaunchable, cpu_count=2)
    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = _read_events(Path(tmp) / 'tree' / 'tests' / 'events.log')
    replay = _assert_worker_pool_record(result.stdout, set(suites), lines)
    _peak, paired_names, unpaired, _orphans = replay
    assert dying_suite not in paired_names, (
        f'dying suite paired unexpectedly: {lines!r}')
    failed_group = _group(result.stdout, dying_suite)
    death_case = ('mid-window' if dying_suite in unpaired
                  else 'never-entered')
    assert _FAILURE_MARKER in failed_group, (
        f'{dying_suite} {death_case} group: {failed_group!r}')
    return result, lines, replay


def test_a_suite_dying_mid_window_is_reported_not_counted(tmp):
    """A dead suite stays diagnosed without fabricating a pool breach."""
    _assert_dying_worker_outcome(tmp)


def test_an_absent_healthy_worker_is_reported_not_counted(tmp):
    result, lines, replay = _assert_dying_worker_outcome(
        tmp, unlaunchable=('test_worker_1.py',))
    peak, paired_names, unpaired, orphans = replay
    assert paired_names == {'test_worker_0.py', 'test_worker_2.py'}, lines
    assert unpaired == {'test_worker_dies.py'}, (unpaired, lines)
    assert not orphans, (orphans, lines)
    assert peak == 2, f'paired peak {peak}, expected 2: {lines!r}'
    failed_group = _group(result.stdout, 'test_worker_1.py')
    assert 'LAUNCH FAILED:' in failed_group, failed_group


def test_a_never_entered_dying_suite_is_reported_not_counted(tmp):
    result, lines, replay = _assert_dying_worker_outcome(
        tmp, unlaunchable=('test_worker_dies.py',))
    peak, paired_names, unpaired, _orphans = replay
    assert not unpaired, f'never-entered suite logged an open: {lines!r}'
    healthy = {f'test_worker_{number}.py' for number in range(3)}
    assert paired_names == healthy, lines
    assert peak == 2, f'never-entered paired peak was {peak}: {lines!r}'
    failed_group = _group(result.stdout, 'test_worker_dies.py')
    assert 'LAUNCH FAILED:' in failed_group, failed_group


def test_each_suite_output_is_one_contiguous_group(tmp):
    """Concurrent child writes must never interleave in workflow logs."""
    suites = {'test_alpha.py': _interleaving_suite('alpha'),
              'test_beta.py': _interleaving_suite('beta')}
    result, _invocations = _coverage_tree(tmp, suites)
    assert result.returncode == 0, (result.stdout, result.stderr)
    for name, label in (('test_alpha.py', 'alpha'),
                        ('test_beta.py', 'beta')):
        block = (f'::group::tests/{name}\n'
                 f'{label} first\n{label} second\n'
                 '::endgroup::\n')
        assert block in result.stdout, result.stdout
        assert result.stdout.count(f'::group::tests/{name}') == 1


def test_one_launch_failure_is_grouped_without_failing_the_run(tmp):
    """One unlaunchable child is reported while its sibling still counts."""
    suites = {'test_unlaunchable.py': "print('must not execute')\n",
              'test_passing.py': "print('passed')\n"}
    result, _invocations = _coverage_tree(
        tmp, suites, unlaunchable=('test_unlaunchable.py',))
    assert result.returncode == 0, (result.stdout, result.stderr)
    failed_group = _group(result.stdout, 'test_unlaunchable.py')
    prefix = ('::group::tests/test_unlaunchable.py\n'
              'LAUNCH FAILED: FileNotFoundError:')
    assert failed_group.startswith(prefix), failed_group
    assert _FAILURE_MARKER in failed_group, failed_group
    assert ('::group::tests/test_passing.py\npassed\n::endgroup::\n'
            in result.stdout), result.stdout


def test_every_launch_failure_fails_with_the_all_failed_guard(tmp):
    """All unlaunchable children reach groups and the terminal diagnostic."""
    suites = {'test_unlaunchable_a.py': "print('must not execute')\n",
              'test_unlaunchable_b.py': "print('must not execute')\n"}
    result, _invocations = _coverage_tree(
        tmp, suites, unlaunchable=tuple(suites))
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert result.stdout.count('::group::') == len(suites), (
        result.stdout, result.stderr)
    for name in suites:
        failed_group = _group(result.stdout, name)
        assert 'LAUNCH FAILED: FileNotFoundError:' in failed_group
        assert _FAILURE_MARKER in failed_group, failed_group
    message = ('every one of the 2 suites failed — refusing to\n'
               'report a coverage number for a program that did not run.')
    assert message in result.stderr, result.stderr


def test_one_failing_suite_does_not_fail_the_run(tmp):
    """Partial coverage survives while the matrix owns suite verdicts."""
    suites = {
        'test_failing.py': ("print('before failure', flush=True)\n"
                            "raise RuntimeError('coverage child boom')\n"),
        'test_passing.py': "print('passed')\n"}
    result, _invocations = _coverage_tree(tmp, suites)
    assert result.returncode == 0, (result.stdout, result.stderr)
    failed_group = _group(result.stdout, 'test_failing.py')
    assert result.stderr == '', (failed_group, result.stderr)
    assert 'before failure\nTraceback (most recent call last):\n' in (
        failed_group), failed_group
    assert 'RuntimeError: coverage child boom\n' in failed_group, failed_group
    assert _FAILURE_MARKER + '\n' in failed_group, failed_group


def test_require_all_refuses_one_failing_suite(tmp):
    """A measurement gate must not publish partial suite coverage."""
    suites = {'test_failing.py': 'raise SystemExit(1)\n',
              'test_passing.py': "print('passed')\n"}
    result, _invocations = _coverage_tree(
        tmp, suites, args=('--require-all',))
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert '1 of the 2 suites failed — refusing partial coverage' in (
        result.stderr), result.stderr


def test_every_suite_failing_fails_the_run(tmp):
    """A coverage number is refused when no suite completed successfully."""
    suites = {'test_failing_a.py': 'raise SystemExit(1)\n',
              'test_failing_b.py': 'raise SystemExit(2)\n'}
    result, _invocations = _coverage_tree(tmp, suites)
    assert result.returncode != 0, (result.stdout, result.stderr)
    message = ('every one of the 2 suites failed — refusing to\n'
               'report a coverage number for a program that did not run.')
    assert message in result.stderr, result.stderr


def test_no_suites_fails_the_run(tmp):
    """An empty tree must not report zero-percent coverage as a pass."""
    result, invocations = _coverage_tree(tmp, {})
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not invocations, invocations
    assert ('no suites found — refusing to report 0% as a pass'
            in result.stderr), result.stderr


def test_every_repository_suite_name_is_discovered(tmp):
    """No matching repository suite may disappear from measurement."""
    names = sorted(suite.name for suite in (ROOT / 'tests').glob('test_*.py'))
    result, invocations = _coverage_tree(tmp, {name: '' for name in names})
    assert result.returncode == 0, (result.stdout, result.stderr)
    measured = {Path(item['argv'][-1]).name for item in invocations}
    assert measured == set(names), (set(names) - measured,
                                    measured - set(names))


def test_unterminated_suite_output_gets_a_group_separator(tmp):
    """A child without a final newline cannot swallow the group terminator."""
    source = "import sys\nsys.stdout.write('no newline')\n"
    result, _calls = _coverage_tree(tmp, {'test_unterminated.py': source})
    assert result.returncode == 0, (result.stdout, result.stderr)
    expected = ('::group::tests/test_unterminated.py\nno newline\n'
                '::endgroup::\n')
    assert result.stdout == expected, result.stdout


raise SystemExit(_util.runner(_util.collect(dict(globals()))))
