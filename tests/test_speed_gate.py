#!/usr/bin/env python3
"""The speed gate's shape: a correctness gate, a suite partition, one verdict.

The pins here hold three things together: the wait names the tests
workflow's aggregate check and aims at the commit its checks attach to, the
matrix cells partition the suites and carry a static name, and the final job
keeps the check name and reads the cells' own verdict records. The wait's
runtime behaviour is executed in test_speed_wait.py; other invariants over
speed.yml stay in `test_ci_workflows.py`.
"""
import fnmatch
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _speedharness import (  # noqa: E402
    INSTRUMENT_STUB, run_workflow_script, speed_script, speed_yml,
    stub_path, write_executable,
)
from _wfgraph import _job_section, _tests_yml  # noqa: E402
from _yamlread import job_scalar  # noqa: E402
from _yamlsteps import complete_job_mapping  # noqa: E402
from _workflows import _trigger_names  # noqa: E402


_BASE_SHA = '1' * 40
_HEAD_SHA = '2' * 40
_MERGE_BASE = '3' * 40


def _compare_durations():
    return _util.load(ROOT / 'scripts' / 'ci' / 'compare_durations.py')


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
    workflow = speed_yml()
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
    workflow = speed_yml()
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
    """Before merge, and against the merge base rather than the last release.

    The gate ran on push alone, so a regression was measured only after it had
    landed. Two details make the pull-request half mean anything: the baseline
    is the merge base of the branch and its base, for the reason speed.yml's
    header gives, and the candidate is the pull request's own head rather than
    the merge commit `actions/checkout` defaults to, which is a tree nobody
    authored and no reviewer can point at.
    """
    del tmp
    workflow = speed_yml()
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

    # Both SHAs are payload; they must travel by environment rather than be
    # expanded inside the script that consumes them.
    _, marker, after = workflow.partition('PR_BASE: ${{')
    assert marker, 'the base SHA does not reach the script by environment'
    assert 'PR_HEAD: ${{' in after, 'the head SHA travels some other way'
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
    names = _trigger_names(speed_yml())
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
    workflow = speed_yml()
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
    workflow = speed_yml()
    expected = complete_job_mapping(_tests_yml(), 'aggregate')['name']
    assert expected == 'Aggregate workflow checks'
    job = complete_job_mapping(workflow, 'correctness')
    timeout = int(job['timeout-minutes'])
    assert timeout > 0
    script = speed_script(
        workflow, 'correctness', 'Wait for the correctness aggregate')

    # The exact check name is what the query selects on, and it is the same
    # string the tests workflow prints the aggregate job under.
    assert expected in script, script
    # On a pull request event github.sha names the merge commit, which
    # carries none of this repository's check runs.
    section = '\n'.join(_job_section(workflow, 'correctness'))
    expected_sha = ('SHA: ${{ github.event.pull_request.head.sha'
                    ' || github.sha }}')
    assert expected_sha in section, section
    assert 'SHA: ${{ github.sha }}' not in section, section
    assert '${{' not in script, 'an expression is interpolated into the script'
    # Lists are paginated and fetched fresh; nothing is cached mid-wait.
    assert '--paginate' in script
    assert 'Cache-Control: no-cache' in script
    assert 'check-runs' in script
    # A re-delivered page must not double a count, and the rows are unique.
    assert 'sort -u' in script, script
    # Bounded, with a ceiling that outlasts the bound: minutes against
    # seconds, so widening either the attempts or the pause trips it.
    tries = int(re.search(r'\btries=(\d+)\b', script).group(1))
    # The production bound lives here, not in the behavioral harness, which
    # substitutes its own: the value 45 is pinned on this side alone.
    assert 'tries=45' in script, script
    pauses = {int(found) for found in re.findall(r'\bsleep (\d+)\b', script)}
    assert len(pauses) == 1, pauses
    pause = pauses.pop()
    assert pause > 0 and tries > 0
    assert tries * pause <= timeout * 60, (tries, pause, timeout)
    # Three endings, none of them a silent pass.
    assert script.count('exit 1') >= 2, script
    assert 'exit 0' in script, script
    assert '|| true' not in script and '|| echo' not in script


def test_the_compare_step_uses_the_head_acceptance_manifest(tmp):
    """The comparator reads the reviewed acceptance file from HEAD."""
    del tmp
    workflow = speed_yml()
    section = '\n'.join(_job_section(workflow, 'timed'))
    _, marker, after = section.partition('- name: Compare\n')
    assert marker, section
    compare_block, _, _ = after.partition('- name:')
    paths = re.findall(r'--accept\s+(\S+)', compare_block)
    assert paths == ['head/scripts/ci/accepted_speed_changes.json'], (
        paths, compare_block)
    assert compare_block.count(
        'head/scripts/ci/accepted_speed_changes.json') == 1, compare_block


def test_the_accepted_speed_manifest_matches_test_names_in_the_tree(tmp):
    """The tracked manifest is strict and cannot silently drift from tests."""
    del tmp
    path = ROOT / 'scripts' / 'ci' / 'accepted_speed_changes.json'
    acceptances = _compare_durations()._load_acceptances(path)
    assert isinstance(acceptances, list), acceptances
    sources = [
        suite.read_text(encoding='utf-8')
        for suite in sorted((ROOT / 'tests').glob('*.py'))]
    for acceptance in acceptances:
        assert isinstance(acceptance, dict), acceptance
        assert set(acceptance) == {
            'test', 'max_ratio', 'reason', 'through_baseline'}, acceptance
        name = acceptance['test']
        assert isinstance(name, str) and name.strip(), acceptance
        bound = acceptance['max_ratio']
        assert (isinstance(bound, (int, float))
                and not isinstance(bound, bool) and bound > 0), acceptance
        assert isinstance(acceptance['reason'], str), acceptance
        baseline = acceptance['through_baseline']
        assert isinstance(baseline, list) and baseline, acceptance
        assert all(isinstance(label, str) and label.strip()
                   for label in baseline), acceptance
        assert len(baseline) == len(set(baseline)), acceptance
        needle = re.compile(r'^\s*def\s+' + re.escape(name) + r'\s*\(',
                            re.MULTILINE)
        assert any(needle.search(source) for source in sources), name


def test_the_accepted_speed_manifest_can_be_empty_or_missing(tmp):
    """Removing every acceptance, or the file, is ordinary cleanup."""
    compare = _compare_durations()
    empty = Path(tmp) / 'empty.json'
    empty.write_text('{"acceptances": []}', encoding='utf-8')
    missing = Path(tmp) / 'missing.json'
    assert compare._load_acceptances(empty) == []
    assert compare._load_acceptances(missing) == []


def test_the_speed_cells_partition_the_suites(tmp):
    """Every suite is measured exactly once, and the catch-all mops up.

    Coverage is proved against the current tree with the instrument's own
    matcher, the same `selected()` the cells run with.
    """
    workflow = speed_yml()
    timed = complete_job_mapping(workflow, 'timed')
    cells = timed['strategy']['matrix']['include']
    assert cells, 'the matrix has no cells'
    groups = [cell['group'] for cell in cells]
    assert len(groups) == len(set(groups)), groups
    # Check-run-safe: the group names appear inside a check run's name.
    for group in groups:
        assert re.fullmatch(r'[a-z0-9][a-z0-9-]*', group), group
    # A skipped matrix job still creates one check run, named after the job's
    # `name:` verbatim. An expression there leaks the raw template into that
    # name whenever the cells never started, so the name is static and the
    # matrix values stay out of it in every job state.
    assert timed['name'] == 'timed', timed.get('name')
    assert '${{' not in timed['name'], timed['name']

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


def test_a_cell_records_its_verdict_beside_its_reports(tmp):
    """The cell that measured is the one that says what it concluded.

    A skipped matrix job leaves no check run to attribute, so each cell
    writes one record into the artifact it already uploads and the verdict
    job reads it back from there.
    """
    workflow = speed_yml()
    section = '\n'.join(_job_section(workflow, 'timed'))
    # The comparison is the named step the verdict is read back from.
    _, marker, after = section.partition('- name: Compare\n')
    assert marker, section
    compare_block, marker, _ = after.partition('- name:')
    assert marker, section
    assert 'id: compare' in compare_block, compare_block

    # A failed comparison is exactly the verdict the summary must still
    # show, so the record runs whenever the cell did work.
    _, marker, after = section.partition('- name: Record the cell verdict\n')
    assert marker, section
    record_block, _, _ = after.partition('- name:')
    condition = ("if: ${{ !cancelled() && steps.baseline.outputs.point"
                 " != '' }}")
    assert condition in record_block, record_block
    script = speed_script(workflow, 'timed', 'Record the cell verdict')
    assert '${{' not in script, 'an expression is interpolated into the script'
    reports = Path(tmp) / 'reports'
    reports.mkdir()
    result = run_workflow_script(tmp, script, {
        'GROUP': 'bridge',
        'VERDICT': 'failure',
    })
    assert result.returncode == 0, (result.stdout, result.stderr)
    written = (reports / 'verdict.json').read_text(encoding='utf-8')
    assert written == '{"group": "bridge", "verdict": "fail"}\n', written


def test_the_speed_verdict_is_one_aggregate_over_the_cells(tmp):
    """One check name, one verdict, decided from the cells' own artifacts.

    The final job fails when any cell failed, and when the cells never ran
    because the aggregate is red it says so instead of failing again on
    skipped work.
    """
    workflow = speed_yml()
    final = complete_job_mapping(workflow, 'speed')
    # No `name:` field: the check run keeps the workflow's own name, "speed".
    assert 'name' not in final, final.get('name')
    assert final['needs'] == ['correctness', 'timed'], final.get('needs')
    assert final['if'] == '${{ always() }}', final.get('if')
    script = speed_script(
        workflow, 'speed', 'Aggregate the cell verdicts')
    # The verdict is decided from the needs results, which reach the script
    # by environment; cancellation has no env form and is read from them too.
    section = '\n'.join(_job_section(workflow, 'speed'))
    assert 'CORRECTNESS: ${{ needs.correctness.result }}' in section, section
    assert 'TIMED: ${{ needs.timed.result }}' in section, section
    assert '${{' not in script, 'an expression is interpolated into the script'
    assert '"$CORRECTNESS" = "cancelled"' in script, script
    # The rows come from the cells' uploaded records, not from check runs.
    assert 'actions/download-artifact@' in section, section
    assert 'pattern: speed-durations-*' in section, section
    assert 'path: cells' in section, section
    assert 'check-runs' not in script, script
    assert 'gh ' not in script, script
    assert 'speed-durations-' in script, script
    assert 'cells/*/verdict.json' in script, script
    assert script.count('exit 1') >= 1, script
    assert script.count('exit 0') >= 2, script


def test_the_verdict_table_is_read_from_the_cells_records(tmp):
    """One row per measuring cell, read out of what the cell itself wrote.

    The record is placed where `actions/download-artifact` leaves it — one
    directory per artifact — and must survive the trip into the table.
    """
    workflow = speed_yml()
    workdir = Path(tmp)
    record = speed_script(workflow, 'timed', 'Record the cell verdict')
    (workdir / 'reports').mkdir()
    written = run_workflow_script(workdir, record, {
        'GROUP': 'bridge',
        'VERDICT': 'success',
    })
    assert written.returncode == 0, (written.stdout, written.stderr)
    source = workdir / 'reports' / 'verdict.json'
    cells = workdir / 'cells' / 'speed-durations-bridge'
    cells.mkdir(parents=True)
    (cells / 'verdict.json').write_text(
        source.read_text(encoding='utf-8'), encoding='utf-8')

    summary = workdir / 'summary.md'
    result = run_workflow_script(
        workdir,
        speed_script(workflow, 'speed', 'Aggregate the cell verdicts'),
        {
            'CORRECTNESS': 'success',
            'TIMED': 'success',
            'GITHUB_STEP_SUMMARY': str(summary),
        })
    assert result.returncode == 0, (result.stdout, result.stderr)
    text = summary.read_text(encoding='utf-8')
    assert '| bridge | pass | `speed-durations-bridge` |' in text, text

    # A run where no cell uploaded a record — no release existed to measure
    # against — explains the empty-table case rather than leaving it blank.
    shutil.rmtree(workdir / 'cells')
    summary.write_text('', encoding='utf-8')
    empty = run_workflow_script(
        workdir,
        speed_script(workflow, 'speed', 'Aggregate the cell verdicts'),
        {
            'CORRECTNESS': 'success',
            'TIMED': 'success',
            'GITHUB_STEP_SUMMARY': str(summary),
        })
    assert empty.returncode == 0, (empty.stdout, empty.stderr)
    text = summary.read_text(encoding='utf-8')
    assert '| bridge |' not in text, text
    assert 'No cell uploaded' in text, text


def test_the_speed_verdict_fails_unless_the_matrix_succeeded(tmp):
    """Anything that is not exactly success fails the verdict job.

    Every result GitHub can hand a matrix job is named here, so a comparison
    that went soft — inverted, or narrowed to one value — fails a case.
    """
    workflow = speed_yml()
    workdir = Path(tmp)
    stub_path(workdir)
    calls = workdir / 'gh-calls'
    script = speed_script(workflow, 'speed', 'Aggregate the cell verdicts')

    def run(timed, correctness='success'):
        summary = workdir / f'summary-{timed}.md'
        result = run_workflow_script(workdir, script, {
            'CORRECTNESS': correctness,
            'TIMED': timed,
            'GITHUB_STEP_SUMMARY': str(summary),
            'STUB_CALLS': str(calls),
            'STUB_FAIL': '1',
        })
        return result, summary.read_text(encoding='utf-8')

    result, text = run('success')
    assert result.returncode == 0, (result.stdout, result.stderr)
    # No cell uploaded a record here, so nothing measured: a verdict that
    # claimed cells passed would be reporting a run that never happened.
    assert 'Every speed cell passed' not in result.stdout, result.stdout
    assert 'no speed comparison ran' in result.stdout, result.stdout
    # No verdict is scraped from anywhere: the matrix result and the cells'
    # own records are the whole story.
    assert not calls.exists(), calls.read_text(encoding='utf-8')

    for timed in ('failure', 'skipped'):
        result, _ = run(timed)
        assert result.returncode == 1, (timed, result.stdout, result.stderr)
        assert 'At least one speed cell failed' in result.stderr, (
            timed, result.stderr)

    # A cancelled matrix is the first branch's case: no measurement, no
    # verdict, and no failure to report on top of the cancellation.
    result, text = run('cancelled')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert 'The run was cancelled' in text, text

    result, text = run('failure', correctness='failure')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert 'Not run: the correctness aggregate' in text, text
    result, text = run('failure', correctness='cancelled')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert 'The run was cancelled' in text, text


def test_the_timing_step_reports_what_it_measured(tmp):
    """A reports tree the step populated passes the step's own guard.

    Noglob left on turns the guard's report globs into literal paths, so a
    step that measured every round it asked for reports "no durations".
    """
    workflow = speed_yml()
    script = speed_script(workflow, 'timed', 'Run both suites, interleaved')
    # Noglob is scoped to the selection, not to the step's own globs.
    assert 'set -f' in script and 'set +f' in script, script
    assert script.index('set -f') < script.index('set +f'), script

    workdir = Path(tmp)
    bin_dir = stub_path(workdir)
    write_executable(bin_dir / 'python3', INSTRUMENT_STUB)
    summary = workdir / 'summary.md'

    def run(skip):
        reports = workdir / 'reports'
        if reports.exists():
            shutil.rmtree(reports)
        return run_workflow_script(workdir, script, {
            'GITHUB_WORKSPACE': str(workdir),
            'ROUNDS': '2',
            'SUITES_ONLY': '',
            'SUITES_EXCEPT': '',
            'STUB_INSTRUMENT_CALLS': str(workdir / 'instrument-calls'),
            'STUB_SKIP': skip or '@@none@@',
            'GITHUB_STEP_SUMMARY': str(summary),
        })

    result = run('')
    assert result.returncode == 0, (result.stdout, result.stderr)
    for round in (1, 2):
        for side in ('base', 'head'):
            assert (workdir / 'reports' / f'{side}-{round}'
                    / 'durations.json').exists()

    # A round with no durations is still a setup failure.
    result = run('base-1')
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert 'no durations' in result.stderr, result.stderr


def _find_baseline(tmp, name, rows, **environment):
    """Run the `Find the baseline` step against a stubbed `gh`."""
    workdir = Path(tmp) / name
    workdir.mkdir(parents=True)
    stub_path(workdir)
    calls = workdir / 'gh-calls'
    calls.write_text('', encoding='utf-8')
    row_file = workdir / 'gh-rows'
    row_file.write_text(rows, encoding='utf-8')
    output = workdir / 'github-output'
    output.write_text('', encoding='utf-8')
    summary = workdir / 'summary.md'
    summary.write_text('', encoding='utf-8')
    result = run_workflow_script(
        workdir,
        speed_script(speed_yml(), 'timed', 'Find the baseline'),
        {'REPO': 'owner/repo', 'GITHUB_OUTPUT': str(output),
         'GITHUB_STEP_SUMMARY': str(summary), 'STUB_CALLS': str(calls),
         'STUB_ROWS': str(row_file), 'STUB_FAIL': '',
         **environment})
    return (result, output.read_text(encoding='utf-8'),
            calls.read_text(encoding='utf-8'),
            summary.read_text(encoding='utf-8'))


def test_a_pull_request_baseline_is_the_merge_base(tmp):
    """The point measured against is where the branch diverged.

    Not the payload's base SHA, which is the base branch's tip; speed.yml's
    header gives the reason.
    """
    result, output, _, _ = _find_baseline(
        tmp, 'merge-base', f'{_MERGE_BASE}\n', EVENT='pull_request',
        PR_BASE=_BASE_SHA, PR_HEAD=_HEAD_SHA)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert output.strip() == f'point={_MERGE_BASE}', output
    assert _BASE_SHA not in output, output


def test_the_merge_base_comes_from_the_compare_endpoint(tmp):
    """Both payload SHAs name the comparison the merge base is read from."""
    _, _, calls, _ = _find_baseline(
        tmp, 'compare', f'{_MERGE_BASE}\n', EVENT='pull_request',
        PR_BASE=_BASE_SHA, PR_HEAD=_HEAD_SHA)
    assert f'repos/owner/repo/compare/{_BASE_SHA}...{_HEAD_SHA}' in calls, (
        calls)
    assert '.merge_base_commit.sha' in calls, calls
    assert 'Cache-Control: no-cache' in calls, calls


def test_an_unresolvable_merge_base_fails_the_gate(tmp):
    """A pull request always has a merge base, so failing to find one is red.

    Skipping would report the check green having measured nothing. `long`
    and `not-hex` pin the two halves of the shape check apart: every other
    answer here is already refused by length alone.
    """
    answers = {
        'lookup-failed': ('', '1'),
        'blob': ('{"message": "Not Found"}\n', ''),
        'null': ('null\n', ''),
        'empty': ('\n', ''),
        'short': (f'{_MERGE_BASE[:39]}\n', ''),
        'long': (f'{_MERGE_BASE}a\n', ''),
        'not-hex': (f'{"z" * 40}\n', ''),
    }
    for name, (rows, failing) in answers.items():
        result, output, _, summary = _find_baseline(
            tmp, name, rows, EVENT='pull_request', PR_BASE=_BASE_SHA,
            PR_HEAD=_HEAD_SHA, STUB_FAIL=failing)
        assert result.returncode != 0, (name, result.stdout, result.stderr)
        assert 'point=' not in output, (name, output)
        assert 'No baseline' in result.stderr, (name, result.stderr)
        assert 'Failed:' in summary, (name, summary)


def test_a_malformed_payload_sha_fails_before_any_api_call(tmp):
    """Neither payload SHA reaches the compare endpoint unchecked.

    The empty call log is the half that proves the refusal came first: a
    dropped guard still fails a later check, but only after asking the API
    about a SHA the payload made up.
    """
    for name, payload in (
            ('bad-base', {'PR_BASE': 'nope', 'PR_HEAD': _HEAD_SHA}),
            ('bad-head', {'PR_BASE': _BASE_SHA, 'PR_HEAD': 'nope'})):
        result, output, calls, summary = _find_baseline(
            tmp, name, f'{_MERGE_BASE}\n', EVENT='pull_request', **payload)
        assert result.returncode != 0, (name, result.stdout, result.stderr)
        assert calls == '', (name, calls)
        assert 'point=' not in output, (name, output)
        assert 'Failed:' in summary, (name, summary)


def test_an_empty_verdict_table_can_only_mean_no_release(tmp):
    """The cause the aggregate states is the only one that can produce it.

    An empty table on a green run is reported as "no release existed", which
    is a claim about the baseline step, so it is pinned against that step.
    """
    assert 'no release existed to measure' in speed_script(
        speed_yml(), 'speed', 'Aggregate the cell verdicts')
    result, output, _, _ = _find_baseline(
        tmp, 'unresolved', '', EVENT='pull_request', PR_BASE=_BASE_SHA,
        PR_HEAD=_HEAD_SHA, STUB_FAIL='1')
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert 'point=' not in output, output
    result, output, _, summary = _find_baseline(
        tmp, 'no-release', '', EVENT='push', PR_BASE='', PR_HEAD='',
        STUB_FAIL='1')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert output.strip() == 'point=', output
    assert 'no release exists yet' in summary, summary


def test_a_push_baseline_is_still_the_latest_release_tag(tmp):
    """The pull-request path leaves the release lookup where it was."""
    result, output, calls, _ = _find_baseline(
        tmp, 'push', 'v1.2.3\n', EVENT='push', PR_BASE='', PR_HEAD='')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert output.strip() == 'point=v1.2.3', output
    assert 'repos/owner/repo/releases/latest' in calls, calls
    assert 'compare/' not in calls, calls


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='speedgate_')


if __name__ == '__main__':
    raise SystemExit(main())
