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
    '    def fake_api(method, path, body=None, timeout=None):\n'
    '        calls.append(path)\n'
    '        if "consume=1" in path:\n'
    '            return {"consumed": True, "resultGeneration": "g1"}\n'
    '        return result_body\n'
    '    transport.api = fake_api\n'
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
    'def fake_api(method, path, body=None, timeout=None):\n'
    '    calls.append(path)\n'
    '    if "consume=1" in path:\n'
    '        return {"consumed": True, "resultGeneration": "g2"}\n'
    '    return result_body\n'
    'transport.api = fake_api\n'
    'result = transport.wait_for_result(\n'
    '    "c1", "extension", "d1", 2.0, interval=0.01)\n'
    'print(json.dumps({"result": result, "calls": calls},\n'
    '                 sort_keys=True))\n')


def _cli_env():
    """Environment with the durable token selected for the subprocess."""
    env = dict(os.environ)
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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
