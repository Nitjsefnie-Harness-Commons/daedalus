#!/usr/bin/env python3
"""What `gh_client` calls an answer that is neither data nor a refusal.

`gh_client.py` is the transport every `ci_wait` read goes through, and
its subject is its own: the shape of a `gh` run, the read the pages of
one become, and the wait a refusal with no instant behind it turns into.
Three neighbours already hold the subjects around it and none of them
holds this one. `test_gh_rate_limit.py` asks WHICH ANSWERS ARE a
refusal, through this same client; `test_ci_wait_published.py` asks
whether a published verdict is a pass, end to end, as a process; and
`test_gh_client_lifetime.py` asks what the pipe a watcher child is
handed does. This file is the middle: the answers the other two do not
ask about, because for them a wrong answer is one their rows never
reach.

So this is NOT the published-verdict predicate `test_ci_wait_published.py`
states as its subject, and that file is not split for it - at 689 lines
against a 700 ceiling it has no room, and its own header claims a
subject this one does not share. The read the pages become is here for
the same reason: `ci_state` is the client's, and a suite about a
published verdict reaching it transitively is not a suite about it.

Every row drives the REAL loaded module against the fake `gh` in
`_fake_gh.py`, so the answer is a real `gh` process and only the
transport behind it is a double. `RateLimited` is deliberately not
caught anywhere in this file: an answer that pauses is the classifier's
verdict, `test_gh_rate_limit.py` owns it, and a row here that expected
one would be reading that suite's subject through this one's client.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
from _watcher_fixtures import RUNS_QUERY  # noqa: E402
from _watcher_fixtures import SHA  # noqa: E402
from _watcher_fixtures import runs_page  # noqa: E402
from _watcher_fixtures import suite  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'

# The instant the clock is held at, so every wait this file reads back is
# a number rather than a band. A band passes on a reader that returned
# half the answer, and the wait is the subject.
FROZEN = 1000.0


def _client(tmp, answer, gate=False):
    """The real module, and the fake `gh` it will read one answer from.

    Keyed on the selection, not on the whole query: a query that stopped
    asking for check runs finds no fixture and is refused. That is the
    width of it - the key is a substring of the request, so a query that
    kept `checkRuns` and dropped `checkSuites` would still match, and
    presence is not exclusivity.
    """
    fake = _fake_gh.FakeGh(tmp, {RUNS_QUERY: answer}, gate=gate)
    return _util.load(SKILL / 'gh_client.py', 'gh_client_answers'), fake


def _answered(client, fake, variables=None, env=None):
    """(data, failure) from one real call, the fake answering it.

    `env` is applied inside the activation, so a row that needs a `gh`
    this tree cannot offer overrides one the activation would otherwise
    have put back before the call.

    Only the failure path is caught. A `RateLimited` raised here is not
    a shape this file asks about, and swallowing it into `failure` would
    let a classifier that paused over a plain failure pass every row
    below.
    """
    with fake.activate():
        before = {name: os.environ.get(name) for name in (env or {})}
        os.environ.update(env or {})
        try:
            return client.graphql(client.RUNS_QUERY, variables), None
        except client.QueryError as failure:
            return None, failure
        finally:
            # Only the activation's own names were ever restored; an `env`
            # key outside them would otherwise stand for the rest of the
            # process, and the row after this one would read an
            # environment it never asked for.
            for name, value in before.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _failed(client, fake, variables=None, env=None):
    """The failure one answer reported, or a failure naming what it did."""
    data, failure = _answered(client, fake, variables, env)
    if failure is None:
        raise AssertionError(f'nothing failed: {data!r}')
    return failure


def _the_data(client, fake):
    """The data one answer delivered, or a failure naming why not."""
    data, failure = _answered(client, fake)
    if data is None:
        raise AssertionError(f'nothing delivered: {failure!r}')
    return data


# ---- the shape of the run itself ----

def test_a_response_with_no_header_block_is_not_an_answer(tmp):
    """The end of the block is what makes the rest of the run an answer,
    and a run that never wrote one - a `gh` killed between its status
    line and its body - is a failure rather than a body.

    This row is on `_parse` itself rather than on `graphql`, and that is
    forced rather than chosen: `_fake_gh` terminates every run it writes
    with a newline, so every answer it can render ends its header block,
    and no fixture of its own can reach the arm this row is about. The
    caller is not what is in question - the parser is, and the parser's
    own subject is a run's bytes.

    The two rows beside it are the boundary in both directions: the same
    run with the blank line back is a body, and the same run with the
    blank line and nothing after it is an empty body - which is a
    DIFFERENT failure, raised further on, and reporting it as a missing
    block would be reporting a `gh` that answered nothing as a `gh` that
    answered in pieces. The third is the line-by-line reason the parser
    reads this way at all: a re-translated ending carries a `\\r` on
    every line, and a reader that cut at the first blank-line byte pair
    would take the block's last header for the body's first line.

    The whitespace-only separator separates a line that is BLANK from one
    that is merely whitespace. Those are the same line to a caller - both
    say the header block is over - and not to a reader testing `line ==
    ''`: that one walks past the whitespace-only line, reads it as a
    header (partition finds no colon, so nothing is stored), reaches the
    end and raises, reporting a `gh` that answered in pieces as one that
    answered nothing.
    """
    del tmp
    client = _util.load(SKILL / 'gh_client.py', 'gh_client_answers')
    try:
        client._parse('HTTP/2.0 200 OK\nx-ratelimit-remaining: 4999')
    except client.QueryError as failure:
        assert str(failure) == 'no header block in the gh response', failure
    else:
        raise AssertionError('a run with no header block was answered')

    assert client._parse(
        'HTTP/2.0 200 OK\nx-ratelimit-remaining: 4999\n\n{"data": {}}'
    ) == (200, {'x-ratelimit-remaining': '4999'}, '{"data": {}}')
    assert client._parse('HTTP/2.0 200 OK\n\n') == (200, {}, '')
    assert client._parse(
        'HTTP/2.0 200 OK\r\nx-ratelimit-remaining: 4999\r\n\r\n{"data": {}}'
    ) == (200, {'x-ratelimit-remaining': '4999'}, '{"data": {}}')
    assert client._parse(
        'HTTP/2.0 200 OK\nx-ratelimit-remaining: 4999\n \n{"data": {}}'
    ) == (200, {'x-ratelimit-remaining': '4999'}, '{"data": {}}')

    # The reason the parser reads line by line, and the only one of its
    # two fixtures that names it: a re-translated ending carries TWO
    # `\r`s, and a reader that ends a line at a lone `\r` as well as at
    # `\n` reads that doubled one as a blank line of its own - which ends
    # the header block there and puts the header lines in the body. The
    # single `\r` above is an ordinary CRLF response, where the two
    # readers agree, so it cannot tell them apart; this one can, and
    # this is what fails if they are confused.
    doubled = 'HTTP/2.0 200 OK\r\r\nx-ratelimit-remaining: 4999\r\r\n\r\r\n'
    doubled += '{"data": {}}'
    assert client._parse(doubled) == (
        200, {'x-ratelimit-remaining': '4999'}, '{"data": {}}'
    )


def test_a_gh_that_cannot_be_launched_is_a_failure(tmp):
    """The half of the launch that is an operating-system refusal: the
    executable is not there. `gh` not installed, or removed between two
    polls of a wait that runs for hours, both arrive here - and neither
    is a rate limit, so neither may become a pause.

    `DAEDALUS_GH` is the module's own override, so this is a real launch
    failing rather than the transport replaced.
    """
    client, fake = _client(tmp, runs_page([]))
    absent = os.path.join(fake.dir, 'gh-that-was-never-installed')
    failure = _failed(client, fake, env={'DAEDALUS_GH': absent})
    assert str(failure).startswith('gh failed: '), failure
    assert absent in str(failure), failure


def test_a_gh_that_never_answers_is_a_failure_too(tmp):
    """The other half of the same `except`, and the reason it is a tuple
    rather than one class: a `gh` that hangs is a `SubprocessError`, not
    an `OSError`, and a reader that caught only the latter let the wait
    die on an exception instead of reporting a failed poll.

    The bound is one second rather than the module's 120 because the
    bound being pinned is not what this row is about, and the hold is
    what makes the timeout real: a fake that answered would be a row
    proving nothing. `entered` is the proof the call reached the hold,
    so a run whose fake answered before the bound would fail here rather
    than pass on a green that meant nothing ran.
    """
    client, fake = _client(tmp, runs_page([]), gate=True)
    # `setattr`, because the module was executed from a path rather than
    # imported: its names are as dynamic as the module object carries them
    # and a checker reading it as a typed namespace would be reading a
    # file it never saw.
    setattr(client, 'GH_TIMEOUT', 1)
    try:
        failure = _failed(client, fake)
    finally:
        fake.open_gate()
    assert fake.entered(), 'the call never reached the hold'
    assert str(failure).startswith('gh failed: '), failure
    assert 'timed out' in str(failure), failure


def test_only_the_launch_becomes_a_failure(tmp):
    """The negative space the tuple is drawn around. A caller whose
    variables cannot be written as JSON is a caller's bug, and it is
    raised before the process is ever started - so it stays a `TypeError`
    out in the open. A reader that widened the `try` to the whole call
    would answer a bug in the caller as a `gh` that failed, which is the
    one thing this handler must never do.
    """
    client = _util.load(SKILL / 'gh_client.py', 'gh_client_answers')
    try:
        client.graphql(client.RUNS_QUERY, {'unwritable': object()})
    except client.QueryError as failure:
        raise AssertionError(
            f'a caller bug was answered as a gh failure: {failure}') from None
    except TypeError:
        return
    raise AssertionError('a variable that cannot be JSON raised nothing')


# ---- the shape of the body, once the run itself was a response ----

def test_a_body_that_is_not_json_is_a_failure_naming_the_parse(tmp):
    """The two halves of the boundary, one character apart: a body cut
    short is a body the client could not read, and the message quotes
    the reader's own complaint about where it stopped. The complete body
    is the row's other half - a reader that refused anything that was not
    a perfectly formed object would refuse a real `gh` over a truncated
    pipe, and the message is what tells the two apart in a wait that
    runs for hours.
    """
    client, fake = _client(tmp, {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': '{"data": {"repository": '})
    failure = _failed(client, fake)
    assert str(failure).startswith('unparseable gh output: '), failure
    assert len(str(failure)) > len('unparseable gh output: '), failure

    fake.write_answers({RUNS_QUERY: {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': '{"data": {"repository": null}}'}})
    assert _the_data(client, fake) == {'repository': None}


def test_a_body_that_is_json_but_not_an_object_is_a_failure(tmp):
    """Every value JSON admits except the object one, because a body is
    data and the client may not assume its shape. The number and the
    boolean are beside the list and the string because a reader that
    refused only the two it had seen would pass every other row here.

    The two rows after the loop are the boundary in both directions, and
    they are a pair rather than one: `[]` and `{}` are both answers that
    carry nothing, and they are two DIFFERENT failures. A list is not a
    response the client can read at all; an object is one it read whole
    and found no data in. A reader that answered both with the same
    message would be reporting a `gh` that answered in pieces as one that
    answered with the wrong shape.
    """
    client, fake = _client(tmp, {'status': 200, 'exit': 0, 'headers': {},
                                 'stderr': '', 'body': 'null'})
    for body in ('[]', '["one"]', '"text"', '7', 'true', 'null'):
        fake.write_answers({RUNS_QUERY: {
            'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
            'body': body}})
        assert str(_failed(client, fake)) == (
            'the gh response is not a JSON object'), body

    fake.write_answers({RUNS_QUERY: {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': '{}'}})
    assert str(_failed(client, fake)).startswith(
        'no data in the gh response: ')

    fake.write_answers({RUNS_QUERY: {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'data': {}}}})
    assert _the_data(client, fake) == {}


def test_a_body_with_no_data_is_a_failure_quoting_the_server(tmp):
    """A GraphQL answer that delivered nothing says why in its own
    `errors[]`, and that is the only text a reader has: the client
    quotes it rather than reporting that data was absent, which is a
    statement about the client and not about the query.

    The cut at 300 characters is the second half, and it is why the
    quote is asserted by its length as well as by the words in it - a
    wait prints this line on every refusal, and a server that answered
    with a stack trace in its `errors[]` would otherwise fill the log.
    The last two rows are the near miss in both directions: the empty
    object IS data, and a `data` that is not an object is this same
    failure rather than something handed back to the caller.
    """
    answer = 'Could not resolve to a Repository with the name o/r.'
    client, fake = _client(tmp, {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'errors': [{'message': answer}]}})
    failure = _failed(client, fake)
    assert str(failure).startswith('no data in the gh response: '), failure
    assert 'Could not resolve to a Repository' in str(failure), failure

    loud = 'x' * 400
    fake.write_answers({RUNS_QUERY: {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'errors': [{'message': loud}]}}})
    quoted = str(_failed(client, fake))
    assert len(quoted) == len('no data in the gh response: ') + 300, quoted
    assert loud not in quoted, quoted

    for body in ({'data': []}, {'data': 'text'}):
        fake.write_answers({RUNS_QUERY: {
            'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
            'body': body}})
        assert str(_failed(client, fake)).startswith(
            'no data in the gh response: '), body
    fake.write_answers({RUNS_QUERY: {
        'status': 200, 'exit': 0, 'headers': {}, 'stderr': '',
        'body': {'data': {}}}})
    assert _the_data(client, fake) == {}


# ---- the read the pages become ----

def test_a_suite_with_no_run_is_no_run_and_its_checks_are_still_read(tmp):
    """Issue 1360's other half. A verdict published through the Checks
    API arrives in a suite that belongs to no workflow run, so the run
    list must not gain an entry for it - and its check runs must be read
    anyway, because that check run is the thing every seat was reading
    the run list to find.

    The pair is one fixture apart: the same check run inside a suite that
    does belong to a run. A reader that dropped the suite from the run
    list AND from the checks would report an empty answer and look like
    a head nothing ran on.

    The third row is the near miss the first two cannot see. A suite
    whose `workflowRun` is there but carries nothing is a run the reader
    cannot read, and it must be stepped over rather than grouped: a
    reader that only asked whether the field was ABSENT would group it,
    find no run in it, and have nothing to collapse the suite into.
    """
    client, fake = _client(tmp, runs_page([]))
    verdict = {'databaseId': 7, 'name': 'gate freshness',
               'status': 'COMPLETED', 'conclusion': 'FAILURE',
               'completedAt': '2026-09-20T10:10:00Z',
               'detailsUrl': 'https://github.com/o/r/runs/7'}
    fake.write_answers({RUNS_QUERY: runs_page([
        suite(7, workflow=None, check_runs=[verdict])])})
    with fake.activate():
        runs, checks = client.ci_state('o', 'r', SHA)
    assert runs == [], runs
    assert [check['name'] for check in checks] == ['gate freshness'], checks

    empty = suite(7, workflow=11, check_runs=[verdict])
    empty['workflowRun'] = {}
    fake.write_answers({RUNS_QUERY: runs_page([empty])})
    with fake.activate():
        runs, checks = client.ci_state('o', 'r', SHA)
    assert runs == [], runs
    assert [check['name'] for check in checks] == ['gate freshness'], checks

    fake.write_answers({RUNS_QUERY: runs_page([
        suite(7, workflow=11, check_runs=[verdict])])})
    with fake.activate():
        runs, checks = client.ci_state('o', 'r', SHA)
    assert [run['id'] for run in runs] == [7], runs
    assert [check['name'] for check in checks] == ['gate freshness'], checks


def test_a_run_whose_jobs_have_not_started_is_the_first_one_outstanding(
        tmp):
    """A run whose matrix has not reported is a WAIT, and the state it
    reads is the first suite of the run that has not completed - the
    order the API listed them in, which is the order the jobs were
    queued. The second row is the same two suites the other way round,
    because a reader that took the last outstanding state would answer
    both rows the same and neither would be pinning anything.
    """
    client, fake = _client(tmp, runs_page([]))
    for states, expected in ((('QUEUED', 'IN_PROGRESS'), 'queued'),
                             (('IN_PROGRESS', 'QUEUED'), 'in_progress')):
        fake.write_answers({RUNS_QUERY: runs_page([
            suite(1, workflow=11, status=states[0], conclusion=None),
            suite(1, workflow=11, status=states[1], conclusion=None)])})
        with fake.activate():
            runs, _ = client.ci_state('o', 'r', SHA)
        assert [run['status'] for run in runs] == [expected], (
            states, runs)


# ---- the wait a refusal with no instant behind it turns into ----

def test_a_refusal_naming_no_instant_is_the_plain_minute(tmp):
    """The classifier reports a pause with no instant whenever the
    evidence it read carries none, and this is where that pause becomes
    a number: the waiter's own default, exact rather than banded
    because the value IS the answer. The floor and the ceiling are
    driven either side of it, because a reader that clamped a
    no-instant refusal to the floor would answer a throttled query with
    a two-second retry against an API that says nothing about when it
    will answer at all.
    """
    client = _util.load(SKILL / 'gh_client.py', 'gh_client_answers')
    watcher = client.Watcher('ci_wait')
    anonymous = client.RateLimited('gh: API rate limit exceeded', None)
    assert watcher._wait_seconds(anonymous, now=FROZEN) == (
        client.DEFAULT_BACKOFF)
    assert watcher._wait_seconds(anonymous, now=FROZEN) == 60.0

    for resume_at, expected in ((FROZEN + 3600, 3600.0),
                                (FROZEN - 30, client.MIN_BACKOFF),
                                (FROZEN + 10 ** 9, client.MAX_BACKOFF)):
        refusal = client.RateLimited('gh: API rate limit exceeded',
                                     resume_at)
        assert watcher._wait_seconds(refusal, now=FROZEN) == expected, (
            resume_at)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ghclient_')


if __name__ == '__main__':
    raise SystemExit(main())
