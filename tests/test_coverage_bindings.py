#!/usr/bin/env python3
"""Launcher bindings the coverage-environment guard cannot follow."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _binding_assertions import (  # noqa: E402
    _assert_binding_pair, _scope_cases, _scope_violations)
from _coverage_guard import _synthetic_violations  # noqa: E402
from _coverage_mutation_specs import (  # noqa: E402
    _BASH_MUTATION_SPECS, _CACHE_MUTATIONS, _DEST_MUTATIONS,
    _SCOPE_INVOKE as _SHARED_SCOPE_INVOKE, _SCOPE_MUTATIONS,
    _UNFOLLOWABLE_MUTATIONS)
from _mutation_sweep import mutation_sweep  # noqa: E402


_SCOPE_INVOKE = (
    'suite.test_python_evaluation_scopes_preserve_builtin_dict_identity(None)')
_INLINE_INVOKE = (
    'import test_static_guard_regressions as regression_suite; '
    'regression_suite.test_inline_dict_receivers_refuse_hidden_'
    'launchers(None)')
_SCOPE_BINDING_INVOKE = (
    'import test_coverage_scope_bindings as binding_suite; '
    'binding_suite.test_import_bindings_shadow_the_builtin_dict(None)')
_ANNOTATION_INVOKE = (
    'import test_coverage_scope_bindings as binding_suite; '
    'binding_suite.test_variadic_parameter_annotations_are_judged(None)')
_SUBSCRIPT_INVOKE = (
    'import test_static_guard_regressions as regression_suite; '
    'regression_suite.test_subscripted_dict_carriers_refuse_hidden_'
    'launchers(None)')


def _mutation_specs():
    """This suite's own rows, then the tables the other suites grew."""
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
        "    if isinstance(node, _SIGNED_FORMS):\n", "    if False:\n")
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
        "    if isinstance(callee, (ast.Tuple, ast.List, ast.Set, ast.Dict)):"
        "\n        yield from _carried_parts(callee)\n",
        "    if False:\n        yield from _carried_parts(callee)\n")
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
        "    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, "
        "ast.MatchStar)):\n",
        "    if isinstance(node, (ast.ExceptHandler, ast.MatchStar)):\n",
    )
    match_star_names = (
        match_as_names[0],
        "    if isinstance(node, (ast.ExceptHandler, ast.MatchAs)):\n",
    )
    match_rest_names = (
        "    if isinstance(node, ast.MatchMapping):\n"
        "        return {node.rest} if node.rest else set()\n", "",
    )
    import_bindings = (
        "    if isinstance(node, (ast.Import, ast.ImportFrom)):\n"
        "        return {_import_bound_name(node, alias) "
        "for alias in node.names}\n",
        "    if isinstance(node, (ast.Import, ast.ImportFrom)):\n"
        "        return set()\n",
    )
    narrowed_bindings = (
        "    return alias.asname or alias.name\n",
        "    if alias.name == '_util':\n        return alias.name\n"
        "    return alias.asname or alias.name\n",
    )
    rebinding_retires = (
        "            if module not in gone "
        "and not _rebound_by_import(name, rebound)}\n",
        "            if module not in gone}\n",
    )
    rebinding_shadows = (
        "    owners = facts.root_owners\n",
        "    owners = {'_util'}\n",
    )
    binding_aliasing = (
        "    return _routed_bindings(shadows, destinations)\n\n"
        "\n_FUNCTION_SCOPES",
        "    for node, scope in scoped:\n"
        "        if isinstance(node, (ast.Import, ast.ImportFrom)):\n"
        "            shadows[scope].update(\n"
        "                alias.asname or alias.name for alias in node.names)\n"
        "    return _routed_bindings(shadows, destinations)\n\n"
        "\n_FUNCTION_SCOPES",
    )
    variadic_annotations = (
        "        variadic = [value for value in (arguments.vararg, "
        "arguments.kwarg)\n"
        "                    if value is not None]\n",
        "        variadic = []\n",
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
         'import test_coverage_unfollowable_forms as form_suite; '
         'form_suite.test_a_launcher_reached_only_through_a_callee_chain_is_'
         'refused(None)'),
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
        ('import bindings', 'scopes', (import_bindings,),
         _SCOPE_BINDING_INVOKE),
        ('narrowed bindings', 'scopes', (narrowed_bindings,),
         _SCOPE_BINDING_INVOKE),
        ('imports contaminate shadows', 'scopes', (binding_aliasing,),
         _SHARED_SCOPE_INVOKE),
        ('import rebinding retires the owner', 'scopes',
         (rebinding_retires,), _SHARED_SCOPE_INVOKE),
        ('import rebinding shadows _util', 'scopes',
         (rebinding_shadows,), _SHARED_SCOPE_INVOKE),
        ('variadic annotations', 'scopes', (variadic_annotations,),
         _ANNOTATION_INVOKE),
        ('MatchAs global', 'scopes', (match_as_names,),
         'suite.test_match_captures_cannot_disguise_nonroot_chdir(None)'),
        ('MatchStar global', 'scopes', (match_star_names,),
         'suite.test_match_captures_cannot_disguise_nonroot_chdir(None)'),
        ('MatchMapping global', 'scopes', (match_rest_names,),
         'suite.test_match_captures_cannot_disguise_nonroot_chdir(None)'),
    ) + _BASH_MUTATION_SPECS + _SCOPE_MUTATIONS \
        + _DEST_MUTATIONS + _CACHE_MUTATIONS \
        + _UNFOLLOWABLE_MUTATIONS


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
from _repo import ROOT
match value:
    case {pattern}:
        pass
os.chdir(ROOT)
subprocess.run(['python3', 'child.py'])
""")
        assert len(violations) == 1, (pattern, violations)
        assert 'os.chdir at line 7 may have moved the cwd' in violations[0]


def test_each_new_binding_and_match_arm_is_mutation_sensitive(tmp):
    mutation_sweep(tmp, _mutation_specs())


def test_controls_never_write_inside_the_repository(tmp):
    from _control_writes import control_write_violations
    from _repo import ROOT

    del tmp
    violations = control_write_violations(Path(__file__), ROOT)
    assert not violations, '\n'.join(violations)


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
