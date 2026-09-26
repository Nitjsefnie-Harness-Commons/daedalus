"""Giving the debugger attachment back, and forgetting it.

Sharing an attachment is the other half of the story and has its own suite
(`test_debugger_attachment.py`). This one is the teardown: what a release
detaches, what a release must NOT detach, what `cdpForgetAttachment` does on
tab close and on Chrome detaching us, and what happens when `detach` itself
refuses — which, unhandled, ends the extension's service worker.

These are the arms with no command call site of their own. A tab close and a
Chrome-initiated detach are events, not commands, so nothing in the worker's
dispatch path exercises them and a control has to drive them directly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _debugger_attachment_harness import (  # noqa: E402
    _by_id, _cdp, run_attachment_case)


def test_a_kept_session_is_forgotten_when_chrome_detaches_us(tmp):
    """I-2: `onDetach` drops the claim, and does not detach again.

    Chrome has already detached by the time it says so — DevTools opened, the
    target crashed — so asking again is a refusal from Chrome that would land
    on whichever command ran next. The record still has to go, or the next
    command on that tab would join a claim whose attachment is gone and be
    answered by a channel with no debugger on it.
    """
    del tmp
    outcome = run_attachment_case({'actions': [
        {'dispatch': _cdp('kept', tabId=7, keep_session=True)},
        {'drain': True},
        {'detach': 7},
        {'drain': True},
    ]})
    assert outcome['attachCalls'] == [7], outcome
    # Chrome detached us; we did not ask it to.
    assert outcome['detachCalls'] == [], outcome
    assert outcome['live'] == [], outcome
    # And the tab is attachable again, which is the half that would break if
    # the record survived.
    later = run_attachment_case({'actions': [
        {'dispatch': _cdp('kept', tabId=7, keep_session=True)},
        {'drain': True},
        {'detach': 7},
        {'drain': True},
        {'dispatch': _cdp('after', tabId=7)},
        {'drain': True},
    ]})
    assert later['attachCalls'] == [7, 7], later
    assert _by_id(later)['after']['error'] is None, later


def test_a_closed_tab_is_detached_once_and_forgotten(tmp):
    """I-2: `onRemoved` detaches what the claim still holds, once.

    The tab is gone, so Chrome has not detached us and the attachment is
    ours to give back. Exactly one detach, and the record goes with it.
    """
    del tmp
    outcome = run_attachment_case({'actions': [
        {'dispatch': _cdp('kept', tabId=7, keep_session=True)},
        {'drain': True},
        {'tabRemoved': 7},
        {'drain': True},
    ]})
    assert outcome['attachCalls'] == [7], outcome
    assert outcome['detachCalls'] == [7], outcome
    # A fresh command on that tab id attaches for itself and gives it back,
    # which is what a released record looks like from outside. A record that
    # outlived the tab would have made it join, and nothing would attach.
    again = run_attachment_case({'actions': [
        {'dispatch': _cdp('kept', tabId=7, keep_session=True)},
        {'drain': True},
        {'tabRemoved': 7},
        {'drain': True},
        {'dispatch': _cdp('after', tabId=7)},
        {'drain': True},
    ]})
    assert again['attachCalls'] == [7, 7], again
    assert again['detachCalls'] == [7, 7], again
    assert _by_id(again)['after']['error'] is None, again

    # A tab with no claim must not be detached: Chrome refuses to detach
    # what is not attached, and that refusal belongs to whatever runs next.
    quiet = run_attachment_case({'actions': [
        {'dispatch': _cdp('transient', tabId=7)},
        {'drain': True},
        {'tabRemoved': 9},
        {'drain': True},
    ]})
    assert quiet['detachCalls'] == [7], quiet


def test_a_forgotten_claim_does_not_stop_a_newer_one_from_attaching(tmp):
    """I-1: a stale release must not detach a NEWER claim's attachment.

    A transient claim is live, Chrome detaches us for its own reasons — the
    entry goes with it — and a second claim attaches the now-free tab. The
    first claim then releases. Its entry is no longer the one installed, so
    it owns nothing, and detaching here would kill the second claim's LIVE
    attachment: the tab would report attached while the command using it
    runs, and the next command would be the one that discovered it.
    """
    del tmp
    outcome = run_attachment_case({'actions': [
        {'claim': {'tabId': 7}},
        {'settle': 2},
        # Armed before the event, because a stale release has to be pending
        # when the forget happens, not after it.
        {'releaseOnEvent': 0},
        {'detach': 7},
        {'claim': {'tabId': 7}},
        {'settle': 4},
    ]})
    # The newer claim's attachment survives the stale release.
    assert outcome['detachCalls'] == [], outcome
    assert outcome['live'] == [7], outcome
    assert outcome['attachCalls'] == [7, 7], outcome
    # And the newer claim still owns what it took, so its own release gives
    # the attachment back rather than leaving it up for the worker's life.
    released = run_attachment_case({'actions': [
        {'claim': {'tabId': 7}},
        {'settle': 2},
        {'releaseOnEvent': 0},
        {'detach': 7},
        {'claim': {'tabId': 7}},
        {'settle': 4},
        {'release': 1},
        {'settle': 4},
    ]})
    assert released['detachCalls'] == [7], released
    assert released['live'] == [], released


def test_a_second_release_on_one_handle_detaches_once(tmp):
    """I-5: a repeat release on a spent handle detaches nothing.

    Two releases on one handle would drive the count negative and detach a
    tab a later claim has since attached — a Chrome refusal that would land
    on whichever command ran next. So the later claim is really set up here,
    and deliberately never released, which leaves a repeat release as the
    only thing that could take its attachment down.

    What carries this is the ENTRY guard in `_cdpRelease`: the first release
    took the entry out of the map, so the second finds nothing installed and
    no-ops. The handle's own "released once" guard is defence in depth for a
    caller that releases twice, which no shipped call site does. Removing it
    alone leaves this control green, which is why it carries no weight
    here and is named in `cdp_attach.js` rather than pinned here.
    """
    del tmp
    outcome = run_attachment_case({'actions': [
        {'claim': {'tabId': 7}},
        {'settle': 2},
        # The first release gives the first attachment back.
        {'release': 0},
        {'settle': 2},
        # A later claim takes the now-free tab...
        {'claim': {'tabId': 7}},
        {'settle': 2},
        # ...and the SAME handle is released again, which is the defect this
        # is about. The later claim is deliberately never released, so the
        # only thing that can take its attachment down is a release that
        # should have been a no-op.
        {'release': 0},
        {'settle': 4},
    ]})
    assert outcome['attachCalls'] == [7, 7], outcome
    # Exactly ONE detach, and the later claim's attachment is still standing.
    # Those two facts are the property together: a release that ran twice on
    # one handle would have issued a second detach and taken the later
    # claim's attachment out from under it, so `live` would be empty.
    assert outcome['detachCalls'] == [7], outcome
    assert outcome['live'] == [7], outcome


def test_a_refused_detach_on_a_closed_capturing_tab_is_survived_and_traced(
        tmp):
    """#1161, in the issue's own shape: a capturing tab is closed.

    A tab running a capture is the one the extension really attaches a
    debugger for, so tab close is where a refused `chrome.debugger.detach`
    happens. The worker is a Node process and Node ends a process on an
    unhandled rejection, so a detach site that lets its own rejection escape
    does not fail one command — it takes the extension's worker, and every
    other tab's capture with it. That was the base's `netcapture.js`, whose
    detach was the one of three not awaited.

    Two things must hold, and the control states both: the worker is still
    alive, and the record is released. A caught refusal that leaves no trace
    is a refused detach and a successful one indistinguishable, so the third
    thing asserted is that the refusal is visible at all.

    One of two controls, one per detach site, and not to be merged: this one
    drives `cdpForgetAttachment`, the release site has its own, and a change
    to either leaves the other green.
    """
    del tmp
    outcome = run_attachment_case({
        'detachFails': True,
        'actions': [
            {'dispatch': {'id': 'capture', 'type': 'net-capture', 'tabId': 7}},
            {'drain': True},
            {'tabRemoved': 7},
            {'settle': 8},
        ]})
    assert outcome['attachCalls'] == [7], outcome
    assert outcome['detachCalls'] == [7], outcome
    # Alive: nothing escaped, so the worker never ended.
    assert outcome['unhandled'] == [], outcome
    # The record is released, but NOT by dispatching a follow-up command here:
    # this control's detach REFUSED, so Chrome is still holding the tab, and a
    # command that tried to attach would be refused for that reason and prove
    # nothing about the record. A refused detach leaves the tab genuinely
    # attached, and a double that let a follow-up attach anyway would be
    # asserting a browser state that cannot exist.
    # `test_a_claim_arriving_after_a_refused_detach_still_works` below is the
    # observable for the release: it attaches a second claim and watches it
    # take, which is what a surviving record would have prevented.
    assert outcome['refused'], outcome
    assert any('detach refused' in line for line in outcome['refused']), \
        outcome
    assert any('tab 7' in line for line in outcome['refused']), outcome


def test_a_refused_detach_on_a_transient_release_is_survived_and_traced(tmp):
    """#1161 on the RELEASE detach site, which settles for a joining claim.

    One of two controls, one per detach site, and not to be merged: the
    capturing-tab control above drives `cdpForgetAttachment`, this one drives
    `_cdpRelease`, and each site has its own mutant — a change to one leaves
    the other green. What they share is the scenario — a detaching
    `chrome.debugger.detach` that rejects — which is why both names say
    which site they reach.

    A claim's release records the refusal and settles UNCONDITIONALLY: a
    claim arriving in the window chains onto that promise, and one that never
    settles is a worse defect than the refusal it was hiding. The later claim
    below is how that is observed — it is free to run rather than waiting
    forever on a promise nobody settles.
    """
    del tmp
    outcome = run_attachment_case({
        'detachFails': True,
        'actions': [
            {'claim': {'tabId': 7}},
            {'settle': 2},
            {'release': 0},
            {'claim': {'tabId': 7}},
            {'settle': 8},
        ]})
    assert outcome['detachCalls'] == [7], outcome
    assert outcome['unhandled'] == [], outcome
    # The later claim ran rather than waiting on a promise nobody settles,
    # which is the unconditional settling this control exists for. It did
    # NOT get in: the detach was refused, so Chrome still holds the tab and
    # the claim's own attach is refused with it. `live == [7]` is Chrome's
    # holding, not a claim's — the record itself was released by the first
    # release, which is why the later claim got far enough to attempt an
    # attach at all.
    assert outcome['attachCalls'] == [7, 7], outcome
    assert outcome['live'] == [7], outcome
    assert any('detach refused' in line for line in outcome['refused']), \
        outcome


def test_a_claim_arriving_after_a_refused_detach_still_works(tmp):
    """The settling is unconditional, and this is what that buys.

    A joining claim chains onto the release's promise. A refusal must settle
    it, or the next command on that tab waits on a promise that never
    completes and the tab is unreachable for the life of the worker.
    """
    del tmp
    outcome = run_attachment_case({
        'detachFails': True,
        'actions': [
            {'claim': {'tabId': 7}},
            {'settle': 2},
            {'release': 0},
            {'claim': {'tabId': 7}},
            {'settle': 8},
        ]})
    assert outcome['attachCalls'] == [7, 7], outcome
    assert outcome['unhandled'] == [], outcome
    assert outcome['live'] == [7], outcome


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='attachtear_')


if __name__ == '__main__':
    raise SystemExit(main())
