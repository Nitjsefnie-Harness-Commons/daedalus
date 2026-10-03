#!/usr/bin/env python3
"""A measurement of fewer than two rounds records no count at all.

Its own file because `test_journey_tighten.py` is within twenty lines of the
700-line ceiling, and because the question here is a different one from that
file's: that one asks which WAY a recorded count may move once it has been
compared, and this one asks whether a measurement that took one draw was ever
a comparison at all.

One draw is an observation; a recorded count is a baseline. CI measures with
`JOURNEY_ROUNDS: "1"`, and on unchanged code one draw has been observed 48%
below the median of three draws of the same journey, so a ratchet that
recorded it would lower the budget for a run that never regressed.

Nothing here asserts a wall-clock margin: the quantity under test is a round
count, which is deterministic.
"""
import contextlib
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _journey_contract  # noqa: E402
from _journey_contract import journeys, measured_report  # noqa: E402

# One count low enough to be a saving past the fixture's ten percent band,
# because a measurement of one round that COULD have tightened is the only
# shape in which the guard is doing anything.
CHEAP = 800


def _on_disk(tmp, report):
    """A recorded budget beside a measurement of `report`, both on disk."""
    policy = _journey_contract.policy()
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(
        _journey_contract.recorded_document()))
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')
    return artifact, measurements


def _tighten(artifact, measurements, tmp):
    """Run one `check --tighten` and report what it did to all three surfaces.

    Returns `(code, out, err, summary)`. The summary is read back off the
    file rather than taken from a return value, because the write this file
    is about is the one thing a refusal's return code cannot show.
    """
    summary = Path(tmp) / 'step-summary.md'
    out, err = io.StringIO(), io.StringIO()
    saved = os.environ.get('GITHUB_STEP_SUMMARY')
    os.environ['GITHUB_STEP_SUMMARY'] = str(summary)
    try:
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = _journey_contract.policy().main(
                ['check', '--artifact', str(artifact),
                 '--measurements', str(measurements), '--tighten'])
    finally:
        os.environ.pop('GITHUB_STEP_SUMMARY', None)
        if saved is not None:
            os.environ['GITHUB_STEP_SUMMARY'] = saved
    said = summary.read_text(encoding='utf-8') if summary.is_file() else ''
    return code, out.getvalue(), err.getvalue(), said


def _cheap(rounds):
    """A measurement every journey measured CHEAP, over `rounds` rounds."""
    names = journeys().NAMES
    report = measured_report({name: CHEAP for name in names})
    if rounds is not None:
        report['rounds'] = rounds
    else:
        report.pop('rounds')
    return report


def test_a_one_round_measurement_records_no_count(tmp):
    """The ruling, in the shape CI reaches it: one draw, a real saving.

    Nothing else about this run is unusual — every journey measured a fifth
    below what it was recorded with, which is a saving past every journey's
    own tolerance and the one thing a tighten exists to record. The only
    thing wrong is that it was seen once. So the artefact stays byte for byte
    what it was, the run succeeds, and both its outputs say which round
    count it took, because a reader who finds a green run with no commit has
    to be able to tell this from a recorded gate that moved.
    """
    artifact, measurements = _on_disk(tmp, _cheap(1))
    before = artifact.read_bytes()
    code, out, err, said = _tighten(artifact, measurements, tmp)
    assert code == 0, (
        'a measurement of one round is not a regression, and exiting nonzero '
        f'reds a required context on every push to main: {err}')
    assert artifact.read_bytes() == before, (
        'one draw was recorded as a baseline, so the budget now sits at one '
        'observation and every later run is measured against it')
    assert '1 round' in out, (
        f'the run must name the round count it took: {out}')
    assert 'not a record' in out, out
    assert '1 round' in said and 'not a record' in said, (
        f'the step summary must name it too: {said!r}')


def test_a_measurement_with_no_round_count_records_nothing(tmp):
    """Absent is not "many", and it is not a number the rule can compare.

    A report that never carried `rounds` — a file an older harness wrote, or
    one hand-assembled — is not evidence of a multi-round measurement, and
    reading its absence as a pass would record a count nothing ever
    confirmed. The non-integer shapes are here for the same reason: `2.5`
    rounds is not two rounds, and the string `"3"` is a count nobody measured.
    """
    for rounds, named in ((None, 'no round count'), ('3', "'3'"),
                          (2.5, '2.5'), (0, '0'), (-1, '-1'), (True, 'True')):
        artifact, measurements = _on_disk(tmp, _cheap(rounds))
        before = artifact.read_bytes()
        code, out, err, _ = _tighten(artifact, measurements, tmp)
        assert code == 0, f'rounds={rounds!r}: {err}'
        assert artifact.read_bytes() == before, (
            f'rounds={rounds!r} recorded a count anyway')
        assert named in out, (
            f'rounds={rounds!r}: the run must say what it read: {out}')
        assert 'not a record' in out, out


def test_a_multi_round_measurement_still_tightens(tmp):
    """The guard is about the round count, not about tightening.

    Three rounds of the same saving record it exactly as they did before the
    guard existed, so the policy in `journey_gates.tightened` is untouched and
    a deliberate multi-round run is still the way a budget follows a journey
    down. A guard that only ever refused would pass every assertion above.
    """
    artifact, measurements = _on_disk(tmp, _cheap(3))
    code, out, err, _ = _tighten(artifact, measurements, tmp)
    assert code == 0, err
    assert 'tightened the journey budget' in out, out
    written = _journey_contract.policy().load(artifact)
    assert written['journeys'] == {name: CHEAP for name in journeys().NAMES}, \
        written['journeys']


def test_a_multi_round_run_still_drops_a_journey_the_set_no_longer_has(tmp):
    """The policy's other half, on a measurement allowed to act at all.

    `tightened` drops a recorded journey the journey set no longer has, and
    that drop needs no comparison and no saving — it is the recorded name
    itself that is gone. So it is the half of the policy a guard placed too
    early would take with it.
    """
    gone = 'a-journey-nobody-runs'
    recorded = _journey_contract.recorded_document()['journeys']
    document = _journey_contract.recorded_document(
        journeys=dict(recorded, **{gone: 5000}))
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(_journey_contract.policy().render(document))
    # LEVEL against every recorded count, so the drop of the stale name is
    # the only thing this run has to write: nothing else may move, which is
    # what makes the second assertion below a control on the drop rather than
    # on the arithmetic that runs beside it.
    report = measured_report({name: 1000 for name in journeys().NAMES})
    report['rounds'] = 3
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')
    code, _, err, _ = _tighten(artifact, measurements, tmp)
    assert code == 0, err
    written = _journey_contract.policy().load(artifact)
    assert gone not in written['journeys'], (
        'a journey the set no longer has survived a tighten that was allowed '
        f'to act: {sorted(written["journeys"])}')
    assert written['journeys'] == recorded, (
        'a journey that is still in the set moved on a run whose only finding '
        f'was the stale one: {written["journeys"]} against {recorded}')


def test_a_one_round_run_still_reports_the_refusals_it_has(tmp):
    """The new guard is a refusal of its own, and it does not shadow the rest.

    Each of these three fires BEFORE it, and each is a red: a run that met a
    rise, or one whose measurement could not resolve a journey, is a run CI
    must stop on. A guard placed ahead of them would turn every one of them
    into the round-count sentence and exit 0, so a genuine regression would
    commit a budget with no red anywhere.
    """
    names = journeys().NAMES
    cases = {}

    over = _cheap(1)
    over['counters']['perf-instructions']['journeys'][names[0]] = {
        'min': 1200, 'max': 1200, 'median': 1200, 'spread': 0, 'raw': 1200}
    cases['over'] = (over, 'is over its budget')

    unmeasured = _cheap(1)
    unmeasured['counters']['perf-instructions']['journeys'].pop(names[0])
    cases['unmeasured'] = (unmeasured, 'the run counted no journey under')

    unresolved = _cheap(1)
    unresolved['counters']['perf-instructions']['journeys'].pop(names[0])
    unresolved['counters']['perf-instructions']['refused'] = {
        names[0]: 'the cdp-result journey printed no record'}
    cases['unresolved'] = (unresolved, 'the run could not resolve')

    for kind, (report, expected) in cases.items():
        artifact, measurements = _on_disk(tmp, report)
        before = artifact.read_bytes()
        code, out, err, _ = _tighten(artifact, measurements, tmp)
        assert code == 1, (
            f'{kind}: a one-round measurement hid the refusal, so the run '
            f'reported success on it: {out}')
        assert expected in err, f'{kind}: {err}'
        assert 'not a record' not in out, (
            f'{kind}: the round-count sentence stood in for the refusal: '
            f'{out}')
        assert artifact.read_bytes() == before, (
            f'{kind}: a refused tighten wrote the artefact anyway')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeytightenrounds_')


if __name__ == '__main__':
    raise SystemExit(main())
