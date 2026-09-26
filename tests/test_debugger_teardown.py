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
    assert outcome['claims'] == [], outcome
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
    assert outcome['claims'] == [], outcome

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
    assert released['claims'] == [], released


def test_a_second_release_on_one_handle_detaches_once(tmp):
    """I-5: a release is idempotent.

    Two releases on one handle would drive the count negative and, once the
    entry is gone, detach a tab a later claim has since attached. The second
    detach is a Chrome refusal that would land on whichever command ran
    next, so the handle owns the fact that it has been released.
    """
    del tmp
    outcome = run_attachment_case({
        'doubleRelease': True,
        'actions': [
            {'claim': {'tabId': 7}},
            {'settle': 2},
            {'release': 0},
            {'settle': 4},
        ]})
    assert outcome['attachCalls'] == [7], outcome
    assert outcome['detachCalls'] == [7], outcome
    assert outcome['claims'] == [], outcome


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
    # Released: the capture and the claim are both gone, so the next command
    # on that tab is free rather than joining a record whose detach is not
    # coming.
    assert outcome['claims'] == [], outcome
    assert outcome['refused'], outcome
    assert any('detach refused' in line for line in outcome['refused']), \
        outcome
    assert any('tab 7' in line for line in outcome['refused']), outcome


def test_a_refused_detach_on_a_transient_release_is_survived_and_traced(tmp):
    """#1161 on the other detach site, which settles for a joining claim.

    A claim's release records the same refusal and settles unconditionally:
    a claim arriving in the window chains onto that promise, and one that
    never settles is a worse defect than the refusal it was hiding.
    """
    del tmp
    outcome = run_attachment_case({
        'detachFails': True,
        'actions': [
            {'claim': {'tabId': 7}},
            {'settle': 2},
            {'release': 0},
            {'settle': 8},
        ]})
    assert outcome['detachCalls'] == [7], outcome
    assert outcome['unhandled'] == [], outcome
    assert outcome['claims'] == [], outcome
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
