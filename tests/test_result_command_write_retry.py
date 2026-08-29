#!/usr/bin/env python3
"""A transient sharing violation on a result or command temp write is retried.

The atomic replace of those temp files already retried one; the writes that
produce them did not. One refusal is injected per site, in a real bridge,
through the sitecustomize channel the other storage tests use.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import TOK, put_command  # noqa: E402

# The sharing violation Windows reports when another process holds the
# handle: the same shape the replace retry already treats as transient.
_REFUSAL = 'PermissionError(32, "The process cannot access the file")'

_QUEUE = f'{TOK}_tab1'

_HEADER = (
    'import pathlib\n'
    '_real_write_bytes = pathlib.Path.write_bytes\n'
    '_real_write_text = pathlib.Path.write_text\n'
)


def _write_fault_dir(tmp, body):
    fault_dir = Path(tmp) / 'fault-injection'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        _HEADER + body, encoding='utf-8')
    return fault_dir


def _bridge_with(tmp, body):
    """A bridge whose child raises one injected PermissionError."""
    fault_dir = _write_fault_dir(tmp, body)
    return _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)})


def test_result_temp_write_retries_transient_refusal(tmp):
    """One refused result temp write retries, and the result still lands."""
    with _bridge_with(tmp, (
            '_refused = [True]\n'
            'def _refuse_one_result_write(path, data):\n'
            '    if (path.name.startswith(".result-")\n'
            '            and path.name.endswith(".tmp")\n'
            '            and _refused[0]):\n'
            '        _refused[0] = False\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_bytes(path, data)\n'
            'pathlib.Path.write_bytes = _refuse_one_result_write\n')) as (
            base, docroot):
        res = {'token': TOK, 'id': 'r1', 'result': {'v': 1},
               'error': None, 'ts': 1}
        status, body = _util.post_json(base + '/result', res)
        assert status == 200 and body == {'ok': True}, (status, body)
        results_dir = Path(docroot) / 'results'
        stored = json.loads(
            (results_dir / f'{TOK}.json').read_text(encoding='utf-8'))
        assert stored['id'] == 'r1' and stored['result'] == {'v': 1}, stored
        assert list(results_dir.glob('.result-*.tmp')) == [], \
            list(results_dir.glob('.result-*.tmp'))


def test_an_exhausted_result_write_still_answers_500(tmp):
    """A refusal that never clears is not retried forever: 500, no residue."""
    with _bridge_with(tmp, (
            'def _refuse_every_result_write(path, data):\n'
            '    if (path.name.startswith(".result-")\n'
            '            and path.name.endswith(".tmp")):\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_bytes(path, data)\n'
            'pathlib.Path.write_bytes = _refuse_every_result_write\n')) as (
            base, docroot):
        res = {'token': TOK, 'id': 'r1', 'result': {'v': 1},
               'error': None, 'ts': 1}
        status, body = _util.post_json(base + '/result', res)
        assert status == 500, (status, body)
        assert body == {'error': 'result storage failure'}, body
        results_dir = Path(docroot) / 'results'
        assert list(results_dir.glob('.result-*.tmp')) == [], \
            list(results_dir.iterdir())
        assert not (results_dir / f'{TOK}.json').exists(), \
            'a refused result must not be stored'


def test_command_temp_write_retries_transient_refusal(tmp):
    """One refused command temp write retries, and the command still queues."""
    with _bridge_with(tmp, (
            f'_queue = "{_QUEUE}"\n'
            '_refused = [True]\n'
            'def _refuse_one_command_write(path, data, **kw):\n'
            '    if (path.parent.name == _queue\n'
            '            and path.name.startswith(".")\n'
            '            and path.name.endswith(".tmp")\n'
            '            and _refused[0]):\n'
            '        _refused[0] = False\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_text(path, data, **kw)\n'
            'pathlib.Path.write_text = _refuse_one_command_write\n')) as (
            base, docroot):
        status, raw = put_command(
            base, {'token': TOK, 'tab': 'tab1', 'id': 'c1', 'code': '1'})
        body = json.loads(raw)
        assert status == 200 and body.get('ok') is True, (status, body)
        qdir = Path(docroot) / 'commands' / _QUEUE
        queued = json.loads(
            (qdir / f"{body['did']}.json").read_text(encoding='utf-8'))
        assert queued['id'] == 'c1' and queued['code'] == '1', queued
        assert queued['_did'] == body['did'], (queued, body)
        assert list(qdir.glob('.*.tmp')) == [], list(qdir.glob('.*.tmp'))


def test_an_exhausted_command_write_still_answers_500(tmp):
    """A refusal that never clears is not retried forever: 500, no residue."""
    with _bridge_with(tmp, (
            f'_queue = "{_QUEUE}"\n'
            'def _refuse_every_command_write(path, data, **kw):\n'
            '    if (path.parent.name == _queue\n'
            '            and path.name.startswith(".")\n'
            '            and path.name.endswith(".tmp")):\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_text(path, data, **kw)\n'
            'pathlib.Path.write_text = _refuse_every_command_write\n')) as (
            base, docroot):
        status, raw = put_command(
            base, {'token': TOK, 'tab': 'tab1', 'id': 'c1', 'code': '1'})
        assert status == 500, (status, raw)
        assert json.loads(raw) == {'error': 'command storage failure'}, raw
        qdir = Path(docroot) / 'commands' / _QUEUE
        assert qdir.is_dir(), 'the queue directory itself must survive'
        assert list(qdir.iterdir()) == [], list(qdir.iterdir())


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='writeretry_')


if __name__ == '__main__':
    sys.exit(main())
