#!/usr/bin/env python3
"""The shared gh client: one request per query, refusals that pause, cursors.

Every call here is a real `gh` invocation answered by the fake executable
double in `_fake_gh.py`, so what is under test is the request the client
actually makes.
"""
import contextlib
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


@contextlib.contextmanager
def _frozen_client_clock(mod, now):
    """The clock the client reads, pinned to the fixture's own instant.

    Everything but `time()` is the real module's, so a client that reads
    the monotonic clock or sleeps while it answers still can. What the
    refusal then carries is derived from the fixture and from this instant,
    which is the whole point: no interval the runner spends decides it.
    """

    class _Frozen:
        @staticmethod
        def time():
            return now

        def __getattr__(self, name):
            return getattr(time, name)

    real = getattr(mod, 'time')
    setattr(mod, 'time', _Frozen())
    try:
        yield
    finally:
        setattr(mod, 'time', real)


def test_a_429_prefers_retry_after_over_the_reset_header(tmp):
    """The refusal carries the instant `Retry-After` asked for, counted
    from the clock the client itself read.

    The two headers name instants an hour apart, so the value the refusal
    carries says which one was read; a window around the runner's own clock
    would say only how fast this machine got here, which is a claim a
    loaded windows leg decides. The delay is 37 because that is not a value
    any default in this client carries: 60 is the absent-reset backoff and 90
    is the other control's fixture, so a client that read the header's
    presence and answered with either of them would be told apart from one
    that read its value.
    """
    mod = _client()
    now = 1790266796.5
    retry_after = 37
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 429, 'headers': {
            'Retry-After': str(retry_after),
            'X-RateLimit-Reset': str(int(now) + 3600)},
        'body': 'You have exceeded a secondary rate limit.'}})
    with fake.activate(), _frozen_client_clock(mod, now):
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at == now + retry_after, refusal.resume_at
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
    """No reset reported, so the refusal's instant is the `retryAfter`
    counted from the clock the client read - the value, not a window.
    """
    mod = _client()
    now = 1790266796.5
    retry_after = 90
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200,
        'body': {'data': None, 'errors': [{
            'type': 'RATE_LIMITED',
            'extensions': {'retryAfter': retry_after}}]}}})
    with fake.activate(), _frozen_client_clock(mod, now):
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at == now + retry_after, refusal.resume_at
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


def _paused(mod, instant, resume_at, deadline=None, monotonic=None):
    """What one real `_pause` claimed and slept, at a pinned instant.

    A bound is measured against `time.monotonic`, which the frozen clock
    delegates to the real module - so an unpinned monotonic leaves the
    remainder this reports decided by how fast the runner is, and the
    assertion a margin rather than a value. `monotonic` pins it on the
    same frozen object: an instance attribute shadows `__getattr__`,
    which only fires for names the instance does not carry.
    """
    out = io.StringIO()
    slept = []
    watcher = mod.Watcher('w', out=out, deadline=deadline)
    watcher.sleep = slept.append
    with _frozen_client_clock(mod, instant):
        if monotonic is not None:
            mod.time.monotonic = lambda: monotonic
        watcher._pause(mod.RateLimited('rate limited', resume_at))
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, lines
    _, _, rest = lines[0].partition('waiting ')
    duration, _, stamp = rest.partition('s until ')
    return duration, stamp, slept


def _stamp(instant):
    return datetime.fromtimestamp(instant, timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


# Across the clamps below, a literal is pinned where the number itself is
# the point, and the constant where it is not.
def test_a_reset_beyond_the_floor_is_slept_to_and_named_exactly(tmp):
    """Past the floor nothing is clamped, so the line, the sleep and the
    reported reset are one instant - the property the near-floor case
    cannot have, and the one it is measured against.
    """
    del tmp
    mod = _client()
    now = 1789012345.0
    reset = now + 5.0
    assert mod.MIN_BACKOFF <= reset - now, (mod.MIN_BACKOFF, reset - now)
    duration, stamp, slept = _paused(mod, now, reset)
    assert slept == [5.0], slept
    assert (duration, stamp) == ('5', _stamp(reset)), (duration, stamp)


def test_a_reset_nearer_than_the_floor_is_floored_and_names_the_floor(tmp):
    """Overshooting by the floor's worth is the price of not spinning.

    A wait sized to a millisecond out spends the poll interval as one
    request per millisecond, and the class promises neither a hostile
    header nor an absurd reset can hot-loop the watcher. So the floor
    holds, and the line names the moment the watcher really resumes -
    after the reset it will have passed.
    """
    del tmp
    mod = _client()
    now = 1789012345.0
    reset = now + 0.001
    assert reset - now < mod.MIN_BACKOFF, (mod.MIN_BACKOFF, reset - now)
    duration, stamp, slept = _paused(mod, now, reset)
    assert mod.MIN_BACKOFF == 2, mod.MIN_BACKOFF
    assert slept == [2.0], slept
    wanted = _stamp(now + mod.MIN_BACKOFF)
    assert (duration, stamp) == ('2', wanted), (duration, stamp)


def test_a_fractional_retry_after_becomes_a_near_reset(tmp):
    """The exposure the floor above defends against is reachable today.

    `_graphql_refusal` takes any `retryAfter` that is a number, and a
    GraphQL body is JSON, so a fractional one arrives - a reset a
    thousandth of a second out. Without this, tightening that
    validation would leave every other control green while the one
    above justified itself falsely.

    The fixture omits `resetAt` because the refusal reads it first and
    would never reach `retryAfter`; add one and this passes for the
    wrong reason.
    """
    del tmp
    mod = _client()
    now = 1789012345.0
    payload = {'errors': [{'type': 'RATE_LIMITED', 'extensions': {
        'rateLimit': {'retryAfter': 0.001}}}]}
    with _frozen_client_clock(mod, now):
        refused, resume = mod._graphql_refusal(payload)
    assert refused is True, refused
    assert resume == now + 0.001, resume
    assert resume - now < mod.MIN_BACKOFF, (mod.MIN_BACKOFF, resume - now)


def test_a_reset_already_past_is_floored_and_names_the_floor(tmp):
    """The case the floor was written for: a stale header names an
    instant already gone, and waiting exactly to it is no wait at all.
    """
    del tmp
    mod = _client()
    now = 1789012345.0
    duration, stamp, slept = _paused(mod, now, now - 5000)
    assert slept == [float(mod.MIN_BACKOFF)], slept
    wanted = _stamp(now + mod.MIN_BACKOFF)
    assert (duration, stamp) == ('2', wanted), (duration, stamp)


def test_a_reset_beyond_the_ceiling_is_capped_and_names_the_cap(tmp):
    """The ceiling is what an absurd header gets, so naming the reset
    there would promise a six-hour wait ends somewhere it never does.
    """
    del tmp
    mod = _client()
    now = 1789012345.0
    duration, stamp, slept = _paused(mod, now, now + 10 ** 9)
    assert slept == [float(mod.MAX_BACKOFF)], slept
    wanted = _stamp(now + mod.MAX_BACKOFF)
    assert (duration, stamp) == ('21600', wanted), (duration, stamp)


def test_a_bound_shorter_than_the_reset_truncates_and_names_the_bound(tmp):
    """The last clamp: a bound shorter than the wait, and its own moment.

    The floor and the ceiling bound the wait; the bound ends it. When
    the watcher would sleep past its own deadline the sleep is the
    remainder, and the line names that - the deadline reached, not a
    reset no part of this watcher will be alive to see.
    """
    del tmp
    mod = _client()
    now = 1789012345.0
    bound = 1000.0
    duration, stamp, slept = _paused(
        mod, now, now + 3600, deadline=bound + 3.0, monotonic=bound)
    assert slept == [3.0], slept
    wanted = _stamp(now + 3.0)
    assert (duration, stamp) == ('3', wanted), (duration, stamp)


def test_a_refusal_with_no_reset_at_all_waits_the_plain_minute(tmp):
    """Nothing to be accurate about, and nothing for the floor or the
    ceiling to act on: a plain minute.
    """
    del tmp
    mod = _client()
    now = 1789012345.0
    duration, stamp, slept = _paused(mod, now, None)
    assert slept == [60.0], slept
    wanted = _stamp(now + mod.DEFAULT_BACKOFF)
    assert (duration, stamp) == ('60', wanted), (duration, stamp)


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


def test_every_run_of_one_workflow_carries_the_workflow_name(tmp):
    """The producer invariant a required-workflow check rests on.

    `ci_wait.py` reads the names of the runs its newest-run filter left, and
    the filter keeps one run per workflow, so a workflow whose newest run
    carried a different name from its older ones would have its `tests` run
    dropped and the head read incomplete. `_run_from_suites` cannot emit
    that: it takes every run's name from the run's own workflow record, so
    two runs of one workflow come out with one name whatever their ids and
    start times. Read through the producer rather than asserted in prose -
    two suites of one workflow, two runs, one name, and a second workflow
    with a name of its own so the equality is not a constant."""
    del tmp
    mod = _client()
    same = [mod._run_from_suites([_suite(rid)]) for rid in (101, 102)]
    other = mod._run_from_suites([_suite(103, workflow=22)])
    assert len({run['workflow_id'] for run in same}) == 1, same
    assert len({run['name'] for run in same}) == 1, same
    assert other['name'] != same[0]['name'], (same, other)


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
