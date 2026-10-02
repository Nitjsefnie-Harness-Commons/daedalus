#!/usr/bin/env python3
"""The journey-cost ratchet's COMMAND LINE: what each subcommand measures,
what it writes, and what it does when there is no measurement to read.

Its own module because the recorded document and the gates are two others:
this is the surface a workflow step calls, and the three are three subjects.
The measurement itself is stubbed throughout — a journey costs what the
machine it ran on costs, so a control that measured one would be asserting
a number this repository must not write down, and a run whose counts vary
per runner is a control that fails on one and passes on the next.
"""
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    ROOT,
    journeys,
    measured_report,
    planting,
    recorded_document,
    summary_file,
)


def test_the_probe_prints_the_facts_it_measured(tmp):
    """What this runner allows is measured, and the printed JSON is that.

    The probe is the diagnosis a step reads before it decides which counter
    to gate on, so the document has to be the measurement rather than a
    rendering of it: a field dropped, renamed or reworded away is a
    capability the gate downstream cannot read at all.
    """
    del tmp
    policy = _journey_contract.policy()
    found = _journey_contract.probe()
    spoken = io.StringIO()
    with planting(policy.journey_counters, facts=lambda: found):
        with contextlib.redirect_stdout(spoken):
            code = policy.main(['probe'])
    assert code == 0, spoken.getvalue()
    assert json.loads(spoken.getvalue()) == found, (
        'the probe printed something other than the facts it measured: '
        f'{spoken.getvalue()} against {found}')


def test_the_probe_diagnoses_a_runner_that_counted_nothing(tmp):
    """The diagnosis block is written where NO counter could be selected.

    That block is the reason `probe --summary` exists: stderr is collapsed
    by default, so a runner where perf and valgrind were both refused leaves
    a step summary that says neither what it found nor why nothing was
    chosen. The other arm matters as much — on a runner that DID select a
    counter the same block is a page of detail about an environment that
    turned out to be fine, so it is written only where it diagnoses.
    """
    policy = _journey_contract.policy()
    found = _journey_contract.probe()
    # An explicit null, not an absent key: a runner that selected nothing
    # reports the field as null, and the renderer reads it by name.
    blind = dict(found, selected=None)
    with summary_file(tmp, 'blind.md') as summary:
        with planting(policy.journey_counters, facts=lambda: blind):
            with contextlib.redirect_stdout(io.StringIO()):
                code = policy.main(['probe', '--summary'])
        assert code == 0, 'a probe that counted nothing is not a failure'
        said = summary.read_text(encoding='utf-8')
    assert 'Journey counter probe' in said, said
    assert 'What this runner actually allows' in said, said
    # The rows carry the fixture's OWN values, so a summary that named a
    # different tool than the one probed fails here rather than passing on
    # the heading alone.
    assert f"- perf on PATH: `{found['perf_path']}`" in said, said
    assert f"- valgrind --version: `{found['valgrind_version']}`" in said, said
    assert '- counter selected here: `None`' in said, (
        'the whole point of the block is that nothing was selectable, and '
        f'the summary does not say so: {said}')

    with summary_file(tmp, 'usable.md') as summary:
        with planting(policy.journey_counters, facts=lambda: found):
            with contextlib.redirect_stdout(io.StringIO()):
                assert policy.main(['probe', '--summary']) == 0
        assert not summary.exists(), (
            'the diagnosis of a runner that can count nothing was written '
            f'for one that selected {found["selected"]!r}')


def test_measure_writes_its_report_where_it_was_asked_to(tmp):
    """`measure` is the one command that produces what every other reads.

    So the file it writes and the payload it prints have to be one run's
    report — a step reading the artefact and a reader reading the log must
    not see two different measurements — and the measurement has to be taken
    from the checkout and the round count the step configured rather than
    from whatever the defaults happen to be on the runner that happens to
    execute it.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    report = measured_report({name: 900 for name in names})
    calls = []

    def _measure(root, rounds):
        calls.append((Path(root), rounds))
        return report

    target = Path(tmp) / 'counts.json'
    spoken = io.StringIO()
    with planting(policy.journey_counters, measure=_measure):
        with contextlib.redirect_stdout(spoken):
            code = policy.main(['measure', '--out', str(target),
                                '--rounds', '2'])
    assert code == 0, spoken.getvalue()
    assert calls == [(ROOT, 2)], (
        'measure was pointed at another checkout, or at another number of '
        f'rounds, than the step configured: {calls}')
    written = target.read_text(encoding='utf-8')
    assert json.loads(written) == report, written
    assert json.loads(spoken.getvalue()) == report, spoken.getvalue()
    assert spoken.getvalue().strip() == written.strip(), (
        'the printed payload and the written file are two different '
        f'reports: {spoken.getvalue()} against {written}')


def test_a_check_with_no_measurement_file_measures_here(tmp):
    """A check reads a measurement or takes one, and a measurement it took
    is written where the next run will read it.

    `--measurements` naming a file that is not there is a step whose upload
    never arrived: the command measures the checkout it was pointed at
    rather than comparing nothing and calling that a pass. The write-back is
    the other half — the report this run produced lands at the path the step
    named, so the measurement survives the run that produced it instead of
    being a number that only ever existed in the log.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    report = measured_report({name: 1000 for name in names})
    calls = []

    def _measure(root, rounds):
        calls.append((Path(root), rounds))
        return report

    spoken = io.StringIO()
    with planting(policy.journey_counters, measure=_measure):
        with contextlib.redirect_stdout(spoken):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--rounds', '3'])
    assert code == 0, spoken.getvalue()
    assert calls == [(ROOT, 3)], calls
    said = spoken.getvalue()
    assert (f'{len(names)} journeys measured against perf-instructions; '
            'none over budget' in said), said

    absent = Path(tmp) / 'never-uploaded.json'
    with planting(policy.journey_counters, measure=_measure):
        with contextlib.redirect_stdout(io.StringIO()):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(absent)])
    assert code == 0, 'a measurement taken here compares like one read'
    assert len(calls) == 2, calls
    written = json.loads(absent.read_text(encoding='utf-8'))
    assert written == report, (
        'the measurement this run took was not written where the next run '
        f'reads it: {written}')


def _dropped_document(tmp, dropped, widened, **over):
    """A written artefact whose budget holds no count for `dropped`."""
    document = recorded_document(tolerance_pct=0.5,
                                 tolerances={widened: 25.0}, **over)
    document['journeys'][dropped] = None
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(_journey_contract.policy().render(document))
    return artifact


def _rebaseline_over(tmp, artifact, report, *flags):
    """Drive the real command, and return `(code, stdout, stderr)`.

    Stderr is captured as well as stdout because every refusal `run` prints
    goes to stderr — a control that quoted only the other stream reported an
    empty reason for a refusal it had just caused.
    """
    policy = _journey_contract.policy()
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = policy.main(['rebaseline', '--artifact', str(artifact),
                            '--measurements', str(measurements), *flags])
    return code, out.getvalue(), err.getvalue()


def test_a_rebaseline_keeps_the_bounds_it_did_not_ask_to_move(tmp):
    """What a re-baseline may move: the counts, and nothing else.

    Every tolerance — the default and a journey's own — is a bound a person
    set from a measured spread, so a command that carried the counts and
    dropped them would widen every budget by a number nobody measured. The
    journey the artefact holds no count for is the manager's case and has its
    own control below.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    dropped, widened = names[0], names[1]
    artifact = _dropped_document(tmp, dropped, widened)
    report = _journey_contract.fixture_report()
    code, _out, err = _rebaseline_over(
        tmp, artifact, report, '--restore', dropped)
    assert code == 0, err
    written = policy.load(artifact)
    assert written['tolerance_pct'] == 0.5, written['tolerance_pct']
    assert written['tolerances'] == {widened: 25.0}, written.get('tolerances')
    assert written['journeys'][widened] == 950, written['journeys']


def test_a_rebaseline_refuses_to_silently_restore_a_dropped_journey(tmp):
    """A `null` nothing can undo is a policy hole with a green face.

    The journey reports `unrecorded`, passes, and stays that way forever. So a
    measurement that now SEPARATES it is a change of fact, and a command that
    answered it by either restoring the count or leaving the null would be
    choosing between two decisions without saying which. It refuses instead,
    naming the journey and the residual that makes the question a question —
    a residual nobody can read off "it says unrecorded".
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    dropped = names[0]
    artifact = _dropped_document(tmp, dropped, names[1])
    report = _journey_contract.fixture_report()
    row = report['counters']['valgrind-callgrind']['journeys'][dropped]
    assert row['median'] is not None, (
        'the fixture measures no residual for the dropped journey, so this '
        'control is not testing the separable case')
    code, _out, err = _rebaseline_over(tmp, artifact, report)
    assert code != 0, (
        'a measurement that separates the journey a re-baseline was asked to '
        'leave alone was recorded without saying so')
    assert dropped in err, err
    assert str(row['median']) in err, (
        f'the refusal must name the residual that makes it separable: {err}')
    assert '--restore' in err, err
    assert policy.load(artifact)['journeys'][dropped] is None, (
        'a refused re-baseline wrote the artefact anyway')


def test_a_restore_flag_records_only_the_journey_it_names(tmp):
    """Restoring is explicit and per-journey, and a typo is not silent.

    Two arms, and the second is the one that matters: a name the artefact
    does not hold at `null` is refused rather than ignored, because an
    ignored `--restore` and a misspelled one look identical from the command
    line and only one of them does what the person meant.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    dropped = names[0]
    artifact = _dropped_document(tmp, dropped, names[1])
    report = _journey_contract.fixture_report()

    code, _out, err = _rebaseline_over(tmp, artifact, report,
                                       '--restore', names[2])
    assert code != 0, (
        f'--restore {names[2]} named a journey the budget holds a count for, '
        'and was ignored, so a misspelled name does the same as a correct one')
    assert names[2] in err, err
    assert policy.load(artifact)['journeys'][dropped] is None, (
        'a refused re-baseline wrote the artefact anyway')

    code, _out, err = _rebaseline_over(
        tmp, artifact, report, '--restore', dropped, '--restore', names[2])
    assert code != 0, (
        'a correct --restore beside a wrong one was accepted, so half the '
        f'request ran: {err}')
    assert policy.load(artifact)['journeys'][dropped] is None, (
        'a refused re-baseline wrote the artefact anyway')

    code, _out, err = _rebaseline_over(tmp, artifact, report,
                                       '--restore', dropped)
    assert code == 0, err
    written = policy.load(artifact)
    assert written['journeys'][dropped] == 950, written['journeys']
    assert policy.unrecorded(written, names) == [], written['journeys']


def test_a_rebaseline_drops_a_bound_named_for_a_journey_the_set_lost(tmp):
    """A bound for a journey the journey set no longer has goes with it.

    The narrowing this pins is load-bearing and silent either way: the
    recorded document is a legal one, because `_validated_tolerances` only
    asks whether the journeys map carries the name, and a stale journey is
    exactly the one a re-baseline drops. Carrying the bound forward past the
    journey leaves a document whose own schema refuses it — so the command
    that exists to make the artefact current fails on the artefact being
    current, which is a re-baseline nobody can run and a budget nobody can
    re-record. Dropping it is the same fate a stale count has, which is what
    makes the two consistent rather than merely convenient.
    """
    policy = _journey_contract.policy()
    gone = 'a-journey-nobody-runs'
    document = recorded_document(tolerance_pct=0.5, tolerances={gone: 1.0})
    document['journeys'][gone] = 5000
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    assert policy.stale(policy.load(artifact), journeys().NAMES) == [gone], (
        'the rendering did not carry the stale journey, so this control is '
        'not testing what it says it is')
    # Both streams, and the reason is not decoration: every refusal `run`
    # prints goes to stderr, so a control quoting only stdout reported an
    # empty reason for a refusal it had just caused.
    code, out, err = _rebaseline_over(
        tmp, artifact, _journey_contract.fixture_report())
    assert code == 0, (
        'a re-baseline over an artefact naming a journey the set no longer '
        'has refused, so the budget cannot be re-recorded until someone '
        f'hand-edits it: {out}{err}')
    written = policy.load(artifact)
    assert gone not in written['journeys'], written['journeys']
    assert written['tolerances'] == {}, written.get('tolerances')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeycli_')


if __name__ == '__main__':
    raise SystemExit(main())
