#!/usr/bin/env python3
"""Launcher bindings the coverage-environment guard cannot follow."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _control_writes import control_write_violations  # noqa: E402
from _coverage_guard import _synthetic_violations  # noqa: E402
from _owned_writes import copy_test_tree  # noqa: E402
from _repo import ROOT  # noqa: E402
from test_bash_resolver_scan import _BASH_MUTATION_SPECS  # noqa: E402


_BINDING_MESSAGE = (
    'a launcher is bound through a form the guard cannot follow')
_NL = '\n'
_FRESH_SOURCE_MARKER = 'mutation child loaded fresh source'
_SCOPE_INVOKE = (
    'suite.test_python_evaluation_scopes_preserve_builtin_dict_identity(None)')
_INLINE_INVOKE = (
    'import test_static_guard_regressions as regression_suite; '
    'regression_suite.test_inline_dict_receivers_refuse_hidden_'
    'launchers(None)')
_SUBSCRIPT_INVOKE = (
    'import test_static_guard_regressions as regression_suite; '
    'regression_suite.test_subscripted_dict_carriers_refuse_hidden_'
    'launchers(None)')


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
        (
            """import os
import subprocess
os.chdir(tmp)
def go(launchers={'sp': subprocess}):
    launchers['sp'].run(['python3', 'child.py'])
go()
""",
            """import os
import subprocess
def go(results={'sp': subprocess.run(
        ['python3', 'child.py'], cwd=tmp)}):
    return results
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
        """import os
import subprocess
launchers = {'sp': subprocess}
os.chdir(tmp)
launchers['sp'].run(['python3', 'child.py'])
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


def _scope_cases():
    return (
        ('unshadowed', """dict(cwd='module')
def nested():
    return dict(cwd='nested')
""", ()),
        ('lambda body', "lambda dict: dict(cwd='lambda body')\n",
         (("dict(cwd='lambda body')", "'lambda body'"),)),
        ('class body', """dict(cwd='before class')
class Example:
    dict = factory
    value = dict(cwd='class body')
dict(cwd='after class')
""", (("dict(cwd='class body')", "'class body'"),)),
        ('function default', """def build(
        dict=dict(cwd='function default')):
    return dict
""", ()),
        ('comprehension', """dict(cwd='before comprehension')
[
    dict(cwd='inside comprehension')
    for dict in dict(cwd='comprehension iterable')
]
dict(cwd='after comprehension')
""", (("dict(cwd='inside comprehension')",
         "'inside comprehension'"),)),
        ('comprehension walrus', """[(dict := factory)
 for item in values]
dict(cwd='after walrus')
""", (("dict(cwd='after walrus')", "'after walrus'"),)),
        ('nested comprehension walrus', """def build():
    [[(dict := factory) for inner in items] for outer in groups]
    return dict(cwd='after nested walrus')
""", (("dict(cwd='after nested walrus')",
         "'after nested walrus'"),)),
        ('definition headers', """@decorate(dict(cwd='function decorator'))
def build(dict):
    return dict
@decorate(dict(cwd='class decorator'))
class Example(dict(cwd='class base')):
    dict = factory
lambda dict=dict(cwd='lambda default'): dict
""", ()),
    )


def _scope_violations(relative, source, expected):
    return [
        f'{relative}:{source[:source.index(marker)].count(_NL) + 1}: '
        f'unresolved callee dict cwd={cwd} declares no env='
        for marker, cwd in expected]


def test_python_evaluation_scopes_preserve_builtin_dict_identity(tmp):
    del tmp
    for name, source, expected in _scope_cases():
        assert _synthetic_violations(source) == _scope_violations(
            'tests/synthetic.py', source, expected), name


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


def _module_text(target):
    return target.read_bytes().decode('utf-8').replace('\r\n', '\n')


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
    dict_values = (
        "    elif isinstance(value, ast.Dict):\n        for part in "
        "[*value.keys, *value.values]:\n"
        "            if part is not None:\n"
        "                "
        "yield from _carried_parts(part)\n",
        "")
    subscript = (
        "    elif isinstance(value, ast.Subscript):\n"
        "        yield from _carried_parts(value.value)\n"
        "        yield from _carried_parts(value.slice)\n", "")
    call_receiver = (
        "        if (isinstance(node, ast.Call)\n"
        "                and not _has_cwd_control(node)\n"
        "                and any(_names_one_of(part, "
        "facts.subprocess_modules)\n"
        "                        or _is_launch_value(part, facts)\n"
        "                        for part in _call_receiver_parts(node))):\n"
        "            lines.append(node.lineno)\n", "")
    default_scope = (
        "            for value in (*node.args.defaults, "
        "*node.args.kw_defaults):\n"
        "                if value is not None:\n"
        "                    visit(value, scope)\n",
        "            for value in (*node.args.defaults, "
        "*node.args.kw_defaults):\n"
        "                if value is not None:\n"
        "                    visit(value, node)\n",
    )
    function_default_scope = (
        default_scope[0] + "            if node.returns is not None:\n",
        default_scope[1] + "            if node.returns is not None:\n")
    lambda_default_scope = (
        default_scope[0] + "            visit(node.body, node)\n",
        default_scope[1] + "            visit(node.body, node)\n")
    class_body_scope = (
        "            for statement in node.body:\n"
        "                visit(statement, node)\n"
        "            return\n"
        "        if isinstance(node, ast.Lambda):\n",
        "            for statement in node.body:\n"
        "                visit(statement, scope)\n"
        "            return\n"
        "        if isinstance(node, ast.Lambda):\n",
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
    walrus_scope = (
        "        if isinstance(node, ast.NamedExpr):\n"
        "            visit(node.value, scope)\n"
        "            visit(node.target, "
        "_containing_binding_scope(scope, parents))\n"
        "            return\n",
        "        if isinstance(node, ast.NamedExpr):\n"
        "            visit(node.value, scope)\n"
        "            visit(node.target, scope)\n"
        "            return\n",
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
        ('dict values', 'bindings', (dict_values,),
         _INLINE_INVOKE),
        ('subscript values', 'bindings', (subscript,),
         _SUBSCRIPT_INVOKE),
        ('call receiver', 'bindings', (call_receiver,), _INLINE_INVOKE),
        ('function default scope', 'scopes',
         (function_default_scope,),
         _SCOPE_INVOKE),
        ('class body scope', 'scopes', (class_body_scope,),
         _SCOPE_INVOKE),
        ('lambda body scope', 'scopes',
         (("            visit(node.body, node)\n",
           "            visit(node.body, scope)\n"),),
         _SCOPE_INVOKE),
        ('lambda default scope', 'scopes',
         (lambda_default_scope,),
         _SCOPE_INVOKE),
        ('comprehension target scope', 'scopes',
         (("            visit(first.target, node)\n",
           "            visit(first.target, scope)\n"),),
         _SCOPE_INVOKE),
        ('comprehension iterable scope', 'scopes',
         (("            visit(first.iter, scope)\n",
           "            visit(first.iter, node)\n"),),
         _SCOPE_INVOKE),
        ('comprehension walrus scope', 'scopes', (walrus_scope,),
         _SCOPE_INVOKE),
        ('MatchAs global', 'scopes', (match_as_names,),
         'suite.test_match_captures_cannot_disguise_nonroot_chdir(None)'),
        ('MatchStar global', 'scopes', (match_star_names,),
         'suite.test_match_captures_cannot_disguise_nonroot_chdir(None)'),
        ('MatchMapping global', 'scopes', (match_rest_names,),
         'suite.test_match_captures_cannot_disguise_nonroot_chdir(None)'),
    ) + _BASH_MUTATION_SPECS


def test_each_new_binding_and_match_arm_is_mutation_sensitive(tmp):
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    bindings_target = root / 'tests' / '_coverage_bindings.py'
    scopes_target = root / 'tests' / '_coverage_scopes.py'
    bash_target = root / 'tests' / '_bash_resolver_scan.py'
    for name, target_name, replacements, invocation in _mutation_specs():
        target = (bindings_target if target_name == 'bindings'
                  else scopes_target if target_name == 'scopes'
                  else bash_target)
        original = target.read_bytes()
        crlf = b'\r\n' in original
        text = original.decode('utf-8').replace('\r\n', '\n')
        for mutation in replacements:
            needle, replacement = mutation
            assert text.count(needle) == 1, (name, needle)
            text = text.replace(needle, replacement, 1)
        try:
            mutated = text.replace('\n', '\r\n') if crlf else text
            target.write_bytes(mutated.encode('utf-8'))
            program = (
                "import sys\nsys.path.insert(0, 'tests')\n"
                "import test_coverage_bindings as suite\n"
                "assert sys.dont_write_bytecode, "
                "'cached bytecode writes enabled without -B'\n"
                f"print({_FRESH_SOURCE_MARKER!r})\n"
                f"{invocation}\n")
            result = subprocess.run(
                [sys.executable, '-B', '-c', program], cwd=root,
                env=_util.child_coverage('scrub', {
                    name: os.environ[name] for name in dict(os.environ)
                    if name != 'PYTHONDONTWRITEBYTECODE'}),
                capture_output=True,
                text=True, timeout=30)
        finally:
            target.write_bytes(original)
        assert result.stdout == _FRESH_SOURCE_MARKER + '\n', (
            'cached bytecode freshness', result.stdout, result.stderr)
        assert result.returncode != 0, (name, result.stdout, result.stderr)
        assert 'AssertionError' in result.stderr, (name, result.stderr)


def test_controls_never_write_inside_the_repository(tmp):
    del tmp
    violations = control_write_violations(Path(__file__), ROOT)
    assert not violations, '\n'.join(violations)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
