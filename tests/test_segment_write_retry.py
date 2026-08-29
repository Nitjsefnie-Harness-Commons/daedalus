#!/usr/bin/env python3
"""A transient sharing violation on a segment-family write is retried.

The atomic replace of a temp file already retried one; the write that
produces the temp did not, so a single PermissionError discarded the
segment. One refusal is injected at each segment-family write, in a real
bridge, through the sitecustomize channel the other storage tests use.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _segments import TOK, mint_job, post_segment, seg_job  # noqa: E402

# The sharing violation Windows reports when another process holds the
# handle: the same shape the replace retry already treats as transient.
_REFUSAL = 'PermissionError(32, "The process cannot access the file")'

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


def _bridge_with(tmp, body, extra_env=None):
    """A bridge whose child raises one injected PermissionError."""
    fault_dir = _write_fault_dir(tmp, body)
    env = {'PYTHONPATH': str(fault_dir)}
    if extra_env:
        env.update(extra_env)
    return _util.bridge(tmp, env=env)


def test_segment_temp_write_retries_transient_refusal(tmp):
    """One refused temp write retries, and the segment still lands."""
    with _bridge_with(tmp, (
            '_refused = [True]\n'
            'def _refuse_one_temp_write(path, data):\n'
            '    if path.name.endswith(".ts.tmp") and _refused[0]:\n'
            '        _refused[0] = False\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_bytes(path, data)\n'
            'pathlib.Path.write_bytes = _refuse_one_temp_write\n')) as (
            base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status0, body0 = post_segment(base, job, sig, '0', payload=b'abc')
        assert status0 == 200, (status0, body0)
        status1, body1 = post_segment(base, job, sig, '1', payload=b'de')
        assert status1 == 200, (status1, body1)
        seg_dir = Path(docroot) / 'segments' / job
        assert sorted(path.name for path in seg_dir.glob('*.ts')) == [
            '000000.ts', '000001.ts']
        assert (seg_dir / '000000.ts').read_bytes() == b'abc'
        assert (seg_dir / '000001.ts').read_bytes() == b'de'
        assert list(seg_dir.glob('*.tmp')) == [], list(seg_dir.glob('*.tmp'))


def test_an_exhausted_write_retry_still_answers_500(tmp):
    """A refusal that never clears is not retried forever: 500, no residue."""
    with _bridge_with(tmp, (
            'def _refuse_every_temp_write(path, data):\n'
            '    if path.name.endswith(".ts.tmp"):\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_bytes(path, data)\n'
            'pathlib.Path.write_bytes = _refuse_every_temp_write\n')) as (
            base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 500, (status, body)
        seg_dir = Path(docroot) / 'segments' / job
        assert not list(seg_dir.glob('*.ts')), list(seg_dir.iterdir())
        assert list(seg_dir.glob('*.tmp')) == [], list(seg_dir.iterdir())
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert record['stored_count'] == 0, record
        dirty_path = Path(docroot) / 'segments' / f'.{job}.json.dirty'
        assert dirty_path.is_file(), 'refused publish must leave its mark'


def test_mint_record_write_retries_transient_refusal(tmp):
    """One refused record write retries, and the mint still succeeds."""
    with _bridge_with(tmp, (
            '_refused = [True]\n'
            'def _refuse_one_record_write(path, data, **kw):\n'
            '    if path.name.endswith(".json.tmp") and _refused[0]:\n'
            '        _refused[0] = False\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_text(path, data, **kw)\n'
            'pathlib.Path.write_text = _refuse_one_record_write\n')) as (
            base, docroot):
        job = seg_job()
        status, body = mint_job(base, TOK, job)
        assert status == 200 and body.get('sig'), (status, body)
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert record['sig'] == body['sig'], (record, body)


def test_legacy_mint_record_write_retries_transient_refusal(tmp):
    """The same retry covers the legacy-record upgrade write on a resume."""
    with _bridge_with(tmp, (
            '_refused = [True]\n'
            'def _refuse_one_record_write(path, data, **kw):\n'
            '    if path.name.endswith(".json.tmp") and _refused[0]:\n'
            '        _refused[0] = False\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_text(path, data, **kw)\n'
            'pathlib.Path.write_text = _refuse_one_record_write\n'), {
            'DAEDALUS_MAX_SEGMENT_INDEX': '10',
            'DAEDALUS_MAX_SEGMENTS_PER_JOB': '3',
            'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5'}) as (base, docroot):
        job = seg_job()
        sig = 'legacy-capability'
        seg_root = Path(docroot) / 'segments'
        seg_dir = seg_root / job
        seg_dir.mkdir()
        (seg_dir / '000000.ts').write_bytes(b'abc')
        record_path = seg_root / f'{job}.json'
        record_path.write_text(json.dumps({'token': TOK, 'sig': sig}))
        status, body = mint_job(base, TOK, job)
        assert status == 200 and body['sig'] == sig, (status, body)
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert record['max_segment_index'] == 10, record


def test_usage_record_write_retries_transient_refusal(tmp):
    """One refused write_usage record write retries; the totals still land."""
    with _bridge_with(tmp, (
            '_usage_writes = [0]\n'
            'def _refuse_second_record_write(path, data, **kw):\n'
            '    if path.name.endswith(".json.tmp"):\n'
            '        _usage_writes[0] += 1\n'
            '        if _usage_writes[0] == 2:\n'
            f'            raise {_REFUSAL}\n'
            '    return _real_write_text(path, data, **kw)\n'
            'pathlib.Path.write_text = _refuse_second_record_write\n')) as (
            base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 200, (status, body)
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert (record['stored_count'], record['stored_bytes']) == (1, 3), \
            record


def test_dirty_marker_write_retries_transient_refusal(tmp):
    """One refused marker write retries; the segment is not refused with it."""
    with _bridge_with(tmp, (
            '_refused = [True]\n'
            'def _refuse_one_marker_write(path, data, **kw):\n'
            '    if str(path).endswith(".dirty") and _refused[0]:\n'
            '        _refused[0] = False\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_text(path, data, **kw)\n'
            'pathlib.Path.write_text = _refuse_one_marker_write\n')) as (
            base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 200, (status, body)
        seg_dir = Path(docroot) / 'segments' / job
        assert (seg_dir / '000000.ts').read_bytes() == b'abc'
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert (record['stored_count'], record['stored_bytes']) == (1, 3), \
            record


def test_an_exhausted_marker_refusal_refuses_the_segment(tmp):
    """A marker that can never land publishes nothing: 500, nothing stored."""
    with _bridge_with(tmp, (
            'def _refuse_every_marker_write(path, data, **kw):\n'
            '    if str(path).endswith(".dirty"):\n'
            f'        raise {_REFUSAL}\n'
            '    return _real_write_text(path, data, **kw)\n'
            'pathlib.Path.write_text = _refuse_every_marker_write\n')) as (
            base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 500, (status, body)
        seg_dir = Path(docroot) / 'segments' / job
        assert list(seg_dir.glob('*.ts')) == [], list(seg_dir.glob('*.ts'))
        assert list(seg_dir.glob('*.tmp')) == [], list(seg_dir.glob('*.tmp'))
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert (record['stored_count'], record['stored_bytes']) == (0, 0), \
            record
        marker = Path(docroot) / 'segments' / f'.{job}.json.dirty'
        assert not marker.exists(), 'a refused mark left a marker behind'


def test_a_non_permission_error_is_not_retried(tmp):
    """A refusal outside the transient class fails fast: one attempt only."""
    with _bridge_with(tmp, (
            'import os\n'
            '_log = pathlib.Path(os.environ["DAEDALUS_DIR"], "refusals.txt")\n'
            'def _refuse_every_temp_write(path, data):\n'
            '    if path.name.endswith(".ts.tmp"):\n'
            '        with _log.open("a", encoding="utf-8") as handle:\n'
            '            handle.write("refused\\n")\n'
            '        raise OSError("injected permanent failure")\n'
            '    return _real_write_bytes(path, data)\n'
            'pathlib.Path.write_bytes = _refuse_every_temp_write\n')) as (
            base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 500, (status, body)
        refusals = Path(docroot) / 'refusals.txt'
        assert refusals.read_text(encoding='utf-8') == 'refused\n'
        seg_dir = Path(docroot) / 'segments' / job
        assert list(seg_dir.glob('*.ts')) == [], list(seg_dir.glob('*.ts'))
        assert list(seg_dir.glob('*.tmp')) == [], list(seg_dir.glob('*.tmp'))


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='segretry_')


if __name__ == '__main__':
    sys.exit(main())
