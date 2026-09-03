#!/usr/bin/env python3
"""No test launches the workflow shell except through the shared resolver."""
import ast
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bash_resolver_scan  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT, iter_tree_files  # noqa: E402

# The three resolver call sites, each as (module, the spelling the site
# carries, the bypass to plant, and the launch that would run it).
_SITES = (
    ('tests/_wfgraph.py',
     '        bash = _util.workflow_bash()',
     "        bash = 'bash'",
     '        return subprocess.run([bash'),
    ('tests/test_claim_workflow.py',
     '    bash = _util.workflow_bash()',
     "    bash = 'bash'",
     '    result = subprocess.run([bash'),
    ('tests/test_coverage_comment_workflow.py',
     '        [_util.workflow_bash(),',
     "        ['bash',",
     '    return subprocess.run('),
)


def _tracked_test_modules():
    """The test modules Git tracks for this checkout."""
    return {
        path.relative_to(ROOT).as_posix()
        for path in iter_tree_files(ROOT)
        if path.parent == ROOT / 'tests' and path.suffix == '.py'
    }


def _tree_copy(tmp):
    """A copy of the tracked test tree this control may mutate."""
    root = Path(tmp) / 'repository'
    for source in sorted((ROOT / 'tests').glob('*.py')):
        destination = root / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return root


def _mutated_copy(tmp, site):
    """A copied tree with one site bypassing, and where its launch sits."""
    root = _tree_copy(tmp)
    relative, needle, replacement, launch_needle = site
    target = root / relative
    text = target.read_text(encoding='utf-8')
    assert text.count(needle) == 1, f'the {relative} site shape changed'
    assert launch_needle in text, f'the {relative} launch shape changed'
    launch_line = text[:text.index(launch_needle)].count('\n') + 1
    target.write_text(text.replace(needle, replacement, 1), encoding='utf-8')
    return root, f'{relative}:{launch_line}:'


def _synthetic(source):
    return _bash_resolver_scan._synthetic_violations(source)


def test_every_launch_names_the_shared_resolver(tmp):
    del tmp
    violations = _bash_resolver_scan._tree_violations(ROOT)
    assert not violations, '\n'.join(violations)


def test_the_scan_reads_exactly_the_tracked_test_tree(tmp):
    """A module Git does not track is not a module the scan reads."""
    del tmp
    tracked = _tracked_test_modules()
    assert tracked, 'Git returned no tracked test modules'
    scanned = {path.relative_to(ROOT).as_posix()
               for path in _bash_resolver_scan._test_modules(ROOT)}
    assert scanned == tracked, sorted(scanned ^ tracked)


def test_a_write_through_a_container_binds_no_name(tmp):
    """A subscript or attribute target is not a binding of its base name."""
    del tmp
    for statement in ("conf['bash'] = 'bash'", 'conf.bash = "bash"',
                      "conf['bash'] = conf.extra = 'bash'"):
        names, _value = _bash_resolver_scan._binding_of(
            ast.parse(statement).body[0])
        assert names == [], statement


def test_a_literal_shell_argv_is_a_violation(tmp):
    del tmp
    violations = _synthetic("""import subprocess
subprocess.run(['bash', '-c', 'exec "$@"', 'bash', 'child.py'], cwd=tmp)
""")
    assert violations == [
        "tests/synthetic.py:2: run resolves the workflow shell as 'bash', "
        'not workflow_bash()'], violations


def test_a_shell_name_bound_to_a_literal_is_a_violation(tmp):
    """The shape a call site takes when it stops calling the resolver."""
    del tmp
    violations = _synthetic("""import subprocess
bash = 'bash'
subprocess.run([bash, '-c', 'exec "$@"', 'bash', 'child.py'], cwd=tmp)
""")
    assert violations == [
        "tests/synthetic.py:3: run resolves the workflow shell as 'bash', "
        'not workflow_bash()'], violations


def test_a_shell_spelled_as_a_path_is_a_violation(tmp):
    """An absolute or Windows spelling names the same executable."""
    del tmp
    for spelling in ('/usr/bin/bash', 'C:\\Git\\bin\\bash.exe'):
        escaped = spelling.replace('\\', '\\\\')
        violations = _synthetic(f"""import subprocess
program = '{escaped}'
subprocess.run([program, '-c', 'true'], cwd=tmp)
""")
        assert len(violations) == 1, (spelling, violations)
        assert f'as {spelling!r}' in violations[0], (spelling, violations)


def test_a_which_of_the_shell_is_a_violation(tmp):
    del tmp
    inline = _synthetic("""import shutil
import subprocess
subprocess.run([shutil.which('bash'), '-c', 'true'], cwd=tmp)
""")
    assert inline == [
        "tests/synthetic.py:3: run resolves the workflow shell as "
        "shutil.which('bash'), not workflow_bash()"], inline
    bound = _synthetic("""import shutil
import subprocess
bash = shutil.which('bash')
subprocess.run([bash, '-c', 'true'], cwd=tmp)
""")
    assert bound == [
        "tests/synthetic.py:4: run resolves the workflow shell as "
        "shutil.which('bash'), not workflow_bash()"], bound


def test_a_launch_alias_is_followed(tmp):
    del tmp
    violations = _synthetic("""import subprocess
from subprocess import run
run(['bash', '-c', 'true'], cwd=tmp)
""")
    assert len(violations) == 1, violations
    assert 'tests/synthetic.py:3' in violations[0], violations


def test_an_argv_spelled_as_a_keyword_is_a_violation(tmp):
    del tmp
    violations = _synthetic("""import subprocess
subprocess.run(args=['bash', '-c', 'true'], cwd=tmp)
""")
    assert len(violations) == 1, violations
    assert "as 'bash'" in violations[0], violations


def test_a_shell_command_string_is_a_violation(tmp):
    """Under `shell=True` the command string's own program is the element."""
    del tmp
    violations = _synthetic("""import subprocess
subprocess.run('bash -c true', shell=True, cwd=tmp)
""")
    assert violations == [
        "tests/synthetic.py:2: run resolves the workflow shell as "
        "'bash -c true', not workflow_bash()"], violations
    bound = _synthetic("""import subprocess
command = 'bash -c true'
subprocess.run(command, shell=True, cwd=tmp)
""")
    assert len(bound) == 1, bound
    assert "as 'bash -c true'" in bound[0], bound


def test_a_quoted_leading_word_is_a_violation(tmp):
    """The shell still runs bash when the leading word carries quotes."""
    del tmp
    violations = _synthetic("""import subprocess
subprocess.run('"bash" -c true', shell=True, cwd=tmp)
""")
    assert violations == [
        "tests/synthetic.py:2: run resolves the workflow shell as "
        """'"bash" -c true', not workflow_bash()"""], violations
    single = _synthetic("""import subprocess
subprocess.run("'bash' -c true", shell=True, cwd=tmp)
""")
    assert len(single) == 1, single
    assert "as \"'bash' -c true\"" in single[0], single


def test_a_quoted_non_shell_leading_word_is_clean(tmp):
    """Quotes change the spelling, never the leading word's meaning."""
    del tmp
    for command in ('"python3" -c true', "'python3' -c true"):
        escaped = command.replace("'", "\\'")
        assert _synthetic(f"""import subprocess
subprocess.run('{escaped}', shell=True, cwd=tmp)
""") == [], command


def test_a_command_string_without_the_flag_is_not_a_program(tmp):
    """Only `shell=True` makes the string a command rather than an argv."""
    del tmp
    assert _synthetic("""import subprocess
subprocess.run('bash -c true', cwd=tmp)
""") == []
    assert _synthetic("""import subprocess
subprocess.run('python -c true', shell=True, cwd=tmp)
""") == []


def test_a_rebound_name_still_reports_the_bypass(tmp):
    del tmp
    violations = _synthetic("""import subprocess
import _util
bash = _util.workflow_bash()
bash = 'bash'
subprocess.run([bash, '-c', 'true'], cwd=tmp)
""")
    assert len(violations) == 1, violations
    assert "as 'bash'" in violations[0], violations


def test_the_resolver_spelling_is_clean(tmp):
    """Inline, imported, once-bound, and closure-bound resolver calls."""
    del tmp
    assert _synthetic("""import subprocess
import _util
subprocess.run([_util.workflow_bash(), '-c', 'true'], cwd=tmp)
""") == []
    assert _synthetic("""import subprocess
from _util import workflow_bash
subprocess.run([workflow_bash(), '-c', 'true'], cwd=tmp)
""") == []
    assert _synthetic("""import subprocess
import _util
bash = _util.workflow_bash()
subprocess.run([bash, '-c', 'true'], cwd=tmp)
""") == []


def test_a_resolver_call_is_classified_as_the_resolver(tmp):
    """Recognition itself, not only the cleanliness it buys.

    An unrecognised resolver site is unjudged, which reads as clean in the
    violation list, so the classification is what has to be watched here.
    """
    del tmp
    for source in ('import _util\n_util.workflow_bash()\n',
                   'from _util import workflow_bash\nworkflow_bash()\n'):
        tree = ast.parse(source)
        facts = _bash_resolver_scan._ModuleFacts(tree)
        call = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.Call))
        assert _bash_resolver_scan._route(call, facts) == 'resolver', source


def test_a_closure_bound_resolver_name_is_clean(tmp):
    del tmp
    assert _synthetic("""import subprocess
import _util
def outer(tmp):
    bash = _util.workflow_bash()
    def inner():
        subprocess.run([bash, '-c', 'true'], cwd=tmp)
    return inner
""") == []


def test_a_lambda_parameter_is_not_an_outer_binding(tmp):
    """A lambda's parameters bind its own scope, the way a def's do."""
    del tmp
    assert _synthetic("""import subprocess
bash = 'bash'
launch = lambda bash: subprocess.run([bash, '-c', 'true'], cwd=tmp)
""") == []
    outer = _synthetic("""import subprocess
program = 'bash'
launch = lambda tmp: subprocess.run([program, '-c', 'true'], cwd=tmp)
""")
    assert len(outer) == 1, outer


def test_a_parameter_shadows_an_outer_binding(tmp):
    """A launch through a parameter is out of scope, not resolved outward.

    That attribution is `_coverage_scopes._scope_shadows`'s, so this pin also
    watches the helper this scan's verdicts stand on.
    """
    del tmp
    assert _synthetic("""import subprocess
bash = 'bash'
def launch(bash):
    subprocess.run([bash, '-c', 'true'], cwd=tmp)
""") == []


def test_a_which_of_another_program_is_clean(tmp):
    """Node resolution is not a Bash route, wherever it is bound."""
    del tmp
    assert _synthetic("""import shutil
import subprocess
node = shutil.which('node')
subprocess.run([node, 'child.js'], cwd=tmp)
""") == []


def test_a_which_used_as_a_comparison_value_is_clean(tmp):
    """A resolution that only feeds an assertion reaches no launch."""
    del tmp
    assert _synthetic("""import shutil
resolved = shutil.which('bash')
assert resolved and len(resolved) > 1, resolved
""") == []


def test_a_non_literal_which_argument_is_not_judged(tmp):
    """Resolving whatever a workflow shell template names is not a route."""
    del tmp
    assert _synthetic("""import shutil
import subprocess
def run_step(program):
    executable = shutil.which(program)
    command = [executable, 'script']
    subprocess.run(command, cwd=tmp)
""") == []


def test_an_unreadable_program_element_is_not_judged(tmp):
    """The interpreter, a parameter, and a computed shell are outside it."""
    del tmp
    assert _synthetic("""import subprocess
import sys
subprocess.run([sys.executable, 'child.py'], cwd=tmp)
""") == []
    assert _synthetic("""import subprocess
def launch(program):
    subprocess.run([program, 'child.py'], cwd=tmp)
""") == []


def test_a_launch_inside_a_string_is_not_a_launch(tmp):
    """The scan reads Python, so a fixture string is not a call site."""
    del tmp
    assert _synthetic("""import subprocess
FIXTURE = \"\"\"import subprocess
subprocess.run(['bash', '-c', 'true'], cwd=tmp)
\"\"\"
""") == []


def test_each_real_site_is_caught_when_it_bypasses(tmp):
    for site in _SITES:
        root, expected = _mutated_copy(tmp, site)
        violations = _bash_resolver_scan._tree_violations(root)
        assert any(v.startswith(expected) for v in violations), (
            site[0], expected, violations)


def test_a_copied_tree_is_clean_before_a_mutation_is_planted(tmp):
    violations = _bash_resolver_scan._tree_violations(_tree_copy(tmp))
    assert not violations, violations


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
