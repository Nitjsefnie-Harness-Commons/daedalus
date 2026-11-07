#!/usr/bin/env python3
"""Bridge-side exec timing stamps: `_timing` rides an eval command only while
`DAEDALUS_DEBUG_TIMING=1`, and every other value leaves the bytes untouched.

The in-process probes run in a child so the flag, read once at import, is
whatever the arm sets; `exec_timing._clock` is counted there, which is what
"no stamp is taken" means.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import BRIDGE_ENV, TOK, put_command  # noqa: E402

SEQ = '0000000000001_00000000000000000001'
_PROBE = r'''
import io
import json
import os
import types
from pathlib import Path

from daedalus_bridge import command_queue, exec_timing, result_routes
from daedalus_bridge import stream_service

root = Path(os.environ['DAEDALUS_DIR'])
cmd_dir, res_dir = root / 'commands', root / 'results'
calls = []
real_clock = exec_timing._clock
exec_timing._clock = lambda: calls.append(1) or real_clock()
seqs = iter(['0000000000001_%020d' % n for n in range(1, 9)])
command_queue.next_seq = lambda _dir: next(seqs)
result_routes.uuid = types.SimpleNamespace(
    uuid4=lambda: types.SimpleNamespace(hex='g' * 32))
result_routes.time = types.SimpleNamespace(time=lambda: 1.0)


def run(cmd, tab):
    did, _ = command_queue.enqueue(
        cmd_dir, 'tok', tab, cmd, command_ttl=90)
    queued = next(Path(cmd_dir / ('tok_' + tab)).glob('*.json'))
    on_disk = queued.read_text(encoding='utf-8')
    frames = io.BytesIO()
    stream_service.drain_queue(
        queued.parent, None, None, command_ttl=90,
        frame_writer=lambda data: stream_service.write_frame(frames, data))
    posted = {'id': cmd['id'], 'tabId': tab, '_did': did, 'ok': True}
    framed = json.loads(frames.getvalue().split(b'data: ')[1])
    if '_timing' in framed:
        posted['_timing'] = framed['_timing']
    result_routes.accept_result(res_dir, cmd_dir, 'tok', posted, 8)
    slot = (res_dir / ('tok_' + tab + '.json')).read_text(encoding='utf-8')
    return {'file': on_disk, 'frame': frames.getvalue().decode(),
            'slot': slot, 'cmd': json.dumps(cmd)}


out = {'eval': run({'id': 'e', 'code': '1'}, 'a')}
out['eval_clock_calls'] = len(calls)
out['typed'] = run({'id': 't', 'type': 'ping'}, 'b')
print(json.dumps(out))
'''


def _probe(tmp, flag):
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    env.update({'DAEDALUS_DIR': str(Path(tmp) / 'root'),
                'DAEDALUS_PORT': '0', 'PYTHONDONTWRITEBYTECODE': '1'})
    if flag is not None:
        env['DAEDALUS_DEBUG_TIMING'] = flag
    proc = subprocess.run(
        [sys.executable, '-c', _PROBE], cwd=_util.ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    return json.loads(proc.stdout.splitlines()[-1])


def test_a_flag_off_takes_no_stamp_and_adds_no_key(tmp):
    for flag in (None, '0', 'true'):
        out = _probe(tmp, flag)
        assert out['eval_clock_calls'] == 0, (flag, out)
        for stage in ('file', 'frame', 'slot'):
            assert '_timing' not in out['eval'][stage], (flag, stage, out)


def test_b_flag_on_stamps_each_stage_of_an_eval_only(tmp):
    out = _probe(tmp, '1')
    assert out['eval_clock_calls'] > 0, out
    queued = json.loads(out['eval']['file'])['_timing']
    framed = json.loads(out['eval']['frame'].split('data: ')[1])['_timing']
    stored = json.loads(out['eval']['slot'])['_timing']
    assert list(queued) == ['srv_enqueue_us'], queued
    assert list(framed) == ['srv_enqueue_us', 'srv_write_us'], framed
    assert list(stored) == [
        'srv_enqueue_us', 'srv_write_us', 'srv_result_us'], stored
    assert all(isinstance(v, int) for v in stored.values()), stored
    for stage in ('file', 'frame', 'slot'):
        assert '_timing' not in out['typed'][stage], (stage, out['typed'])


def test_c_flag_off_bytes_equal_the_base_literals(tmp):
    base_cmd = '{"id": "e", "code": "1", "_did": "%s"}' % SEQ
    for flag in (None, '0', 'true'):
        got = _probe(tmp, flag)['eval']
        assert got['file'] == base_cmd, (flag, got['file'])
        assert got['frame'] == (
            f'event: command\ndata: {base_cmd}\n\n'), (flag, got['frame'])
        assert got['slot'] == (
            '{"id": "e", "tabId": "a", "ok": true, '
            '"resultGeneration": "%s", "deliveryId": "%s", '
            '"roundtrip_ms": 999}' % ('g' * 32, SEQ)), (flag, got['slot'])
        assert got['cmd'] == '{"id": "e", "code": "1"}', (flag, got['cmd'])


def test_d_put_answer_and_queue_file_follow_the_flag(tmp):
    body = {'token': TOK, 'id': 'e', 'code': '1'}
    for flag in (None, '0', 'true', '1'):
        env = dict(BRIDGE_ENV)
        if flag is not None:
            env['DAEDALUS_DEBUG_TIMING'] = flag
        arm = os.path.join(tmp, flag or 'unset')
        with _util.bridge(arm, env=env) as (base, docroot):
            status, raw = put_command(base, body)
            assert status == 200, (flag, status, raw)
            did = json.loads(raw)['did']
            queued = Path(docroot) / 'commands' / TOK / f'{did}.json'
            stored = queued.read_bytes()
        if flag == '1':
            command = json.loads(raw)['command']
            assert list(command['_timing']) == ['srv_enqueue_us'], raw
            assert json.loads(stored) == command, (stored, raw)
            continue
        command = '{"id": "e", "code": "1", "_did": "%s"}' % did
        assert stored == command.encode(), (flag, stored)
        assert raw == (
            '{"ok": true, "target": "broadcast", "did": "%s", '
            '"command": %s}' % (did, command)).encode(), (flag, raw)


def test_e_a_retry_with_a_new_stamp_still_coalesces(tmp):
    script = (
        'import os\nfrom pathlib import Path\n'
        'from daedalus_bridge import command_queue\n'
        'd = Path(os.environ["DAEDALUS_DIR"])\n'
        'a = command_queue.enqueue(d, "tok", "t", {"id": "e", "code": "1"},'
        ' command_ttl=90)\n'
        'b = command_queue.enqueue(d, "tok", "t", {"id": "e", "code": "1"},'
        ' command_ttl=90)\n'
        'print(a[1], b[1], a[0] == b[0])\n')
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    env.update({'DAEDALUS_DIR': str(Path(tmp) / 'root'),
                'DAEDALUS_DEBUG_TIMING': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    proc = subprocess.run(
        [sys.executable, '-c', script], cwd=_util.ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    assert proc.stdout == 'False True True\n', (proc.stdout, proc.stderr)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='exec_timing_')


if __name__ == '__main__':
    raise SystemExit(main())
