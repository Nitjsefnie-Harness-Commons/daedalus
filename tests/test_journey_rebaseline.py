#!/usr/bin/env python3
"""What the re-baseline WRITES, and the artefact states its two flags act on.

`rebaseline` — `--restore`, `--drop`, and every artefact state and
measurement outcome the pair is given together — driven through the real
command and its real refusals.
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
    journeys,
    recorded_document,
)


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

    Stderr comes back too: every refusal `run` prints goes there, and a
    control quoting only stdout reported an empty reason it had caused.
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

    Every tolerance is a bound a person set from a measured spread, so a
    command that carried the counts and dropped them would widen every
    budget by a number nobody measured. The null-holding journey has its
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

    The journey reports `unrecorded` and passes forever, so a measurement
    that now SEPARATES it is a change of fact: the command refuses, naming
    the journey and the residual that makes the question a question.
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

    A name the artefact does not hold at `null` is refused rather than
    ignored: an ignored `--restore` and a misspelled one look identical from
    the command line.
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


def _separable():
    """A measurement that resolved every journey."""
    return _journey_contract.fixture_report()


def _unresolved(name):
    """The same measurement, with `name` the one journey it could not do.

    A refused residual rather than a missing journey — the count was
    withheld, which makes it a drop's case and not a shape failure's.
    """
    report = _journey_contract.fixture_report()
    entry = report['counters']['valgrind-callgrind']
    entry['journeys'].pop(name)
    entry['refused'] = {
        name: f'the {name} journey measured [2000] instructions net [-9000] '
              'against a bridge-only baseline of 11000 read through its own '
              'exclusion list, so its own work is smaller than the fixed '
              'background it shares and the run cannot separate them'}
    return report


def test_a_drop_writes_the_null_only_when_the_flag_names_the_journey(tmp):
    """The `null` is an explicit decision, never a side effect.

    A routine re-baseline must not write one: it is the one value in the
    artefact that says the budget does not hold a journey, and a command
    that produced it as a byproduct would remove a journey from the budget
    on the next re-baseline of anything at all.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    dropped = names[0]
    document = recorded_document()
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))

    code, _out, err = _rebaseline_over(tmp, artifact, _unresolved(dropped))
    assert code != 0, (
        'a run that could not resolve one journey re-baselined the rest and '
        f'wrote a budget comparing journeys it never measured: {err}')

    code, _out, err = _rebaseline_over(
        tmp, artifact, _separable(), '--drop', names[1])
    assert code != 0, (
        f'--drop {names[1]} wrote a null for a journey the run resolved, '
        f'so the flag is not what decides: {err}')
    assert policy.load(artifact)['journeys'][dropped] == 1000, (
        'a refused re-baseline wrote the artefact anyway')

    code, out, err = _rebaseline_over(tmp, artifact, _unresolved(dropped),
                                      '--drop', dropped)
    assert code == 0, err
    written = policy.load(artifact)
    assert written['journeys'][dropped] is None, written['journeys']
    assert policy.unrecorded(written, names) == [dropped], written['journeys']
    assert dropped in out, (
        f'the command said nothing about what it dropped: {out}')
    # The journey still ran: its sha is what says so.
    assert dropped in written['shas'], written['shas']


def test_a_drop_refuses_a_journey_the_measurement_can_separate(tmp):
    """`--drop` cannot be used to dodge a regression.

    A separable, positive residual is the opposite of the case a drop exists
    for, and the flag is the only thing standing between a count and its
    deletion — so it refuses rather than warns.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    dropped = names[0]
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    before = artifact.read_bytes()
    report = _separable()
    code, _out, err = _rebaseline_over(tmp, artifact, report,
                                       '--drop', dropped)
    median = (report['counters']['valgrind-callgrind']
              ['journeys'][dropped]['median'])
    assert code != 0, (
        f'--drop removed a journey whose own work this run measured at '
        f'{median} instructions, so it can drop a journey that regressed')
    assert dropped in err, err
    assert 'separat' in err, err
    assert artifact.read_bytes() == before, (
        'a refused re-baseline wrote the artefact anyway')


def test_a_drop_or_restore_naming_no_such_journey_is_a_typo_not_a_state(tmp):
    """A name no journey carries is its own failure, with its own words.

    A per-journey flag's likeliest failure is a misspelling, once reported
    as "the budget already holds a count for it" — true of nothing. The two
    are told apart by the journey set, so the refusal names it.
    """
    policy = _journey_contract.policy()
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(recorded_document()))
    for flag in ('--drop', '--restore'):
        code, _out, err = _rebaseline_over(tmp, artifact, _separable(),
                                           flag, 'command-rond-trip')
        assert code != 0, f'{flag} on a misspelled name was accepted'
        assert 'command-rond-trip' in err, err
        assert 'no journey is called' in err, (
            f'{flag} reported a misspelling as a state of the budget: {err}')
    del policy


def test_a_drop_succeeds_against_an_artefact_that_already_dropped_it(tmp):
    """`--drop` has to work in the state it creates.

    The next re-baseline runs against exactly the file a drop leaves — the
    artefact at `null` — and that second invocation is where it was broken:
    the recorded-null refusal ran first, named a measurement separating the
    journey it could not separate, and printed `None` where a number
    belongs. So the artefact starts at `null` here and the flag is asked to
    do the one thing it exists for.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    dropped = names[0]
    document = recorded_document()
    document['journeys'][dropped] = None
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))
    report = _unresolved(dropped)

    code, _out, err = _rebaseline_over(tmp, artifact, report,
                                       '--drop', dropped)
    assert code == 0, (
        f'--drop refused against an artefact that already holds {dropped} at '
        f'null, so the second re-baseline of any dropped journey fails: {err}')
    written = policy.load(artifact)
    assert written['journeys'][dropped] is None, written['journeys']
    assert dropped in written['shas'], written['shas']

    # And the refusal that DOES fire must not say anything the run did not
    # establish. A journey this run resolved has a residual; one it refused
    # has none, and a sentence that prints `None` where a number belongs is
    # a sentence nobody can act on. Both are driven here, from the same
    # already-dropped artefact, so the arm that fires is the one a real
    # second re-baseline would reach.
    resolved = Path(tmp) / 'resolved.json'
    resolved.write_bytes(policy.render(recorded_document()))
    code, _out, err = _rebaseline_over(
        tmp, resolved, _separable(), '--drop', dropped)
    assert code != 0, (
        '--drop recorded a null for a journey this run resolved, so the '
        'refusal it should have raised is not the one raising')
    assert 'None instructions' not in err, (
        f'the refusal names a residual it does not have: {err}')
    assert '950' in err, (
        f'the refusal does not name the residual it does have: {err}')


def test_a_rebaseline_drops_a_bound_named_for_a_journey_the_set_lost(tmp):
    """A bound for a journey the journey set no longer has goes with it.

    The narrowing is load-bearing and silent either way: a stale journey is
    exactly the one a re-baseline drops, and carrying its bound forward
    leaves a document the schema itself refuses — the command that exists
    to make the artefact current failing on the artefact being current.
    Dropping it is the same fate a stale count has.
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


def _drop_matrix(tmp, name):
    """Every artefact state against every flag shape, and what each said.

    Two states (holds a count, holds `null`) crossed with both outcomes and
    the three flag shapes — returned rather than asserted here, because the
    cells are the FIXTURE and the controls below assert over them.
    """
    def report_unresolved():
        report = _journey_contract.fixture_report()
        entry = report['counters']['valgrind-callgrind']
        entry['journeys'].pop(name)
        entry['refused'] = {
            name: f'the {name} journey measured [2000] net [-9000]'}
        return report

    def report_resolved():
        return _journey_contract.fixture_report()

    rows = []
    for state in ('a count', 'null'):
        document = recorded_document()
        if state == 'null':
            document['journeys'][name] = None
        written = _journey_contract.policy().render(document)
        for outcome, report in (('separates', report_resolved()),
                                ('cannot separate', report_unresolved())):
            for flags in (['--restore', name], ['--drop', name],
                          ['--restore', name, '--drop', name]):
                artifact = Path(tmp) / 'matrix.json'
                artifact.write_bytes(written)
                measurements = Path(tmp) / 'matrix-counts.json'
                measurements.write_text(json.dumps(report), encoding='utf-8')
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    code = _journey_contract.policy().main(
                        ['rebaseline', '--artifact', str(artifact),
                         '--measurements', str(measurements), *flags])
                rows.append((state, outcome, flags, code,
                             err.getvalue().strip() or out.getvalue().strip()))
    return rows


def test_a_restore_and_a_drop_do_not_refuse_each_other(tmp):
    """Two flags about one journey, and the pair must not refuse itself.

    The recorded-null refusal hands `--restore` the journeys "recorded at
    null AND not being dropped" while its message says "the budget already
    holds a count for" — a one-line subtraction once put the wrong set
    there, refusing `--restore X --drop X` on the very artefact it is meant
    for. What has to hold: on the state a drop exists for, the pair does
    what `--drop` alone does; the other cells legitimately refuse, and the
    control below checks every sentence in them.
    """
    name = journeys().NAMES[0]
    both = ['--restore', name, '--drop', name]
    rows = [(state, outcome, code, said)
            for state, outcome, flags, code, said in _drop_matrix(tmp, name)
            if flags == both]
    droppable = [(said, code) for state, outcome, code, said in rows
                 if state == 'null' and outcome == 'cannot separate']
    assert droppable, 'the matrix lost the one cell this control is about'
    for said, code in droppable:
        assert code == 0, (
            f'--restore and --drop together refused against an artefact '
            f'holding {name} at null and a run that cannot separate it, so '
            f'the pair refuses the flag that works there: {said}')
        assert 'already holds a count for' not in said, (
            f'the refusal names the state backwards — the artefact holds '
            f'{name} at null, and a null is not a count: {said}')
    # And the pair never succeeds where the two flags contradict: restoring a
    # count and dropping it are not one request.
    for state, outcome, code, said in rows:
        if state == 'null' and outcome == 'cannot separate':
            continue
        assert code != 0, (
            f'--restore and --drop together recorded {name} against an '
            f'artefact holding it as {state} and a run that {outcome}, which '
            f'are two contradictory requests: {said}')


def test_a_dropped_journey_carries_no_bound_with_it(tmp):
    """`--drop` takes the journey's OWN tolerance with it.

    A bound beside a journey the budget does not hold decides nothing and
    `journey_artifact` refuses the document for it, so this starts from a
    legal document — the journey holding BOTH a count and a bound — and
    drives the whole command: a helper-level assertion would pass on a
    helper the caller never used that way.
    """
    policy = _journey_contract.policy()
    names = journeys().NAMES
    dropped = names[0]
    document = recorded_document(tolerance_pct=0.5,
                                 tolerances={dropped: 25.0})
    assert document['journeys'][dropped] is not None, document['journeys']
    artifact = Path(tmp) / 'journey-budget.json'
    artifact.write_bytes(policy.render(document))

    code, out, err = _rebaseline_over(tmp, artifact, _unresolved(dropped),
                                      '--drop', dropped)
    assert code == 0, (
        f'--drop refused a journey the budget held a count and a bound '
        f'for: {out}{err}')
    written = policy.load(artifact)
    assert written['journeys'][dropped] is None, written['journeys']
    assert dropped not in written['tolerances'], written['tolerances']


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeyrebaseline__')


if __name__ == '__main__':
    raise SystemExit(main())
