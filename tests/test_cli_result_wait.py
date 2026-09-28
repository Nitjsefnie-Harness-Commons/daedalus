#!/usr/bin/env python3
"""Regression controls for CLI result delivery matching."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


TOK = 'clitok'


_WAIT_HARNESS = (
    'import json\n'
    'from daedalus_cli import transport\n'
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
    'cases = (\n'
    '    ("absent", None, {}),\n'
    '    ("empty", "", {"deliveryId": ""}),\n'
    '    ("foreign", "d1", {"deliveryId": "D1"}),\n'
    ')\n'
    'outcomes = []\n'
    'for name, sent_id, delivery_field in cases:\n'
    '    transport.time = _Clock()\n'
    '    deadline = transport.time.monotonic() + 0.5\n'
    '    calls = []\n'
    '    result_body = {"id": "c1", "result": "STALE",\n'
    '                   "error": None, "resultGeneration": "g1"}\n'
    '    result_body.update(delivery_field)\n'
    '    def fake_api(method, path, body=None, timeout=None,\n'
    '                 headers=None):\n'
    '        calls.append(path)\n'
    '        if "consume=1" in path:\n'
    '            return {"consumed": True, "resultGeneration": "g1"}\n'
    '        return result_body\n'
    '    transport._request = fake_api\n'
    '    result = transport.wait_for_result(\n'
    '        "c1", "extension", sent_id, 0.5, interval=0.01)\n'
    '    outcomes.append({\n'
    '        "name": name,\n'
    '        "result": None if result is None else result["result"],\n'
    '        "peeks": [p for p in calls if "consume=1" not in p],\n'
    '        "consumes": [p for p in calls if "consume=1" in p],\n'
    '        "sleeps": transport.time.sleeps,\n'
    '        "now": transport.time.now,\n'
    '        "deadline": deadline,\n'
    '    })\n'
    'print(json.dumps(outcomes, sort_keys=True))\n')


_GENERATION_HARNESS = (
    'import json\n'
    'from daedalus_cli import transport\n'
    'calls = []\n'
    'result_body = {"id": "c1", "deliveryId": "d1", "result": "R1",\n'
    '               "error": None, "resultGeneration": "g1"}\n'
    'def fake_api(method, path, body=None, timeout=None, headers=None):\n'
    '    calls.append(path)\n'
    '    if "consume=1" in path:\n'
    '        return {"consumed": True, "resultGeneration": "g2"}\n'
    '    return result_body\n'
    'transport._request = fake_api\n'
    'result = transport.wait_for_result(\n'
    '    "c1", "extension", "d1", 2.0, interval=0.01)\n'
    'print(json.dumps({"result": result, "calls": calls},\n'
    '                 sort_keys=True))\n')


_BACKOFF_HARNESS = (
    'import json\n'
    'from daedalus_cli import transport\n'
    'class _Clock:\n'
    '    def __init__(self):\n'
    '        self.now = 1000.0\n'
    '        self.sleeps = []\n'
    '    def monotonic(self):\n'
    '        return self.now\n'
    '    def sleep(self, seconds):\n'
    '        if seconds < 0:\n'
    '            raise ValueError("sleep length must be non-negative")\n'
    '        self.sleeps.append(seconds)\n'
    '        self.now += seconds\n'
    'transport.time = _Clock()\n'
    'calls = []\n'
    'def fake_api(method, path, body=None, timeout=None, headers=None):\n'
    '    calls.append(path)\n'
    '    return {"pending": True}\n'
    'transport._request = fake_api\n'
    'result = transport.wait_for_result(\n'
    '    "c1", "extension", "d1", %s, interval=%s)\n'
    'print(json.dumps({"sleeps": transport.time.sleeps,\n'
    '                  "polls": len(calls),\n'
    '                  "result": result}, sort_keys=True))\n')


_CUTOFF_HARNESS = (
    'import json\n'
    'from daedalus_cli import transport\n'
    'class _Clock:\n'
    '    def __init__(self):\n'
    '        self.now = 1000.0\n'
    '        self.sleeps = []\n'
    '    def monotonic(self):\n'
    '        return self.now\n'
    '    def sleep(self, seconds):\n'
    '        if seconds < 0:\n'
    '            raise ValueError("sleep length must be non-negative")\n'
    '        self.sleeps.append(seconds)\n'
    '        self.now += seconds\n'
    'transport.time = _Clock()\n'
    'calls = []\n'
    'def fake_api(method, path, body=None, timeout=None, headers=None):\n'
    '    calls.append(path)\n'
    '    raise transport.ConnectionFailed("cut off")\n'
    'transport._request = fake_api\n'
    'result = transport.wait_for_result(\n'
    '    "c1", "extension", "d1", %s, interval=%s)\n'
    'print(json.dumps({"sleeps": transport.time.sleeps,\n'
    '                  "polls": len(calls),\n'
    '                  "result": result}, sort_keys=True))\n')


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


def _cli_env():
    """Environment with the durable token selected for the subprocess."""
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    env.pop('TOKEN', None)
    env['DAEDALUS_TOKEN'] = TOK
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def test_result_wait_requires_nonempty_exact_delivery_ids(tmp):
    """Uncorrelated delivery IDs keep polling without consuming a result.

    The virtual clock stands in for time.sleep and records each
    interval the loop REQUESTS. The real-clock form demanded two polls,
    a wall-clock margin a busy runner could exhaust - the recorded leg
    reached no poll at all, failing a correct waiter one level below the
    intermittency that leg was recorded for.
    """
    del tmp
    run = subprocess.run(
        [sys.executable, '-c', _WAIT_HARNESS],
        cwd=str(_util.ROOT), env=_cli_env(), capture_output=True,
        text=True, encoding='utf-8', timeout=10)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    outcomes = json.loads(run.stdout)
    expected = (
        ('absent', '/result?tab=extension'),
        ('empty', '/result?tab=extension'),
        ('foreign', '/result?tab=extension&delivery=d1'),
    )
    assert len(outcomes) == len(expected), outcomes
    for outcome, (name, selector) in zip(outcomes, expected):
        assert outcome['name'] == name, outcome
        assert outcome['result'] is None, outcome
        assert outcome['consumes'] == [], outcome
        sleeps = outcome['sleeps']
        # Bounded failure message: a pathological record can be long.
        brief = {'name': outcome['name'], 'result': outcome['result'],
                 'peeks': len(outcome['peeks']), 'sleep_n': len(sleeps),
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
        assert len(outcome['peeks']) == len(sleeps) - 1, brief
        assert outcome['now'] == outcome['deadline'], brief
        assert set(outcome['peeks']) == {selector}, brief


def test_result_wait_rejects_receipt_for_different_generation(tmp):
    """Repeated wrong-generation receipts cannot claim the selected body."""
    del tmp
    run = subprocess.run(
        [sys.executable, '-c', _GENERATION_HARNESS],
        cwd=str(_util.ROOT), env=_cli_env(), capture_output=True,
        text=True, encoding='utf-8', timeout=10)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    outcome = json.loads(run.stdout)
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
        run = subprocess.run(
            [sys.executable, '-c', _BACKOFF_HARNESS % (timeout, interval)],
            cwd=str(_util.ROOT), env=_cli_env(), capture_output=True,
            text=True, encoding='utf-8', timeout=10)
        assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
        outcome = json.loads(run.stdout)
        sleeps = outcome['sleeps']
        # Bounded failure message: a pathological record can be long.
        brief = {'polls': outcome['polls'], 'result': outcome['result'],
                 'sleep_n': len(sleeps), 'sleep_head': sleeps[:6]}
        assert outcome['result'] is None, brief
        assert sleeps[:len(ramp)] == ramp, brief
        assert set(sleeps[len(ramp):-1]) == {interval}, brief
        # The last lap is cut to what is left of the budget, so the
        # requested record alone spends it exactly.
        assert abs(sum(sleeps) - timeout) < 1e-9, brief
        assert outcome['polls'] == len(sleeps) - 1, brief


def test_the_result_wait_expires_on_the_ramp_when_every_peek_fails(tmp):
    """Every peek failing still ends in None, on the ramp, exactly.

    ConnectionFailed from _request is what a proxy cut produces at
    transport level, whatever the HTTP shape of the cut. The virtual
    clock records the sleeps the loop REQUESTS and advances by nothing
    else, so the poll count and sleep list below are the ramp
    arithmetic itself — a loaded runner cannot starve them, which is
    the property the CLI-level pins could not have (issue 901).
    """
    del tmp
    run = subprocess.run(
        [sys.executable, '-c', _CUTOFF_HARNESS % (1.0, 0.5)],
        cwd=str(_util.ROOT), env=_cli_env(), capture_output=True,
        text=True, encoding='utf-8', timeout=10)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    outcome = json.loads(run.stdout)
    sleeps = outcome['sleeps']
    brief = {'polls': outcome['polls'], 'result': outcome['result'],
             'sleeps': sleeps}
    assert outcome['result'] is None, brief
    # Five ramp laps fit in 1.0s (0.62 spent); the sixth is cut to the
    # remainder, which spends the budget, so the loop expires before a
    # sixth peek.
    assert outcome['polls'] == 5, brief
    assert sleeps[:5] == [0.02, 0.04, 0.08, 0.16, 0.32], brief
    assert len(sleeps) == 6, brief
    assert 0.32 < sleeps[-1] < 0.5, brief
    assert abs(sum(sleeps) - 1.0) < 1e-9, brief


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
        cwd=str(_util.ROOT), env=_cli_env(), capture_output=True,
        text=True, encoding='utf-8', timeout=10)
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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
