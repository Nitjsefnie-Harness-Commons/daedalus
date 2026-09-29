#!/usr/bin/env python3
"""What an idle watched pull request costs, and what a refusal does.

Every watcher here is a real process answering from the fake `gh` in
`_fake_gh.py`, so the numbers are the requests the scripts actually make.

What these watchers cost BEFORE this branch is measured in
`tests/test_watcher_budget_base.py`. The two halves are one method rather
than two because they answer from the same fake `gh`, are measured by the
same `tests/_watcher_once.py`, and are pinned to the same `BASE` in
`tests/_watcher_fixtures.py`.
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
import _watcher_once as once_run  # noqa: E402
import _watcher_waits as waits  # noqa: E402
from _processtree import process_is_gone  # noqa: E402
from _watcher_fixtures import BRANCH  # noqa: E402
from _watcher_fixtures import IDLE_POLL_BOUND  # noqa: E402
from _watcher_fixtures import PR  # noqa: E402
from _watcher_fixtures import SHA  # noqa: E402
from _watcher_fixtures import STAMP  # noqa: E402
from _watcher_fixtures import TICK  # noqa: E402
from _watcher_fixtures import check  # noqa: E402
from _watcher_fixtures import ci_page  # noqa: E402
from _watcher_fixtures import comment  # noqa: E402
from _watcher_fixtures import idle_answers  # noqa: E402
from _watcher_fixtures import pr_page  # noqa: E402
from _watcher_fixtures import published  # noqa: E402
from _watcher_fixtures import rate_limited_error  # noqa: E402
from _watcher_fixtures import refusal_response  # noqa: E402
from _watcher_fixtures import review  # noqa: E402
from _watcher_fixtures import runs_page  # noqa: E402
from _watcher_fixtures import RUNS_QUERY  # noqa: E402
from _watcher_fixtures import suite  # noqa: E402
from _watcher_fixtures import throttled_query  # noqa: E402
from _watcher_fixtures import THROTTLED  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'


def _announces_pid(line):
    return 'watcher pid' in line


def _reports_rate_limit(line):
    return 'rate limit' in line


def _announces_review(line):
    return ' review from ' in line


def _reports_state(line):
    return 'state: open' in line


def _announces_failure(line):
    return 'pyright: failure' in line


def _reports_a_failed_poll(line):
    return 'poll failed' in line


class _Child(waits.ChildProcess):
    """A child the budget suite starts from an argv and an environment."""

    def __init__(self, argv, env):
        super().__init__(argv, _util.child_coverage('scrub',
                                                    environment=env))


def _watcher(name, args, fake):
    return _Child([sys.executable, '-u', str(SKILL / name)] + args,
                  fake.env())


def _watcher_on_a_pinned_clock(name, args, fake, now):
    """A watcher child whose `time.time` reads `now` from its first line.

    Patching the `time` module object itself is what makes it one reading:
    the `gh_client` the script imports shares it, so the reset the refusal
    carries and the instant the pause reads cannot disagree. `sleep` and
    `monotonic` stay real, so the pause is a real sleep.
    """
    script = SKILL / name
    preamble = ('import runpy, sys, time;'
                f' time.time = lambda: {now!r};'
                f' sys.argv = [{str(script)!r}] + {list(args)!r};'
                f' runpy.run_path({str(script)!r}, run_name="__main__")')
    return _Child([sys.executable, '-u', '-c', preamble], fake.env())


def _await_calls(fake, count, child):
    return waits.await_calls(fake, count, child, f'{count} gh call(s)')


def _hourly(per_poll, tick):
    return per_poll * (3600 / tick)


def _windows_pid_alive(pid):
    """`is alive` on the Windows leg, read from the shared receipt.

    The receipt answers IS GONE and this is asked IS ALIVE, at five call
    sites, so the answer is its NEGATION — a name ending `_is_gone` called
    from one ending `_is_alive` is the cheapest tell there is. The branch is
    behind a `sys.platform` check no run here crosses, so the control below
    forces it rather than trusting a whole-suite green.
    """
    return not process_is_gone(pid, 0)


def _pid_alive(pid):
    """Whether a pid still names a process, on any platform CI runs.

    The Windows half reads through `tests/_processtree.py`'s receipt rather
    than repeating it — a second copy of the question is how the two drifted
    apart in the first place.
    """
    if sys.platform.startswith('win'):
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = Path(f'/proc/{pid}/stat')
    if not state.exists():
        return True          # a POSIX host with no procfs to consult
    try:
        text = state.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False         # it exited between the two checks
    # An orphan nobody has reaped yet is a corpse, not a survivor.
    return text.rsplit(')', 1)[-1].split()[0] != 'Z'


def test_an_idle_comment_watch_costs_one_query_per_tick(tmp):
    fake = _fake_gh.FakeGh(tmp, idle_answers())
    per_poll, seen = once_run.measure(
        SKILL / 'pr_comment_watch.py', [PR], fake, TICK)
    print(f'\n  comment watcher: {per_poll} call(s) per poll, '
          f'{_hourly(per_poll, TICK):.0f}/hour at a {TICK}s tick, '
          f'from {len(seen)} logged call(s)')
    assert per_poll <= IDLE_POLL_BOUND, (
        per_poll, [call['request'][:80] for call in seen])


def test_an_idle_ci_watch_costs_one_query_per_tick(tmp):
    fake = _fake_gh.FakeGh(tmp, idle_answers())
    per_poll, seen = once_run.measure(
        SKILL / 'ci_watch.py', [BRANCH], fake, TICK)
    print(f'\n  CI watcher: {per_poll} call(s) per poll, '
          f'{_hourly(per_poll, TICK):.0f}/hour at a {TICK}s tick, '
          f'from {len(seen)} logged call(s)')
    assert per_poll <= IDLE_POLL_BOUND, (
        per_poll, [call['request'][:80] for call in seen])


# One more of the very same call, immediately after the one already there.
_IDENTICAL_POLL = """    gh_client.paginate(
        PR_QUERY,
        {'owner': owner, 'name': name, 'number': int(pr),
         'reviewCursor': None, 'talkCursor': None},
        CONNECTIONS)
"""
_PULL_PAGE = '    found = gh_client.at(pages[0], PULL)\n'


def test_a_poll_asking_the_same_question_twice_costs_two(tmp):
    """The doubled query the base measure caught and this one could not.

    One poll of two identical calls and two polls of one call each record
    the same sequence, so a figure read off the sequence answers 1 for both.
    The copy is the tracked comment watcher with one more of the same
    paginate spliced in, run through a single `--once` trial - the path that
    counts one poll as one process.
    """
    here = Path(tmp) / 'doubled'
    here.mkdir(parents=True, exist_ok=True)
    script = once_run.planted(here, 'pr_comment_watch.py',
                              (_PULL_PAGE, _IDENTICAL_POLL))
    fake = _fake_gh.FakeGh(here, idle_answers())
    seen = once_run.trial(script, [PR, '--interval', str(TICK)], fake)
    print(f'\n  a trial poll asking twice: {len(seen)} call(s) per poll, '
          f'from {len(seen)} logged call(s)')
    assert len(seen) == 2, [call['request'][:80] for call in seen]
    assert len(seen) > IDLE_POLL_BOUND, (len(seen), IDLE_POLL_BOUND)


_DIES_MID_POLL = '        for kind in KINDS:\n'
_DEATH = '        raise SystemExit(1)\n'


def test_a_trial_that_dies_part_way_through_is_not_counted(tmp):
    """A nonzero exit is refused, so a dead trial reports no calls at all.

    The trial logged its one call and then died before the poll finished,
    so counting it would report a request the trial never finished asking.
    The refusal is the returncode in the failure, not a silent zero.
    """
    here = Path(tmp) / 'dead'
    here.mkdir(parents=True, exist_ok=True)
    script = once_run.planted(here, 'pr_comment_watch.py',
                              (_DIES_MID_POLL, _DEATH))
    fake = _fake_gh.FakeGh(here, idle_answers())
    refused = None
    try:
        once_run.trial(script, [PR, '--interval', str(TICK)], fake)
    except AssertionError as exc:
        refused = exc.args[0]
    assert refused is not None, 'a trial that exited 1 was counted'
    assert refused[0] == 1, refused
    assert len(fake.calls()) == 1, [call['request'][:60]
                                    for call in fake.calls()]


def test_the_children_die_with_their_parent(tmp):
    fake = _fake_gh.FakeGh(tmp, idle_answers())
    parent = _Child([sys.executable, '-u', str(SKILL / 'watch_all.py'),
                     PR, BRANCH, '--log', str(Path(tmp) / 'watch.log'),
                     '--debounce', '1', '--max-hold', '5'], fake.env())
    try:
        waits.await_lines(parent.err, _announces_pid, 2,
                          'both children to announce their pid')
        pids = [int(line.rsplit(' ', 1)[-1]) for line in parent.err.lines
                if _announces_pid(line)]
        _await_calls(fake, 2, parent)
        assert all(_pid_alive(pid) for pid in pids), pids
        parent.proc.kill()
        parent.proc.wait(timeout=60)
        waits.await_gone(pids, parent, f'children {pids} to die with the '
                         f'parent', _pid_alive)
        assert not any(_pid_alive(pid) for pid in pids), pids
    finally:
        parent.stop()


def test_a_refused_comment_poll_pauses_until_the_reset_and_resumes(tmp):
    # One reading supplies both the instant the child is pinned to and the
    # reset the fixture names, so the six between them is a value chosen here.
    now = int(time.time())
    reset = now + 6
    answers = dict(idle_answers())
    answers['reviews(first: 100'] = [
        refusal_response(headers={'X-RateLimit-Reset': str(reset)}),
        pr_page(reviews=[review(1)], conversation=[comment(2)])]
    fake = _fake_gh.FakeGh(tmp, answers)
    child = _watcher_on_a_pinned_clock(
        'pr_comment_watch.py', [PR, '--interval', '5'], fake, now)
    try:
        waits.await_lines(child.out, _reports_rate_limit, 1,
                          'the pause line naming the reset')
        stamp = datetime.fromtimestamp(reset, timezone.utc).strftime(STAMP)
        pause = [line for line in child.out.lines
                 if _reports_rate_limit(line)][0]
        assert stamp in pause, (stamp, pause)
        assert 'waiting 6s' in pause, pause
        waits.await_lines(child.out, _reports_state, 1,
                          'the resumed poll to report what it found')
        # The one give-up the watcher announces rather than swallows; the
        # indexes below are what tell a resume from a give-up.
        assert not [line for line in child.err.lines
                    if _reports_a_failed_poll(line)], child.err.lines
        calls = fake.calls()
        assert len(calls) == 2, [call['request'][:60] for call in calls]
        assert calls[1]['t'] >= reset, (calls[1]['t'], reset)
        # Each query carries the index of the poll that issued it. The pause
        # reissues inside that one poll; a poll that returns, breaks, falls
        # off the end or ends the process ends the poll, and the next
        # iteration publishes the next index. No wall clock is read, so this
        # holds however long either took - and since both values come from
        # the same poll it says which poll issued the query, not that the
        # index advances; that is tests/test_watcher_poll_index.py. The
        # presence check is the other failure, an unwired seam rather than
        # a frozen index.
        assert calls[0]['poll'] is not None, calls[0]
        assert calls[1]['poll'] == calls[0]['poll'], [c['poll'] for c in calls]
        # One line for the whole wait, not one per poll inside it.
        assert len([line for line in child.out.lines
                    if _reports_rate_limit(line)]) == 1
    finally:
        child.stop()


def test_a_refused_ci_poll_pauses_on_a_retry_after(tmp):
    before = time.time()
    answers = dict(idle_answers())
    answers['statusCheckRollup'] = [
        refusal_response(429, {'Retry-After': '4'}),
        ci_page([check(1, 'pyright', 'FAILURE')])]
    fake = _fake_gh.FakeGh(tmp, answers)
    child = _watcher('ci_watch.py',
                     [BRANCH, '--interval', '5', '--debounce', '0'], fake)
    try:
        waits.await_lines(child.out, _reports_rate_limit, 1,
                          'the CI pause line')
        waits.await_lines(child.out, _announces_failure, 1,
                          'the resumed poll to announce the conclusion')
        calls = fake.calls()
        assert len(calls) == 2, [call['request'][:60] for call in calls]
        assert calls[1]['t'] >= before + 4, (calls[1]['t'], before)
        assert len([line for line in child.out.lines
                    if _reports_rate_limit(line)]) == 1
    finally:
        child.stop()


def _ci_wait(fake, extra=(), bound=30, limit=120):
    return subprocess.run(
        [sys.executable, '-u', str(SKILL / 'ci_wait.py'), SHA,
         '--interval', '1', '--timeout', str(bound), *extra],
        env=fake.env(), capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=limit)


def test_a_refused_wait_pauses_and_still_answers(tmp):
    reset_at = datetime.fromtimestamp(time.time() + 3, timezone.utc)
    answers = dict(idle_answers())
    answers[RUNS_QUERY] = [
        rate_limited_error(reset_at=reset_at.strftime(STAMP)),
        runs_page([suite(1, name='tests', check_runs=[published()])])]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert len([line for line in done.stderr.splitlines()
                if 'rate limit' in line]) == 1, done.stderr
    assert 'acceptable' in done.stdout, done.stdout
    assert len(fake.calls()) == 2, [call['request'][:60]
                                    for call in fake.calls()]


def test_a_wait_pauses_on_the_live_throttled_query_and_still_answers(tmp):
    """The wait's own contract on the refusal GitHub really sends.

    Every carrier is present here - the spent headers, the `RATE_LIMIT`
    entry with its `graphql_rate_limit` code, and the complaint `gh`
    wrote - because this control is about the wait rather than about any
    one carrier, and tests/test_gh_client.py drives each of them alone.
    Before the fix this exited 3 within a second of starting, which is
    what two pull-request waits on 2026-09-29 saw.
    """
    reset_at = datetime.fromtimestamp(time.time() + 3, timezone.utc)
    refusal = throttled_query(
        reset_at=reset_at.strftime(STAMP), exit=1,
        stderr=f'gh: {THROTTLED}\n')
    answers = dict(idle_answers())
    answers[RUNS_QUERY] = [refusal, runs_page([
        suite(1, name='tests', check_runs=[published()])])]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert len([line for line in done.stderr.splitlines()
                if 'rate limit' in line]) == 1, done.stderr
    assert 'acceptable' in done.stdout, done.stdout
    assert len(fake.calls()) == 2, [call['request'][:60]
                                    for call in fake.calls()]


def test_a_persistent_live_refusal_exits_two_and_never_three(tmp):
    """The other half of the exit contract, on the same shape: a limit
    that outlives the bound ends the wait at 2, and the line says the
    bound fell inside a pause. Exit 3 here would say the query FAILED,
    which is the outcome the whole of this branch exists to remove.
    """
    far = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(STAMP)
    answers = dict(idle_answers())
    answers[RUNS_QUERY] = throttled_query(
        reset_at=far, exit=1, stderr=f'gh: {THROTTLED}\n')
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake, bound=5, limit=40)
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert 'still rate limited' in done.stdout, done.stdout
    assert len(fake.calls()) <= 2, len(fake.calls())


def test_a_persistent_refusal_exits_two_at_its_timeout(tmp):
    """A limit that outlives the bound ends the wait; it does not spin. The
    refusal is never lifted, so the only way out is the wait's own --timeout,
    and the call count is the point: a wait that keeps polling past its bound
    is hammering the API refusing it.
    """
    far = datetime.now(timezone.utc) + timedelta(hours=2)
    reset_at = far.strftime(STAMP)
    answers = dict(idle_answers())
    answers[RUNS_QUERY] = rate_limited_error(reset_at=reset_at)
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake, bound=5, limit=40)
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert 'wait exceeded' in done.stdout, done.stdout
    calls = [call['request'][:60] for call in fake.calls()]
    assert len(calls) <= 2, len(calls)


def test_a_review_whose_inline_comments_overflow_is_followed_once(tmp):
    """A review repeated across pages is one review, not two: a page list
    that re-sends a connection it has finished - as a server does when a
    cursor is not honoured - must not buy a second follow-up query for the
    same review's inline comments. The quota is spent per query.
    """
    repeated = review(1, comments=[comment(10, inline=True)])
    repeated['id'] = 'REV1'
    repeated['comments'] = {
        'pageInfo': {'hasNextPage': True, 'endCursor': 'I'},
        'nodes': [comment(10, inline=True)]}
    page_one = pr_page(reviews=[repeated])
    page_one['data']['repository']['pullRequest']['comments']['pageInfo'] = {
        'hasNextPage': True, 'endCursor': 'C'}
    page_two = pr_page(reviews=[repeated], conversation=[comment(20)])
    answers = dict(idle_answers())
    answers['reviews(first: 100'] = [page_one, page_two]
    answers['on PullRequestReview'] = {'data': {'node': {'comments': {
        'pageInfo': {'hasNextPage': False, 'endCursor': None},
        'nodes': [comment(11, inline=True)]}}}}
    fake = _fake_gh.FakeGh(tmp, answers)
    done = subprocess.run(
        [sys.executable, '-u', str(SKILL / 'pr_comment_watch.py'),
         PR, '--once'], env=fake.env(), capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=60)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert 'ok 5 existing item(s) readable' in done.stderr, done.stderr
    follow_ups = fake.calls('on PullRequestReview')
    assert len(follow_ups) == 1, len(follow_ups)


def test_the_review_line_keeps_the_uppercase_state(tmp):
    """The REST review state was uppercase; the line must stay that way."""
    answers = dict(idle_answers())
    answers['reviews(first: 100'] = pr_page(reviews=[review(1)])
    fake = _fake_gh.FakeGh(tmp, answers)
    child = _watcher('pr_comment_watch.py', [PR, '--interval', '5'], fake)
    try:
        waits.await_lines(child.out, _announces_review, 1,
                          'the review announcement')
        line = [row for row in child.out.lines
                if _announces_review(row)][0]
        assert 'state=APPROVED' in line, line
    finally:
        child.stop()


def test_the_once_trial_counts_the_checks_that_have_not_concluded(tmp):
    """`N check run(s), M concluded` are two numbers again, not one twice."""
    page = ci_page([check(1, 'pylint')])
    contexts = page['data']['repository']['ref']['target'][
        'statusCheckRollup']['contexts']['nodes']
    contexts.append({'__typename': 'CheckRun', 'databaseId': 2,
                     'name': 'pyright', 'conclusion': None,
                     'detailsUrl': 'https://github.com/o/r/runs/2'})
    answers = dict(idle_answers())
    answers['statusCheckRollup'] = page
    fake = _fake_gh.FakeGh(tmp, answers)
    done = subprocess.run(
        [sys.executable, '-u', str(SKILL / 'ci_watch.py'), BRANCH, '--once'],
        env=fake.env(), capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=60)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert 'ok 2 check run(s), 1 concluded' in done.stderr, done.stderr


def test_a_graceful_exit_leaves_no_children_behind(tmp):
    """The teardown path, which a hard kill never reaches."""
    fake = _fake_gh.FakeGh(tmp, idle_answers())
    parent = _Child([sys.executable, '-u', str(SKILL / 'watch_all.py'),
                     PR, BRANCH, '--log', str(Path(tmp) / 'watch.log'),
                     '--debounce', '1', '--max-hold', '5'], fake.env())
    try:
        waits.await_lines(parent.err, _announces_pid, 2,
                          'both children to announce their pid')
        pids = [int(line.rsplit(' ', 1)[-1]) for line in parent.err.lines
                if _announces_pid(line)]
        _await_calls(fake, 2, parent)
        assert all(_pid_alive(pid) for pid in pids), pids
        if sys.platform.startswith('win'):
            parent.proc.send_signal(
                getattr(signal, 'CTRL_BREAK_EVENT'))
        else:
            parent.proc.send_signal(signal.SIGINT)
        parent.proc.wait(timeout=60)
        waits.await_gone(pids, parent, f'children {pids} to leave with a '
                         f'graceful exit', _pid_alive)
        assert not any(_pid_alive(pid) for pid in pids), pids
    finally:
        parent.stop()


def test_a_plain_refusal_still_exits_three_at_once(tmp):
    answers = dict(idle_answers())
    answers[RUNS_QUERY] = [
        refusal_response(403, {}, 'Resource not accessible by integration.')]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    assert 'rate limit' not in done.stderr, done.stderr
    assert len(fake.calls()) == 1, [call['request'][:60]
                                    for call in fake.calls()]


def test_a_superseded_cancelled_run_is_ignored_through_the_new_query(tmp):
    answers = dict(idle_answers())
    # The published verdict rides along, or the wait never certifies.
    answers[RUNS_QUERY] = [runs_page([
        suite(1, 'CANCELLED', started='2026-09-20T10:00:00Z', name='tests'),
        suite(2, 'SUCCESS', started='2026-09-20T10:05:00Z',
              name='tests', check_runs=[published()])])]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '1 superseded run(s) ignored' in done.stdout, done.stdout
    assert '  tests (run 1): cancelled ' \
           'https://github.com/o/r/actions/runs/1' in done.stdout, done.stdout


def test_a_superseded_failure_is_ignored_through_the_new_query(tmp):
    """Issue 1249 end to end, through the query the wait really makes: a
    `tests` failure on the SHA beside the newer `tests` run that cleared
    it. The rationale is ci_wait's module docstring; what is pinned here
    is that the discarded failure is still named."""
    answers = dict(idle_answers())
    answers[RUNS_QUERY] = [runs_page([
        suite(1, 'FAILURE', started='2026-09-20T10:00:00Z', name='tests'),
        suite(2, 'SUCCESS', started='2026-09-20T10:05:00Z',
              name='tests', check_runs=[published()])])]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert 'all 1 run(s)' in done.stdout, done.stdout
    assert '1 superseded run(s) ignored' in done.stdout, done.stdout
    assert '  tests (run 1): failure ' \
           'https://github.com/o/r/actions/runs/1' in done.stdout, done.stdout


def test_a_deliberate_cancel_still_fails_through_the_new_query(tmp):
    answers = dict(idle_answers())
    answers[RUNS_QUERY] = [runs_page([
        suite(1, 'CANCELLED', started='2026-09-20T10:00:00Z')])]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert 'workflow 11: cancelled' in done.stdout, done.stdout


def test_the_wait_reads_runs_for_the_pinned_sha_in_one_query(tmp):
    fake = _fake_gh.FakeGh(tmp, idle_answers())
    done = _ci_wait(fake, extra=['--once'])
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    calls = fake.calls()
    assert len(calls) == 1, [call['request'][:80] for call in calls]
    payload = json.loads(calls[0]['request'])
    assert payload['variables']['sha'] == SHA, payload['variables']
    assert 'check-runs' not in calls[0]['request']


def test_an_incomplete_wait_costs_one_query_beyond_the_runs_each_tick(tmp):
    """The incomplete path's per-tick price, measured rather than argued.

    While the set is incomplete the wait re-reads the head's pull request
    every tick, because `mergeable` is UNKNOWN while GitHub computes it and
    can still come back CONFLICTING minutes later. So this path costs TWO
    calls per tick where the acceptable path costs one, and the read count
    is `grace/interval + 1` for one wait: the read happens on the first
    observation AND on the tick that reaches the grace, which is SIX at
    the defaults (300/60) - by that arithmetic, not by a 300-second run,
    and the five an earlier version of this sentence claimed was wrong.
    What this control actually measures is one read per tick, since its
    bound is deliberately shorter than the grace, and that is the equality
    below.

    The count is taken from the fake's own key rather than by subtracting
    the runs reads from the total: a third query kind added to the wait
    later would otherwise be counted as pull-request reads and the equality
    would keep passing.
    """
    answers = dict(idle_answers())
    answers[RUNS_QUERY] = runs_page([suite(1, name='gate freshness')])
    empty = {'associatedPullRequests': {'nodes': []}}
    answers['associatedPullRequests'] = {'data': {'repository': {
        'object': empty}}}
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake, bound=5, limit=40)
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert 'not certified' in done.stdout, done.stdout
    run_calls = fake.calls(RUNS_QUERY)
    head_calls = fake.calls('associatedPullRequests')
    assert len(run_calls) >= 2, [c['request'][:60] for c in fake.calls()]
    # Lengths, not the call lists: two calls of different kinds are
    # different entries, so comparing the lists compares timestamps.
    assert len(head_calls) == len(run_calls), (
        len(head_calls), len(run_calls))


def test_the_windows_arm_answers_the_question_its_caller_asks(tmp):
    """The Windows arm is forced, because no local run can reach it.

    A whole-suite green proves nothing about it: `sys.platform` is never
    `win32` on the host that runs the suite, and
    `test_watcher_budget.py` runs on every OS in the matrix — which is
    exactly where a polarity error is free.

    So the arm is driven here through the seam the receipt is separable by: a
    stand-in `kernel32`, a chosen `GetLastError`, the platform forced on. A
    real child is not needed; the question is which way the two senses sit,
    not whether the API behaves as documented.
    """
    import _processtree as tree
    from unittest import mock

    del tmp

    def answers_alive(handle, last_error, exit_code=tree._STILL_ACTIVE):
        # The receipt asks the handle a second question now — not only
        # whether it opens, but whether the process has ended — so a
        # stand-in that answers only the first one leaves the control
        # reading a bare handle as a corpse. It owes an answer to both.
        kernel32 = mock.Mock()
        kernel32.OpenProcess.return_value = handle

        def get_exit_code(_handle, out):
            out._obj.value = exit_code
            return 1

        kernel32.GetExitCodeProcess.side_effect = get_exit_code
        with mock.patch.object(tree.sys, 'platform', 'win32'), \
                mock.patch.object(tree, '_windows_kernel32',
                                  return_value=kernel32), \
                mock.patch.object(tree.ctypes, 'get_last_error',
                                  return_value=last_error, create=True):
            return _windows_pid_alive(4242)

    assert answers_alive(7, tree._ERROR_INVALID_PARAMETER), (
        'an opened handle for a running process read as not alive')
    assert not answers_alive(7, tree._ERROR_INVALID_PARAMETER,
                             exit_code=0), (
        'an opened handle for an ended process read as alive')
    assert not answers_alive(None, tree._ERROR_INVALID_PARAMETER), (
        'a pid Windows says was never there read as alive')
    assert answers_alive(None, tree._ERROR_ACCESS_DENIED), (
        'a running pid this process may not open read as not alive')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchbudget_')


if __name__ == '__main__':
    raise SystemExit(main())
