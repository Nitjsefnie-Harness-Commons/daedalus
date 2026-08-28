#!/usr/bin/env python3
"""Every test subprocess either runs provably safe or declares its env.

The fail-closed decision procedure lives in tests/_coverage_guard.py;
these tests pin each rule, the mutation-shaped regressions it exists to
catch, and the behaviour of the _util.child_coverage declaration it
reads.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _coverage_guard  # noqa: E402
import _util  # noqa: E402
from _coverage_guard import (  # noqa: E402
    _coverage_environment_violations, _synthetic_violations)
from _repo import ROOT  # noqa: E402


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
    original = _coverage_guard._KEEP_ALLOWLIST
    _coverage_guard._KEEP_ALLOWLIST = (
        original | {'tests/synthetic.py::ghost'})
    try:
        violations = _coverage_environment_violations()
    finally:
        _coverage_guard._KEEP_ALLOWLIST = original
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
    start = text.rindex('subprocess.run(', 0, text.index(needle))
    line = text[:start].count('\n') + 1
    try:
        target.write_bytes(text.replace(
            needle, replacement, 1).encode('utf-8'))
        violations = _coverage_environment_violations()
        assert any(
            v.startswith(f'tests/test_diff_coverage.py:{line}:')
            for v in violations), violations
    finally:
        target.write_bytes(original)
    restored = _coverage_environment_violations()
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
    restored_line = text[:text.index(needle)].count('\n') + 1
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
    restored = _coverage_environment_violations()
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
