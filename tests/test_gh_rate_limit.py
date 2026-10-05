#!/usr/bin/env python3
"""Which `gh` answers are a rate-limit refusal, and when to resume.

`gh_rate_limit.py` states that rule, and it has exactly one caller -
`gh_client.py` - so every control here drives the REAL `gh_client.graphql`
against the fake `gh` in `_fake_gh.py` and reads back the exception the
client raised or the data it returned. Calling the classifier directly
would prove the classifier's own spelling; what the tree depends on is
that the CLIENT asks it and obeys it, and a client that dropped
`RateLimited` leaves a direct call green.

The rule is an answer reports exhaustion when it did NOT DELIVER what was
asked for and carries rate-limit evidence, or when it delivered and its
own `errors[]` entry is the report. The line is drawn at delivery because
of who owns the words: an `errors[]` `type` or `code` is a label the
server writes and no caller can put its own data into, while a header,
a body and a stderr complaint are three places a caller's own field
spells the two words. So each carrier is driven in BOTH directions here -
the shape that must be a pause and the near miss that must not - because
a classifier pinned in one direction is a classifier pinned on whichever
direction was written first. The two false positives that shape produced
are recorded in the module's own docstring - a 200 that SUCCEEDED with
complete data and a `gh` warning that merely mentioned a limit, and the
last good request before the window closes carrying
`X-Ratelimit-Remaining: 0` beside valid data - both rows below, each
its pause-shaped partner with one field changed.
"""
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
from _watcher_fixtures import RUNS_QUERY  # noqa: E402
from _watcher_fixtures import runs_page  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'

# The instant the frozen clock reads. Every assertion that needs a
# resume instant states the exact value, not a band: a band passes on
# a reader returning half the number.
FROZEN = 1000.0
# The reset a `rateLimit` extension reports, as the string a body carries
# and as the epoch it resolves to. Spelled out rather than re-parsed, so
# an assertion is never the module's parser agreeing with itself.
STAMP = '2026-09-20T11:00:00Z'
STAMP_EPOCH = 1789902000.0


class _Held:
    """A clock that reads one instant and never moves. Only `time` is
    here, because `gh_rate_limit` reads only `time`: a missing `sleep`
    is then a loud `AttributeError`, not a real wait."""

    def __init__(self, moment):
        self.moment = moment

    def time(self):
        return self.moment


@contextlib.contextmanager
def _clock(moment=FROZEN):
    """`gh_rate_limit`'s clock, held at one instant.

    Patched on the module the client already imported - `gh_client` does
    `from gh_rate_limit import exhausted`, so a fresh load would be an
    object nothing the client calls reads. `setattr` for the two
    assignments: the module was executed from a path, and a checker
    reading it as a typed namespace reads a file never seen."""
    mod = sys.modules['gh_rate_limit']
    real = mod.time
    setattr(mod, 'time', _Held(moment))
    try:
        yield mod
    finally:
        setattr(mod, 'time', real)


def _client():
    return _util.load(SKILL / 'gh_client.py', 'gh_client_rate_limit_contract')


def _answered(client, tmp, answer, moment=FROZEN):
    """(data, refusal, failure) from one real `gh_client.graphql` call.

    The three are exclusive by construction, and a caller asserts on
    the one it expects. Both classes are read off the passed client
    rather than named here, because `_util.load` executes the file per
    call and two loads are two classes - a check against a second
    copy's `QueryError` fails every run for a reason the rule never
    touched."""
    fake = _fake_gh.FakeGh(tmp, {RUNS_QUERY: answer})
    with fake.activate(), _clock(moment):
        try:
            return client.graphql(client.RUNS_QUERY), None, None
        except client.RateLimited as refusal:
            return None, refusal, None
        except client.QueryError as failure:
            return None, None, failure


def _paused(client, tmp, answer):
    """The `RateLimited` one answer reports, or a failure naming why not.
    A control that wants a pause and reads anything else is told which of
    the two it got and what the other said, rather than an `is None` that
    reads the same for a classifier that refused and one that gave up.
    """
    data, refusal, failure = _answered(client, tmp, answer)
    if refusal is None:
        raise AssertionError(
            f'no pause: data={data!r} failure={failure!r}')
    return refusal


def _the_data(client, tmp, answer):
    """The data one answer delivered, or a failure naming why not."""
    data, refusal, failure = _answered(client, tmp, answer)
    if data is None:
        raise AssertionError(
            f'nothing delivered: refusal={refusal!r} failure={failure!r}')
    return data


def _undelivered(client, tmp, answer):
    """The `QueryError` one answer reported, or a failure naming the pause."""
    data, refusal, failure = _answered(client, tmp, answer)
    if failure is None:
        raise AssertionError(
            f'no failure: data={data!r} refusal={refusal!r}')
    return failure


# ---- the complaint: the only carrier a refusal before transport has ----

def test_an_empty_answer_whose_complaint_names_a_limit_is_a_pause(tmp):
    """`gh` refusing before the transport produced a response leaves no
    status and no header block, so the complaint is all there is - and
    the only carrier that can never name an instant, so the pause it
    leads to is the caller's plain minute. `resume_at` being None is
    the assertion: a reader inventing a default here would send the
    waiter to sleep on a guess the answer never made."""
    client = _client()
    refusal = _paused(client, tmp, {
        'status': 200, 'stdout': '', 'exit': 1,
        'stderr': 'gh: API rate limit exceeded for user 1\n'})
    assert refusal.resume_at is None, refusal.resume_at
    assert str(refusal) == 'gh: API rate limit exceeded for user 1', refusal


def test_an_empty_answer_whose_complaint_names_nothing_is_a_failure(tmp):
    """The near miss, the shape every `gh` that cannot reach the API has
    always had: the same empty stdout, a complaint saying nothing about
    a limit, and the plain failure. Without this row a reader calling
    every complaint a pause sleeps a minute over a DNS error."""
    client = _client()
    failure = _undelivered(client, tmp, {
        'status': 200, 'stdout': '', 'exit': 1,
        'stderr': 'gh: could not resolve host\n'})
    assert str(failure) == 'gh: could not resolve host', failure


# ---- the exit code, and the evidence read before it ----

def test_a_complaint_naming_a_limit_pauses_an_answer_that_did_not_deliver(
        tmp):
    """The live shape, issue 1338: a throttled GraphQL query is answered
    200 and `gh` exits 1 over a message naming the limit. The status
    says nothing is wrong, the body carries the query's complete data,
    and the words are on stderr. Read the exit code first and this is
    a plain failure - the evidence is read BEFORE the code for that
    reason, the instant None."""
    client = _client()
    refusal = _paused(client, tmp, {
        'status': 200, 'exit': 1, 'headers': {}, 'stderr': (
            'gh: API rate limit exceeded for user 1\n'),
        'body': runs_page([])})
    assert refusal.resume_at is None, refusal.resume_at
    assert 'HTTP 200' in str(refusal), refusal


def test_an_undelivered_answer_carrying_no_evidence_is_a_failure(tmp):
    """The co-condition's other half, alone: nothing here names a limit -
    no header, no `errors[]`, no body text, no complaint - so the
    evidence arm runs over four carriers and finds nothing. A reader
    reading "did not deliver" as "is a pause" would sleep a minute over
    a 500."""
    client = _client()
    failure = _undelivered(client, tmp, {
        'status': 500, 'exit': 1, 'headers': {}, 'stderr': '',
        'body': {'message': 'server error'}})
    assert 'server error' in str(failure), failure


# ---- the headers: an instant, and the two things that make it evidence ----

def test_a_retry_after_header_is_the_instant_the_refusal_carries(tmp):
    """A `Retry-After` is a report on its own and it counts DOWN, so the
    instant is the clock plus the number. Driven on a 200 because that
    is the status that cannot complete the co-condition any other way:
    a reader that dropped the header fast path would still answer this
    on a 403, and the short-circuit is the header's whole own rule. The
    value is exact, a band passing on a reader returning the wall clock
    or half the number."""
    client = _client()
    refusal = _paused(client, tmp, {
        'status': 200, 'exit': 1, 'headers': {'Retry-After': '7'},
        'stderr': '', 'body': {'message': 'forbidden'}})
    assert refusal.resume_at == FROZEN + 7, refusal.resume_at


def test_a_retry_after_the_reader_cannot_count_falls_to_the_reset(tmp):
    """`Retry-After` is an HTTP-date as often as it is a count, and a
    date is not something this reader can turn into a moment - so the
    value is not read as one, and the reset beside it is. The same
    header with no reset beside it is still a REPORT - a pause carrying
    no instant, so the waiter falls back to its plain minute. The third
    arm: a `x-ratelimit-reset` the reader cannot turn into a moment is
    not an instant, and with no `Retry-After` beside it nothing is left
    to report - a failure, not a pause."""
    client = _client()
    dated = 'Wed, 21 Oct 2026 07:28:00 GMT'
    refusal = _paused(client, tmp, {
        'status': 200, 'exit': 1, 'stderr': '',
        'headers': {'Retry-After': dated, 'x-ratelimit-reset': '1900'},
        'body': {'message': 'forbidden'}})
    assert refusal.resume_at == 1900.0, refusal.resume_at
    alone = _paused(client, tmp, {
        'status': 200, 'exit': 1, 'stderr': '',
        'headers': {'Retry-After': dated},
        'body': {'message': 'forbidden'}})
    assert alone.resume_at is None, alone.resume_at
    _undelivered(client, tmp, {
        'status': 200, 'exit': 1, 'stderr': '',
        'headers': {'x-ratelimit-reset': dated},
        'body': {'message': 'forbidden'}})


def test_a_reset_on_its_own_is_not_evidence(tmp):
    """Half the co-condition, absent. A reset says the limit is gone and
    says nothing about when it returns, so a 200 that exited 1 carrying
    a reset and nothing beside it is a failure. `_undelivered` returning
    is the assertion, and this row also catches a widening of the
    refusal pair to every status: a 200 refused nothing, and its reset
    names no limit of its own."""
    client = _client()
    _undelivered(client, tmp, {
        'status': 200, 'exit': 1, 'headers': {'x-ratelimit-reset': '1900'},
        'stderr': '',
        'body': {'errors': [{'message': 'could not resolve'}]}})


def test_a_spent_counter_with_no_reset_carries_no_instant_to_wait_for(tmp):
    """The other half: the counter says the limit is gone and the answer
    does not say when it returns, so `_resume_at` answers None rather
    than the clock, and a reader that took the counter alone takes this
    row."""
    client = _client()
    _undelivered(client, tmp, {
        'status': 200, 'exit': 1, 'headers': {'x-ratelimit-remaining': '0'},
        'stderr': '', 'body': {'errors': [{'message': 'nope'}]}})


def test_a_spent_counter_beside_a_reset_is_a_pause_at_that_reset(tmp):
    """The pair, and the shape the live refusal actually arrives in: a
    200 carrying `X-Ratelimit-Remaining: 0` and the reset it returns
    at, over a `gh` that exits 1. Neither header is evidence alone and
    together they are the report - and the instant is the reset, an
    absolute epoch from the header rather than the clock plus a count,
    which is what tells the two apart."""
    client = _client()
    refusal = _paused(client, tmp, {
        'status': 200, 'exit': 1, 'stderr': '',
        'headers': {'x-ratelimit-remaining': '0',
                    'x-ratelimit-reset': '1900'},
        'body': {'errors': [{'message': 'could not resolve'}]}})
    assert refusal.resume_at == 1900.0, refusal.resume_at


def test_a_reset_on_a_403_or_a_429_is_a_pause_and_a_400_is_a_failure(tmp):
    """On a status that has already said the request was refused, the
    reset says when to try again and needs no spent counter beside it.
    Both statuses are driven, because a reader that kept only the one
    this repository has seen answers the other as an ordinary failure.
    The 400 is the negative space: a reset means retry only under a
    status that already refused."""
    client = _client()
    for status in (403, 429):
        refusal = _paused(client, tmp, {
            'status': status, 'exit': 1, 'stderr': '',
            'headers': {'x-ratelimit-reset': '1900'},
            'body': {'message': 'try later'}})
        assert refusal.resume_at == 1900.0, (status, refusal.resume_at)
    _undelivered(client, tmp, {
        'status': 400, 'exit': 1, 'stderr': '',
        'headers': {'x-ratelimit-reset': '1900'},
        'body': {'message': 'try later'}})


# ---- the body, read only where the status has already said something ----

def test_the_body_is_a_carrier_on_a_refusal_status_and_on_nothing_else(
        tmp):
    """Six statuses, one body, both directions. A throttled GraphQL query
    is answered 200, so a reader that read the body only on 403 and 429
    misses the shape this repository actually met; and a body naming a
    limit under any other status says so by accident. The 400 is the
    near miss a widening would reach for: `gh` answers a malformed
    GraphQL request with one, a validation error quoting the field
    naming a limit under a status that never refused one."""
    client = _client()
    body = 'API rate limit exceeded for user 1'
    for status in (200, 403, 429):
        refusal = _paused(client, tmp, {
            'status': status, 'exit': 1, 'headers': {}, 'stderr': '',
            'body': body})
        assert str(refusal) == f'HTTP {status}: {body}', (status, refusal)
    for status in (404, 400, 500):
        _undelivered(client, tmp, {
            'status': status, 'exit': 1, 'headers': {}, 'stderr': '',
            'body': body})


def test_the_statuses_a_body_is_read_on_are_exactly_three(tmp):
    """The SET and not three members of it: a status ADDED to it - 422,
    the next one a reader would reach for - is caught by none of the
    rows above, each of which reads the same body on the same call.
    `ACCEPTABLE` is pinned that way in `test_ci_wait_published.py` too.
    `client` is loaded for its side effect: `gh_client` does
    `from gh_rate_limit import exhausted`, so the set this reads is
    the object the client itself is holding."""
    del tmp
    _client()
    assert sys.modules['gh_rate_limit'].REFUSAL_STATUSES == frozenset(
        {200, 403, 429})


def test_a_delivered_200_carrying_a_limit_name_is_not_a_pause(tmp):
    """`rateLimit` is the name of a REAL GraphQL extension, so a body
    that merely CONTAINS the two words is a body that will contain them
    on a completely successful read. This is a delivered 200 whose data
    has such a field, and reading its body as a report would refuse a
    call that worked and returned what it was asked for."""
    client = _client()
    data = _the_data(client, tmp, {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'data': {'viewer': {'rateLimit': {'remaining': 4999}}}}})
    assert data == {'viewer': {'rateLimit': {'remaining': 4999}}}, data


def test_a_delivered_200_with_a_limit_mention_on_stderr_is_not_a_pause(
        tmp):
    """The other false positive the module's docstring records, measured:
    a 200 that SUCCEEDED, carrying complete data, with a `gh` warning on
    stderr that merely mentions a limit was answered
    `RateLimited(resume_at=None)` - a flat minute's pause over a call
    that worked. The identical stderr beside an exit code of 1 IS the
    pause, the row above this one; the delivery is the difference."""
    client = _client()
    data = _the_data(client, tmp, {
        'status': 200, 'exit': 0, 'headers': {}, 'body': runs_page([]),
        'stderr': 'gh: warning: rate limit remaining 12\n'})
    assert data == runs_page([])['data'], data


def test_the_last_good_request_is_not_discarded_for_its_own_reset(tmp):
    """The second measured false positive: the LAST successful request
    before the window closes carries `X-Ratelimit-Remaining: 0` and the
    reset beside valid data, and reading that pair as a report threw
    away a good answer to wait for a limit that had not refused it. The
    same headers on an undelivered answer are the pause three rows up;
    delivery is the difference again."""
    client = _client()
    data = _the_data(client, tmp, {
        'status': 200, 'exit': 0, 'stderr': '', 'body': runs_page([]),
        'headers': {'x-ratelimit-remaining': '0',
                    'x-ratelimit-reset': '1900'}})
    assert data == runs_page([])['data'], data


# ---- the `errors[]` entry, read whatever the answer delivered ----

def test_an_errors_entry_is_the_report_even_on_a_delivered_answer(tmp):
    """The one carrier read on an answer that DID deliver: a throttler
    reporting at a NESTED field nulls part of `data` and leaves the
    rest, so the answer is delivered and its own `errors[]` entry is
    the only report there is. The reset is driven in both places the
    extension carries it, a reader keeping only the nested one pausing
    with no instant on every real capture."""
    client = _client()
    for extensions in ({'rateLimit': {'resetAt': STAMP}},
                       {'resetAt': STAMP}):
        refusal = _paused(client, tmp, {
            'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
            'body': {'data': {'repository': None},
                     'errors': [{'type': 'RATE_LIMITED',
                                 'extensions': extensions}]}})
        assert refusal.resume_at == STAMP_EPOCH, (
            extensions, refusal.resume_at)


def test_a_report_naming_a_retry_after_pauses_for_those_seconds(tmp):
    """The instant the extension reports as a COUNT rather than a
    moment, added to the clock - and driven in both places it is
    carried, for the reason the reset above is. A reader that read the
    wall clock instead of adding to it would wake a watcher in the
    past, the waiter's own floor turning that into a hot loop."""
    client = _client()
    for extensions in ({'rateLimit': {'retryAfter': 45}},
                       {'retryAfter': 45}):
        refusal = _paused(client, tmp, {
            'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
            'body': {'data': {'repository': None},
                     'errors': [{'type': 'RATE_LIMITED',
                                 'extensions': extensions}]}})
        assert refusal.resume_at == FROZEN + 45, (
            extensions, refusal.resume_at)


def test_a_reset_it_cannot_read_is_skipped_for_the_one_beside_it(tmp):
    """A body is data, and the reader may not assume its shape: an
    extension carrying a `resetAt` that is not a timestamp is stepped
    over rather than believed, the pause falling to the retry beside
    it. A reader that let the `ValueError` out would answer a throttled
    query with a loud failure."""
    client = _client()
    refusal = _paused(client, tmp, {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'data': {'repository': None},
                 'errors': [{'type': 'RATE_LIMITED', 'extensions': {
                     'rateLimit': {'resetAt': 'not-a-timestamp',
                                   'retryAfter': 45}}}]}})
    assert refusal.resume_at == FROZEN + 45, refusal.resume_at


def test_a_report_naming_no_instant_is_a_pause_with_no_instant(tmp):
    """The capture this tree actually holds carries a `type` and nothing
    else, so the common case is a pause with no moment in it at all. A
    reader that demanded an instant before agreeing it was a refusal
    would answer the only shape GitHub has been observed to send as an
    ordinary failure. The `retryAfter` beside it is a string, the same
    reason again: a body is data, so the reader may not assume a number
    where the key is."""
    client = _client()
    refusal = _paused(client, tmp, {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'data': {'repository': None},
                 'errors': [{'type': 'RATE_LIMIT'}]}})
    assert refusal.resume_at is None, refusal.resume_at
    counted = _paused(client, tmp, {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'data': {'repository': None},
                 'errors': [{'type': 'RATE_LIMIT',
                             'extensions': {'retryAfter': '45'}}]}})
    assert counted.resume_at is None, counted.resume_at


def test_every_spelling_of_the_report_is_read_and_a_lookalike_is_not(tmp):
    """The match is on the two WORDS rather than on a list of the
    strings, so a spelling nobody has seen yet still reads as the report
    it is. Driven on a 500, and the status is the load-bearing part of
    the fixture: on a status the body is not read on, the `errors[]`
    entry's own `code` is the only witness. The near misses are the
    other half: a different report, a word that merely ENDS in the two
    behind a letter, letters standing BETWEEN the two, and the two words
    the other way round - all four failures, the separator class the
    matcher holds between the two words rejecting the third."""
    client = _client()
    for label in ('RATE_LIMITED', 'RATE_LIMIT', 'graphql_rate_limit'):
        refusal = _paused(client, tmp, {
            'status': 500, 'exit': 1, 'headers': {}, 'stderr': '',
            'body': {'data': None, 'errors': [{'code': label}]}})
        assert refusal.resume_at is None, (label, refusal.resume_at)
    for label in ('NOT_FOUND', 'SUBRATELIMITED', 'RATEWINDOWLIMIT',
                  'limit_reached'):
        _undelivered(client, tmp, {
            'status': 500, 'exit': 1, 'headers': {}, 'stderr': '',
            'body': {'data': None, 'errors': [{'code': label}]}})


def test_an_errors_entry_that_is_not_an_object_is_stepped_over(tmp):
    """A body is data, so an `errors[]` that is not a list of objects is
    stepped over rather than believed - the sharpest near miss in the
    module, the string spelling the two words exactly and still not the
    report: an `errors[]` entry is read only for its `type` and `code`,
    the labels the server writes."""
    client = _client()
    data = _the_data(client, tmp, {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'data': {'repository': None},
                 'errors': ['API rate limit exceeded']}})
    assert data == {'repository': None}, data


def test_an_errors_message_is_not_the_report_the_labels_carry(tmp):
    """The field beside the pair, and the reason the pair is two and not
    three: `type` and `code` are labels the server writes and no caller
    can put its own data into, while `message` is free text - a
    validation error quotes the two words while saying nothing about a
    window. Driven on a delivered 200, the body carrier never asked, so
    the words this body spells are the entry's own."""
    client = _client()
    data = _the_data(client, tmp, {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'data': {'repository': None},
                 'errors': [{'type': 'NOT_FOUND',
                             'message': 'API rate limit exceeded'}]}})
    assert data == {'repository': None}, data


# ---- which instant the refusal carries ----

def test_the_instant_is_taken_from_the_first_carrier_that_carries_one(tmp):
    """Both halves, because they are different failures. Two carriers
    report an instant here and the first one wins, so a reader that took
    the last - or lost the counting header to the absolute epoch in the
    body - waits for a different moment than the answer named. A carrier
    reporting no instant is stepped over rather than taken as a null, so
    the epoch in the second half is not lost to the carriers before it."""
    client = _client()
    first = _paused(client, tmp, {
        'status': 403, 'exit': 1, 'stderr': '',
        'headers': {'Retry-After': '7'},
        'body': {'data': {'repository': None},
                 'errors': [{'type': 'RATE_LIMITED',
                             'extensions': {'resetAt': STAMP}}]}})
    assert first.resume_at == FROZEN + 7, first.resume_at
    second = _paused(client, tmp, {
        'status': 200, 'exit': 1, 'headers': {}, 'stderr': '',
        'body': {'data': {'repository': None},
                 'errors': [{'type': 'RATE_LIMITED',
                             'extensions': {'resetAt': STAMP}}]}})
    assert second.resume_at == STAMP_EPOCH, second.resume_at


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ghratelimit_')


if __name__ == '__main__':
    raise SystemExit(main())
