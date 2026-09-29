"""Fabricate isolated repositories for coverage_suites.py controls."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import _util
from _repo import ROOT


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


SYNTHETIC_PROCESS_START = 'fabricated coverage startup'


def _launch_failure_site(unlaunchable):
    return f"""import subprocess
from pathlib import Path

_real_run = subprocess.run
_real_popen = subprocess.Popen
_unlaunchable = {tuple(unlaunchable)!r}


def _unlaunchable_command(command):
    if (isinstance(command, (list, tuple)) and command
            and Path(str(command[-1])).name in _unlaunchable):
        command = list(command)
        command[0] = str(Path(__file__).resolve().parent / 'missing-python')
    return command


def run(command, *args, **kwargs):
    return _real_run(_unlaunchable_command(command), *args, **kwargs)


def Popen(command, *args, **kwargs):
    return _real_popen(_unlaunchable_command(command), *args, **kwargs)


subprocess.run = run
subprocess.Popen = Popen
"""


def _suite_bound_site(bound):
    """Rewrite the one definition of the per-suite bound, in the child.

    The launcher resolves the bound when it launches, so setting the
    constant the module publishes is what a control that shrinks the real
    default must do: a parameter default captured at `def` time would not
    see it, and the control would go green against an unbounded launcher.
    """
    return ('import scripts.ci.suite_bound as _suite_bound\n'
            f'_suite_bound.DEFAULT_SUITE_TIMEOUT_S = {bound!r}\n')


def _cpu_count_site(cpu_count):
    return f"""import os

os.cpu_count = lambda: {cpu_count}
"""


def coverage_tree(
        tmp, suites, unlaunchable=(), cpu_count=None, args=(),
        real_coverage=False, suite_bound=None, outer_timeout=120,
        timeout_env=None):
    """Copy the runner over fabricated suites and execute it there.

    `suite_bound` rewrites the one per-suite bound the copied launcher
    resolves at launch time, which is how a control shrinks the real
    default instead of carrying a second number of its own. `outer_timeout`
    is THIS fixture's limit on the launcher, independent of the bound the
    launcher itself applies: without one, a launcher that never ends its
    wedged suite reproduces the hang the control exists to catch, and the
    control's own verdict never arrives.
    """
    root = Path(tmp) / 'tree'
    (root / 'scripts' / 'ci').mkdir(parents=True, exist_ok=True)
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'coverage-invocations').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'coverage_suites.py',
                 root / 'scripts' / 'ci' / 'coverage_suites.py')
    shutil.copy2(ROOT / 'scripts' / 'ci' / 'suite_bound.py',
                 root / 'scripts' / 'ci' / 'suite_bound.py')
    if real_coverage:
        shutil.copy2(ROOT / 'pyproject.toml', root / 'pyproject.toml')
    else:
        (root / 'coverage').mkdir(exist_ok=True)
        (root / 'coverage' / '__init__.py').write_text(
            _FAKE_COVERAGE_INIT, encoding='utf-8')
        (root / 'coverage' / '__main__.py').write_text(
            _FAKE_COVERAGE, encoding='utf-8')
    for name, source in suites.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    env = dict(os.environ)
    if timeout_env:
        env.update(timeout_env)
    if not real_coverage:
        env['COVERAGE_PROCESS_START'] = SYNTHETIC_PROCESS_START
    env['COVERAGE_FILE'] = str(root / '.coverage')
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
    if suite_bound is not None:
        sitecustomize += _suite_bound_site(suite_bound)
    if sitecustomize:
        (root / 'sitecustomize.py').write_text(
            sitecustomize, encoding='utf-8')
    result = subprocess.run(
        [sys.executable, 'scripts/ci/coverage_suites.py', *args],
        cwd=str(root), env=_util.child_coverage('keep', env, cwd=root),
        input='runner-only input\n', capture_output=True, text=True,
        timeout=outer_timeout)
    records = root / 'coverage-invocations'
    invocations = [json.loads(record.read_text(encoding='utf-8'))
                   for record in sorted(records.glob('*.json'))]
    return result, invocations
