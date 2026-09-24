#!/usr/bin/env python3
"""The shared gh client: one request per query, refusals that pause, cursors.

Every call here goes through a real `gh` invocation answered by the fake
executable double in `_fake_gh.py`, so what is under test is the request the
client actually makes, not a stub of the function that would make it.
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
    before = time.time()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200,
        'body': {'data': None, 'errors': [{
            'type': 'RATE_LIMITED',
            'extensions': {'retryAfter': 90}}]}}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert 90 <= refusal.resume_at - before <= 91, refusal.resume_at
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
    """A refused call that records when each attempt was made."""

    def __init__(self, refusals):
        self.refusals = refusals
        self.at = []

    def __call__(self):
        self.at.append(time.time())
        if self.refusals:
            raise self.refusals.pop(0)
        return 'answered'


def _suite_page(runs, has_next=False, cursor=None):
    return {'data': {'repository': {'commit': {'checkSuites': {
        'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        'nodes': [{'workflowRun': run} for run in runs]}}}}}


def _run(rid, conclusion='success', started='2026-09-20T10:00:00Z'):
    return {'databaseId': rid, 'name': f'run {rid}', 'status': 'COMPLETED',
            'conclusion': conclusion.upper(), 'createdAt': started,
            'url': f'https://github.com/o/r/runs/{rid}',
            'workflow': {'databaseId': 11}}


def test_a_run_past_the_first_page_is_still_read(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': [
        _suite_page([_run(1)], has_next=True, cursor='CURSOR-1'),
        _suite_page([_run(2)])]})
    with fake.activate():
        runs = mod.workflow_runs('o', 'r', 'a' * 40)
    assert len(fake.calls()) == 2
    second = json.loads(fake.calls()[1]['request'])
    assert second['variables']['after'] == 'CURSOR-1'
    assert second['variables']['sha'] == 'a' * 40
    assert [run['id'] for run in runs] == [1, 2]
    assert runs[0]['status'] == 'completed'
    assert runs[0]['conclusion'] == 'success'
    assert runs[0]['workflow_id'] == 11


def test_the_suites_of_one_run_collapse_to_that_run(tmp):
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'checkSuites': _suite_page(
        [_run(1), _run(1), _run(2)])})
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


def test_an_absent_or_nonsense_reset_clamps_into_a_bounded_wait(tmp):
    del tmp
    mod = _client()
    watcher = mod.Watcher('w', out=io.StringIO())
    now = time.time()
    assert 0 < watcher._wait_seconds(mod.RateLimited('x', None)) <= 3600
    assert watcher._wait_seconds(mod.RateLimited('x', now - 5000)) >= 2
    assert watcher._wait_seconds(
        mod.RateLimited('x', now + 10 ** 9)) == 3600
    assert 0 < watcher._wait_seconds(
        mod.RateLimited('x', now + 30)) <= 31


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


def test_the_parent_check_passes_while_the_parent_is_there(tmp):
    del tmp
    mod = _client()
    assert mod.check_parent(os.getppid()) is None
    mod.check_parent(None)
    child = subprocess.run(
        [sys.executable, '-c',
         'import sys; sys.path.insert(0, sys.argv[1]);'
         ' import gh_client; gh_client.check_parent(1)',
         str(SKILL)], capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, (child.returncode, child.stderr)
    # The sleep it waits on is a parent check in a loop, so a gone parent is
    # noticed while the watcher is waiting rather than at the next poll.
    sleeper = subprocess.run(
        [sys.executable, '-c',
         'import sys; sys.path.insert(0, sys.argv[1]);'
         ' import gh_client; gh_client.Watcher("w", parent_pid=1).sleep(5);'
         ' print("slept")',
         str(SKILL)], capture_output=True, text=True, timeout=60)
    assert sleeper.returncode == 0, (sleeper.returncode, sleeper.stderr)
    assert 'slept' not in sleeper.stdout, sleeper.stdout


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ghclient_')


if __name__ == '__main__':
    raise SystemExit(main())
