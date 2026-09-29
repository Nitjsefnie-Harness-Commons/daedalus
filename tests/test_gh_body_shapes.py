#!/usr/bin/env python3
"""The states the evidence can be in that a control most often forgets.

A refusal is rendered from whatever the answer carried, so every state
that evidence can hold has to survive the rendering: a body that is not
JSON, a body that is JSON and not an object, an `errors[]` that is not a
list of entries, a complaint `gh` never wrote because the status was
happy. These are the rows a fix reaches last, and each of them is driven
through the client's own `graphql` and a real `gh` process, so the state
is one the reader actually met.

The two questions the rest of the refusal controls ask - which carrier
carries the report, and what a pause is worth - are in
`tests/test_gh_refusal.py`. The double these run on is in
`tests/test_fake_gh.py`.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ghshapes_')


if __name__ == '__main__':
    raise SystemExit(main())
