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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeycli_')


if __name__ == '__main__':
    raise SystemExit(main())
