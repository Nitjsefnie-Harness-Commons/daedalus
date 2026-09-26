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
    attach_call, channels, cmd, detach_call, entries, errors, finished,
    ids, keep,
    read, request, run_capture, send_call, sent_bodies, start, stop,
    worlds)


# ─── handleNetCapture ───
def test_a_capture_without_a_tab_id_resolves_the_active_tab(tmp):
    outcome = run_capture([{'command': cmd('net-capture')}])
    assert apis(outcome, QUERY) == [
        [QUERY, [{'active': True, 'currentWindow': True}]]], outcome
    assert apis(outcome, ATTACH) == [attach_call(7)], outcome
    assert answers(outcome)[-1] == {'capturing': True, 'tabId': 7}, outcome


def test_an_explicit_null_tab_id_takes_the_active_tab_arm(tmp):
    # The query call is the whole witness: without it the arm is skipped
    # and a capture is opened under the key "null".
    outcome = run_capture([{'command': cmd('net-capture', tabId=None)}])
    assert apis(outcome, QUERY) == [
        [QUERY, [{'active': True, 'currentWindow': True}]]], outcome
    assert answers(outcome)[-1] == {'capturing': True, 'tabId': 7}, outcome


def test_a_capture_with_no_active_tab_is_refused(tmp):
    outcome = run_capture([{'command': cmd('net-capture')}], activeTabs=[])
    assert errors(outcome) == ['No active tab'], outcome
    assert apis(outcome, ATTACH) == [], outcome
    assert outcome['state']['captures'] == [], outcome


def test_a_capture_parses_a_numeric_string_tab_id(tmp):
    outcome = run_capture([{'command': cmd('net-capture', tabId='5')}])
    assert apis(outcome, ATTACH) == [attach_call(5)], outcome
    assert answers(outcome)[-1] == {'capturing': True, 'tabId': 5}, outcome


def test_a_non_numeric_tab_id_reaches_the_attach_as_nan(tmp):
    # `parseInt('abc')` is NaN and the module attaches with it unvalidated.
    # This double's attach accepts any tabId, so this pins the module's
    # arithmetic, not an outcome the real program reaches: Chrome types
    # tabId as an integer, and both clients coerce before sending. `null`
    # is how this double's JSON copy spells the NaN it was handed.
    outcome = run_capture([{'command': cmd('net-capture', tabId='abc')}])
    assert apis(outcome, ATTACH) == [
        [ATTACH, [{'tabId': None}, '1.3']]], outcome


def test_a_capture_without_a_limit_publishes_the_default(tmp):
    outcome = run_capture([start()])
    assert outcome['state']['captures'] == [
        {'tabId': '5', 'maxRequests': 1000, 'requestIds': []}], outcome


def test_a_capture_clamps_a_limit_above_the_maximum(tmp):
    outcome = run_capture([start(maxRequests=50000)])
    assert outcome['state']['captures'] == [
        {'tabId': '5', 'maxRequests': 20000, 'requestIds': []}], outcome
    assert apis(outcome, ATTACH) == [attach_call(5)], outcome


def test_a_limit_out_of_bounds_is_refused_before_the_attach(tmp):
    # The single attach on record is the oracle the absence rests on: a
    # limit checked after it would add a second, for the refused tab.
    outcome = run_capture([start(5, maxRequests=-1), start(6, maxRequests=3)])
    assert errors(outcome) == [BOUND, None], outcome
    assert apis(outcome, ATTACH) == [attach_call(6)], outcome
    assert outcome['state']['captures'] == [
        {'tabId': '6', 'maxRequests': 3, 'requestIds': []}], outcome


def test_a_second_capture_on_the_same_tab_answers_already(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'POST',
                         {'X-Probe': 'r1'}), start(),
    ])
    assert answers(outcome)[1] == {
        'already': True, 'tabId': 5, 'buffered': 1}, outcome
    assert apis(outcome, ATTACH) == [attach_call(5)], outcome


def test_a_kept_cdp_session_skips_the_attach_and_enables_the_domain(tmp):
    # The one attach on record is the kept session's own, from `keep(5)`.
    # The capture reused that attachment and opened none; what it did do
    # is send Network.enable, the second of the two calls below.
    outcome = run_capture([keep(5), start(5)])
    assert apis(outcome, ATTACH) == [attach_call(5)], outcome
    assert apis(outcome, SEND) == [
        send_call(5, 'Runtime.enable'), send_call(5, ENABLE)], outcome
    assert answers(outcome)[-1] == {'capturing': True, 'tabId': 5}, outcome


def test_an_attach_failure_publishes_no_capture(tmp):
    outcome = run_capture([start()], chromeReject={
        'debugger.attach': 'Another debugger is already attached'})
    assert errors(outcome) == ['Another debugger is already attached'], outcome
    assert outcome['state']['captures'] == [], outcome
    assert apis(outcome, SEND) == [], outcome
    # Chrome reports that refusal precisely when DevTools owns the tab, so
    # detaching here would tear down the debugger the user opened. Unlike
    # the kept-session case this one has no property to assert: the attach
    # never succeeded, so there is nothing left to use afterwards, and the
    # recorded call is the whole observable.
    assert apis(outcome, DETACH) == [], outcome


def test_enabling_the_domain_after_an_attach_detaches_and_publishes_nothing(
        tmp):
    outcome = run_capture([start()], sendCommandReject={
        ENABLE: 'Network.enable failed'})
    assert errors(outcome) == ['Network.enable failed'], outcome
    assert apis(outcome, DETACH) == [detach_call(5)], outcome
    assert outcome['state']['captures'] == [], outcome


def test_a_kept_sessions_attachment_is_still_usable_after_a_failed_enable(
        tmp):
    # A capture that JOINED a kept session's attachment did not create it,
    # so a failed enable must leave it standing. The cdp command that
    # follows is the property: it goes out on that same attachment, with
    # no second attach, and Chrome refuses a protocol call to a tab that
    # holds none. The detach count beside it is today's shape; the answer
    # is what a release the module did not own would change.
    outcome = run_capture([
        keep(5), start(5),
        {'command': cmd('cdp', tabId=5, method='Runtime.enable')},
    ], sendCommandReject={ENABLE: 'Network.enable failed'})
    assert apis(outcome, ATTACH) == [attach_call(5)], outcome
    assert apis(outcome, DETACH) == [], outcome
    assert errors(outcome) == [None, 'Network.enable failed', None], outcome
    assert answers(outcome)[-1] == {'modelled': 'Runtime.enable'}, outcome
    assert outcome['state'] == {'captures': [], 'cdpSessions': ['5']}, outcome


def test_a_failed_rollback_detach_is_swallowed_too(tmp):
    # A rollback detach that fails as well is swallowed too.
    outcome = run_capture([start()], sendCommandReject={
        ENABLE: 'Network.enable failed'},
        chromeReject={'debugger.detach': 'target closed'})
    assert apis(outcome, DETACH) == [detach_call(5)], outcome
    assert outcome['state']['captures'] == [], outcome
    assert errors(outcome) == ['Network.enable failed'], outcome


def test_a_capture_attaches_before_enabling_the_domain(tmp):
    outcome = run_capture([start()])
    assert apis(outcome, ATTACH, SEND) == [
        attach_call(5), send_call(5, ENABLE)], outcome


# ─── handleNetCaptureStop ───
def test_a_stop_without_a_capture_reports_not_capturing(tmp):
    outcome = run_capture([stop(5)])
    assert answers(outcome) == [{
        'stopped': False, 'reason': 'not capturing'}], outcome
    assert apis(outcome, DETACH) == [], outcome


def test_a_stop_without_a_tab_id_resolves_the_active_tab(tmp):
    outcome = run_capture([start(12), {'command': cmd('net-capture-stop')}],
                          activeTabs=[{'id': 12,
                                       'url': 'https://c.example.com'}])
    assert apis(outcome, QUERY) == [
        [QUERY, [{'active': True, 'currentWindow': True}]]], outcome
    assert answers(outcome)[-1]['tabId'] == 12, outcome
    assert apis(outcome, DETACH) == [detach_call(12)], outcome


def test_a_stop_with_no_active_tab_is_refused(tmp):
    outcome = run_capture([{'command': cmd('net-capture-stop')}],
                          activeTabs=[])
    assert errors(outcome) == ['No active tab'], outcome
    assert apis(outcome, DETACH) == [], outcome


def test_a_stop_reports_a_tab_lookup_that_failed(tmp):
    outcome = run_capture([{'command': cmd('net-capture-stop')}],
                          chromeReject={'tabs.query': 'no window for query'})
    assert errors(outcome) == ['no window for query'], outcome
    assert answers(outcome) == [None], outcome
    assert apis(outcome, DETACH) == [], outcome


def test_a_stop_without_bodies_sends_no_body_request(tmp):
    # The trailing read is the witness: it asks for the same entry's body
    # and the request appears, so the list above counts a working counter.
    outcome = run_capture([
        start(5), request(5, 'r1', 'https://a.example.com/a', 'HEAD',
                          {'X-Probe': 'r1'}),
        finished(5, 'r1', encodedDataLength=10), start(6),
        request(6, 'r2', 'https://b.example.com/b', 'PUT', {'X-Probe': 'r2'}),
        finished(6, 'r2'), stop(5), read(6, bodies=True),
    ], bodies={'r2': {'body': 'served'}})
    assert sent_bodies(outcome) == ['r2'], outcome
    assert 'body' not in entries(outcome, 2)[0], outcome
    assert entries(outcome, -1)[0]['body'] == 'served', outcome


def test_a_stop_with_bodies_fetches_only_finished_entries(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'PATCH',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://a.example.com/two', 'OPTIONS',
                {'X-Probe': 'r2'}),
        request(5, 'r3', 'https://a.example.com/three', 'DELETE',
                {'X-Probe': 'r3'}),
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
        'method': 'OPTIONS', 'headers': {'X-Probe': 'r2'},
        'postData': None, 'type': '',
        'frameId': '', 'ts': FROZEN, 'initiator': '',
    }, outcome


def test_a_stop_does_not_refetch_a_body_an_entry_already_carries(tmp):
    # The read fetched it; the stop that follows adds no request.
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'TRACE',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://a.example.com/two', 'GET',
                {'X-Probe': 'r2'}),
        finished(5, 'r1'), read(bodies=True), stop(bodies=True),
    ], bodies={'r1': {'body': 'first'}, 'r2': {'body': 'unused'}})
    assert sent_bodies(outcome) == ['r1'], outcome
    assert entries(outcome)[0]['body'] == 'first', outcome


def test_a_stop_records_the_base64_flag_it_was_given(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'POST',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://a.example.com/two', 'HEAD',
                {'X-Probe': 'r2'}),
        finished(5, 'r1'), finished(5, 'r2'),
        stop(bodies=True),
    ], bodies={'r1': {'body': 'first', 'base64Encoded': True},
               'r2': {'body': 'second'}})
    flags = {entry['requestId']: entry['bodyBase64']
             for entry in entries(outcome)}
    assert flags == {'r1': True, 'r2': False}, outcome


def test_a_body_the_worker_could_not_fetch_leaves_the_entry_without_one(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'PUT',
                         {'X-Probe': 'r1'}),
        finished(5, 'r1'), stop(bodies=True),
    ], bodies={'r1': {'throw': 'No resource with given identifier'}})
    assert sent_bodies(outcome) == ['r1'], outcome
    assert 'body' not in entries(outcome)[0], outcome
    assert answers(outcome)[-1]['count'] == 1, outcome


def test_a_stop_reports_the_buffered_count_and_clears_the_capture(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'PATCH',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://a.example.com/two', 'OPTIONS',
                {'X-Probe': 'r2'}), stop(),
    ])
    assert answers(outcome)[-1]['count'] == 2, outcome
    assert outcome['state']['captures'] == [], outcome


def test_a_stop_returns_the_buffered_entries_and_frees_the_tab(tmp):
    # The stop returns the buffered entries in order and frees the tab. The
    # returned array's identity is NOT pinned: the answer crosses a JSON
    # boundary, so no assertion here can reach the worker's own array.
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'DELETE',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://a.example.com/two', 'TRACE',
                {'X-Probe': 'r2'}), stop(),
        start(), request(5, 'r3', 'https://a.example.com/three', 'GET',
                         {'X-Probe': 'r3'}), read(),
    ])
    assert ids(outcome, 1) == ['r1', 'r2'], outcome
    assert answers(outcome)[1]['count'] == 2, outcome
    assert ids(outcome) == ['r3'], outcome


def test_a_stop_detaches_only_the_attachment_it_owns(tmp):
    # Tab 5's attachment is a kept session's, tab 6's this capture's; the
    # one detach, for the second, is the oracle for the first's absence.
    outcome = run_capture([
        keep(5), start(5), start(6),
        request(5, 'r1', 'https://a.example.com/one', 'POST',
                {'X-Probe': 'r1'}),
        request(6, 'r2', 'https://a.example.com/two', 'HEAD',
                {'X-Probe': 'r2'}),
        stop(5), stop(6),
    ])
    assert apis(outcome, DETACH) == [detach_call(6)], outcome
    assert outcome['state']['cdpSessions'] == ['5'], outcome


# ─── handleNetCaptureGet ───
def test_a_read_without_a_capture_reports_not_capturing(tmp):
    outcome = run_capture([read()])
    assert errors(outcome) == ['Not capturing on this tab'], outcome


def test_a_read_without_a_tab_id_resolves_the_active_tab(tmp):
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
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'PUT',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://b.example.com/two', 'PATCH',
                {'X-Probe': 'r2'}), read(),
    ])
    assert answers(outcome)[-1]['count'] == 2, outcome


def test_a_read_filter_matches_the_url_case_insensitively(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/Alpha', 'OPTIONS',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://b.example.com/beta', 'DELETE',
                {'X-Probe': 'r2'}), read(filter='alpha'),
    ])
    # The FILTERED length, not the buffer's: a filter-blind read says 2.
    assert answers(outcome)[-1]['count'] == 1, outcome
    assert ids(outcome) == ['r1'], outcome


def test_a_read_filter_matches_a_type_the_url_does_not_carry(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'TRACE',
                         {'X-Probe': 'r1'}, type='Stylesh'),
        request(5, 'r2', 'https://a.example.com/two', 'GET',
                {'X-Probe': 'r2'}), read(filter='stylesh'),
    ])
    assert answers(outcome)[-1]['count'] == 1, outcome
    assert ids(outcome) == ['r1'], outcome


def test_a_read_filter_excludes_every_entry_that_does_not_match(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'POST',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://a.example.com/two', 'HEAD',
                {'X-Probe': 'r2'}), read(filter='zzz'),
    ])
    assert answers(outcome)[-1] == {
        'tabId': 5, 'count': 0, 'requests': []}, outcome


def test_a_read_with_no_active_tab_is_refused(tmp):
    outcome = run_capture([{'command': cmd('net-capture-get')}], activeTabs=[])
    assert errors(outcome) == ['No active tab'], outcome


def test_a_read_with_an_invalid_filter_posts_the_error(tmp):
    outcome = run_capture([start(), read(filter='([')])
    assert answers(outcome) == [{'capturing': True, 'tabId': 5}, None], outcome
    assert errors(outcome) == [None, 'Invalid regular expression: '
                               '/([/i: Unterminated character class'], outcome


def test_a_read_with_bodies_fetches_only_finished_bodiless_entries(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'PUT',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://a.example.com/two', 'PATCH',
                {'X-Probe': 'r2'}),
        finished(5, 'r1'), read(bodies=True), read(bodies=True),
    ], bodies={'r1': {'body': 'first'}})
    # The first read fetched it; the second found the body already there.
    assert sent_bodies(outcome) == ['r1'], outcome
    assert entries(outcome)[0]['body'] == 'first', outcome
    assert 'body' not in entries(outcome)[1], outcome


def test_a_read_with_bodies_fetches_only_the_filtered_entries(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'OPTIONS',
                         {'X-Probe': 'r1'}),
        request(5, 'r2', 'https://b.example.com/two', 'DELETE',
                {'X-Probe': 'r2'}),
        finished(5, 'r1'), finished(5, 'r2'),
        read(filter='one', bodies=True),
    ], bodies={'r1': {'body': 'kept'}, 'r2': {'body': 'skipped'}},
        frozenNow=FROZEN)
    assert sent_bodies(outcome) == ['r1'], outcome
    assert entries(outcome) == [
        {'requestId': 'r1', 'url': 'https://a.example.com/one',
         'method': 'OPTIONS', 'headers': {'X-Probe': 'r1'},
         'postData': None, 'type': '',
         'frameId': '', 'ts': FROZEN, 'initiator': '', 'done': True,
         'encodedLength': 0, 'body': 'kept', 'bodyBase64': False}], outcome


def test_a_read_records_the_base64_flag_it_was_given(tmp):
    outcome = run_capture([
        start(), request(5, 'r1', 'https://a.example.com/one', 'TRACE',
                         {'X-Probe': 'r1'}),
        finished(5, 'r1'), read(bodies=True),
    ], bodies={'r1': {'body': 'first', 'base64Encoded': True}})
    assert entries(outcome)[0]['bodyBase64'] is True, outcome


# ─── module-level wiring ───
def test_the_debugger_event_target_holds_exactly_one_listener(tmp):
    # Every event test above reaches the handler only through this array, so
    # the count is what makes those dispatches unambiguous; which listener
    # it is, they demonstrate by the entries landing in the buffer.
    outcome = run_capture([], probes=[
        'chrome.debugger.onEvent.listeners.length'])
    assert [probe.get('value') for probe in outcome['probes']] == [1], outcome


def test_a_tab_close_releases_both_maps_for_that_tab(tmp):
    outcome = run_capture([
        keep(5), start(6), request(6, 'r1', 'https://a.example.com/one', 'GET',
                                   {'X-Probe': 'r1'}),
        {'tabRemoved': 5}, {'tabRemoved': 6},
    ])
    assert outcome['state'] == {'captures': [], 'cdpSessions': []}, outcome
    assert apis(outcome, DETACH) == [
        detach_call(5), detach_call(6)], outcome


def test_a_tab_close_with_neither_map_holding_it_does_not_detach(tmp):
    # Tab 6 holds neither map, so nothing detaches. The live oracle is
    # test_a_tab_close_releases_both_maps_for_that_tab, where it does.
    outcome = run_capture([
        start(5), request(5, 'r1', 'https://a.example.com/one', 'POST',
                          {'X-Probe': 'r1'}),
        {'tabRemoved': 6},
    ])
    assert apis(outcome, DETACH) == [], outcome
    assert outcome['state']['captures'] == [
        {'tabId': '5', 'maxRequests': 1000, 'requestIds': ['r1']}], outcome


def test_a_detach_clears_both_maps_for_its_own_tab(tmp):
    outcome = run_capture([
        keep(5), keep(6), start(5), {'debuggerDetached': {'tabId': 5}},
    ])
    assert outcome['state'] == {'captures': [], 'cdpSessions': ['6']}, outcome


def test_a_detach_that_names_no_tab_changes_nothing(tmp):
    # Three ways the source can fail to name a tab: a null one reaches the
    # `source &&` conjunct, the other two the `tabId != null` half. Chrome
    # never delivers a null source, so that one pins the guard only.
    outcome = run_capture([
        keep(5), start(5), start(6),
        {'debuggerDetached': None},
        {'debuggerDetached': {}},
        {'debuggerDetached': {'tabId': None}},
    ])
    assert outcome['state'] == {
        'captures': [
            {'tabId': '5', 'maxRequests': 1000, 'requestIds': []},
            {'tabId': '6', 'maxRequests': 1000, 'requestIds': []}],
        'cdpSessions': ['5']}, outcome


def test_every_answer_this_module_posts_to_the_extension_channel(tmp):
    # The channel an answer is filed under is a contract of every post the
    # module makes. Each comment names the sites its run is the route to.
    no_active = run_capture(
        [{'command': cmd('net-capture')},
         {'command': cmd('net-capture-stop')},
         {'command': cmd('net-capture-get')}], activeTabs=[])
    # :73, :121, :160
    assert channels(no_active) == ['extension'] * 3, no_active
    healthy = run_capture([
        start(5), start(5), start(6, maxRequests=0), stop(9), read(9)])
    # :110, :83, :112, :127, :166
    assert channels(healthy) == ['extension'] * 5, healthy
    attach_failed = run_capture([start(5)], chromeReject={
        'debugger.attach': 'Another debugger is already attached'})
    assert channels(attach_failed) == ['extension'], attach_failed  # :107
    lookup_failed = run_capture(
        [{'command': cmd('net-capture-stop')},
         {'command': cmd('net-capture-get')}],
        chromeReject={'tabs.query': 'no window for query'})
    # :151, :189
    assert channels(lookup_failed) == ['extension'] * 2, lookup_failed
    answered = run_capture([
        start(5), request(5, 'r1', 'https://a.example.com/one', 'HEAD',
                          {'X-Probe': 'r1'}), stop(5),
        start(5), read(5)])
    # :149, :187
    assert channels(answered) == ['extension'] * 4, answered
    assert worlds(answered) == ['extension'] * 4, answered


def test_stop_and_read_both_coerce_a_string_tab_id(tmp):
    # A tab key is a string either way, so only the answer's tabId can.
    outcome = run_capture([
        start(5), request(5, 'r1', 'https://a.example.com/one', 'PUT',
                          {'X-Probe': 'r1'}),
        {'command': cmd('net-capture-stop', tabId='5')},
        start(5), request(5, 'r2', 'https://a.example.com/two', 'PATCH',
                          {'X-Probe': 'r2'}),
        {'command': cmd('net-capture-get', tabId='5')},
    ])
    assert answers(outcome)[1]['tabId'] == 5, outcome
    assert answers(outcome)[1]['count'] == 1, outcome
    assert answers(outcome)[3]['tabId'] == 5, outcome
    assert answers(outcome)[3]['count'] == 1, outcome


def test_a_stop_swallows_a_detach_that_failed(tmp):
    # The detach is on record, so the counter is live.
    outcome = run_capture([
        start(5), request(5, 'r1', 'https://a.example.com/one', 'OPTIONS',
                          {'X-Probe': 'r1'}), stop(5),
    ], chromeReject={'debugger.detach': 'target closed'})
    assert apis(outcome, DETACH) == [detach_call(5)], outcome
    assert answers(outcome)[-1]['count'] == 1, outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='netcapture_')


if __name__ == '__main__':
    raise SystemExit(main())
