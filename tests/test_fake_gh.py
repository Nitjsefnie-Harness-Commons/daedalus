#!/usr/bin/env python3
"""The executable double for `gh`: what it can model, and what it refuses.

A stub must fail on what it does not model. Every case here is one the
double could not express at all before issue 1338 - a 200 that exits 1
over a throttled query, a run whose stdout is empty - and every control
either drives one through a real process or proves the fake refuses by
name rather than answering a plausible empty.

The client-side controls that run ON this double are in
`tests/test_gh_refusal.py`. These stand in for its own contract, which
nothing else in the tree asserts.
"""
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
from _watcher_fixtures import THROTTLED  # noqa: E402
from _watcher_fixtures import throttled_query  # noqa: E402


# A writable answer of every field's own right type, which the loop
# above spoils one field at a time. Written out rather than built by
# comprehension so a change to the answer's shape is visible here.
WRITABLE = {'status': 200, 'headers': {}, 'body': {'data': None},
            'exit': 0, 'stderr': '', 'stdout': ''}


def _run_fake(fake, argv, request=''):
    """One answered call to the fake, as a real process wrote it."""
    return subprocess.run([str(fake.launcher), *argv], input=request,
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=60, env=fake.env())


def test_the_fake_writes_the_live_throttled_answer(tmp):
    """What the widening is for: a 200 that exits 1 with the spent
    headers on stdout and the complaint on stderr, which the previous
    fake could not produce at all - every 200 it wrote exited 0. Read
    from the process rather than from the fixture, so the rendering is
    the thing under test.
    """
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': throttled_query(
        reset_epoch=1700000000, exit=1, stderr=f'gh: {THROTTLED}\n')})
    done = _run_fake(fake, ['api', '-i', 'graphql', '--input', '-'],
                     '{"query":"items(first: 2)"}')
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    # The fake spells CRLF; a text-mode read translates it back, and what
    # this control reads is the shape rather than the line-ending bytes.
    assert done.stdout.startswith('HTTP/2.0 200 OK\n'), repr(done.stdout)
    assert 'X-Ratelimit-Remaining: 0' in done.stdout, done.stdout
    assert 'RATE_LIMIT' in done.stdout, done.stdout
    assert done.stderr == f'gh: {THROTTLED}\n', done.stderr


def test_the_fake_writes_nothing_at_all_for_an_empty_stdout(tmp):
    """The other half: a run whose stdout is empty, which no answer could
    express before. The complaint is all it leaves, and that is the whole
    of the evidence this carrier has.
    """
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {
        'stdout': '', 'exit': 1, 'stderr': f'gh: {THROTTLED}\n'}})
    done = _run_fake(fake, ['api', '-i', 'graphql', '--input', '-'],
                     '{"query":"items(first: 2)"}')
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert done.stdout == '', repr(done.stdout)
    assert done.stderr == f'gh: {THROTTLED}\n', done.stderr


def test_the_fake_refuses_a_status_it_has_no_reason_phrase_for(tmp):
    """A stub must fail on what it does not model. A status line written
    for a status the fake has no phrase for is a plausible empty: it
    parses, and it says nothing true. So the fake refuses by name and
    exits 2 rather than answering.

    This stands in for the fake's own contract - the launcher is what
    every other control in this file runs, and this is the only assertion
    that its refusals are refusals rather than defaults.
    """
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2': {'status': 418,
                                                    'body': {}}})
    done = _run_fake(fake, ['api', '-i', 'graphql', '--input', '-'],
                     '{"query":"items(first: 2)"}')
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert 'does not model' in done.stderr, done.stderr
    assert '418' in done.stderr, done.stderr


def test_the_fake_refuses_a_stdout_it_cannot_place(tmp):
    """The other unmodellable request: a stdout the fake was handed on a
    call that asked for no header block. The renderer that answers those
    calls would drop it, so answering at all would answer something else.
    """
    fake = _fake_gh.FakeGh(tmp, {'pulls/195': {'stdout': '', 'exit': 1}})
    done = _run_fake(fake, ['api', 'pulls/195'])
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert 'header block' in done.stderr, done.stderr


def test_the_fake_refuses_a_field_of_the_wrong_type_by_name(tmp):
    """The five type refusals `_response` makes, one limb each.

    They are the fake's only guard against a fixture that says something
    the renderer cannot honour, and without a control per limb nothing
    would fail if one of them were dropped - the field would be read as
    whatever it happened to be, which is how a double starts answering
    questions it was not asked. Each is refused BY NAME, because a
    refusal nobody can name is a refusal nobody can fix.
    """
    for field, value in (('status', '200'), ('headers', ['X: 1']),
                         ('exit', '1'), ('stderr', ['nope']),
                         ('stdout', ['nope'])):
        with unittest.TestCase().subTest(field=field):
            answer = dict(WRITABLE)
            answer[field] = value
            where = Path(tmp) / field
            fake = _fake_gh.FakeGh(where, {'items(first: 2': answer})
            done = _run_fake(fake, ['api', '-i', 'graphql', '--input', '-'],
                             '{"query":"items(first: 2)"}')
            assert done.returncode == 2, (field, done.returncode, done.stderr)
            assert 'does not model' in done.stderr, (field, done.stderr)
            assert field in done.stderr, (field, done.stderr)
            assert repr(value) in done.stderr, (field, done.stderr)
            assert done.stdout == '', (field, done.stdout)


def test_the_fake_holds_a_call_open_until_its_gate_opens(tmp):
    """A call answered at once is a call a reader can only sample.

    The watcher lifecycle cases decide whether a watcher is still running by
    looking, and looking is a sample: the process can stop in the gap and
    nothing says which side of it the look landed on. That is what made
    `test_the_children_die_with_their_parent` report a red about two
    watchers that were only following their aggregator out. Holding the
    call closes the window in which a watcher exits of its OWN accord
    between the log entry and the reading.

    What it does not do is make the watcher unkillable, and the difference
    matters: `gh_client._exit_at_eof` calls `os._exit(0)` from a daemon
    thread, and `os._exit` ends the process from any thread - including
    the one blocked in `subprocess.run` on the call being held here. So an
    aggregator that dies still takes both watchers out from under the
    reading, which is the signature the original CI cell carried. The
    reading is therefore anchored on the aggregator by the cases
    themselves (`tests/test_watcher_lifecycle.py`), not by this hold, and
    a red there names the aggregator's exit rather than two dead pids.

    The log entry is written BEFORE the hold, which is the whole point: a
    reader counting entries can see a call entered and still open, and that
    is a fact about the process rather than about when anybody looked.
    """
    fake = _fake_gh.FakeGh(tmp, {'items(first: 2)': {'data': None}}, gate=True)
    # The call is made the way a WATCHER makes it, poll marker and all.
    # Nothing the double can observe about a held call at the reading tells
    # it from one answered at once - the record the watcher cases count is
    # written by the same code that decides to wait. What does separate them
    # is a control making the same KIND of call and asserting the hold
    # waited, and that is only this control if its call carries the marker
    # a scoping would key on.
    env = fake.env()
    env[_fake_gh.POLL_MARK] = '1'
    answer = subprocess.Popen(
        [str(fake.launcher), 'api', '-i', 'graphql', '--input', '-'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding='utf-8',
        errors='replace', env=env)
    request_in = answer.stdin
    assert request_in is not None, 'the fake is launched with a stdin pipe'
    # `Popen` types every stream as optional, so the two reads below need
    # the same narrowing this one does.
    answer_out, answer_err = answer.stdout, answer.stderr
    assert answer_out is not None and answer_err is not None, (
        'the fake is launched with its output piped')
    try:
        request_in.write('{"query":"items(first: 2)"}')
        # The close is what tells the fake the request is over - it reads
        # stdin to end of file - so it cannot be dropped, and cannot be
        # followed by `communicate`, which flushes and closes `self.stdin`
        # itself behind a guard that catches BrokenPipeError only. An
        # already-closed handle raises ValueError on 3.11 and 3.12 and is
        # tolerated only on 3.13.
        request_in.close()
        entered = _await_entered(fake, answer, 1)
        assert entered, fake.calls()
        # The wait is observable as UNBALANCED: nothing released while the
        # gate is shut. This half is what catches a hold that was given a
        # bound - a release record is written either way, so the count
        # below cannot tell a hold that waited from one that gave up -
        # and the count below is what catches a hold that does not exist,
        # which leaves this one true for the wrong reason. Neither half
        # alone is a gate; the pair is.
        assert fake.releases() == [], fake.releases()
        assert len(fake.entered()) == 1, fake.entered()
        fake.open_gate()
        answer.wait(timeout=60)
        out, err = answer_out.read(), answer_err.read()
        assert answer.returncode == 0, err
        assert 'data' in out, 'the gate withheld the answer'
    finally:
        answer.kill()
        answer.wait(timeout=60)
    released = fake.releases()
    assert len(released) == 1, f'expected one recorded release: {released}'
    assert released[0]['gate'] == str(fake.gate_path), released


def _await_entered(fake, answer, count):
    """The call log, once it holds `count` entries.

    Guarded on the process that would write them, exactly as
    `waits.await_calls` is: a caller whose fake never logs would otherwise
    spin for the whole job budget and name nothing, and this file's runner
    imposes no per-test timeout to stop it.
    """
    while len(fake.calls()) < count:
        assert answer.poll() is None, (
            f'the fake never logged a call, and it exited '
            f'{answer.returncode}:\n{answer.stderr.read()}')
        time.sleep(0.02)
    return fake.calls()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='fakegh_')


if __name__ == '__main__':
    raise SystemExit(main())
