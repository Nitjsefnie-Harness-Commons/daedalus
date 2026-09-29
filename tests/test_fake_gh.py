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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='fakegh_')


if __name__ == '__main__':
    raise SystemExit(main())
