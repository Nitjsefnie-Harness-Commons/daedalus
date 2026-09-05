#!/usr/bin/env python3
"""Focused real-tree regressions for the static guard suites."""
import ast
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_guard import _coverage_environment_violations  # noqa: E402
from _coverage_scopes import (  # noqa: E402
    _evaluation_scopes, _scope_bindings)
from _owned_writes import copy_test_tree  # noqa: E402
import test_coverage_bindings as _coverage_suite  # noqa: E402


def _real_module_copy(tmp, relative):
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    return root, root / relative


def test_mutation_gate_accepts_crlf_copied_helpers(tmp):
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    bindings = root / 'tests' / '_coverage_bindings.py'
    scopes = root / 'tests' / '_coverage_scopes.py'
    bash = root / 'tests' / '_bash_resolver_scan.py'
    sources = [bindings.read_bytes(), scopes.read_bytes(), bash.read_bytes()]
    crlf_sources = [source.replace(b'\r\n', b'\n').replace(
        b'\n', b'\r\n') for source in sources]
    assert all(b'\r\n' in source
               and b'\n' not in source.replace(b'\r\n', b'')
               for source in crlf_sources)
    bindings.write_bytes(crlf_sources[0])
    scopes.write_bytes(crlf_sources[1])
    bash.write_bytes(crlf_sources[2])
    mutation_tmp = Path(tmp) / 'mutations'
    program = (
        "import sys\nsys.path.insert(0, 'tests')\n"
        "import test_coverage_bindings as suite\n"
        'suite.test_each_new_binding_and_match_arm_is_mutation_sensitive('
        f'{str(mutation_tmp)!r})\n')
    result = subprocess.run(
        [sys.executable, '-B', '-c', program], cwd=root,
        env=_util.child_coverage('scrub'), capture_output=True,
        text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert [bindings.read_bytes(), scopes.read_bytes(),
            bash.read_bytes()] == crlf_sources


def test_subscripted_dict_carriers_refuse_hidden_launchers(tmp):
    del tmp
    pairs = (
        (
            """import os
import subprocess
launcher = {'sp': subprocess}['sp']
os.chdir(tmp)
launcher.run(['python3', 'child.py'])
""",
            3,
            """import os
import subprocess
result = {'sp': subprocess.run(
        ['python3', 'child.py'], cwd=tmp)}['sp']
""",
            3),
        (
            """import os
import subprocess
os.chdir(tmp)
def go(launcher={'sp': subprocess}['sp']):
    launcher.run(['python3', 'child.py'])
go()
""",
            4,
            """import os
import subprocess
def go(result={'sp': subprocess.run(
        ['python3', 'child.py'], cwd=tmp)}['sp']):
    return result
""",
            3),
        (
            """import os
import subprocess
launchers = {subprocess: 'sp'}
os.chdir(tmp)
for launcher in launchers:
    launcher.run(['python3', 'child.py'])
""",
            3,
            """import os
import subprocess
results = {subprocess.run(
        ['python3', 'child.py'], cwd=tmp): 'sp'}
""",
            3),
    )
    for unsafe, unsafe_line, explicit, explicit_line in pairs:
        _coverage_suite._assert_binding_pair(
            unsafe, unsafe_line, explicit, explicit_line)


def test_subscripted_unresolved_callee_stays_unresolved(tmp):
    del tmp
    source = """import subprocess
launchers = {'sp': mystery}
launchers['sp'](['python3', 'child.py'], cwd=tmp)
"""
    assert _coverage_suite._synthetic_violations(source) == [
        "tests/synthetic.py:3: unresolved callee launchers['sp'] "
        'cwd=tmp declares no env='
    ]


def test_inline_dict_receivers_refuse_hidden_launchers(tmp):
    del tmp
    unsafe_sources = (
        ("""import os
import subprocess
os.chdir(tmp)
{'sp': subprocess}['sp'].run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
for launcher in {'sp': subprocess}.values():
    launcher.run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
for launcher in {'outer': {'sp': subprocess}}['outer'].values():
    launcher.run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
[{'sp': subprocess}][0]['sp'].run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
({'sp': subprocess},)[0]['sp'].run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
for launcher in [{'sp': subprocess}][0].values():
    launcher.run(['python3', 'child.py'])
""", 4),
        ("""import os
import subprocess
os.chdir(tmp)
for launcher in ([{'sp': subprocess}],)[0][0].values():
    launcher.run(['python3', 'child.py'])
""", 4),
    )
    for source, line in unsafe_sources:
        assert _coverage_suite._synthetic_violations(source) == [
            _coverage_suite._binding_violation(line)]

    assert _coverage_suite._synthetic_violations(
        """import os
import subprocess
os.chdir(tmp)
{'sp': subprocess}['sp'].run(['python3', 'child.py'], cwd=ROOT)
""") == []

    assert _coverage_suite._synthetic_violations(
        """import os
import subprocess
os.chdir(tmp)
{'sp': subprocess}['sp'].run(['python3', 'child.py'], cwd=tmp)
""") == [
        "tests/synthetic.py:4: unresolved callee "
        "{'sp': subprocess}['sp'].run cwd=tmp declares no env="
    ]


def test_guard_and_binding_scans_share_cwd_predicate(tmp):
    del tmp
    from _coverage_bindings import _has_cwd_control  # noqa: PLC0415
    from _coverage_guard import (  # noqa: PLC0415
        _has_cwd_control as guard_has_cwd_control)

    assert guard_has_cwd_control is _has_cwd_control


def test_inline_dict_receiver_keeps_unresolved_callee_diagnostic(tmp):
    del tmp
    source = """import os
import mystery
os.chdir(tmp)
{'sp': mystery}['sp'](['python3', 'child.py'], cwd=tmp)
"""
    assert _coverage_suite._synthetic_violations(source) == [
        "tests/synthetic.py:4: unresolved callee {'sp': mystery}['sp'] "
        'cwd=tmp declares no env='
    ]


def test_nonlauncher_binding_controls_stay_clean(tmp):
    del tmp
    assert _coverage_suite._synthetic_violations(
        """import subprocess
match subprocess:
    case None:
        pass
result = subprocess.run(['python3', 'child.py'], cwd=ROOT)
""") == []


def test_named_unreadable_spread_remains_a_violation(tmp):
    del tmp
    violations = _coverage_suite._synthetic_violations(
        """import subprocess
kw = dict({'cwd': tmp})
subprocess.run(['python3', 'child.py'], **kw)
""")
    assert len(violations) == 1, violations
    assert 'cwd may arrive through a ** spread' in violations[0], violations


def test_real_tree_refuses_each_complete_binding_bypass(tmp):
    root, target = _real_module_copy(tmp, Path('tests/test_diff_coverage.py'))
    source = _coverage_suite._module_text(target)
    anchor = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    assert anchor in source, 'the coverage declaration shape changed'
    original = target.read_bytes()
    for name, unsafe, marker, explicit in _coverage_suite._binding_snippets():
        mutated, line = _coverage_suite._inserted_line(
            source, anchor, unsafe, marker)
        try:
            target.write_bytes(mutated.encode('utf-8'))
            violations = _coverage_environment_violations(root)
            expected = (
                f'tests/test_diff_coverage.py:{line}: '
                f'{_coverage_suite._BINDING_MESSAGE}')
            assert expected in violations, (name, violations)
        finally:
            target.write_bytes(original)
        restored = _coverage_environment_violations(root)
        assert not any(v.startswith(
            f'tests/test_diff_coverage.py:{line}:') for v in restored), (
                name, restored)

        explicit_source, _ = _coverage_suite._inserted_line(
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
            assert all(_coverage_suite._BINDING_MESSAGE not in item
                       for item in explicit_violations), (
                           name, explicit_violations)
        finally:
            target.write_bytes(original)


def test_real_tree_allows_an_unshadowed_builtin_dict(tmp):
    root, target = _real_module_copy(tmp, Path('tests/test_diff_coverage.py'))
    source = _coverage_suite._module_text(target)
    anchor = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    snippet = "_BUILTIN_DICT_CONTROL = dict(cwd='x')\n"
    target.write_bytes(source.replace(
        anchor, snippet + anchor, 1).encode('utf-8'))
    assert _coverage_environment_violations(root) == []


def test_a_star_import_removes_the_builtin_exemption(tmp):
    del tmp
    assert _coverage_suite._synthetic_violations(
        "from helpers import *\nkw = dict(cwd='x')\n") == [
            "tests/synthetic.py:2: unresolved callee dict "
            "cwd='x' declares no env="]
    tree = ast.parse('from helpers import *\n')
    bindings = _scope_bindings(*_evaluation_scopes(tree))
    assert '*' in bindings[tree], bindings[tree]


def test_real_tree_applies_python_evaluation_scopes(tmp):
    root, target = _real_module_copy(tmp, Path('tests/test_diff_coverage.py'))
    source = _coverage_suite._module_text(target)
    anchor = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    original = target.read_bytes()
    for name, snippet, expected in _coverage_suite._scope_cases():
        mutated = source.replace(anchor, snippet + anchor, 1)
        try:
            target.write_bytes(mutated.encode('utf-8'))
            violations = _coverage_environment_violations(root)
        finally:
            target.write_bytes(original)
        wanted = _coverage_suite._scope_violations(
            'tests/test_diff_coverage.py', mutated, expected)
        assert violations == wanted, (name, violations)


def test_malformed_controls_and_destinations_fail_closed(tmp):
    from test_workflow_cache_boundary import (  # noqa: PLC0415
        _cache_write_reason, _refuses)

    _refuses(
        _cache_write_reason,
        {'uses': 'actions/setup-go@v6', 'with': {'cache': ['false']}},
        'wheel', 3, contains='not a literal scalar')
    _refuses(
        _cache_write_reason,
        {'uses': 'docker/build-push-action@v6',
         'with': {'cache-to': 'type=${{ inputs.kind }}'}},
        'wheel', 3, contains='dynamic destination')
    _refuses(
        _cache_write_reason,
        {'uses': 'docker/build-push-action@v6', 'with': {'cache-to': None}},
        'wheel', 3, contains='cache-to is not a literal')
    for keys in (('cache-to', 'CACHE-TO'), ('cache-to', 'Cache-To')):
        _refuses(
            _cache_write_reason,
            {'uses': 'docker/build-push-action@v6',
             'with': {key: 'type=gha' for key in keys}},
            'wheel', 3, contains='duplicated case-insensitively')


def test_direct_cache_markers_are_token_bounded(tmp):
    from test_workflow_cache_boundary import _direct_cache_run  # noqa: PLC0415

    positives = (
        'curl "$ACTIONS_CACHE_URL/_apis/artifactcache/cache"',
        'curl "/_apis/artifactcache/cache"',
        'curl "$ACTIONS_RESULTS_URL"',
        'curl -H "$ACTIONS_RUNTIME_TOKEN" /cache',
        'github.actions.results.api.v1.CacheService/GetCacheEntry',
        "node -e \"require('@actions/cache')\"",
        'docker buildx build --cache-to type=gha,mode=max .',
        'docker buildx build --cache-to \\\n type=gha .',
    )
    for run in positives:
        assert _direct_cache_run(run) is not None, run
    negatives = (
        'echo MY_ACTIONS_CACHE_URL_BACKUP',
        'echo ACTIONS_CACHE_URL_BACKUP',
        'echo github.actions.results.api.v1.CacheServiceX',
        "echo '@actions/cacheable'",
        'docker buildx build --cache-to type=local --cache-from type=gha .',
        'docker buildx build --cache-from type=gha .',
        'docker buildx build --cache-to type=gha2 .',
        'docker buildx build --cache-from type=gha --cache-to type=local .',
        'docker buildx build --cache-to type=gh .',
    )
    for run in negatives:
        assert _direct_cache_run(run) is None, run


def test_direct_dynamic_buildx_destination_is_indeterminate(tmp):
    from test_workflow_cache_boundary import (  # noqa: PLC0415
        _direct_cache_run, _refuses)

    for run in (
            'docker buildx build --cache-to type=${TYPE} .',
            'docker buildx build --cache-to "$CACHE_DEST" .',
            'docker buildx build --cache-to type=${{ matrix.type }} .'):
        _refuses(_direct_cache_run, run, contains='dynamic destination')
    _refuses(_direct_cache_run, 'x' * 65537, contains='65536')


def test_real_workflow_mutations_are_seen_by_the_writer_inventory(tmp):
    from test_workflow_cache_boundary import (  # noqa: PLC0415
        _assert_writer_inventory, _cache_writing_jobs, _insert_wheel_step,
        _real_step, _refuses)
    from _wfgraph import _tests_yml  # noqa: PLC0415

    workflow = _tests_yml()
    positives = (
        _real_step(uses='actions/setup-go@v6'),
        _real_step(uses='actions/setup-node@v7'),
        _real_step(uses='docker/setup-buildx-action@v3'),
        _real_step(uses='astral-sh/setup-uv@v7',
                   inputs={'enable-cache': 'true'}),
        _real_step(uses='Swatinem/rust-cache@v2'),
        _real_step(uses='docker/build-push-action@v6',
                   inputs={'cache-to': 'type=gha'}),
        _real_step(run='curl "$ACTIONS_CACHE_URL/_apis/artifactcache/cache"'),
        _real_step(run='curl "$ACTIONS_RESULTS_URL"'),
        _real_step(run='curl "$ACTIONS_RUNTIME_TOKEN"'),
        _real_step(run='github.actions.results.api.v1.CacheService/Get'),
        _real_step(run="node -e \"require('@actions/cache')\""),
        _real_step(run='docker buildx build --cache-to type=gha .'),
    )
    for step in positives:
        assert 'wheel' in _cache_writing_jobs(
            _insert_wheel_step(workflow, step)), step
    uppercase = _insert_wheel_step(
        workflow, _real_step(uses='docker/build-push-action@v6',
                             inputs={'CACHE-TO': 'type=gha'}))
    _refuses(_assert_writer_inventory, uppercase,
             contains="unrecorded cache-writing jobs: ['wheel']")


def test_real_workflow_unknown_and_expression_mutations_refuse(tmp):
    from test_workflow_cache_boundary import (  # noqa: PLC0415
        _cache_writing_jobs, _insert_wheel_step, _real_step, _refuses)
    from _wfgraph import _tests_yml  # noqa: PLC0415

    workflow = _tests_yml()
    for step, expected in (
            (_real_step(uses='${{ matrix.action }}'),
             "expression-valued uses '${{ matrix.action }}'"),
            (_real_step(uses='owner/action@v1'),
             "no cache policy for action 'owner/action@v1'"),
            (_real_step(uses='actions/cache/unknown@v4'),
             "unknown actions/cache sub-action 'actions/cache/unknown'")):
        _refuses(_cache_writing_jobs, _insert_wheel_step(workflow, step),
                 contains=expected)


def test_eslint_opt_out_keeps_the_production_set_closed(tmp):
    from test_workflow_cache_boundary import (  # noqa: PLC0415
        _assert_writer_inventory, _refuses)
    from _wfgraph import _tests_yml  # noqa: PLC0415

    workflow = _tests_yml()
    _assert_writer_inventory(workflow)
    line = '          package-manager-cache: false\n'
    assert line in workflow
    without_opt_out = workflow.replace(line, '', 1)
    message = _refuses(_assert_writer_inventory, without_opt_out)
    assert message == (
        "unrecorded cache-writing jobs: ['eslint']; "
        "recorded cache-writing jobs gone quiet: []")


def test_production_cache_steps_keep_restore_and_save_separate(tmp):
    from test_workflow_cache_boundary import (  # noqa: PLC0415
        _cache_write_reason)
    from _wfgraph import _tests_yml  # noqa: PLC0415
    from _yamlsteps import complete_job_mapping  # noqa: PLC0415

    workflow = _tests_yml()
    for job in ('suites', 'coverage-matrix', 'coverage'):
        steps = complete_job_mapping(workflow, job)['steps']
        restores = [step for step in steps if step.get('uses', '').startswith(
            'actions/cache/restore@')]
        saves = [step for step in steps if step.get('uses', '').startswith(
            'actions/cache/save@')]
        assert len(restores) == len(saves) == 1, (job, restores, saves)
        assert _cache_write_reason(restores[0], job, 1) is None
        assert _cache_write_reason(saves[0], job, 1) is not None


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals())),
                                  tmp_prefix='staticguards_'))
