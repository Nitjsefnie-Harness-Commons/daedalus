#!/usr/bin/env python3
"""What one poll of a watcher costs, measured through the loop that runs it.

A `--once` trial and a loop poll are the same poll asked twice, but only
one of them is a path anybody runs, and the gap between them is where a
regression reads green: the trial returns before the loop body, so a loop
that repeats a request after it is invisible to a figure taken from the
trial, and a fixed-length comparison of the two cannot see it either,
because the extra call extends the aligned prefix instead of breaking it.

Every watcher here is a real process answering from the fake `gh` in
`_fake_gh.py`, and the idle answers are the ones
`test_watcher_budget.py` bounds the watchers against, so a control that
reports two and a bound that refuses two are about the same poll.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
import _watcher_once as once_run  # noqa: E402
from _watcher_fixtures import IDLE_POLL_BOUND  # noqa: E402
from _watcher_fixtures import PR  # noqa: E402
from _watcher_fixtures import TICK  # noqa: E402
from _watcher_fixtures import idle_answers  # noqa: E402

# One more of the very same call, immediately after the one already there.
_IDENTICAL_POLL = """    gh_client.paginate(
        PR_QUERY,
        {'owner': owner, 'name': name, 'number': int(pr),
         'reviewCursor': None, 'talkCursor': None},
        CONNECTIONS)
"""
_PULL_PAGE = '    found = gh_client.at(pages[0], PULL)\n'
# The loop body's own tail, below the poll: the request it already made,
# asked a second time, inside the same poll. The `--once` trial returns
# before this point, so a trial figure cannot see it at all.
_LOOP_TAIL = ('            failures = 0\n'
              '        except Exception as exc:                      '
              '# noqa: BLE001\n')
_WATCHER_BUILT = "    watcher = gh_client.Watcher(f'PR {args.pr} watcher')\n"


def _repeat(indent):
    """The poll's own request asked again, at the loop body's indentation."""
    pad = ' ' * indent
    return (f"{pad}owner, name = args.repo.split('/', 1)\n"
            f'{pad}gh_client.paginate(\n'
            f'{pad}    PR_QUERY,\n'
            f"{pad}    {{'owner': owner, 'name': name, "
            f"'number': int(args.pr),\n"
            f"{pad}     'reviewCursor': None, 'talkCursor': None}},\n"
            f'{pad}    CONNECTIONS)\n')


# The extra request from the third poll on, indented inside its own guard.
_LATER_POLL = ('            grown += 1\n'
               '            if grown > 2:\n' + _repeat(16))


def test_a_loop_poll_asking_the_same_question_twice_costs_two(tmp):
    """The doubled query, where production runs it: inside the loop.

    `test_watcher_budget.py` proves the measure counts two calls in one
    `--once` poll; this proves the loop figure does too, and the loop
    figure is the one the idle bounds judge.
    """
    here = Path(tmp) / 'doubled'
    here.mkdir(parents=True, exist_ok=True)
    script = once_run.planted(here, 'pr_comment_watch.py',
                              (_PULL_PAGE, _IDENTICAL_POLL))
    fake = _fake_gh.FakeGh(here, idle_answers())
    per_poll, seen = once_run.measure(script, [PR], fake, TICK)
    print(f'\n  a loop poll asking twice: {per_poll} call(s) per poll, from '
          f'{len(seen)} logged call(s)')
    assert per_poll == 2, (per_poll, [call['request'][:80] for call in seen])
    assert per_poll > IDLE_POLL_BOUND, (per_poll, IDLE_POLL_BOUND)


def test_a_loop_that_repeats_its_last_request_costs_two(tmp):
    """#1215: the loop asking twice where a trial cannot follow it.

    The repeat is spliced into the loop body, below the poll and above the
    state a `--once` trial returns from, so the trial log stays one call
    long and every poll the loop runs costs two. A figure read from the
    trial - or from a fixed-length comparison of the two - reports 1 for a
    watcher spending two, which is the understatement this pins shut.
    """
    here = Path(tmp) / 'repeated'
    here.mkdir(parents=True, exist_ok=True)
    script = once_run.planted(here, 'pr_comment_watch.py',
                              (_LOOP_TAIL, _repeat(12)))
    fake = _fake_gh.FakeGh(here, idle_answers())
    per_poll, seen = once_run.measure(script, [PR], fake, TICK)
    ran = once_run.trial(script, [PR, '--interval', str(TICK)], fake)
    print(f'\n  a loop repeating its last request: {per_poll} call(s) per '
          f'poll, from {len(seen)} logged call(s), and a trial that sees '
          f'{len(ran)}')
    assert len(ran) == 1, [call['request'][:80] for call in ran]
    assert per_poll == 2, (per_poll, [call['request'][:80] for call in seen])
    assert per_poll > IDLE_POLL_BOUND, (per_poll, IDLE_POLL_BOUND)


# One call, appended by a process of its own, in the shape the fake logs: a
# `gh` child the cancellation did not reach writes to the log after the tree
# it belonged to is gone. Only `request` is read back; the other fields are
# what a logged call looks like, not what a real child carries. The request
# travels in the environment rather than in the program text or an argument,
# because one of the shapes is a query with newlines in it and the suites
# run on Windows.
_LATE_ENV = 'DAEDALUS_FAKE_LATE_REQUEST'
_LATE_REQUEST = 'query WatchPull { viewer }'
_LATE_CALL = (
    'import json, os, sys\n'
    'entry = {"t": 0.0, "argv": ["api", "-i"], "fragment": None,\n'
    '          "poll": None, "request": os.environ[sys.argv[2]]}\n'
    'with open(sys.argv[1], "a", encoding="utf-8") as handle:\n'
    '    handle.write(json.dumps(entry) + "\\n")\n'
)


def _late_append(log, request):
    """One real process, appending one call to the log it is handed."""
    done = subprocess.run(
        [sys.executable, '-c', _LATE_CALL, str(log), _LATE_ENV],
        env=dict(os.environ, **{_LATE_ENV: request}),
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (done.returncode, done.stderr)


def _entries_at(log):
    """Every call one log holds; a torn last line is ignored.

    The `ValueError` arm is what a survivor reaches: one that outlived
    the cancellation is still appending to the log this reads, so its
    last line can be half written. Read off the path rather than off the
    fake, because the fake is no longer the thing naming that log.
    `_fake_gh.py`'s `_entries` is the same walk; it is private to another
    branch's file, and a control reaching into it would depend on that
    branch's internals rather than on what the fake logs.
    """
    if not log.exists():
        return []
    found = []
    for line in log.read_text(encoding='utf-8').splitlines():
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found


def _requests_at(log):
    """The requests one call log holds, in the order they were made."""
    return [call['request'] for call in _entries_at(log)]


def _measured_log(directory):
    """The log one measurement wrote, found by the marker only it publishes.

    A loop publishes a poll index its `gh` children inherit; a `--once`
    trial publishes none and the stand-in plants none. So the log holding
    marked entries is the measurement's, whichever end re-point is in
    place and whatever the harness happens to name its logs - which is
    what keeps this control modelling the bug rather than the fix's
    spelling of it.
    """
    marked = [path for path in sorted(Path(directory).glob('*.jsonl'))
              if any(call.get('poll') for call in _entries_at(path))]
    assert len(marked) == 1, sorted(p.name for p in Path(directory).iterdir())
    return marked[0]


def _trial_query(directory, script):
    """The query one real trial makes, read off that trial's own log.

    Its own fake, in its own directory: reading the subject's real call off
    a real trial must not be a second subject writing to the log the case
    is measuring.
    """
    here = Path(directory)
    here.mkdir(parents=True, exist_ok=True)
    fake = _fake_gh.FakeGh(here, idle_answers())
    ran = once_run.trial(script, [PR, '--interval', str(TICK)], fake)
    assert len(ran) == 1, [call['request'][:60] for call in ran]
    return ran[0]['request']


def _survivor_row(root, script, name, request):
    """One survivor shape, from the cancelled log to the trial's figure.

    Reported rather than asserted, so every shape runs before anything is
    judged: a sweep that stops on its first failing row cannot say which
    of the rest were clean, and on a broken tree that is every one of them.

    The measurement's own log is emptied the way the pre-fix suite emptied
    it, so what a trial sharing it reads is this one entry and its own -
    the two entries both CI sightings carried, which is what makes each
    shape reproduce its own signature rather than a longer log's.
    """
    here = Path(root) / name
    here.mkdir(parents=True, exist_ok=True)
    fake = _fake_gh.FakeGh(here, idle_answers())
    once_run.measure(script, [PR], fake, TICK)
    measured_log = _measured_log(here)
    measured_log.write_text('', encoding='utf-8')
    _late_append(measured_log, request)
    held = _requests_at(measured_log)
    ran = once_run.trial(script, [PR, '--interval', str(TICK)], fake)
    return {'name': name, 'planted': request in held, 'held': len(held),
            'seen': [call['request'] for call in ran],
            'separate': fake.log != measured_log}


def _summarise(row):
    return (f'{row["name"]}: the trial sees {len(row["seen"])} call(s) of its '
            f'own, the cancelled log holds {row["held"]}')


def test_a_trial_ignores_a_call_appended_after_the_measurement(tmp):
    """#1256: whatever a survivor wrote, the trial's figure is its own.

    `measure` cancels the watcher it started, and a `gh` child the
    cancellation did not reach keeps running: it appends to the log the
    measurement was reading, after `measure` has returned and before the
    trial reads - the window in which a shared log let a survivor into
    the figure the trial reports. The stand-in is a real process, joined
    before the trial starts, so the append is ordered rather than timed.

    Every shape the bug arrived in is driven, because the fix separates
    FILES and the claim is that content does not matter. `_fake_gh` logs
    `sys.stdin.read()`, so a survivor that read its GraphQL payload logs
    the query - a full duplicate of the trial's own, the wide window and
    the signature `suites (windows-latest, 3.13)` carried in run
    36336201676 - and one killed before that write logs an empty string,
    the narrow window `suites (macos-latest, 3.14)` carried in run
    36318252784. The unrelated query is driven too, because a stand-in
    that is not a copy of the subject's own call is the stronger control.

    `len(ran) == 1` is the line that carries the property, given the
    stand-in's entry is on the old log; the last line states that the
    two paths differ.
    """
    here = Path(tmp) / 'shapes'
    here.mkdir(parents=True, exist_ok=True)
    script = once_run.planted(here, 'pr_comment_watch.py',
                              (_LOOP_TAIL, _repeat(12)))
    duplicate = _trial_query(here / 'capture', script)
    rows = [_survivor_row(here, script, name, request)
            for name, request in (('wide', duplicate), ('narrow', ''),
                                  ('unrelated', _LATE_REQUEST))]
    print('\n  a trial beside a late append - '
          + '; '.join(map(_summarise, rows)))
    assert all(row['planted'] for row in rows), [
        (row['name'], row['planted'], row['held']) for row in rows]
    assert all(len(row['seen']) == 1 for row in rows), [
        (row['name'], [call[:80] for call in row['seen']]) for row in rows]
    assert all(row['separate'] for row in rows), [
        (row['name'], row['separate']) for row in rows]


def test_two_measurements_on_one_fake_hand_out_different_logs(tmp):
    """Two measurements on one fake hand out two different logs.

    Two measurements on one fake that handed out the same path would put
    the first measurement's calls in the second subject's log, which is
    the defect this suite exists to keep out. The case measures twice on
    one fake, because one measurement is satisfied by any path at all,
    including the one `itertools.repeat(0)` hands out.
    """
    here = Path(tmp) / 'twice'
    here.mkdir(parents=True, exist_ok=True)
    fake = _fake_gh.FakeGh(here, idle_answers())
    script = once_run.SKILL / 'pr_comment_watch.py'
    first = fake.log
    once_run.measure(script, [PR], fake, TICK)
    second = fake.log
    once_run.measure(script, [PR], fake, TICK)
    third = fake.log
    print(f'\n  two measurements on one fake: {first.name}, {second.name}, '
          f'{third.name}')
    assert second != first, (first, second)
    assert third != second, (second, third)


def test_a_measurement_never_writes_the_log_two_fakes_share(tmp):
    """The path a second `FakeGh` over this directory would also name.

    Every `FakeGh.__init__` sets `<dir>/calls.jsonl` and truncates it, so
    two of them over one directory name one path: a measurement writing
    there would hand its own figure to whichever instance ran last. So
    the measurement takes a fresh path before the loop starts, and the
    shared one is never written - which an instance that never measures
    cannot collide with, because nothing is left there to collide over.
    """
    here = Path(tmp) / 'shared'
    here.mkdir(parents=True, exist_ok=True)
    fake = _fake_gh.FakeGh(here, idle_answers())
    _fake_gh.FakeGh(here, idle_answers())
    script = once_run.SKILL / 'pr_comment_watch.py'
    once_run.measure(script, [PR], fake, TICK)
    shared = _entries_at(here / 'calls.jsonl')
    assert not shared, [call['request'][:60] for call in shared]
    assert fake.log != here / 'calls.jsonl', (fake.log, here)


def test_a_trial_of_a_watcher_that_names_its_boundary_is_refused(tmp):
    """A one-poll loop is not a measure of the loop, and says so.

    The idle bounds are judged over the loop because a loop repeats its
    body and a `--once` invocation of the same script runs one poll. So
    the trial measure refuses a script that names its own poll boundary
    rather than answering in `measure`'s place with a figure that passes
    a bound the loop would fail.
    """
    here = Path(tmp) / 'refused'
    here.mkdir(parents=True, exist_ok=True)
    script = once_run.planted(here, 'pr_comment_watch.py')
    fake = _fake_gh.FakeGh(here, idle_answers())
    refused = None
    try:
        once_run.once(script, [PR, '--interval', str(TICK)], fake)
    except AssertionError as exc:
        refused = exc.args[0]
    assert refused is not None, (
        'a one-poll trial answered as a measure of the loop')
    assert 'measure()' in refused[0], refused


def test_a_loop_that_grows_from_its_third_poll_costs_two(tmp):
    """A poll that starts costing more is measured at its grown size.

    The first two polls spend the budgeted request and the third spends one
    more, so a figure read from the first poll passes the bound for a
    watcher spending twice as much. The bound holds for every poll in the
    window, which is what makes the grouping worth doing.
    """
    here = Path(tmp) / 'growing'
    here.mkdir(parents=True, exist_ok=True)
    script = once_run.planted(
        here, 'pr_comment_watch.py',
        (_WATCHER_BUILT, _WATCHER_BUILT + '    grown = 0\n'),
        (_LOOP_TAIL, _LATER_POLL))
    fake = _fake_gh.FakeGh(here, idle_answers())
    per_poll, seen = once_run.measure(script, [PR], fake, TICK)
    print(f'\n  a loop growing at its third poll: {per_poll} call(s) per '
          f'poll, from {len(seen)} logged call(s)')
    assert per_poll == 2, (per_poll, [call['request'][:80] for call in seen])
    assert per_poll > IDLE_POLL_BOUND, (per_poll, IDLE_POLL_BOUND)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchloop_')


if __name__ == '__main__':
    raise SystemExit(main())
