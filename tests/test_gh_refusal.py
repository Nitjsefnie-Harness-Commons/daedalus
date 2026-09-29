#!/usr/bin/env python3
"""Which answers report a rate limit, which report only that they failed.

Every control but the two that say so is one axis of the evidence, driven
through the client's own `graphql` and answered by a real `gh` process from
the fake executable double in `_fake_gh.py`; the two reach the reader by
name, and each says which call site it stands in for. The axis matters
more than the case:
a fix that closes the headers and leaves the body, or a control that
reads two carriers of the same value and is blinded by their agreeing,
is the failure this file is arranged to make impossible. Every widening
the matcher got carries its negative in the same place: an entry naming
no limit, a complaint naming no limit, a nonzero exit over nothing.
"""
import contextlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
from _watcher_fixtures import THROTTLED  # noqa: E402
from _watcher_fixtures import spent_limit_response  # noqa: E402
from _watcher_fixtures import throttled_query  # noqa: E402

ROOT = _util.ROOT
SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'gh_client.py'

ITEM_QUERY = ('query Watch($after: String) { repository { items('
              'first: 2, after: $after) { pageInfo { hasNextPage '
              'endCursor } nodes { id } } } }')


def _client():
    return _util.load(SOURCE, 'gh_refusal_contract')


def _windows_text(text):
    """What a Windows text-mode stdout writes: every `\n` translated, so a
    `\r\n` the caller already spelled leaves as `\r\r\n`.
    """
    return text.replace('\n', '\r\n')


def _page(nodes):
    return {'data': {'repository': {'items': {
        'pageInfo': {'hasNextPage': False, 'endCursor': None},
        'nodes': nodes}}}}


@contextlib.contextmanager
def _frozen_client_clock(mod, now):
    """The clock the client AND the reader read, pinned to one instant.

    Everything but `time()` is the real module's, so a client that reads
    the monotonic clock or sleeps while it answers still can. Both are
    frozen because the reader is a module of its own with its own clock:
    freezing only the client would pin the reset against one instant and
    the delay against another, which is the disagreement these controls
    exist to rule out. No interval the runner spends decides either.
    """

    class _Pinned:
        @staticmethod
        def time():
            return now

        def __getattr__(self, name):
            return getattr(time, name)

    # The reader is a module of its own with its own clock, so it is
    # frozen beside the client. `get`, not `[]`: on a tree that predates
    # the split there is no such module, and a `KeyError` here would make
    # every control in this file red for a reason that says nothing.
    reader = sys.modules.get('gh_rate_limit')
    targets = [mod] if reader is None else [mod, reader]
    real = {target: getattr(target, 'time') for target in targets}
    for target in targets:
        setattr(target, 'time', _Pinned())
    try:
        yield
    finally:
        for target, clock in real.items():
            setattr(target, 'time', clock)


def test_a_200_whose_only_evidence_is_a_spent_limit_is_a_refusal(tmp):
    """The header axis alone, which is the refusal issue 1338 is about.

    Every other carrier is silent: the body names no limit, there is no
    `errors[]` entry to read, and `gh` exits 1 over a complaint about a
    timeout. A reader that believes only the body, or that raises over
    the exit code before reading anything, does not see this one at all -
    which is why it died with exit 3 within a second of starting.
    """
    mod = _client()
    reset = int(time.time()) + 120
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': spent_limit_response(
        reset, body={'data': {'repository': {'items': {'nodes': []}}}},
        exit=1, stderr='gh: the request timed out\n')})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at == reset, refusal.resume_at
        else:
            raise AssertionError('a spent rate-limit header must refuse')


def test_a_200_whose_spent_limit_carries_no_reset_is_not_a_refusal(tmp):
    """The negative of the row above: the counter alone is not the report.

    There is no moment to wait until, and a 200 that also exits 0 with
    data in it is an answer. Reading the counter as exhaustion on its own
    is what a widened guard buys, and the wait it would produce sleeps on
    a number with no instant behind it.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200,
        'headers': {'X-RateLimit-Limit': '5000',
                    'X-Ratelimit-Remaining': '0'},
        'body': _page([{'id': 1}])}})
    with fake.activate():
        data = mod.graphql(ITEM_QUERY, {'after': None})
    assert mod.nodes(data, ('repository', 'items')) == [{'id': 1}]


def test_a_200_with_a_reset_header_and_no_spent_counter_is_not_a_refusal(tmp):
    """The other negative of the header axis, and the one the 403 clause
    could swallow: a reset with a counter that still has budget in it, on
    a status the transport is satisfied with. Nothing says the limit is
    spent, and a reader that treats every reset header as a report sleeps
    on an answer that was simply a success.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200,
        'headers': {'X-RateLimit-Limit': '5000',
                    'X-Ratelimit-Remaining': '4999',
                    'X-Ratelimit-Reset': str(int(time.time()) + 120)},
        'body': _page([{'id': 1}])}})
    with fake.activate():
        data = mod.graphql(ITEM_QUERY, {'after': None})
    assert mod.nodes(data, ('repository', 'items')) == [{'id': 1}]


def test_a_graphql_error_whose_type_names_the_limit_is_a_refusal(tmp):
    """The body axis under the spelling the live refusal really used.

    `RATE_LIMIT` is not `RATE_LIMITED`, and this entry carries no `code`
    at all, so only the `type` can be the evidence. The refusal names no
    instant because the entry names none: pinning that `None` is what says
    the reset was genuinely absent rather than merely unread.
    """
    mod = _client()
    # Nothing else in the answer names a limit, or this control would pass
    # against a reader that never looked at the `type` at all: the body's
    # own text is a carrier of its own and a message naming the limit
    # would be evidence beside the one under test.
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': throttled_query(
        kind='RATE_LIMIT', code=None, exit=0, stderr='',
        message='The query could not be completed.')})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at is None, refusal.resume_at
        else:
            raise AssertionError('a RATE_LIMIT type must refuse')


def test_a_graphql_error_naming_the_limit_only_in_its_code_is_a_refusal(tmp):
    """The other half of the same entry: no `type` at all, and a `code` of
    `graphql_rate_limit`. Reading one field is not reading the report, and
    a control exercising only the `type` would pass against a reader that
    never looks at the `code` at all.
    """
    mod = _client()
    # Silent everywhere but the `code`, for the reason the row above gives.
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': throttled_query(
        kind=None, exit=0, stderr='',
        message='The query could not be completed.')})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited:
            pass
        else:
            raise AssertionError('a graphql_rate_limit code must refuse')


def test_a_graphql_reset_under_the_live_spelling_is_named(tmp):
    """The instant, read out of the entry under that spelling. No reset
    header is sent, so nothing else can supply it: a reader that took the
    instant from the headers and stopped there answers `None` here, and
    the pause falls back to the plain minute instead of the reset.
    """
    mod = _client()
    reset_at = '2030-01-01T00:00:00Z'
    wanted = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
    answer = throttled_query(reset_at=reset_at, exit=0, stderr='')
    assert not answer['headers'], answer['headers']
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at == wanted, refusal.resume_at
        else:
            raise AssertionError('the entry names a reset')


def test_a_graphql_error_naming_no_limit_is_a_query_error(tmp):
    """The inversion of the two rows above, and the guard against the
    matcher being widened past the report: an entry whose `type`, `code`
    and `message` all name something else is an ordinary failure, on a
    200 with `gh` exiting 0. `RateLimited` is not a `QueryError`, so a
    reader that slept on this fails here rather than passing quietly.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200, 'body': {'data': None, 'errors': [
            {'type': 'NOT_FOUND', 'code': 'NOT_FOUND',
             'message': 'Could not resolve to a Repository.'}]}}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('an ordinary GraphQL error must fail')


def test_a_refusal_with_no_stdout_is_a_refusal_when_it_names_the_limit(tmp):
    """The stderr axis, with nothing at all on stdout to corroborate it.

    Two seats saw a refusal reach this client with an empty stdout, and
    the old code raised over the complaint before anything could read it
    - the same defect by a different door. The refusal names no instant,
    because a complaint carries none: this is the one carrier with
    nothing to wait until, so the pause falls back to the plain minute
    rather than the client guessing a moment.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200, 'stdout': '', 'exit': 1,
        'stderr': f'gh: {THROTTLED}\n'}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at is None, refusal.resume_at
            assert refusal.args[0] == f'gh: {THROTTLED}', refusal.args[0]
        else:
            raise AssertionError('a complaint naming the limit must refuse')


def test_a_refusal_with_no_stdout_and_no_limit_is_a_query_error(tmp):
    """The inversion: the same empty stdout over a complaint naming no
    limit is the plain failure it has always been, and it says what `gh`
    said. A reader that made every empty stdout a wait would spin here.
    """
    mod = _client()
    complaint = 'gh: the request timed out\n'
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200, 'stdout': '', 'exit': 1, 'stderr': complaint}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError as exc:
            assert str(exc) == complaint.strip(), str(exc)
        else:
            raise AssertionError('an ordinary complaint must fail')


def test_a_200_that_exits_nonzero_over_an_ordinary_complaint_fails(tmp):
    """The exit code is the last evidence and not the first, so alone it
    is none. A 200 carrying a complete answer that exits 1 over a
    complaint naming no limit is a failure, and a reader treating every
    nonzero exit as a wait sleeps on it until the bound, forever.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200, 'body': _page([{'id': 1}]), 'exit': 1,
        'stderr': 'gh: the request timed out\n'}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('a nonzero exit over no evidence must fail')


def test_a_200_that_exits_nonzero_in_silence_is_a_query_error(tmp):
    """The same answer with no complaint at all: the last state the
    evidence can be in, and the one a stub fills in plausibly when it
    cannot model the case. Nothing names a limit, so nothing is a wait.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200, 'body': _page([{'id': 1}]), 'exit': 1,
        'stderr': ''}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('a silent nonzero exit must fail')


def test_a_refusal_renders_the_status_and_the_answer_it_refused(tmp):
    """The first line a person reads. Pinned because the carriers of a
    refusal changed under it, and this is where a reader would notice
    which one was used.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 403,
        'body': 'API rate limit exceeded for the account.'}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.args[0] == (
                'HTTP 403: API rate limit exceeded for the account.'
            ), refusal.args[0]
        else:
            raise AssertionError('a 403 naming the limit must refuse')


def test_a_refusal_with_no_body_renders_the_complaint_instead(tmp):
    """The second rendering: a header block and an empty body, where the
    only words anywhere are the ones `gh` wrote to stderr. A reader that
    rendered the status and dropped the complaint would leave a person
    holding a status code and no reason.
    """
    mod = _client()
    reset = int(time.time()) + 120
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': spent_limit_response(
        reset, body='', exit=1, stderr=f'gh: {THROTTLED}\n')})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.args[0] == f'HTTP 200: gh: {THROTTLED}', (
                refusal.args[0])
        else:
            raise AssertionError('the spent header must refuse')


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


def test_a_retranslated_header_block_still_yields_its_values(tmp):
    """A re-translated header block still yields its values. Without that,
    the block ends early, the header lines fall into the body, and a
    reported reset arrives as no reset - a 60-second default instead.

    The refusal those values produce is not asserted here, and the
    reason is that reaching the reader by name made this control
    unrunnable on a tree without the split - its redness there was a
    `KeyError` about a module name, which says nothing. The whole chain
    over the same bytes is driven through the public entry point in
    `tests/test_gh_client.py`, by
    `test_a_header_reset_survives_a_windows_text_stream_end_to_end`.
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


def test_a_fractional_retry_after_becomes_a_near_reset(tmp):
    """The exposure the floor above defends against is reachable today.

    `_graphql_refusal` takes any `retryAfter` that is a number, and a
    GraphQL body is JSON, so a fractional one arrives - a reset a
    thousandth of a second out. Without this, tightening that
    validation would leave every other control green while the one
    above justified itself falsely.

    Driven through `graphql` and a real `gh` process: `data` is null, so
    the answer did not deliver and the entry is read, and the instant it
    reports is the one the pause would wake at. The fixture omits
    `resetAt` because the refusal reads it first and would never reach
    `retryAfter`; add one and this passes for the wrong reason.
    """
    mod = _client()
    now = 1789012345.0
    answer = {'status': 200, 'exit': 0, 'stderr': '',
              'body': {'data': None, 'errors': [
                  {'type': 'RATE_LIMITED', 'extensions': {
                      'rateLimit': {'retryAfter': 0.001}}}]}}
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    resume = 'a fractional retryAfter did not refuse'
    with fake.activate(), _frozen_client_clock(mod, now):
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            resume = refusal.resume_at
    assert resume != 'a fractional retryAfter did not refuse', resume
    assert resume - now < mod.MIN_BACKOFF, (mod.MIN_BACKOFF, resume - now)


def test_a_refusal_is_never_a_query_error(tmp):
    """The invariant the `RateLimited` docstring claims and nothing held.

    `ci_wait.py:409` and `watch_all.py:209` both wrap a call in `except
    gh_client.QueryError` and read the failure as a failed query. A
    refusal that were a subclass would be swallowed by both, and a wait
    that paused on a rate limit would report it as a broken query - the
    exact reading this issue exists to remove, arrived at from the other
    direction.
    """
    mod = _client()
    assert issubclass(mod.RateLimited, RuntimeError), mod.RateLimited
    assert not issubclass(mod.RateLimited, mod.QueryError), mod.RateLimited
    assert not issubclass(mod.QueryError, mod.RateLimited), mod.RateLimited
    try:
        raise mod.RateLimited('rate limited', None)
    except mod.QueryError as caught:                   # noqa: B902
        raise AssertionError('a refusal was caught as a query error') \
            from caught
    except mod.RateLimited:
        pass


def test_a_json_body_naming_a_rate_limit_outside_errors_is_not_a_refusal(tmp):
    """A parsed body is read through `errors[]`, not through its text.

    A 200 that merely MENTIONS a limit in a payload field of its own is
    an answer, not a refusal, and reading the raw text of a body that
    parsed is a second opinion about an answer the structure already
    gave - a second one that fires on this body because `rateLimit` is
    the name of a real extension.
    """
    mod = _client()
    body = {'data': {'repository': {'items': {'nodes': [{'id': 1}]}}},
            'note': 'the account rate limit was consulted'}
    answer = {'status': 200, 'body': body}
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        data = mod.graphql(ITEM_QUERY, {'after': None})
    assert mod.nodes(data, ('repository', 'items')) == [{'id': 1}]


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ghrefusal_')


if __name__ == '__main__':
    raise SystemExit(main())
