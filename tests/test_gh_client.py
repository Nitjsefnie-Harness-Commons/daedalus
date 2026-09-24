#!/usr/bin/env python3
"""The shared gh client: one request per query, refusals that pause, cursors.

Every call here is a real `gh` invocation answered by the fake executable
double in `_fake_gh.py`, so what is under test is the request the client
actually makes.
"""
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'
SOURCE = SKILL / 'gh_client.py'

ITEM_QUERY = ('query Watch($after: String) { repository { items('
              'first: 2, after: $after) { pageInfo { hasNextPage '
              'endCursor } nodes { id } } } }')


def _client():
    return _util.load(SOURCE, 'gh_client_contract')


def _windows_text(text):
    """What a Windows text-mode stdout writes: every `\n` translated, so a
    `\r\n` the caller already spelled leaves as `\r\r\n`.
    """
    return text.replace('\n', '\r\n')


def _page(nodes, has_next=False, cursor=None):
    return {'data': {'repository': {'items': {
        'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        'nodes': nodes}}}}


def test_the_request_is_one_no_cache_json_payload_with_headers_included(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': _page([{'id': 1}])})
    with fake.activate():
        data = mod.graphql(ITEM_QUERY, {'after': None})
    assert mod.nodes(data, ('repository', 'items')) == [{'id': 1}]
    calls = fake.calls()
    assert len(calls) == 1
    argv = calls[0]['argv']
    assert 'Cache-Control: no-cache' in argv
    assert '-i' in argv
    assert argv[argv.index('--input') + 1] == '-'
    payload = json.loads(calls[0]['request'])
    assert payload['query'] == ITEM_QUERY
    # A GraphQL null must arrive as null, not as the empty string a
    # `-f field=` would send.
    assert payload['variables']['after'] is None


def test_a_403_with_a_reset_header_is_a_rate_limit_refusal(tmp):
    mod = _client()
    reset = int(time.time()) + 120
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 403, 'headers': {'X-RateLimit-Reset': str(reset)},
        'body': 'API rate limit exceeded for the account.'}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at == reset
        else:
            raise AssertionError('a 403 with a reset header must refuse')


def test_a_403_whose_only_evidence_is_the_body_is_still_a_refusal(tmp):
    """Some refusals carry the rate limit in the body and nowhere else;
    without that clause ci_wait would exit 3 instead of waiting out the
    reset.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 403, 'headers': {},
        'body': 'API rate limit exceeded for the account.'}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited:
            pass
        else:
            raise AssertionError('a body-only rate-limit 403 must refuse')


def test_a_retranslated_header_block_still_yields_its_values(tmp):
    """A re-translated header block still yields its values. Without that,
    the block ends early, the header lines fall into the body, and a
    reported reset arrives as no reset - a 60-second default instead.
    """
    del tmp
    mod = _client()
    answered = _windows_text(
        'HTTP/2.0 403 Forbidden\r\nX-RateLimit-Reset: 42\r\n'
        'Retry-After: 7\r\n\r\n{"data": null}\n')
    status, headers, body = mod._parse(answered)
    assert status == 403
    assert headers.get('x-ratelimit-reset') == '42', headers
    assert headers.get('retry-after') == '7', headers
    assert json.loads(body) == {'data': None}, body
    refused, resume = mod._refused(status, headers, body)
    assert refused and resume is not None, resume


def test_a_header_reset_survives_a_windows_text_stream_end_to_end(tmp):
    """The whole chain over the bytes a Windows stdout really delivers: a
    real `gh` process, the real request, the real parse.
    """
    mod = _client()
    reset = int(time.time()) + 120
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 403, 'headers': {'X-RateLimit-Reset': str(reset)},
        'body': 'API rate limit exceeded for the account.'}})
    with fake.activate():
        os.environ['DAEDALUS_FAKE_GH_CRLF'] = '1'
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at == reset
        else:
            raise AssertionError('a 403 with a reset header must refuse')
        finally:
            os.environ.pop('DAEDALUS_FAKE_GH_CRLF', None)


def test_a_429_prefers_retry_after_over_the_reset_header(tmp):
    mod = _client()
    before = time.time()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 429, 'headers': {
            'Retry-After': '60',
            'X-RateLimit-Reset': str(int(before) + 3600)},
        'body': 'You have exceeded a secondary rate limit.'}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert 60 <= refusal.resume_at - before <= 61, refusal.resume_at
        else:
            raise AssertionError('a 429 with Retry-After must refuse')


def test_a_graphql_rate_limited_error_is_a_refusal_naming_its_reset(tmp):
    mod = _client()
    reset_at = '2030-01-01T00:00:00Z'
    wanted = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200,
        'body': {'data': None, 'errors': [{
            'type': 'RATE_LIMITED',
            'message': 'API rate limit exceeded.',
            'extensions': {'rateLimit': {'resetAt': reset_at,
                                         'retryAfter': 5}}}]}}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at == wanted
        else:
            raise AssertionError('a GraphQL RATE_LIMITED error must refuse')


def test_a_graphql_retry_after_is_honoured_when_no_reset_is_reported(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200,
        'body': {'data': None, 'errors': [{
            'type': 'RATE_LIMITED',
            'extensions': {'retryAfter': 90}}]}}})
    with fake.activate():
        before = time.time()
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert 90 <= refusal.resume_at - before <= 91, refusal.resume_at
        else:
            raise AssertionError('retryAfter must be honoured')


def test_a_slow_install_cannot_move_the_measured_retry_after(tmp):
    """The offset is read at the call, so setup time cannot shift it:
    `before` once sat before the fake's install, a subprocess, and a second
    of that is the whole margin of a one-second window.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200,
        'body': {'data': None, 'errors': [{
            'type': 'RATE_LIMITED',
            'extensions': {'retryAfter': 90}}]}}})
    with fake.activate():
        at_install = time.time()
        before = time.time()
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert 90 <= refusal.resume_at - before <= 91, refusal.resume_at
            # The point the old control measured from, a second and a half
            # of setup earlier, lands outside the window it asserts.
            assert refusal.resume_at - (at_install - 1.5) > 91
        else:
            raise AssertionError('retryAfter must be honoured')


def test_a_403_without_rate_limit_evidence_is_an_ordinary_failure(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 403, 'body': 'Resource not accessible by integration.'}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('a plain 403 must fail the query')


def test_an_unparseable_body_is_an_ordinary_failure(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200, 'body': 'not json at all'}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('an unparseable body must fail the query')


def test_pagination_is_one_call_per_page_and_every_cursor_is_followed(tmp):
    mod = _client()
    connection = (('repository', 'items'), 'after')
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': [
        _page([{'id': 1}], has_next=True, cursor='CURSOR-1'),
        _page([{'id': 2}])]})
    with fake.activate():
        pages = mod.paginate(ITEM_QUERY, {'after': None}, [connection])
    calls = fake.calls()
    assert len(calls) == 2, calls
    assert len(pages) == 2
    second = json.loads(calls[1]['request'])
    assert second['variables']['after'] == 'CURSOR-1'
    seen = [node for page in pages
            for node in mod.nodes(page, ('repository', 'items'))]
    assert seen == [{'id': 1}, {'id': 2}]


class _Clock:
    """A call that records each attempt's time and may refuse."""

    def __init__(self, refusals):
        self.refusals = refusals
        self.at = []

    def __call__(self):
        self.at.append(time.time())
        if self.refusals:
            raise self.refusals.pop(0)
        return 'answered'


def _suite_page(suites, has_next=False, cursor=None):
    return {'data': {'repository': {'object': {'checkSuites': {
        'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        'nodes': list(suites)}}}}}


def _suite(rid, conclusion: str | None = 'SUCCESS',
           status='COMPLETED', workflow=11, name=None,
           started='2026-09-20T10:00:00Z'):
    """One check suite, as the live schema reports a workflow's jobs."""
    return {'status': status, 'conclusion': conclusion,
            'createdAt': started,
            'workflowRun': {
                'databaseId': rid, 'createdAt': started,
                'url': f'https://github.com/o/r/actions/runs/{rid}',
                'file': {'path': '.github/workflows/ci.yml'},
                'workflow': {'databaseId': workflow,
                             'name': name or f'workflow {workflow}'}}}


def test_a_run_past_the_first_page_is_still_read(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': [
        _suite_page([_suite(1)], has_next=True, cursor='CURSOR-1'),
        _suite_page([_suite(2)])]})
    with fake.activate():
        runs = mod.workflow_runs('o', 'r', 'a' * 40)
    assert len(fake.calls()) == 2
    second = json.loads(fake.calls()[1]['request'])
    assert second['variables']['after'] == 'CURSOR-1'
    assert second['variables']['sha'] == 'a' * 40
    assert [run['id'] for run in runs] == [1, 2]
    assert runs[0]['status'] == 'completed'
    assert runs[0]['conclusion'] == 'success'
    assert runs[0]['name'] == 'workflow 11'
    assert runs[0]['workflow_id'] == 11


def test_the_jobs_of_one_run_collapse_to_the_run(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': _suite_page([
        _suite(1, 'SUCCESS', 'COMPLETED', started='2026-09-20T10:00:00Z'),
        _suite(1, 'FAILURE', 'COMPLETED', started='2026-09-20T10:01:00Z')])})
    with fake.activate():
        runs = mod.workflow_runs('o', 'r', 'a' * 40)
    assert len(runs) == 1
    assert runs[0]['conclusion'] == 'failure'
    assert runs[0]['status'] == 'completed'
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': _suite_page([
        _suite(1, 'SUCCESS', 'COMPLETED', started='2026-09-20T10:00:00Z'),
        _suite(1, None, 'IN_PROGRESS',
               started='2026-09-20T10:01:00Z')])})
    with fake.activate():
        runs = mod.workflow_runs('o', 'r', 'a' * 40)
    assert runs[0]['status'] == 'in_progress'


def test_the_same_run_twice_in_one_page_is_one_run(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': _suite_page(
        [_suite(1), _suite(1), _suite(2)])})
    with fake.activate():
        runs = mod.workflow_runs('o', 'r', 'a' * 40)
    assert [run['id'] for run in runs] == [1, 2]


def test_a_sha_with_no_run_yet_reads_as_no_runs(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': {
        'data': {'repository': {'commit': None}}}})
    with fake.activate():
        assert mod.workflow_runs('o', 'r', 'a' * 40) == []


def test_a_refusal_pauses_once_naming_the_reset_and_then_resumes(tmp):
    del tmp
    mod = _client()
    out = io.StringIO()
    reset = int(time.time()) + 5
    clock = _Clock([mod.RateLimited('rate limited', reset)])
    watcher = mod.Watcher('PR 1 watcher', out=out)
    assert watcher.poll(clock) == 'answered'
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, lines
    assert 'rate limit' in lines[0]
    stamp = lines[0].rsplit(' ', 1)[-1]
    wanted = datetime.fromtimestamp(reset, timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')
    assert stamp == wanted, (stamp, wanted)
    assert len(clock.at) == 2
    # The second call waits for the reset the line named; it does not retry
    # on the poll interval.
    assert clock.at[1] >= clock.at[0] + 3, clock.at


def test_a_child_exits_when_the_pipe_this_process_holds_closes(tmp):
    del tmp
    mod = _client()
    child, pipe_end = mod.spawn_watched(
        [sys.executable, '-c',
         'import sys, time; sys.path.insert(0, sys.argv[1]);'
         ' import gh_client; gh_client.watch_parent();'
         ' time.sleep(5); print("slept")',
         str(SKILL)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    os.close(pipe_end)
    out, err = child.communicate(timeout=60)
    assert child.returncode == 0, (child.returncode, err)
    assert 'slept' not in out, out


def test_a_poll_strictly_inside_the_window_still_answers(tmp):
    """The positive half near the bound: inside the window, it answers.

    A bound that fires early - seconds early, or a stale clock, or `>` for
    `>=` - must be caught by a poll that completes immediately. The window
    is two seconds: long enough that an instant poll is nowhere near it,
    short enough that a bound firing five seconds early is caught.
    """
    del tmp
    mod = _client()
    watcher = mod.Watcher('w', out=io.StringIO(),
                          deadline=time.monotonic() + 2)
    assert watcher.poll(lambda: 'answered') == 'answered'
    try:
        watcher.poll(lambda: (_ for _ in ()).throw(mod.QueryError('no')))
    except mod.QueryError:
        pass
    else:
        raise AssertionError('a failed query inside the window is a failure')


def test_a_passed_bound_ends_the_wait_without_another_request(tmp):
    del tmp
    mod = _client()
    clock = _Clock([mod.RateLimited('rate limited', None)])
    watcher = mod.Watcher('w', out=io.StringIO(),
                          deadline=time.monotonic() - 1)
    try:
        watcher.poll(clock)
    except mod.WaitExpired:
        pass
    else:
        raise AssertionError('a bound that has passed must end the wait')
    assert clock.at == [], clock.at


def test_an_absent_or_nonsense_reset_clamps_into_a_bounded_wait(tmp):
    del tmp
    mod = _client()
    watcher = mod.Watcher('w', out=io.StringIO())
    now = time.time()
    assert 0 < watcher._wait_seconds(mod.RateLimited('x', None)) <= 3600
    assert watcher._wait_seconds(mod.RateLimited('x', now - 5000)) >= 2
    assert watcher._wait_seconds(
        mod.RateLimited('x', now + 10 ** 9)) == mod.MAX_BACKOFF
    assert 0 < watcher._wait_seconds(
        mod.RateLimited('x', now + 30)) <= 31


def test_a_reset_beyond_the_hour_is_slept_to_and_not_clamped(tmp):
    """The pause is slept to the reported reset; the bound is far past one.

    A reset an hour and a half out woke at the old one-hour clamp, was
    refused again, and printed a second line for one wait. The bound stays
    - a hostile header must not wedge a watcher - but it bounds an absurd
    header, not the resets GitHub actually reports.
    """
    del tmp
    mod = _client()
    watcher = mod.Watcher('w', out=io.StringIO())
    now = time.time()
    ninety = watcher._wait_seconds(
        mod.RateLimited('x', now + 90 * 60), now)
    assert 89 * 60 <= ninety <= 90 * 60, ninety
    assert mod.MAX_BACKOFF > 2 * 3600, mod.MAX_BACKOFF


def test_a_query_failure_is_not_retried_behind_the_pause(tmp):
    mod = _client()
    out = io.StringIO()
    clock = _Clock([mod.QueryError('gh exploded')])
    watcher = mod.Watcher('w', out=out)
    try:
        watcher.poll(clock)
    except mod.QueryError:
        pass
    else:
        raise AssertionError('a failed query must reach the caller')
    assert len(clock.at) == 1
    assert not out.getvalue().strip(), out.getvalue()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ghclient_')


if __name__ == '__main__':
    raise SystemExit(main())
