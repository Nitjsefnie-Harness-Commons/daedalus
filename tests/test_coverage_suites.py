#!/usr/bin/env python3
"""coverage_suites.py: concurrent measurement without mixed suite output."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

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


_CONCURRENCY_COUNTER_SUITE = r"""import json, time
from pathlib import Path

root = Path(__file__).resolve().parent
lock = root / 'concurrency.lock'
state_path = root / 'concurrency.json'


def acquire():
    deadline = time.monotonic() + 30
    while True:
        try:
            lock.mkdir()
            return
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise AssertionError('timed out acquiring concurrency lock')
            time.sleep(0.01)


acquire()
try:
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding='utf-8'))
    else:
        state = {'active': 0, 'peak': 0}
    state['active'] += 1
    state['peak'] = max(state['peak'], state['active'])
    state_path.write_text(json.dumps(state), encoding='utf-8')
finally:
    lock.rmdir()

time.sleep(0.5)

acquire()
try:
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['active'] -= 1
    state_path.write_text(json.dumps(state), encoding='utf-8')
finally:
    lock.rmdir()
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
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith('COVERAGE_')
    }
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
        env=env, input='runner-only input\n', capture_output=True, text=True,
        timeout=120)
    records = root / 'coverage-invocations'
    invocations = [
        json.loads(record.read_text(encoding='utf-8'))
        for record in sorted(records.glob('*.json'))
    ]
    return result, invocations


def _group(stdout, name):
    start = stdout.index(f'::group::tests/{name}\n')
    end = stdout.index('::endgroup::\n', start) + len('::endgroup::\n')
    return stdout[start:end]


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
    result, _invocations = _coverage_tree(tmp, {
        f'test_worker_{number}.py': _CONCURRENCY_COUNTER_SUITE
        for number in range(4)
    }, cpu_count=2)
    assert result.returncode == 0, (result.stdout, result.stderr)
    state = json.loads(
        (Path(tmp) / 'tree' / 'tests' / 'concurrency.json').read_text(
            encoding='utf-8'))
    assert state == {'active': 0, 'peak': 2}, state


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
