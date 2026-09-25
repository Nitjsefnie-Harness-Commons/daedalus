#!/usr/bin/env python3
"""Which evaluator runs submitted source, and what may answer for it.

The background tries a source-free probe, then main-world injection, then
CDP, then the page relay — and once submitted source has been dispatched, the
outcome is terminal whatever it is. These run the shipped scripts in a Node
VM so the ordering, the handle release and the invocation-id bounds can be
observed rather than inferred.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cdpharness import run_cdp_handle_lifecycle  # noqa: E402
from _relayharness import (run_eval_after_cdp_fails_mid_flight,  # noqa: E402
                           run_eval_relay_marker, run_eval_relay_overlap,
                           run_eval_same_tab_preemption, run_gm_abort,
                           run_eval_with_poisoned_page_globals,
                           run_main_world_injection_shapes)
from _mainworldharness import (run_main_world_eval_timeout,  # noqa: E402
                               run_main_world_eval_inside,
                               run_main_world_eval_probe_hang,
                               run_hotfix_replay_timeout,
                               run_hotfix_replay_probe_hang,
                               run_hotfix_replay_cdp_timeout,
                               run_hotfix_replay_inside)

# The chosen MAIN-world settlement ceiling, in ms. Deliberately equal to the
# CDP settlement bound so one caller sees the same limit whichever channel
# the source-free probe selects; the two are pinned independently, by this
# suite and by tests/test_starvation_bounds.py. The harness's inside modes
# step to 9000, strictly inside it, so a control that halves or quarters
# the ceiling crosses that step and goes red.
_SETTLE_MS = 10000


def _replay_errors(replay):
    return [entry for entry in replay if entry['level'] == 'error']


def test_a_gm_request_handle_can_actually_cancel_its_fetch(tmp):
    """`abort()` cancelled nothing: it was an empty function.

    The handle GM.xmlhttpRequest returns was `{ abort: function() {} }`, so a
    caller that stopped caring about a slow request had no way to say so. The
    fetch ran to completion or to its timeout in the service worker, holding
    the relay entry and the connection, and the page's callbacks fired for a
    response nobody was waiting for.

    All three scripts run here — page, content script and service worker —
    because the cancellation has to cross both hops to reach the
    AbortController, and a relay that drops it at either one looks identical
    from the page.
    """
    del tmp
    outcome = run_gm_abort()
    assert outcome['inFlight'] == 1, outcome
    assert outcome['aborted'] is True, outcome
    # Exactly one terminal callback, and it is the abort one: a load or error
    # arriving afterwards must find nothing to call.
    assert outcome['onabort'] is True, outcome
    assert outcome['onload'] is False, outcome
    assert outcome['onerror'] is False, outcome
    assert outcome['ontimeout'] is False, outcome
    # Two abort() calls, one message: idempotent, and the second finds the
    # request already gone rather than telling the worker about a fetch it is
    # no longer running.
    assert outcome['abortMessages'] == 1, outcome
    # The worker keeps no controller for a request that is over.
    assert outcome['controllers'] == 0, outcome


def test_main_world_injection_result_shapes_are_explicit(tmp):
    """Every transport shape differs from a valid evaluated `null`."""
    del tmp
    actual = run_main_world_injection_shapes()
    assert actual == {
        'reject': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: executeScript rejected',
            'world': 'page-main'},
        'empty': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: no result frame',
            'world': 'page-main'},
        'frame-error': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: frame exception',
            'world': 'page-main'},
        'missing-result': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: result frame has no result',
            'world': 'page-main'},
        'bare-null': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: no result envelope',
            'world': 'page-main'},
        'genuine-null': {
            'hasResult': True, 'result': None, 'error': None,
            'world': 'page-main'},
        'eval-exception': {
            'hasResult': False, 'result': None,
            'error': 'operator exception', 'world': 'page-main'},
        'page-substitution': {
            'hasResult': True, 'result': 'PAGE-SUBSTITUTED', 'error': None,
            'world': 'page-main'},
    }, actual


def test_page_replaced_evaluators_use_injection_before_cdp(tmp):
    """A source-free probe keeps ordinary eval on the injection channel."""
    del tmp
    without_cdp = run_eval_with_poisoned_page_globals(False)
    assert without_cdp == {
        'result': 'FORGED-EVAL:2 + 2',
        'world': 'page-main',
        'deliveryId': 'did-poisoned',
        'scriptingCalls': 2,
    }, without_cdp

    with_cdp = run_eval_with_poisoned_page_globals(True)
    assert with_cdp == {
        'result': 'FORGED-EVAL:2 + 2',
        'world': 'page-main',
        'deliveryId': 'did-poisoned',
        'scriptingCalls': 2,
    }, with_cdp


def test_cdp_failure_after_dispatch_never_reruns_the_source(tmp):
    """Once the inspector has the source, no other evaluator may run it.

    Falling back after a dispatched evaluation would execute a command's side
    effects a second time, so a mid-flight inspector failure has to surface as
    an error rather than as a page-influenced answer.
    """
    del tmp
    actual = run_eval_after_cdp_fails_mid_flight()
    assert actual['cdpSideEffects'] == 1, actual
    assert actual['scriptingCalls'] == 1, actual
    assert actual['result'] is None, actual
    # The error still names the channel that executed the command.
    assert actual['world'] == 'cdp', actual
    assert 'inspector detached mid-evaluation' in (actual['error'] or ''), (
        actual)


def test_cdp_eval_releases_every_remote_handle_in_held_sessions(tmp):
    """CDP routes preserve transport fields and release every handle."""
    del tmp
    actual = run_cdp_handle_lifecycle()
    transport = {
        'replModeEnabled': True,
        'awaitPromiseEnabled': False,
        'returnByValueEnabled': False,
    }
    assert actual['evalTransports'] == [transport, transport, transport], (
        'eval CDP transport semantics', actual['evalTransports'])
    assert actual['hotfixTransports'] == [transport], (
        'hotfix CDP transport semantics', actual['hotfixTransports'])
    assert actual['released'] == [
        'compile-exception',
        'compile-result',
        'pending-late',
        'pending-original',
        'reject-exception',
        'reject-original',
        'reject-result',
        'throw-exception',
        'throw-result',
    ], actual
    assert actual['pendingHasTimeout'] is True, actual
    # The settle path (the reject case) and the reject path (the pending
    # case) must both tear their sampler down once the race resolves.
    assert actual['armedSamplers'] == 0, actual
    assert actual['resultWorlds'] == ['cdp', 'cdp', 'cdp'], actual


def test_eval_relay_same_id_overlap_uses_bounded_invocation_ids(tmp):
    """Eval results retain delivery ids and unknown relay ids are ignored."""
    del tmp
    actual = {
        'a-first': run_eval_relay_overlap(['owner-a', 'owner-b']),
        'b-first': run_eval_relay_overlap(['owner-b', 'owner-a']),
    }
    assert actual == {
        'a-first': {
            'relayIds': ['relay-1', 'relay-2'],
            'results': [
                {'result': 'owner-a', 'deliveryId': 'did-a'},
                {'result': 'owner-b', 'deliveryId': 'did-b'},
            ],
        },
        'b-first': {
            'relayIds': ['relay-1', 'relay-2'],
            'results': [
                {'result': 'owner-b', 'deliveryId': 'did-b'},
                {'result': 'owner-a', 'deliveryId': 'did-a'},
            ],
        },
    }, actual


def test_same_tab_page_cannot_preempt_direct_eval_result(tmp):
    """A page-forged relay result cannot win a direct eval invocation."""
    del tmp
    actual = run_eval_same_tab_preemption()
    assert actual == {
        'pageEvalMessages': 0,
        'results': [{'result': 'LEGIT', 'deliveryId': 'did-legit'}],
    }, actual


def test_page_eval_relay_world_is_namespaced_and_not_page_overridable(tmp):
    """Reserved hostnames and a forged marker stay in the page namespace."""
    del tmp
    hostnames = ('cdp', 'page-main', 'extension', 'page', 'relay.test')
    actual = {
        hostname: run_eval_relay_marker(hostname)
        for hostname in hostnames
    }
    assert actual == {
        hostname: {
            'result': 'FORGED',
            'world': f'page:{hostname}',
            'deliveryId': 'did-marker',
        }
        for hostname in hostnames
    }, actual


def test_a_main_world_eval_that_never_settles_reports_the_bound(tmp):
    """`exec x 'await new Promise(()=>{})'` used to await `executeScript`
    with no deadline, so the caller timed out and could retry a command that
    had already run. The bound is armed worker-side, and crossing it posts
    one ordinary result through the same channel every other failure uses.
    """
    del tmp
    outcome = run_main_world_eval_timeout()
    assert outcome['armed'] is True, outcome
    # The probe's arming and the injection's are the same ceiling; a second
    # or differently-timed one would be a different entry here.
    assert outcome['deadlines'] == [_SETTLE_MS, _SETTLE_MS], outcome
    # Nothing is posted while the page promise is open; the single result
    # appears only once the worker-side bound has been crossed.
    assert outcome['postedBeforeClock'] == 0, outcome
    assert outcome['got'] is True, outcome
    assert len(outcome['posted']) == 1, outcome
    posted = outcome['posted'][0]
    assert posted['result'] is None, outcome
    # The whole refusal, not a substring: the channel is the part a caller
    # keys on, and a number alone would survive a dropped label.
    assert posted['error'] == (
        'MAIN-world eval failed: MAIN-world eval timed out after '
        + f'{_SETTLE_MS} ms'), outcome
    assert posted['world'] == 'page-main', outcome
    # Nothing left armed once the race has settled: a leaked one-shot timer
    # delays an MV3 worker suspend.
    assert outcome['remaining'] == [], outcome


def test_a_main_world_eval_settling_inside_the_bound_returns_its_value(tmp):
    """The clock is driven to strictly inside the window before the page
    promise resolves, so a ceiling that fires early is caught by the
    assertion on the real value rather than passing because nothing was ever
    crossed.
    """
    del tmp
    outcome = run_main_world_eval_inside()
    assert outcome['armed'] is True, outcome
    assert outcome['deadlines'] == [_SETTLE_MS, _SETTLE_MS], outcome
    assert outcome['got'] is True, outcome
    assert len(outcome['posted']) == 1, outcome
    posted = outcome['posted'][0]
    assert posted['result'] == 'V', outcome
    assert posted['error'] is None, outcome
    assert posted['world'] == 'page-main', outcome
    # A settled envelope still carries its own timing, the channel identity
    # intact, so a caller can tell which channel answered.
    assert isinstance(posted['exec_ms'], (int, float)), outcome
    # The fast settle is the one that must tear its timer down.
    assert outcome['remaining'] == [], outcome


def test_a_wedged_eval_probe_does_not_hold_the_worker(tmp):
    """A probe that never answers is the same shape as one that fails: the
    MAIN-world path is unavailable, so the eval is answered on CDP.

    The replay's bound covers the whole per-fix operation precisely because
    its probe is inside it; the eval path holds to the same rationale, so a
    worker serving no later command is not the price of diagnosing a page.
    """
    del tmp
    outcome = run_main_world_eval_probe_hang()
    assert outcome['armed'] is True, outcome
    assert outcome['deadlines'] == [_SETTLE_MS], outcome
    # Nothing is posted while the probe is open.
    assert outcome['postedBeforeClock'] == 0, outcome
    assert outcome['got'] is True, outcome
    # The bound released the routing, and the eval was dispatched on CDP.
    assert outcome['dispatches'] == 1, outcome
    assert outcome['posted'] == [{
        'result': 2, 'error': None, 'world': 'cdp', 'exec_ms': None,
    }], outcome
    assert outcome['remaining'] == [], outcome


def test_a_stuck_hotfix_fix_does_not_block_a_later_fix(tmp):
    """Replay is sequential, so one fix whose MAIN-world injection never
    settles stopped every later stored fix on every load of that page, with
    nothing in the console. The per-fix refusal must reach the operator's
    only surface and name the limit it enforced.
    """
    del tmp
    outcome = run_hotfix_replay_timeout()
    assert outcome['armed'] is True, outcome
    assert outcome['deadlines'] == [_SETTLE_MS, _SETTLE_MS], outcome
    assert outcome['fix1Started'] is True, outcome
    # The stuck fix is the only thing that fails; the fix after it runs.
    assert outcome['ranSecond'] is True, outcome
    errors = _replay_errors(outcome['replay'])
    assert len(errors) == 1, outcome
    assert errors[0]['text'] == (
        '[Daedalus] hotfix replay failed on tab 7: fix1: '
        + f'MAIN-world hotfix fix timed out after {_SETTLE_MS} ms'), outcome
    allClear = [e for e in outcome['replay'] if e['level'] == 'log']
    assert not allClear, outcome
    assert outcome['remaining'] == [], outcome


def test_a_cdp_routed_stuck_fix_names_the_channel_that_ran(tmp):
    """An operator reading a MAIN-world label for a fix that never entered
    the MAIN world is sent to the wrong subsystem, so the channel in the
    refusal is the one the replay actually took.
    """
    del tmp
    outcome = run_hotfix_replay_cdp_timeout()
    assert outcome['armed'] is True, outcome
    # Both fixes dispatched through CDP and were bounded there.
    assert outcome['dispatches'] == 2, outcome
    assert outcome['deadlines'] == [_SETTLE_MS, _SETTLE_MS], outcome
    errors = _replay_errors(outcome['replay'])
    assert len(errors) == 1, outcome
    assert errors[0]['text'] == (
        '[Daedalus] hotfix replay failed on tab 7: '
        + f'fix1: CDP hotfix fix timed out after {_SETTLE_MS} ms; '
        + f'fix2: CDP hotfix fix timed out after {_SETTLE_MS} ms'), outcome
    assert outcome['remaining'] == [], outcome


def test_the_replay_bound_covers_the_whole_per_fix_operation(tmp):
    """The first fix's source-free probe never answers, so its injection is
    never reached. A bound placed around the injection alone would sit
    downstream of the hang and let this fix stop the ones after it.
    """
    del tmp
    outcome = run_hotfix_replay_probe_hang()
    assert outcome['armed'] is True, outcome
    assert outcome['deadlines'] == [_SETTLE_MS, _SETTLE_MS], outcome
    # The wedged fix never reached its own source; the later fix still ran.
    assert outcome['fix1Ran'] is False, outcome
    assert outcome['ranSecond'] is True, outcome
    errors = _replay_errors(outcome['replay'])
    assert len(errors) == 1, outcome
    # The fix wedged before its channel was chosen, so the refusal names
    # none: the operator must not be sent to a channel the fix never took.
    assert errors[0]['text'] == (
        '[Daedalus] hotfix replay failed on tab 7: '
        + f'fix1: hotfix fix timed out after {_SETTLE_MS} ms'), outcome
    assert outcome['remaining'] == [], outcome


def test_a_hotfix_fix_settling_inside_the_bound_is_not_a_failure(tmp):
    del tmp
    outcome = run_hotfix_replay_inside()
    assert outcome['armed'] is True, outcome
    assert outcome['deadlines'] == [_SETTLE_MS, _SETTLE_MS], outcome
    assert outcome['cleared'] is True, outcome
    assert outcome['ranSecond'] is True, outcome
    assert not outcome['errors'], outcome
    assert outcome['clear'], outcome
    # Both fixes settled inside the window, so both bounds tore down.
    assert outcome['remaining'] == [], outcome


def test_the_eval_and_replay_paths_arm_only_the_named_ceiling(tmp):
    """The deadlines are read from the two real runs, not from source text.

    A source-text pin cannot see a second ceiling worded so the refusal
    string never mentions a second number, nor a stray worker timer, so
    what the control observes is the armings themselves: each operation
    arms the one deadline the constant names, and no other.
    """
    del tmp
    eval_outcome = run_main_world_eval_timeout()
    replay_outcome = run_hotfix_replay_timeout()
    assert eval_outcome['deadlines'] == [_SETTLE_MS, _SETTLE_MS], eval_outcome
    assert replay_outcome['deadlines'] == [_SETTLE_MS, _SETTLE_MS], (
        replay_outcome)
    # The refusal each side receives names the same ceiling, in full.
    assert eval_outcome['posted'][0]['error'] == (
        'MAIN-world eval failed: MAIN-world eval timed out after '
        + f'{_SETTLE_MS} ms'), eval_outcome
    replay_errors = _replay_errors(replay_outcome['replay'])
    assert len(replay_errors) == 1, replay_outcome
    assert replay_errors[0]['text'] == (
        '[Daedalus] hotfix replay failed on tab 7: fix1: '
        + f'MAIN-world hotfix fix timed out after {_SETTLE_MS} ms'), (
            replay_outcome)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='evalrelay_')


if __name__ == '__main__':
    raise SystemExit(main())
