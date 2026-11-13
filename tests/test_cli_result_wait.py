#!/usr/bin/env python3
"""Regression controls for CLI result delivery matching."""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _overlap_clients  # noqa: E402
import _util  # noqa: E402
from _cli_helpers import BRIDGE_ENV, CLI, cli_env, run_cli  # noqa: E402
from _queueread import queued_command  # noqa: E402


TOK = 'clitok'


_SCRIPTED_HARNESS = (
    'import itertools, json, sys, urllib.error\n'
    'from daedalus_cli import transport\n'
    'def _open(req, timeout=None):\n'
    '    raise opened\n'
    'transport._open = _open\n'
    'class _Clock:\n'
    '    def __init__(self):\n'
    '        self.now = 1000.0\n'
    '        self.sleeps = []\n'
    '    def monotonic(self):\n'
    '        return self.now\n'
    '    def sleep(self, seconds):\n'
    '        # time.sleep refuses a negative duration.\n'
    '        if seconds < 0:\n'
    '            raise ValueError("sleep length must be non-negative")\n'
    '        self.sleeps.append(seconds)\n'
    '        self.now += seconds\n'
    'transport.time = _Clock()\n'
    'replies, timeout, interval, delivery = json.loads(sys.argv[1])\n'
    'script = itertools.cycle(replies)\n'
    'calls = []\n'
    'def fake_api(method, path, body=None, timeout=None, headers=None):\n'
    '    calls.append(path)\n'
    '    reply = next(script)\n'
    '    # spends its timeout; a consume, sent with none, its 30s\n'
    '    if reply in ("slow", "late", "connect", "reset"):\n'
    '        transport.time.now += timeout or 30\n'
    '    if reply in ("slow", "early"):\n'
    '        raise transport.ConnectionFailed(TimeoutError("timed out"))\n'
    '    if reply in ("connect", "blank"):  # _open raises it\n'
    '        global opened\n'
    '        opened = urllib.error.URLError(\n'
    '            TimeoutError("timed out") if reply == "connect" else "")\n'
    '        return transport._exchange(None, timeout, None)\n'
    '    if reply == "reset":\n'
    '        raise transport.ConnectionFailed(\n'
    '            ConnectionResetError(104, "Connection reset by peer"))\n'
    '    if reply == "late":\n'
    '        raise transport.ErrorStatus("HTTP 502: x")\n'
    '    if reply == "mute":\n'
    '        raise transport.ConnectionFailed(OSError())\n'
    '    if reply == "cut":\n'
    '        raise transport.ConnectionFailed("cut off")\n'
    '    if reply == "500":\n'
    '        raise transport.ErrorStatus("HTTP 500: {\'error\': 1}")\n'
    '    return reply\n'
    'transport._request = fake_api\n'
    'try:\n'
    '    result = transport.wait_for_result(\n'
    '        "c1", "extension", delivery, timeout, interval=interval)\n'
    'except SystemExit as stop:\n'
    '    result = {"exit": stop.code}\n'
    'print(json.dumps({"sleeps": transport.time.sleeps, "calls": calls,\n'
    '                  "now": transport.time.now, "result": result}))\n')


def _scripted_wait(replies, timeout=1.0, interval=0.5,
                   delivery: str | None = 'd1'):
    """The wait over `_request` answers replayed in a cycle."""
    run = subprocess.run(
        [sys.executable, '-c', _SCRIPTED_HARNESS,
         json.dumps([replies, timeout, interval, delivery])],
        cwd=str(_util.ROOT), env=cli_env(DAEDALUS_TOKEN=TOK),
        capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    return json.loads(run.stdout)


OWN = {'id': 'c1', 'deliveryId': 'd1', 'resultGeneration': 'g1',
       'result': 7, 'error': None}
PEEK = '/result?tab=extension&delivery=d1'
CONSUME = PEEK + '&consume=1&expected=g1'


NOT_OBJECT = 'Connection failed: the answer is JSON but not an object'


def _unknown(timeout, failure):
    return {'exit': (
        f'Timeout ({timeout}s), outcome unknown: a result request failed '
        f'({failure}). The command was sent and may have run; check its '
        'effect before retrying.')}


_OVERSHOOT_HARNESS = (
    'import json\n'
    'from daedalus_cli import transport\n'
    'class _Clock:\n'
    '    def __init__(self, timeout):\n'
    '        self.now = 1000.0\n'
    '        self.deadline = self.now + timeout\n'
    '        self.sleeps = []\n'
    '    def monotonic(self):\n'
    '        return self.now\n'
    '    def sleep(self, seconds):\n'
    '        # time.sleep refuses a negative duration.\n'
    '        if seconds < 0:\n'
    '            raise ValueError("sleep length must be non-negative")\n'
    '        self.sleeps.append(seconds)\n'
    '        if len(self.sleeps) == 1:\n'
    '            # The opening sleep runs the budget out, landing on\n'
    '            # the deadline: a loaded runner returning from 0.02s\n'
    '            # after the 0.5s is gone. Landing exactly is what\n'
    '            # tells "<= 0" apart from "< 0"; no real clock can.\n'
    '            self.now = self.deadline\n'
    '        else:\n'
    '            self.now += seconds\n'
    'transport.time = _Clock(0.5)\n'
    'calls = []\n'
    'def fake_api(method, path, body=None, timeout=None, headers=None):\n'
    '    calls.append(path)\n'
    '    return {"pending": True}\n'
    'transport._request = fake_api\n'
    'result = transport.wait_for_result(\n'
    '    "c1", "extension", "d1", 0.5, interval=0.01)\n'
    'print(json.dumps({"result": result, "calls": calls,\n'
    '                  "sleeps": transport.time.sleeps,\n'
    '                  "now": transport.time.now,\n'
    '                  "deadline": transport.time.deadline},\n'
    '                 sort_keys=True))\n')


def _wait_for(predicate, timeout=15, what='condition'):
    left_ms = timeout * 1000 + 50
    while (left_ms := left_ms - 50) > 0:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f'timed out waiting for {what}')


def test_result_wait_requires_nonempty_exact_delivery_ids(tmp):
    """Uncorrelated delivery IDs keep polling without consuming a result."""
    del tmp
    for name, sent_id, delivery_field, selector in (
            ('absent', None, {}, '/result?tab=extension'),
            ('empty', '', {'deliveryId': ''}, '/result?tab=extension'),
            ('foreign', 'd1', {'deliveryId': 'D1'},
             '/result?tab=extension&delivery=d1')):
        stale = {'id': 'c1', 'result': 'STALE', 'error': None,
                 'resultGeneration': 'g1', **delivery_field}
        outcome = _scripted_wait([stale], 0.5, 0.01, sent_id)
        peeks = [p for p in outcome['calls'] if 'consume=1' not in p]
        assert outcome['result'] is None, (name, outcome['result'])
        assert len(peeks) == len(outcome['calls']), (name, outcome['calls'])
        sleeps = outcome['sleeps']
        # Bounded failure message: a pathological record can be long.
        brief = {'name': name, 'result': outcome['result'],
                 'peeks': len(peeks), 'sleep_n': len(sleeps),
                 'sleep_head': sleeps[:6], 'sleep_tail': sleeps[-2:]}
        # What the loop REQUESTS, which a real clock records nothing of.
        # No exact peek count: how many laps clear a 0.5s budget is a
        # float-accumulation artifact of the stand-in's epoch, and it
        # moved when only the epoch changed.
        assert sleeps[:1] == [0.02], brief         # opens below interval
        assert set(sleeps[1:-1]) == {0.01}, brief  # saturates at interval
        # The last lap is cut to the budget that was left, so the loop
        # stopped short of asking for another whole interval. The slice
        # guards keep an empty record a named failure, not an IndexError.
        assert sleeps[-1:] and 0 < sleeps[-1] < 0.01, brief
        # One peek per lap that left budget. now == deadline says the
        # loop neither quit early nor overshot: the last sleep is the
        # exact remainder by construction, so this holds for any number
        # of laps and is not what reds on a wrong ramp.
        assert len(peeks) == len(sleeps) - 1, brief
        assert outcome['now'] == 1000.0 + 0.5, brief
        assert set(peeks) == {selector}, brief


def test_result_wait_rejects_receipt_for_different_generation(tmp):
    """Repeated wrong-generation receipts cannot claim the selected body."""
    del tmp
    outcome = _scripted_wait([OWN, {
        'consumed': True, 'resultGeneration': 'g2'}], 2.0, 0.01)
    assert outcome['result'] is None, outcome
    peeks = [path for path in outcome['calls'] if 'consume=1' not in path]
    consumes = [path for path in outcome['calls'] if 'consume=1' in path]
    assert set(peeks) == {'/result?tab=extension&delivery=d1'}, outcome
    assert set(consumes) == {
        '/result?tab=extension&delivery=d1&consume=1&expected=g1'
    }, outcome


def test_the_result_wait_backs_off_while_the_result_stays_pending(tmp):
    """A pending result is polled on a ramp that saturates, never a flood.

    The ramp opens at 20ms and doubles to the CALLER'S interval, then
    saturates there. Two cases drive a different interval each, so a cap
    hardcoded to either value cannot survive. A virtual clock stands in
    for time.sleep and records each requested interval, so the test
    asserts the schedule the loop REQUESTS — identical on every machine —
    rather than how many polls a real scheduler happened to grant a real
    1.0-second wait, which is what made the original form a wall-clock
    margin: it failed a macOS leg with 3 polls.
    """
    del tmp
    cases = (
        (3.0, 0.5, [0.02, 0.04, 0.08, 0.16, 0.32]),
        (3.0, 0.3, [0.02, 0.04, 0.08, 0.16, 0.3]),
    )
    for timeout, interval, ramp in cases:
        outcome = _scripted_wait([{'pending': True}], timeout, interval)
        sleeps = outcome['sleeps']
        # Bounded failure message: a pathological record can be long.
        brief = {'polls': len(outcome['calls']), 'result': outcome['result'],
                 'sleep_n': len(sleeps), 'sleep_head': sleeps[:6]}
        assert outcome['result'] is None, brief
        assert sleeps[:len(ramp)] == ramp, brief
        assert set(sleeps[len(ramp):-1]) == {interval}, brief
        # The last lap is cut to what is left of the budget, so the
        # requested record alone spends it exactly.
        assert abs(sum(sleeps) - timeout) < 1e-9, brief
        assert len(outcome['calls']) == len(sleeps) - 1, brief


def test_the_result_wait_expires_on_the_ramp_when_every_peek_fails(tmp):
    """Every peek failing ends on the ramp, exactly, its outcome unknown.

    ConnectionFailed from _request is what a proxy cut produces at
    transport level, whatever the HTTP shape of the cut. The virtual
    clock records the sleeps the loop REQUESTS and advances by nothing
    else, so the poll count and sleep list below are the ramp
    arithmetic itself — a loaded runner cannot starve them, which is
    the property the CLI-level pins could not have (issue 901).
    """
    del tmp
    outcome = _scripted_wait(['cut'])
    sleeps = outcome['sleeps']
    brief = {'polls': len(outcome['calls']), 'result': outcome['result'],
             'sleeps': sleeps}
    assert outcome['result'] == _unknown(
        1.0, 'Connection failed: cut off'), brief
    # Five ramp laps fit in 1.0s (0.62 spent); the sixth is cut to the
    # remainder, which spends the budget, so the loop expires before a
    # sixth peek.
    assert len(outcome['calls']) == 5, brief
    assert sleeps[:5] == [0.02, 0.04, 0.08, 0.16, 0.32], brief
    assert len(sleeps) == 6, brief
    assert 0.32 < sleeps[-1] < 0.5, brief
    assert abs(sum(sleeps) - 1.0) < 1e-9, brief


def test_a_failed_consume_is_retried_and_never_reported_as_failed(tmp):
    """A failed consume is retried; never claimed, the outcome is unknown."""
    del tmp
    for failure, named in (
            ('500', "HTTP 500: {'error': 1}"),
            ('cut', 'Connection failed: cut off'),
            ('mute', 'Connection failed: OSError'),
            ('blank', 'Connection failed: URLError'),
            *((value, NOT_OBJECT) for value in ([], None, 'x', 3))):
        claimed = {'consumed': True, 'resultGeneration': 'g1'}
        outcome = _scripted_wait([OWN, failure, OWN, claimed])
        assert outcome['result'] == OWN, (failure, outcome)
        assert outcome['calls'] == [PEEK, CONSUME] * 2, (failure, outcome)
        outcome = _scripted_wait([OWN, failure] + [{'pending': True}] * 4)
        assert outcome['result'] == _unknown(1.0, named), (failure, outcome)
        assert outcome['calls'] == [PEEK, CONSUME] + [PEEK] * 4, (
            failure, outcome)
    outcome = _scripted_wait([OWN, 'slow'])  # consume ends past the deadline
    assert outcome['result'] == _unknown(
        1.0, 'Connection failed: timed out'), outcome


def test_only_a_peeks_own_timeout_is_exempt_from_the_deadline(tmp):
    """Only a peek's own timeout is the deadline; a late 502 still fails."""
    del tmp
    for reply, expected in (
            ('slow', None), ('connect', None),
            ('late', _unknown(1.0, 'HTTP 502: x')),
            ('early', _unknown(1.0, 'Connection failed: timed out')),
            ('reset', _unknown(1.0, 'Connection failed: [Errno 104] '
                               'Connection reset by peer'))):
        outcome = _scripted_wait([reply])
        assert outcome['result'] == expected, (reply, outcome)
        if expected is None:
            assert outcome['calls'] == [PEEK], (reply, outcome)


def test_the_result_wait_reports_none_when_the_sleep_spends_the_budget(tmp):
    """A wait whose opening sleep outlives the budget polls nothing.

    The recorded macOS failure was
    {'consumes': [], 'name': 'empty', 'peeks': [], 'result': None}: the
    waiter issued no request and returned before its first poll. The
    opening sleep outliving the budget is the reading that record
    supports — the other, a guard firing before any sleep, is far less
    likely — but no control can pin which, because the real-clock
    harness recorded no sleep schedule. The empty record is correct
    either way: the caller asked to wait at most 0.5s, the deadline says
    stop, and nothing was consumed.

    What was wrong was the control around it, not the waiter. The
    virtual clock here lands the opening sleep exactly on the deadline,
    so the record is produced by a run instead of by a busy machine.
    """
    del tmp
    run = subprocess.run(
        [sys.executable, '-c', _OVERSHOOT_HARNESS],
        cwd=str(_util.ROOT), env=cli_env(DAEDALUS_TOKEN=TOK),
        capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    outcome = json.loads(run.stdout)
    peeks = [path for path in outcome['calls'] if 'consume=1' not in path]
    consumes = [path for path in outcome['calls'] if 'consume=1' in path]
    brief = {'result': outcome['result'], 'sleeps': outcome['sleeps'],
             'sleep_n': len(outcome['sleeps']), 'calls': outcome['calls'][:4]}
    # The loop slept and slept past the deadline, so the guard it reached
    # is the post-sleep one; without this the empty-record assertions
    # below would also hold for a loop that returned before any sleep.
    assert outcome['sleeps'] == [0.02], brief
    assert outcome['now'] == outcome['deadline'], brief
    assert outcome['result'] is None, brief
    assert peeks == [], brief
    assert consumes == [], brief


def test_waiter_leaves_a_foreign_result_in_place(tmp):
    """A waiter that sees another caller's result must not consume it.

    The foreign result is posted while the CLI waiter is already polling, the
    waiter's own result never arrives, and the foreign result remains readable
    afterwards. Driven through `cookies` because typed extension commands use
    the shared extension result slot.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the enqueued command file')
            # Another caller's result lands while our waiter is polling.
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': 'theirs',
                'result': 'not yours', 'error': None, 'ts': 1})
            assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
        # Our result never arrived, so the waiter times out ...
        assert proc.returncode != 0, (proc.returncode, out, err)
        assert 'Timeout' in err, (out, err)
        # ... and the foreign result is still there to be read.
        status, body = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert status == 200, status
        assert body.get('id') == 'theirs', body
        assert body.get('result') == 'not yours', body


def test_waiter_skips_a_foreign_result_and_finds_its_own(tmp):
    """A foreign result seen mid-wait is neither returned as ours nor fatal:
    the waiter keeps polling and completes when its own result arrives."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            queued = queued_command(qdir, 'the enqueued command file')
            # A foreign result first (results share one slot per tab, so the
            # own result posted after it overwrites the slot) ...
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': 'theirs',
                'result': 'not yours', 'error': None, 'ts': 1})
            assert status == 200, status
            # let the waiter see the foreign result at least once
            time.sleep(0.6)
            # ... then our own.
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': queued['id'],
                'result': [], 'error': None, 'ts': 2,
                '_did': queued['_did']})
            assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
        assert proc.returncode == 0, (proc.returncode, out, err)
        assert '0 cookies' in out, out
        assert 'not yours' not in out, out
        # The waiter consumed its own result.
        assert not (Path(docroot) / 'results'
                    / f'{TOK}_extension.json').exists()


def test_typed_command_does_not_return_a_stale_fixed_id_result(tmp):
    """A prior `_cookies` result cannot satisfy a new cookies invocation."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': '_cookies',
            'result': [{'domain': 'stale.invalid', 'name': 'stale',
                        'value': 'old'}],
            'error': None, 'ts': 1, '_did': '1000000000000_000001'})
        assert status == 200, status

        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'], cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            queued = queued_command(qdir, 'the fresh cookies command')
            # Let the first poll observe the stale result before answering the
            # newly queued invocation as the extension would.
            time.sleep(0.7)
            if proc.poll() is None:
                status, _ = _util.post_json(base + '/result', {
                    'token': TOK, 'tabId': 'extension', 'id': queued['id'],
                    'result': [{'domain': 'fresh.invalid', 'name': 'fresh',
                                'value': 'new'}],
                    'error': None, 'ts': 2, '_did': queued['_did']})
                assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
        assert proc.returncode == 0, (proc.returncode, out, err)
        assert 'fresh.invalid' in out, out
        assert 'stale.invalid' not in out, out


def test_two_same_id_clients_receive_only_their_own_results(tmp):
    """Two CLI callers stay correlated in either completion order."""
    def client_argv(owner):
        # The client's patience has to cover this fixture's whole setup -- a
        # node spawn and the harness's own waits -- and the default ten
        # seconds does not on a loaded Windows runner: both clients exited
        # with `Timeout (10s)` before the first result was posted, leaving
        # nobody to consume it.
        return CLI + [
            'cookies', '--domain', owner, '--timeout', '120',
        ]

    background = _util.ROOT / 'extension' / 'background.js'
    actual = {
        'a-first': _overlap_clients.run_same_id_client_overlap(
            Path(tmp) / 'a-first', ['owner-a', 'owner-b'], client_argv,
            cli_env(), TOK, background),
        'b-first': _overlap_clients.run_same_id_client_overlap(
            Path(tmp) / 'b-first', ['owner-b', 'owner-a'], client_argv,
            cli_env(), TOK, background),
    }
    per_owner = {
        owner: {
            'returncode': 0,
            'ownResult': True,
            'foreignResult': False,
            'stderr': '',
        }
        for owner in ('owner-a', 'owner-b')
    }
    assert actual == {
        'a-first': per_owner,
        'b-first': per_owner,
    }, actual


def test_a_negative_timeout_is_refused_before_the_command_is_sent(tmp):
    """Refusing the wait is only useful if nothing was admitted first.

    The command was PUT before the deadline was evaluated, so a negative
    timeout polled zero times, told the caller it had timed out, and left a
    command the browser was still free to execute. Retrying after that
    report runs the side effect twice.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['screenshot', '--timeout', '-1'], env)
        assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
        assert 'timeout must not be negative' in r.stderr, r.stderr
        qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
        queued = sorted(qdir.glob('*.json')) if qdir.is_dir() else []
        assert queued == [], queued

        # Zero keeps the meaning it already had — "unset", so the
        # subcommand's own default applies — and is not refused.
        r = run_cli(['screenshot', '--timeout', '0', '--help'], env)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
