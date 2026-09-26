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


# Which jobs run the suites is DERIVED, not written here: a job is in
# the set when one of its steps invokes a tracked script that enumerates
# the tests tree. Deriving it is the whole point — the hand list this
# replaces missed the `timed` matrix, which runs slices of tests/ and so
# ran both boundary controls where neither `origin/main` nor a local
# `main` resolved.
#
def _enumerates_the_tests_tree(source):
    """Whether this script ENUMERATES the tests tree itself.

    A `.glob(...)`/`.rglob(...)` call whose RECEIVER names `tests` — so
    `(ROOT / "tests").glob("test_*.py")` and `(tree / 'tests').glob(...)`
    both count, and the argument's spelling does not matter.

    It is an AST and not a substring because the substring version of
    this question matched a docstring: it put two non-runners in the set
    and MISSED `coverage_suites`, which is the job whose fetch-depth
    matters most. Same mention-versus-call mistake the
    re-implementation control's `__main__` rule had, in a second
    control, and it is why the rule reads the tree.

    Three of the twenty-eight tracked scripts enumerate the tests tree,
    and they are the three that run suites: `run_tests.py`,
    `scripts/ci/coverage_suites.py` and `scripts/ci/time_tests.py`. The
    first two write the identical expression; the third spells its
    receiver differently, which is exactly what a SHAPE rule tolerates
    and a name rule does not.
    """
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ('glob', 'rglob')
                and 'tests' in ast.unparse(node.func.value)):
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
            if _enumerates_the_tests_tree(source)}


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

      * (a) A JOB THAT NAMES A SUITE DIRECTLY. LIVE IN THE TREE TODAY:
        `timed-timings.yml:300-301` runs
        `python3 tests/test_timed_planner.py` and
        `python3 tests/test_timed_refresh.py` at depth 1, and no bullet
        here reaches it — not a wrapper, not the matrix entrypoint, not
        a shell wrapper. Adding this branch's boundary suite to that step
        breaks the property with this guard green. Latent, not a live
        violation: nothing reads the base there today.
      * (b) A CHECKOUT SHAPE this rule cannot see. Replacing `suites`'s
        `actions/checkout` with `uses: ./.github/actions/checkout` gives
        no width at all, and the check is on the WIDTH — see the empty
        list in the assertion below.
      * (c) THE SCRIPT UNIVERSE IS TWO GLOBS. `_tracked_scripts` reads
        `run_tests.py` and `scripts/ci/*.py` — 28 of the repository's
        tracked files — and this module's docstring used to say "a
        tracked script" as though that were all of them. A suite
        launched by any other tracked script is invisible.
      * (d) JOB-LEVEL `uses:` DELEGATION. A job that delegates to
        `uses: ./.github/workflows/…` has no `steps` to read.
      * (e) JOB-SIDE LITERAL DEPENDENCY. `_invoked_scripts` needs the
        tracked filename as a literal substring of the `run:` text, so
        `run: python "$SUITE_RUNNER"` drops the job silently.

    `timed` IS in the set, matched by the path literal
    `scripts/ci/time_tests.py` in the step's `run:` — the earlier
    disclosure listed the matrix entrypoint as uncovered, which
    under-claimed, and being told less than is true is the safe
    direction but still wrong.

    Every one of those needs the WORKFLOW edited, and (c) and (e) need
    this reader widened. The failing check is the mitigation for the
    routes it can see; it is not a claim about the rest.
    """
    del tmp
    scripts = _tracked_scripts(ROOT)
    runners = _suite_runners(scripts)
    assert runners, 'no tracked script reaches a suite, so this guard is blind'
    jobs = _jobs_running_suites(_tests_yml(), runners)
    assert jobs, (
        'no job in the workflow invokes a suite runner, so the suite set '
        'this guard reads is empty')
    unreadable = {
        job: _checkout_widths(job) for job in jobs
        # `not widths` matters: a job with no `actions/checkout` step at
        # all has nothing this rule can read, and `any(...)` over the
        # empty list is False, which would read that as "every checkout
        # asked for 0" and pass.
        if not _checkout_widths(job) or any(
            width != '0' for width in _checkout_widths(job))}
    assert not unreadable, (
        'these jobs run a suite and so run the branch-boundary controls, '
        'which cannot be evaluated without the merge base; give every '
        f'checkout step in them fetch-depth: 0 (found {unreadable})')


def test_the_runnerhood_rule_finds_all_three_runners(tmp):
    """The shape the rule keys on, pinned on the three that matter.

    A name-keyed rule recognised two of these three only because a loop
    variable happened to be called `suite`, and the reviewer's
    `sed -i 's/\bsuite\b/suitepath/g' scripts/ci/time_tests.py` reached
    that. The rule keys on ENUMERATING THE TESTS TREE instead, and this
    is the statement of why: `run_tests.py` and `coverage_suites.py`
    write the identical expression, and `time_tests.py` spells its
    receiver differently — which a shape tolerates and a name does not.
    """
    del tmp
    runners = _suite_runners(_tracked_scripts(ROOT))
    for runner in ('run_tests.py', 'scripts/ci/coverage_suites.py',
                   'scripts/ci/time_tests.py'):
        assert runner in runners, (
            f'{runner} no longer enumerates the tests tree, so the guard '
            'drops the job that runs it from the policed set. The rule is '
            'a shape now, so this is a change to what the tree does, not '
            'a rename.')


def test_the_runnerhood_rule_is_not_a_substring_match(tmp):
    """The rule reads the AST, and over-includes in the safe direction.

    A substring version of this question put two CI scripts whose
    DOCSTRINGS name runners into the set and missed
    `coverage_suites` — the job whose fetch-depth matters most. The
    planner enumerates the tests tree too and is included, which costs
    one workflow line, where under-inclusion is the hole.
    """
    del tmp
    runners = _suite_runners(_tracked_scripts(ROOT))
    for planner_only in ('scripts/ci/compare_durations.py',
                         'scripts/ci/gate_freshness.py'):
        assert planner_only not in runners, (
            f'{planner_only} is in the runner set and enumerates no tests '
            'tree; the rule has grown past what it claims')
    assert 'scripts/ci/coverage_suites.py' in runners, (
        'coverage_suites reaches the suites through run_tests; a rule '
        'that misses it misses the job whose fetch-depth matters most')


def main():
    return _util.runner(_util.collect(globals()))


if __name__ == '__main__':
    sys.exit(main())
