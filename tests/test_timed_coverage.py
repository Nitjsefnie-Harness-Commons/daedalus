#!/usr/bin/env python3
"""Which downloaded runs count as evidence for a refresh.

A refresh reads the runs under a root, picks a sample, and writes the
data file from it. Two rules decide whether a run is in that sample,
and both live in `scripts/ci/timings_runs.py`: a run is judged against
the cell set of the newest run that produced any, and a run that
produced ONE cell where the file bounds the matrix at more than one is
a collapsed matrix rather than a partition, so it is skipped and
reported instead of becoming the reference every other run is then
filed `incomplete` against.

That is not a hypothetical shape. It is how the shipped data file came
to describe 28 of the tree's 326 suites: the one-cell run became the
partition, the runs that would have carried the tree were under the
root and were stepped over, and the write took the measured set alone.
`test_timed_refresh.py` covers the other half -- that a write is a
union -- and `test_timed_planner.py` covers the guard on the other
side of the file, which refuses a plan whose weight is mostly
estimated.

THE GUARD ON THE OTHER SIDE, `scripts/ci/timings_coverage.py`, is here
too, and its two conditions are the subject of the last three tests.
The weight share alone could not hold it: a file recording the
twenty-nine LIGHTEST suites of the tree plus its heaviest is nine per
cent of the plan's suites and, because that one heavy weight inflates
the denominator the share is computed against, thirty-one per cent of
its weight. That is under the weight bound, the planner published one
cell for 327 suites, and the plan's total was 22.3 where the tree
really holds 345.2 -- a 15.5x understatement, worse than the 3.15x the
guard exists to stop.

The artifacts are fixtures under a temp tree: no API, no `gh`, no
network. Each test builds a run the way the timed job leaves one --
`<run>/<cell>/head-N/<suite>.json`, one JSON per suite, plus the cell's
reference reading -- and drives `refresh_timings.main()` over it.
"""
import contextlib
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT, git_index, git_output  # noqa: E402
from _speedharness import (  # noqa: E402
    run_workflow_script, workflow_script)
from _timed_basis import (  # noqa: E402
    fixture_tree, unmeasured_names, write_run as _write_run)

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


def _planner():
    return _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                      'plan_timed_matrix')


def _seed(tmp, weights, max_cells=15, name='suite-timings.json'):
    """The data file a refresh starts from: the file's own defaults."""
    path = Path(tmp) / name
    seed = {'schema_version': _planner().SCHEMA_VERSION,
            'target_cell_weight': 10.0, 'max_cells': max_cells,
            'units': 'reference-multiples', 'measured_from': 'tests run 1',
            'runs': 1, 'suite_weights': weights}
    path.write_text(json.dumps(seed, indent=2) + '\n', encoding='utf-8')
    return path


def _drive(tmp, root, weights, runs=3, max_cells=15, tree=None, **flags):
    """Refresh `weights` from `root` and report what the command did.

    One shape because every test here is the same three steps with
    different numbers: seed a data file, run the refresher over a runs
    root, read the file back. `flags` are the refresher's own options by
    name, so a test that needs `--message-file` says so and one that
    needs nothing says nothing. Returns the file's TEXT, the exit code
    and stderr -- a test that wants the weights parses the text, and one
    that wants to prove the file is untouched compares it verbatim.

    The tree is a fixture holding exactly the file's recorded suites,
    because the rules here are judged against what the TREE holds: the
    collapse is counted against the recorded suites the tree still has,
    and against the real repository's 327 suites every fixture name
    would be a deleted suite and the rule could never fire.
    """
    path = _seed(tmp, weights, max_cells)
    tree = tree if tree is not None else fixture_tree(tmp, sorted(weights))
    argv = ['--runs-root', str(root), '--out', str(path),
            '--runs', str(runs), '--tree', str(tree)]
    argv += [item for name in sorted(flags)
             for item in (f'--{name.replace("_", "-")}', str(flags[name]))]
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                          'refresh_timings').main(argv)
    return path.read_text(encoding='utf-8'), code, err.getvalue()


def test_a_one_cell_run_is_not_the_partition_when_an_older_measured_more(
        tmp):
    """A collapsed matrix must not make every richer run `incomplete`.

    `select` takes the reference cell set from the newest run that
    produced any cell, so a one-cell run is "complete" against its own
    one-cell set and every older, richer run is filed as incomplete and
    skipped. That is what turned a single bad run into a 28-suite file:
    the runs that would have carried the tree were under the root and
    were stepped over. The newest run here measures one cell and one of
    the two suites the file records; the two behind it measured two
    cells and both suites, and the sample must come from those.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 140, {'cell-01': {'test_a.py': 4.0}})
    for run_id in (139, 138):
        _write_run(root, run_id, {'cell-01': {'test_a.py': 8.0},
                                  'cell-02': {'test_b.py': 2.0}})
    text, _code, err = _drive(tmp, root, {'test_a.py': 2.0, 'test_b.py': 1.0})
    written = json.loads(text)
    assert written['measured_from'] == '139, 138', written
    assert written['suite_weights'] == {'test_a.py': 4.0,
                                        'test_b.py': 1.0}, written
    assert '140' in err and 'one cell' in err, err


def test_a_deleted_suites_weight_does_not_move_the_derived_target(tmp):
    """The target is derived from what the TREE holds, not the file.

    The union carries a weight forward for every suite the runs did not
    measure, including one the tree has since deleted -- no run can ever
    measure a deleted suite, so the entry is permanent. The planner
    drops such a weight and names it `stale`, and the three consumers
    that are not the planner have to as well: `derive_target` summed
    the raw dict, so two dead weights of 900 beside six live ones of
    20.0 put the derived target at 130, and a 130 target packs a
    120-multiple live tree into ONE cell where its own weights ask for
    six. The target is the file's other policy number, and this is the
    chokepoint every write passes through.
    """
    planner = _planner()
    bounds = _util.load(ROOT / 'scripts' / 'ci' / 'timings_bounds.py',
                        'timings_bounds')
    live = [f'test_{index:02d}.py' for index in range(6)]
    tree = fixture_tree(tmp, live)
    weights = {name: 20.0 for name in live}
    weights['test_gone_a.py'] = 900.0
    weights['test_gone_b.py'] = 900.0
    data = json.loads(_seed(tmp, weights).read_text(encoding='utf-8'))
    target, _note = bounds.verify_target(tree, data, 15)
    plan = planner.plan(tree, dict(data, target_cell_weight=target))
    assert len(plan.cells) == 6, [cell.suites for cell in plan.cells]
    assert plan.stale == ['test_gone_a.py', 'test_gone_b.py'], plan.stale


def test_a_deleted_suites_weight_is_not_a_recorded_suite_for_the_collapse(
        tmp):
    """The collapse rule counts what the TREE holds, not the file.

    The third consumer: the collapsed-run rule is judged on how many
    suites the file already records, and it was handed the raw count, so
    fifty weights for deleted suites stood in for fifty suites the
    planner will never pack. A file with six live and fifty dead
    weights, refreshed by a one-cell run that measured all six live
    suites, was filed `degenerate` -- and the refresh then found
    nothing to sample, so the file could only recover by hand.
    """
    live = [f'test_{index:02d}.py' for index in range(6)]
    tree = fixture_tree(tmp, live)
    weights = {name: 1.0 for name in live}
    for index in range(50):
        weights[f'test_gone_{index:02d}.py'] = 1.0
    root = Path(tmp) / 'runs'
    _write_run(root, 300, {'cell-01': {name: 4.0 for name in live}},
               reference=2.0)
    text, _code, err = _drive(tmp, root, weights, runs=1, tree=tree)
    assert 'collapsed' not in err, err
    assert 'carried forward' in err, err
    assert json.loads(text)['measured_from'] == '300', text


def test_a_refresh_with_only_a_collapsed_matrix_to_choose_from_is_refused(
        tmp):
    """Nothing usable under the root: a named refusal, and no write.

    The healthy limb of the control above removes the richer runs, and
    the single one-cell run left is not a partition of a tree this file
    bounds at fifteen cells. The file must come back byte-identical
    rather than narrowed to whatever that run happened to measure.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 150, {'cell-01': {'test_a.py': 4.0}})
    weights = {'test_a.py': 2.0, 'test_b.py': 1.0}
    text, _code, err = _drive(tmp, root, weights)
    assert 'wrote nothing' in err, err
    assert '150' in err and 'one cell' in err, err
    assert 'max_cells bound of 15' in err, err
    assert json.loads(text)['suite_weights'] == weights, text


def test_a_one_cell_run_that_measured_everything_is_not_a_collapse(tmp):
    """The refusal needs BOTH conditions, and this is the one that is not.

    A single cell against a bound of fifteen is on its own the shape a
    small tree leaves, not evidence of anything: this file records both
    suites, the run measured both of them, and the refresh is an
    ordinary measurement that leaves the file alone. A guard firing
    here would stop the refresher on every hand-built runs root, and the
    two conditions exist so that it does not.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 160, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 2.0}})
    weights = {'test_a.py': 2.0, 'test_b.py': 1.0}
    text, _code, err = _drive(tmp, root, weights)
    assert 'wrote nothing' in err, err
    assert 'one cell' not in err, err
    assert json.loads(text)['suite_weights'] == weights, text


def test_a_file_bounded_at_one_cell_takes_a_one_cell_measurement(tmp):
    """A seed derives its bound from the run that measured it.

    A tree small enough to be one cell has a file bounded at one, so a
    one-cell run of that tree is exactly the partition the bound says
    to expect, and refusing it would stop the refresher on every small
    tree.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 170, {'cell-01': {'test_a.py': 4.0}})
    _text, _code, err = _drive(tmp, root, {'test_a.py': 2.0,
                                           'test_b.py': 1.0}, max_cells=1)
    assert 'carried forward' in err, err
    assert 'one cell' not in err, err


def test_a_one_cell_candidate_that_lost_its_reference_is_stepped_over(tmp):
    """Counting a collapsed run's suites must not read its references.

    The collapsed-run rule needs a candidate's suite count before it
    knows whether that candidate is the partition, and it got the count
    by reading the whole run. A cell that lost its `reference.json` is
    a REFUSAL in that path, so a run that would have been filed
    `incomplete` and stepped over -- which is what the base does with
    it, and what the two dispositions agree is right -- instead
    aborted the entire refresh, including the good two-cell run behind
    it. Same input, same root, two different answers from the two
    versions of this code.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 200, {'cell-01': {'test_a.py': 4.0},
                           'cell-02': {'test_b.py': 4.0}})
    _write_run(root, 199, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 4.0}},
               reference=None)
    text, code, err = _drive(tmp, root, {'test_a.py': 2.0, 'test_b.py': 1.0})
    assert code == 0, err
    assert json.loads(text)['measured_from'] == '200', text
    assert '199' in err and 'incomplete' in err, err


def test_a_multi_cell_run_that_measured_less_is_carried_not_refused(tmp):
    """Limb one of the collapsed rule, on its own: two cells is a matrix.

    The rule is a conjunction of three, and the two that read the FILE
    have controls of their own. The one that reads the RUN -- a single
    cell against a multi-cell bound -- did not, and a mutation that
    dropped it left every other control green. Here the newest run has
    TWO cells and measured one of the two suites the file records: a
    partial matrix, which the file carries forward by the union, and
    not the collapse the rule exists to stop.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 210, {'cell-01': {'test_a.py': 4.0},
                           'cell-02': {'test_c.py': 2.0}})
    text, _code, err = _drive(tmp, root, {'test_a.py': 2.0, 'test_b.py': 1.0,
                                          'test_d.py': 3.0}, runs=1)
    assert 'carried forward' in err, err
    assert 'one cell' not in err, err
    assert json.loads(text)['suite_weights'] == {
        'test_a.py': 2.0, 'test_b.py': 1.0, 'test_c.py': 1.0,
        'test_d.py': 3.0}, text


def test_a_partial_run_does_not_claim_to_have_measured_the_tree(tmp):
    """The `basis` is about the RUNS, not about the file the union wrote.

    The write is a union, so a suite the runs did not measure is in
    the file anyway -- and the coverage clause was driven by the file's
    own weights, so it stopped counting those suites as estimated. A
    two-cell run measuring three of five recorded suites then wrote a
    file whose own prose read "every suite in the tree is measured by
    these runs". The committed file is honest today and a generator
    that agrees with the file it wrote cannot see it: both are wrong
    together. This one is the runs' side, and the cell-count clause
    beside it is the same defect in the other sentence.
    """
    root = Path(tmp) / 'runs'
    _write_run(root, 220, {'cell-01': {'test_a.py': 4.0, 'test_b.py': 4.0},
                           'cell-02': {'test_c.py': 4.0}})
    weights = {'test_a.py': 1.0, 'test_b.py': 1.0, 'test_c.py': 1.0,
               'test_d.py': 1.0, 'test_e.py': 1.0}
    text, _code, err = _drive(tmp, root, weights, runs=1)
    basis = json.loads(text)['basis']
    assert 'wrote' in err, err
    assert 'every suite in the tree is measured' not in basis, basis
    assert "2 of the tree's 5 suites are not measured" in basis, basis
    assert 'test_d.py, test_e.py' in basis, basis
    # The other sentence: two measured cells are not the concurrency
    # the repository runs, whatever the file went on to derive.
    assert 'the concurrency the repository runs today' not in basis, basis
    assert 'the measured run ran 2 cells,' in basis, basis


def _thinned_to_the_lightest(data, names, keep=30):
    """The file's own weights, kept at their lightest plus its heaviest.

    The shape that flatters the weight share: `keep - 1` weights at the
    bottom of the tree's distribution and the single heaviest one on
    top. The median the planner lends the unmeasured suites is the
    median of that set, which is tiny, and the heaviest weight sits in
    the denominator the share is divided by.
    """
    recorded = {name: data['suite_weights'][name] for name in names
                if name in data['suite_weights']}
    light = sorted(recorded, key=lambda name: recorded[name])[:keep - 1]
    heavy = max(recorded, key=lambda name: recorded[name])
    return {name: recorded[name] for name in list(light) + [heavy]}


def test_a_light_tailed_recorded_set_is_not_a_share_the_guard_believes(tmp):
    """The weight share, on a real file, is 31.1% of a 15.5x error.

    The guard's own arithmetic, on the shipped file's own weights over
    the real tree: twenty-nine lightest suites plus the heaviest. The
    estimate is their median, the denominator is their sum, and the
    heaviest weight is most of it -- so the share reads 31% and passes
    a 50% bound, while the plan it produces is ONE cell for 327 suites
    and totals 22.3 reference multiples where the tree holds 345.2.
    A heavy recorded suite flatters the very statistic meant to catch
    it, and a second condition that no recorded weight can move is the
    only thing that closes that.
    """
    planner = _planner()
    coverage = _util.load(ROOT / 'scripts' / 'ci' / 'timings_coverage.py',
                          'timings_coverage')
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    names = planner.suite_names(ROOT)
    kept = _thinned_to_the_lightest(data, names)
    weights, estimated, _stale = planner.resolve(kept, names, 1.0)
    assert len(kept) == 30 and len(estimated) == len(names) - 30
    truth = sum(planner.resolve(
        data['suite_weights'], names, 1.0)[0].values())
    assert coverage.estimated_share(weights, estimated) < 0.5
    plan = planner.plan(ROOT, dict(data, suite_weights=kept))
    assert len(plan.cells) == 1, [cell.suites for cell in plan.cells]
    assert truth / sum(weights.values()) > 15, (truth, sum(weights.values()))
    refusal = coverage.coverage_refusal(weights, estimated)
    assert refusal is not None, 'the planner published a matrix on a fiction'
    assert 'refresh_timings.py' in refusal, refusal


def test_each_coverage_bound_refuses_a_file_the_other_one_publishes(tmp):
    """Neither statistic is a restatement of the other; both earn their place.

    Ten suites, three recorded at 1.0, 1.0 and 90.0: seven of the
    plan's SUITES are estimated and 7% of its weight, so the weight
    bound alone would publish it and the count bound refuses. Nine
    suites, five recorded at 0.001, 0.001, 5, 5, 5: four of its
    suites are estimated -- 44%, under the count bound -- and 57% of
    its weight, so the count bound alone would publish it and the
    weight bound refuses. A file with fewer than half its suites
    missing can still be a fiction, and one with an eighth of its
    weight missing can still be a quarter of the tree.
    """
    planner = _planner()
    coverage = _util.load(ROOT / 'scripts' / 'ci' / 'timings_coverage.py',
                          'timings_coverage')
    planner_module = planner
    counts = {'weight': 0, 'suite': 0}
    for case, (recorded, suites) in enumerate((
            ({'test_00.py': 1.0, 'test_01.py': 1.0, 'test_09.py': 90.0},
             [f'test_{index:02d}.py' for index in range(10)]),
            ({'test_00.py': 0.001, 'test_01.py': 0.001, 'test_02.py': 5.0,
              'test_03.py': 5.0, 'test_04.py': 5.0},
             [f'test_{index:02d}.py' for index in range(9)]))):
        # One tree per case: a shared one would carry the first case's
        # tenth suite into the second.
        tree = fixture_tree(Path(tmp) / f'case{case}', suites)
        weights, estimated, _stale = planner_module.resolve(
            recorded, planner_module.suite_names(tree), 1.0)
        weight_share = coverage.estimated_share(weights, estimated)
        suite_share = coverage.estimated_suite_share(weights, estimated)
        assert weight_share != suite_share, (weight_share, suite_share)
        refusal = coverage.coverage_refusal(weights, estimated)
        if weight_share > 0.5:
            assert suite_share < 0.5, (weight_share, suite_share)
            assert 'weight is estimated' in refusal, refusal
            counts['weight'] += 1
        else:
            assert suite_share > 0.5, (weight_share, suite_share)
            assert 'suites are estimated' in refusal, refusal
            counts['suite'] += 1
    assert counts == {'weight': 1, 'suite': 1}, counts


# The two halves of the seam, as the runner sees them: every command
# line of the refresh step, and the commit step up to and including the
# commit itself. The comments between them are not pinned -- prose moves
# -- but the command list is the whole of what the seam is, and a grep
# for two substrings is not a list: planting `rm -f refreshed-subject.txt`
# as the commit step's first line left every asserted substring in place
# and the suite green, on a workflow that can no longer commit at all.
_REFRESH_COMMANDS = [
    'python3 scripts/ci/refresh_timings.py --runs-root runs \\',
    '  --message-file refreshed-subject.txt \\',
    '  2>> "$GITHUB_STEP_SUMMARY"',
    'git diff --text -- .github/suite-timings.json \\',
    '  >> "$GITHUB_STEP_SUMMARY"',
]
_COMMIT_COMMANDS = [
    'if git diff --quiet -- .github/suite-timings.json; then',
    '  echo "No weight moved; nothing to commit."',
    '  exit 0',
    'fi',
    'install -d -m 700 ~/.ssh',
    "printf '%s\\n' \"$RATCHET_SSH_KEY\" > ~/.ssh/ratchet",
    'chmod 600 ~/.ssh/ratchet',
    "printf '%s\\n' 'github.com ssh-ed25519 "
    'AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\''
    ' \\',
    '  > ~/.ssh/known_hosts',
    'chmod 600 ~/.ssh/known_hosts',
    "git config user.name 'github-actions[bot]'",
    "git config user.email "
    "'41898282+github-actions[bot]@users.noreply.github.com'",
    'git add .github/suite-timings.json',
    'git commit -F refreshed-subject.txt',
]


def _commands(script):
    """The step's source lines that are commands: no comment, no blank."""
    return [line for line in script.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def test_the_committed_subject_names_exactly_the_runs_the_file_records(tmp):
    """The seam, pinned as a list of commands AND executed end to end.

    The refresh step writes the subject to a workspace file and the
    commit step commits that file, and the whole point of the seam is
    that the subject then names the runs the refreshed file records in
    `measured_from`. Grepping both steps for `--message-file` and
    `-F` never established that: one line planted in the commit step
    left every asserted substring intact and the workflow unable to
    commit. So the command lists are compared exactly, and then the
    commit step is RUN, in a real checkout, over a real data file
    whose `measured_from` is known, and the resulting commit subject is
    read back.
    """
    source = (ROOT / '.github' / 'workflows' / 'timed-timings.yml'
              ).read_text(encoding='utf-8')
    refresh_step = workflow_script(source, 'refresh', 'Refresh the data file')
    commit_step = workflow_script(source, 'refresh', 'Commit the refresh')
    commands = _commands(refresh_step)
    assert commands == _REFRESH_COMMANDS, commands
    commit_commands = _commands(commit_step)
    through = commit_commands[:len(_COMMIT_COMMANDS)]
    assert through == _COMMIT_COMMANDS, through

    repository = Path(tmp) / 'checkout'
    (repository / '.github').mkdir(parents=True)
    data_file = repository / '.github' / 'suite-timings.json'
    refresh = _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                         'refresh_timings')
    runs = [101, 100]
    data_file.write_text(json.dumps(
        {'schema_version': 2, 'target_cell_weight': 25.0,
         'max_cells': 15, 'units': 'reference-multiples',
         'measured_from': '300', 'runs': 1,
         'suite_weights': {'test_a.py': 1.0}}) + '\n', encoding='utf-8')
    git_index(repository, 'init', '-q')
    git_index(repository, 'add', '--', '.github/suite-timings.json')
    git_index(repository, '-c', 'user.name=base',
               '-c', 'user.email=base@example.invalid',
               'commit', '-q', '-m', 'base')
    # What the refresh left behind: the same file re-derived from two
    # other runs, unstaged, which is the state the commit step runs on.
    data_file.write_text(json.dumps(
        {'schema_version': 2, 'target_cell_weight': 25.0,
         'max_cells': 15, 'units': 'reference-multiples',
         'measured_from': ','.join(str(run) for run in runs),
         'runs': len(runs),
         'suite_weights': {'test_a.py': 2.0}}) + '\n', encoding='utf-8')
    (repository / 'refreshed-subject.txt').write_text(
        refresh.commit_message(runs) + '\n', encoding='utf-8')
    home = Path(tmp) / 'home'
    home.mkdir()
    run_workflow_script(
        repository, '\n'.join(through),
        {'HOME': str(home), 'RATCHET_SSH_KEY': 'not-a-key',
         'GITHUB_STEP_SUMMARY': str(Path(tmp) / 'summary.md'),
         'REPO': 'example/example'})
    subject = git_output(repository, 'log', '-1', '--pretty=%s')
    named = set(re.findall(r'\d+', subject))
    assert named == {str(run) for run in runs}, subject
    assert 'ci: refresh suite timings from run' in subject, subject
    assert json.loads(data_file.read_text(encoding='utf-8'))[
        'measured_from'] == ','.join(str(run) for run in runs)


def test_the_shipped_file_describes_the_tree_it_plans(tmp):
    """The other half of the tripwire, and the half that was missing.

    `test_timed_refresh.py` checks that the shipped file's own numbers
    hold the balance margin they name. That file satisfied it while
    recording 28 of the tree's 326 suites, because a total that is
    wrong by 3.15x is balanced with itself: the check reads the file
    against ITSELF and cannot see that it describes a corner of the
    tree.

    So this reads the file against the TREE. Every suite the file
    claims -- a recorded weight or a name in its own coverage clause --
    is a suite the tree holds, and the coverage guard accepts the plan
    those weights make. Both fail on the file that shipped: 28
    recorded, 288 estimated, and names it never heard of.

    A tree suite that arrived AFTER the write is deliberately outside
    it: the clause is about the runs, and the file is recomputed rather
    than parsed. Asserting the other way would fail the day this
    branch's own new suite was added, which is the state the branch is
    driving toward.
    """
    planner = _util.load(ROOT / 'scripts' / 'ci' / 'plan_timed_matrix.py',
                         'plan_timed_matrix')
    data = planner.read_timings(ROOT / '.github' / 'suite-timings.json')
    names = set(planner.suite_names(ROOT))
    _count, _total, listed = unmeasured_names(data['basis'])
    unknown = sorted((set(data['suite_weights']) | set(listed)) - names)
    assert not unknown, unknown
    # The guard is the same chokepoint the planner's CLI calls, so this
    # is a refusal on the shipped file rather than a restatement of the
    # arithmetic: a 28-weight file raises here. Its own anti-vacuity
    # control is `test_a_file_whose_weight_is_mostly_estimated_is_a_
    # named_refusal` in the planner suite, which drives this same
    # chokepoint on a narrowed file -- a control that `assert listed`
    # never was, since it failed the day the file covered the tree.
    planner.verify_measured(ROOT, data)


def test_the_commit_message_names_the_runs_the_file_records(tmp):
    """The subject and `measured_from` are rendered from ONE value.

    They did not have to be. Commit `eed3ae9e` is titled "ci: refresh
    suite timings from run 36318864740" and the file it wrote records
    `measured_from: 36310409594`, because the workflow built its
    subject from `${{ github.run_id }}` -- the REFRESH workflow's own
    run -- while the refresher recorded the `tests` run it measured.
    Two different runs, spelled as if they were one, and nothing
    compared them: the refresher is what writes `measured_from`, and
    the workflow is what writes the subject, and each was right about
    its own value.

    So the refresher now renders the subject too, from the same run
    list and through the same join as the field, and writes it where
    the workflow can read it. The assertion is on the two strings
    agreeing, over a THREE-run sample, because a one-run sample cannot
    tell a shared rendering from a coincidence.
    """
    root = Path(tmp) / 'runs'
    for run_id, seconds in ((30, 4.0), (29, 6.0), (28, 8.0)):
        _write_run(root, run_id, {'cell-01': {'test_a.py': seconds}})
    message = Path(tmp) / 'subject.txt'
    text, _code, err = _drive(tmp, root, {'test_a.py': 2.0},
                              message_file=message)
    written = json.loads(text)
    assert written['measured_from'] == '30, 29, 28', written
    subject = message.read_text(encoding='utf-8')
    refresher = _util.load(ROOT / 'scripts' / 'ci' / 'refresh_timings.py',
                           'refresh_timings')
    assert subject.strip() == refresher.commit_message([30, 29, 28]), subject
    # The property, not the spelling: every run the file records is
    # named in the subject, and nothing else is.
    for run_id in written['measured_from'].split(', '):
        assert run_id in subject, (run_id, subject)
    assert '36318864740' not in subject, subject
    assert 'wrote' in err, err


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='timedcover_')


if __name__ == '__main__':
    raise SystemExit(main())
