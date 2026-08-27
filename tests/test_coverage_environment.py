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


def _binding_names(target):
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.List, ast.Tuple)):
        return set().union(*(_binding_names(item) for item in target.elts))
    return set()


def _is_helper_call(node):
    if not isinstance(node, ast.Call):
        return False
    function = node.func
    if isinstance(function, ast.Name):
        return function.id == _HELPER
    return (isinstance(function, ast.Attribute)
            and function.attr == _HELPER
            and isinstance(function.value, ast.Name)
            and function.value.id == '_util')


def _helper_bindings(tree):
    bindings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_helper_call(node.value):
            for target in node.targets:
                bindings.update(_binding_names(target))
        elif (isinstance(node, ast.NamedExpr)
              and _is_helper_call(node.value)):
            bindings.update(_binding_names(node.target))
    return bindings


def _is_subprocess_call(node):
    function = node.func
    return (isinstance(function, ast.Attribute)
            and function.attr in {'run', 'Popen'}
            and isinstance(function.value, ast.Name)
            and function.value.id == 'subprocess')


def _is_repository_root(cwd):
    return ast.unparse(cwd) in _ROOT_CWDS


def _env_uses_helper(value, bindings):
    return _is_helper_call(value) or (
        isinstance(value, ast.Name) and value.id in bindings)


def _coverage_environment_violations():
    violations = []
    for path in sorted((ROOT / 'tests').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        bindings = _helper_bindings(tree)
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
                continue
            keywords = {item.arg: item.value for item in node.keywords
                        if item.arg is not None}
            cwd = keywords.get('cwd')
            if cwd is None or _is_repository_root(cwd):
                continue
            env = keywords.get('env')
            if env is None or not _env_uses_helper(env, bindings):
                violations.append(
                    f'{relative}:{node.lineno}: subprocess.{node.func.attr} '
                    f'cwd={ast.unparse(cwd)} lacks '
                    f'env={_HELPER}(...)')
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
