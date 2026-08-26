#!/usr/bin/env python3
"""Unit contract for HLS segment records and quota accounting."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


_SEGMENT_STORE_PROBE = r'''
import json
from pathlib import Path

import segment_store

job = 'unit-job'
record_path = segment_store.record_path(job)
record_path.parent.mkdir(parents=True)
record_path.write_text(json.dumps({
    'token': 'unit-token',
    'sig': 'sig-good',
    'max_segment_index': 99,
    'max_segment_count': 7,
    'max_bytes': 4096,
}), encoding='utf-8')
loaded = segment_store.load_record(job)
matching = segment_store.record_for_sig(job, 'sig-good')
wrong = segment_store.record_for_sig(job, 'sig-wrong')
quota = segment_store.quota(loaded)
usage_before = segment_store.usage(loaded)
job_dir = Path(record_path).with_suffix('')
job_dir.mkdir()
(job_dir / '000001.ts').write_bytes(b'abcd')
recounted = segment_store.recount_segments(job_dir)
segment_store.write_usage(job, *recounted)
updated = segment_store.load_record(job)
print(json.dumps({
    'loaded_sig': loaded['sig'],
    'matching_token': matching['token'],
    'wrong': wrong,
    'quota': quota,
    'usage_before': usage_before,
    'recounted': recounted,
    'usage_after': segment_store.usage(updated),
}, sort_keys=True))
'''


def test_segment_store_owns_record_and_quota_accounting(tmp):
    root = Path(tmp) / 'segment-store-root'
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(root),
        'DAEDALUS_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    proc = subprocess.run(
        [sys.executable, '-c', _SEGMENT_STORE_PROBE],
        cwd=_util.ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    answer = json.loads(proc.stdout)
    assert answer == {
        'loaded_sig': 'sig-good',
        'matching_token': 'unit-token',
        'wrong': None,
        'quota': [99, 7, 4096],
        'usage_before': None,
        'recounted': [1, 4],
        'usage_after': [1, 4],
    }, answer


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='segment_store_')


if __name__ == '__main__':
    raise SystemExit(main())
