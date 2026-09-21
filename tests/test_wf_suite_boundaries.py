#!/usr/bin/env python3
"""The workflow-reader suites import no sibling suite module.

The reader is split three ways — the structural walk, its scalar layer, and
the pin — and the split creates no boundary while one suite reaches another
for a helper: running the scalar suite then imports both other suites, and a
change to one lands in every run. Each suite is imported in a fresh
subprocess, and only its own name may land in `sys.modules`.
"""
import ast
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


def test_shared_workflow_fixtures_are_bound_only_in_their_module(tmp):
    """The five shared fixture names are bound only in tests/_wffixtures.py.

    A module-level def or assignment elsewhere is either a reintroduced
    copy of a helper the shared module replaced or a shadow that wins over
    the import, so the suite reading the name stops reading the shared one.
    Only module-level bindings count: `_real_step` is a different name and
    a function-local binding never reaches another module's import.
    """
    del tmp
    shared = frozenset((
        'BLOCK_NEEDS', 'BLOCK_OUTPUTS', '_real', '_replaced', '_refuses'))
    named = subprocess.run(
        ['git', 'ls-files', 'tests/*.py'], cwd=ROOT, capture_output=True,
        text=True, check=True).stdout.splitlines()
    assert named, 'git ls-files named no tests module'
    shadows = []
    for name in named:
        if name == 'tests/_wffixtures.py':
            continue
        tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
        bound = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Assign):
                bound.update(target.id for target in node.targets
                             if isinstance(target, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(
                    node.target, ast.Name):
                bound.add(node.target.id)
        hits = sorted(bound & shared)
        if hits:
            shadows.append((name, hits))
    assert not shadows, (
        'shared workflow fixtures bound outside tests/_wffixtures.py: '
        f'{shadows}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
