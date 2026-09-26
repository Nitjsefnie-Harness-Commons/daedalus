#!/usr/bin/env python3
"""Every job this guard identifies must be able to read the merge base.

The branch-boundary controls in `test_helper_reimplementation.py` read the
merge base's own declarations, and a checkout that resolves neither
`origin/main` nor a local `main` makes both boundary tests take their
refusal arm. That is not a red gate in every job — `timed` records
durations and does not fail on a failing suite — so the consequence is
quieter and worse: the branch's central guarantee goes unevaluated in a
job that runs it, and the two suites drop out of the measured durations.

The job set is DERIVED. A tracked script is a suite runner when, as an
AST fact, it launches a suite; a job runs the suites when one of its
steps invokes one. That replaced a hand-written job list which missed
the `timed` matrix, and the derivation found all four jobs on the first
run where the list had two.
"""
import ast
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import _job_names, _tests_yml  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402


# Which jobs run the suites is DERIVED, not written here. A job is in the
# set when one of its steps invokes a tracked script that reaches a suite:
# either it spawns one, or it invokes a script that does. Deriving it is
# the whole point — the hand list this replaces missed the `timed`
# matrix, which runs slices of tests/ and therefore ran both boundary
# controls in a checkout where neither `origin/main` nor a local `main`
# resolved, so both took their refusal arm and the property every
# allowance row rests on was never evaluated there.
#
_SUITE_LAUNCH_CALLS = frozenset({
    'subprocess.run', 'subprocess.Popen', 'subprocess.check_output',
    'subprocess.check_call'})


def _launches_a_suite(source):
    """Whether this script, as an AST, launches a suite.

    A `subprocess` call whose first argument names a path under `tests/`
    or is the suite it was handed. It is an AST fact and not a substring
    because the substring version of this question matched a docstring
    and put two non-runners in the set while missing the one that
    mattered — the same mention-versus-call mistake the re-implementation
    control's `__main__` rule had, in a different control.

    The set is over-inclusive by design in the SAFE direction: the
    planner launches nothing but is included, which costs one workflow
    line, where under-inclusion is the hole this guard exists to close.

    This rule is still partly a SPELLING — the three runners that
    delegate their suite enumeration are recognised by a variable NAME,
    and no shape in this tree separates them from the CI scripts that
    read the tests tree directly, because those ENUMERATE while the
    runners are handed a computed path. What that costs, the rename that
    reaches it, and why it survives are written once, in
    `test_the_runnerhood_rule_still_recognises_the_instrument`.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if ast.unparse(node.func) not in _SUITE_LAUNCH_CALLS:
            continue
        for argument in node.args[:1]:
            for inner in ast.walk(argument):
                if isinstance(inner, ast.Constant) \
                        and isinstance(inner.value, str) \
                        and inner.value.replace('\\', '/').startswith(
                            'tests/'):
                    return True
                if isinstance(inner, ast.Name) and inner.id in (
                        'suite', 'suites'):
                    return True
    return False


def _tracked_scripts(root):
    """{repo-relative path: source} for the tracked entry-point scripts."""
    listed = subprocess.run(
        ['git', 'ls-files', 'run_tests.py', 'scripts/ci/*.py'], cwd=root,
        capture_output=True, text=True, check=True,
        env=_util.child_coverage('scrub')).stdout.split()
    return {name: (root / name).read_text(encoding='utf-8')
            for name in listed}


def _suite_runners(scripts):
    """The tracked scripts that launch a suite."""
    return {name for name, source in scripts.items()
            if _launches_a_suite(source)}


def _invoked_scripts(command, runners):
    """Which of `runners` this shell command invokes, by its own name."""
    return {name for name in runners if name in command}


def _jobs_running_suites(workflow, runners):
    """{job: {the runners it invokes}} over the workflow's own steps."""
    found = {}
    for job in _job_names(workflow):
        steps = (complete_job_mapping(workflow, job) or {}).get('steps') or []
        for step in steps:
            if not step:
                continue
            invoked = _invoked_scripts(str(step.get('run', '')), runners)
            if invoked:
                found.setdefault(job, set()).update(invoked)
    return found


def _checkout_widths(job):
    """Every `fetch-depth` the job's checkout steps ask for."""
    widths = []
    for step in (complete_job_mapping(_tests_yml(), job) or {}).get(
            'steps') or []:
        if not step or 'actions/checkout@' not in str(step.get('uses', '')):
            continue
        inputs = step.get('with') or {}
        widths.append(str(inputs.get('fetch-depth', '')) or None)
    return widths


def test_each_job_this_guard_identifies_can_read_the_merge_base(tmp):
    """Each job THIS GUARD IDENTIFIES must fetch the merge base.

    The branch-boundary controls read the merge base's own declarations,
    and a checkout that resolves neither `origin/main` nor a local `main`
    makes both boundary tests take their refusal arm. That is not a red
    gate in every job — `timed` records durations and does not fail on a
    failing suite — so the consequence is quieter and worse: the branch's
    central guarantee goes unevaluated in a job that runs it, and the
    two suites drop out of the measured durations.

    WHAT THIS DOES NOT COVER, and the test is named for what it proves
    rather than for an enumeration it cannot deliver. A named bypass is a
    disclosure; an unnamed one is a hole, and a test called "every" with
    a docstring saying "every" is the next contributor's licence to add a
    route without noticing. These are the routes it does NOT follow, each
    found by the review that sent this wave back:

      * a job that runs a suite through a TRANSITIVE WRAPPER — a script
        that neither spawns a suite nor invokes a runner this walk sees;
      * the timed MATRIX ENTRYPOINT, where the suites come from the
        planner's cells rather than from a command line;
      * a SHELL WRAPPER, where a `.sh` step runs a suite and no tracked
        Python script is named at all;
      * `os.system`, `subprocess.call` and any launch this shape does
        not read, in this or any other workflow file;
      * `release.yml`, which runs `run_tests.py` from a detached tag and
        is a different file: it is fixed in the workflow, not by this
        guard, and a workflow added beside it inherits nothing.

    Every one of those needs the WORKFLOW edited. The failing check is
    the mitigation for the routes this guard can see; it is not a claim
    about the rest.
    """
    del tmp
    scripts = _tracked_scripts(ROOT)
    runners = _suite_runners(scripts)
    assert runners, 'no tracked script reaches a suite, so this guard is blind'
    jobs = _jobs_running_suites(_tests_yml(), runners)
    assert jobs, (
        'no job in the workflow invokes a suite runner, so the suite set '
        'this guard reads is empty')
    unreadable = {job: widths for job, widths in jobs.items()
                  if any(width != '0' for width in _checkout_widths(job))}
    assert not unreadable, (
        'these jobs run a suite and so run the branch-boundary controls, '
        'which cannot be evaluated without the merge base; give every '
        f'checkout step in them fetch-depth: 0 (found {unreadable})')


def test_the_runnerhood_rule_still_recognises_the_instrument(tmp):
    """The spelling the rule depends on, reported when it moves.

    The rule is still a SPELLING, and this is the statement of what that
    costs. The three runners that delegate their suite enumeration are
    recognised because the path they launch is built from a variable the
    predicate looks for BY NAME — no shape in this tree separates them
    from the CI scripts that read the tests tree directly, because those
    ENUMERATE while the runners are handed a computed path, and a rule
    keyed on the enumeration catches only the former.

    So `sed -i 's/\bsuite\b/suitepath/g' scripts/ci/time_tests.py` drops
    `timed` from the set this guard polices with everything else green —
    the original failure reached by a spelling. This test is what makes
    that a red rather than a silence, and its message names both halves
    of the choice: widen the rule, or say which route is no longer
    policed.
    """
    scripts = _tracked_scripts(ROOT)
    runners = _suite_runners(scripts)
    for runner in ('run_tests.py', 'scripts/ci/coverage_suites.py',
                   'scripts/ci/time_tests.py'):
        assert runner in runners, (
            f'{runner} no longer launches a suite this rule recognises; the '
            'rule keys on a variable name, so a rename of it drops the '
            'job from the guard. Widen the rule, or say in the guard which '
            'route is no longer policed')
    assert 'scripts/ci/time_tests.py' in runners


def test_the_runnerhood_rule_is_not_a_substring_match(tmp):
    """The rule reads the AST, and over-includes in the safe direction.

    The planner launches nothing and is in the set; two CI scripts whose
    DOCSTRINGS name runners are not. A substring version of this question
    put the two in the set and missed `coverage_suites`, so the shape and
    the direction are both pinned.
    """
    del tmp
    scripts = _tracked_scripts(ROOT)
    runners = _suite_runners(scripts)
    for planner_only in ('scripts/ci/compare_durations.py',
                         'scripts/ci/gate_freshness.py'):
        assert planner_only not in runners, (
            f'{planner_only} is in the runner set and launches no suite')
    assert 'scripts/ci/coverage_suites.py' in runners, (
        'coverage_suites reaches the suites by invoking run_tests; a rule '
        'that misses it misses the job whose fetch-depth matters most')


def main():
    return _util.runner(_util.collect(globals()))


if __name__ == '__main__':
    sys.exit(main())
