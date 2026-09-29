#!/usr/bin/env python3
"""A rate-limit pause is not a poll failure, and the counter must know it.

Both watchers escalate CONSECUTIVE poll failures, and `Watcher.poll` does
its pausing inside the call it wraps, so a pause never raises out of the
loop and never reaches the `failures += 1` beside it. This file pins that,
because the branch widened which answers pause: a throttled 200 used to
arrive as a failed query and escalate, and now it waits instead, so the
counter's behaviour across a pause is a behaviour change with no witness
until there is one.

The control is the escalation line itself. `FAIL_ESCALATE` genuine
failures AFTER a pause still produce it, and at exactly that number - a
pause that advanced the counter would escalate EARLIER, which is the only
way the claim can fail while the escalation still works at all. The row
beside each of these is the negative: the same run with the pause removed
escalates at the same number, so the pause is the only difference.
"""
import sys
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


def _child(script, args, fake):
    return waits.ChildProcess([sys.executable, '-u', str(SKILL / script)]
                              + args, fake.env())


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
        counts = [int(row.split('poll failed (')[1].split(')')[0])
                  for row in child.err.lines if _reports_a_failed_poll(row)]
        assert counts[:ESCALATE] == list(range(1, ESCALATE + 1)), counts
        # The FIRST failed poll is the permission refusal, not the pause.
        # The count alone cannot tell a counted pause from an uncounted
        # one - a pause that reached the counter would still leave five
        # lines and an escalation at five - so what discriminates is
        # WHICH failure came first.
        first = [row for row in child.err.lines
                 if _reports_a_failed_poll(row)][0]
        assert 'not accessible' in first, first
        assert not _reports_pause(first), first
        assert len([row for row in child.out.lines
                    if _reports_pause(row)]) == 1, child.out.lines
    finally:
        child.stop()


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
    """`pr_comment_watch` escalates the same way, so it gets its own
    witness rather than an inference from the sibling's control.
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
    finally:
        child.stop()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='escalation_')


if __name__ == '__main__':
    raise SystemExit(main())
