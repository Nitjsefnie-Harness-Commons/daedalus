#!/usr/bin/env python3
"""Launcher bindings the coverage-environment guard cannot follow."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _control_writes import control_write_violations  # noqa: E402
from _coverage_guard import (  # noqa: E402
    _coverage_environment_violations, _synthetic_violations)
from _owned_writes import copy_test_tree  # noqa: E402
from _repo import ROOT  # noqa: E402


_BINDING_MESSAGE = (
    'a launcher is bound through a form the guard cannot follow')


def _binding_violation(line):
    return f'tests/synthetic.py:{line}: {_BINDING_MESSAGE}'


def _assert_binding_pair(unsafe, line, explicit, explicit_line):
    assert _synthetic_violations(unsafe) == [
        _binding_violation(line)
    ]
    assert _synthetic_violations(explicit) == [
        f'tests/synthetic.py:{explicit_line}: subprocess.run cwd=tmp '
        'declares no env='
    ]


def test_call_result_assignment_refuses_a_hidden_launcher(tmp):
    del tmp
    _assert_binding_pair(
        """import os
import subprocess
from contextlib import nullcontext
launcher = nullcontext(subprocess).__enter__()
os.chdir(tmp)
launcher.run(['python3', 'child.py'])
""",
        4,
        """import os
import subprocess
result = subprocess.run(['python3', 'child.py'], cwd=tmp)
""",
        3)


def test_function_defaults_refuse_hidden_launchers(tmp):
    del tmp
    pairs = (
        (
            """import os
import subprocess
os.chdir(tmp)
def go(launcher=subprocess):
    launcher.run(['python3', 'child.py'])
go()
""",
            """import os
import subprocess
def go(result=subprocess.run(['python3', 'child.py'], cwd=tmp)):
    return result
"""),
        (
            """import os
import subprocess
os.chdir(tmp)
def go(*, launcher=subprocess):
    launcher.run(['python3', 'child.py'])
go()
""",
            """import os
import subprocess
def go(*, result=subprocess.run(['python3', 'child.py'], cwd=tmp)):
    return result
"""),
    )
    for unsafe, explicit in pairs:
        _assert_binding_pair(unsafe, 4, explicit, 3)


def test_match_capture_refuses_a_hidden_launcher(tmp):
    del tmp
    for pattern in ('launcher', '[*launcher]', '{**launcher}'):
        unsafe = f"""import os
import subprocess
os.chdir(tmp)
match subprocess:
    case {pattern}:
        launcher.run(['python3', 'child.py'])
"""
        explicit = f"""import os
import subprocess
match subprocess.run(['python3', 'child.py'], cwd=tmp):
    case {pattern.replace('launcher', 'result')}:
        pass
"""
        _assert_binding_pair(unsafe, 4, explicit, 3)


def test_comprehension_target_refuses_a_hidden_launcher(tmp):
    del tmp
    _assert_binding_pair(
        """import os
import subprocess
os.chdir(tmp)
[launcher.run(['python3', 'child.py']) for launcher in [subprocess]]
""",
        4,
        """import os
import subprocess
[result for result in
 [subprocess.run(['python3', 'child.py'], cwd=tmp)]]
""",
        4)


def test_single_name_container_refuses_a_hidden_launcher(tmp):
    del tmp
    unsafe_sources = (
        """import os
import subprocess
launchers = [subprocess]
os.chdir(tmp)
for launcher in launchers:
    launcher.run(['python3', 'child.py'])
""",
        """import os
import subprocess
launchers: list = [subprocess]
os.chdir(tmp)
for launcher in launchers:
    launcher.run(['python3', 'child.py'])
""",
    )
    for unsafe in unsafe_sources:
        _assert_binding_pair(
            unsafe,
            3,
            """import os
import subprocess
results = [subprocess.run(['python3', 'child.py'], cwd=tmp)]
""",
            3)


def test_callee_chain_iterable_refuses_a_hidden_launcher(tmp):
    del tmp
    _assert_binding_pair(
        """import os
import subprocess
from contextlib import nullcontext
os.chdir(tmp)
for launcher in [nullcontext(subprocess).__enter__()]:
    launcher.run(['python3', 'child.py'])
""",
        5,
        """import os
import subprocess
from contextlib import nullcontext
for result in [nullcontext(subprocess.run(
        ['python3', 'child.py'], cwd=tmp)).__enter__()]:
    pass
""",
        4)


def test_builtin_dict_is_clean_only_while_unshadowed(tmp):
    del tmp
    assert _synthetic_violations(
        """dict(cwd='x')
def nested():
    return dict(cwd='x')
""") == []


def test_shadowed_dict_bindings_remain_launchers_or_unresolved(tmp):
    del tmp
    cases = (
        """import subprocess
dict = subprocess.run
dict(['python3', 'child.py'], cwd=tmp)
""",
        """def dict(**kwargs):
    return kwargs
dict(cwd='x')
""",
        """def build(dict):
    return dict(cwd='x')
""",
        """match value:
    case dict:
        dict(cwd='x')
""",
        """match value:
    case [*dict]:
        dict(cwd='x')
""",
        """match value:
    case {**dict}:
        dict(cwd='x')
""",
    )
    for source in cases:
        violations = _synthetic_violations(source)
        assert len(violations) == 1, violations
        assert 'dict' in violations[0], violations


def test_bash_resolver_match_captures_shadow_outer_bindings(tmp):
    patterns = ('dict', '[*dict]', '{**dict}')
    for pattern in patterns:
        source = f'''import subprocess
dict = 'bash'
def launch(value, tmp):
    match value:
        case {pattern}:
            subprocess.run([dict, '-c', 'true'], cwd=tmp)
'''
        program = (
            'import sys\n'
            f'sys.path.insert(0, {str(ROOT / "tests")!r})\n'
            'from _bash_resolver_scan import _synthetic_violations\n'
            f'assert _synthetic_violations({source!r}) == []\n')
        result = subprocess.run(
            [sys.executable, '-c', program], cwd=tmp,
            env=_util.child_coverage('scrub'), capture_output=True,
            text=True, timeout=30)
        assert result.returncode == 0, result.stderr


def test_match_captures_cannot_disguise_nonroot_chdir(tmp):
    del tmp
    for pattern in ('ROOT', '[*ROOT]', '{**ROOT}'):
        violations = _synthetic_violations(f"""import os
import subprocess
match value:
    case {pattern}:
        pass
os.chdir(ROOT)
subprocess.run(['python3', 'child.py'])
""")
        assert len(violations) == 1, (pattern, violations)
        assert 'os.chdir at line 6 may have moved the cwd' in violations[0]


def test_nonlauncher_binding_controls_stay_clean(tmp):
    del tmp
    assert _synthetic_violations(
        """import subprocess
match subprocess:
    case None:
        pass
result = subprocess.run(['python3', 'child.py'], cwd=ROOT)
""") == []


def test_named_unreadable_spread_remains_a_violation(tmp):
    del tmp
    violations = _synthetic_violations(
        """import subprocess
kw = dict({'cwd': tmp})
subprocess.run(['python3', 'child.py'], **kw)
""")
    assert len(violations) == 1, violations
    assert 'cwd may arrive through a ** spread' in violations[0], violations


def _module_text(target):
    return target.read_bytes().decode('utf-8').replace('\r\n', '\n')


def _real_module_copy(tmp, relative):
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    return root, root / relative


def _inserted_line(source, anchor, snippet, marker):
    mutated = source.replace(anchor, snippet + anchor, 1)
    return mutated, mutated[:mutated.index(marker)].count('\n') + 1


def _binding_snippets():
    return (
        (
            'call result',
            """def _binding_probe(tmp):
    launcher = nullcontext(subprocess).__enter__()
    os.chdir(tmp)
    launcher.run(['python3', 'child.py'])
""",
            'launcher = nullcontext',
            """def _binding_probe(tmp):
    result = subprocess.run(['python3', 'child.py'], cwd=tmp)
"""),
        (
            'defaults',
            """def _binding_probe(
        tmp, launcher=subprocess, *, other=subprocess):
    os.chdir(tmp)
    launcher.run(['python3', 'child.py'])
    other.run(['python3', 'child.py'])
""",
            'launcher=subprocess',
            """def _binding_probe(tmp, result=subprocess.run(
        ['python3', 'child.py'], cwd=tmp), *, other=subprocess.run(
        ['python3', 'child.py'], cwd=tmp)):
    return result, other
"""),
        (
            'match capture',
            """def _binding_probe(tmp):
    os.chdir(tmp)
    match subprocess:
        case launcher:
            launcher.run(['python3', 'child.py'])
""",
            'match subprocess:',
            """def _binding_probe(tmp):
    match subprocess.run(['python3', 'child.py'], cwd=tmp):
        case result:
            return result
"""),
        (
            'comprehension',
            """def _binding_probe(tmp):
    os.chdir(tmp)
    return [launcher.run(['python3', 'child.py'])
            for launcher in [subprocess]]
""",
            'for launcher in',
            """def _binding_probe(tmp):
    return [result for result in
            [subprocess.run(['python3', 'child.py'], cwd=tmp)]]
"""),
        (
            'container',
            """def _binding_probe(tmp):
    launchers = [subprocess]
    os.chdir(tmp)
    return [launcher.run(['python3', 'child.py'])
            for launcher in launchers]
""",
            'launchers = [subprocess]',
            """def _binding_probe(tmp):
    return [subprocess.run(['python3', 'child.py'], cwd=tmp)]
"""),
        (
            'callee chain',
            """def _binding_probe(tmp):
    os.chdir(tmp)
    for launcher in [nullcontext(subprocess).__enter__()]:
        launcher.run(['python3', 'child.py'])
""",
            'for launcher in',
            """def _binding_probe(tmp):
    for result in [nullcontext(subprocess.run(
            ['python3', 'child.py'], cwd=tmp)).__enter__()]:
        return result
"""),
    )


def _mutation_specs():
    assign = (
        "        if (len(node.targets) == 1 and "
        "isinstance(node.targets[0], ast.Name)\n"
        "                and (_names_one_of("
        "node.value, facts.subprocess_modules)\n"
        "                     or _is_launch_value(node.value, facts))):\n"
        "            return []\n",
        "        if len(node.targets) == 1 and "
        "isinstance(node.targets[0], ast.Name):\n"
        "            return []\n",
    )
    annotated = (
        "        if (isinstance(node.target, ast.Name)\n"
        "                and (_names_one_of("
        "node.value, facts.subprocess_modules)\n"
        "                     or _is_launch_value(node.value, facts))):\n"
        "            return []\n",
        "        if isinstance(node.target, ast.Name):\n"
        "            return []\n",
    )
    comprehension = (
        "    if isinstance(node, ast.comprehension):\n"
        "        return [(node.target.lineno, node.iter)]\n",
        "",
    )
    defaults = (
        "    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, "
        "ast.Lambda)):\n"
        "        defaults = [*node.args.defaults,\n"
        "                    *(value for value in node.args.kw_defaults\n"
        "                      if value is not None)]\n"
        "        return [(value.lineno, value) for value in defaults]\n",
        "",
    )
    match = (
        "    if isinstance(node, ast.Match) and any(\n"
        "            _pattern_binds(case.pattern) for case in node.cases):\n"
        "        return [(node.lineno, node.subject)]\n",
        "",
    )
    callee = (
        "        callee = value.func\n"
        "        while isinstance(callee, (ast.Attribute, ast.Subscript)):\n"
        "            callee = callee.value\n"
        "        if isinstance(callee, ast.Call):\n"
        "            yield from _carried_parts(callee)\n",
        "",
    )
    match_as_names = (
        "    names.update(node.name for node in walked\n"
        "                 if isinstance(node, ast.MatchAs)\n"
        "                 and node.name)\n",
        "",
    )
    match_star_names = (
        "    names.update(node.name for node in walked\n"
        "                 if isinstance(node, ast.MatchStar)\n"
        "                 and node.name)\n",
        "",
    )
    match_rest_names = (
        "    names.update(node.rest for node in walked\n"
        "                 if isinstance(node, ast.MatchMapping) "
        "and node.rest)\n",
        "",
    )
    match_as_scope = (
        "            elif isinstance(child, ast.MatchAs) and child.name:\n"
        "                shadows[scope].add(child.name)\n",
        "",
    )
    match_star_scope = (
        "            elif isinstance(child, ast.MatchStar) and child.name:\n"
        "                shadows[scope].add(child.name)\n",
        "",
    )
    match_rest_scope = (
        "            elif isinstance(child, ast.MatchMapping) "
        "and child.rest:\n"
        "                shadows[scope].add(child.rest)\n",
        "",
    )
    return (
        ('plain assignment', 'bindings', (assign,),
         'suite.test_single_name_container_refuses_a_hidden_launcher(None)'),
        ('annotated assignment', 'bindings', (annotated,),
         'suite.test_single_name_container_refuses_a_hidden_launcher(None)'),
        ('comprehension', 'bindings', (comprehension,),
         'suite.test_comprehension_target_refuses_a_hidden_launcher(None)'),
        ('defaults', 'bindings', (defaults,),
         'suite.test_function_defaults_refuse_hidden_launchers(None)'),
        ('match subject', 'bindings', (match,),
         'suite.test_match_capture_refuses_a_hidden_launcher(None)'),
        ('callee base', 'bindings', (callee,),
         'suite.test_call_result_assignment_refuses_a_hidden_launcher(None)'),
        ('MatchAs global', 'scopes', (match_as_names,),
         'suite.test_match_captures_cannot_disguise_nonroot_chdir(None)'),
        ('MatchStar global', 'scopes', (match_star_names,),
         'suite.test_match_captures_cannot_disguise_nonroot_chdir(None)'),
        ('MatchMapping global', 'scopes', (match_rest_names,),
         'suite.test_match_captures_cannot_disguise_nonroot_chdir(None)'),
        ('MatchAs scope', 'scopes', (match_as_scope,),
         "suite.test_bash_resolver_match_captures_shadow_outer_bindings('.')"),
        ('MatchStar scope', 'scopes', (match_star_scope,),
         "suite.test_bash_resolver_match_captures_shadow_outer_bindings('.')"),
        ('MatchMapping scope', 'scopes', (match_rest_scope,),
         "suite.test_bash_resolver_match_captures_shadow_outer_bindings('.')"),
    )


def test_each_new_binding_and_match_arm_is_mutation_sensitive(tmp):
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    bindings_target = root / 'tests' / '_coverage_bindings.py'
    scopes_target = root / 'tests' / '_coverage_scopes.py'
    for name, target_name, replacements, invocation in _mutation_specs():
        target = (bindings_target if target_name == 'bindings'
                  else scopes_target)
        original = target.read_bytes()
        text = original.decode('utf-8')
        for needle, replacement in replacements:
            assert text.count(needle) == 1, (name, needle)
            text = text.replace(needle, replacement, 1)
        try:
            target.write_bytes(text.encode('utf-8'))
            program = (
                "import sys\n"
                "sys.path.insert(0, 'tests')\n"
                "import test_coverage_bindings as suite\n"
                f"{invocation}\n")
            result = subprocess.run(
                [sys.executable, '-c', program], cwd=root,
                env=_util.child_coverage('scrub'), capture_output=True,
                text=True, timeout=30)
        finally:
            target.write_bytes(original)
        assert result.returncode != 0, name
        assert 'AssertionError' in result.stderr, (name, result.stderr)


def test_real_tree_refuses_each_complete_binding_bypass(tmp):
    root, target = _real_module_copy(
        tmp, Path('tests/test_diff_coverage.py'))
    source = _module_text(target)
    anchor = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    assert anchor in source, 'the coverage declaration shape changed'
    original = target.read_bytes()
    for name, unsafe, marker, explicit in _binding_snippets():
        mutated, line = _inserted_line(source, anchor, unsafe, marker)
        try:
            target.write_bytes(mutated.encode('utf-8'))
            violations = _coverage_environment_violations(root)
            expected = (
                f'tests/test_diff_coverage.py:{line}: {_BINDING_MESSAGE}')
            assert expected in violations, (name, violations)
        finally:
            target.write_bytes(original)
        restored = _coverage_environment_violations(root)
        assert not any(v.startswith(
            f'tests/test_diff_coverage.py:{line}:') for v in restored), (
                name, restored)

        explicit_source, _ = _inserted_line(
            source, anchor, explicit, 'def _binding_probe')
        try:
            target.write_bytes(explicit_source.encode('utf-8'))
            explicit_violations = _coverage_environment_violations(root)
            expected_count = 2 if name == 'defaults' else 1
            assert len(explicit_violations) == expected_count, (
                name, explicit_violations)
            assert all('subprocess.run cwd=tmp declares no env=' in item
                       for item in explicit_violations), (
                           name, explicit_violations)
            assert all(_BINDING_MESSAGE not in item
                       for item in explicit_violations), (
                           name, explicit_violations)
        finally:
            target.write_bytes(original)


def test_real_tree_allows_an_unshadowed_builtin_dict(tmp):
    root, target = _real_module_copy(
        tmp, Path('tests/test_diff_coverage.py'))
    source = _module_text(target)
    anchor = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    snippet = "_BUILTIN_DICT_CONTROL = dict(cwd='x')\n"
    target.write_bytes(source.replace(
        anchor, snippet + anchor, 1).encode('utf-8'))
    assert _coverage_environment_violations(root) == []


def test_controls_never_write_inside_the_repository(tmp):
    del tmp
    violations = control_write_violations(Path(__file__), ROOT)
    assert not violations, '\n'.join(violations)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
