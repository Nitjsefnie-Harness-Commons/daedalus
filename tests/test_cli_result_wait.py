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
    'cases = (\n'
    '    ("absent", None, {}),\n'
    '    ("empty", "", {"deliveryId": ""}),\n'
    '    ("foreign", "d1", {"deliveryId": "D1"}),\n'
    ')\n'
    'outcomes = []\n'
    'for name, sent_id, delivery_field in cases:\n'
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


def _cli_env():
    """Environment with the durable token selected for the subprocess."""
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    env.pop('TOKEN', None)
    env['DAEDALUS_TOKEN'] = TOK
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def test_result_wait_requires_nonempty_exact_delivery_ids(tmp):
    """Uncorrelated delivery IDs keep polling without consuming a result."""
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
        assert len(outcome['peeks']) >= 2, outcome
        assert set(outcome['peeks']) == {selector}, outcome


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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
