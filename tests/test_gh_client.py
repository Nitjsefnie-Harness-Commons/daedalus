#!/usr/bin/env python3
"""What `gh_client` calls an answer that is neither data nor a refusal.

`gh_client.py` is the transport every `ci_wait` read goes through, and
its subject is its own: the shape of a `gh` run, the read the pages of
one become, and the wait a refusal with no instant behind it turns
into. The neighbours hold the subjects around it, and this file is the
middle - not the published-verdict predicate,
`test_ci_wait_published.py`'s subject; `ci_state` is here because it
is the client's. Every row drives the REAL loaded module against the
fake `gh` in `_fake_gh.py`, so the answer is a real `gh` process and
only the transport behind it is a double. `RateLimited` is deliberately
not caught anywhere in this file: a pause is the classifier's verdict,
and a row here expecting one would read another suite's subject
through this one's client.
"""
import contextlib
import os
import subprocess
import sys
import threading
import time
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

# The instant the clock is held at, so every wait this file reads back
# is a number, not a band: a band passes on a reader returning half.
FROZEN = 1000.0

# How long a held call is left in the call before its gate is opened
# elsewhere: a ceiling on a hang, not a margin on a result. The one row
# it serves reads a bound of one second, so this fires only for a call
# that is not coming back on its own.
RELEASE_CEILING = 5.0


def _client(tmp, answer, gate=False):
    """The real module, and the fake `gh` it will read one answer from.

    Keyed on the selection, not the whole query: the key is a substring
    of the request, so presence is not exclusivity, and a query that
    dropped the clause finds no fixture and is refused."""
    fake = _fake_gh.FakeGh(tmp, {RUNS_QUERY: answer}, gate=gate)
    return _util.load(SKILL / 'gh_client.py', 'gh_client_answers'), fake


def _answered(client, fake, variables=None, env=None):
    """(data, failure) from one real call, the fake answering it.

    `env` is applied inside the activation, so a row that needs a `gh`
    this tree cannot offer overrides one the activation would otherwise
    put back. Only the failure path is caught: `RateLimited` into
    `failure` would let a paused classifier pass every row."""
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


def _launch_refusal(executable):
    """(class, message) of the refusal this platform makes of a launch.

    Asked of `subprocess` itself, the text belonging to the operating
    system, whose wording differs by platform; comparing keeps this
    file out of that prose and still pins WHICH refusal the client
    reported. The shape is the client's: a payload on stdin."""
    try:
        subprocess.run([executable], input=b'', capture_output=True,
                       timeout=1)
    except OSError as refusal:
        return type(refusal), str(refusal)
    raise AssertionError(f'{executable} was launched')


@contextlib.contextmanager
def _gate_opened_off_thread(fake):
    """Release a hold from a thread the call is not on.

    `_fake_gh` writes its launcher per platform, and the two forms do
    not agree about what the process being killed IS: a POSIX script
    `exec`s, so the launched process is the python reading stdin; a
    `.bat` cannot, so on Windows it is `cmd.exe` and the python is its
    child, whom the bound kills only. Opening the gate here, on a
    thread, is what the finally below could no longer do once the call
    stopped returning; it opens the gate on the way out too, only after
    the ceiling while the call is in it."""
    finished = threading.Event()

    def release():
        finished.wait(RELEASE_CEILING)
        fake.open_gate()

    watchdog = threading.Thread(target=release, daemon=True)
    watchdog.start()
    try:
        yield
    finally:
        finished.set()
        watchdog.join(RELEASE_CEILING)


def _the_data(client, fake):
    """The data one answer delivered, or a failure naming why not."""
    data, failure = _answered(client, fake)
    if data is None:
        raise AssertionError(f'nothing delivered: {failure!r}')
    return data


def _pid_gone(pid, bound=5.0):
    """Bounded linear wait for a pid to leave the process table."""
    deadline = time.monotonic() + bound
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


# ---- the shape of the run itself ----

def test_a_response_with_no_header_block_is_not_an_answer(tmp):
    """The end of the block is what makes the rest of the run an answer,
    and a run that never wrote one - a `gh` killed between its status
    line and its body - is a failure rather than a body.

    This row is on `_parse` itself, forced rather than chosen:
    `_fake_gh` terminates every run it writes with a newline, so no
    fixture of its own can reach this arm. The rows beside it are the
    boundary in both directions; the last is the line-by-line reason
    the parser reads this way at all: a re-translated ending carries a
    `\r` on every line, and a reader that cut at the first blank-line
    byte pair would take the block's last header for the body's first
    line. The whitespace-only separator separates a line that is BLANK
    from one that is merely whitespace - the same line to a caller, not
    to a reader testing `line == ''`: that one walks past it, reads it
    as a header, reaches the end and raises over a `gh` that answered
    in pieces."""
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
    # readers agree, so it cannot tell them apart; this one can.
    doubled = 'HTTP/2.0 200 OK\r\r\nx-ratelimit-remaining: 4999\r\r\n\r\r\n'
    doubled += '{"data": {}}'
    assert client._parse(doubled) == (
        200, {'x-ratelimit-remaining': '4999'}, '{"data": {}}'
    )


def test_a_gh_that_cannot_be_launched_is_a_failure(tmp):
    """The half of the launch that is an operating-system refusal: the
    executable is not there. Neither this nor a hang is a rate limit, so
    neither may become a pause. `DAEDALUS_GH` is the module's own
    override, so this is a real launch failing rather than the transport
    replaced. Three things are pinned, and all three are what a pause
    would take away: the exact failure class, the message - the launch's
    own refusal - and the exception it was raised from, which tells this
    half from the timeout beside it.
    """
    client, fake = _client(tmp, runs_page([]))
    absent = os.path.join(fake.dir, 'gh-that-was-never-installed')
    refusal_type, message = _launch_refusal(absent)
    failure = _failed(client, fake, env={'DAEDALUS_GH': absent})
    assert isinstance(failure, client.QueryError), failure
    assert str(failure) == f'gh failed: {message}', failure
    assert isinstance(failure.__cause__, refusal_type), failure


def test_a_gh_that_never_answers_is_a_failure_too(tmp):
    """The other half of the same `except`, and the reason it is a tuple
    rather than one class: a `gh` that hangs is a `SubprocessError`, not
    an `OSError`, and a reader that caught only the latter let the wait
    die on an exception instead of reporting a failed poll. The bound is
    one second, and the hold makes the timeout real: a fake that
    answered would be a row proving nothing, `entered` the proof the
    call reached it. The cause is compared by class as well as message -
    the rows either side assert causes no single narrower `except`
    could raise, so narrowing it takes one of them red."""
    client, fake = _client(tmp, runs_page([]), gate=True)
    # `setattr`, because the module was executed from a path rather than
    # imported: its names are as dynamic as the module object carries them
    # and a checker reading it as a typed namespace would be reading a
    # file it never saw.
    setattr(client, 'GH_TIMEOUT', 1)
    with _gate_opened_off_thread(fake):
        failure = _failed(client, fake)
    assert fake.entered(), 'the call never reached the hold'
    assert str(failure).startswith('gh failed: '), failure
    assert 'timed out' in str(failure), failure
    assert isinstance(failure.__cause__, subprocess.TimeoutExpired), failure
    if os.name != 'nt':
        # The launcher spelling again: on Windows the timeout kills
        # cmd.exe and the drain waits out the grandchild's handles, so
        # the error arrives only after the release there. Here the kill
        # lands before the error, and the release has not happened.
        pid = fake.calls()[-1]['pid']
        assert _pid_gone(pid), f'the killed gh still runs: {pid}'
        assert not fake.releases(), 'the timeout drained before it killed'


def test_only_the_launch_becomes_a_failure(tmp):
    """The negative space the tuple is drawn around: a caller whose
    variables cannot be written as JSON has a caller's bug, raised
    before the process is ever started, and it stays a `TypeError` out
    in the open - a widened `try` would answer a caller's bug as a `gh`
    that failed."""
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
    the reader's own complaint about where it stopped. The complete
    body is the row's other half - a reader refusing anything that was
    not a perfectly formed object would refuse a real `gh` over a
    truncated pipe, and the message tells the two apart."""
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
    boolean ride beside the list and the string because a reader that
    refused only the two it had seen would pass every other row here.
    The two rows after the loop are a pair rather than one: `[]` and
    `{}` are both answers that carry nothing, and two DIFFERENT
    failures - a list is no response the client can read at all, an
    object one it read whole and found no data in."""
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
    quotes it rather than reporting that data was absent. The cut at 300
    characters is why the quote is asserted by length as well as words -
    a wait prints this line on every refusal, and a server answering
    with a stack trace would otherwise fill the log. The last two rows
    are the near miss in both directions: the empty object IS data, and
    a `data` that is not an object is this same failure rather than
    something handed back to the caller."""
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
    the run list to find. The pair is one fixture apart; the third row
    is the near miss: a suite whose `workflowRun` is empty is stepped
    over, not grouped."""
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
    order the API listed them in. The second row is the same two suites
    the other way round, because a reader that took the last outstanding
    state would answer both rows the same and neither would pin anything.
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
    evidence read carries none, and this is where that pause becomes a
    number: the waiter's own default, exact because the value IS the
    answer. The floor and the ceiling are driven either side: a reader
    that clamped a no-instant refusal to the floor would answer a
    throttled query with a two-second retry."""
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
