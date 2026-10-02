#!/usr/bin/env python3
"""The journey-cost ratchet's COMMAND LINE: what each subcommand measures,
what it writes, and what it does when there is no measurement to read.

Its own module because the recorded document and the gates are two others:
this is the surface a workflow step calls, and the three are three subjects.
The measurement itself is stubbed throughout — a journey costs what the
machine it ran on costs, so a control that measured one would be asserting
a number this repository must not write down, and a run whose counts vary
per runner is a control that fails on one and passes on the next.

The `rebaseline` half of this command lives in `test_journey_rebaseline.py`;
this is `probe`, `measure` and `check`.
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
    summaries,
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


def _with_a_refused_residual(names, refused):
    """A measurement that resolved every journey but `refused`.

    The refusal carries the journey's own numbers, so what `check` reports
    is the sentence the counter produced rather than a flag it inferred.
    """
    report = measured_report({name: 1000 for name in names})
    entry = report['counters']['perf-instructions']
    entry['journeys'].pop(refused)
    entry['refused'] = {
        refused: f'the {refused} journey measured [2000] instructions net '
                 '[-9000] against a startup-only baseline of 7000 and a '
                 'bridge-only baseline of 4000, so its own work is smaller '
                 'than the fixed background it shares'}
    return report


def test_a_refused_residual_fails_the_check_when_a_count_is_recorded(tmp):
    """A count the run could not compute is never a pass.

    The journey is absent from the measured counts, so a check that only
    asked "is this count over budget?" would find no count, compare
    nothing and report a green — which is the exact false green the
    unmeasured gate exists against, arriving through the other door. So a
    refused residual on a journey the budget DOES hold a count is a named
    failure, and the run's own sentence is what it reports.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    refused = names[0]
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    counts = Path(tmp) / 'counts.json'
    counts.write_text(json.dumps(_with_a_refused_residual(names, refused)),
                      encoding='utf-8')
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(counts)])
    assert code == 1, (
        'a journey whose residual was refused passed the check against a '
        f'recorded count, having measured none: {out.getvalue()} '
        f'{err.getvalue()}')
    said = err.getvalue()
    assert refused in said, said
    assert '-9000' in said, (
        f'the refusal must carry the run\'s own numbers, which are what a '
        f'reader needs and what the pull-request body has to quote: {said}')
    assert 'unmeasured' not in said, (
        f'a refused residual reported as an unmeasured one, which reads as '
        f'the counter missing rather than the journey unresolvable: {said}')


def test_a_refused_residual_reports_unrecorded_when_no_count_is_held(tmp):
    """A dropped journey is measured, refused, and passes — loudly.

    The budget deliberately holds no count for it, so there is nothing to be
    over budget against and nothing to compare. The journey still RAN and
    still rendered, so a crash or a shape change still surfaces through its
    sha; what it does not do is go quietly, and the run says its name on
    stdout the way every other unrecorded journey is announced.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    refused = names[0]
    document = recorded_document()
    document['journeys'][refused] = None
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    counts = Path(tmp) / 'counts.json'
    counts.write_text(json.dumps(_with_a_refused_residual(names, refused)),
                      encoding='utf-8')
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(counts)])
    assert code == 0, (
        'a journey the budget does not hold failed a check it is not part '
        f'of: {out.getvalue()} {err.getvalue()}')
    assert f'unrecorded, reported and passing: {refused}' in out.getvalue(), (
        f'the journey did not go quietly: {out.getvalue()}')
    assert err.getvalue() == '', err.getvalue()


def test_a_refused_journey_reaches_the_summary_with_its_remedy(tmp):
    """The remedy for a refused residual is where a reader learns `--drop`.

    stderr is collapsed by default, so the step summary is the only place a
    person finds out what to do about a run this gate refuses — and for this
    kind that remedy names a command nothing else mentions. Removing the line
    that writes it is invisible from the exit status, which is why it needs a
    control rather than a glance.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    refused = names[0]
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    counts = Path(tmp) / 'counts.json'
    counts.write_text(json.dumps(_with_a_refused_residual(names, refused)),
                      encoding='utf-8')
    with summary_file(tmp, 'refused.md') as summary:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(counts), '--summary'])
        said = summary.read_text(encoding='utf-8')
    assert code == 1, out.getvalue()
    # The table row says the journey could not be resolved; the REMEDY is
    # what the line that writes it adds, and nothing else on the summary
    # names `--drop`. Asserting the word "unresolved" here would be
    # asserting a string no output prints.
    assert 'could not resolve it' in said, (
        'the refused journey is absent from the step summary, so the reader '
        f'never learns what kind of refusal it was: {said}')
    assert policy.UNRESOLVED_REMEDY in said, (
        'the summary names the refusal and no remedy for it: ' + said)
    assert '--drop' in said, (
        'the remedy for this refusal is the only place any reader learns the '
        f'command exists, and it is not in the summary: {said}')


def test_the_summary_remedies_come_in_the_report_s_order(tmp):
    """The step summary lists what a reader must act on, in one order.

    stderr is collapsed by default, so the summary is where a person meets
    a run this gate refuses — and the log beside it lists the same kinds.
    Three hand-written `if`s each naming a kind were free to print them in
    a different order from the report, and a reader comparing the two had
    both orders to hold in their head for no gain.

    So the order is `found`'s, which is the report's, and this drives a run
    that refuses on EVERY kind at once — the only shape where an order is
    observable at all, because one kind's remedy beside another's says
    nothing.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    document = recorded_document(tolerance_pct=0.5)
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    counts = Path(tmp) / 'counts.json'
    report = measured_report({name: 5000 for name in names})
    entry = report['counters']['perf-instructions']
    unresolved, unmeasured = names[0], names[1]
    entry['journeys'].pop(unresolved)
    entry['journeys'].pop(unmeasured)
    entry['refused'] = {
        unresolved: 'its own work is smaller than the background it shares'}
    counts.write_text(json.dumps(report), encoding='utf-8')
    with summary_file(tmp, 'every-kind.md') as summary:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = policy.main(['check', '--artifact', str(artifact),
                                '--measurements', str(counts), '--summary'])
    said = summary.read_text(encoding='utf-8')
    assert code == 1, out.getvalue()
    reported = [line.split(':', 1)[0] for line in err.getvalue().split('\n')
                if line.startswith(('over:', 'unmeasured:', 'unresolved:'))]
    assert reported == ['over', 'unmeasured', 'unresolved'], reported
    # Every remedy reaches the summary, and the two that are different
    # strings are distinguishable in it — an order control that could not
    # tell them apart would pass on any order.
    assert policy.UNMEASURED_REMEDY in said, said
    assert policy.UNRESOLVED_REMEDY in said, said
    over = summaries().rebaseline_lines()[0]
    assert over in said, (over, said)
    places = [said.index(over),
              said.index(policy.UNMEASURED_REMEDY),
              said.index(policy.UNRESOLVED_REMEDY)]
    assert places == sorted(places), (places, said)


def test_a_drop_inside_the_tolerance_is_not_recorded(tmp):
    """A journey's own run-to-run spread is not a cheaper journey.

    The recorded count is what the NEXT check compares against, and the
    tolerance is the band around it that a measurement of unchanged code
    lands inside. Recording a count from inside that band moves the band
    down by the drop, so the run after it — unchanged code, the same
    spread — measures the count that was already recorded and is now over
    it. That is issue 1484: a single-round tighten put `screenshot` 0.51%
    below a run that measured 0.35% above it.

    So a drop is recorded only when it is WIDER than the band: the budget
    the new record produces then still sits below the count the previous
    run measured, so that run passes against it. The tolerance is the
    journey's own, read from the one owner of it, so a journey named in
    `tolerances` is held to its own band and every other one to the
    document default.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    wide, exact = names[0], names[1]
    # The shape the measured values land in: one journey held to a
    # tolerance above the document default, its neighbour on the default.
    document = recorded_document(tolerance_pct=0.5, tolerances={wide: 2.0})
    assert policy.tolerance_of(document, wide) == 2.0, document
    assert policy.tolerance_of(document, exact) == 0.5, document
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    before = artifact.read_bytes()

    # A tenth of the default band, so it is inside every journey's own.
    noise = {name: document['journeys'][name] * 999 // 1000 for name in names}
    assert policy.tightened(noise, document, names) is None, (
        'a drop inside the tolerance was recorded, so the next unchanged '
        'run is measured against a count this one measured below: '
        f'{policy.tightened(noise, document, names)}')
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(measured_report(noise)),
                            encoding='utf-8')
    spoken = io.StringIO()
    with contextlib.redirect_stdout(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements), '--tighten'])
    assert code == 0, spoken.getvalue()
    assert artifact.read_bytes() == before, (
        'a run that recorded nothing still rewrote the artefact')

    # The other direction, or the budget has frozen: a drop past the band
    # is a real saving and is still recorded, and only that journey moves.
    halved = dict(noise, **{wide: document['journeys'][wide] // 2})
    artifact.write_bytes(before)
    measurements.write_text(json.dumps(measured_report(halved)),
                            encoding='utf-8')
    with contextlib.redirect_stdout(spoken):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements), '--tighten'])
    assert code == 0, spoken.getvalue()
    written = json.loads(artifact.read_text(encoding='utf-8'))['journeys']
    assert written == dict(document['journeys'], **{wide: halved[wide]}), (
        'a drop past the tolerance was refused, or a journey inside it was '
        'recorded anyway, so the budget moved without a saving')


def test_a_tighten_writes_no_budget_the_next_check_would_refuse(tmp):
    """`check` and `check --tighten` must agree about the same measurement.

    Probed before this control existed: a run whose measurement refused one
    journey tightened the other six to 1000 → 10 and printed "tightened the
    journey budget", and the `check` run after it exited 1 on `unresolved` —
    the same measurement, read twice, with the two readers disagreeing about
    whether the artefact it wrote is a valid one.

    The argument is the one the `over` refusal already makes: a budget the
    next check cannot accept should not be written by a command that reports
    success. An unresolved journey is not a RISE, and the arithmetic is
    right to skip its row — that part of the counter-argument holds. It is
    the WRITE that has to give.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    refused = names[0]
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    before = artifact.read_bytes()
    counts = Path(tmp) / 'counts.json'
    # A journey that MEASURED CHEAPER than recorded, so without the refusal
    # this write is a real one: the reviewer measured `command-round-trip
    # 1000 -> 10` and the artefact changing under it.
    report = _with_a_refused_residual(names, refused)
    report['counters']['perf-instructions']['journeys'][names[1]] = {
        'min': 10, 'max': 10, 'median': 10, 'spread': 0, 'raw': 10}
    counts.write_text(json.dumps(report), encoding='utf-8')
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(counts), '--tighten'])
    assert code != 0, (
        'a tighten wrote a budget the next check refuses, and reported '
        f'success: {out.getvalue()}')
    assert artifact.read_bytes() == before, (
        'a refused tighten wrote the artefact anyway')
    said = err.getvalue()
    assert refused in said, (
        f'the refusal does not name the journey it is about: {said}')
    assert 'nothing is tightened' in said or 'tightened nothing' in said, said

    # And the check on that same artefact still refuses, so the two readers
    # agree rather than one having gone quiet.
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        assert policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(counts)]) == 1, (
            'the artefact the tighten refused is one the check accepts')


def test_a_tighten_writes_nothing_the_check_refuses_over_a_missing_journey(
        tmp):
    """The same disagreement, one key over: a journey with no count at all.

    `unresolved` is a journey the run refused and `unmeasured` is one it
    never counted, and only the first was guarded. So a measurement that
    simply lacked a journey let the tighten write six lowered counts and
    print success, and the `check` on the artefact it wrote exited 1 on
    `unmeasured`. Same run, same measurement, opposite answers.

    The sentence that settles it is the same one and is true of both: a
    budget the next check cannot accept is not one this command may write.
    `tightened()` skips the missing journey's row for the same reason it
    skips a refused one.

    The remedies stay DISTINCT, because a reader who hits one has to be
    told the thing that fixes that one — and this control pins both, so
    hoisting the guard cannot quietly collapse them into one sentence.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    missing = names[0]
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    before = artifact.read_bytes()
    # No `refused` entry at all: the counter simply produced no count for
    # this journey, which is the other way a journey can be missing.
    counts = {name: 1000 for name in names if name != missing}
    counts[names[1]] = 10
    report = measured_report(counts)
    measurements = Path(tmp) / 'counts.json'
    measurements.write_text(json.dumps(report), encoding='utf-8')

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements), '--tighten'])
    assert code != 0, (
        'a tighten wrote a budget the next check refuses because of an '
        f'unmeasured journey, and reported success: {out.getvalue()}')
    assert artifact.read_bytes() == before, (
        'a refused tighten wrote the artefact anyway')
    said = err.getvalue()
    assert missing in said, said
    assert policy.UNMEASURED_REMEDY in said, (
        'the refusal for an unmeasured journey must name the remedy for an '
        f'unmeasured journey, not the one for a refused residual: {said}')
    assert policy.UNRESOLVED_REMEDY not in said, (
        'the two remedies are distinct findings and must not be collapsed '
        f'into one sentence: {said}')
    # The arm's OWN clause, which neither assertion above reaches.
    assert (f'the run counted no journey under perf-instructions for '
            f'{missing}' in said), (
        'the unmeasured refusal must say what the run did instead of '
        f'counting this journey, under the counter it used: {said}')
    # And the check on the artefact the tighten refused still refuses it.
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        assert policy.main(['check', '--artifact', str(artifact),
                            '--measurements', str(measurements)]) == 1, (
            'the artefact the tighten refused is one the check accepts')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeycli__')


if __name__ == '__main__':
    raise SystemExit(main())
