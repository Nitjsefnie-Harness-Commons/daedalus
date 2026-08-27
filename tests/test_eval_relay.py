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
    assert 'inspector detached mid-evaluation' in (actual['error'] or ''), actual


def test_cdp_eval_releases_every_remote_handle_in_held_sessions(tmp):
    """CDP routes preserve transport fields and release every handle."""
    del tmp
    actual = run_cdp_handle_lifecycle()
    transport = {
        'replMode': True,
        'awaitPromise': False,
        'returnByValuePresent': False,
    }
    assert actual == {
        'released': [
            'compile-exception',
            'compile-result',
            'pending-late',
            'pending-original',
            'reject-exception',
            'reject-original',
            'reject-result',
            'throw-exception',
            'throw-result',
        ],
        'evalTransports': [transport, transport, transport],
        'finalAwaitPromise': [False, False, False],
        'hotfixTransports': [transport],
        'pendingHasTimeout': True,
        'resultWorlds': ['cdp', 'cdp', 'cdp'],
    }, actual


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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='evalrelay_')


if __name__ == '__main__':
    raise SystemExit(main())
