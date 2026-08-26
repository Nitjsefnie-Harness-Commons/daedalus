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
record = root / 'coverage-invocations.jsonl'
line = json.dumps({'argv': sys.argv[1:], 'pid': os.getpid()}) + '\n'
flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
descriptor = os.open(record, flags, 0o600)
try:
    os.write(descriptor, line.encode('utf-8'))
finally:
    os.close(descriptor)
runpy.run_path(sys.argv[-1], run_name='__main__')
"""


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


def _interleaving_suite(label):
    return f"""import time
from pathlib import Path

marks = Path(__file__).resolve().parent / 'marks'
marks.mkdir(exist_ok=True)
print('{label} first', flush=True)
(marks / Path(__file__).name).touch()
deadline = time.monotonic() + 30
while len(list(marks.iterdir())) < 2 and time.monotonic() < deadline:
    time.sleep(0.05)
if len(list(marks.iterdir())) < 2:
    raise AssertionError('no sibling suite was writing concurrently')
print('{label} second', flush=True)
"""


def _coverage_tree(tmp, suites):
    """Copy the runner over fabricated suites and execute it in that tree."""
    root = Path(tmp) / 'tree'
    (root / 'scripts' / 'ci').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'coverage').mkdir()
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'coverage_suites.py',
                 root / 'scripts' / 'ci' / 'coverage_suites.py')
    shutil.copy2(ROOT / 'tests' / '_util.py', root / 'tests' / '_util.py')
    (root / 'coverage' / '__init__.py').write_text('', encoding='utf-8')
    (root / 'coverage' / '__main__.py').write_text(
        _FAKE_COVERAGE, encoding='utf-8')
    for name, source in suites.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith('COVERAGE_')
    }
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    result = subprocess.run(
        [sys.executable, 'scripts/ci/coverage_suites.py'], cwd=str(root),
        env=env, capture_output=True, text=True, timeout=120)
    records = root / 'coverage-invocations.jsonl'
    invocations = []
    if records.exists():
        invocations = [
            json.loads(line)
            for line in records.read_text(encoding='utf-8').splitlines()
        ]
    return result, invocations


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
    expected = {
        str(Path(tmp) / 'tree' / 'tests' / name)
        for name in suites
    }
    assert {
        tuple(invocation['argv'][:2]) for invocation in invocations
    } == {('run', '--parallel-mode')}, invocations
    assert {
        invocation['argv'][2] for invocation in invocations
    } == expected, invocations


def test_suites_run_concurrently(tmp):
    """Each suite must observe its sibling while both are still running."""
    if (os.cpu_count() or 1) < 2:
        _util.skip('parallel coverage test requires at least two CPUs')
    result, _invocations = _coverage_tree(tmp, {
        'test_rendezvous_a.py': _RENDEZVOUS_SUITE,
        'test_rendezvous_b.py': _RENDEZVOUS_SUITE,
    })
    assert result.returncode == 0, (result.stdout, result.stderr)


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


def test_one_failing_suite_does_not_fail_the_run(tmp):
    """Partial coverage survives while the matrix owns suite verdicts."""
    result, _invocations = _coverage_tree(tmp, {
        'test_failing.py': "print('before failure')\nraise SystemExit(1)\n",
        'test_passing.py': "print('passed')\n",
    })
    assert result.returncode == 0, (result.stdout, result.stderr)
    failed_group = (
        '::group::tests/test_failing.py\n'
        'before failure\n'
        '  (suite did not pass; its coverage still counts)\n'
        '::endgroup::\n'
    )
    assert failed_group in result.stdout, result.stdout


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


raise SystemExit(_util.runner(_util.collect(dict(globals()))))
