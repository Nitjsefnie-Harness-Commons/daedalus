#!/usr/bin/env python3
"""The states the evidence can be in that a control most often forgets.

A refusal is rendered from whatever the answer carried, so every state
that evidence can hold has to survive the rendering: a body that is not
JSON, a body that is JSON and not an object, an `errors[]` that is not a
list of entries, a complaint `gh` never wrote because the status was
happy. These are the rows a fix reaches last, and each of them is driven
through the client's own `graphql` and a real `gh` process, so the state
is one the reader actually met.

The state the whole rule turns on is the first of them: DELIVERED, which
`gh_rate_limit.delivered` names once and the carriers are gated on. An
answer that did what it was asked for is not a refusal whatever else it
carries, and the rows below are the measured shapes of "whatever else it
carries" - a warning on stderr that mentions a limit, the last request
before the window closes, a field of the payload that names one.

Which carrier carries a report is in `tests/test_gh_refusal.py`, and what
a pause is worth is in `tests/test_gh_client.py`. The double these run
on is in `tests/test_fake_gh.py`.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
from _watcher_fixtures import THROTTLED  # noqa: E402
from _watcher_fixtures import delivered_answer  # noqa: E402
from _watcher_fixtures import rest_403  # noqa: E402
from _watcher_fixtures import spent_headers  # noqa: E402
from _watcher_fixtures import spent_limit_response  # noqa: E402

ROOT = _util.ROOT
SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'gh_client.py'

ITEM_QUERY = ('query Watch($after: String) { repository { items('
              'first: 2, after: $after) { pageInfo { hasNextPage '
              'endCursor } nodes { id } } } }')


def _client():
    return _util.load(SOURCE, 'gh_shapes_contract')


def _page(nodes):
    return {'data': {'repository': {'items': {
        'pageInfo': {'hasNextPage': False, 'endCursor': None},
        'nodes': nodes}}}}


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


def test_a_403_naming_the_limit_only_in_its_own_text_is_a_refusal(tmp):
    """The unparsed body, read by its own words and by nothing else.

    `gh` writes the body it read into stderr over any error status, so a
    403 whose body names a limit is ALSO evidence on the stderr carrier
    and a control that leaves the complaint where the fake puts it says
    nothing about which carrier answered. An empty complaint is what
    leaves the body the only carrier: this is the one row that can fail
    when the words of an unparsed body stop being read at all.
    """
    mod = _client()
    answer = {'status': 403, 'stderr': '',
              'body': 'API rate limit exceeded for the account.'}
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at is None, refusal.resume_at
        else:
            raise AssertionError('a 403 naming the limit must refuse')


def test_a_delivered_200_with_a_warning_about_the_limit_returns_its_data(tmp):
    """A call that worked is not a pause, however gh chose to describe it.

    `gh` writes warnings to stderr and one of them mentions the limit
    without the request being throttled. Before the delivery co-condition
    this answer was `RateLimited(resume_at=None)` - a flat minute's pause
    over a successful call, repeatable on every poll of a window the
    account still had budget in.
    """
    mod = _client()
    answer = delivered_answer(
        stderr='warning: 0 requests left before the rate limit resets\n')
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        data = mod.graphql(ITEM_QUERY, {'after': None})
    assert mod.nodes(data, ('repository', 'items')) == [{'id': 1}]


def test_a_delivered_200_that_spent_the_last_request_returns_its_data(tmp):
    """The last successful request before the window closes.

    It carries `X-Ratelimit-Remaining: 0` and a reset, and it also
    carries the data the caller asked for. Discarding it is the false
    positive the header axis caused on its own: a request that consumed
    the last of the budget is the one whose ANSWER the caller most wants.
    """
    mod = _client()
    answer = delivered_answer(headers=spent_headers(int(time.time()) + 45))
    assert answer['headers']['X-Ratelimit-Remaining'] == '0'
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        data = mod.graphql(ITEM_QUERY, {'after': None})
    assert mod.nodes(data, ('repository', 'items')) == [{'id': 1}]


def test_a_delivered_body_whose_own_field_names_a_limit_returns_its_data(tmp):
    """The words are the CALLER's, in a field of the caller's own.

    A query answer is whatever the caller asked for, and a field of it
    can contain the two words for reasons that have nothing to do with
    this account's budget. Reading a delivered body as text is reading
    the answer to a question nobody asked; a body that did not deliver is
    the API's own report, and that is the asymmetry the rule keeps.
    """
    mod = _client()
    answer = delivered_answer()
    answer['body']['note'] = 'the account rate limit was consulted'
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        data = mod.graphql(ITEM_QUERY, {'after': None})
    assert mod.nodes(data, ('repository', 'items')) == [{'id': 1}]


def test_the_same_body_without_the_data_is_read_as_the_report_it_is(tmp):
    """The inversion, and the line the co-condition draws: take the `data`
    away and the very same words become the API's own report of a limit.

    A partial answer - `data: null` beside what refused it - carries no
    result, so it did not deliver, so the text is read. That is what the
    two rows above protect and what this row takes away.
    """
    mod = _client()
    answer = delivered_answer()
    answer['body'] = {'data': None,
                      'note': 'the account rate limit was consulted'}
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at is None, refusal.resume_at
        else:
            raise AssertionError('an answer with no data is read as text')


def test_a_403_whose_only_evidence_is_the_body_is_still_a_refusal(tmp):
    """Some refusals carry the rate limit in the body and nowhere else;
    without that clause ci_wait would exit 3 instead of waiting out the
    reset.

    The body is the JSON one GitHub really sends, not a line of text: a
    plain string is not a shape this API produces for a 403, and a
    control written against one is a control against a fiction. The
    `message` is neither a GraphQL `type` nor a `code`, so the
    structured reader cannot see this at all and the text is the only
    reading of it.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': rest_403(
        stderr='gh: something else went wrong (HTTP 403)\n')})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at is None, refusal.resume_at
        else:
            raise AssertionError('a body-only rate-limit 403 must refuse')


def test_a_403_naming_no_limit_anywhere_is_an_ordinary_failure(tmp):
    """The inversion of the row above, on the same shape: a 403 with the
    same body shape, a complaint that names something else, and no
    headers. A permission refusal must not be answered by sleeping, and
    this is the row that says so for the body carrier specifically.
    """
    mod = _client()
    answer = {'status': 403, 'headers': {}, 'stderr':
              'gh: Resource not accessible by integration (HTTP 403)\n',
              'body': {'message': 'Resource not accessible by integration.'}}
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('a 403 naming no limit must fail the query')


def test_a_403_whose_json_body_names_the_limit_refuses_with_nothing_else(tmp):
    """The refusal the base read and a narrowing lost, with no carrier
    left to rescue it.

    No rate-limit headers - a secondary limit answers 403 with none - no
    `errors[]`, and `gh` writing nothing at all. The words are in the
    body's JSON `message`, so the text carrier is the only reader that
    can see this, and an answer that refused it answered a query that
    worked. `rest_403.py` in the review's evidence is this shape.
    """
    mod = _client()
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': rest_403()})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at is None, refusal.resume_at
            assert refusal.args[0].startswith('HTTP 403: '), refusal.args[0]
        else:
            raise AssertionError('the body names the limit: must refuse')


def test_that_403_with_a_body_naming_nothing_is_an_ordinary_failure(tmp):
    """The inversion: the same 403 - no headers, `gh` silent - carrying a
    `message` that names no limit. Nothing anywhere says a limit, so
    nothing is a pause and the query fails.
    """
    mod = _client()
    answer = rest_403(message='Resource not accessible by integration.')
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('a 403 naming no limit must fail the query')


def test_a_403_whose_only_evidence_is_the_complaint_is_a_refusal(tmp):
    """What real `gh` writes over a throttled 403, and the carrier the
    review showed is pinned by nothing.

    The body here is a permission refusal in every particular - it names
    no limit, and so do the headers - and the words are only on stderr.
    Deleting the stderr carrier leaves every other refusal control green
    while this answer, the one a real `gh` produces, stops refusing.
    """
    mod = _client()
    answer = {'status': 403, 'headers': {},
              'body': {'message': 'You have been temporarily blocked.'},
              'stderr': f'gh: {THROTTLED} (HTTP 403)\n'}
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at is None, refusal.resume_at
        else:
            raise AssertionError('a complaint naming the limit must refuse')


def test_that_403_with_a_complaint_naming_something_else_fails(tmp):
    """The inversion of the row above, character for character: the same
    403 with the same body, and a complaint that names a block rather
    than a limit. A reader that treated every complaint as a rate-limit
    report would sleep on a permission refusal.
    """
    mod = _client()
    answer = {'status': 403, 'headers': {},
              'body': {'message': 'You have been temporarily blocked.'},
              'stderr': 'gh: You have been temporarily blocked (HTTP 403)\n'}
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('a complaint naming no limit must fail')


def test_a_200_that_delivered_nothing_and_was_complained_about_refuses(tmp):
    """The complaint alone, on an answer that did not deliver, from a `gh`
    that exited 0.

    This is the shape a reader must not gate on the EXIT CODE: `gh` exits
    0 whenever the transport succeeded, and writes its warning to stderr
    whatever it did, so a 200 that carries no `data` beside a complaint
    naming the limit is a refusal with no header, no `errors[]` and no
    nonzero code to read. The co-condition the rule actually names is
    delivery, and this is the row that says so - gate the complaint on
    the code instead and only this one dies.
    """
    mod = _client()
    answer = {'status': 200, 'exit': 0,
              'body': {'data': None, 'repository': None},
              'stderr': f'gh: {THROTTLED}\n'}
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.RateLimited as refusal:
            assert refusal.resume_at is None, refusal.resume_at
        else:
            raise AssertionError('a complaint naming the limit must refuse')


def test_that_200_with_a_complaint_naming_something_else_fails(tmp):
    """The inversion, on the same shape: the same 200, the same exit 0,
    and a complaint that names a warning. Nothing says a limit.
    """
    mod = _client()
    answer = {'status': 200, 'exit': 0,
              'body': {'data': None, 'repository': None},
              'stderr': 'gh: the query was empty (HTTP 200)\n'}
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': answer})
    with fake.activate():
        try:
            mod.graphql(ITEM_QUERY, {'after': None})
        except mod.QueryError:
            pass
        else:
            raise AssertionError('a complaint naming no limit must fail')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ghshapes_')


if __name__ == '__main__':
    raise SystemExit(main())
