#!/usr/bin/env python3
"""Keep temporary-tree subprocesses out of Python coverage data."""
import ast
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_HELPER = 'coverage_free_environment'
_ROOT_CWDS = frozenset({
    'ROOT',
    '_util.ROOT',
    'str(ROOT)',
    'str(_util.ROOT)',
    'behaviour.ROOT',
})
# `_run_checker` receives the `copy_root` built under the `*/tree` anchor.
_MAPPED_PATH_NAMES = frozenset({'copy_root'})
_ROOT = 'root'
_MAPPED = 'mapped'
_TEMP = 'temporary'
_UNKNOWN = 'unknown'
_PYTHON = 'python'
_NODE = 'node'
_GIT = 'git'
_SHELL = 'shell'
_UNKNOWN_COMMAND = 'unknown-command'

# Static analysis cannot resolve arbitrary values passed through getattr(),
# eval(), or a command assembled by another module. Such wrappers must keep
# using coverage_free_environment themselves; this guard only claims the
# direct subprocess shapes it can prove.
_STATIC_ANALYSIS_LIMITATION = (
    'unresolved dynamic subprocess wrappers and command values are manual')


class _State:
    """Facts that remain useful while visiting one module or function."""

    def __init__(self):
        self.callables = {}
        self.commands = {}
        self.paths = {}
        self.repo_paths = set()
        self.subprocess_modules = {'subprocess'}
        self.helper_modules = {'_util'}
        self.helper_functions = {_HELPER}
        self.safe_envs = set()
        self.tainted_envs = set()
        self.cwd = _ROOT

    def child(self):
        result = _State()
        result.callables = dict(self.callables)
        result.commands = dict(self.commands)
        result.paths = dict(self.paths)
        result.repo_paths = set(self.repo_paths)
        result.subprocess_modules = set(self.subprocess_modules)
        result.helper_modules = set(self.helper_modules)
        result.helper_functions = set(self.helper_functions)
        result.safe_envs = set(self.safe_envs)
        result.tainted_envs = set(self.tainted_envs)
        return result


def _binding_names(target):
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.List, ast.Tuple)):
        return set().union(*(_binding_names(item) for item in target.elts))
    return set()


def _is_helper_call(node, state):
    if not isinstance(node, ast.Call):
        return False
    function = node.func
    if isinstance(function, ast.Name):
        return function.id in state.helper_functions
    return (isinstance(function, ast.Attribute)
            and function.attr == _HELPER
            and isinstance(function.value, ast.Name)
            and function.value.id in state.helper_modules)


def _is_root_path(node):
    """Whether an expression names the repository root explicitly."""
    if ast.unparse(node) in _ROOT_CWDS:
        return True
    return (isinstance(node, ast.Attribute)
            and node.attr == 'ROOT'
            and isinstance(node.value, ast.Name)
            and node.value.id in {'_util', 'behaviour'})


def _combine_paths(*kinds):
    if _UNKNOWN in kinds:
        if _ROOT in kinds:
            return _ROOT
        return _UNKNOWN
    if _MAPPED in kinds:
        return _MAPPED
    if _TEMP in kinds:
        return _TEMP
    return _ROOT


def _path_kind(node, state):
    """Classify a cwd expression against the configured coverage paths."""
    if _is_root_path(node):
        return _ROOT
    if isinstance(node, ast.Name):
        if node.id in _MAPPED_PATH_NAMES:
            return _MAPPED
        if node.id in state.paths:
            return state.paths[node.id]
        return _TEMP
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value in {'', '.'}:
            return _ROOT
        components = node.value.replace('\\', '/').split('/')
        if 'tree' in components:
            return _MAPPED
        if node.value.startswith('/tmp/') or node.value == '/tmp':
            return _TEMP
        return _UNKNOWN
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        return _combine_paths(
            _path_kind(node.left, state), _path_kind(node.right, state))
    if isinstance(node, ast.JoinedStr):
        return _UNKNOWN
    if isinstance(node, ast.Call):
        function = node.func
        if (isinstance(function, ast.Name) and function.id == 'str'
                and node.args):
            return _path_kind(node.args[0], state)
        if (isinstance(function, ast.Attribute)
                and function.attr in {'resolve', 'absolute'}):
            return _path_kind(function.value, state)
        if (isinstance(function, ast.Attribute)
                and function.attr == 'getcwd'):
            return state.cwd
        if (isinstance(function, ast.Attribute)
                and function.attr == 'join'):
            return _combine_paths(*(_path_kind(arg, state)
                                    for arg in node.args))
        if isinstance(function, ast.Name) and function.id == 'Path':
            return (_path_kind(node.args[0], state)
                    if node.args else _TEMP)
        if isinstance(function, ast.Name) and function.id in {
                'TemporaryDirectory', 'mkdtemp'}:
            return _TEMP
    return _UNKNOWN


def _is_repo_expression(node, state):
    """Whether an expression resolves to a source file in this checkout."""
    if _is_root_path(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in state.repo_paths
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        return _is_repo_expression(node.left, state)
    if isinstance(node, ast.Call):
        function = node.func
        if (isinstance(function, ast.Name) and function.id == 'str'
                and node.args):
            return _is_repo_expression(node.args[0], state)
        if (isinstance(function, ast.Attribute)
                and function.attr in {'resolve', 'absolute'}):
            return _is_repo_expression(function.value, state)
    return False


def _executable_kind(node, state):
    """Classify the first command word, conservatively for unknown names."""
    if isinstance(node, ast.Name):
        if node.id in state.commands:
            return state.commands[node.id]
        if node.id in {'node', 'node_path'}:
            return _NODE
        if node.id in {'git', 'git_path'}:
            return _GIT
        if node.id in {'bash', 'sh', 'zsh', 'shell'}:
            return _SHELL
        return _UNKNOWN_COMMAND
    if isinstance(node, ast.Attribute):
        if (isinstance(node.value, ast.Name)
                and node.value.id == 'sys'
                and node.attr == 'executable'):
            return _PYTHON
        return _PYTHON
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        name = node.value.replace('\\', '/').rsplit('/', 1)[-1].lower()
        if name == 'node' or name.startswith('node.'):
            return _NODE
        if name == 'git' or name.startswith('git.'):
            return _GIT
        if name in {'bash', 'sh', 'zsh', 'cmd', 'pwsh'}:
            return _SHELL
        if name == 'python' or name.startswith('python3') \
                or name.startswith('python2') or name == 'py.exe':
            return _PYTHON
        return _PYTHON
    if isinstance(node, ast.Call):
        function = node.func
        if (isinstance(function, ast.Attribute)
                and function.attr == 'which' and node.args):
            return _executable_kind(node.args[0], state)
    return _UNKNOWN_COMMAND


def _command_kind(node, state):
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _executable_kind(node.elts[0], state)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _command_kind(node.left, state)
    if isinstance(node, ast.Subscript):
        return _command_kind(node.value, state)
    if isinstance(node, ast.Name) and node.id in state.commands:
        return state.commands[node.id]
    return _UNKNOWN_COMMAND


def _command_script_is_repo(node, state):
    if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) > 1:
        return _is_repo_expression(node.elts[1], state)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _command_script_is_repo(node.left, state)
    if isinstance(node, ast.Subscript):
        return _command_script_is_repo(node.value, state)
    return False


def _subprocess_method(node, state):
    function = node.func
    if (isinstance(function, ast.Attribute)
            and function.attr in {'run', 'Popen'}):
        if (isinstance(function.value, ast.Name)
                and function.value.id in state.subprocess_modules):
            return function.attr
    if isinstance(function, ast.Name):
        return state.callables.get(function.id)
    return None


def _callable_binding(node, state):
    if isinstance(node, ast.Attribute):
        if (isinstance(node.value, ast.Name)
                and node.value.id in state.subprocess_modules
                and node.attr in {'run', 'Popen'}):
            return node.attr
    if isinstance(node, ast.Name):
        return state.callables.get(node.id)
    return None


def _cwd_expression(node):
    """Return (cwd expression, explicit) including literal ``**`` mappings."""
    for keyword in node.keywords:
        if keyword.arg == 'cwd':
            return keyword.value, True
        if keyword.arg is None and isinstance(keyword.value, ast.Dict):
            for key, value in zip(keyword.value.keys, keyword.value.values):
                if (isinstance(key, ast.Constant) and key.value == 'cwd'):
                    return value, True
    return None, False


def _is_coverage_key(node):
    return (isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith('COVERAGE_'))


def _mutation_taints_environment(node, state):
    """Mark helper-derived env names unsafe when a coverage key can return."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        name = node.value.id
        if name not in state.safe_envs:
            return
        key = node.slice
        if _is_coverage_key(key) or not isinstance(key, ast.Constant):
            state.tainted_envs.add(name)
    elif isinstance(node, ast.Name):
        state.safe_envs.discard(node.id)
        state.tainted_envs.discard(node.id)


def _env_is_safe(node, state):
    return (_is_helper_call(node, state)
            or (isinstance(node, ast.Name)
                and node.id in state.safe_envs
                and node.id not in state.tainted_envs))


def _synthetic_violations(source):
    """Run the guard over one source string for mutation-shaped cases."""
    return _analyze_source('tests/synthetic.py', source)


def test_guard_catches_alias_and_expanded_cwd(tmp):
    """Aliases and ``**{'cwd': ...}`` cannot bypass the guard."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
runner = subprocess.run
runner(['python3', 'child.py'], **{'cwd': tmp})
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:3' in violations[0], violations


def test_guard_catches_inherited_temporary_cwd(tmp):
    """A cwd changed with ``os.chdir`` applies to later child launches."""
    del tmp
    violations = _synthetic_violations(
        """import os
import subprocess
os.chdir(tmp)
subprocess.run(['python3', 'child.py'])
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:4' in violations[0], violations


def test_guard_tracks_helper_environment_mutation(tmp):
    """Restoring a coverage key after scrubbing makes the env unsafe."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
env = coverage_free_environment(os.environ)
env['COVERAGE_PROCESS_START'] = 'pyproject.toml'
subprocess.run(['python3', 'child.py'], cwd=tmp, env=env)
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:4' in violations[0], violations


def test_guard_allows_mapped_and_non_python_launches(tmp):
    """Mapped trees and Node/Git launches are outside this guard's class."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
subprocess.run(['python3', 'child.py'], cwd=Path(tmp) / 'tree')
subprocess.run(['node', 'child.js'], cwd=tmp)
subprocess.run(['git', 'status'], cwd=tmp)
""")
    assert violations == [], violations


def test_guard_allows_a_real_repository_script_in_a_fixture_cwd(tmp):
    """A real source path stays measurable even when fixtures are elsewhere."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
subprocess.run([sys.executable, str(ROOT / 'scripts' / 'ci' / 'tool.py')],
               cwd=tmp)
""")
    assert violations == [], violations


def test_guard_allows_a_repository_subdirectory_cwd(tmp):
    """Repository subdirectories cannot leave source paths unmapped."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
subprocess.run(['python3', 'child.py'], cwd=ROOT / 'fixtures')
""")
    assert violations == [], violations


def test_guard_requires_a_definite_tree_anchor(tmp):
    """A conditional tree component may choose an unmapped directory."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
subprocess.run(['python3', 'child.py'],
               cwd=Path(tmp) / ('tree' if use_tree else 'tracked'))
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:2' in violations[0], violations


def _bind_target(target, value, state):
    if (isinstance(target, (ast.List, ast.Tuple))
            and isinstance(value, (ast.List, ast.Tuple))):
        for target_item, value_item in zip(target.elts, value.elts):
            _bind_target(target_item, value_item, state)
        return
    names = _binding_names(target)
    for name in names:
        state.callables.pop(name, None)
        state.commands.pop(name, None)
        state.paths.pop(name, None)
        state.repo_paths.discard(name)
        state.safe_envs.discard(name)
        state.tainted_envs.discard(name)
    if len(names) != 1 or not isinstance(target, ast.Name):
        return
    name = target.id
    if isinstance(value, ast.Name):
        if value.id in state.callables:
            state.callables[name] = state.callables[value.id]
        if value.id in state.commands:
            state.commands[name] = state.commands[value.id]
        if value.id in state.paths:
            state.paths[name] = state.paths[value.id]
    method = _callable_binding(value, state)
    if method is not None:
        state.callables[name] = method
    if _is_helper_call(value, state):
        state.safe_envs.add(name)
    path = _path_kind(value, state)
    if path != _UNKNOWN:
        state.paths[name] = path
    if _is_repo_expression(value, state):
        state.repo_paths.add(name)


def _bind_command_target(target, iterable, state):
    names = _binding_names(target)
    if len(names) != 1 or not isinstance(target, ast.Name):
        return
    kinds = []
    if isinstance(iterable, (ast.Tuple, ast.List)):
        for item in iterable.elts:
            kinds.append(_command_kind(item, state))
    if kinds and all(kind == kinds[0] for kind in kinds):
        state.commands[target.id] = kinds[0]


def _visit_call(node, state, relative, violations):
    function = node.func
    if (isinstance(function, ast.Attribute)
            and function.attr == 'chdir'
            and isinstance(function.value, ast.Name)
            and function.value.id == 'os' and node.args):
        state.cwd = _path_kind(node.args[0], state)
        return
    if (isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in state.safe_envs
            and function.attr in {
                'clear', 'pop', 'popitem', 'setdefault', 'update'}):
        state.tainted_envs.add(function.value.id)
    method = _subprocess_method(node, state)
    if method is None or not node.args:
        return
    command = _command_kind(node.args[0], state)
    if command in {_NODE, _GIT, _UNKNOWN_COMMAND}:
        return
    cwd, explicit = _cwd_expression(node)
    cwd_kind = (_path_kind(cwd, state) if explicit else state.cwd)
    if cwd_kind in {_ROOT, _MAPPED}:
        return
    if command == _PYTHON and _command_script_is_repo(node.args[0], state):
        return
    env = next((keyword.value for keyword in node.keywords
                if keyword.arg == 'env'), None)
    if env is None or not _env_is_safe(env, state):
        shown_cwd = ast.unparse(cwd) if explicit else '<inherited>'
        violations.append(
            f'{relative}:{node.lineno}: subprocess.{method} '
            f'cwd={shown_cwd} lacks env={_HELPER}(...)')


def _visit_expr(node, state, relative, violations):
    if isinstance(node, ast.Call):
        _visit_call(node, state, relative, violations)
    for child in ast.iter_child_nodes(node):
        if isinstance(node, ast.Call) and child is node.func:
            continue
        _visit_expr(child, state, relative, violations)


def _visit_statement(node, state, relative, violations):
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name == 'subprocess':
                state.subprocess_modules.add(alias.asname or 'subprocess')
            if alias.name == '_util':
                state.helper_modules.add(alias.asname or '_util')
        return
    if isinstance(node, ast.ImportFrom):
        if node.module == 'subprocess':
            for alias in node.names:
                if alias.name in {'run', 'Popen'}:
                    state.callables[alias.asname or alias.name] = alias.name
        if node.module == '_util':
            for alias in node.names:
                if alias.name == _HELPER:
                    state.helper_functions.add(alias.asname or alias.name)
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _visit_block(node.body, state.child(), relative, violations)
        return
    if isinstance(node, ast.ClassDef):
        _visit_block(node.body, state.child(), relative, violations)
        return
    if isinstance(node, ast.Assign):
        _visit_expr(node.value, state, relative, violations)
        for target in node.targets:
            _mutation_taints_environment(target, state)
            _bind_target(target, node.value, state)
        return
    if isinstance(node, ast.AnnAssign):
        if node.value is not None:
            _visit_expr(node.value, state, relative, violations)
            _mutation_taints_environment(node.target, state)
            _bind_target(node.target, node.value, state)
        return
    if isinstance(node, ast.NamedExpr):
        _visit_expr(node.value, state, relative, violations)
        _mutation_taints_environment(node.target, state)
        _bind_target(node.target, node.value, state)
        return
    if isinstance(node, ast.AugAssign):
        _visit_expr(node.value, state, relative, violations)
        _mutation_taints_environment(node.target, state)
        return
    if isinstance(node, ast.Delete):
        for target in node.targets:
            _mutation_taints_environment(target, state)
        return
    if isinstance(node, ast.For):
        _visit_expr(node.iter, state, relative, violations)
        _bind_command_target(node.target, node.iter, state)
        _visit_block(node.body, state, relative, violations)
        _visit_block(node.orelse, state, relative, violations)
        return
    if isinstance(node, (ast.If, ast.While)):
        _visit_expr(node.test, state, relative, violations)
        _visit_block(node.body, state, relative, violations)
        _visit_block(node.orelse, state, relative, violations)
        return
    if isinstance(node, ast.Try):
        _visit_block(node.body, state, relative, violations)
        for handler in node.handlers:
            _visit_block(handler.body, state, relative, violations)
        _visit_block(node.orelse, state, relative, violations)
        _visit_block(node.finalbody, state, relative, violations)
        return
    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            _visit_expr(item.context_expr, state, relative, violations)
            if item.optional_vars is not None:
                _bind_target(item.optional_vars, item.context_expr, state)
        _visit_block(node.body, state, relative, violations)
        return
    _visit_expr(node, state, relative, violations)


def _visit_block(nodes, state, relative, violations):
    for node in nodes:
        _visit_statement(node, state, relative, violations)


def _analyze_source(relative, source):
    tree = ast.parse(source, filename=relative)
    violations = []
    _visit_block(tree.body, _State(), relative, violations)
    return violations


def _coverage_environment_violations():
    violations = []
    for path in sorted((ROOT / 'tests').glob('*.py')):
        relative = path.relative_to(ROOT).as_posix()
        violations.extend(_analyze_source(
            relative, path.read_text(encoding='utf-8')))
    return violations


def test_every_non_root_subprocess_scrubs_coverage(tmp):
    """A temporary-tree subprocess cannot inherit the coverage collector."""
    del tmp
    violations = _coverage_environment_violations()
    assert not violations, '\n'.join(violations)


def test_coverage_free_environment_scrubs_a_real_child(tmp):
    """The shared helper removes every COVERAGE_* name from a child."""
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
    helper = getattr(_util, _HELPER, None)
    assert callable(helper), f'_util.{_HELPER} is missing'
    child_env = _util.coverage_free_environment(parent)
    result = subprocess.run(
        [sys.executable, str(probe)], cwd=tmp, env=child_env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == '[]\n', result.stdout


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
