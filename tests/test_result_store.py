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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='result_store_')


if __name__ == '__main__':
    raise SystemExit(main())
