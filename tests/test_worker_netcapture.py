#!/usr/bin/env python3
"""extension/worker/netcapture.js — capture, stop, retrieval, event wiring.

Each test drives the real dispatcher and the worker's own registered
listeners, and asserts the posted answer, the calls made and the state left.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _netcapture_harness import (  # noqa: E402
    ATTACH, BOUND, DETACH, ENABLE, FROZEN, QUERY, SEND, answers, apis,
    attach_call, buffered, cmd, detach_call, entries, errors, event,
    finished, ids, keep, read, request, response, run_capture, send_call,
    sent_bodies, start, stop)


# ─── _netCaptureLimit ───
def test_the_limit_defaults_to_one_thousand_for_three_absent_spellings(tmp):
    del tmp
    # A dropped key, an explicit null and an empty string: three
    # spellings of "not supplied", each taking the default arm.
    outcome = run_capture([], probes=['_netCaptureLimit(undefined)',
                                      '_netCaptureLimit(null)',
                                      "_netCaptureLimit('')"])
    assert [probe.get('value') for probe in outcome['probes']] == [
        1000, 1000, 1000], outcome


def test_the_limit_returns_a_value_inside_its_bound_unchanged(tmp):
    del tmp
    outcome = run_capture([], probes=['_netCaptureLimit(1)',
                                      '_netCaptureLimit(19999)'])
    assert [probe.get('value') for probe in outcome['probes']] == [
        1, 19999], outcome


def test_the_limit_clamps_the_maximum_from_both_sides(tmp):
    del tmp
    # 20000 is inside the bound, 20001 is not; one alone cannot.
    outcome = run_capture([], probes=['_netCaptureLimit(20000)',
                                      '_netCaptureLimit(20001)'])
    assert [probe.get('value') for probe in outcome['probes']] == [
        20000, 20000], outcome


def test_the_limit_refuses_a_non_integer_with_its_message(tmp):
    del tmp
    outcome = run_capture([], probes=["_netCaptureLimit('many')",
                                      '_netCaptureLimit(2.5)'])
    assert [probe.get('error') for probe in outcome['probes']] == [
        BOUND, BOUND], outcome


def test_the_limit_refuses_every_value_below_one(tmp):
    del tmp
    # -1 is the value the module's own comment records as once accepted: it
    # evicted the only event on arrival and left an empty capture.
    outcome = run_capture([], probes=['_netCaptureLimit(0)',
                                      '_netCaptureLimit(-1)',
                                      '_netCaptureLimit(-1000000)'])
    assert [probe.get('error') for probe in outcome['probes']] == [
        BOUND] * 3, outcome


# ─── _netEventHandler ───
def test_an_event_for_a_tab_with_no_capture_is_ignored(tmp):
    del tmp
    # The silent path returns before it reads params at all.
    outcome = run_capture([
        request(9, 'r1', 'https://a.example.com/one'),
        start(9), read(9),
    ])
    assert answers(outcome)[-1] == {
        'tabId': 9, 'count': 0, 'requests': []}, outcome


def test_a_request_event_builds_the_entry_from_the_event(tmp):
    del tmp
    entry = buffered([start(), request(5, 'r1', 'https://a.example.com/one',
                                       type='Document')])[0]
    assert entry == {
        'requestId': 'r1',
        'url': 'https://a.example.com/one',
        'method': 'GET',
        'headers': {},
        'postData': None,
        'type': 'Document',
        'frameId': '',
        'ts': FROZEN,
        'initiator': '',
    }, entry


def test_a_request_defaults_an_absent_post_data_to_null(tmp):
    del tmp
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                    )[0]['postData'] is None


def test_a_request_carries_a_post_data_it_was_given(tmp):
    del tmp
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a',
                                      post='q=1')])[0]['postData'] == 'q=1'


def test_a_request_defaults_an_absent_type_to_the_empty_string(tmp):
    del tmp
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                    )[0]['type'] == ''


def test_a_request_defaults_an_absent_frame_id_to_the_empty_string(tmp):
    del tmp
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                    )[0]['frameId'] == ''


def test_a_request_defaults_an_absent_initiator_to_the_empty_string(tmp):
    del tmp
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                    )[0]['initiator'] == ''


def test_a_request_takes_the_initiator_url_when_it_carries_one(tmp):
    del tmp
    entry = buffered([start(), request(
        5, 'r1', 'https://a.example.com/a',
        initiator={'url': 'https://a.example.com/seed', 'type': 'parser'})
    ])[0]
    assert entry['initiator'] == 'https://a.example.com/seed', entry


def test_a_request_falls_back_to_the_initiator_type_without_a_url(tmp):
    del tmp
    entry = buffered([start(), request(
        5, 'r1', 'https://a.example.com/a',
        initiator={'type': 'parser'})])[0]
    assert entry['initiator'] == 'parser', entry


def test_a_request_prefers_the_wall_time_over_the_protocol_timestamp(tmp):
    del tmp
    # Both supplied: the timestamp arm would answer 1700000000500.
    entry = buffered([start(), request(
        5, 'r1', 'https://a.example.com/a',
        wallTime=1700000000.5, timestamp=1700000000.5)])[0]
    assert entry['ts'] == 1700000000.5, entry


def test_a_request_multiplies_the_protocol_timestamp_by_one_thousand(tmp):
    del tmp
    entry = buffered([start(), request(5, 'r1', 'https://a.example.com/a',
                                       timestamp=1700000000.5)])[0]
    assert entry['ts'] == 1700000000500, entry


def test_a_request_without_either_timestamp_reads_the_clock(tmp):
    del tmp
    # Frozen clock: this pins what the code computed, not elapsed time.
    entry = buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                     )[0]
    assert entry['ts'] == FROZEN, entry


def test_a_response_event_attaches_the_response_fields(tmp):
    del tmp
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        response(5, 'r1', status=204, statusText='No Content',
                 headers={'X-Trace': 'abc'}, mimeType='text/plain',
                 url='https://a.example.com/final'),
    ])[0]
    assert entry['status'] == 204, entry
    assert entry['statusText'] == 'No Content', entry
    assert entry['responseHeaders'] == {'X-Trace': 'abc'}, entry
    assert entry['mimeType'] == 'text/plain', entry
    assert entry['responseUrl'] == 'https://a.example.com/final', entry


def test_a_response_event_defaults_each_absent_field(tmp):
    del tmp
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        response(5, 'r1', status=200),
    ])[0]
    assert entry['statusText'] == '' and entry['mimeType'] == '', entry
    assert entry['responseHeaders'] == {} and entry['responseUrl'] == '', entry


def test_a_response_event_for_an_unknown_request_id_is_ignored(tmp):
    del tmp
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        response(5, 'absent', status=500),
    ])[0]
    assert 'status' not in entry and entry['requestId'] == 'r1', entry


def test_loading_finished_marks_the_entry_and_carries_its_length(tmp):
    del tmp
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        finished(5, 'r1', encodedDataLength=4096),
    ])[0]
    assert entry['done'] is True and entry['encodedLength'] == 4096, entry


def test_loading_finished_defaults_a_missing_length_to_zero(tmp):
    del tmp
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        finished(5, 'r1'),
    ])[0]
    assert entry['done'] is True and entry['encodedLength'] == 0, entry


def test_loading_finished_for_an_unknown_request_id_is_ignored(tmp):
    del tmp
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        finished(5, 'absent', encodedDataLength=10),
    ])[0]
    assert 'done' not in entry and 'encodedLength' not in entry, entry


def test_an_unrecognised_event_method_is_ignored(tmp):
    del tmp
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        event(5, 'Network.requestWillBeSentExtraInfo', headers={'a': 'b'}),
        event(5, 'Page.loadEventFired', timestamp=1.0),
    ])[0]
    assert entry == {
        'requestId': 'r1', 'url': 'https://a.example.com/a',
        'method': 'GET', 'headers': {}, 'postData': None, 'type': '',
        'frameId': '', 'ts': FROZEN, 'initiator': '',
    }, entry


def test_nothing_is_evicted_at_exactly_the_limit(tmp):
    del tmp
    entries_at_limit = buffered(
        [start(maxRequests=2),
         request(5, 'r1', 'https://a.example.com/one'),
         request(5, 'r2', 'https://a.example.com/two')])
    assert [entry['requestId'] for entry in entries_at_limit] == [
        'r1', 'r2'], entries_at_limit


def test_the_oldest_is_evicted_past_the_limit(tmp):
    del tmp
    over = buffered(
        [start(maxRequests=2),
         request(5, 'r1', 'https://a.example.com/one'),
         request(5, 'r2', 'https://a.example.com/two'),
         request(5, 'r3', 'https://a.example.com/three')])
    # The survivors are named, not just counted: a count alone is satisfied
    # by evicting the newest instead.
    assert [entry['requestId'] for entry in over] == ['r2', 'r3'], over
    assert [entry['url'] for entry in over] == [
        'https://a.example.com/two', 'https://a.example.com/three'], over


# ─── handleNetCapture ───
def test_a_capture_without_a_tab_id_resolves_the_active_tab(tmp):
    del tmp
    outcome = run_capture([{'command': cmd('net-capture')}])
    assert apis(outcome, QUERY) == [
        [QUERY, [{'active': True, 'currentWindow': True}]]], outcome
    assert apis(outcome, ATTACH) == [attach_call(7)], outcome
    assert answers(outcome)[-1] == {'capturing': True, 'tabId': 7}, outcome


def test_a_capture_with_no_active_tab_is_refused(tmp):
    del tmp
    outcome = run_capture([{'command': cmd('net-capture')}], activeTabs=[])
    assert errors(outcome) == ['No active tab'], outcome
    assert apis(outcome, ATTACH) == [], outcome
    assert outcome['state']['captures'] == [], outcome


def test_a_capture_parses_a_numeric_string_tab_id(tmp):
    del tmp
    outcome = run_capture([{'command': cmd('net-capture', tabId='5')}])
    assert apis(outcome, ATTACH) == [attach_call(5)], outcome
    assert answers(outcome)[-1] == {'capturing': True, 'tabId': 5}, outcome


def test_a_non_numeric_tab_id_reaches_the_attach_as_nan(tmp):
    del tmp
    # `null` is how this double's JSON copy spells the NaN parseInt('abc')
    # produced, which is what chrome.debugger.attach then received.
    outcome = run_capture([{'command': cmd('net-capture', tabId='abc')}])
    assert apis(outcome, ATTACH) == [
        [ATTACH, [{'tabId': None}, '1.3']]], outcome
    assert outcome['state']['captures'] == [
        {'tabId': 'NaN', 'maxRequests': 1000, 'requestIds': []}], outcome


def test_a_capture_without_a_limit_publishes_the_default(tmp):
    del tmp
    outcome = run_capture([start()])
    assert outcome['state']['captures'] == [
        {'tabId': '5', 'maxRequests': 1000, 'requestIds': []}], outcome


def test_a_capture_clamps_a_limit_above_the_maximum(tmp):
    del tmp
    outcome = run_capture([start(maxRequests=50000)])
    assert outcome['state']['captures'] == [
        {'tabId': '5', 'maxRequests': 20000, 'requestIds': []}], outcome
    assert apis(outcome, ATTACH) == [attach_call(5)], outcome


def test_a_limit_out_of_bounds_is_refused_before_the_attach(tmp):
    del tmp
    # The single attach on record is the oracle the absence rests on: a
    # limit checked after it would add a second, for the refused tab.
    outcome = run_capture([start(5, maxRequests=-1), start(6, maxRequests=3)])
    assert errors(outcome) == [BOUND, None], outcome
    assert apis(outcome, ATTACH) == [attach_call(6)], outcome
    assert outcome['state']['captures'] == [
        {'tabId': '6', 'maxRequests': 3, 'requestIds': []}], outcome


def test_a_second_capture_on_the_same_tab_answers_already(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'), start(),
    ])
    assert answers(outcome)[1] == {
        'already': True, 'tabId': 5, 'buffered': 1}, outcome
    assert apis(outcome, ATTACH) == [attach_call(5)], outcome


def test_a_kept_cdp_session_skips_the_attach_and_enables_the_domain(tmp):
    del tmp
    outcome = run_capture([keep(5), start(5)])
    assert apis(outcome, ATTACH) == [attach_call(5)], outcome
    assert apis(outcome, SEND) == [
        send_call(5, 'Runtime.enable'), send_call(5, ENABLE)], outcome
    assert answers(outcome)[-1] == {'capturing': True, 'tabId': 5}, outcome


def test_an_attach_failure_publishes_no_capture(tmp):
    del tmp
    outcome = run_capture([start()], chromeReject={
        'debugger.attach': 'Another debugger is already attached'})
    assert errors(outcome) == ['Another debugger is already attached'], outcome
    assert outcome['state']['captures'] == [], outcome
    assert apis(outcome, SEND) == [], outcome


def test_enabling_the_domain_after_an_attach_detaches_and_publishes_nothing(
        tmp):
    del tmp
    outcome = run_capture([start()], sendCommandReject={
        ENABLE: 'Network.enable failed'})
    assert errors(outcome) == ['Network.enable failed'], outcome
    assert apis(outcome, DETACH) == [detach_call(5)], outcome
    assert outcome['state']['captures'] == [], outcome


def test_a_capture_attaches_before_enabling_the_domain(tmp):
    del tmp
    outcome = run_capture([start()])
    assert apis(outcome, ATTACH, SEND) == [
        attach_call(5), send_call(5, ENABLE)], outcome


# ─── handleNetCaptureStop ───
def test_a_stop_without_a_capture_reports_not_capturing(tmp):
    del tmp
    outcome = run_capture([stop(5)])
    assert answers(outcome) == [{
        'stopped': False, 'reason': 'not capturing'}], outcome
    assert apis(outcome, DETACH) == [], outcome


def test_a_stop_without_a_tab_id_resolves_the_active_tab(tmp):
    del tmp
    outcome = run_capture([start(12), {'command': cmd('net-capture-stop')}],
                          activeTabs=[{'id': 12,
                                       'url': 'https://c.example.com'}])
    assert apis(outcome, QUERY) == [
        [QUERY, [{'active': True, 'currentWindow': True}]]], outcome
    assert answers(outcome)[-1]['tabId'] == 12, outcome
    assert apis(outcome, DETACH) == [detach_call(12)], outcome


def test_a_stop_with_no_active_tab_is_refused(tmp):
    del tmp
    outcome = run_capture([{'command': cmd('net-capture-stop')}],
                          activeTabs=[])
    assert errors(outcome) == ['No active tab'], outcome
    assert apis(outcome, DETACH) == [], outcome


def test_a_stop_reports_a_tab_lookup_that_failed(tmp):
    del tmp
    # Inside the try, so the rejection reaches the handler's own catch.
    outcome = run_capture([{'command': cmd('net-capture-stop')}],
                          chromeReject={'tabs.query': 'no window for query'})
    assert errors(outcome) == ['no window for query'], outcome
    assert answers(outcome) == [None], outcome
    assert apis(outcome, DETACH) == [], outcome


def test_a_stop_without_bodies_sends_no_body_request(tmp):
    del tmp
    # The trailing read is the witness: it asks for the same entry's body
    # and the request appears, so the list above counts a working counter.
    outcome = run_capture([
        start(5), request(5, 'r1', 'https://a.example.com/a'),
        finished(5, 'r1', encodedDataLength=10), start(6),
        request(6, 'r2', 'https://b.example.com/b'),
        finished(6, 'r2'), stop(5), read(6, bodies=True),
    ], bodies={'r2': {'body': 'served'}})
    assert sent_bodies(outcome) == ['r2'], outcome
    assert 'body' not in entries(outcome, 2)[0], outcome
    assert entries(outcome, -1)[0]['body'] == 'served', outcome


def test_a_stop_with_bodies_fetches_only_finished_entries(tmp):
    del tmp
    # Two finished, one still in flight; the one left alone is pinned whole.
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        request(5, 'r2', 'https://a.example.com/two'),
        request(5, 'r3', 'https://a.example.com/three'),
        finished(5, 'r1'), finished(5, 'r3', encodedDataLength=2),
        stop(bodies=True),
    ], bodies={'r1': {'body': 'first'}, 'r3': {'body': 'third'}},
        frozenNow=FROZEN)
    assert sent_bodies(outcome) == ['r1', 'r3'], outcome
    bodies_by_id = {entry['requestId']: entry for entry in entries(outcome)}
    assert bodies_by_id['r1']['body'] == 'first', outcome
    assert bodies_by_id['r3']['body'] == 'third', outcome
    assert bodies_by_id['r2'] == {
        'requestId': 'r2', 'url': 'https://a.example.com/two',
        'method': 'GET', 'headers': {}, 'postData': None, 'type': '',
        'frameId': '', 'ts': FROZEN, 'initiator': '',
    }, outcome


def test_a_stop_does_not_refetch_a_body_an_entry_already_carries(tmp):
    del tmp
    # The read fetched it; the stop that follows adds no request.
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        request(5, 'r2', 'https://a.example.com/two'),
        finished(5, 'r1'), read(bodies=True), stop(bodies=True),
    ], bodies={'r1': {'body': 'first'}, 'r2': {'body': 'unused'}})
    assert sent_bodies(outcome) == ['r1'], outcome
    assert entries(outcome)[0]['body'] == 'first', outcome


def test_a_stop_records_the_base64_flag_it_was_given(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        request(5, 'r2', 'https://a.example.com/two'),
        finished(5, 'r1'), finished(5, 'r2'),
        stop(bodies=True),
    ], bodies={'r1': {'body': 'first', 'base64Encoded': True},
               'r2': {'body': 'second'}})
    flags = {entry['requestId']: entry['bodyBase64']
             for entry in entries(outcome)}
    assert flags == {'r1': True, 'r2': False}, outcome


def test_a_body_the_worker_could_not_fetch_leaves_the_entry_without_one(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        finished(5, 'r1'), stop(bodies=True),
    ], bodies={'r1': {'throw': 'No resource with given identifier'}})
    assert sent_bodies(outcome) == ['r1'], outcome
    assert 'body' not in entries(outcome)[0], outcome
    assert answers(outcome)[-1]['count'] == 1, outcome


def test_a_stop_reports_the_buffered_count_and_clears_the_capture(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        request(5, 'r2', 'https://a.example.com/two'), stop(),
    ])
    assert answers(outcome)[-1]['count'] == 2, outcome
    assert outcome['state']['captures'] == [], outcome


def test_a_stop_returns_an_array_the_caller_may_hold_and_change(tmp):
    del tmp
    # The array the answer carried is emptied after the stop; a later
    # capture on the same tab is the buffer that must be unaffected.
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        request(5, 'r2', 'https://a.example.com/two'), stop(),
        {'mutateReturnedRequests': 'clear'},
        start(), request(5, 'r3', 'https://a.example.com/three'), read(),
    ])
    assert ids(outcome) == ['r3'], outcome
    assert answers(outcome)[1]['count'] == 2, outcome


def test_a_stop_detaches_only_the_attachment_it_owns(tmp):
    del tmp
    # Tab 5's attachment is a kept session's, tab 6's this capture's; the
    # one detach, for the second, is the oracle for the first's absence.
    outcome = run_capture([
        keep(5), start(5), start(6),
        request(5, 'r1', 'https://a.example.com/one'),
        request(6, 'r2', 'https://a.example.com/two'),
        stop(5), stop(6),
    ])
    assert apis(outcome, DETACH) == [detach_call(6)], outcome
    assert outcome['state']['cdpSessions'] == ['5'], outcome


# ─── handleNetCaptureGet ───
def test_a_read_without_a_capture_reports_not_capturing(tmp):
    del tmp
    outcome = run_capture([read()])
    assert errors(outcome) == ['Not capturing on this tab'], outcome


def test_a_read_without_a_tab_id_resolves_the_active_tab(tmp):
    del tmp
    outcome = run_capture([{'command': cmd('net-capture')},
                           {'command': cmd('net-capture-get')}],
                          activeTabs=[{'id': 12,
                                       'url': 'https://b.example.com'}])
    assert apis(outcome, QUERY) == [
        [QUERY, [{'active': True, 'currentWindow': True}]],
        [QUERY, [{'active': True, 'currentWindow': True}]]], outcome
    assert answers(outcome)[-1] == {
        'tabId': 12, 'count': 0, 'requests': []}, outcome


def test_a_read_without_a_filter_returns_the_whole_buffer(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        request(5, 'r2', 'https://b.example.com/two'), read(),
    ])
    assert answers(outcome)[-1]['count'] == 2, outcome


def test_a_read_filter_matches_the_url_case_insensitively(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/Alpha'),
        request(5, 'r2', 'https://b.example.com/beta'), read(filter='alpha'),
    ])
    # The FILTERED length, not the buffer's: a filter-blind read says 2.
    assert answers(outcome)[-1]['count'] == 1, outcome
    assert ids(outcome) == ['r1'], outcome


def test_a_read_filter_matches_a_type_the_url_does_not_carry(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', type='Stylesh'),
        request(5, 'r2', 'https://a.example.com/two'), read(filter='stylesh'),
    ])
    assert answers(outcome)[-1]['count'] == 1, outcome
    assert ids(outcome) == ['r1'], outcome


def test_a_read_filter_excludes_every_entry_that_does_not_match(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        request(5, 'r2', 'https://a.example.com/two'), read(filter='zzz'),
    ])
    assert answers(outcome)[-1] == {
        'tabId': 5, 'count': 0, 'requests': []}, outcome


def test_a_read_with_an_invalid_filter_posts_the_error(tmp):
    del tmp
    outcome = run_capture([start(), read(filter='([')])
    assert answers(outcome) == [{'capturing': True, 'tabId': 5}, None], outcome
    assert errors(outcome) == [None, 'Invalid regular expression: '
                               '/([/i: Unterminated character class'], outcome


def test_a_read_with_bodies_fetches_only_finished_bodiless_entries(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        request(5, 'r2', 'https://a.example.com/two'),
        finished(5, 'r1'), read(bodies=True), read(bodies=True),
    ], bodies={'r1': {'body': 'first'}})
    # The first read fetched it; the second found the body already there.
    assert sent_bodies(outcome) == ['r1'], outcome
    assert entries(outcome)[0]['body'] == 'first', outcome
    assert 'body' not in entries(outcome)[1], outcome


def test_a_read_with_bodies_fetches_only_the_filtered_entries(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        request(5, 'r2', 'https://b.example.com/two'),
        finished(5, 'r1'), finished(5, 'r2'),
        read(filter='one', bodies=True),
    ], bodies={'r1': {'body': 'kept'}, 'r2': {'body': 'skipped'}},
        frozenNow=FROZEN)
    assert sent_bodies(outcome) == ['r1'], outcome
    assert entries(outcome) == [
        {'requestId': 'r1', 'url': 'https://a.example.com/one',
         'method': 'GET', 'headers': {}, 'postData': None, 'type': '',
         'frameId': '', 'ts': FROZEN, 'initiator': '', 'done': True,
         'encodedLength': 0, 'body': 'kept', 'bodyBase64': False}], outcome


def test_a_read_records_the_base64_flag_it_was_given(tmp):
    del tmp
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one'),
        finished(5, 'r1'), read(bodies=True),
    ], bodies={'r1': {'body': 'first', 'base64Encoded': True}})
    assert entries(outcome)[0]['bodyBase64'] is True, outcome


# ─── module-level wiring ───
def test_the_debugger_event_target_carries_the_module_handler(tmp):
    del tmp
    outcome = run_capture([], probes=[
        'chrome.debugger.onEvent.listeners.length',
        'chrome.debugger.onEvent.listeners[0] === _netEventHandler'])
    assert [probe.get('value') for probe in outcome['probes']] == [
        1, True], outcome


def test_events_reach_the_module_through_the_registered_listener(tmp):
    del tmp
    # The accumulation every other event test observes IS this wiring.
    entries_through_listener = buffered(
        [start(), request(5, 'r1', 'https://a.example.com/one')])
    assert [entry['requestId'] for entry in entries_through_listener] == [
        'r1'], entries_through_listener


def test_a_tab_close_releases_both_maps_for_that_tab(tmp):
    del tmp
    outcome = run_capture([
        keep(5), start(6), request(6, 'r1', 'https://a.example.com/one'),
        {'tabRemoved': 5}, {'tabRemoved': 6},
    ])
    assert outcome['state'] == {'captures': [], 'cdpSessions': []}, outcome
    assert apis(outcome, DETACH) == [
        detach_call(5), detach_call(6)], outcome


def test_a_tab_close_with_neither_map_holding_it_does_not_detach(tmp):
    del tmp
    # Tab 6 holds nothing, so tab 5's detach is the live oracle.
    outcome = run_capture([
        start(5), request(5, 'r1', 'https://a.example.com/one'),
        {'tabRemoved': 6},
    ])
    assert apis(outcome, DETACH) == [], outcome
    assert outcome['state']['captures'] == [
        {'tabId': '5', 'maxRequests': 1000, 'requestIds': ['r1']}], outcome


def test_a_detach_clears_both_maps_for_its_own_tab(tmp):
    del tmp
    outcome = run_capture([
        keep(5), keep(6), start(5), {'debuggerDetached': {'tabId': 5}},
    ])
    assert outcome['state'] == {'captures': [], 'cdpSessions': ['6']}, outcome


def test_a_detach_without_a_tab_id_changes_nothing(tmp):
    del tmp
    # The oracle for the two tabId-less detaches is this run's state.
    outcome = run_capture([
        keep(5), start(5), start(6),
        {'debuggerDetached': {}},
        {'debuggerDetached': {'tabId': None}},
    ])
    assert outcome['state'] == {
        'captures': [
            {'tabId': '5', 'maxRequests': 1000, 'requestIds': []},
            {'tabId': '6', 'maxRequests': 1000, 'requestIds': []}],
        'cdpSessions': ['5']}, outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='netcapture_')


if __name__ == '__main__':
    raise SystemExit(main())
