"""Fabricate isolated repositories for the two suite launchers' controls.

The runners themselves and the helpers a control needs to read what a
launcher did are both here rather than in either suite: `tests/` forbids a
suite importing another suite, so a helper both of them want has to live
in a module under `tests/` that is not one.
"""
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
    'process': [os.getpid(), vars(sys).setdefault('_first_suite', str(suite))],
    'stdin_byte': stdin_byte.decode('ascii', errors='replace'),
}), encoding='utf-8')
runpy.run_path(sys.argv[-1], run_name='__main__')
"""


_FAKE_COVERAGE_INIT = """def process_startup(**_kwargs):
    pass
"""


SYNTHETIC_PROCESS_START = 'fabricated coverage startup'

# The sentence an operator reads, and the sentence no platform prints any
# more: a control asserts the sentence its run carried and REFUSES this
# one, so a conditional resurrecting the forced-only wording is red.
OPERATOR_ASKS_FIRST = ('a suite that took the request to stop keeps what it '
                       'had measured, and one that did not contributes '
                       'nothing')
OPERATOR_FORCES = 'a forced kill leaves a suite nothing to flush'


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


def _cpu_count_site(cpu_count):
    return f"""import os

os.cpu_count = lambda: {cpu_count}
"""


def coverage_tree(
        tmp, suites, unlaunchable=(), cpu_count=None, args=(),
        real_coverage=False):
    """Copy the runner over fabricated suites and execute it there."""
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
    if sitecustomize:
        (root / 'sitecustomize.py').write_text(
            sitecustomize, encoding='utf-8')
    # A generated `sitecustomize.py` is scaffolding, not repository source:
    # the coverage configuration measures the synthetic tree and attributes
    # what it measured to repository paths, so a child that imports one
    # hands `coverage report` a measured file with no source and the gate
    # refuses it. Scrubbing the collector from exactly these children stops
    # the RECORDING, on every platform -- an exclusion would only hide a
    # path shape that reads differently per platform. It also lowers the
    # declaration for the `unlaunchable` and `cpu_count` sites, which this
    # branch did not write; the lines they drive stay measured by the
    # children that have no sitecustomize.
    #
    # Two launches rather than one launch with a chosen mode, because the
    # guard reads the mode as a LITERAL at the `env=` keyword: a name bound
    # once to a `child_coverage(mode, ...)` is `invalid`, and the keep site
    # this module is allowlisted for stops existing.
    if (root / 'sitecustomize.py').exists():
        result = subprocess.run(
            [sys.executable, 'scripts/ci/coverage_suites.py', *args],
            cwd=str(root), env=_util.child_coverage('scrub', env, cwd=root),
            input='runner-only input\n', capture_output=True, text=True,
            timeout=120)
    else:
        result = subprocess.run(
            [sys.executable, 'scripts/ci/coverage_suites.py', *args],
            cwd=str(root), env=_util.child_coverage('keep', env, cwd=root),
            input='runner-only input\n', capture_output=True, text=True,
            timeout=120)
    records = root / 'coverage-invocations'
    invocations = [json.loads(record.read_text(encoding='utf-8'))
                   for record in sorted(records.glob('*.json'))]
    return result, invocations
