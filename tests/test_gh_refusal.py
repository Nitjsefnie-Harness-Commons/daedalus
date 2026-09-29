#!/usr/bin/env python3
"""Which answers report a rate limit, which report only that they failed.

Every control here is one axis of the evidence, driven through the
client's own `graphql` and answered by a real `gh` process from the fake
executable double in `_fake_gh.py`. The axis matters more than the case:
a fix that closes the headers and leaves the body, or a control that
reads two carriers of the same value and is blinded by their agreeing,
is the failure this file is arranged to make impossible. Every widening
the matcher got carries its negative in the same place - an entry naming
no limit, a complaint naming no limit, a nonzero exit over nothing, a
counter spent with no reset behind it.
"""
import contextlib
import io
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

    targets = [mod, _reader()]
    real = {target: getattr(target, 'time') for target in targets}
    for target in targets:
        setattr(target, 'time', _Pinned())
    try:
        yield
    finally:
        for target, clock in real.items():
            setattr(target, 'time', clock)


def _reader():
    """The module the client reads a refusal with, by its imported name.

    Reading a refusal is `gh_rate_limit`'s subject and the client's only
    import of it, so the name it arrived under is the one place a control
    can reach the reader itself rather than the answer it produces.
    """
    return sys.modules['gh_rate_limit']


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


def test_a_spent_limit_header_survives_a_body_that_is_not_json(tmp):
    """Evidence the body could neither corroborate nor contradict.

    The header block is read before the body is understood, so a body
    that is not JSON at all is a failure only when nothing else reported
    the limit. With the headers reporting it the refusal stands and names
    the reset, rather than dying on a parse it never needed.
    """
    mod = _client()
    reset = int(time.time()) + 120
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': spent_limit_response(
        reset, body='not json at all', exit=1, stderr='gh: timed out\n')})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at == reset, refusal.resume_at
        else:
            raise AssertionError('the headers reported the limit')


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


def test_an_error_entry_that_is_not_an_object_is_stepped_over(tmp):
    """An `errors[]` that is not a list of objects is a shape a body can
    hold and a reader has to survive. GitHub's schema says objects, and a
    reader that believes the entry rather than the shape it is reading
    dies on the first string. The answer is the ordinary failure, not a
    refusal and not data.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200,
        'body': {'data': None, 'errors': ['boom', 42, None]}}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('a malformed errors[] must fail the query')


def test_errors_that_is_not_a_list_of_entries_is_a_query_error(tmp):
    """The same shape one level out: `errors` is a string, so a reader
    that iterates it reaches a character where it expected an entry.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200, 'body': {'data': None, 'errors': 'boom'}}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('errors that is a string must fail')


def test_a_body_that_is_a_json_array_is_a_query_error(tmp):
    """A 200 whose body parses and is not an object. The reader is handed
    it before the exit code is judged, so it has to survive the shape
    rather than reach for `errors` on something that carries none.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'status': 200, 'body': [1, 2, 3]}})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('a JSON array body must fail the query')


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


def test_a_retranslated_header_block_still_yields_its_values(tmp):
    """A re-translated header block still yields its values. Without that,
    the block ends early, the header lines fall into the body, and a
    reported reset arrives as no reset - a 60-second default instead.

    The reader is reached by name because the subject is the PARSE, not
    the refusal: `gh_client.graphql` calls this same reader on this same
    pair, so a refusal here is a refusal there, and the call site is not
    left unpinned by this control standing in for it.
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
    refused, resume = _reader().exhausted(status, headers, body)
    assert refused and resume is not None, resume


def test_a_fractional_retry_after_becomes_a_near_reset(tmp):
    """The reader is reached by name here, standing in for the one call
    `gh_client.graphql` makes on a parsed body: a control through the
    public entry point cannot hand the reader a `retryAfter` this small
    without a fixture the client would turn down for some other reason
    first.

    The exposure the floor above defends against is reachable today.

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
        refused, resume = _reader()._graphql_refusal(payload)
    assert refused is True, refused
    assert resume == now + 0.001, resume
    assert resume - now < mod.MIN_BACKOFF, (mod.MIN_BACKOFF, resume - now)


def _wait_for(mod, answer, now, tmp):
    """The wait a refusal the reader found would buy, in whole seconds.

    Driven through `graphql` and a real `gh` process, so the instant is
    one the reader actually derived from the answer rather than one this
    control handed it: a reader returning a value the clamps below do not
    reach is exactly what these three rows exist to catch.
    """
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    watcher = mod.Watcher('w', out=io.StringIO())
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            return watcher._wait_seconds(refusal, now)
        raise AssertionError('the answer was not read as a refusal')


def test_a_refusal_carrying_no_instant_waits_the_plain_minute(tmp):
    """The first of the three states the evidence can be in: no reset
    reported at all. A pause still has to happen - the limit is real - but
    there is nothing to wake at, so the wait is the module's plain minute
    and not a moment the reader invented.
    """
    mod = _client()
    now = 1790266796.5
    answer = {'status': 200, 'stdout': '', 'exit': 1,
              'stderr': f'gh: {THROTTLED}\n'}
    assert _wait_for(mod, answer, now, tmp) == float(mod.DEFAULT_BACKOFF)
    assert mod.DEFAULT_BACKOFF == 60, mod.DEFAULT_BACKOFF


def test_a_refusal_carrying_a_reset_already_gone_waits_the_floor(tmp):
    """The second: a reset that has passed. Waiting exactly to it is no
    wait at all, and the counter the headers carry is spent, so the
    floor is the price of not hot-looping the API that just refused us.
    """
    mod = _client()
    now = 1790266796.5
    spent = spent_limit_response(int(now) - 5000, exit=0, stderr='')
    assert _wait_for(mod, spent, now, tmp) == float(mod.MIN_BACKOFF)
    assert mod.MIN_BACKOFF == 2, mod.MIN_BACKOFF


def test_a_refusal_carrying_an_absurd_reset_waits_the_ceiling(tmp):
    """The third: a reset no clock will reach. A fixture that reports one
    is a fixture that hangs a suite for the length of the wait, so the
    ceiling is what bounds it - past it the next refusal is a new pause
    with its own line rather than one sleep that never returns.
    """
    mod = _client()
    now = 1790266796.5
    absurd = spent_limit_response(int(now) + 10 ** 9, exit=0, stderr='')
    assert _wait_for(mod, absurd, now, tmp) == float(mod.MAX_BACKOFF)
    assert mod.MAX_BACKOFF == 6 * 3600, mod.MAX_BACKOFF


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ghrefusal_')


if __name__ == '__main__':
    raise SystemExit(main())
