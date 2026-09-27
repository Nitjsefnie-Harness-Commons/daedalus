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
from _helper_binds import scan  # noqa: E402

ROOT = _util.ROOT
TESTS = ROOT / 'tests'

# The three suites of the workflow checkout reader split.
SUITES = ('test_checkout_pin.py', 'test_wfcheckout.py', 'test_wfscalars.py')

# The module the shared fixtures are imported FROM, as a module name
# rather than a path: an import is settled by its source, and this one
# source is the pattern every suite is meant to follow.
FIXTURE_SOURCE = '_wffixtures'

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


def _fixture_binds(tree, shared):
    """The shared names `tree` binds during module execution.

    A bind is a bind: a `for` target, a `with ... as`, an `except ... as`,
    a walrus, a comprehension target, a `type` alias and a tuple or star
    target in an assignment all establish one during module execution, and
    each shadows a shared fixture exactly as a `def` does. What does not
    count is a function-local binding, which is a namespace of its own and
    reaches no import.

    An import is the one carrier counted conditionally: bringing one of
    these names from the shared module is the pattern this rule exists to
    protect, and refusing it would turn the tree's right answer into a
    false positive. Bringing one from anywhere ELSE is a redefinition
    under another spelling, so it is refused.

    So this rule and `test_helper_reimplementation.py` read the same scope
    and answer different questions, which is the distinction that keeps
    both right. That one owns a name a helper DEFINES, so a walrus is not
    a second definition of it and needs an allowance row. This one owns a
    name at all, has no allowance table, and so refuses the bind itself.

    WHAT IT STILL CANNOT SEE. A `from _wffixtures import *` brings names
    no reader enumerates — the hole `test_helper_reimplementation.py`
    names in its own docstring. And `import _wffixtures as _refuses` is
    exempt, because its source is the shared module though it binds the
    MODULE rather than the function: `scan` records an `Import` and an
    `ImportFrom` from one source identically, so telling them apart needs
    a second scope walk this rule does not own. It fails loudly rather
    than silently — the name becomes a module object and the first call
    raises TypeError — and the shape that IS dangerous, a fixture name
    imported and then bound again, is `test_helper_shadow_boundaries.py`'s.
    """
    imports, binds = scan(tree)
    borrowed = {name for name in shared
                if name in imports
                and any(source != FIXTURE_SOURCE
                        for lines in imports[name].values()
                        for source in lines)}
    return sorted((set(binds) | borrowed) & shared)


def test_shared_workflow_fixtures_are_bound_only_in_their_module(tmp):
    """The shared fixture names are bound only in tests/_wffixtures.py.

    A module-execution bind elsewhere is either a reintroduced copy of a
    helper the shared module replaced or a shadow that wins over the
    import, so the suite reading the name stops reading the shared one.
    The scope is `_helper_binds.scan`'s rather than a walk of `tree.body`,
    which missed a `def` nested under an `if` and every bind form that is
    not a `def` or an assignment: a `for` target, a `with ... as`, an
    `except ... as`, a walrus, a `type` alias, and a tuple or star target
    in an assignment.
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
        hits = _fixture_binds(tree, shared)
        if hits:
            shadows.append((name, hits))
    assert not shadows, (
        'shared workflow fixtures bound outside tests/_wffixtures.py: '
        f'{shadows}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
