#!/usr/bin/env python3
"""Arms of the bounded teardown that no healthy launcher ever reaches.

The survivor clause is the first of them: `scripts/ci/suite_bound.py`
answers a suite that TOOK the request with a sentence whose second clause
is the outcome of the escalation that still went out: an escalation that
found a member is one sentence, and one that found the group already gone
is another. The `insisted or` selecting between them has two behaviours,
and each needs a control pairing a child that STOPPED with an escalation
whose own outcome is the thing under test.

Nothing paired the two before. The only control reaching the stopped branch
gave its `Signals` no `killpg_errors` and so never saw the escalation fail,
and the only control making `insisted` name something gave a `Child` that
never stops and so never reached the clause. Each half of the `or` was
therefore green whether the production line read `insisted or <constant>` or
just the constant.

It is a suite of its own rather than two more controls in
`test_suite_bound.py`: that file is at the 700-line ceiling this repository
holds test modules to, and its other arms are where a reader looks first for
what the teardown does when the tree misbehaves. The cleanup report is the
second arm to arrive here, for the same reason and by the same rule.

It stood in for `sys` and not for `signal`, on the reading that the route is
chosen by the `sys.platform` read and the escalation follows from it. The
`sys` read is a stand-in, and the escalation was not: `_ask_and_insist` names
`signal.SIGKILL`, which a windows-latest interpreter has no member for, so
the read raised inside the arm before the escalation went out and
`kill_process_tree`'s broad guard made the record that exception instead.
Both controls then saw one signal where the clause's own record has two
(`scripts/ci/suite_bound.py`'s `note = insisted or ...`), on all four
`windows-latest` cells and on no other. The `signal` global is stood in for
too, so the arm is reached on every cell and neither control depends on what
this interpreter can name.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _suite_bound_stubs import (  # noqa: E402
    GROUP, Child, Clock, Escalation, Platform, Removals, Signals, swapped)

SUITE_BOUND = _util.load(ROOT / 'scripts' / 'ci' / 'suite_bound.py',
                         'suite_bound_survivor')

# What the record says when the escalation found a member of the group, and
# what it says when it found the group already gone. Spelled here rather than
# derived, because the subject's own sentence is the thing under test and a
# control that rebuilt it would agree with a subject that got it wrong.
ESCALATION_WENT_OUT = 'the escalation reached what was still in it'
GROUP_WAS_GONE = f'process group {GROUP} was already gone'

# The two signals the clause under test is only reachable behind, in order.
# `SIGTERM` is the interpreter's own member and the escalation is
# `Escalation`'s, so this reads the same on a cell that can name an
# escalation and on one that cannot.
REQUEST_THEN_ESCALATION = [Escalation.SIGTERM, Escalation.SIGKILL]


class _EscalationFindsNothing(Signals):
    """`Signals` whose escalation finds the group already gone.

    Keyed on the SECOND `killpg` rather than on the escalation's own name:
    the subject asks and then escalates on every cell now that its `signal`
    global is stood in for, and the ordinal is what says which of the two
    calls failed without either control having to know what the escalation
    is called -- a name this interpreter may not be able to supply.
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

    The child is stood in for as one that complies with the request, the
    clock spends the grace window in arithmetic, and the two signals the arm
    names are stood in for by `Escalation` -- so nothing here depends on how
    fast this machine is, on how long anything really took, or on whether
    this interpreter can name an escalation at all.
    """
    with swapped(SUITE_BOUND, sys=Platform('linux'), os=signals, time=Clock(),
                 signal=Escalation):
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
    assert [sig for _group, sig in signals.sent] == \
        REQUEST_THEN_ESCALATION, signals.sent
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
    assert [sig for _group, sig in signals.sent] == \
        REQUEST_THEN_ESCALATION, signals.sent
    assert record == (f'process group {GROUP} asked to stop and the suite '
                      f'did; {GROUP_WAS_GONE}'), record
    assert ESCALATION_WENT_OUT not in record, record


# The bound this control shrinks, and the refusals that spend it: five of
# them against a fifth of a second, so the loop reaches the report in a
# handful of calls and none of them waits on a real clock.
_UNREADABLE_BOUND_S = 0.2
_UNREADABLE_REFUSALS = 5


def test_a_listing_that_cannot_be_read_is_reported_as_unreadable(tmp):
    """The report says the listing failed, rather than failing itself.

    `discard_outputs` never raises FOR FAILING TO, and the report is half
    of that: it names the directory that outlived the bound and what is
    still in it. A directory that cannot even be listed is the third
    shape, and it was the only arm of the report no control reached --
    because deleting the `except OSError` arm does not make the directory
    go, it makes the listing raise out of a launcher's `finally`, which is
    issue #1485 on the reporting path instead of the removal one.

    Both refusals stand in at once because only their combination reaches
    the arm: nothing lists a directory whose removal succeeded, and a
    listing that failed over a directory still on disk is what the arm is
    for. The two stand-ins are the ones this suite already uses, and the
    `os` one is a local class rather than a shared helper, so the
    generated reserved-name set stays current.
    """
    directory = Path(tmp) / 'outputs'
    directory.mkdir()
    unreadable = OSError(13, 'the directory cannot be read')
    real_os = SUITE_BOUND.os

    class UnreadableOs:
        """`os` as the report reads it: every name but `listdir` is real."""

        def __getattr__(self, name):
            return getattr(real_os, name)

        def listdir(self, path):
            del path
            raise unreadable

    removals = Removals(refuse=_UNREADABLE_REFUSALS)
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), swapped(
            SUITE_BOUND, shutil=removals, os=UnreadableOs(), time=Clock(),
            CLEANUP_TIMEOUT_S=_UNREADABLE_BOUND_S):
        SUITE_BOUND.discard_outputs(str(directory))
    # The stand-in's own record, before the report: more than one refusal,
    # all of them this directory, so the bound was spent retrying and the
    # report below is about the one cleanup under test.
    assert len(removals.calls) > 1, removals.calls
    assert set(removals.calls) == {str(directory)}, removals.calls
    assert stderr.getvalue().count('\n') == 1, stderr.getvalue()
    assert str(directory) in stderr.getvalue(), stderr.getvalue()
    # What an `OSError`'s own message reads like is the platform's prose,
    # so the report is pinned on the shape this module promised to print.
    assert 'left in place: (unreadable: ' in stderr.getvalue(), (
        stderr.getvalue())


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='boundsurvivor_')


if __name__ == '__main__':
    raise SystemExit(main())
