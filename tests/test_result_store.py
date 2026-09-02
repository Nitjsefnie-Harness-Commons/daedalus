#!/usr/bin/env python3
"""Unit contract for result slots and delivery-id retention."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


_RESULT_STORE_PROBE = r'''
import json
import os
from pathlib import Path

from daedalus_bridge import result_store

slot = Path(os.environ['RESULT_SLOT'])
payload = {
    'id': 'unit-result',
    'result': {'value': 7},
    'resultGeneration': 'generation-1',
    'deliveryId': 'delivery-1',
}
result_store.atomic_result_write(
    slot, json.dumps(payload, ensure_ascii=False).encode('utf-8'))
peek, delivery = result_store.read_result_file(slot, False, '')
mismatch, mismatch_delivery = result_store.read_result_file(
    slot, True, 'generation-other')
mismatch_kept = slot.is_file()
consumed, consumed_delivery = result_store.read_result_file(
    slot, True, 'generation-1')
consumed_missing = not slot.exists()
before = result_store.delivery_recorded('delivery-1')
with result_store.result_lock:
    result_store.record_delivery('delivery-1')
after = result_store.delivery_recorded('delivery-1')
print(json.dumps({
    'peek': peek,
    'delivery': delivery,
    'mismatch': mismatch,
    'mismatch_delivery': mismatch_delivery,
    'mismatch_kept': mismatch_kept,
    'consumed': consumed,
    'consumed_delivery': consumed_delivery,
    'consumed_missing': consumed_missing,
    'recorded_before': before,
    'recorded_after': after,
}, sort_keys=True))
'''


_DELIVERY_PATH_PROBE = r'''
import contextlib
import io
import json
import os
from pathlib import Path

from daedalus_bridge import path_safety, result_store

res_dir = Path(os.environ['DAEDALUS_DIR']) / 'results'
delivery_root = result_store.delivery_root(res_dir)
key = result_store.result_key('tok', 'extension')
delivery_dir = delivery_root / key
delivery_file = delivery_dir / '123_1.json'
degraded_root = Path(os.environ['DAEDALUS_DIR']) / 'RESULT~1' / 'deliveries'
wrong_root = Path(os.environ['DAEDALUS_DIR']) / 'wrong' / 'deliveries'
realpath = path_safety.os.path.realpath


def call_with(answers):
    calls = []
    answers = iter(str(answer) for answer in answers)

    def resolving_stub(path):
        calls.append(os.fspath(path))
        return next(answers)

    path_safety.os.path.realpath = resolving_stub
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            paths = result_store.delivery_result_paths(
                res_dir, 'tok', 'extension', '123_1')
    finally:
        path_safety.os.path.realpath = realpath
    return paths, calls, output.getvalue()


paths, transient_calls, transient_log = call_with([
    delivery_root, delivery_dir,
    degraded_root, delivery_root,
    delivery_root, delivery_root,
    delivery_dir, delivery_file,
])
stable_calls = []
stable_log = io.StringIO()
answers = iter(str(answer) for answer in [
    delivery_root, delivery_dir,
    wrong_root, delivery_root,
    wrong_root, delivery_root,
])


def stable_stub(path):
    stable_calls.append(os.fspath(path))
    return next(answers)


path_safety.os.path.realpath = stable_stub
try:
    with contextlib.redirect_stdout(stable_log):
        try:
            result_store.delivery_result_paths(
                res_dir, 'tok', 'extension', '123_1')
        except ValueError:
            stable = 'refused'
        else:
            stable = 'allowed'
finally:
    path_safety.os.path.realpath = realpath

print('DELIVERY_PATH ' + json.dumps({
    'paths': [str(path) for path in paths],
    'transient_calls': transient_calls,
    'transient_log': transient_log,
    'stable': stable,
    'stable_calls': stable_calls,
    'stable_log': stable_log.getvalue(),
}))
'''


_UNCONFIGURED_PROBE = r'''
import json
import os
from pathlib import Path

from daedalus_bridge import result_store

root = Path(os.environ['THROWAWAY_ROOT'])
delivery_dir, delivery_file = result_store.delivery_result_paths(
    root, 'tok', 'extension', '123_1')
print('UNCONFIGURED ' + json.dumps({
    'daedalus_env': sorted(
        name for name in os.environ if name.startswith('DAEDALUS_')),
    'dir': str(delivery_dir),
    'file': str(delivery_file),
}))
'''


def test_result_store_owns_atomic_slots_and_delivery_dedup(tmp):
    root = Path(tmp) / 'result-store-root'
    slot = root / 'results' / 'unit.json'
    slot.parent.mkdir(parents=True)
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(root),
        'DAEDALUS_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
        'RESULT_SLOT': str(slot),
    })
    proc = subprocess.run(
        [sys.executable, '-c', _RESULT_STORE_PROBE],
        cwd=_util.ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    answer = json.loads(proc.stdout)
    assert answer == {
        'peek': {
            'deliveryId': 'delivery-1',
            'id': 'unit-result',
            'result': {'value': 7},
            'resultGeneration': 'generation-1',
        },
        'delivery': '',
        'mismatch': {'consumed': False},
        'mismatch_delivery': '',
        'mismatch_kept': True,
        'consumed': {
            'consumed': True,
            'resultGeneration': 'generation-1',
        },
        'consumed_delivery': 'delivery-1',
        'consumed_missing': True,
        'recorded_before': False,
        'recorded_after': True,
    }, answer


def test_delivery_paths_use_the_retrying_parent_comparison(tmp):
    """The real delivery caller retries a degraded parent spelling."""
    docroot = Path(tmp) / 'docroot'
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(docroot),
        'DAEDALUS_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    proc = subprocess.run(
        [sys.executable, '-c', _DELIVERY_PATH_PROBE],
        cwd=_util.ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    marked = [line for line in proc.stdout.splitlines()
              if line.startswith('DELIVERY_PATH ')]
    assert len(marked) == 1, (proc.stdout, proc.stderr)
    answer = json.loads(marked[0][len('DELIVERY_PATH '):])
    delivery_root = docroot / 'results' / 'deliveries'
    delivery_dir = delivery_root / 'tok_extension'
    delivery_file = delivery_dir / '123_1.json'
    wrong_root = docroot / 'wrong' / 'deliveries'
    assert answer['paths'] == [str(delivery_dir), str(delivery_file)], answer
    assert answer['transient_calls'] == [
        str(delivery_root), str(delivery_dir),
        str(delivery_root), str(delivery_root),
        str(delivery_root), str(delivery_root),
        str(delivery_dir), str(delivery_file),
    ], answer
    assert answer['transient_log'] == '', answer
    assert answer['stable'] == 'refused', answer
    assert answer['stable_calls'] == [
        str(delivery_root), str(delivery_dir),
        str(delivery_root), str(delivery_root),
        str(delivery_root), str(delivery_root),
    ], answer
    stable_lines = answer['stable_log'].splitlines()
    assert len(stable_lines) == 1, answer
    assert stable_lines[0].startswith('[PATH-REFUSAL] kind=alias '), answer
    assert f'root={str(delivery_root)!r}' in stable_lines[0], answer
    assert "parts=('tok_extension',)" in stable_lines[0], answer
    stable_attempts = (
        (str(wrong_root), str(delivery_root)),
        (str(wrong_root), str(delivery_root)),
    )
    assert f'attempts={stable_attempts!r}' in stable_lines[0], answer


def test_the_store_needs_no_bridge_configuration(tmp):
    """A results root is a parameter, so no `DAEDALUS_*` value is needed.

    Importing `daedalus_bridge.config` with those stripped raises
    `SystemExit`, so a store that still read it could not run here at all.
    """
    root = Path(tmp) / 'unconfigured' / 'results'
    root.mkdir(parents=True)
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    env.update({
        'PYTHONDONTWRITEBYTECODE': '1',
        'THROWAWAY_ROOT': str(root),
    })
    proc = subprocess.run(
        [sys.executable, '-c', _UNCONFIGURED_PROBE],
        cwd=_util.ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    marked = [line for line in proc.stdout.splitlines()
              if line.startswith('UNCONFIGURED ')]
    assert len(marked) == 1, (proc.stdout, proc.stderr)
    answer = json.loads(marked[0][len('UNCONFIGURED '):])
    assert answer['daedalus_env'] == [], answer
    # Resolved on both sides: `under` returns the path it checked.
    expected_dir = os.path.realpath(root / 'deliveries' / 'tok_extension')
    assert answer['dir'] == expected_dir, (answer, expected_dir)
    expected_file = os.path.realpath(
        root / 'deliveries' / 'tok_extension' / '123_1.json')
    assert answer['file'] == expected_file, (answer, expected_file)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='result_store_')


if __name__ == '__main__':
    raise SystemExit(main())
