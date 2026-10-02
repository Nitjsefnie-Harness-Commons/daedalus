#!/usr/bin/env python3
"""The survivor clause of the bounded tree kill, on both of its outcomes.

`scripts/ci/suite_bound.py` answers a suite that TOOK the request with a
sentence whose second clause is the outcome of the escalation that still went
out: an escalation that found a member is one sentence, and one that found the
group already gone is another. The `insisted or` selecting between them has two
behaviours, and each needs a control pairing a child that STOPPED with an
escalation whose own outcome is the thing under test.

Nothing paired the two before. The only control reaching the stopped branch
gave its `Signals` no `killpg_errors` and so never saw the escalation fail,
and the only control making `insisted` name something gave a `Child` that
never stops and so never reached the clause. Each half of the `or` was
therefore green whether the production line read `insisted or <constant>` or
just the constant.

It is a suite of its own rather than two more controls in
`test_suite_bound.py`: that file is at the 700-line ceiling this repository
holds test modules to, and its other arms are where a reader looks first for
what the teardown does when the tree misbehaves.
"""
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _suite_bound_stubs import (  # noqa: E402
    GROUP, Child, Clock, Platform, Signals, swapped)

SUITE_BOUND = _util.load(ROOT / 'scripts' / 'ci' / 'suite_bound.py',
                         'suite_bound_survivor')

# What the record says when the escalation found a member of the group, and
# what it says when it found the group already gone. Spelled here rather than
# derived, because the subject's own sentence is the thing under test and a
# control that rebuilt it would agree with a subject that got it wrong.
ESCALATION_WENT_OUT = 'the escalation reached what was still in it'
GROUP_WAS_GONE = f'process group {GROUP} was already gone'


class _EscalationFindsNothing(Signals):
    """`Signals` whose escalation finds the group already gone.

    Keyed on the SECOND `killpg` rather than on `signal.SIGKILL`, which
    Windows has no name for. The subject asks and then escalates on every
    cell -- the route it takes is chosen by a `sys` stand-in, not by the
    host -- so the ordinal asks the same question the signal-keyed row asks,
    in a spelling every cell can carry. Keying it on the signal instead
    would need `require_sigkill()`, and a suite whose every control skips
    reports no coverage at all on the cells that skipped it.
    """

    def __init__(self):
        super().__init__()
        self.calls = 0

    def killpg(self, group, sig):
        self.sent.append((group, sig))
        self.calls += 1
        if self.calls == 2:
            raise ProcessLookupError()


def _record_for(signals):
    """The record for a suite that stopped, under the signals it was given.

    The child is stood in for as one that complies with the request, and the
    clock spends the grace window in arithmetic, so nothing here depends on
    how fast this machine is or on how long anything really took.
    """
    with swapped(SUITE_BOUND, sys=Platform('linux'), os=signals, time=Clock()):
        return SUITE_BOUND.kill_process_tree(Child(stops_after=1))


def test_a_survivor_clause_names_the_escalation_that_went_out(tmp):
    """The suite's own exit emptied nothing, and the escalation proved it.

    An escalation that went out proves a member of the group was there,
    which is the opposite of what "nothing was left" would say -- so the
    clause names the escalation rather than a group that may no longer
    exist. Read as `note = insisted`, the same line renders the record
    with `None` in it, and no record this control can see says that.
    """
    del tmp
    signals = Signals()
    record = _record_for(signals)
    assert len(signals.sent) == 2, signals.sent
    assert signals.sent[0][1] is signal.SIGTERM, signals.sent
    assert record == (f'process group {GROUP} asked to stop and the suite '
                      f'did; {ESCALATION_WENT_OUT}'), record
    assert GROUP_WAS_GONE not in record, record


def test_a_survivor_clause_names_the_escalation_that_found_nothing(tmp):
    """The group was already gone, and the escalation is what proved it.

    The other half of the same clause, and the one a record written from
    the constant alone gets wrong: a group that no longer exists is a fact
    the escalation established, so it is the escalation's own wording that
    belongs here. Deleting the `insisted or` leaves this control red and
    the one above green, which is why the pair is one suite and not one
    arm.
    """
    del tmp
    signals = _EscalationFindsNothing()
    record = _record_for(signals)
    assert len(signals.sent) == 2, signals.sent
    assert record == (f'process group {GROUP} asked to stop and the suite '
                      f'did; {GROUP_WAS_GONE}'), record
    assert ESCALATION_WENT_OUT not in record, record


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='boundsurvivor_')


if __name__ == '__main__':
    raise SystemExit(main())
