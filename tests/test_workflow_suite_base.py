#!/usr/bin/env python3
"""Every job this guard identifies must be able to read the merge base.

The branch-boundary control a retired reserved-name suite carried read
the merge base's own declarations over both allowance tables, and a
checkout that resolves neither `origin/main` nor a local `main` made
it take its refusal arm. In a job that turns a failing suite red, the
consequence is already visible; in a job that runs the suites without
failing on one it is quieter and worse — the branch's central guarantee
goes unevaluated in a job that runs it, and nothing reports that.

The job set is DERIVED. A tracked script is a suite runner when, as an
AST fact, it launches a suite; a job runs the suites when one of its
steps invokes one. That replaced a hand-written job list, which was a
second statement of the same fact and could drift from the first.
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
# the tests tree. Deriving it is the whole point — a hand list is a
# second statement of the same fact, and a matrix leg added beside the
# jobs it replaces would leave both boundary controls unevaluated there
# without any of this noticing.
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
    unrelated path are not a launch. Reading them as one put a job that
    ran `git ls-files -- tests/` into the policed set, in a set whose
    stated reason was that the rule recognises a launch.
    """
    if not (isinstance(node, ast.Constant)
            and isinstance(node.value, str)):
        return False
    path = node.value.replace(chr(92) * 2, '/')
    return (path.startswith('tests/')
            and (fnmatch.fnmatch(path, 'tests/run_tests.py')
                 or fnmatch.fnmatch(path, 'tests/test_*.py')))


# The five names the retired resolver-bypass guard declared, plus the
# two its corpus entry named as ITS bypass: that guard enumerated those
# five and `subprocess.getoutput` slipped past it, so an arm repeating
# the same five would re-open a bypass this repository has already
# recorded. Measured on this arm: `getoutput(["python3",
# "tests/test_x.py"])` is `True` and the string form
# `getoutput("python3 tests/test_x.py")` is `False` — the path is inside
# the string, which is the declined class named below, not a claim that
# the name is absent.
_LAUNCHER_NAMES = frozenset({
    'run', 'Popen', 'call', 'check_call', 'check_output',
    'getoutput', 'getstatusoutput'})


def _is_a_launch_call(node):
    """A parsed callee naming a process launcher, not a source match.

    "run" inside a callee name is not a launch: a method called `run` on
    some other object, and a path that merely contains the letters, must
    not match — which is why the callee is read as a node here.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in _LAUNCHER_NAMES:
        return False
    return isinstance(func.value, ast.Name) and func.value.id in (
        'subprocess', 'sp')


def _launches_a_tests_file(source):
    """Whether this script LAUNCHES a runnable file under tests/.

    RECOGNISES, each measured against this predicate:
      * `subprocess.run`, `Popen`, `call`, `check_call`, `check_output`,
        `getoutput` and `getstatusoutput`, written as `subprocess.<name>`
        or as an `import subprocess as sp` alias, with the suite path
        given as an argument — directly, or reached indirectly, since the
        walk descends into the argument expression;
      * and NOTHING ELSE. Every other shape is not policed: anything
        reached through an import, any launcher outside those seven
        names, and any suite path not written as an argument.
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

    Two of the twenty-five tracked scripts enumerate the tests tree, and
    they are the two that run suites: `run_tests.py` and
    `scripts/ci/coverage_suites.py`. They write the identical expression,
    which is exactly what a SHAPE rule reads and a name rule does not.
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

    The branch-boundary controls the retired reserved-name suite
    carried read the merge base's own declarations, and a checkout
    that resolved neither `origin/main` nor a local `main` made both
    boundary tests take their refusal arm. In a job that turned a
    failing suite red that was already visible; in a job that ran the
    suites without failing on one it was quieter and worse — the
    branch's central guarantee went unevaluated in a job that ran it,
    and nothing reported that.

    WHAT THIS DOES NOT COVER, and the test is named for what it proves
    rather than for an enumeration it cannot deliver. A named bypass is a
    disclosure; an unnamed one is a hole, and a test called "every" with
    a docstring saying "every" is the next contributor's licence to add a
    route without noticing. These are the routes it does NOT follow, each
    found by the review that sent this wave back:

      * (a) A JOB THAT NAMES A SUITE DIRECTLY, which no bullet below
        reaches: a step carrying a fixed list of suite paths runs them
        whatever the tree grows to. `tests/test_ci_lint_tools.py`'s
        `SUITE_DOORS` is where such a door is declared, and it is empty.
      * (b) A `tests/` DIRECTORY LISTING filtered at run time, which
        neither arm can see: `git ls-files -- tests/` names every entry
        the directory holds, and the filter that narrows it to runnable
        suites lives beside the listing rather than in the predicate.

    Every one of those needs the WORKFLOW edited, and (b) needs this
    reader widened. The failing check is the mitigation for the routes it
    can see; it is not a claim about the rest.
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
        'these jobs run a suite and must fetch the merge base; give '
        'every checkout step in them fetch-depth: 0 '
        f'(found {unreadable})')


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
    # The two names added because the guard this arm shadows declares
    # only five; measured on the list form, which is the only form that
    # can carry a suite path as an argument.
    assert _launches_a_tests_file(
        'import subprocess\n'
        'subprocess.getoutput(["python3", "tests/test_x.py"])\n')
    assert _launches_a_tests_file(
        'import subprocess\n'
        'subprocess.getstatusoutput(["python3", "tests/test_x.py"])\n')
    # The shape that leaves BOTH arms: a `tests/` DIRECTORY listing
    # filtered at run time reaches no predicate here.
    assert not _launches_a_tests_file(
        "import subprocess\n"
        "subprocess.run(['git', 'ls-files', '--', 'tests/'])\n")
    # A call reached through a nested expression still counts, because
    # the walk descends into the argument.
    assert _launches_a_tests_file(
        'import glob, subprocess, sys\n'
        'subprocess.run([sys.executable,'
        ' glob.glob("tests/test_x.py")[0]])\n')


def test_the_runnerhood_rule_finds_every_runner_in_the_set(tmp):
    """Each member of the policed set, pinned — including the ones no
    hand-written fixture stands for.

    An earlier version named three runners and left a fourth in the set
    with nothing holding it, so a change to that script would have
    dropped its job with the guard green. The members are named.

    One thing this test does NOT show, because it is true: removing the
    literal-launch arm leaves the set unchanged. Both runners glob, so
    the arm currently contributes NO member — it is kept for the route it
    is meant to cover, a workflow step that runs a suite directly
    (bullet (a)), and not for a member of today's set.
    """
    del tmp
    runners = _suite_runners(_tracked_scripts(ROOT))
    for runner in ('run_tests.py', 'scripts/ci/coverage_suites.py'):
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
    `coverage_suites` — the job whose fetch-depth matters most.
    """
    del tmp
    runners = _suite_runners(_tracked_scripts(ROOT))
    for planner_only in ('scripts/ci/diff_coverage.py',
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
