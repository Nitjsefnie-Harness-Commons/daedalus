#!/usr/bin/env python3
"""Unit contract for HLS segment records and quota accounting."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _probe(tmp, script, extra_env=None):
    """Run a probe against a throwaway data root and return (root, verdict).

    Each probe prints one JSON object and nothing else, so the assertions
    stay in this file where a reader can see them.
    """
    root = Path(tmp) / 'segment-store-root'
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(root),
        'DAEDALUS_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, '-c', script], cwd=_util.ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    return root, json.loads(proc.stdout)


_SEGMENT_STORE_PROBE = r'''
import json
from pathlib import Path

from daedalus_bridge import segment_store

job = 'unit-job'
record_path = segment_store.record_path(job)
record_path.parent.mkdir(parents=True)
record = segment_store.new_record('unit-token')
record.update({
    'max_segment_index': 99,
    'max_segment_count': 7,
    'max_bytes': 4096,
})
record.pop('stored_count')
record.pop('stored_bytes')
record_path.write_text(json.dumps(record), encoding='utf-8')
loaded = segment_store.load_record(job)
matching = segment_store.record_for_sig(job, loaded['sig'])
wrong = segment_store.record_for_sig(job, 'sig-wrong')
quota = segment_store.quota(loaded)
usage_before = segment_store.usage(loaded)
job_dir = Path(record_path).with_suffix('')
job_dir.mkdir()
(job_dir / '000001.ts').write_bytes(b'abcd')
recounted = segment_store.recount(job_dir)
segment_store.write_usage(job, *recounted)
updated = segment_store.load_record(job)
print(json.dumps({
    'sig_length': len(loaded['sig']),
    'matching_token': matching['token'],
    'wrong': wrong,
    'quota': quota,
    'usage_before': usage_before,
    'recounted': recounted,
    'usage_after': segment_store.usage(updated),
}, sort_keys=True))
'''


def test_segment_store_owns_record_and_quota_accounting(tmp):
    _root, answer = _probe(tmp, _SEGMENT_STORE_PROBE)
    assert answer == {
        'sig_length': 43,
        'matching_token': 'unit-token',
        'wrong': None,
        'quota': [99, 7, 4096],
        'usage_before': None,
        'recounted': [1, 4],
        'usage_after': [1, 4],
    }, answer


_RECORD_PROBE = r'''
import json
from pathlib import Path

from daedalus_bridge import segment_store

job = 'probe-record'
path = segment_store.record_path(job)
path.parent.mkdir(parents=True, exist_ok=True)
out = {'absent': segment_store.load_record(job)}

path.write_text('[]', encoding='utf-8')
try:
    segment_store.load_record(job)
    out['not_an_object'] = 'accepted'
except segment_store.SegmentRecordError:
    out['not_an_object'] = 'refused'

path.write_text('{"token": "t", "sig": "s"}', encoding='utf-8')
out['read_back'] = segment_store.load_record(job)

_real_read_text = Path.read_text


def _unreadable(self, *args, **kwargs):
    if self.name == path.name:
        raise OSError('injected record read failure')
    return _real_read_text(self, *args, **kwargs)


def _vanished(self, *args, **kwargs):
    if self.name == path.name:
        raise FileNotFoundError('injected record disappearance')
    return _real_read_text(self, *args, **kwargs)


Path.read_text = _unreadable
try:
    segment_store.load_record(job)
    out['unreadable'] = 'accepted'
except segment_store.SegmentRecordError:
    out['unreadable'] = 'refused'

Path.read_text = _vanished
out['vanished'] = segment_store.load_record(job)
Path.read_text = _real_read_text

path.write_text('{', encoding='utf-8')
out['sig_on_corrupt'] = segment_store.record_for_sig(job, 'any-sig')
out['sig_ok_on_corrupt'] = segment_store.sig_ok(job, 'any-sig')

path.write_text(json.dumps({'token': 't'}), encoding='utf-8')
out['sig_absent'] = segment_store.record_for_sig(job, 'any-sig')
path.write_text(json.dumps({'token': 't', 'sig': 'sí'}), encoding='utf-8')
out['sig_not_ascii'] = segment_store.record_for_sig(job, 'any-sig')
path.write_text(json.dumps({'token': 't', 'sig': 'kept'}), encoding='utf-8')
out['supplied_not_ascii'] = segment_store.record_for_sig(job, 'sí')
out['supplied_empty'] = segment_store.record_for_sig(job, '')
out['supplied_matching'] = segment_store.record_for_sig(job, 'kept')
print(json.dumps(out, sort_keys=True))
'''


def test_a_record_that_cannot_be_read_is_never_an_absent_one(tmp):
    """Absent, unreadable and non-object stay three different answers.

    The mint reads None as "never minted" and writes a fresh owner over it,
    so collapsing unreadable into absent turns corruption into a destroyed
    resume identity reported as a successful mint. The non-object case is
    the same trap one shape over: valid JSON that is not a record.
    """
    _root, answer = _probe(tmp, _RECORD_PROBE)
    assert answer == {
        'absent': None,
        'not_an_object': 'refused',
        'read_back': {'token': 't', 'sig': 's'},
        'unreadable': 'refused',
        'vanished': None,
        'sig_on_corrupt': None,
        'sig_ok_on_corrupt': False,
        'sig_absent': None,
        'sig_not_ascii': None,
        'supplied_not_ascii': None,
        'supplied_empty': None,
        'supplied_matching': {'token': 't', 'sig': 'kept'},
    }, answer


_QUOTA_PROBE = r'''
import json

from daedalus_bridge import segment_store

good = {'max_segment_index': 5, 'max_segment_count': 3, 'max_bytes': 64,
        'stored_count': 1, 'stored_bytes': 2}


def with_field(name, value):
    record = dict(good)
    if value is ...:
        record.pop(name, None)
    else:
        record[name] = value
    return record


print(json.dumps({
    'quota': segment_store.quota(good),
    'usage': segment_store.usage(good),
    'index_bool': segment_store.quota(
        with_field('max_segment_index', True)),
    'count_str': segment_store.quota(with_field('max_segment_count', '3')),
    'count_bool': segment_store.quota(with_field('max_segment_count', True)),
    'count_negative': segment_store.quota(with_field('max_segment_count', -1)),
    'count_missing': segment_store.quota(with_field('max_segment_count', ...)),
    'bytes_str': segment_store.quota(with_field('max_bytes', '64')),
    'bytes_bool': segment_store.quota(with_field('max_bytes', True)),
    'bytes_negative': segment_store.quota(with_field('max_bytes', -1)),
    'stored_str': segment_store.usage(with_field('stored_bytes', '2')),
    'stored_bool': segment_store.usage(with_field('stored_bytes', True)),
    'stored_negative': segment_store.usage(with_field('stored_bytes', -1)),
}, sort_keys=True))
'''


def test_quota_and_usage_refuse_every_untrusted_shape(tmp):
    """Record fields arrive from a file any writer could have replaced.

    `True` is an int to isinstance, and a coerced or negative limit would
    hand a job budget it was never minted with, so each field is checked
    one at a time and a malformed one refuses the whole tuple.
    """
    _root, answer = _probe(tmp, _QUOTA_PROBE)
    assert answer == {
        'quota': [5, 3, 64],
        'usage': [1, 2],
        'index_bool': None,
        'count_str': None,
        'count_bool': None,
        'count_negative': None,
        'count_missing': None,
        'bytes_str': None,
        'bytes_bool': None,
        'bytes_negative': None,
        'stored_str': None,
        'stored_bool': None,
        'stored_negative': None,
    }, answer


_RECOUNT_PROBE = r'''
import json
from pathlib import Path

from daedalus_bridge import segment_store

job = 'probe-recount'
root = segment_store.record_path(job).parent
root.mkdir(parents=True, exist_ok=True)
plain = root / 'plain-file'
plain.write_text('x', encoding='utf-8')
job_dir = root / job
job_dir.mkdir()
(job_dir / '000000.ts').write_bytes(b'abc')
(job_dir / '000002.ts').write_bytes(b'de')
(job_dir / 'notes.txt').write_text('not a segment', encoding='utf-8')
(job_dir / '.000005.ts.tmp').write_bytes(b'crashed write')
(job_dir / '000007.ts').mkdir()
(job_dir / '000009.ts').symlink_to(root / 'never-was-a-file')

out = {
    'totals': segment_store.recount(job_dir),
    'temp_survived_a_clean_sweep': (job_dir / '.000005.ts.tmp').exists(),
    'absent_dir': segment_store.recount(root / (job + '.gone')),
    'not_a_directory': segment_store.recount(plain),
}

_real_unlink = Path.unlink


def _stuck_temp(self):
    if self.name.endswith('.ts.tmp'):
        raise OSError('injected temp removal failure')
    return _real_unlink(self)


Path.unlink = _stuck_temp
(job_dir / '.000005.ts.tmp').write_bytes(b'crashed write')
out['totals_with_stuck_temp'] = segment_store.recount(job_dir)
Path.unlink = _real_unlink
out['stuck_temp_survived'] = (job_dir / '.000005.ts.tmp').exists()

print(json.dumps(out, sort_keys=True))
'''


def test_recount_answers_for_every_shape_a_job_directory_can_take(tmp):
    """The scan is the fallback both callers fall back on, so it cannot raise.

    A job name may contain a dot, so one job's directory can be another
    job's record file; enumerating that has to arrive as None rather than
    as an exception escaping into a dropped connection. Inside a directory,
    anything that is not a finalized regular .ts file is invisible to the
    accounting, and a temp that will not go is not worth failing a write.
    """
    probe = Path(tmp) / 'symlink-probe'
    probe.mkdir()
    try:
        (probe / 'link').symlink_to(probe / 'missing')
        (probe / 'link').unlink()
    except (OSError, NotImplementedError) as why:
        _util.skip(f'this filesystem will not hold a symlink: {why}')
    _root, answer = _probe(tmp, _RECOUNT_PROBE)
    assert answer == {
        'totals': [2, 5],
        'temp_survived_a_clean_sweep': False,
        'absent_dir': [0, 0],
        'not_a_directory': None,
        'totals_with_stuck_temp': [2, 5],
        'stuck_temp_survived': True,
    }, answer


_WRITE_USAGE_PROBE = r'''
import json
from pathlib import Path

from daedalus_bridge import atomic_file
from daedalus_bridge import segment_store

root = segment_store.record_path('probe-usage').parent
root.mkdir(parents=True, exist_ok=True)
out = {}

segment_store.write_usage('probe-usage', 1, 2)
out['absent_record_wrote_nothing'] = not any(root.iterdir())

corrupt = root / 'corrupt.json'
corrupt.write_text('{', encoding='utf-8')
segment_store.write_usage('corrupt', 1, 2)
out['corrupt_left_alone'] = corrupt.read_text(encoding='utf-8')

job = 'probe-usage'
path = segment_store.record_path(job)
record = segment_store.new_record('probe-token')
path.write_text(json.dumps(record), encoding='utf-8')
dirty = path.with_name(f'.{path.name}.dirty')
segment_store.mark_dirty(job)
segment_store.write_usage(job, 3, 9)
stored = segment_store.load_record(job)
out['after_write'] = [stored['stored_count'], stored['stored_bytes']]
out['mark_cleared'] = not dirty.exists()

_real_write = atomic_file.write_text_retrying


def _failing_write(path_, data):
    raise OSError('injected record write failure')


_real_unlink = Path.unlink


def _stuck_temp(self):
    if self.name.endswith('.json.tmp'):
        raise OSError('injected temp removal failure')
    return _real_unlink(self)


# The caller's mark goes down before the write it guards, while the real
# writer still works; the failure is injected for the write itself.
segment_store.mark_dirty(job)
leftover = path.with_name(f'.{path.name}.tmp')
leftover.write_text('stale', encoding='utf-8')
atomic_file.write_text_retrying = _failing_write
Path.unlink = _stuck_temp
segment_store.write_usage(job, 4, 16)
Path.unlink = _real_unlink
atomic_file.write_text_retrying = _real_write
after = segment_store.load_record(job)
out['after_failed_write'] = [after['stored_count'], after['stored_bytes']]
out['mark_still_down'] = dirty.exists()
out['temp_content'] = leftover.read_text(encoding='utf-8')
print(json.dumps(out, sort_keys=True))
'''


def test_write_usage_updates_totals_and_leaves_a_broken_record_alone(tmp):
    """The usage writer never answers for corruption and never raises.

    Only the mint may replace a record, because the owner and capability a
    resume depends on live in it; a write that cannot land leaves the old
    totals and the caller's dirty mark, which is what sends the next read
    to a recount instead of trusting them.
    """
    _root, answer = _probe(tmp, _WRITE_USAGE_PROBE)
    assert answer == {
        'absent_record_wrote_nothing': True,
        'corrupt_left_alone': '{',
        'after_write': [3, 9],
        'mark_cleared': True,
        'after_failed_write': [3, 9],
        'mark_still_down': True,
        'temp_content': 'stale',
    }, answer


_TIMING_PROBE = r'''
import contextlib
import io
import json

from daedalus_bridge import segment_store

marks = [('enter', 0.0), ('acquire', 0.25), ('usage', 0.5), ('record', 1.0)]
out = {'timing_marks': [name for name, _ts in segment_store.timing_marks()]}
captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    segment_store.log_timing('probe-job', 3, marks)
out['timing_line'] = captured.getvalue().strip()
captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    segment_store.log_timing('probe-job', 3, [('enter', 0.0)])
out['short_marks_line'] = captured.getvalue().strip()
print(json.dumps(out, sort_keys=True))
'''


_MARK_DIRTY_PROBE = r'''
import json
from pathlib import Path

from daedalus_bridge import atomic_file
from daedalus_bridge import segment_store

path = segment_store.record_path('probe-mark')
path.parent.mkdir(parents=True, exist_ok=True)
out = {'mark_written': segment_store.mark_dirty('probe-mark')}
out['mark_exists'] = path.with_name(f'.{path.name}.dirty').is_file()

_real_write = atomic_file.write_text_retrying


def _failing_write(path_, data):
    raise OSError('injected marker write failure')


atomic_file.write_text_retrying = _failing_write
out['mark_refused'] = segment_store.mark_dirty('probe-mark')
atomic_file.write_text_retrying = _real_write
print(json.dumps(out, sort_keys=True))
'''


def test_a_mark_that_cannot_be_written_is_reported_not_swallowed(tmp):
    """Publishing bytes that make storage disagree with the record is gated
    on establishing the durable marker first. A marker that cannot land is
    the caller's signal to refuse the write (#203), so `mark_dirty` has to
    say so instead of letting the write proceed unaccounted.
    """
    _root, answer = _probe(tmp, _MARK_DIRTY_PROBE)
    assert answer == {
        'mark_written': True,
        'mark_exists': True,
        'mark_refused': False,
    }, answer


def test_log_timing_reports_each_phase_under_the_mark_that_ends_it(tmp):
    """A phase is reported under what it did, and the parts are summed.

    The gap the arithmetic leaves visible is the point: a total printed
    beside the sum of the named parts is what makes instrumentation with
    holes in it noticeable. The marks are synthetic, so the whole line is
    fixed and nothing wall-clock is asserted.
    """
    _root, answer = _probe(
        tmp, _TIMING_PROBE, extra_env={'DAEDALUS_DEBUG_TIMING': '1'})
    assert answer == {
        'timing_marks': ['enter'],
        'timing_line': '[SEGMENT-TIMING] probe-job stored=3 '
                       'acquire=250.00 usage=250.00 record=500.00 '
                       'parts=1000.00 total=1000.00',
        'short_marks_line': '[SEGMENT-TIMING] probe-job stored=3  '
                            'parts=0.00 total=0.00',
    }, answer


def test_timing_stays_inert_without_the_debug_variable(tmp):
    """`DAEDALUS_DEBUG_TIMING` unset means no marks and no printed line."""
    script = r'''
import contextlib
import io
import json

from daedalus_bridge import segment_store

out = {'timing_marks': segment_store.timing_marks()}
captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    segment_store.log_timing('probe-job', 3, [('enter', 0.0)])
out['printed'] = captured.getvalue()
print(json.dumps(out, sort_keys=True))
'''
    _root, answer = _probe(tmp, script)
    assert answer == {'timing_marks': None, 'printed': ''}, answer


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='segment_store_')


if __name__ == '__main__':
    raise SystemExit(main())
