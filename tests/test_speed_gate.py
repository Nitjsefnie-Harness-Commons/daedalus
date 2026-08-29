#!/usr/bin/env python3
"""The speed gate's shape: a correctness gate, a suite partition, one verdict.

The pins here hold three things together across two files: the wait names the
tests workflow's aggregate check, the matrix cells partition the suites, and
the final job keeps the check name. Other invariants over speed.yml stay in
`test_ci_workflows.py`.
"""
import fnmatch
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import _job_section, _tests_yml  # noqa: E402
from _yamlread import job_scalar  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from _workflows import _trigger_names  # noqa: E402


def _speed_yml():
    return (ROOT / '.github' / 'workflows' / 'speed.yml').read_text(
        encoding='utf-8')


def _speed_script(workflow, job, step_name):
    """One named step's run block from speed.yml, dedented for reading."""
    section = '\n'.join(_job_section(workflow, job))
    _, marker, after = section.partition(f'- name: {step_name}\n')
    assert marker, f'speed.yml has no {step_name!r} step in {job!r}'
    _, marker, body = after.partition('        run: |\n')
    assert marker, f'the {step_name!r} step in {job!r} has no run block'
    lines = []
    for line in body.splitlines():
        if line.strip() and not line.startswith('          '):
            break
        lines.append(line[10:])
    return '\n'.join(lines)


def test_only_the_benchmark_cells_benchmark_without_a_reviewer(tmp):
    """The benchmark environment sits on the job that runs pull-request code.

    The matrix cells that check out a pull request's own head and run it are
    the only surface a fork's code reaches in this workflow, so the
    environment is what decides whose code runs at all, and it is pinned
    whole. The jobs that run no pull-request code carry no environment at
    all: a deployment record on either would claim a reviewer gate stood in
    front of work that never touched the tree.
    """
    del tmp
    expected = (
        "${{ github.event_name == 'pull_request'"
        ' && github.event.pull_request.head.repo.full_name'
        " != github.repository"
        " && 'fork-benchmark' || 'benchmark' }}")
    workflow = _speed_yml()
    actual = job_scalar(workflow, 'timed', 'environment')
    # Whitespace inside the expression collapses, so how the scalar is
    # wrapped is free; a trailing newline collapses to a space rather than
    # vanishing, so `>` in place of `>-` fails here instead of shipping a
    # newline in an environment name.
    assert actual is not None and re.sub(r'\s+', ' ', actual) == expected, (
        f'speed.yml routes the timed cells by {actual!r}, '
        f'not by {expected!r}')
    # Both jobs that run no pull-request code stay outside every environment.
    for job in ('correctness', 'speed'):
        assert job_scalar(workflow, job, 'environment') is None, job


def test_the_speed_gate_throws_away_its_first_round(tmp):
    """The first suite a job runs is not one of the measured ones.
    A cold page cache is paid by whichever side goes first, and interleaving
    does not share it: across eight runs the baseline's first round exceeded
    its second by 19.14s on average and was the largest of the four totals
    every time, while the head's first round exceeded its second by 0.96s. In
    the same direction every run, so rounds do not average it out — it made
    every verdict about 3% optimistic, which is a gate reading low on exactly
    the regressions it exists to catch.

    Discarding is only discarding if the comparison cannot see it, so this
    pins both halves: a warm-up runs before the measured loop, and it writes
    outside the globs the comparison reads.
    """
    del tmp
    workflow = _speed_yml()
    _, marker, after = workflow.partition(
        '- name: Run both suites, interleaved')
    assert marker, 'the timing step is not named the way this test finds it'
    step, _, rest = after.partition('- name: Compare')
    warmup = step.index('reports/warmup/')
    measured = step.index('reports/$side-$round')
    assert warmup < measured, 'the warm-up does not run before the measurement'

    # The comparison reads reports/base-* and reports/head-*; a warm-up
    # written as reports/base-0 would be picked up as a round.
    for glob in re.findall(r'ls -d (reports/\S+)', rest):
        assert not fnmatch.fnmatch('reports/warmup', glob), glob
        assert not fnmatch.fnmatch('reports/warmup/base', glob), glob

    # A ceiling that does not cover the extra runs kills the job for doing
    # them, which is the failure the warm-up would introduce. The ceiling
    # that matters is the one the measuring cells run under.
    rounds = int(re.search(r'ROUNDS: "(\d+)"', workflow).group(1))
    ceiling = int(complete_job_mapping(workflow, 'timed')['timeout-minutes'])
    assert ceiling >= 15 * (rounds * 2 + 2), (ceiling, rounds)


def test_the_speed_gate_measures_a_pull_request_against_its_own_base(tmp):
    """Before merge, and against the base SHA rather than the last release.
    The gate ran on push alone, so a regression was measured only after it had
    landed. Two details make the pull-request half mean anything: the baseline
    is the exact base SHA — the last release would fold every commit merged
    since into the number and attribute all of it to whoever opened the pull
    request — and the candidate is the pull request's own head rather than the
    merge commit `actions/checkout` defaults to, which is a tree nobody
    authored and no reviewer can point at.
    """
    del tmp
    workflow = _speed_yml()
    triggers, _, jobs = workflow.partition('permissions:')
    assert '\n  pull_request:' in triggers, triggers
    # pull_request_target would run the proposed code with a writable token
    # and the base repository's secrets in reach. Checked against the trigger
    # block alone: the workflow's own comment says why it is not used, and a
    # whole-file search would match that comment.
    assert '\n  pull_request_target:' not in triggers, triggers
    assert 'contents: read' in workflow, workflow
    assert 'github.event.pull_request.base.sha' in jobs, jobs
    assert 'github.event.pull_request.head.sha' in jobs, jobs
    # Two open pull requests must not cancel each other.
    assert 'group: speed-${{ github.event.pull_request.number' in workflow

    # The base SHA is payload; it must travel by environment rather than be
    # expanded inside the script that consumes it.
    _, marker, after = workflow.partition('PR_BASE: ${{')
    assert marker, 'the base SHA does not reach the script by environment'
    script, _, _ = after.partition('- name: Check out this commit')
    _, _, body = script.partition('run: |')
    assert '${{' not in body, 'an expression is interpolated into the script'


def test_the_speed_gate_is_not_manually_dispatchable(tmp):
    """The one job whose checkout ref is a step output takes no manual run.

    Code scanning reports three `actions/cache-poisoning/poisonable-step`
    findings on this file, and each names the trigger rather than any cache
    input: `(workflow_dispatch)`. They are false positives — the query treats
    a dispatched run as holding the default branch's cache scope whatever ref
    it was started on, while a real run's scope follows the ref it was given.
    The trigger is dropped anyway rather than carrying three permanently
    dismissed alerts, so re-adding it reopens all three.

    Read through `_workflow_triggers` rather than by substring: `push` and
    `pull_request` are asserted present because without them the refusal above
    would be satisfied by a file that had stopped declaring triggers at all.
    """
    del tmp
    names = _trigger_names(_speed_yml())
    assert 'workflow_dispatch' not in names, sorted(names)
    assert 'repository_dispatch' not in names, sorted(names)
    assert 'push' in names, sorted(names)
    assert 'pull_request' in names, sorted(names)


def test_the_speed_cells_start_only_after_correctness(tmp):
    """The cells that benchmark a tree first learn that its tests pass.

    The matrix job names `correctness` in `needs` and adds no `if` of its own,
    so a red or cancelled aggregate leaves every cell skipped and no runner is
    spent measuring a tree nobody can ship.
    """
    del tmp
    workflow = _speed_yml()
    for job in ('correctness', 'timed', 'speed'):
        assert complete_job_mapping(workflow, job) is not None, job
    timed = complete_job_mapping(workflow, 'timed')
    assert timed['needs'] == ['correctness'], timed.get('needs')
    # The default condition is what skips the cells on a red aggregate.
    assert timed.get('if') is None, timed.get('if')
    assert timed['strategy']['fail-fast'] == 'false'
    assert int(timed['timeout-minutes']) > 0


def test_the_speed_gate_waits_for_the_exact_aggregate_check(tmp):
    """The wait names the check it waits for, and absence is not a pass.

    The aggregate's check run carries the name the tests workflow gave its
    job, so the two spellings have to agree or the wait polls a name nothing
    will ever carry — and a wait that gave up on absence would report the
    gates as green on exactly the commit whose gates never ran.
    """
    del tmp
    workflow = _speed_yml()
    expected = complete_job_mapping(_tests_yml(), 'aggregate')['name']
    assert expected == 'Aggregate workflow checks'
    job = complete_job_mapping(workflow, 'correctness')
    assert int(job['timeout-minutes']) > 0
    script = _speed_script(workflow, 'correctness',
                           'Wait for the correctness aggregate')

    # The exact check name is what the query selects on, and it is the same
    # string the tests workflow prints the aggregate job under.
    assert expected in script, script
    # The commit the check runs attach to is this workflow's own event SHA,
    # and it travels by environment rather than being interpolated in.
    assert 'SHA: ${{ github.sha }}' in workflow
    assert '${{' not in script, 'an expression is interpolated into the script'
    # Lists are paginated and fetched fresh; nothing is cached mid-wait.
    assert '--paginate' in script
    assert 'Cache-Control: no-cache' in script
    assert 'check-runs' in script
    # Bounded, with a ceiling that outlasts the bound.
    assert re.search(r'\bsleep (\d+)\b', script), script
    assert re.search(r'\btries=(\d+)\b', script), script
    assert int(job['timeout-minutes']) >= int(
        re.search(r'\btries=(\d+)\b', script).group(1))
    # Three endings, none of them a silent pass.
    assert script.count('exit 1') >= 2, script
    assert 'exit 0' in script, script
    assert '|| true' not in script and '|| echo' not in script


def test_the_speed_cells_partition_the_suites(tmp):
    """Every suite is measured exactly once, and the catch-all mops up.

    Coverage is proved against the current tree with the instrument's own
    matcher, the same `selected()` the cells run with.
    """
    del tmp
    workflow = _speed_yml()
    timed = complete_job_mapping(workflow, 'timed')
    cells = timed['strategy']['matrix']['include']
    assert cells, 'the matrix has no cells'
    groups = [cell['group'] for cell in cells]
    assert len(groups) == len(set(groups)), groups
    # Check-run-safe: the group names appear inside a check run's name.
    for group in groups:
        assert re.fullmatch(r'[a-z0-9][a-z0-9-]*', group), group
    assert timed['name'] == 'timed (${{ matrix.group }})', timed.get('name')

    catch = [cell for cell in cells if 'except' in cell]
    named = [cell for cell in cells if cell not in catch]
    assert len(catch) == 1, [cell['group'] for cell in catch]
    assert 'suites' in catch[0], 'the catch-all names no include glob'
    named_globs = [glob for cell in named for glob in
                   cell['suites'].split()]
    # The catch-all is exactly the complement of the named groups.
    assert sorted(catch[0]['except'].split()) == sorted(named_globs), (
        catch[0]['except'].split(), named_globs)

    instrument = _util.load(ROOT / 'scripts' / 'ci' / 'time_tests.py')
    suites = sorted(path.name for path in (ROOT / 'tests').glob('test_*.py'))
    assert suites, 'no suite files on this tree'
    unowned, double = [], []
    caught_by_catchall = 0
    for name in suites:
        owners = [cell['group'] for cell in named
                  if instrument.selected(name, cell['suites'].split(), ())]
        if instrument.selected(name, catch[0]['suites'].split(),
                               catch[0]['except'].split()):
            owners.append(catch[0]['group'])
            caught_by_catchall += 1
        if not owners:
            unowned.append(name)
        if len(owners) > 1:
            double.append((name, owners))
    assert not unowned, unowned
    assert not double, double
    # A catch-all holding nothing on today's tree is a cell that would run
    # and measure nothing, which the instrument refuses.
    assert caught_by_catchall > 0, 'the catch-all cell has no suites to time'

    # Each cell's durations land in an artifact named after its group.
    section = '\n'.join(_job_section(workflow, 'timed'))
    assert 'speed-durations-${{ matrix.group }}' in section, section


def test_the_speed_verdict_is_one_aggregate_over_the_cells(tmp):
    """One check name, one verdict, decided from the cells' own results.

    The final job fails when any cell failed, and when the cells never ran
    because the aggregate is red it says so instead of failing again on
    skipped work.
    """
    del tmp
    workflow = _speed_yml()
    final = complete_job_mapping(workflow, 'speed')
    # No `name:` field: the check run keeps the workflow's own name, "speed".
    assert 'name' not in final, final.get('name')
    assert final['needs'] == ['correctness', 'timed'], final.get('needs')
    assert final['if'] == '${{ always() }}', final.get('if')
    script = _speed_script(workflow, 'speed',
                           'Aggregate the cell verdicts')
    # The verdict is decided from the needs results, which reach the script
    # by environment; cancellation has no env form and is read from them too.
    section = '\n'.join(_job_section(workflow, 'speed'))
    assert 'CORRECTNESS: ${{ needs.correctness.result }}' in section, section
    assert 'TIMED: ${{ needs.timed.result }}' in section, section
    assert '${{' not in script, 'an expression is interpolated into the script'
    assert '"$CORRECTNESS" = "cancelled"' in script, script
    # Per-group verdicts come from the cells' own check runs, and each row
    # names where that group's durations live.
    assert '--paginate' in script and 'Cache-Control: no-cache' in script
    assert 'check-runs' in script
    assert 'timed (' in script, script
    assert 'speed-durations-' in script, script
    assert script.count('exit 1') >= 1, script
    assert script.count('exit 0') >= 2, script


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='speedgate_')


if __name__ == '__main__':
    raise SystemExit(main())
