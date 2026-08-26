#!/usr/bin/env python3
"""What the service worker owes the bridge, across restarts and overlaps.

A delivery id is spent once even if the worker is restarted between the two
halves of that promise; a screenshot names the tab it captured; a refused
upload is an error rather than an envelope with nothing in it; a rule id of
zero does not widen into remove-all. These run the shipped background script
in a Node VM with a fake browser under it.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overlap  # noqa: E402
import _util  # noqa: E402
from _boundary import run_extension_result_boundary  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402


def test_extension_same_id_overlap_keeps_each_delivery_id(tmp):
    """Both completion orders preserve each command's server delivery id."""
    del tmp
    commands = [
        {'id': '_cookies', 'type': 'cookies', 'domain': 'owner-a',
         '_did': 'did-a'},
        {'id': '_cookies', 'type': 'cookies', 'domain': 'owner-b',
         '_did': 'did-b'},
    ]
    actual = {
        'a-first': _overlap.run_background_overlap(
            ROOT / 'extension' / 'background.js', commands,
            ['owner-a', 'owner-b']),
        'b-first': _overlap.run_background_overlap(
            ROOT / 'extension' / 'background.js', commands,
            ['owner-b', 'owner-a']),
    }
    expected = {
        'a-first': [
            {'id': '_cookies', 'owner': 'owner-a', 'deliveryId': 'did-a'},
            {'id': '_cookies', 'owner': 'owner-b', 'deliveryId': 'did-b'},
        ],
        'b-first': [
            {'id': '_cookies', 'owner': 'owner-b', 'deliveryId': 'did-b'},
            {'id': '_cookies', 'owner': 'owner-a', 'deliveryId': 'did-a'},
        ],
    }
    assert actual == expected, actual


def test_eval_relay_capacity_rejects_1001st_and_preserves_first(tmp):
    """The 1,001st relay fails while the first live relay remains valid."""
    del tmp
    actual = run_extension_result_boundary('capacity')
    assert actual == {
        'firstId': 'existing-0',
        'sentMessages': 0,
        'results': [{
            'kind': 'result',
            'url': 'https://initial.example.com/result',
            'token': 'initial-token',
            'id': 'new-at-capacity',
            'error': 'Eval relay capacity exceeded',
        }],
    }, actual


def test_eval_relay_expiry_posts_one_timeout_at_300000_ms(tmp):
    """The exact relay TTL removes the entry and posts one terminal error."""
    del tmp
    actual = run_extension_result_boundary('expiry')
    assert actual == {
        'stillPending': False,
        'results': [{
            'kind': 'result',
            'url': 'https://initial.example.com/result',
            'token': 'initial-token',
            'id': 'slow-eval',
            'error': 'Eval relay timed out after 300000 ms',
        }],
    }, actual


def test_result_route_snapshot_covers_retries_and_side_operations(tmp):
    """Config rotation cannot retarget result retries or side operations."""
    del tmp
    actual = run_extension_result_boundary('route')
    assert actual == {
        'requests': [
            {
                'kind': 'upload',
                'url': 'https://initial.example.com/upload',
                'token': 'initial-token',
                'id': 'route-snapshot',
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'route-snapshot',
                'error': None,
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'route-snapshot',
                'error': None,
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'block-route-snapshot',
                'error': None,
            },
        ],
        'excludedRequestDomains': ['initial.example.com'],
    }, actual


def test_a_targeted_screenshot_captures_the_tab_it_names(tmp):
    """Naming a tab has to select it, because capture does not.

    captureVisibleTab captures whatever is active in the WINDOW it is given,
    so a screenshot aimed at an inactive tab returned the active sibling's
    pixels under the requested tab's url and title. Nothing in the answer said
    the image was of a different page.
    """
    del tmp
    actual = run_extension_result_boundary('screenshot-target')
    assert actual['captured'] == 'captured:8', actual
    assert actual['posted'] == [
        {'tabUrl': 'about:blank#target', 'error': None}], actual
    # And the window is left as it was found.
    assert actual['activeAfter'] == 7, actual
    assert actual['activations'] == [8, 7], actual


def test_rejected_screenshot_upload_is_reported_as_an_error(tmp):
    """A 400 from /upload must not become a success envelope with no path."""
    del tmp
    actual = run_extension_result_boundary('screenshot-reject')
    assert actual == {
        'uploads': 1,
        'posted': [{
            'result': None,
            'error': 'Screenshot upload failed: invalid path component',
        }],
    }, actual


def test_failed_net_capture_setup_leaves_no_capture_and_no_attachment(tmp):
    """Attach and enable failures roll back; a detach ends the capture."""
    del tmp
    actual = run_extension_result_boundary('net-capture')
    assert actual == {
        'outcomes': [
            {
                'step': 'attach-fails',
                'result': None,
                'error': 'Another debugger is already attached',
            },
            {
                'step': 'enable-fails',
                'result': None,
                'error': 'Network.enable failed',
            },
            {
                'step': 'succeeds',
                'result': {'capturing': True, 'tabId': 7},
                'error': None,
            },
            {
                'step': 'after-detach',
                'result': {'capturing': True, 'tabId': 7},
                'error': None,
            },
        ],
        # One attach per call — a failed setup never answers `already: true`.
        'attachCalls': 4,
        # Only the enable failure had an attachment to give back.
        'detachCalls': 1,
    }, actual


def test_concurrent_hotfix_stores_both_survive(tmp):
    """Two stores dispatched together must both be in the record afterwards."""
    del tmp
    actual = run_extension_result_boundary('hotfix-race')
    assert actual == {
        'posted': [
            {
                'result': {'stored': 'fix-a', 'total': 1, 'permanent': False},
                'error': None,
            },
            {
                'result': {'stored': 'fix-b', 'total': 2, 'permanent': False},
                'error': None,
            },
        ],
        'storedIds': ['fix-a', 'fix-b'],
    }, actual


def test_a_delivery_id_is_spent_once_across_worker_restarts(tmp):
    """At-most-once has to survive the worker, or it is at-most-once per boot.

    The ledger of spent delivery ids was module state, so an MV3 restart
    emptied it. The bridge redelivers a command whose socket write succeeded
    but whose unlink did not, which is exactly the case dedup exists for — and
    a worker that restarted in between executed it a second time.
    """
    del tmp
    actual = run_extension_result_boundary('dedup-restart')
    assert actual['created'] == 1, actual
    assert actual['posted'].count('did-dedup-1') == 1, actual


def test_clearing_cookies_removes_the_partitioned_ones_too(tmp):
    """A cookie the browser refused to remove must not be counted as removed.

    `chrome.cookies.remove` matches a partitioned cookie only when the
    partition is named, and the call dropped `partitionKey` — so a CHIPS
    cookie stayed readable while the count said it had gone. The count was
    incremented per iteration rather than per removal, which is what let the
    two disagree in the first place.
    """
    del tmp
    actual = run_extension_result_boundary('clear-partitioned')
    assert actual['remaining'] == [], actual
    assert len(actual['posted']) == 1, actual
    assert actual['posted'][0]['error'] is None, actual
    assert actual['posted'][0]['result']['removed'] == 2, actual
    assert actual['posted'][0]['result']['failed'] == [], actual
    partitioned = [call for call in actual['removeCalls']
                   if call['partitionKey']]
    assert len(partitioned) == 1, actual['removeCalls']


def test_rule_id_zero_is_refused_rather_than_removing_everything(tmp):
    """A specific id that is invalid must not widen into remove-all.

    `if (cmd.ruleId)` is false for 0, so `unblock-requests` with ruleId 0 fell
    through to the branch that removes every session rule and reported them as
    removed. The narrowest possible request destroyed the most.
    """
    del tmp
    actual = run_extension_result_boundary('unblock-zero')
    assert actual['installedIds'] == [9001, 9002, 9003], actual
    assert len(actual['posted']) == 1, actual
    assert actual['posted'][0]['error'], actual
    assert actual['posted'][0]['removed'] is None, actual


def test_block_rule_ids_survive_a_worker_restart(tmp):
    """Session rules outlive the worker, so ids must not restart at the base."""
    del tmp
    actual = run_extension_result_boundary('block-rule-restart')
    assert actual == {
        'posted': [
            {'ruleId': 9001, 'error': None},
            {'ruleId': 9002, 'error': None},
            {'ruleId': 9003, 'error': None},
            {'ruleId': 9004, 'error': None},
        ],
        'installedIds': [9001, 9002, 9003, 9004],
    }, actual


def test_the_gm_fetch_relay_bounds_the_response_while_it_reads(tmp):
    """An oversized response is abandoned at the limit, not measured after it.

    The shim is injected into every matching top-level page, so any visited
    site can invoke this relay. It had no ceiling at all: an 8 MiB response
    was materialized whole and then copied again into 11,184,812 base64
    characters. Reading through a counter is the part that matters — a size
    check after `arrayBuffer()` learns the size only once the worker is
    already holding every byte.
    """
    del tmp
    actual = run_extension_result_boundary('fetch-bound')
    steps = {step['name']: step for step in actual['steps']}
    assert len(steps) == 4, actual

    mib = 1024 * 1024
    # Exactly the default is allowed: the limit is a ceiling, not a threshold
    # the last permitted byte trips.
    at_default = steps['at the default']
    assert at_default['error'] is None, at_default
    assert at_default['dataLength'] == 8 * mib, at_default
    assert at_default['cancelled'] is False, at_default

    over = steps['over the default']
    assert over['tooLarge'] is True, over
    assert '8388608' in (over['error'] or ''), over
    assert over['dataLength'] is None, over
    # The read stopped at the chunk that crossed the limit and cancelled the
    # body, rather than draining the response and rejecting it afterwards.
    assert over['chunksRead'] == 9, over
    assert over['cancelled'] is True, over

    raised = steps['raised by opt-in']
    assert raised['error'] is None, raised
    assert raised['dataLength'] == 12 * mib, raised

    # The binary path still base64s, because chrome.runtime.sendMessage is
    # JSON-serialized and an ArrayBuffer does not survive it.
    binary = steps['binary under the default']
    assert binary['error'] is None, binary
    assert binary['dataLength'] > mib, binary

    assert actual['limits'] == {
        # Anything that is not a usable positive number means "no preference".
        'omitted': 8 * mib,
        'zero': 8 * mib,
        'negative': 8 * mib,
        'text': 8 * mib,
        # A caller may ask for LESS, which is a safer request, not a weaker
        # one — including the floor of a fractional value.
        'fractional': 1,
        'below the default': 1024,
        # And may not ask its way past the ceiling.
        'above the ceiling': 64 * mib,
    }, actual['limits']

    # The diagnostic ring records the refusal as its own outcome and the raw
    # byte count for the rest; the text path used to record characters.
    assert [entry['error'] for entry in actual['timings']] == [
        None, 'too-large', None, None], actual['timings']
    assert [entry['bodySize'] for entry in actual['timings']] == [
        8 * mib, None, 12 * mib, mib], actual['timings']


def test_the_relay_ceiling_is_declared_once_and_bounds_the_default(tmp):
    """Both limits live in the worker, and the ceiling is the larger one."""
    del tmp
    source = (EXTENSION_ROOT / 'background.js').read_text(encoding='utf-8')
    found = dict(re.findall(
        r'const (GM_FETCH_MAX_RESPONSE|GM_FETCH_RESPONSE_CEILING) = '
        r'([0-9 *]+);', source))
    assert set(found) == {'GM_FETCH_MAX_RESPONSE',
                          'GM_FETCH_RESPONSE_CEILING'}, found
    values = {}
    for name, expression in found.items():
        product = 1
        for factor in expression.split('*'):
            product *= int(factor.strip())
        values[name] = product
    assert values['GM_FETCH_MAX_RESPONSE'] > 0, values
    assert (values['GM_FETCH_RESPONSE_CEILING']
            >= values['GM_FETCH_MAX_RESPONSE']), values


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='extboundary_')


if __name__ == '__main__':
    raise SystemExit(main())
