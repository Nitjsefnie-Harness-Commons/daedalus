#!/usr/bin/env python3
"""Every test subprocess either runs provably safe or declares its env.

The fail-closed decision procedure lives in tests/_coverage_guard.py;
these tests pin each rule, the mutation-shaped regressions it exists to
catch, and the behaviour of the _util.child_coverage declaration it
reads.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _coverage_guard  # noqa: E402
import _util  # noqa: E402
from _control_writes import control_write_violations  # noqa: E402
from _coverage_guard import (  # noqa: E402
    _coverage_environment_violations, _synthetic_violations)
from _repo import ROOT  # noqa: E402


def _module_text(target):
    """A test module's source with checkout line endings normalised."""
    return target.read_bytes().decode('utf-8').replace('\r\n', '\n')


def _real_module_copy(tmp, relative):
    """Copy the real test tree under a root this control owns."""
    root = Path(tmp) / 'repository'
    for source in sorted((ROOT / 'tests').glob('*.py')):
        destination = root / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return root, root / relative


def _repository_write_lines():
    """Writes not provably below storage owned by their control."""
    return control_write_violations(Path(__file__), ROOT)


def test_controls_never_write_inside_the_repository(tmp):
    """A control may mutate copied content, never a checkout path."""
    del tmp
    violations = _repository_write_lines()
    assert not violations, '\n'.join(violations)


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


def test_an_absolute_root_suffix_is_unresolved(tmp):
    """An absolute right operand discards ROOT and needs a declaration."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
subprocess.run(['python3', 'child.py'], cwd=ROOT / '/tmp')
""")
    assert len(violations) == 1, violations
    assert "cwd=ROOT / '/tmp'" in violations[0], violations


def test_a_rebound_root_spelling_is_unresolved(tmp):
    """A local ROOT spelling does not prove the repository value."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
ROOT = '/tmp'
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""")
    assert len(violations) == 1, violations
    assert 'cwd=ROOT' in violations[0], violations


def test_a_literal_interpreter_launch_needs_a_declaration(tmp):
    """node/git/shell literals run whatever they are handed: declare."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
subprocess.run(['node', 'child.js'], cwd=tmp)
subprocess.run(['git', 'status'], cwd=tmp)
subprocess.run(['bash', '-c', 'exec "$@"', 'bash', 'child.py'], cwd=tmp)
""")
    assert len(violations) == 3, violations


def test_no_first_element_exempts_a_launch(tmp):
    """A name, attribute or call first element changes nothing."""
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
        assert any('tests/synthetic.py:5' in v for v in violations), (
            mutation, violations)


def test_a_declaration_name_used_elsewhere_is_a_violation(tmp):
    """Aliasing, subscripting or passing the name are all refused."""
    del tmp
    for appearance in ('alias = env',
                       'env["x"]',
                       'print(env)',
                       'env.copy()'):
        violations = _synthetic_violations(
            f"""import subprocess
import _util
env = _util.child_coverage('scrub')
{appearance}
subprocess.run(['python3', 'child.py'], cwd=tmp, env=env)
""")
        assert any('tests/synthetic.py:4' in v for v in violations), (
            appearance, violations)


def test_a_declaration_name_cannot_be_aliased(tmp):
    """An alias mutates the same dict the launches use: it is refused."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    needle = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    text = _module_text(target)
    assert needle in text, 'the declaration binding shape changed'
    line = text[:text.index(needle)].count('\n') + 2
    mutated = text.replace(
        needle,
        needle
        + "_COVERAGE_ALIAS = _COVERAGE_ENV\n"
        + "_COVERAGE_ALIAS['COVERAGE_PROCESS_START'] = "
        + "os.environ['COVERAGE_PROCESS_START']\n", 1)
    original = target.read_bytes()
    try:
        target.write_bytes(mutated.encode('utf-8'))
        violations = _coverage_environment_violations(root)
        assert any(
            v.startswith(f'tests/test_diff_coverage.py:{line}:')
            for v in violations), violations
    finally:
        target.write_bytes(original)
    restored = _coverage_environment_violations(root)
    assert not any(
        v.startswith(f'tests/test_diff_coverage.py:{line}:')
        for v in restored), restored


def test_rebinding_the_declaration_helper_is_a_violation(tmp):
    """Assigning to the helper, its module attribute, or its alias fails."""
    del tmp
    for binding in ('child_coverage = lambda _mode: {}',
                    '_util.child_coverage = lambda _mode: {}',
                    "setattr(_util, 'child_coverage', lambda _mode: {})",
                    'setattr(_util, helper_name, lambda _mode: {})',
                    '_util = replacement',
                    "globals()['_util'] = replacement",
                    'globals()[helper_name] = replacement',
                    "globals().update({'_util': replacement})",
                    'cc = None'):
        violations = _synthetic_violations(
            f"""import subprocess
import _util
from _util import child_coverage as cc
{binding}
_ENV = _util.child_coverage('scrub')
subprocess.run(['python3', 'child.py'], cwd=tmp, env=_ENV)
""")
        assert any('tests/synthetic.py:4' in v for v in violations), (
            binding, violations)


def test_a_rebound_declaration_helper_is_caught(tmp):
    """Rebinding _util.child_coverage makes declarations no-ops: refuse."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    needle = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
    text = _module_text(target)
    assert needle in text, 'the declaration binding shape changed'
    line = text[:text.index(needle)].count('\n') + 1
    mutated = text.replace(
        needle,
        "_util.child_coverage = lambda _mode: dict(os.environ)\n"
        + needle, 1)
    original = target.read_bytes()
    try:
        target.write_bytes(mutated.encode('utf-8'))
        violations = _coverage_environment_violations(root)
        assert any(
            v.startswith(f'tests/test_diff_coverage.py:{line}:')
            for v in violations), violations
    finally:
        target.write_bytes(original)
    restored = _coverage_environment_violations(root)
    assert not any(
        v.startswith(f'tests/test_diff_coverage.py:{line}:')
        for v in restored), restored


def test_a_rebound_scrub_delegate_fails_in_a_real_module(tmp):
    """A declaration rejects the value returned by a rebound scrub."""
    relative = Path('tests/test_coverage_combine.py')
    root, target = _real_module_copy(tmp, relative)
    needle = "_ENV = _util.child_coverage('scrub')\n"
    text = _module_text(target)
    assert needle in text, 'the declaration binding shape changed'
    mutated = text.replace(
        needle,
        "import os\n"
        + "os.environ['COVERAGE_ROUND6_PROBE'] = 'must-not-leak'\n"
        + "_util.coverage_free_environment = lambda env: dict(env)\n"
        + needle, 1)
    target.write_bytes(mutated.encode('utf-8'))
    violations = _coverage_environment_violations(root)
    assert not violations, violations
    result = subprocess.run(
        [sys.executable, '-c',
         f"import runpy; runpy.run_path({str(target)!r})"],
        cwd=root, env=_util.child_coverage('scrub'), capture_output=True,
        text=True, timeout=30)
    assert result.returncode != 0, result.stdout
    assert 'COVERAGE_ROUND6_PROBE' in result.stderr, result.stderr


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


def test_an_imported_chdir_taints_an_inherited_cwd(tmp):
    """Importing chdir directly cannot hide a cwd mutation."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
from os import chdir
chdir(tmp)
subprocess.run(['python3', 'child.py'])
""")
    assert len(violations) == 1, violations
    assert 'chdir at line 3' in violations[0], violations


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
               env=_util.child_coverage('keep', cwd=tmp))
""")
    assert violations == [
        'tests/synthetic.py::<module> declares keep without an allowlist '
        + 'entry'], violations


def test_an_allowlist_entry_without_a_keep_site_fails(tmp):
    """A stale entry is as much a failure as an unlisted site."""
    del tmp
    original = _coverage_guard._KEEP_ALLOWLIST
    _coverage_guard._KEEP_ALLOWLIST = (
        original | {'tests/synthetic.py::ghost'})
    try:
        violations = _coverage_environment_violations(ROOT)
    finally:
        _coverage_guard._KEEP_ALLOWLIST = original
    assert ('allowlisted keep site tests/synthetic.py::ghost has no launch'
            in violations), violations


def test_shell_wrapped_python_in_a_temp_cwd_is_caught(tmp):
    """A bash literal wrapping sys.executable runs Python: declare."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    needle = (
        "        done = subprocess.run(\n"
        "            [sys.executable, str(_SCRIPT), '--coverage', "
        "str(coverage_xml),\n"
        "             '--diff', str(diff)], cwd=tmp, env=_COVERAGE_ENV,\n"
        "            capture_output=True, text=True, timeout=60)")
    text = _module_text(target)
    assert needle in text, 'the shell-wrap launch shape changed'
    line = text[:text.index(needle)].count('\n') + 1
    mutated = text.replace(
        needle,
        "        done = subprocess.run(\n"
        "            ['bash', '-c', 'exec \"$@\"', 'bash', sys.executable,\n"
        "             str(_SCRIPT), '--coverage', str(coverage_xml),\n"
        "             '--diff', str(diff)], cwd=tmp,\n"
        "            capture_output=True, text=True, timeout=60)", 1)
    original = target.read_bytes()
    try:
        target.write_bytes(mutated.encode('utf-8'))
        violations = _coverage_environment_violations(root)
        assert any(
            v.startswith(f'tests/test_diff_coverage.py:{line}:')
            for v in violations), violations
    finally:
        target.write_bytes(original)
    restored = _coverage_environment_violations(root)
    assert not any(
        v.startswith(f'tests/test_diff_coverage.py:{line}:')
        for v in restored), restored


def test_real_module_controls_agree_under_crlf_endings(tmp):
    """A CRLF checkout searches and counts lines exactly like LF."""
    del tmp
    target = ROOT / 'tests' / 'test_diff_coverage.py'
    lf = _module_text(target)
    crlf = lf.replace('\n', '\r\n')
    single = "'--diff', str(diff)], cwd=tmp, env=_COVERAGE_ENV,"
    multi = ("    done = subprocess.run(\n"
             "        [sys.executable, str(_SCRIPT), '--coverage', "
             "str(coverage_xml),\n")
    assert single in lf and single in crlf
    assert multi in lf and multi not in crlf
    normalised = crlf.replace('\r\n', '\n')
    assert multi in normalised
    assert (lf[:lf.index(multi)].count('\n')
            == normalised[:normalised.index(multi)].count('\n'))


def test_row1_a_repository_script_in_a_temp_cwd_must_declare(tmp):
    """Deleting env= from a real repo-script launch in tmp is caught."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    needle = "'--diff', str(diff)], cwd=tmp, env=_COVERAGE_ENV,"
    text = _module_text(target)
    assert needle in text, 'the row 1 launch shape changed'
    replacement = "'--diff', str(diff)], cwd=tmp,"
    start = text.rindex('subprocess.run(', 0, text.index(needle))
    line = text[:start].count('\n') + 1
    original = target.read_bytes()
    try:
        target.write_bytes(text.replace(
            needle, replacement, 1).encode('utf-8'))
        violations = _coverage_environment_violations(root)
        assert any(
            v.startswith(f'tests/test_diff_coverage.py:{line}:')
            for v in violations), violations
    finally:
        target.write_bytes(original)
    restored = _coverage_environment_violations(root)
    assert not any(
        v.startswith(f'tests/test_diff_coverage.py:{line}:')
        for v in restored), restored


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
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    needle = (
        "    done = subprocess.run(\n"
        "        [sys.executable, str(_SCRIPT), '--coverage', "
        "str(coverage_xml),\n"
        "         '--diff', str(diff)],\n"
        "        cwd=tmp, env=_COVERAGE_ENV, capture_output=True, text=True, "
        "timeout=60)")
    text = _module_text(target)
    assert needle in text, 'the row 3 launch shape changed'
    restored_line = text[:text.index(needle)].count('\n') + 1
    mutated = text.replace(
        needle,
        "    command = [sys.executable, str(_SCRIPT), '--coverage',\n"
        "               str(coverage_xml), '--diff', str(diff)]\n"
        "    done = subprocess.run(command, cwd=tmp, capture_output=True,\n"
        "                          text=True, timeout=60)", 1)
    line = mutated[:mutated.index(
        'subprocess.run(command, cwd=tmp,')].count('\n') + 1
    original = target.read_bytes()
    try:
        target.write_bytes(mutated.encode('utf-8'))
        violations = _coverage_environment_violations(root)
        assert any(
            v.startswith(f'tests/test_diff_coverage.py:{line}:')
            for v in violations), violations
    finally:
        target.write_bytes(original)
    restored = _coverage_environment_violations(root)
    assert not any(
        v.startswith(f'tests/test_diff_coverage.py:{restored_line}:')
        for v in restored), restored


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
    violations = _coverage_environment_violations(ROOT)
    assert not violations, '\n'.join(violations)


def test_child_coverage_declares_scrub_and_keep(tmp):
    """The helper scrubs, keeps in a mapped tree, and rejects bad modes."""
    environment = {'COVERAGE_PROCESS_START': 'x', 'PATH': '/bin'}
    assert _util.child_coverage('scrub', environment) == {'PATH': '/bin'}
    kept = _util.child_coverage('keep', environment, cwd=Path(tmp) / 'tree')
    assert kept == environment and kept is not environment
    try:
        _util.child_coverage('maybe')
    except ValueError:
        pass
    else:
        raise AssertionError("child_coverage accepted mode 'maybe'")


def test_child_coverage_rejects_a_leaking_scrub_result(tmp):
    """Scrub mode validates the environment it is about to return."""
    del tmp

    def leaking_scrub(environment):
        return dict(environment)

    original = _util.coverage_free_environment
    _util.coverage_free_environment = leaking_scrub
    try:
        environment = {'COVERAGE_PROCESS_START': 'must-not-leak'}
        try:
            _util.child_coverage('scrub', environment)
        except ValueError as error:
            assert 'COVERAGE_PROCESS_START' in str(error), error
        else:
            raise AssertionError('child_coverage returned a leaking scrub')
    finally:
        _util.coverage_free_environment = original


def test_child_coverage_keep_requires_a_mapped_tree(tmp):
    """A keep outside the '*/tree' anchor fails where it is declared."""
    for cwd in (None, Path(tmp) / 'unmapped-runner',
                Path(tmp) / 'tree' / '..' / 'unmapped-runner'):
        try:
            _util.child_coverage('keep', {}, cwd=cwd)
        except ValueError as error:
            if cwd is not None:
                assert 'unmapped-runner' in str(error), error
        else:
            raise AssertionError(f'keep accepted cwd={cwd}')


def test_keep_outside_a_mapped_tree_fails_at_runtime(tmp):
    """Renaming the runner's tree anchor trips the keep declaration."""
    relative = Path('tests/test_suite_runner.py')
    _, target = _real_module_copy(tmp, relative)
    needle = "    root = Path(tmp) / under / 'tree'"
    text = _module_text(target)
    assert needle in text, 'the runner tree anchor shape changed'
    mutated = text.replace(
        needle, "    root = Path(tmp) / under / 'unmapped-runner'", 1)
    target.write_bytes(mutated.encode('utf-8'))
    module = _util.load(target, 'suite_runner_unmapped')
    try:
        module._runner_tree(
            tmp, {'test_ok.py': 'def test_ok(tmp):\n    del tmp\n'})
    except ValueError as error:
        assert 'unmapped-runner' in str(error), error
    else:
        raise AssertionError(
            'a keep launch outside a mapped tree ran unchallenged')


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
