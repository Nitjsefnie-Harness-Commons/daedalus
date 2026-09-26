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
import fnmatch
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
def _is_the_tests_directory(node):
    """Whether this expression IS the tests directory, read as a node.

    A BinOp `a / "tests"` counts whatever the segment is. A bare Name or
    attribute does not: `old_tests` is a different directory that happens
    to end in the same five letters, and the unparsed-source test this
    replaces called it a match.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_the_tests_directory(node.right)
    return (isinstance(node, ast.Constant) and node.value == 'tests')


def _is_a_runnable_suite_file(node):
    """Whether this string names a file under tests/ THAT CAN BE RUN.

    A directory (`tests/`), a listing flag (`-- tests/`) and an
    unrelated path are not a launch. Reading them as one put
    `plan-matrix` in the policed set for a `git ls-files` LISTING, in a
    set whose stated reason was that the rule recognises a launch.
    """
    if not (isinstance(node, ast.Constant)
            and isinstance(node.value, str)):
        return False
    path = node.value.replace(chr(92) * 2, '/')
    return (path.startswith('tests/')
            and (fnmatch.fnmatch(path, 'tests/run_tests.py')
                 or fnmatch.fnmatch(path, 'tests/test_*.py')))


def _is_a_launch_call(node):
    """A parsed callee naming a process launcher, not a source match.

    "run" inside a callee name is not a launch: a method called `run` on
    some other object, and a path that merely contains the letters, must
    not match — which is why the callee is read as a node here.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in ('run', 'Popen', 'call', 'check_call',
                         'check_output'):
        return False
    return isinstance(func.value, ast.Name) and func.value.id in (
        'subprocess', 'sp')


def _launches_a_tests_file(source):
    """Whether this script LAUNCHES a runnable file under tests/.

    ADMITTED, each measured against this predicate:
      * `subprocess.run([sys.executable, "tests/test_x.py"])`, and the
        same under `Popen`, `call`, `check_call` and `check_output`,
        written as `subprocess.<name>` or as an `import subprocess as
        sp` alias;
      * an argument list that REACHES a suite path indirectly, so
        `subprocess.run([..., glob.glob("tests/test_x.py")[0]])` counts
        — the walk descends into the expression.

    DELIBERATELY NOT ADMITTED, each measured as a miss rather than
    assumed:
      * `os.system`, `os.popen` and `os.execv`: a `system` call carries
        a shell string, and reading it as an argv would be a guess;
      * `subprocess.run(args=[...])` with no positional argument — the
        walk reads `node.args[0]`, and the `args=` keyword is not read
        at all;
      * `from subprocess import run`, and a callee on any other
        owner, because the owner is a Name equal to `subprocess` or
        `sp` and nothing else;
      * a command assembled in an f-string, which is a string only at
        run time.
    """
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and _is_a_launch_call(node)):
            continue
        if not node.args:
            continue
        for inner in ast.walk(node.args[0]):
            if _is_a_runnable_suite_file(inner):
                return True
    return False


def _enumerates_the_tests_tree(source):
    """Whether this script ENUMERATES the tests tree itself.

    A `.glob(...)`/`.rglob(...)` call whose RECEIVER is the tests
    directory — so `(ROOT / "tests").glob("test_*.py")` and
    `(tree / 'tests').glob(...)` both count, and a sibling directory
    that merely CONTAINS the word (`old_tests`) does not. The receiver is
    the PARSED node, not its source text: the unparsed form matched
    `old_tests` and every other name with the letters in it, which is
    the substring match this docstring argues against, one level down.

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
                and _is_the_tests_directory(node.func.value)):
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
            if _enumerates_the_tests_tree(source)
            or _launches_a_tests_file(source)}


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
      * (f) A `tests/` LITERAL LAUNCH *the rule this replaced never
        caught either*: measured against the pre-wave predicate, a
        literal `subprocess.run([py, "tests/test_x.py"])` is `False`
        under both the old and the new rule, because the old one looked
        only at `.glob(...)` calls. The arm exists for that gap, not
        because the old rule lost the shape.
      * (g) A WORKFLOW FILE OTHER THAN tests.yml. The guard reads
        `tests.yml`, so every other workflow file is invisible to it --
        `release.yml`, which runs `run_tests.py` from a detached tag, and
        `timed-timings.yml`, whose two suite steps at depth 1 bullet
        (a) names.
      * (h) A TRANSITIVE WRAPPER, in either direction: a job that
        reaches a runner through a script which itself reaches a runner.
        This is the route that produced the tag-build incident, and the
        rule has no transitive arm in either direction.

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


def test_the_glob_receiver_is_read_as_a_node_not_as_source_text(tmp):
    """`old_tests` is a different directory that ends in the same five
    letters, and the unparsed-source predicate this replaces called it a
    match.
    """
    del tmp
    for source, expected in (
            ('out = (ROOT / "tests").glob("test_*.py")', True),
            ("out = (tree / 'tests').glob('test_*.py')", True),
            ('out = (ROOT / "old_tests").glob("test_*.py")', False),
            ('out = (ROOT / "dashboard").glob("test_*.py")', False)):
        assert _enumerates_the_tests_tree(source) is expected, source
    # A callee whose NAME merely contains a launch word, with an
    # argument that does not name a suite: the source-text callee test
    # this replaced counted it.
    assert not _launches_a_tests_file(
        'import subprocess\nsubprocess.runner(["python3", "docs/x.py"])\n')
    # A call reached through a nested expression still counts, because
    # the walk descends into the argument.
    assert _launches_a_tests_file(
        'import glob, subprocess, sys\n'
        'subprocess.run([sys.executable,'
        ' glob.glob("tests/test_x.py")[0]])\n')


def test_the_runnerhood_rule_finds_every_runner_in_the_set(tmp):
    """Each member of the policed set, pinned — including the ones no
    hand-written fixture stands for.

    An earlier version of this test named three runners and asserted
    them, which left a fourth (`plan_timed_matrix.py`) in the set with
    nothing holding it: changing that script's shape would have dropped
    `plan-matrix` with the guard green. Every member is named here now.

    One thing this test does NOT show, because it is true: removing the
    literal-launch arm leaves the set unchanged. All three runners glob,
    so the arm currently contributes NO member — it is kept for the
    route it is meant to cover, a workflow step that runs a suite
    directly (`timed-timings.yml`, bullet (a)), and not for a member of
    today's set.
    """
    del tmp
    runners = _suite_runners(_tracked_scripts(ROOT))
    for runner in ('run_tests.py', 'scripts/ci/coverage_suites.py',
                   'scripts/ci/time_tests.py'):
        assert runner in runners, (
            f'{runner} no longer enumerates the tests tree or launches a '
            'suite file, so the guard drops the job that runs it. The rule '
            'is a shape now, so this is a change to what the tree does, '
            'not a rename.')
    # The listing that is NOT a launch, and the launch that is.
    assert not _launches_a_tests_file(
        "import subprocess\n"
        "subprocess.run(['git', '-C', str(t), 'ls-files', '-z', '--',"
        " 'tests/'])\n")
    assert _launches_a_tests_file(
        'import subprocess, sys\n'
        'subprocess.run([sys.executable, "tests/test_x.py"])\n')


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
