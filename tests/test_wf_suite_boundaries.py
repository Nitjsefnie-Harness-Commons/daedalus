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


def _module_imports(tree):
    """(line, bound name) for every `import X as Y` in the tree.

    `scan` records an `ast.Import` and an `ast.ImportFrom` from one
    source identically, so the node kind is the only thing that tells
    them apart, and it is readable at the line `scan` already reported
    without a second scope walk. Keyed on the bound name as well as the
    line, because the line alone is not enough: `import b as y; from b
    import _refuses` is one line and two import statements, and only the
    second one brings the fixture.

    `ast.walk` reaches function-local imports too, and they cannot
    matter: a name pair is only consulted for a line `scan` reported, and
    `scan` reports no import that module execution does not perform.
    """
    return {(node.lineno, alias.asname or alias.name.split('.')[0])
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names}


def _fixture_binds(tree, shared):
    """The shared names `tree` binds during module execution.

    A bind is a bind: a `for` target, a `with ... as`, an `except ... as`,
    a walrus, a comprehension target, a `type` alias and a tuple or star
    target in an assignment all establish one during module execution, and
    each shadows a shared fixture exactly as a `def` does. What does not
    count is a function-local binding, which is a namespace of its own and
    reaches no import.

    An import is the one carrier counted conditionally, and either half
    of its spelling can say no. Bringing one of these names from anywhere
    but the shared module is a redefinition under another spelling. And
    `import _wffixtures as _refuses` binds the MODULE under the fixture's
    own name, which shadows that name whatever the source:
    `from _wffixtures import _refuses` is the pattern this rule exists to
    protect, and this is not it.

    So this rule and `test_helper_reimplementation.py` read the same scope
    and answer different questions, which is the distinction that keeps
    both right. That one owns a name a helper DEFINES, so a walrus is not
    a second definition of it and needs an allowance row. This one owns a
    name at all, has no allowance table, and so refuses the bind itself.

    WHAT IT STILL CANNOT SEE, and it is the honest end state. A `from
    _wffixtures import *` brings a module's EXPORTED names, which no
    static reader enumerates without executing it; `_helper_binds.py`
    skips `alias.name == '*'` for the same reason, and
    `test_helper_reimplementation.py` names the same hole in its own
    docstring. Nothing about this rule would change that.
    """
    imports, binds = scan(tree)
    spelled = _module_imports(tree)
    borrowed = set()
    for name in shared & set(imports):
        for lineno, sources in imports[name].items():
            if ((lineno, name) in spelled
                    or any(source != FIXTURE_SOURCE for source in sources)):
                borrowed.add(name)
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
