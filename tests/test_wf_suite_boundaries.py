#!/usr/bin/env python3
"""The workflow-reader suites import no sibling suite module.

The reader is split three ways — the structural walk, its scalar layer, and
the pin — and the split creates no boundary while one suite reaches another
for a helper: running the scalar suite then imports both other suites, and a
change to one lands in every run. Each suite is imported in a fresh
subprocess, and only its own name may land in `sys.modules`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT
TESTS = ROOT / 'tests'

# The three suites of the workflow checkout reader split.
SUITES = ('test_checkout_pin.py', 'test_wfcheckout.py', 'test_wfscalars.py')

_PROBE = (
    'import importlib, json, sys\n'
    'sys.path.insert(0, sys.argv[1])\n'
    'importlib.import_module(sys.argv[2][:-3])\n'
    'loaded = sorted(name for name in sys.modules if name.startswith('
    '"test_"))\n'
    'print(json.dumps(loaded))\n'
)


def test_importing_a_workflow_suite_loads_no_other_suite_module(tmp):
    """Each suite imports alone; a sibling suite module is never reached."""
    del tmp
    env = dict(os.environ)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    failures = []
    for suite in SUITES:
        proc = subprocess.run(
            [sys.executable, '-c', _PROBE, str(TESTS), suite],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            failures.append((suite, proc.stderr.strip()))
            continue
        loaded = json.loads(proc.stdout.strip().splitlines()[-1])
        if loaded != [suite[:-3]]:
            failures.append((suite, loaded))
    assert not failures, (
        'importing a workflow suite loaded other suite modules: '
        f'{failures}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
