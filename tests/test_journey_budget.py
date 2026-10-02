#!/usr/bin/env python3
"""Contracts for the journey-cost ratchet's RECORDED DOCUMENT: what the
committed artefact may hold, which shapes its schema refuses, and the
identity fields a count is only comparable on.

Its own half of a module that held the policy, the gates and the command
line together, because the other two have outgrown what one test file may
hold. Nothing here asserts a wall-clock margin or a timing bound, because a
count is a number this repository must not write down: the runner it was
measured on is not the runner the next run lands on."""
import io
import contextlib
import subprocess
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    ARTIFACT,
    ROOT,
    _report_file,
    _util,
    budget_document,
    journeys,
    line_endings,
    measured_report,
    recorded_document,
    recorded_maps,
)


def test_the_committed_artefact_is_the_canonical_rendering(tmp):
    del tmp
    policy = _journey_contract.policy()
    document = policy.load(ARTIFACT)
    canonical = policy.render(document)
    committed = line_endings(ARTIFACT.read_bytes())
    assert canonical == committed, (
        'the committed artefact is not what render() writes for it, so the '
        'file is hand-edited or out of date')
    # The control that keeps the folding above from making this vacuous: a
    # CRLF checkout of the same content passes, and an edit to a count does
    # not. Without both, "compare the folded bytes" could be satisfied by
    # folding everything until nothing differed.
    assert line_endings(canonical.replace(b'\n', b'\r\n')) == canonical
    assert line_endings(committed.replace(b'"mcp-exec"', b'"mcp-exec "')) \
        != canonical


def test_the_artefact_names_exactly_the_journeys_that_exist(tmp):
    del tmp
    policy = _journey_contract.policy()
    document = policy.load(ARTIFACT)
    names = journeys().NAMES
    assert sorted(document['journeys']) == sorted(names), (
        'the budget and the journey set disagree: '
        f'{sorted(document["journeys"])} against {sorted(names)}')
    assert policy.stale(document, names) == []
    # The budget is recorded whole or not at all. A journey left carrying
    # no count beside others that carry one is the `unmeasured` false green
    # reached by omission rather than by a runner that could not count: the
    # gate compares the recorded ones and says nothing about the rest.
    missing = policy.unrecorded(document, names)
    if missing:
        assert len(missing) == len(names), (
            f'the budget is half recorded — {len(names) - len(missing)} of '
            f'{len(names)} journeys carry a count and these carry none, so '
            f'the gate never compares them: {missing}')
    else:
        unmeasured = [name for name in names
                      if not isinstance(document['journeys'][name], int)
                      or isinstance(document['journeys'][name], bool)
                      or document['journeys'][name] <= 0]
        assert not unmeasured, (
            f'a recorded count is not a positive integer: {unmeasured}')


def test_the_artefact_is_its_own_file_and_a_tracked_one(tmp):
    del tmp
    policy = _journey_contract.policy()
    assert policy.ARTIFACT == ARTIFACT, policy.ARTIFACT
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '--error-unmatch',
         '.github/journey-budget.json'],
        capture_output=True, text=True, check=False)
    assert listed.returncode == 0, (
        'the artefact is not tracked, so a deny-by-default ignore file is '
        f'holding it out: {listed.stderr.strip()}')
    shared = json.loads(
        (ROOT / '.github' / 'ci-thresholds.json').read_text(encoding='utf-8'))
    assert not [key for key in shared if 'journey' in key], (
        'a journey baseline reached the closed thresholds document, whose '
        'normalise() refuses any field it does not own')


def test_the_artefact_records_the_threads_each_journey_excludes(tmp):
    """A count that drops a background thread means something the count
    alone cannot say, so the roles are recorded beside it.

    The shape is a per-journey map of role names, and every wrong shape is
    refused rather than coerced: a journey excluded nothing, a role the
    profiler never produces, and a repeated role are three different ways
    for the field to say something the measurement did not do.
    """
    del tmp
    policy = _journey_contract.policy()
    document = budget_document()
    good = {'command-round-trip': ['front-end-import', 'uvicorn-serve'],
            'dashboard-fanout': ['front-end-import', 'uvicorn-serve'],
            'mcp-exec': ['front-end-import']}
    document['excluded_threads'] = good
    assert policy._validated(document) is document
    for bad in ([], 'front-end-import', {'mcp-exec': []},
                {'mcp-exec': ['no-such-thread']},
                {'mcp-exec': ['front-end-import', 'front-end-import']},
                {'no-such-journey': ['front-end-import']}):
        document = budget_document()
        document['excluded_threads'] = bad
        try:
            policy._validated(document)
        except ValueError:
            continue
        raise AssertionError(f'the schema accepted excluded_threads {bad!r}')


def test_a_count_measured_over_different_threads_is_not_compared(tmp):
    """The same rule as a toolchain that moved, for the same reason.

    Two counts that differ only in which threads were dropped out are two
    quantities; comparing them would measure the difference between them
    rather than the code.
    """
    del tmp
    policy = _journey_contract.policy()
    applied = {'command-round-trip': ['front-end-import', 'uvicorn-serve'],
               'dashboard-fanout': ['front-end-import', 'uvicorn-serve'],
               'mcp-exec': ['front-end-import']}
    assert policy.exclusion_diff(applied, applied) == {}
    assert policy.exclusion_diff(applied, None) != {}
    # Order is not meaning: the same roles in another order are the same gate.
    reversed_roles = {name: list(reversed(roles))
                      for name, roles in applied.items()}
    assert policy.exclusion_diff(applied, reversed_roles) == {}
    moved = dict(applied, **{'mcp-exec': ['front-end-import',
                                          'uvicorn-serve']})
    differs = policy.exclusion_diff(applied, moved)
    assert set(differs) == {'mcp-exec'}, differs
    assert differs['mcp-exec'][0] == ['front-end-import']


def test_the_artefact_schema_is_closed(tmp):
    del tmp
    policy = _journey_contract.policy()
    # 'wall-clock' is a quantity the ratchet does not defend at all, and
    # 'syscalls' is one the counters module measures and never gates on. A
    # recorded count denominated in either would be a number nothing holds.
    for over in ({'unknown': 1}, {'counter': 'wall-clock'},
                 {'counter': 'syscalls'},
                 {'tolerance_pct': -1}, {'journeys': []},
                 {'schema_version': 2},
                 {'journeys': {'command-round-trip': -5}},
                 {'journeys': {'command-round-trip': 'many'}}):
        document = budget_document()
        document.update(over)
        try:
            policy._validated(document)
        except ValueError:
            continue
        raise AssertionError(f'the schema accepted {sorted(over)}')


# ─── the toolchain identity a count depends on ───────────────────────────────


def test_the_artefact_carries_the_toolchain_a_count_depends_on(tmp):
    """The identity is present, complete, and exactly the three things.

    The shape is the invariant, not the emptiness. A block with a field
    missing is a defect that makes a moved toolchain read as a match on
    that field, so each must be a non-empty string and the set must be
    exactly what a count depends on and no code change controls.
    """
    del tmp
    policy = _journey_contract.policy()
    document = policy.load(ARTIFACT)
    recorded = document['toolchain']
    assert sorted(recorded) == sorted(policy.journey_counters
                                      .TOOLCHAIN_FIELDS), recorded
    present = {field: seen for field, seen in recorded.items() if seen}
    if present:
        # Recorded whole or not at all: a field left null beside fields
        # that are recorded compares unequal to every measured value, so a
        # toolchain that moved on it would read as a match.
        assert sorted(present) == sorted(recorded), (
            'the identity is half recorded, and a null field beside recorded '
            'ones reads as a match on every value: '
            f'{sorted(set(recorded) - set(present))}')
        for field, seen in sorted(present.items()):
            assert isinstance(seen, str) and seen.strip(), (
                f'the recorded {field} is not a non-empty string, so a '
                f'toolchain that moved on it would read as a match: {seen!r}')
    for over in ({'toolchain': []}, {'toolchain': {'cpython': '3.13'}},
                 {'toolchain': {'python': 31315}},
                 {'toolchain': {'python': '', 'valgrind_version': 'v',
                                'runner_image': 'i'}}):
        bad = budget_document()
        bad.update(over)
        try:
            policy._validated(bad)
        except ValueError:
            continue
        raise AssertionError(f'the schema accepted {over}')


# ─── the bands, and every shape the schema refuses ───────────────────────────


def test_the_bands_a_count_measured_under_are_recorded(tmp):
    """`excluded_threads` records the roles; the `Ir` numbers that put a
    thread IN a role are recorded beside them, so moving a threshold is a
    change the gate can see."""
    policy = _journey_contract.policy()
    threads = _journey_contract.threads()
    assert threads.BANDS == {'front-end-import': 1_000_000_000,
                             'uvicorn-serve': 10_000_000,
                             'request': 1_000}, threads.BANDS
    document = recorded_document()
    assert document['thread_bands'] == threads.BANDS, document['thread_bands']
    # The recorded artefact carries a threshold the CODE no longer uses,
    # which is what moving one looks like from the gate's side: the
    # measurement is the module's own table, so the artefact is the only
    # thing that can differ.
    document['thread_bands'] = dict(
        threads.BANDS, **{'front-end-import': 100_000_000})
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    spoken = io.StringIO()
    with contextlib.redirect_stdout(spoken):
        code = policy.main([
            'check', '--artifact', str(artifact),
            '--measurements', str(_report_file(tmp, {}, recorded_maps()))])
    assert code == 0, spoken.getvalue()
    said = spoken.getvalue()
    assert 'thread bands changed' in said, said
    assert ('front-end-import: recorded 100000000, measured 1000000000'
            in said), said


def test_every_shape_the_schema_refuses_is_still_refused(tmp):
    """The table is shared, so one suite reads it and every row must die.

    Each row names the exact refusal, which is what makes a plausible
    simplification of the validator — a band of any size, a sha of any
    shape — fail here rather than in a later run.
    """
    policy = _journey_contract.policy()
    for document, fragment in _journey_contract.artifact_shapes():
        try:
            policy._validated(document)
        except ValueError as error:
            assert fragment in str(error), (fragment, error)
            continue
        raise AssertionError(f'the schema accepted {document!r}')
    for path, fragment in _journey_contract.unreadable_artifacts(tmp):
        try:
            policy.load(path)
        except ValueError as error:
            assert fragment in str(error), (path, error)
            continue
        raise AssertionError(f'load accepted {path.name}')


# ─── what a half-recorded budget reports ────────────────────────────────────


def test_a_budget_that_names_no_counter_says_no_count_was_compared(tmp):
    """An artefact before its first baseline names no counter, and a run
    that finds that says so rather than reporting a green.

    Nothing can be denominated, so every recorded journey is unmeasured —
    which is the refusal, not a pass — and the line that says which half of
    the budget is missing is printed beside the refusal that follows it. The
    OTHER state must not be printed with it: a budget that names a counter
    has no missing tolerance, and reporting both tells a reader to fix
    something that is already there.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = recorded_document()
    document['counter'] = None
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    assert policy.load(artifact)['counter'] is None, (
        'the rendering did not carry a null counter, so this control would '
        'be measuring a document the artefact cannot hold')
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(measured_report(
        {name: 1000 for name in names})), encoding='utf-8')
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements)])
    assert code == 1, 'a budget naming no counter measured nothing'
    said = out.getvalue()
    assert 'the journey budget names no counter yet, so no count is ' \
           'compared' in said, said
    assert 'no tolerance yet' not in said, (
        f'a missing counter was reported as a missing tolerance too: {said}')
    refused = err.getvalue()
    assert 'unmeasured:' in refused, refused
    assert policy.UNMEASURED_REMEDY in refused, (
        'the refusal for a budget that measured nothing named no remedy: '
        f'{refused}')


def test_a_budget_with_no_tolerance_compares_against_the_recorded_count(tmp):
    """A null tolerance leaves zero headroom, and the run says which state
    it is in rather than reading a movement as nothing.

    With no tolerance the budget IS the recorded count, so a count one above
    it is over and the refusal has to carry both numbers — the measured
    count and the budget it was measured against. That is the arithmetic the
    reported line exists for: reading the null as a percentage that happens
    to be large passes every measurement the repository will ever take.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = recorded_document()
    document['tolerance_pct'] = None
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    assert policy.load(artifact)['tolerance_pct'] is None, (
        'the rendering did not carry a null tolerance, so this control '
        'would be measuring a document the artefact cannot hold')
    for measured, expected in (
            ({name: 1000 for name in names}, 0),
            (dict({name: 1000 for name in names}, **{names[0]: 1001}), 1)):
        measurements = Path(tmp) / f'at-{measured[names[0]]}.json'
        measurements.write_text(json.dumps(measured_report(measured)),
                                encoding='utf-8')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(measurements)])
        assert code == expected, (measured[names[0]], out.getvalue())
        said = out.getvalue()
        assert ('the journey budget names perf-instructions but no tolerance '
                'yet' in said), said
        assert 'zero headroom' in said, said
        assert 'names no counter yet' not in said, (
            f'a missing tolerance was reported as a missing counter too: '
            f'{said}')
        if not expected:
            continue
        refused = err.getvalue()
        assert 'over:' in refused, refused
        assert '1001' in refused and '1000.0' in refused, (
            'the refusal must name the measured count and the zero-headroom '
            f'budget it was measured against: {refused}')


def test_a_recorded_journey_the_set_no_longer_has_is_reported(tmp):
    """A count recorded for a journey nothing runs is reported, not compared.

    The name is not in the journey set, so nothing measures it and nothing
    compares it, and a run that said nothing about it would leave a rule in
    the artefact that reads like one being enforced. The next tighten drops
    it; until then the report has to name it.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    gone = 'a-journey-nobody-runs'
    document = recorded_document()
    document['journeys'][gone] = 5000
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    assert policy.stale(policy.load(artifact), names) == [gone], (
        'the rendering did not carry the stale journey, so this control '
        'would be measuring a document the artefact cannot hold')
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(measured_report(
        {name: 1000 for name in names})), encoding='utf-8')
    spoken = io.StringIO()
    with contextlib.redirect_stdout(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements)])
    assert code == 0, spoken.getvalue()
    said = spoken.getvalue()
    assert (f'recorded but the journey set no longer has them: {gone}'
            in said), said
    # A journey recorded and measured is not either of the two states this
    # report can name, and printing it under one of them is a reader sent
    # to fix a budget that is fine.
    assert 'unrecorded, reported and passing' not in said, said


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybudget_')


if __name__ == '__main__':
    raise SystemExit(main())
