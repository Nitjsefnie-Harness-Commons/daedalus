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

_PROBE = r'''
import io
import json
import os
import time
import types
from pathlib import Path

from daedalus_bridge import command_queue, exec_timing, result_routes
from daedalus_bridge import stream_service

root = Path(os.environ['DAEDALUS_DIR'])
cmd_dir, res_dir = root / 'commands', root / 'results'
calls = []
clock_is_time_ns = exec_timing._clock is time.time_ns
exec_timing._clock = lambda: calls.append(1) or len(calls) * 1_000_000_999
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
    return {'did': did, 'file': on_disk, 'frame': frames.getvalue().decode(),
            'slot': slot, 'cmd': json.dumps(cmd)}


out = {'eval': run({'id': 'e', 'code': '1'}, 'a')}
out['typed'] = run({'id': 't', 'type': 'ping'}, 'b')
out['client'] = run({'id': 'c', 'code': '1', '_timing': {}}, 'c')
out['clock_calls'] = len(calls)
out['clock_is_time_ns'] = clock_is_time_ns
print(json.dumps(out))
'''


def _run(tmp, script, flag):
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    env.update({'DAEDALUS_DIR': str(Path(tmp) / 'root'),
                'DAEDALUS_PORT': '0', 'PYTHONDONTWRITEBYTECODE': '1'})
    if flag is not None:
        env['DAEDALUS_DEBUG_TIMING'] = flag
    proc = subprocess.run(
        [sys.executable, '-c', script], cwd=_util.ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    return proc.stdout.splitlines()[-1]


def _probe(tmp, flag):
    return json.loads(_run(tmp, _PROBE, flag))


def test_b_flag_on_stamps_each_stage_of_an_eval_only(tmp):
    out = _probe(tmp, '1')
    assert out['clock_is_time_ns'], out
    queued = json.loads(out['eval']['file'])['_timing']
    framed = json.loads(out['eval']['frame'].split('data: ')[1])['_timing']
    stored = json.loads(out['eval']['slot'])['_timing']
    # The probe clock returns n * 1_000_000_999 ns on its nth read, so the
    # exact values pin the microsecond unit and the floor.
    assert queued == {'srv_enqueue_us': 1000000}, queued
    assert framed == {'srv_enqueue_us': 1000000,
                      'srv_write_us': 2000001}, framed
    assert stored == {'srv_enqueue_us': 1000000, 'srv_write_us': 2000001,
                      'srv_result_us': 3000002}, stored
    for stage in ('file', 'frame', 'slot'):
        assert '_timing' not in out['typed'][stage], (stage, out['typed'])


def test_c_flag_off_reads_no_clock_and_bytes_equal_the_base(tmp):
    cases = {'eval': ('e', 'a', '', ''),
             'client': ('c', 'c', ', "_timing": {}', ' "_timing": {},')}
    for flag in (None, '0', 'true'):
        out = _probe(tmp, flag)
        assert out['clock_calls'] == 0, (flag, out)
        for name, (ident, tab, field, slot_field) in cases.items():
            got, did = out[name], out[name]['did']
            body = '"id": "%s", "code": "1"%s' % (ident, field)
            queued = '{%s, "_did": "%s"}' % (body, did)
            assert got['file'] == queued, (flag, got['file'])
            assert got['frame'] == (
                f'event: command\ndata: {queued}\n\n'), (flag, got['frame'])
            assert got['slot'] == (
                '{"id": "%s", "tabId": "%s", "ok": true,%s '
                '"resultGeneration": "%s", "deliveryId": "%s", '
                '"roundtrip_ms": 999}' % (
                    ident, tab, slot_field, 'g' * 32, did)), (
                        flag, got['slot'])
            assert got['cmd'] == '{%s}' % body, (flag, got['cmd'])


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
            again = put_command(base, body)[1] if flag == '1' else None
        if flag == '1':
            command = json.loads(raw)['command']
            assert list(command['_timing']) == ['srv_enqueue_us'], raw
            assert json.loads(stored) == command, (stored, raw)
            retry = json.loads(again)
            assert retry['duplicate'] is True, again
            assert retry['command'] == command, (again, stored)
            continue
        command = '{"id": "e", "code": "1", "_did": "%s"}' % did
        assert stored == command.encode(), (flag, stored)
        assert raw == (
            '{"ok": true, "target": "broadcast", "did": "%s", '
            '"command": %s}' % (did, command)).encode(), (flag, raw)


def test_e_a_retry_coalesces_as_the_flag_leaves_it(tmp):
    script = (
        'import os\nfrom pathlib import Path\n'
        'from daedalus_bridge import command_queue\n'
        'def put(extra):\n'
        '    return command_queue.enqueue(\n'
        '        Path(os.environ["DAEDALUS_DIR"]), "tok", "t",\n'
        '        {"id": "e", "code": "1", **extra}, command_ttl=90)\n'
        'first = put(EXTRA)\n'
        'print(first[1], put(EXTRA)[1])\n')
    assert _run(tmp, script.replace('EXTRA', '{}'), '1') == 'False True'
    # Flag off, a body carrying its own _timing is an ordinary field.
    assert _run(tmp, script.replace('EXTRA', '{"_timing": {}}'),
                None) == 'False True'


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='exec_timing_')


if __name__ == '__main__':
    raise SystemExit(main())
