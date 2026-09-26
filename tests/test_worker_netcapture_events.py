#!/usr/bin/env python3
"""extension/worker/netcapture.js — the limit reader and the CDP event handler.

These two run where no command reaches: the handler only when a capture is
already buffering, and the limit reader only through the command that calls
it. Both are driven here; the handlers and the module registrations are in
test_worker_netcapture.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _netcapture_harness import (  # noqa: E402
    BOUND, FROZEN, answers, buffered, event, finished, read, request,
    response, run_capture, start)


# ─── _netCaptureLimit ───
def test_the_limit_defaults_to_one_thousand_for_three_absent_spellings(tmp):
    # A dropped key, an explicit null and an empty string are three.
    outcome = run_capture([], probes=['_netCaptureLimit(undefined)',
                                      '_netCaptureLimit(null)',
                                      "_netCaptureLimit('')"])
    assert [probe.get('value') for probe in outcome['probes']] == [
        1000, 1000, 1000], outcome


def test_the_limit_returns_a_value_inside_its_bound_unchanged(tmp):
    outcome = run_capture([], probes=['_netCaptureLimit(1)',
                                      '_netCaptureLimit(19999)',
                                      "_netCaptureLimit('250')"])
    # The string probe is what pins `Number(value)`: a bare pass-through
    # leaves '250' a string, which is not an integer and throws instead.
    assert [probe.get('value') for probe in outcome['probes']] == [
        1, 19999, 250], outcome


def test_the_limit_clamps_the_maximum_from_both_sides(tmp):
    # 20000 is inside the bound, 20001 is not; one alone cannot.
    outcome = run_capture([], probes=['_netCaptureLimit(20000)',
                                      '_netCaptureLimit(20001)'])
    assert [probe.get('value') for probe in outcome['probes']] == [
        20000, 20000], outcome


def test_the_limit_refuses_a_non_integer_with_its_message(tmp):
    outcome = run_capture([], probes=["_netCaptureLimit('many')",
                                      '_netCaptureLimit(2.5)'])
    assert [probe.get('error') for probe in outcome['probes']] == [
        BOUND, BOUND], outcome


def test_the_limit_refuses_every_value_below_one(tmp):
    # -1 once evicted the only event on arrival, leaving an empty capture.
    outcome = run_capture([], probes=['_netCaptureLimit(0)',
                                      '_netCaptureLimit(-1)',
                                      '_netCaptureLimit(-1000000)'])
    assert [probe.get('error') for probe in outcome['probes']] == [
        BOUND] * 3, outcome


# ─── _netEventHandler ───
def test_an_event_for_a_tab_with_no_capture_is_ignored(tmp):
    # The silent path returns before it reads params at all.
    outcome = run_capture([
        request(9, 'r1', 'https://a.example.com/one'),
        start(9), read(9),
    ])
    assert answers(outcome)[-1] == {
        'tabId': 9, 'count': 0, 'requests': []}, outcome


def test_a_request_event_builds_the_entry_from_the_event(tmp):
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
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                    )[0]['postData'] is None


def test_a_request_carries_a_post_data_it_was_given(tmp):
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a',
                                      post='q=1')])[0]['postData'] == 'q=1'


def test_a_request_defaults_an_absent_type_to_the_empty_string(tmp):
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                    )[0]['type'] == ''


def test_a_request_defaults_an_absent_frame_id_to_the_empty_string(tmp):
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                    )[0]['frameId'] == ''


def test_a_request_defaults_an_absent_initiator_to_the_empty_string(tmp):
    assert buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                    )[0]['initiator'] == ''


def test_a_request_takes_the_initiator_url_when_it_carries_one(tmp):
    entry = buffered([start(), request(
        5, 'r1', 'https://a.example.com/a',
        initiator={'url': 'https://a.example.com/seed', 'type': 'parser'})
    ])[0]
    assert entry['initiator'] == 'https://a.example.com/seed', entry


def test_a_request_falls_back_to_the_initiator_type_without_a_url(tmp):
    entry = buffered([start(), request(
        5, 'r1', 'https://a.example.com/a',
        initiator={'type': 'parser'})])[0]
    assert entry['initiator'] == 'parser', entry


def test_a_request_prefers_the_wall_time_over_the_protocol_timestamp(tmp):
    # Both supplied: the timestamp arm would answer 1700000000500.
    entry = buffered([start(), request(
        5, 'r1', 'https://a.example.com/a',
        wallTime=1700000000.5, timestamp=1700000000.5)])[0]
    assert entry['ts'] == 1700000000.5, entry


def test_a_request_multiplies_the_protocol_timestamp_by_one_thousand(tmp):
    entry = buffered([start(), request(5, 'r1', 'https://a.example.com/a',
                                       timestamp=1700000000.5)])[0]
    assert entry['ts'] == 1700000000500, entry


def test_a_request_without_either_timestamp_reads_the_clock(tmp):
    # Frozen clock: this pins what the code computed, not elapsed time.
    entry = buffered([start(), request(5, 'r1', 'https://a.example.com/a')]
                     )[0]
    assert entry['ts'] == FROZEN, entry


def test_a_response_event_attaches_the_response_fields(tmp):
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
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        response(5, 'r1', status=200),
    ])[0]
    assert entry['statusText'] == '' and entry['mimeType'] == '', entry
    assert entry['responseHeaders'] == {} and entry['responseUrl'] == '', entry


def test_a_response_event_for_an_unknown_request_id_is_ignored(tmp):
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        response(5, 'absent', status=500),
    ])[0]
    assert 'status' not in entry and entry['requestId'] == 'r1', entry


def test_loading_finished_marks_the_entry_and_carries_its_length(tmp):
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        finished(5, 'r1', encodedDataLength=4096),
    ])[0]
    assert entry['done'] is True and entry['encodedLength'] == 4096, entry


def test_loading_finished_defaults_a_missing_length_to_zero(tmp):
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        finished(5, 'r1'),
    ])[0]
    assert entry['done'] is True and entry['encodedLength'] == 0, entry


def test_loading_finished_for_an_unknown_request_id_is_ignored(tmp):
    entry = buffered([
        start(), request(5, 'r1', 'https://a.example.com/a'),
        finished(5, 'absent', encodedDataLength=10),
    ])[0]
    assert 'done' not in entry and 'encodedLength' not in entry, entry


def test_an_unrecognised_event_method_is_ignored(tmp):
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
    entries_at_limit = buffered(
        [start(maxRequests=2),
         request(5, 'r1', 'https://a.example.com/one'),
         request(5, 'r2', 'https://a.example.com/two')])
    assert [entry['requestId'] for entry in entries_at_limit] == [
        'r1', 'r2'], entries_at_limit


def test_the_oldest_is_evicted_past_the_limit(tmp):
    over = buffered(
        [start(maxRequests=2),
         request(5, 'r1', 'https://a.example.com/one'),
         request(5, 'r2', 'https://a.example.com/two'),
         request(5, 'r3', 'https://a.example.com/three')])
    # Named, not just counted: a count alone is satisfied by pop().
    assert [entry['requestId'] for entry in over] == ['r2', 'r3'], over
    assert [entry['url'] for entry in over] == [
        'https://a.example.com/two', 'https://a.example.com/three'], over


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='netcaptureev_')


if __name__ == '__main__':
    raise SystemExit(main())
