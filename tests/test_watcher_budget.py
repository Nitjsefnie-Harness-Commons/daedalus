#!/usr/bin/env python3
"""What an idle watched pull request costs, and what a refusal does.

Every watcher here is a real process answering from the fake `gh` in
`_fake_gh.py`, so the numbers are the requests the scripts actually make. The
same harness measures the base commit's scripts, extracted with `git show`,
which is what makes the before/after comparison one method rather than two.
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'
BASE = '3cc3605f38f1b0c0d0e47d5252ad17154bad72ec'
PR = '195'
BRANCH = 'issue-997'
SHA = 'a' * 40

# The measurement tick. Long enough that the gap between ticks dominates the
# few milliseconds a request takes, short enough to measure two of them.
TICK = 2
MEASURED_CALLS = 8
STAMP = '%Y-%m-%dT%H:%M:%SZ'


def _page_info(has_next=False, cursor=None):
    return {'hasNextPage': has_next, 'endCursor': cursor}


def _author(login):
    return {'login': login}


def pr_page(reviews=(), conversation=(), state='OPEN', is_draft=False,
            merged_at=None):
    """One page of the single query the comment watcher now makes."""
    return {'data': {'repository': {'pullRequest': {
        'state': state, 'isDraft': is_draft, 'mergedAt': merged_at,
        'reviews': {'pageInfo': _page_info(), 'nodes': list(reviews)},
        'comments': {'pageInfo': _page_info(), 'nodes': list(conversation)}}}}}


def _review(rid, body='LGTM', comments=()):
    return {'databaseId': rid, 'body': body, 'state': 'APPROVED',
            'submittedAt': '2026-09-20T10:00:00Z', 'author': _author('alice'),
            'comments': {'pageInfo': _page_info(), 'nodes': list(comments)}}


def _comment(rid, body='looks good', inline=False):
    node = {'databaseId': rid, 'body': body,
            'createdAt': '2026-09-20T10:01:00Z',
            'updatedAt': '2026-09-20T10:01:00Z', 'author': _author('bob')}
    if inline:
        node['path'] = 'daedalus_bridge/result_store.py'
        node['line'] = 12
    return node


def ci_page(checks=(), sha=SHA):
    """One page of the single query the CI watcher now makes."""
    return {'data': {'repository': {'ref': {'target': {
        'oid': sha,
        'statusCheckRollup': {'contexts': {
            'pageInfo': _page_info(),
            'nodes': [{'__typename': 'CheckRun', 'databaseId': node['id'],
                       'name': node['name'],
                       'conclusion': node['conclusion'],
                       'detailsUrl': node['url']} for node in checks]}}}}}}}


def _check(rid, name, conclusion='SUCCESS'):
    return {'id': rid, 'name': name, 'conclusion': conclusion,
            'url': f'https://github.com/o/r/runs/{rid}'}


def runs_page(runs=()):
    """One page of the single query the wait and the hold now make."""
    return {'data': {'repository': {'commit': {'checkSuites': {
        'pageInfo': _page_info(),
        'nodes': [{'workflowRun': run} for run in runs]}}}}}


def _run(rid, conclusion='SUCCESS', status='COMPLETED',
         started='2026-09-20T10:00:00Z', workflow=11):
    return {'databaseId': rid, 'name': f'run {rid}', 'status': status,
            'conclusion': conclusion, 'createdAt': started,
            'url': f'https://github.com/o/r/actions/runs/{rid}',
            'workflow': {'databaseId': workflow}}


def _refusal(status=403, headers=None, body='API rate limit exceeded.'):
    return {'status': status, 'headers': headers or {}, 'body': body}


def _rate_limited_error(reset_at=None, retry_after=None):
    rate = {}
    if reset_at:
        rate['resetAt'] = reset_at
    if retry_after:
        rate['retryAfter'] = retry_after
    return {'status': 200, 'body': {'data': None, 'errors': [
        {'type': 'RATE_LIMITED', 'message': 'API rate limit exceeded.',
         'extensions': {'rateLimit': rate}}]}}


def _until(predicate, what, timeout=45):
    """Wait for a thing to become true; fail with what never became true."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f'timed out after {timeout}s waiting for '
                                 f'{what}')
        time.sleep(0.05)


class _Child:
    """A watcher process with both of its streams drained."""

    def __init__(self, argv, env):
        self.argv = argv
        self.proc = subprocess.Popen(
            argv, env=_util.child_coverage('scrub', environment=env),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', errors='replace')
        self.out = []
        self.err = []
        for stream, sink in ((self.proc.stdout, self.out),
                             (self.proc.stderr, self.err)):
            threading.Thread(target=self._pump, args=(stream, sink),
                             daemon=True).start()

    @staticmethod
    def _pump(stream, sink):
        for line in stream:
            sink.append(line.rstrip('\n'))

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=60)
        return self.proc.returncode


def _watcher(name, args, fake):
    return _Child([sys.executable, '-u', str(SKILL / name)] + args,
                  fake.env())


def _wait_for_calls(fake, count, timeout=45):
    def enough():
        calls = fake.calls()
        return calls if len(calls) >= count else None
    return _until(enough, f'{count} gh call(s)', timeout)


def _ticks(calls, tick=TICK):
    """Polls, counted from the log: a gap longer than half a tick is one."""
    ticks = 1
    for before, after in zip(calls, calls[1:]):
        if after['t'] - before['t'] > tick / 2:
            ticks += 1
    return ticks


def _per_tick(calls, tick=TICK):
    return len(calls) / _ticks(calls, tick)


def _hourly(per_tick, tick):
    return per_tick * (3600 / tick)


def _pid_alive(pid):
    """Whether a pid still names a process, on any platform CI runs."""
    if sys.platform.startswith('win'):
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = Path(f'/proc/{pid}/stat')
    if state.exists():
        # An orphan nobody has reaped yet is a corpse, not a survivor.
        text = state.read_text(encoding='utf-8', errors='replace')
        return text.rsplit(')', 1)[-1].split()[0] != 'Z'
    return True


def _measure(tmp, name, args, fake, tick=TICK, calls=MEASURED_CALLS):
    """Calls per poll for one watcher, measured from what gh received."""
    child = _watcher(name, args, fake)
    try:
        _wait_for_calls(fake, calls)
    finally:
        child.stop()
    seen = fake.calls()
    per_tick = _per_tick(seen, tick)
    return per_tick, seen


def _base_answers():
    """Answers for the REST surfaces the base watchers read.

    The first fragments are the specific paths: `pulls/195` alone is a
    substring of all three comment surfaces.
    """
    return {
        'pulls/195/reviews': json.dumps(
            [{'id': 1, 'body': 'LGTM', 'user': {'login': 'alice'},
              'submitted_at': '2026-09-20T10:00:00Z'}]),
        'pulls/195/comments': json.dumps([]),
        'issues/195/comments': json.dumps([]),
        'pulls/195': json.dumps({'merged_at': None, 'state': 'open',
                                 'draft': False}),
        f'branches/{BRANCH}': json.dumps({'commit': {'sha': SHA}}),
        'check-runs': json.dumps({'check_runs': [
            {'id': 1, 'name': 'pylint', 'conclusion': 'success',
             'html_url': 'https://github.com/o/r/runs/1'}]}),
        'actions/runs': json.dumps({'workflow_runs': [
            {'id': 1, 'name': 'run 1', 'status': 'completed',
             'conclusion': 'success',
             'run_started_at': '2026-09-20T10:00:00Z',
             'workflow_id': 11,
             'html_url': 'https://github.com/o/r/actions/runs/1'}]}),
    }


def _idle_answers():
    """One idle pull request, answerable by this tree and by the base.

    The same fixture set serves both, which is what makes the before/after
    measurement one method: a base watcher answers from the REST surfaces and
    spends four and two requests per poll, exactly as it did in production.
    """
    return {
        'reviews(first: 100': pr_page(),
        'statusCheckRollup': ci_page([_check(1, 'pylint')]),
        'checkSuites': runs_page([_run(1)]),
        **_base_answers(),
    }


def test_an_idle_comment_watch_costs_one_query_per_tick(tmp):
    fake = _fake_gh.FakeGh(tmp, _idle_answers())
    per_tick, seen = _measure(
        tmp, 'pr_comment_watch.py', [PR, '--interval', str(TICK)], fake)
    print(f'\n  comment watcher: {per_tick} call(s) per tick, '
          f'{_hourly(per_tick, TICK):.0f}/hour at a {TICK}s tick, '
          f'from {len(seen)} logged call(s)')
    assert per_tick <= 1, (per_tick, [call['request'][:80] for call in seen])


def test_an_idle_ci_watch_costs_one_query_per_tick(tmp):
    fake = _fake_gh.FakeGh(tmp, _idle_answers())
    per_tick, seen = _measure(
        tmp, 'ci_watch.py', [BRANCH, '--interval', str(TICK)], fake)
    print(f'\n  CI watcher: {per_tick} call(s) per tick, '
          f'{_hourly(per_tick, TICK):.0f}/hour at a {TICK}s tick, '
          f'from {len(seen)} logged call(s)')
    assert per_tick <= 1, (per_tick, [call['request'][:80] for call in seen])


def _base_script(directory, name):
    """The base commit's copy of one watcher, or None when unreachable."""
    found = subprocess.run(
        ['git', '-C', str(ROOT), 'show', f'{BASE}:.claude/skills/'
         f'changing-daedalus/{name}'], capture_output=True)
    if found.returncode != 0:
        return None
    Path(directory).mkdir(parents=True, exist_ok=True)
    path = Path(directory) / name
    path.write_bytes(found.stdout)
    return path


def test_the_hourly_cost_of_an_idle_watch_is_two_queries(tmp):
    """The branch's own watchers, measured through the whole poll surface."""
    after = {}
    for name, args in (('pr_comment_watch.py', [PR]),
                       ('ci_watch.py', [BRANCH])):
        here = Path(tmp) / 'after' / name
        here.parent.mkdir(parents=True, exist_ok=True)
        fake = _fake_gh.FakeGh(here.parent, _idle_answers())
        per_tick, seen = _measure(here.parent, name,
                                  args + ['--interval', str(TICK)], fake)
        after[name] = per_tick
        assert per_tick <= 1, (name, per_tick,
                               [call['request'][:80] for call in seen])
    total = sum(after.values())
    print(f'\n  AFTER an idle watched pull request costs {total:.0f} gh '
          f'call(s) per poll ({after}), '
          f'{total * 60:.0f}/hour at the 60s default tick')


def test_the_base_commit_cost_through_the_same_harness(tmp):
    """The same measurement over the base commit's scripts, for the delta.

    The base scripts invoke `gh` by bare name, which only a POSIX PATH can
    resolve to the fake; the figure is therefore reported from the platform
    that can produce it rather than from an invented one.
    """
    if _fake_gh.WINDOWS:
        _util.skip('the base scripts call gh by bare name, which no PATH '
                   'seam can answer on Windows; the AFTER figure and the '
                   'measurement method are platform-independent')
    before = {}
    for name, args in (('pr_comment_watch.py', [PR]),
                       ('ci_watch.py', [BRANCH])):
        source = _base_script(Path(tmp) / 'before', name)
        if source is None:
            _util.skip(f'base commit {BASE} is not reachable in this '
                       f'checkout; the BEFORE figure is never invented')
        before_dir = Path(tmp) / 'before' / f'src-{name}'
        before_dir.mkdir(parents=True, exist_ok=True)
        script = before_dir / name
        script.write_bytes(source.read_bytes())
        fake = _fake_gh.FakeGh(before_dir, _base_answers())
        child = _Child([sys.executable, '-u', str(script),
                        *args, '--interval', str(TICK)], fake.env())
        try:
            _wait_for_calls(fake, MEASURED_CALLS)
        finally:
            child.stop()
        before[name] = _per_tick(fake.calls())
    total = sum(before.values())
    assert total >= 6, before
    print(f'\n  BEFORE an idle watched pull request cost {total:.0f} gh '
          f'call(s) per poll ({before}), '
          f'{total * 60:.0f}/hour at the 60s default tick')


def test_the_children_die_with_their_parent(tmp):
    fake = _fake_gh.FakeGh(tmp, _idle_answers())
    parent = _Child([sys.executable, '-u', str(SKILL / 'watch_all.py'),
                     PR, BRANCH, '--log', str(Path(tmp) / 'watch.log'),
                     '--debounce', '1', '--max-hold', '5'], fake.env())
    try:
        _until(lambda: len([line for line in parent.err
                            if 'watcher pid' in line]) == 2,
               'both children to announce their pid')
        pids = [int(line.rsplit(' ', 1)[-1]) for line in parent.err
                if 'watcher pid' in line]
        _wait_for_calls(fake, 2)
        assert all(_pid_alive(pid) for pid in pids), pids
        parent.proc.kill()
        parent.proc.wait(timeout=60)
        _until(lambda: not any(_pid_alive(pid) for pid in pids),
               f'children {pids} to die with the parent')
        assert not any(_pid_alive(pid) for pid in pids), pids
    finally:
        parent.stop()


def test_a_refused_comment_poll_pauses_until_the_reset_and_resumes(tmp):
    reset = int(time.time()) + 6
    answers = dict(_idle_answers())
    answers['reviews(first: 100'] = [
        _refusal(headers={'X-RateLimit-Reset': str(reset)}),
        pr_page(reviews=[_review(1)], conversation=[_comment(2)])]
    fake = _fake_gh.FakeGh(tmp, answers)
    child = _watcher('pr_comment_watch.py',
                     [PR, '--interval', '5', '--parent-pid',
                      str(os.getpid())], fake)
    try:
        _until(lambda: [line for line in child.out
                        if 'rate limit' in line],
               'the pause line naming the reset')
        stamp = datetime.fromtimestamp(reset, timezone.utc).strftime(STAMP)
        pause = [line for line in child.out if 'rate limit' in line][0]
        assert stamp in pause, (stamp, pause)
        _until(lambda: any('state: open' in line for line in child.out),
               'the resumed poll to report what it found')
        calls = fake.calls()
        assert len(calls) == 2, [call['request'][:60] for call in calls]
        assert calls[1]['t'] >= reset, (calls[1]['t'], reset)
        # One line for the whole wait, not one per poll inside it.
        assert len([line for line in child.out
                    if 'rate limit' in line]) == 1
    finally:
        child.stop()


def test_a_refused_ci_poll_pauses_on_a_retry_after(tmp):
    before = time.time()
    answers = dict(_idle_answers())
    answers['statusCheckRollup'] = [
        _refusal(429, {'Retry-After': '4'}),
        ci_page([_check(1, 'pyright', 'FAILURE')])]
    fake = _fake_gh.FakeGh(tmp, answers)
    child = _watcher('ci_watch.py',
                     [BRANCH, '--interval', '5', '--debounce', '0',
                      '--parent-pid', str(os.getpid())], fake)
    try:
        _until(lambda: [line for line in child.out
                        if 'rate limit' in line],
               'the CI pause line')
        _until(lambda: any('pyright: failure' in line
                           for line in child.out),
               'the resumed poll to announce the conclusion')
        calls = fake.calls()
        assert len(calls) == 2, [call['request'][:60] for call in calls]
        assert calls[1]['t'] >= before + 4, (calls[1]['t'], before)
        assert len([line for line in child.out
                    if 'rate limit' in line]) == 1
    finally:
        child.stop()


def _ci_wait(fake, extra=()):
    return subprocess.run(
        [sys.executable, '-u', str(SKILL / 'ci_wait.py'), SHA,
         '--interval', '1', '--timeout', '30', *extra],
        env=fake.env(), capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=120)


def test_a_refused_wait_pauses_and_still_answers(tmp):
    reset_at = datetime.fromtimestamp(time.time() + 3, timezone.utc)
    answers = dict(_idle_answers())
    answers['checkSuites'] = [
        _rate_limited_error(reset_at=reset_at.strftime(STAMP)),
        runs_page([_run(1)])]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert len([line for line in done.stderr.splitlines()
                if 'rate limit' in line]) == 1, done.stderr
    assert 'acceptable' in done.stdout, done.stdout
    assert len(fake.calls()) == 2, [call['request'][:60]
                                    for call in fake.calls()]


def test_a_plain_refusal_still_exits_three_at_once(tmp):
    answers = dict(_idle_answers())
    answers['checkSuites'] = [
        _refusal(403, {}, 'Resource not accessible by integration.')]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    assert 'rate limit' not in done.stderr, done.stderr
    assert len(fake.calls()) == 1, [call['request'][:60]
                                    for call in fake.calls()]


def test_a_superseded_cancelled_run_is_ignored_through_the_new_query(tmp):
    answers = dict(_idle_answers())
    answers['checkSuites'] = [runs_page([
        _run(1, 'CANCELLED', started='2026-09-20T10:00:00Z'),
        _run(2, 'SUCCESS', started='2026-09-20T10:05:00Z')])]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert '1 superseded cancelled ignored' in done.stdout, done.stdout


def test_a_deliberate_cancel_still_fails_through_the_new_query(tmp):
    answers = dict(_idle_answers())
    answers['checkSuites'] = [runs_page([
        _run(1, 'CANCELLED', started='2026-09-20T10:00:00Z')])]
    fake = _fake_gh.FakeGh(tmp, answers)
    done = _ci_wait(fake)
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert 'run 1: cancelled' in done.stdout, done.stdout


def test_the_wait_reads_runs_for_the_pinned_sha_in_one_query(tmp):
    fake = _fake_gh.FakeGh(tmp, _idle_answers())
    done = _ci_wait(fake, extra=['--once'])
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    calls = fake.calls()
    assert len(calls) == 1, [call['request'][:80] for call in calls]
    payload = json.loads(calls[0]['request'])
    assert payload['variables']['sha'] == SHA, payload['variables']
    assert 'check-runs' not in calls[0]['request']


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchbudget_')


if __name__ == '__main__':
    raise SystemExit(main())
