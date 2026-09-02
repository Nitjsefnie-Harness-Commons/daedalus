#!/usr/bin/env python3
"""Where a segment lands, and the quotas its job records at mint time.

Every accepted write is accounted against the running totals in the job
record rather than against a fresh count of the directory, so these tests
drive the totals through writes, overwrites and failures, and pin each of the
three quotas — index, count and bytes — at the boundary the mint recorded.
"""
import concurrent.futures
import http.client
import json
import os
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _segments import (TMP_SEG_ROOT, TOK, mint_job, post_segment,  # noqa: E402
                       seg_job)

# `[SEGMENT-TIMING] <job> stored=<n> <phase>=<ms>... parts=<ms> total=<ms>`
TIMING_LINE = re.compile(
    r'\[SEGMENT-TIMING\] (?P<job>\S+) stored=(?P<stored>\d+) '
    r'(?P<phases>acquire=[\d.]+ usage=[\d.]+ replaced=[\d.]+ '
    r'write=[\d.]+ record=[\d.]+) '
    r'parts=(?P<parts>[\d.]+) total=(?P<total>[\d.]+)')


def test_legacy_segment_job_migrates_with_existing_usage(tmp):
    """An owner re-mint upgrades a legacy record and counts stored segments."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '3',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        sig = 'legacy-capability'
        seg_root = Path(docroot) / 'segments'
        seg_dir = seg_root / job
        seg_dir.mkdir()
        (seg_dir / '000000.ts').write_bytes(b'abc')
        record_path = seg_root / f'{job}.json'
        legacy = {'token': TOK, 'sig': sig}
        record_path.write_text(json.dumps(legacy))

        status, body = mint_job(base, 'othertok', job)
        assert status == 401 and json.loads(record_path.read_text(encoding='utf-8')) == legacy, (
            status, body)

        status, body = mint_job(base, TOK, job)
        assert status == 200 and body['sig'] == sig, (status, body)
        # Exact equality on purpose: the migration must add what it needs and
        # nothing else. The totals are seeded from the count this branch
        # already made, so the first segment write does not have to recount.
        assert json.loads(record_path.read_text(encoding='utf-8')) == {
            'token': TOK,
            'sig': sig,
            'max_segment_index': 10,
            'max_segment_count': 3,
            'max_bytes': 5,
            'stored_count': 1,
            'stored_bytes': 3,
        }

        status, body = post_segment(base, job, sig, '1', payload=b'de')
        assert status == 200, (status, body)
        status, body = post_segment(base, job, sig, '2', payload=b'x')
        assert status == 413, (status, body)
        assert sorted(path.read_bytes() for path in seg_dir.glob('*.ts')) == [
            b'abc', b'de']


def test_a_segment_write_reads_its_totals_instead_of_recounting(tmp):
    """The write path trusts the record, and never counts the directory.

    Admission used to scan the job directory twice and stat every stored
    segment on every valid POST, so a job approaching its cap made each new
    segment cost more than the last -- 6.6 requests/s at 5,000 stored against
    110 on an empty job. The totals now live in the record.

    Pinned without a clock, because a timing assertion decides differently on
    a loaded runner. The record here claims the job is full while its
    directory is empty: a handler that recounts sees nothing stored and
    admits the write, and one that reads the record refuses it. The two
    answers are opposite, so the mechanism is what is being measured.
    """
    env = {'DAEDALUS_MAX_SEGMENTS_PER_JOB': '4'}
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert record['stored_count'] == 0, record
        record['stored_count'] = 4
        record_path.write_text(json.dumps(record), encoding='utf-8')

        seg_dir = Path(docroot) / 'segments' / job
        assert not list(seg_dir.glob('*.ts')), sorted(seg_dir.iterdir())
        status, body = post_segment(base, job, sig, '0')
        assert status == 413, (status, body)
        assert json.loads(body) == {
            'error': 'segment count limit exceeded'}, body
        # And nothing was stored, so the refusal is the whole answer.
        assert not list(seg_dir.glob('*.ts')), sorted(seg_dir.iterdir())


def test_segment_totals_follow_writes_and_overwrites(tmp):
    """Stored count and bytes stay exact, including when a segment is replaced.

    The totals are only worth trusting if they track reality, so this walks
    the three transitions that change them: a new segment, a second new one,
    and an overwrite of the first -- which must move bytes without moving the
    count, the case a naive increment gets wrong.
    """
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        record_path = Path(docroot) / 'segments' / f'{job}.json'

        def totals():
            record = json.loads(record_path.read_text(encoding='utf-8'))
            return record['stored_count'], record['stored_bytes']

        assert totals() == (0, 0), totals()
        assert post_segment(base, job, sig, '0', payload=b'abc')[0] == 200
        assert totals() == (1, 3), totals()
        assert post_segment(base, job, sig, '1', payload=b'de')[0] == 200
        assert totals() == (2, 5), totals()
        # Replacing segment 0 with a longer body: bytes move, count does not.
        assert post_segment(base, job, sig, '0', payload=b'wxyz')[0] == 200
        assert totals() == (2, 6), totals()

        # The record agrees with what is actually on disk.
        seg_dir = Path(docroot) / 'segments' / job
        stored = sorted(seg_dir.glob('*.ts'))
        assert len(stored) == 2, stored
        assert sum(path.stat().st_size for path in stored) == 6, stored


def test_segment_replacement_reuses_count_and_byte_quota(tmp):
    """Replacing one index subtracts its old bytes and adds no file count."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '1',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = post_segment(base, job, sig, '0', payload=b'abcde')
        assert status == 200, (status, body)
        status, body = post_segment(base, job, sig, '0', payload=b'xy')
        assert status == 200, (status, body)
        seg_dir = Path(docroot) / 'segments' / job
        assert list(seg_dir.glob('*.ts')) == [seg_dir / '000000.ts']
        assert (seg_dir / '000000.ts').read_bytes() == b'xy'


def test_segment_index_is_bound_by_minted_job_quota(tmp):
    """A page cannot turn a small trusted job quota into a sparse huge index."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '1',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '16',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        status, minted = mint_job(base, TOK, job)
        assert status == 200, (status, minted)
        sig = minted['sig']

        # Exact reviewer reproduction: the request claims total=1 but selects
        # segment 999999. Only the server-minted record is authoritative.
        status, body = post_segment(base, job, sig, '999999', total='1')
        assert status == 400, (status, body)
        assert json.loads(body)['error'] == 'seg out of range', body
        assert not list((Path(docroot) / 'segments' / job).glob('*.ts'))

        record = json.loads(
            (Path(docroot) / 'segments' / f'{job}.json').read_text(encoding='utf-8'))
        assert record['max_segment_index'] == 10, record
        assert record['max_segment_count'] == 1, record
        assert record['max_bytes'] == 16, record


def test_segment_count_is_bound_by_minted_job_quota(tmp):
    """Distinct files stop at the record's count even if request totals vary."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '100',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        for segment in ('0', '10'):
            status, body = post_segment(
                base, job, sig, segment, payload=b'x', total='999999')
            assert status == 200, (segment, status, body)

        status, body = post_segment(
            base, job, sig, '1', payload=b'x', total='999999')
        assert status == 413, (status, body)
        assert json.loads(body)['error'] == 'segment count limit exceeded', body
        stored = list((Path(docroot) / 'segments' / job).glob('*.ts'))
        assert len(stored) == 2, stored


def test_segment_bytes_are_bound_by_minted_job_quota(tmp):
    """Individually small bodies cannot cross the aggregate per-job byte cap."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '10',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 200, (status, body)

        status, body = post_segment(base, job, sig, '1', payload=b'def')
        assert status == 413, (status, body)
        assert json.loads(body)['error'] == 'job byte limit exceeded', body
        seg_dir = Path(docroot) / 'segments' / job
        assert (seg_dir / '000000.ts').read_bytes() == b'abc'
        assert not (seg_dir / '000001.ts').exists()


def test_concurrent_segment_writes_share_one_quota_snapshot(tmp):
    """Two barrier-released requests cannot both spend the same byte budget."""
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        barrier = threading.Barrier(3)

        def post(segment):
            barrier.wait(timeout=5)
            return post_segment(base, job, sig, segment, payload=b'abc')

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(post, segment) for segment in ('0', '1')]
            barrier.wait(timeout=5)
            replies = [future.result(timeout=10) for future in futures]

        assert sorted(status for status, _body in replies) == [200, 413], replies
        stored = list((Path(docroot) / 'segments' / job).glob('*.ts'))
        assert len(stored) == 1 and stored[0].read_bytes() == b'abc', stored


def test_segment_write_failure_removes_temp_and_answers(tmp):
    """A partial pathlib write gets a response and leaves no temp artifacts."""
    fault_dir = Path(tmp) / 'fault-injection'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_write_bytes = pathlib.Path.write_bytes\n'
        'def _partial_segment_write(path, data):\n'
        '    if path.name.endswith(".ts.tmp"):\n'
        '        with path.open("wb") as stream:\n'
        '            stream.write(data[:2])\n'
        '        raise OSError("injected segment write failure")\n'
        '    return _real_write_bytes(path, data)\n'
        'pathlib.Path.write_bytes = _partial_segment_write\n',
        encoding='utf-8')
    env = {
        'PYTHONPATH': str(fault_dir),
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        statuses = []
        for segment in ('0', '1', '2'):
            try:
                status, _body = post_segment(
                    base, job, sig, segment, payload=b'abc')
                statuses.append(status)
            except http.client.RemoteDisconnected:
                statuses.append('dropped')

        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)
        seg_dir = Path(docroot) / 'segments' / job
        residue = sorted(
            (path.name, path.read_bytes()) for path in seg_dir.glob('*.tmp'))
        assert statuses == [500, 500, 500] and not residue, (statuses, residue)


def test_a_stale_temp_never_enters_the_accounting_and_is_swept_on_resume(tmp):
    """A crashed write's temp costs the job nothing, and the mint clears it.

    Sweeping it cost a full directory scan on every admitted segment, which is
    most of what made a large job slow. The guarantee that mattered was never
    the sweep's timing: it is that a temp outside the finalized .ts set is not
    charged against the job's byte budget. That still holds, and the
    documented resume -- a re-mint by the owner -- is where the file goes.
    """
    env = {
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '3',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        seg_dir = Path(docroot) / 'segments' / job
        stale = seg_dir / '.000001.ts.tmp'
        stale.write_bytes(b'stale bytes outside finalized accounting')

        # 39 stale bytes against a 3-byte job budget: admitted anyway, which
        # is the accounting guarantee. The write path no longer scans, so the
        # temp is still there afterwards.
        status, body = post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 200, (status, body)
        assert (seg_dir / '000000.ts').read_bytes() == b'abc'
        record_path = Path(docroot) / 'segments' / (job + '.json')
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert (record['stored_count'], record['stored_bytes']) == (1, 3), record

        # The owner's re-mint is the resume path, and it sweeps.
        status, again = mint_job(base, TOK, job)
        assert status == 200 and again['sig'] == sig, (status, again)
        assert not stale.exists()
        assert list(seg_dir.glob('*.tmp')) == []


def test_segment_rejection_writes_nothing(tmp):
    with _util.bridge(tmp) as (base, docroot):
        docroot = Path(docroot)

        def post(query):
            return _util.request(
                base + '/segment?' + query, 'POST', body=b'\x00',
                headers={'Content-Type': 'application/octet-stream'})

        # Missing parameters.
        status, body = post('seg=1&total=2')
        assert status == 400, (status, body)
        assert json.loads(body)['error'] == 'missing job or seg'
        status, body = post(f'job={seg_job()}')
        assert status == 400, status

        # Traversal in any of job / seg / total.
        for query in ('job=../x&seg=1&total=2',
                      'job=a/b&seg=1&total=2',
                      'job=a\\b&seg=1&total=2',
                      f'job={seg_job()}&seg=..&total=2',
                      f'job={seg_job()}&seg=1/2&total=2',
                      f'job={seg_job()}&seg=1\\2&total=2',
                      f'job={seg_job()}&seg=1&total=..'):
            status, body = post(query)
            assert status == 400, (query, status, body)
            assert json.loads(body)['error'] == 'invalid param', (query, body)

        # The mint endpoint applies the same component rules to the job name.
        status, _ = mint_job(base, TOK, '../x')
        assert status == 400, status

        # The point of the exercise: nothing was written, inside or outside the
        # docroot. tmp held only docroot/ before this test and must still.
        assert os.listdir(tmp) == ['docroot'], os.listdir(tmp)
        segments = docroot / 'segments'
        created = [str(p.relative_to(segments)) for p in segments.rglob('*')] \
            if segments.is_dir() else []
        assert created == [], created


def test_segment_storage_never_touches_the_old_tmp_root(tmp):
    if os.name == 'nt':
        _util.skip('/tmp means something else on Windows')
    before = set(TMP_SEG_ROOT.iterdir()) if TMP_SEG_ROOT.is_dir() else set()
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status, _ = _util.request(
            base + f'/segment?job={job}&seg=1&total=1&sig={sig}', 'POST',
            body=b'bytes', headers={'Content-Type': 'application/octet-stream'})
        assert status == 200, status
        status, body = _util.get_json(base + f'/segment-status?job={job}&sig={sig}')
        assert status == 200 and body['count'] == 1, (status, body)
        # Everything landed under the bridge's own data root.
        assert (Path(docroot) / 'segments' / job / '000001.ts').read_bytes() == b'bytes'
    after = set(TMP_SEG_ROOT.iterdir()) if TMP_SEG_ROOT.is_dir() else set()
    assert after == before, f'the old world-shared root changed: {after - before}'


def test_a_failing_accounting_write_still_leaves_the_quota_enforced(tmp):
    """The job mints fine, but every write_usage after that fails to land,
    and the count quota still has to hold (#203).

    Before this was fixed, the record kept reporting whatever it recorded
    at mint, zero, forever, and every request after the first read that
    same zero and got waved through. Two segments land, a third should not,
    and the only way it still gets refused is if something other than the
    record notices two are already on disk.
    """
    fault_dir = Path(tmp) / 'fault-injection'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import os\n'
        '_real_replace = os.replace\n'
        '_json_replaces = [0]\n'
        'def _fail_record_replace(src, dst):\n'
        '    if str(dst).endswith(".json"):\n'
        '        _json_replaces[0] += 1\n'
        '        if _json_replaces[0] > 1:\n'
        '            raise OSError("injected accounting write failure")\n'
        '    return _real_replace(src, dst)\n'
        'os.replace = _fail_record_replace\n',
        encoding='utf-8')
    env = {
        'PYTHONPATH': str(fault_dir),
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '100',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status0, _ = post_segment(base, job, sig, '0', payload=b'abc')
        status1, _ = post_segment(base, job, sig, '1', payload=b'abc')
        status2, body2 = post_segment(base, job, sig, '2', payload=b'abc')
        assert (status0, status1) == (200, 200), (status0, status1)
        assert status2 == 413, (status2, body2)
        seg_dir = Path(docroot) / 'segments' / job
        assert len(list(seg_dir.glob('*.ts'))) == 2, list(seg_dir.iterdir())
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert record['stored_count'] == 0, record


def test_a_write_that_cannot_mark_itself_dirty_is_refused_not_published(tmp):
    """If the mark itself cannot be written, the segment must not land either
    (#203): an unmarked write that then also fails to update the record would
    leave neither a trace nor a correct total.
    """
    fault_dir = Path(tmp) / 'fault-injection'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_write_text = pathlib.Path.write_text\n'
        'def _fail_dirty_write(self, *a, **kw):\n'
        '    if str(self).endswith(".dirty"):\n'
        '        raise OSError("injected marker write failure")\n'
        '    return _real_write_text(self, *a, **kw)\n'
        'pathlib.Path.write_text = _fail_dirty_write\n',
        encoding='utf-8')
    env = {'PYTHONPATH': str(fault_dir)}
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status, body = post_segment(base, job, sig, '0', payload=b'abc')
        assert status == 500, (status, body)
        seg_dir = Path(docroot) / 'segments' / job
        assert not list(seg_dir.glob('*.ts')), list(seg_dir.iterdir())
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert record['stored_count'] == 0, record


def test_a_recount_reconciles_and_clears_the_mark_before_a_rejection(tmp):
    """A write that lands under a stale mark reconciles the record from the
    mark's own scan and clears it before this request's own quota is checked
    against that scan (#203) — a rejection must not be the one outcome that
    leaves the next request paying for the same full scan again.
    """
    fault_dir = Path(tmp) / 'fault-injection'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import os\n'
        '_real_replace = os.replace\n'
        '_json_replaces = [0]\n'
        'def _fail_one_record_replace(src, dst):\n'
        '    if str(dst).endswith(".json"):\n'
        '        _json_replaces[0] += 1\n'
        '        if _json_replaces[0] == 2:\n'
        '            raise OSError("injected accounting write failure")\n'
        '    return _real_replace(src, dst)\n'
        'os.replace = _fail_one_record_replace\n',
        encoding='utf-8')
    env = {
        'PYTHONPATH': str(fault_dir),
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '1',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '100',
    }
    with _util.bridge(tmp, env=env) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        status0, _ = post_segment(base, job, sig, '0', payload=b'abc')
        status1, body1 = post_segment(base, job, sig, '1', payload=b'abc')
        assert status0 == 200, status0
        assert status1 == 413, (status1, body1)
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        assert record['stored_count'] == 1, record
        dirty_path = Path(docroot) / 'segments' / f'.{job}.json.dirty'
        assert not dirty_path.exists(), 'mark should have cleared on reconcile'


def _await_lines(proc, drained, probe, what):
    """Poll `probe` with no deadline, giving up only when the child dies.

    What is being waited for is an ordering: the line travels on the child's
    drained stdout, so a deadline would turn a loaded machine into a failure
    while proving nothing extra on a fast one. A regression surfaces as a
    hung job instead, which is the trade this repository takes deliberately.
    """
    while True:
        value = probe()
        if value:
            return value
        assert proc.poll() is None, f'{what}:\n' + ''.join(drained)


def test_a_segment_write_reports_one_timing_line_per_write_when_enabled(tmp):
    """`DAEDALUS_DEBUG_TIMING=1` attributes the write path per phase.

    The line is pinned for its shape — the prefix, the job, the stored
    count and the phase names in the order the phases happen — and never
    for an elapsed value, which would decide differently on a loaded
    runner. Unset, the instrumentation prints nothing at all, which the
    unit contract for `log_timing` pins.
    """
    lines, child = [], []
    env = {'DAEDALUS_DEBUG_TIMING': '1'}
    with _util.bridge(
            tmp, env=env, output=lines, proc_out=child) as (base, _docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        indices = ('0', '1')
        for index in indices:
            status, body = post_segment(
                base, job, sig, index, payload=b'abc')
            assert status == 200, (index, status, body)

        def timing_lines():
            matches = [match for match in
                       (TIMING_LINE.fullmatch(line.strip()) for line in lines)
                       if match]
            return matches if len(matches) == len(indices) else []

        matches = _await_lines(
            child[0], lines, timing_lines,
            'the bridge printed no segment timing line')

    shape = ('prefix', 'job', 'stored count',
             'acquire', 'usage', 'replaced', 'write', 'record',
             'parts', 'total')
    observed = [match.groupdict() for match in matches]
    assert len(observed) == 2, (shape, lines)
    for row in observed:
        assert row['job'] == job, row
        # The stored count is the one the write read at its start, so the
        # first line says 0 and the second 1.
        assert row['stored'] in ('0', '1'), row
        names = [pair.split('=')[0] for pair in row['phases'].split()]
        assert names == ['acquire', 'usage', 'replaced', 'write',
                         'record'], row
        for name, value in row.items():
            assert value is not None, (name, row)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='segstorage_')


if __name__ == '__main__':
    raise SystemExit(main())
