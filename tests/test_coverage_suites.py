#!/usr/bin/env python3
"""coverage_suites.py: concurrent measurement without mixed suite output."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_FAKE_COVERAGE = r"""import json, os, runpy, sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
suite = Path(sys.argv[-1])
stdin_byte = sys.stdin.buffer.read(1)
record = (root / 'coverage-invocations'
          / f'{suite.name}.{os.getpid()}.json')
record.write_text(json.dumps({
    'argv': sys.argv[1:],
    'coverage_process_start': os.environ.get('COVERAGE_PROCESS_START'),
    'pid': os.getpid(),
    'stdin_byte': stdin_byte.decode('ascii', errors='replace'),
}), encoding='utf-8')
runpy.run_path(sys.argv[-1], run_name='__main__')
"""


_FAKE_COVERAGE_INIT = """def process_startup(**_kwargs):
    pass
"""


_SYNTHETIC_PROCESS_START = 'fabricated coverage startup'


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


_RETRY_SOURCE = r"""def append_event(marker):
    deadline = time.monotonic() + 30
    while True:
        try:
            with events_path.open('a', encoding='utf-8') as events:
                events.write(f'{marker}{Path(__file__).name}\n')
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f'timed out appending event to {events_path}') from exc
            time.sleep(0.01)
def release():
    deadline = time.monotonic() + 30
    while True:
        try:
            lock.rmdir()
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f'timed out removing lock directory {lock}') from exc
            time.sleep(0.01)
"""


_CONCURRENCY_EVENT_SUITE = r"""import time; from pathlib import Path
root = Path(__file__).resolve().parent
lock = root / 'concurrency.lock'; events_path = root / 'events.log'
def acquire():
    deadline = time.monotonic() + 30
    while True:
        try:
            lock.mkdir(); return
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise AssertionError('timed out acquiring concurrency lock')
            time.sleep(0.01)
""" + _RETRY_SOURCE + r"""
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
def acquire():
    deadline = time.monotonic() + 30
    while True:
        try:
            lock.mkdir(); return
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise AssertionError('timed out acquiring concurrency lock')
            time.sleep(0.01)
""" + _RETRY_SOURCE + r"""
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


def _launch_failure_site(unlaunchable):
    return f"""import subprocess
from pathlib import Path

_real_run = subprocess.run
_unlaunchable = {tuple(unlaunchable)!r}


def run(command, *args, **kwargs):
    if (isinstance(command, (list, tuple)) and command
            and Path(str(command[-1])).name in _unlaunchable):
        command = list(command)
        command[0] = str(Path(__file__).resolve().parent / 'missing-python')
    return _real_run(command, *args, **kwargs)


subprocess.run = run
"""


def _cpu_count_site(cpu_count):
    return f"""import os

os.cpu_count = lambda: {cpu_count}
"""


def _coverage_tree(tmp, suites, unlaunchable=(), cpu_count=None):
    """Copy the runner over fabricated suites and execute it in that tree."""
    root = Path(tmp) / 'tree'
    (root / 'scripts' / 'ci').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'coverage').mkdir()
    (root / 'coverage-invocations').mkdir()
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'coverage_suites.py',
                 root / 'scripts' / 'ci' / 'coverage_suites.py')
    (root / 'coverage' / '__init__.py').write_text(
        _FAKE_COVERAGE_INIT, encoding='utf-8')
    (root / 'coverage' / '__main__.py').write_text(
        _FAKE_COVERAGE, encoding='utf-8')
    for name, source in suites.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    env = _util.coverage_free_environment(os.environ)
    env['COVERAGE_PROCESS_START'] = _SYNTHETIC_PROCESS_START
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    inherited_path = env.get('PYTHONPATH')
    env['PYTHONPATH'] = str(root)
    if inherited_path:
        env['PYTHONPATH'] += os.pathsep + inherited_path
    sitecustomize = ''
    if unlaunchable:
        sitecustomize += _launch_failure_site(unlaunchable)
    if cpu_count is not None:
        sitecustomize += _cpu_count_site(cpu_count)
    if sitecustomize:
        (root / 'sitecustomize.py').write_text(
            sitecustomize, encoding='utf-8')
    result = subprocess.run(
        [sys.executable, 'scripts/ci/coverage_suites.py'], cwd=str(root),
        env=_util.child_coverage('keep', env, cwd=root),
        input='runner-only input\n', capture_output=True, text=True,
        timeout=120)
    records = root / 'coverage-invocations'
    invocations = [json.loads(record.read_text(encoding='utf-8'))
                   for record in sorted(records.glob('*.json'))]
    return result, invocations


def _group(stdout, name):
    start = stdout.index(f'::group::tests/{name}\n')
    end = stdout.index('::endgroup::\n', start) + len('::endgroup::\n')
    return stdout[start:end]


def _fake_time(moments):
    moments = iter(moments)
    sleeps = []
    return SimpleNamespace(
        monotonic=lambda: next(moments), sleep=sleeps.append), sleeps


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
    namespace = {'time': fake_time, 'Path': Path,
                 'events_path': (
                     target if kind == 'append_event' else MagicMock()),
                 'lock': target if kind == 'release' else MagicMock()}
    namespace['__file__'] = '/fake/test_retry.py'
    exec(_RETRY_SOURCE, namespace)  # pylint: disable=exec-used
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


def test_every_suite_is_measured_in_its_own_parallel_mode_process(tmp):
    """Dropping a suite, parallel mode, or process isolation must fail."""
    suites = {
        'test_alpha.py': "print('alpha')\n",
        'test_beta.py': "print('beta')\n",
        'test_gamma.py': "print('gamma')\n",
    }
    result, invocations = _coverage_tree(tmp, suites)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(invocations) == len(suites), invocations
    process_ids = {invocation['pid'] for invocation in invocations}
    assert len(process_ids) == len(suites), invocations
    tree = Path(tmp) / 'tree'
    expected = {(tree / 'tests' / name).resolve() for name in suites}
    assert {
        tuple(invocation['argv'][:2]) for invocation in invocations
    } == {('run', '--parallel-mode')}, invocations
    measured = {
        (tree / invocation['argv'][2]).resolve()
        for invocation in invocations
    }
    assert measured == expected, invocations


def test_measured_children_keep_coverage_start_but_not_runner_stdin(tmp):
    """Child setup must preserve tracing without inheriting readable stdin."""
    result, invocations = _coverage_tree(tmp, {
        'test_subprocess_contract.py': "print('contract observed')\n",
    })
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len(invocations) == 1, invocations
    assert (invocations[0]['coverage_process_start']
            == _SYNTHETIC_PROCESS_START), invocations
    assert invocations[0]['stdin_byte'] == '', invocations


def test_suites_run_concurrently(tmp):
    """Each suite must observe its sibling while both are still running."""
    if (os.cpu_count() or 1) < 2:
        _util.skip('parallel coverage test requires at least two CPUs')
    result, _invocations = _coverage_tree(tmp, {
        'test_rendezvous_a.py': _RENDEZVOUS_SUITE,
        'test_rendezvous_b.py': _RENDEZVOUS_SUITE,
    })
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_worker_pool_reaches_but_never_exceeds_cpu_count(tmp):
    """Four suites on two reported CPUs must reach a peak of exactly two."""
    suites = {
        f'test_worker_{number}.py': _CONCURRENCY_EVENT_SUITE
        for number in range(4)
    }
    result, _invocations = _coverage_tree(tmp, suites, cpu_count=2)
    assert result.returncode == 0, (result.stdout, result.stderr)
    events_path = Path(tmp) / 'tree' / 'tests' / 'events.log'
    lines = _read_events(events_path)
    peak, paired_names, unpaired, orphans = _replay_events(lines)
    expected_names = set(suites)
    assert paired_names == expected_names, (
        f'paired worker suites differ: expected {sorted(expected_names)}, '
        f'observed {sorted(paired_names)}; log: {lines!r}')
    assert not unpaired, (
        f'unpaired worker suites {sorted(unpaired)} in log: {lines!r}')
    assert not orphans, (
        f'orphan worker suite closes {sorted(orphans)} in log: {lines!r}')
    failure_marker = '(suite did not pass; its coverage still counts)'
    for name in suites:
        group = _group(result.stdout, name)
        assert failure_marker not in group, (
            f'{name} was reported failed: {group!r}')
    assert peak == 2, f'worker pool paired peak was {peak}: {lines!r}'


def test_replay_exposes_a_three_suite_peak(_tmp):
    """Three paired open windows must remain visible as a cap breach."""
    lines = [
        '+test_a.py\n',
        '+test_b.py\n',
        '+test_c.py\n',
        '-test_a.py\n',
        '-test_b.py\n',
        '-test_c.py\n',
    ]
    peak, paired_names, unpaired, orphans = _replay_events(lines)
    assert peak == 3, f'peak lost from event log: {lines!r}'
    assert paired_names == {
        'test_a.py', 'test_b.py', 'test_c.py'
    }, f'paired suites lost from complete log: {lines!r}'
    assert not unpaired, f'unpaired suite in complete log: {unpaired}'
    assert not orphans, f'orphan close in complete log: {orphans}'


def test_replay_reports_a_close_without_an_open(_tmp):
    """A close event without a matching open must name its suite."""
    line = '-test_orphan.py\n'
    peak, paired_names, unpaired, orphans = _replay_events([line])
    assert peak == 0, f'orphan log line changed peak: {line!r}'
    assert not paired_names, f'orphan log line paired a suite: {line!r}'
    assert not unpaired, f'orphan log line opened a suite: {line!r}'
    assert orphans == {'test_orphan.py'}, (
        f'orphan log line not reported: {line!r}')


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
    lines = [
        '+test_a.py\n',
        '+test_b.py\n',
        '-test_a.py\n',
        '-test_b.py\n',
    ]
    peak, paired_names, unpaired, orphans = _replay_events(lines)
    assert peak == 2, f'paired event peak was not two: {lines!r}'
    assert paired_names == {'test_a.py', 'test_b.py'}, (
        f'paired suites missing from complete log: {lines!r}')
    assert not unpaired, f'unpaired suite in complete log: {unpaired}'
    assert not orphans, f'orphan close in complete log: {orphans}'


def test_replay_excludes_an_unpaired_open_from_the_peak(_tmp):
    """A dead suite's open event must not inflate paired concurrency."""
    lines = [
        '+test_dead.py\n',
        '+test_a.py\n',
        '+test_b.py\n',
        '-test_a.py\n',
        '-test_b.py\n',
    ]
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
    operation, path, success, expected_operation = {
        'append_event': ('open', '/fake/events.log', None, 'appending event'),
        'release': ('rmdir', '/fake/concurrency.lock', None,
                    'removing lock directory'),
        'read_events': ('read_text', '/fake/events.log', '+test_a.py\n',
                        'reading event log'),
    }[kind]
    expected_result = ['+test_a.py\n'] if success else None
    fake_time, sleeps = _fake_time([0, 1, 2])
    transient = _retry_target(
        path, [PermissionError('first'), PermissionError('second'), success],
        operation)
    result = _retry_call(kind, fake_time, transient)()
    assert sleeps == [0.01, 0.01], sleeps
    assert result == expected_result, result
    fake_time, _sleeps = _fake_time([0, 30])
    denied = PermissionError('denied')
    exhausted = _retry_target(path, [denied], operation)
    try:
        _retry_call(kind, fake_time, exhausted)()
    except AssertionError as exc:
        assert expected_operation in str(exc), exc
        assert path in str(exc), exc
        if kind == 'read_events':
            assert exc.__cause__ is denied, exc.__cause__
    else:
        raise AssertionError(f'exhausted {kind} retry returned silently')
    fake_time, sleeps = _fake_time([0])
    error = OSError('not a permission denial')
    other_error = _retry_target(path, [error], operation)
    try:
        _retry_call(kind, fake_time, other_error)()
    except OSError as exc:
        assert exc is error, (exc, error)
    else:
        raise AssertionError(f'non-permission {kind} error was swallowed')
    assert not sleeps, sleeps
    fake_time, sleeps = _fake_time([0])
    immediate = _retry_target(path, [success], operation)
    result = _retry_call(kind, fake_time, immediate)()
    assert not sleeps, sleeps
    assert result == expected_result, result


def test_suite_strings_embed_the_retry_source_verbatim(_tmp):
    assert _CONCURRENCY_EVENT_SUITE.count(_RETRY_SOURCE) == 1
    assert _DYING_CONCURRENCY_SUITE.count(_RETRY_SOURCE) == 1


def test_append_event_retry_contract(_tmp):
    _assert_retry_contract('append_event')


def test_release_retry_contract(_tmp):
    _assert_retry_contract('release')


def test_read_events_retry_contract(_tmp):
    _assert_retry_contract('read_events')


def test_a_suite_dying_mid_window_is_reported_not_counted(tmp):
    """A dead suite stays diagnosed without fabricating a pool breach."""
    dying_suite = 'test_worker_dies.py'
    suites = {
        dying_suite: _DYING_CONCURRENCY_SUITE,
        **{
            f'test_worker_{number}.py': _CONCURRENCY_EVENT_SUITE
            for number in range(3)
        },
    }
    result, _invocations = _coverage_tree(tmp, suites, cpu_count=2)
    assert result.returncode == 0, (result.stdout, result.stderr)
    events_path = Path(tmp) / 'tree' / 'tests' / 'events.log'
    lines = _read_events(events_path)
    peak, paired_names, unpaired, orphans = _replay_events(lines)
    healthy_suites = set(suites) - {dying_suite}
    assert paired_names == healthy_suites, (
        f'healthy paired suites differ: expected {sorted(healthy_suites)}, '
        f'observed {sorted(paired_names)}; log: {lines!r}')
    assert unpaired == {dying_suite}, (
        f'{dying_suite} was not the sole unpaired suite: {lines!r}')
    assert not orphans, f'orphan close in event log: {lines!r}'
    failure_marker = '(suite did not pass; its coverage still counts)'
    for name in healthy_suites:
        group = _group(result.stdout, name)
        assert failure_marker not in group, (
            f'healthy suite {name} was reported failed: {group!r}')
    failed_group = _group(result.stdout, dying_suite)
    assert ('  (suite did not pass; its coverage still counts)'
            in failed_group), f'{dying_suite} group: {failed_group!r}'
    assert peak == 2, (
        f'paired event peak was {peak}, expected 2: {lines!r}')


def test_each_suite_output_is_one_contiguous_group(tmp):
    """Concurrent child writes must never interleave in workflow logs."""
    result, _invocations = _coverage_tree(tmp, {
        'test_alpha.py': _interleaving_suite('alpha'),
        'test_beta.py': _interleaving_suite('beta'),
    })
    assert result.returncode == 0, (result.stdout, result.stderr)
    for name, label in (('test_alpha.py', 'alpha'),
                        ('test_beta.py', 'beta')):
        block = (
            f'::group::tests/{name}\n'
            f'{label} first\n'
            f'{label} second\n'
            '::endgroup::\n'
        )
        assert block in result.stdout, result.stdout
        assert result.stdout.count(f'::group::tests/{name}') == 1


def test_one_launch_failure_is_grouped_without_failing_the_run(tmp):
    """One unlaunchable child is reported while its sibling still counts."""
    result, _invocations = _coverage_tree(tmp, {
        'test_unlaunchable.py': "print('must not execute')\n",
        'test_passing.py': "print('passed')\n",
    }, unlaunchable=('test_unlaunchable.py',))
    assert result.returncode == 0, (result.stdout, result.stderr)
    failed_group = _group(result.stdout, 'test_unlaunchable.py')
    assert failed_group.startswith(
        '::group::tests/test_unlaunchable.py\n'
        'LAUNCH FAILED: FileNotFoundError:'), failed_group
    assert ('  (suite did not pass; its coverage still counts)'
            in failed_group), failed_group
    assert ('::group::tests/test_passing.py\npassed\n::endgroup::\n'
            in result.stdout), result.stdout


def test_every_launch_failure_fails_with_the_all_failed_guard(tmp):
    """All unlaunchable children reach groups and the terminal diagnostic."""
    suites = {
        'test_unlaunchable_a.py': "print('must not execute')\n",
        'test_unlaunchable_b.py': "print('must not execute')\n",
    }
    result, _invocations = _coverage_tree(
        tmp, suites, unlaunchable=tuple(suites))
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert result.stdout.count('::group::') == len(suites), (
        result.stdout, result.stderr)
    for name in suites:
        failed_group = _group(result.stdout, name)
        assert 'LAUNCH FAILED: FileNotFoundError:' in failed_group
        assert ('  (suite did not pass; its coverage still counts)'
                in failed_group), failed_group
    assert ('every one of the 2 suites failed — refusing to\n'
            in result.stderr), result.stderr
    assert ('report a coverage number for a program that did not run.'
            in result.stderr), result.stderr


def test_one_failing_suite_does_not_fail_the_run(tmp):
    """Partial coverage survives while the matrix owns suite verdicts."""
    result, _invocations = _coverage_tree(tmp, {
        'test_failing.py': (
            "print('before failure', flush=True)\n"
            "raise RuntimeError('coverage child boom')\n"
        ),
        'test_passing.py': "print('passed')\n",
    })
    assert result.returncode == 0, (result.stdout, result.stderr)
    failed_group = _group(result.stdout, 'test_failing.py')
    assert result.stderr == '', (failed_group, result.stderr)
    assert 'before failure\nTraceback (most recent call last):\n' in (
        failed_group), failed_group
    assert 'RuntimeError: coverage child boom\n' in failed_group, failed_group
    assert ('  (suite did not pass; its coverage still counts)\n'
            in failed_group), failed_group


def test_every_suite_failing_fails_the_run(tmp):
    """A coverage number is refused when no suite completed successfully."""
    result, _invocations = _coverage_tree(tmp, {
        'test_failing_a.py': 'raise SystemExit(1)\n',
        'test_failing_b.py': 'raise SystemExit(2)\n',
    })
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert ('every one of the 2 suites failed — refusing to\n'
            in result.stderr), result.stderr
    assert ('report a coverage number for a program that did not run.'
            in result.stderr), result.stderr


def test_no_suites_fails_the_run(tmp):
    """An empty tree must not report zero-percent coverage as a pass."""
    result, invocations = _coverage_tree(tmp, {})
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not invocations, invocations
    assert ('no suites found — refusing to report 0% as a pass'
            in result.stderr), result.stderr


def test_every_repository_suite_name_is_discovered(tmp):
    """No matching repository suite may disappear from measurement."""
    names = sorted(
        suite.name for suite in (ROOT / 'tests').glob('test_*.py'))
    result, invocations = _coverage_tree(
        tmp, {name: '' for name in names})
    assert result.returncode == 0, (result.stdout, result.stderr)
    measured = {Path(item['argv'][2]).name for item in invocations}
    assert measured == set(names), (set(names) - measured,
                                    measured - set(names))


def test_unterminated_suite_output_gets_a_group_separator(tmp):
    """A child without a final newline cannot swallow the group terminator."""
    result, _invocations = _coverage_tree(tmp, {
        'test_unterminated.py': "import sys\nsys.stdout.write('no newline')\n",
    })
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == (
        '::group::tests/test_unterminated.py\n'
        'no newline\n'
        '::endgroup::\n'
    ), result.stdout


raise SystemExit(_util.runner(_util.collect(dict(globals()))))
