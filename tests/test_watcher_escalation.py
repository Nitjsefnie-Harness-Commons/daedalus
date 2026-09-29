#!/usr/bin/env python3
"""A rate-limit pause is not a poll failure, and the counter must know it.

Both watchers escalate CONSECUTIVE poll failures, and `Watcher.poll` does
its pausing inside the call it wraps, so a pause never raises out of the
loop and never reaches the `failures += 1` beside it. This file pins that,
because the branch widened which answers pause: a throttled 200 used to
arrive as a failed query and escalate, and now it waits instead, so the
counter's behaviour across a pause is a behaviour change with no witness
until there is one.

The escalation LINE cannot tell a counted pause from an uncounted one -
a pause that reached the counter is still answer #1, and the escalation
still fires at the same threshold - so each row says WHICH failure came
first. The CI row has its negative beside it, the same run without the
pause, so the pause is the only difference between them; the comment
watcher does not, because its own negative is that shape with a
different query and repeating it would measure the fixture.
"""
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
import _watcher_waits as waits  # noqa: E402
from _watcher_fixtures import idle_answers  # noqa: E402
from _watcher_fixtures import permission_403  # noqa: E402
from _watcher_fixtures import refusal_response  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'
BRANCH = 'issue-997'
PR = '195'
# Read out of the module rather than written here: a control carrying its
# own copy of the threshold passes against a threshold that moved.
ESCALATE = int([line.split('=')[1] for line in
                (SKILL / 'ci_watch.py').read_text(encoding='utf-8')
                .splitlines() if line.startswith('FAIL_ESCALATE')][0])


def _reports_pause(line):
    return 'rate limit' in line


def _reports_a_failed_poll(line):
    return 'poll failed (' in line


def _reports_the_escalation(line):
    return 'consecutive failures' in line


# The bound a stderr wait gives up after: the figure `await_gone` uses.
BACKSTOP = 90


class _SlowStream(waits.Stream):
    """A stderr drain that has not caught up when the reader looks.

    The two streams are pumped on two INDEPENDENT threads, so how far the
    stderr drain has got when a line arrives on stdout is a property of
    the machine and not of the code under test - Linux keeps pace, and a
    loaded Windows cell does not. This holds the FIRST line past the
    moment the escalation reaches stdout, and lets every later line
    through at the drain's own speed.

    The lag must outlive the CHILD's run, not merely be long: the
    escalation is printed after `FAIL_ESCALATE` polls, so a lag shorter
    than that is a drain that keeps pace and never a drain that is
    behind. A quarter of a second per line, and then eight seconds, both
    passed an unsynchronised read here for exactly that reason. The cost
    is about fifteen seconds a run, and it buys a control that can tell
    the two reads apart.
    """

    LAG = 15

    def pump(self, pipe):
        for index, line in enumerate(pipe):
            if index == 0:
                time.sleep(self.LAG)
            self.publish(line.rstrip('\n'))
        with self.changed:
            self.ended = True
            self.changed.notify_all()


def _child(script, args, fake):
    argv = [sys.executable, '-u', str(SKILL / script)] + list(args)
    return waits.ChildProcess(argv, fake.env())


def _lagging_child(script, args, fake):
    """A child whose STDERR DRAIN falls behind, and stdout's does not.

    Its own launch, and that duplication is the point: the harness's
    `ChildProcess` starts a stderr pump in its constructor, so swapping
    the Stream afterwards puts a SECOND reader on one pipe and the two
    split the lines between them - a slower drain that is not slower.
    """
    argv = [sys.executable, '-u', str(SKILL / script)] + list(args)
    proc = subprocess.Popen(
        argv, env=fake.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace', start_new_session=True,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                       if sys.platform.startswith('win') else 0))
    out, err = waits.Stream(), _SlowStream()
    for pipe, sink in ((proc.stdout, out), (proc.stderr, err)):
        threading.Thread(target=sink.pump, args=(pipe,),
                         daemon=True).start()
    return proc, out, err


def _await_failed_polls(err, count, what):
    """The first `count` failed-poll lines, read on STDERR's own condition.

    The lines are written to stderr and the escalation to stdout, on two
    independent drains, so a line read off stdout says nothing about what
    the stderr drain has caught up to. This waits for the state itself,
    under the same lock `await_lines` uses, and gives up after `BACKSTOP`.

    The bound is not a wall-clock margin: nothing here asserts that the
    child was quick, only that it reached a state, and the state a live
    child reaches in a few polls. It exists so a child that never
    reaches it fails the suite instead of holding it open - the one thing
    `await_lines` deliberately does not do, and the one thing a test
    about TWO streams cannot afford not to do.
    """
    deadline = time.monotonic() + BACKSTOP
    while True:
        with err.changed:
            found = [row for row in err.lines if _reports_a_failed_poll(row)]
            if len(found) >= count:
                return found
        if time.monotonic() >= deadline:
            raise AssertionError(
                f'{what}: stderr never carried {count} failed polls:\n'
                + '\n'.join(err.lines))
        time.sleep(waits.POLL)


def _escalation_line(child):
    return [row for row in child.out.lines
            if _reports_the_escalation(row)][0]


def test_a_ci_pause_does_not_advance_the_failure_counter(tmp):
    """`ci_watch`: one pause, then the failures it still takes to escalate."""
    answers = dict(idle_answers())
    pause = refusal_response(429, {'Retry-After': '1'})
    answers['statusCheckRollup'] = (
        [pause] + [permission_403()] * ESCALATE)
    fake = _fake_gh.FakeGh(tmp, answers)
    child = _child('ci_watch.py',
                   [BRANCH, '--interval', '1', '--debounce', '0'], fake)
    try:
        waits.await_lines(child.out, _reports_pause, 1, 'the pause line')
        waits.await_lines(child.out, _reports_the_escalation, 1,
                          'the escalation line')
        line = _escalation_line(child)
        assert f'after {ESCALATE} consecutive failures' in line, line
        # WHICH failure came first is what discriminates, and the COUNT
        # of them is not - see this file's docstring. The read is on
        # stderr because the escalation waited for above is a STDOUT
        # line, and the two drains are independent.
        failed = _await_failed_polls(child.err, 1, 'the first failed poll')
        first = failed[0]
        assert 'not accessible' in first, first
        assert 'rate limit' not in first.lower(), first
        assert len([row for row in child.out.lines
                    if _reports_pause(row)]) == 1, child.out.lines
    finally:
        child.stop()


def test_the_pause_row_holds_when_the_stderr_drain_falls_behind(tmp):
    """The race, made repeatable, by driving the CI row through a drain
    that has not caught up. It asserts both that an unsynchronised read
    of that drain finds nothing and that the row still holds, so the
    wait is what holds.

    It is a control for the CONTROL, which is the only kind that can
    witness a timing change: without it, "wait on the right stream" is
    a change nobody can tell from the one before it.
    """
    answers = dict(idle_answers())
    pause = refusal_response(429, {'Retry-After': '1'})
    answers['statusCheckRollup'] = ([pause]
                                    + [permission_403()] * ESCALATE)
    fake = _fake_gh.FakeGh(tmp, answers)
    proc, out, err = _lagging_child(
        'ci_watch.py', [BRANCH, '--interval', '1', '--debounce', '0'], fake)
    try:
        waits.await_lines(out, _reports_pause, 1, 'the pause line')
        waits.await_lines(out, _reports_the_escalation, 1,
                          'the escalation line')
        # The escalation is a STDOUT line and the drain is still holding
        # the first stderr line, which is the whole point: an
        # unsynchronised read of `err.lines` here finds nothing. The row
        # waits for the state, so it holds either way.
        assert err.lines == [], err.lines
        first = _await_failed_polls(err, 1, 'the first failed poll')[0]
        assert 'not accessible' in first, first
        assert 'rate limit' not in first.lower(), first
    finally:
        waits._cancel(proc)
        proc.wait(timeout=60)


def test_the_same_ci_run_without_a_pause_escalates_at_the_same_number(tmp):
    """The negative, and the reason the row above can conclude anything."""
    answers = dict(idle_answers())
    answers['statusCheckRollup'] = [permission_403()] * ESCALATE
    fake = _fake_gh.FakeGh(tmp, answers)
    child = _child('ci_watch.py',
                   [BRANCH, '--interval', '1', '--debounce', '0'], fake)
    try:
        waits.await_lines(child.out, _reports_the_escalation, 1,
                          'the escalation line')
        line = _escalation_line(child)
        assert f'after {ESCALATE} consecutive failures' in line, line
    finally:
        child.stop()


def test_a_comment_pause_does_not_advance_the_failure_counter(tmp):
    """`pr_comment_watch` escalates the same way and gets its own
    witness. It carries the assertion this file is about - the FIRST
    failed poll is the permission refusal, not the pause - which it did
    not have, and without it this row passed against a plant that let
    the pause reach the counter.
    """
    answers = dict(idle_answers())
    pause = refusal_response(429, {'Retry-After': '1'})
    answers['reviews(first: 100'] = (
        [pause] + [permission_403()] * ESCALATE)
    fake = _fake_gh.FakeGh(tmp, answers)
    child = _child('pr_comment_watch.py', [PR, '--interval', '1'], fake)
    try:
        waits.await_lines(child.out, _reports_pause, 1, 'the pause line')
        waits.await_lines(child.out, _reports_the_escalation, 1,
                          'the escalation line')
        line = _escalation_line(child)
        assert f'after {ESCALATE} consecutive failures' in line, line
        failed = _await_failed_polls(child.err, 1, 'the first failed poll')
        first = failed[0]
        assert 'not accessible' in first, first
        assert 'rate limit' not in first.lower(), first
    finally:
        child.stop()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='escalation_')


if __name__ == '__main__':
    raise SystemExit(main())
