#!/usr/bin/env python3
"""The enumeration of guard arms, and the controls the row files hold.

`tests/_launch_arms.py` is the data: every arm of both analysers with
the state it is in, the evidence for that state, and the one clause a
sweep deleted to find out. This suite is what stops the table drifting
from the tree — a named row that no longer exists, a duplicated arm, an
arm whose text has left the analyser — and it replays the sweep for one
arm of each state, so the table's own method is exercised rather than
merely described.

It also carries the arms the two row files cannot express: three
controls asserting sites AND refusals where a `LAUNCH_REFUSAL_ROW` is
built to hold exactly one refusal, and one step ceiling for the arm
whose mutant does not answer wrong but does not stop.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _arm_sweep import arm_sweep  # noqa: E402
from _bound_site_rows import BOUND_SITE_ROWS  # noqa: E402
from _launch_arms import (ARM_CONTROLS, DEAD, LAUNCH_ARMS,  # noqa: E402
                          REDUNDANT, STEP_CEILING_CONTROL, STATES)
from _launch_audit import bound_sites, launch_refusals  # noqa: E402
from _launch_refusal_rows import LAUNCH_REFUSAL_ROWS  # noqa: E402
from _step_ceiling import within_step_ceiling  # noqa: E402

TESTS = Path(__file__).resolve().parent

# (id, file, line, cut, anchor, what, state, evidence)
ID, FILE, LINE, CUT, ANCHOR, WHAT, STATE, EVIDENCE = range(8)

ROW_LABELS = ({label for label, _, _ in BOUND_SITE_ROWS}
              | {label for label, _, _ in LAUNCH_REFUSAL_ROWS})
CONTROL_LABELS = {label for label, _, _, _ in ARM_CONTROLS}

# One arm per state, and one of the two the row files cannot hold, so the
# sweep is replayed in every shape the table claims for it.
SWEEP_SAMPLE = ('ch.namedexpr-in-bound', 'res.call-attribute-subprocess',
                'pf.in-bound', 'ra.else')


def _collapsed(text):
    return ' '.join(text.split())


def _anchor(arm):
    """The arm's own text, less the ellipsis a truncated anchor ends on.

    An anchor is a PREFIX of the arm's block, so the table costs one
    line per arm instead of three, and the prefix is what has to still
    be there for the arm to be where the table says it is.
    """
    return _collapsed(arm[ANCHOR]).removesuffix('...')


def _arm(name):
    for arm in LAUNCH_ARMS:
        if arm[ID] == name:
            return arm
    raise AssertionError(f'{name} is not in the enumeration')


def test_the_enumeration_is_a_closed_list_with_no_holes(tmp):
    """Every arm is listed once, in one of three states, with evidence.

    A fourth state — "live and deliberately unpinned" — is what this
    table exists to make unrepresentable, so the states are closed and
    checked rather than documented.
    """
    del tmp
    assert LAUNCH_ARMS, 'the enumeration is empty'
    names = [arm[ID] for arm in LAUNCH_ARMS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f'arms listed twice: {duplicates}'
    outside = sorted({arm[STATE] for arm in LAUNCH_ARMS} - set(STATES))
    assert not outside, f'states outside the closed set: {outside}'
    blank = [arm[ID] for arm in LAUNCH_ARMS
             if not str(arm[WHAT]).strip() or not str(arm[EVIDENCE]).strip()]
    assert not blank, f'arms with no description or no evidence: {blank}'
    uncut = [arm[ID] for arm in LAUNCH_ARMS if not arm[CUT].strip()]
    assert not uncut, f'arms with no clause the sweep deleted: {uncut}'
    files = {arm[FILE] for arm in LAUNCH_ARMS}
    assert files == {'_launch_audit.py', '_argv_read.py'}, files


def test_every_controlled_arm_names_a_row_or_a_control_that_exists(tmp):
    """A CONTROLLED entry is only worth what its evidence resolves to.

    The two row files, `ARM_CONTROLS` and the step ceiling are the whole
    set of things that can fail when an arm is deleted, so an evidence
    string naming none of them is a claim no test can check.
    """
    del tmp
    known = ROW_LABELS | CONTROL_LABELS | {STEP_CEILING_CONTROL}
    unknown = sorted({arm[EVIDENCE] for arm in LAUNCH_ARMS
                      if arm[STATE] == 'CONTROLLED'
                      and arm[EVIDENCE] not in known})
    assert not unknown, f'controlled arms naming no row or control: {unknown}'
    unused = sorted(CONTROL_LABELS - {arm[EVIDENCE] for arm in LAUNCH_ARMS})
    assert not unused, f'controls no arm names: {unused}'
    stepped = [arm[ID] for arm in LAUNCH_ARMS
               if arm[EVIDENCE] == STEP_CEILING_CONTROL]
    assert len(stepped) == 1, (
        f'the step ceiling is bound to {stepped}; it exists for a fixpoint '
        'that does not stop, so exactly one arm should reach for it')


def test_every_arm_is_still_in_the_analyser_it_was_classified_in(tmp):
    """The `anchor` column is a drift check, not decoration.

    A classified arm whose text has left the analyser is an arm the
    table is still describing, and reading it as current is how a
    successor is misled.
    """
    del tmp
    names = {arm[FILE] for arm in LAUNCH_ARMS}
    sources = {name: _collapsed((TESTS / name).read_text(encoding='utf-8'))
               for name in names}
    lengths = {name: len((TESTS / name).read_text(
        encoding='utf-8').splitlines()) for name in sources}
    missing = sorted(arm[ID] for arm in LAUNCH_ARMS
                     if _anchor(arm) not in sources[arm[FILE]])
    assert not missing, ('arms whose text is no longer in the analyser: '
                         f'{missing}')
    out_of_range = sorted(arm[ID] for arm in LAUNCH_ARMS
                          if not 0 < arm[LINE] <= lengths[arm[FILE]])
    assert not out_of_range, (
        f'arms whose line is outside its file: {out_of_range}')


def test_each_control_asserts_what_the_analyser_answers_today(tmp):
    """The arms no row file can hold, pinned on sites AND refusals.

    A `LAUNCH_REFUSAL_ROW` is built to hold exactly one refusal, and
    each of these shapes is the one that tells its arm apart only
    because the OTHER refusal moves. So the control states the whole
    answer, and deleting the arm moves it.
    """
    del tmp
    for label, source, sites, refusals in ARM_CONTROLS:
        assert bound_sites(source, label) == sites, label
        assert launch_refusals(source, label) == refusals, label


def test_the_sweep_reproduces_each_state_it_claims(tmp):
    """Deleting the arm moves a CONTROLLED arm's evidence and nothing else.

    The table's method, run in place: a CONTROLLED arm's own evidence
    label is among the verdicts that move, a REDUNDANT or DEAD arm moves
    none, and every cut removed text rather than nothing. So the three
    states are told apart by what the sweep does, which is the claim the
    whole enumeration rests on.
    """
    arms = [_arm(name) for name in SWEEP_SAMPLE]
    findings = arm_sweep(Path(tmp), arms)
    for arm in arms:
        name, state, evidence = arm[ID], arm[STATE], arm[EVIDENCE]
        found = findings[name]
        assert found['removed'].strip(), f'{name}: the cut removed nothing'
        assert 'refused' not in found, f'{name}: {found["refused"]}'
        if state == 'CONTROLLED':
            assert evidence in found['moved'], (
                f'{name}: deleting it left every verdict alone, so its own '
                f'evidence {evidence!r} does not control it; moved '
                f'{found["moved"]}')
        else:
            assert state in (REDUNDANT, DEAD), name
            assert not found['moved'], (
                f'{name}: recorded {state} but deleting it moved '
                f'{found["moved"]}')


def test_the_fixpoint_stops_on_a_factory_it_has_already_registered(tmp):
    """`fx.skip-registered` is load-bearing by being a stop, not a value.

    Without it the launcher branch re-adds its name and re-sets `changed`
    on every pass, so the analysis does not answer wrong — it does not
    stop. A row asserts a verdict and there is none to assert, so the
    bound is a step COUNT taken in a child, which is what
    `_step_ceiling` is for.
    """
    del tmp
    source = ("import subprocess\n"
              "def probe(ns):\n"
              "    return ns['subprocess'].run(\n"
              "        ['git', 'status'], check=True, timeout=30)\n")
    sites, child = within_step_ceiling(source, 'launcher-factory')
    assert sites == [(3, 'git', 'timeout')], sites
    assert child != os.getpid(), 'the step count was taken in this process'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
