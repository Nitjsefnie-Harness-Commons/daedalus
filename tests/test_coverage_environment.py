#!/usr/bin/env python3
"""Every test subprocess either runs provably safe or declares its env.

A Python child that inherits COVERAGE_* into a working directory that
[tool.coverage.paths] does not map back onto the repository records
coverage against paths that vanish with the temporary tree, and a later
`coverage combine` fails with `No source for code`. This guard fails
CLOSED: it proves each launch safe, and a launch it cannot prove safe is
a violation, so a spelling it does not understand can never slip past.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_DECLARATION = 'child_coverage'
_NON_PYTHON_BASENAMES = frozenset({
    'node', 'git', 'bash', 'sh', 'zsh', 'cmd', 'pwsh',
})
_MUTATING_METHODS = frozenset({
    'clear', 'pop', 'popitem', 'setdefault', 'update',
})
_ROOT_SPELLINGS = frozenset({
    'ROOT', 'str(ROOT)', '_util.ROOT', 'str(_util.ROOT)',
    'behaviour.ROOT', 'str(behaviour.ROOT)',
})

# The launches that declare 'keep', as module::function so an unrelated
# edit above a site does not churn the list. A keep site with no entry
# fails, and an entry with no keep site fails. Every tree below sits
# under the `*/tree` anchor that [tool.coverage.paths] maps back onto
# the repository (see pyproject.toml).
_KEEP_ALLOWLIST = frozenset({
    # The fixture hands the child a synthetic COVERAGE_PROCESS_START and
    # asserts it arrives; a scrub would strip the very variable under test.
    'tests/test_coverage_suites.py::_coverage_tree',
    # run_tests.py is measured where it stands in the copied tree;
    # scrubbing would report the file every one of those tests drives at 0%.
    'tests/test_suite_runner.py::_runner_tree',
    # The copied checker runs from the mapped copy so its lines count.
    'tests/test_version_contract.py::_run_checker',
    # Same mapped copy: drift detection is the checker's main path.
    'tests/test_version_contract.py::test_check_versions_detects_drift',
    # Same mapped copy: site completeness is asserted from its output.
    'tests/test_version_contract.py::'
    'test_check_versions_sites_all_present_in_copy',
})


def _is_root_spelling(node):
    """Whether an expression provably names the repository root."""
    if ast.unparse(node) in _ROOT_SPELLINGS:
        return True
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, str)):
        return _is_root_spelling(node.left)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'str' and len(node.args) == 1
            and not node.keywords):
        return _is_root_spelling(node.args[0])
    return False


class _ModuleFacts:
    """The syntactic facts one module's launches are judged against."""

    def __init__(self, tree):
        self.subprocess_modules = set()
        self.launch_callables = set()
        self.declaration_modules = set()
        self.declaration_functions = set()
        self.module_bindings = {}
        self.chdir_calls = []
        self._collect(tree)

    def _collect(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == 'subprocess':
                        self.subprocess_modules.add(
                            alias.asname or alias.name)
                    if alias.name == '_util':
                        self.declaration_modules.add(
                            alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if (node.module == 'subprocess'
                            and alias.name in {'run', 'Popen'}):
                        self.launch_callables.add(bound)
                    if (node.module == '_util'
                            and alias.name == _DECLARATION):
                        self.declaration_functions.add(bound)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if _is_launch_alias(node, self.subprocess_modules):
                    self.launch_callables.add(node.targets[0].id)
            elif isinstance(node, ast.Call):
                function = node.func
                if (isinstance(function, ast.Attribute)
                        and function.attr == 'chdir'
                        and isinstance(function.value, ast.Name)
                        and function.value.id == 'os'):
                    is_root = (bool(node.args)
                               and _is_root_spelling(node.args[0]))
                    self.chdir_calls.append((node.lineno, is_root))
        self._collect_bindings(tree.body)

    def _collect_bindings(self, statements):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            targets, value = [], None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            for target in targets:
                if isinstance(target, ast.Name):
                    self.module_bindings.setdefault(
                        target.id, []).append((node.lineno, value))
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    self._collect_bindings([child])

    def declaration_mode(self, node):
        """The mode when `node` is a direct child_coverage(mode) call."""
        if not isinstance(node, ast.Call) or not node.args:
            return None
        function = node.func
        if isinstance(function, ast.Name):
            if function.id not in self.declaration_functions:
                return None
        elif not (isinstance(function, ast.Attribute)
                  and function.attr == _DECLARATION
                  and isinstance(function.value, ast.Name)
                  and function.value.id in self.declaration_modules):
            return None
        mode = node.args[0]
        if (isinstance(mode, ast.Constant)
                and mode.value in {'scrub', 'keep'}):
            return mode.value
        return 'invalid'


def _is_launch_alias(node, subprocess_modules):
    """Whether an Assign binds a plain name to subprocess.run/Popen."""
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return False
    value = node.value
    return (isinstance(value, ast.Attribute)
            and value.attr in {'run', 'Popen'}
            and isinstance(value.value, ast.Name)
            and value.value.id in subprocess_modules)


def _launch_method(node, facts):
    """'run' or 'Popen' when `node` launches through subprocess."""
    function = node.func
    if (isinstance(function, ast.Attribute)
            and function.attr in {'run', 'Popen'}
            and isinstance(function.value, ast.Name)
            and function.value.id in facts.subprocess_modules):
        return function.attr
    if (isinstance(function, ast.Name)
            and function.id in facts.launch_callables):
        return function.id
    return None


def _first_command_literal(node):
    """The first command word when it is a plain string literal."""
    if not node.args:
        return None
    command = node.args[0]
    while True:
        if isinstance(command, (ast.List, ast.Tuple)) and command.elts:
            command = command.elts[0]
        elif (isinstance(command, ast.BinOp)
              and isinstance(command.op, ast.Add)):
            command = command.left
        elif isinstance(command, ast.Subscript):
            command = command.value
        else:
            break
    if isinstance(command, ast.Constant) and isinstance(command.value, str):
        return command.value
    return None


def _runs_no_python(node):
    """A literal node/git/shell launch runs no Python and is exempt."""
    first = _first_command_literal(node)
    if first is None:
        return False
    basename = first.replace('\\', '/').rsplit('/', 1)[-1].lower()
    return basename in _NON_PYTHON_BASENAMES


def _unresolved_cwd(node, facts):
    """Why the launch's working directory is not provably safe, or None."""
    spread = False
    for keyword in node.keywords:
        if keyword.arg == 'cwd':
            if _is_root_spelling(keyword.value):
                return None
            return f'cwd={ast.unparse(keyword.value)}'
        if keyword.arg is None:
            spread = True
    if spread:
        return 'cwd may arrive through a ** spread'
    earlier = [line for line, is_root in facts.chdir_calls
               if not is_root and line < node.lineno]
    if earlier:
        return f'os.chdir at line {earlier[0]} may have moved the cwd'
    return None


def _assignment_target_lines(tree, name):
    """Every line where `name` itself is an assignment target."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                lines.append(node.lineno)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for part in ast.walk(target):
                    if isinstance(part, ast.Name) and part.id == name:
                        lines.append(node.lineno)
    return lines


def _mutation_lines(tree, name):
    """Every line where `name` is mutated rather than plainly rebound."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            for target in targets:
                for part in ast.walk(target):
                    if (isinstance(part, ast.Subscript)
                            and isinstance(part.value, ast.Name)
                            and part.value.id == name):
                        lines.append(node.lineno)
        elif isinstance(node, (ast.AugAssign, ast.Delete)):
            targets = ([node.target] if isinstance(node, ast.AugAssign)
                       else node.targets)
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name:
                    lines.append(node.lineno)
                elif (isinstance(target, ast.Subscript)
                      and isinstance(target.value, ast.Name)
                      and target.value.id == name):
                    lines.append(node.lineno)
        elif (isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr in _MUTATING_METHODS
              and isinstance(node.func.value, ast.Name)
              and node.func.value.id == name):
            lines.append(node.lineno)
    return lines


def _bare_name_problem(name, facts, tree):
    """Why a bare env= name is no declaration, or None when it is one."""
    bindings = facts.module_bindings.get(name, [])
    if len(bindings) != 1:
        return (f'env={name} has {len(bindings)} module-level bindings, '
                'not exactly one')
    target_lines = _assignment_target_lines(tree, name)
    if len(target_lines) != 1:
        return (f'env={name} is an assignment target at lines '
                f'{target_lines}')
    mutations = _mutation_lines(tree, name)
    if mutations:
        return f'env={name} is mutated at line {mutations[0]}'
    if facts.declaration_mode(bindings[0][1]) not in {'scrub', 'keep'}:
        return f'env={name} is not bound to {_DECLARATION}(...)'
    return None


def _declaration(node, facts, tree):
    """(problem, mode) for the launch's env= keyword."""
    env = None
    for keyword in node.keywords:
        if keyword.arg == 'env':
            env = keyword.value
    if env is None:
        return 'declares no env=', None
    mode = facts.declaration_mode(env)
    if mode == 'invalid':
        return "env= mode must be the literal 'scrub' or 'keep'", None
    if mode is not None:
        return None, mode
    if not isinstance(env, ast.Name):
        return (f'env={ast.unparse(env)} is not {_DECLARATION}(...) or a '
                'name bound once to it', None)
    problem = _bare_name_problem(env.id, facts, tree)
    if problem is not None:
        return problem, None
    return None, facts.declaration_mode(facts.module_bindings[env.id][0][1])


def _check_launch(node, method, relative, function, facts, tree, keeps,
                  violations):
    why = _unresolved_cwd(node, facts)
    if why is None or _runs_no_python(node):
        return
    problem, mode = _declaration(node, facts, tree)
    if problem is not None:
        violations.append(
            f'{relative}:{node.lineno}: subprocess.{method} {why} {problem}')
    elif mode == 'keep':
        keeps.append((relative, function))


def _visit(node, relative, function, facts, tree, keeps, violations):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _visit(child, relative, child.name, facts, tree, keeps,
                   violations)
            continue
        if isinstance(child, ast.Call):
            method = _launch_method(child, facts)
            if method is not None:
                _check_launch(child, method, relative, function, facts,
                              tree, keeps, violations)
        _visit(child, relative, function, facts, tree, keeps, violations)


def _analyze(relative, source, keeps):
    tree = ast.parse(source, filename=relative)
    facts = _ModuleFacts(tree)
    violations = []
    _visit(tree, relative, '<module>', facts, tree, keeps, violations)
    return violations


def _keep_site(module, function):
    return f'{module}::{function}'


def _unlisted_keeps(keeps):
    return [
        f'{_keep_site(module, function)} declares keep without an '
        'allowlist entry'
        for module, function in keeps
        if _keep_site(module, function) not in _KEEP_ALLOWLIST
    ]


def _synthetic_violations(source):
    """Run the guard over one source string for mutation-shaped cases."""
    keeps = []
    violations = _analyze('tests/synthetic.py', source, keeps)
    return violations + _unlisted_keeps(keeps)


def _coverage_environment_violations():
    violations = []
    keeps = []
    for path in sorted((ROOT / 'tests').glob('*.py')):
        relative = path.relative_to(ROOT).as_posix()
        violations.extend(_analyze(
            relative, path.read_text(encoding='utf-8'), keeps))
    violations.extend(_unlisted_keeps(keeps))
    declared = {_keep_site(module, function) for module, function in keeps}
    for entry in sorted(_KEEP_ALLOWLIST - declared):
        violations.append(f'allowlisted keep site {entry} has no launch')
    return violations


def test_a_launch_with_a_provable_cwd_is_safe(tmp):
    """Root spellings and an inherited root cwd need no declaration."""
    del tmp
    assert _synthetic_violations(
        """import os
import subprocess
subprocess.run(['python3', 'child.py'])
subprocess.run(['python3', 'a.py'], cwd=ROOT)
subprocess.run(['python3', 'b.py'], cwd=str(ROOT))
subprocess.run(['python3', 'c.py'], cwd=_util.ROOT)
subprocess.run(['python3', 'd.py'], cwd=str(_util.ROOT))
subprocess.run(['python3', 'e.py'], cwd=behaviour.ROOT)
subprocess.run(['python3', 'f.py'], cwd=str(behaviour.ROOT))
subprocess.run(['python3', 'g.py'], cwd=ROOT / 'fixtures')
subprocess.run(['python3', 'h.py'], cwd=str(ROOT / 'fixtures'))
subprocess.run(['python3', 'i.py'], cwd=ROOT / 'a' / 'b')
os.chdir(ROOT)
subprocess.run(['python3', 'j.py'])
""") == []


def test_a_literal_non_python_launch_is_safe(tmp):
    """A string-literal node/git/shell first element runs no Python."""
    del tmp
    assert _synthetic_violations(
        """import subprocess
subprocess.run(['node', 'child.js'], cwd=tmp)
subprocess.run(['git', 'status'], cwd=tmp)
subprocess.run(['bash', '-c', 'true'], cwd=tmp)
subprocess.run(['sh', 'script.sh'], cwd=tmp)
subprocess.run(['zsh', 'script.sh'], cwd=tmp)
subprocess.run(['cmd', '/c', 'ver'], cwd=tmp)
subprocess.run(['pwsh', 'script.ps1'], cwd=tmp)
""") == []


def test_a_non_literal_first_element_is_treated_as_python(tmp):
    """A name, attribute or call first element is Python to the guard."""
    del tmp
    for argv in ('node', "shutil.which('bash')"):
        violations = _synthetic_violations(
            f"""import subprocess
subprocess.run([{argv}, 'x'], cwd=tmp)
""")
        assert len(violations) == 1, violations
        assert 'tests/synthetic.py:2' in violations[0], violations


def test_a_declaration_makes_an_unresolved_cwd_safe(tmp):
    """Direct, imported and once-bound child_coverage calls declare."""
    del tmp
    assert _synthetic_violations(
        """import subprocess
import _util
from _util import child_coverage
_ENV = _util.child_coverage('scrub')
subprocess.run(['python3', 'a.py'], cwd=tmp,
               env=_util.child_coverage('scrub'))
subprocess.run(['python3', 'b.py'], cwd=tmp, env=child_coverage('scrub'))
def run():
    subprocess.run(['python3', 'c.py'], cwd=tmp, env=_ENV)
""") == []


def test_an_unresolved_cwd_without_env_is_a_violation(tmp):
    """An unresolved cwd with no env= names the file and line."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
subprocess.run(['python3', 'child.py'], cwd=tmp)
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:2' in violations[0], violations
    assert 'declares no env=' in violations[0], violations


def test_an_env_built_from_os_environ_is_a_violation(tmp):
    """Only a child_coverage call declares; a dict copy does not."""
    del tmp
    violations = _synthetic_violations(
        """import os
import subprocess
subprocess.run(['python3', 'child.py'], cwd=tmp, env=dict(os.environ))
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:3' in violations[0], violations


def test_a_function_local_declaration_name_is_a_violation(tmp):
    """A bare env= name must be bound at module level, not in a function."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
import _util
def run():
    env = _util.child_coverage('scrub')
    subprocess.run(['python3', 'child.py'], cwd=tmp, env=env)
""")
    assert len(violations) == 1, violations
    assert '0 module-level bindings' in violations[0], violations


def test_a_mutated_declaration_name_is_a_violation(tmp):
    """Subscript writes, mutating methods and del break the binding."""
    del tmp
    for mutation in ("env['COVERAGE_PROCESS_START'] = 'x'",
                     'env.update({})',
                     'del env'):
        violations = _synthetic_violations(
            f"""import subprocess
import _util
env = _util.child_coverage('scrub')
{mutation}
subprocess.run(['python3', 'child.py'], cwd=tmp, env=env)
""")
        assert len(violations) == 1, (mutation, violations)
        assert 'tests/synthetic.py:5' in violations[0], (mutation,
                                                         violations)


def test_an_earlier_non_root_chdir_taints_an_inherited_cwd(tmp):
    """A launch without cwd= after os.chdir(tmp) must declare."""
    del tmp
    violations = _synthetic_violations(
        """import os
import subprocess
os.chdir(tmp)
subprocess.run(['python3', 'child.py'])
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:4' in violations[0], violations
    assert 'os.chdir' in violations[0], violations


def test_a_spread_of_any_kind_makes_the_cwd_unresolved(tmp):
    """Even a literal dict spread is not resolved syntactically."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
subprocess.run(['python3', 'child.py'], **{'cwd': tmp})
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:2' in violations[0], violations


def test_a_keep_declaration_needs_an_allowlist_entry(tmp):
    """A keep site the allowlist does not name is a violation."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
import _util
subprocess.run(['python3', 'child.py'], cwd=tmp,
               env=_util.child_coverage('keep'))
""")
    assert violations == [
        'tests/synthetic.py::<module> declares keep without an allowlist '
        'entry'], violations


def test_an_allowlist_entry_without_a_keep_site_fails(tmp):
    """A stale entry is as much a failure as an unlisted site."""
    del tmp
    original = _KEEP_ALLOWLIST
    globals()['_KEEP_ALLOWLIST'] = original | {'tests/synthetic.py::ghost'}
    try:
        violations = _coverage_environment_violations()
    finally:
        globals()['_KEEP_ALLOWLIST'] = original
    assert ('allowlisted keep site tests/synthetic.py::ghost has no launch'
            in violations), violations


def test_row1_a_repository_script_in_a_temp_cwd_must_declare(tmp):
    """Deleting env= from a real repo-script launch in tmp is caught."""
    del tmp
    target = ROOT / 'tests' / 'test_diff_coverage.py'
    original = target.read_bytes()
    needle = "'--diff', str(diff)], cwd=tmp, env=_COVERAGE_ENV,"
    text = original.decode('utf-8')
    assert needle in text, 'the row 1 launch shape changed'
    replacement = "'--diff', str(diff)], cwd=tmp,"
    mutated = text.replace(needle, replacement, 1)
    start = mutated.rindex('subprocess.run(', 0, mutated.index(replacement))
    line = mutated[:start].count('\n') + 1
    try:
        target.write_bytes(mutated.encode('utf-8'))
        violations = _coverage_environment_violations()
        assert any(
            v.startswith(f'tests/test_diff_coverage.py:{line}:')
            for v in violations), violations
    finally:
        target.write_bytes(original)
    assert _coverage_environment_violations() == []


def test_row2_cwd_hidden_in_a_spread_name_is_caught(tmp):
    """A **kwargs spread is an unresolved cwd, so it must declare."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
kwargs = {'cwd': tmp}
subprocess.run(['python3', 'child.py'], **kwargs)
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:3' in violations[0], violations


def test_row3_argv_bound_to_a_local_name_is_caught(tmp):
    """A named argv is treated as Python: mutating a real launch fails."""
    del tmp
    target = ROOT / 'tests' / 'test_diff_coverage.py'
    original = target.read_bytes()
    needle = (
        "    done = subprocess.run(\n"
        "        [sys.executable, str(_SCRIPT), '--coverage', "
        "str(coverage_xml),\n"
        "         '--diff', str(diff)],\n"
        "        cwd=tmp, env=_COVERAGE_ENV, capture_output=True, text=True, "
        "timeout=60)")
    text = original.decode('utf-8')
    assert needle in text, 'the row 3 launch shape changed'
    mutated = text.replace(
        needle,
        "    command = [sys.executable, str(_SCRIPT), '--coverage',\n"
        "               str(coverage_xml), '--diff', str(diff)]\n"
        "    done = subprocess.run(command, cwd=tmp, capture_output=True,\n"
        "                          text=True, timeout=60)", 1)
    line = mutated[:mutated.index(
        'subprocess.run(command, cwd=tmp,')].count('\n') + 1
    try:
        target.write_bytes(mutated.encode('utf-8'))
        violations = _coverage_environment_violations()
        assert any(
            v.startswith(f'tests/test_diff_coverage.py:{line}:')
            for v in violations), violations
    finally:
        target.write_bytes(original)
    assert _coverage_environment_violations() == []


def test_row4_a_declaration_that_never_executes_is_caught(tmp):
    """Two syntactic bindings violate even when one is unreachable."""
    del tmp
    violations = _synthetic_violations(
        """import os
import subprocess
child_env = dict(os.environ)
if False:
    child_env = coverage_free_environment(os.environ)
subprocess.run(['python3', 'child.py'], cwd=tmp, env=child_env)
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:6' in violations[0], violations


def test_every_launch_is_proven_safe_or_declares(tmp):
    """The whole tests/ tree under the fail-closed decision procedure."""
    del tmp
    violations = _coverage_environment_violations()
    assert not violations, '\n'.join(violations)


def test_child_coverage_declares_scrub_and_keep(tmp):
    """The helper scrubs, copies, and rejects any other mode."""
    del tmp
    environment = {'COVERAGE_PROCESS_START': 'x', 'PATH': '/bin'}
    assert _util.child_coverage('scrub', environment) == {'PATH': '/bin'}
    kept = _util.child_coverage('keep', environment)
    assert kept == environment and kept is not environment
    try:
        _util.child_coverage('maybe')
    except ValueError:
        pass
    else:
        raise AssertionError("child_coverage accepted mode 'maybe'")


def test_child_coverage_scrubs_a_real_child(tmp):
    """The declared scrub removes every COVERAGE_* name from a child."""
    probe = Path(tmp) / 'coverage-env-probe.py'
    probe.write_text(
        'import json, os\n'
        'print(json.dumps(sorted(name for name in os.environ\n'
        "                           if name.startswith('COVERAGE_'))))\n",
        encoding='utf-8')
    parent = dict(os.environ)
    parent.update({
        'COVERAGE_PROCESS_START': 'synthetic-config',
        'COVERAGE_CONTEXT': 'coverage-environment-test',
    })
    result = subprocess.run(
        [sys.executable, str(probe)], cwd=tmp,
        env=_util.child_coverage('scrub', parent),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == '[]\n', result.stdout


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
