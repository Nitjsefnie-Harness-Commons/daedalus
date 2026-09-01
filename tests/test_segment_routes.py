#!/usr/bin/env python3
"""The segment routes and job minting as functions returning answers.

`daedalus_bridge/segment_routes.py` owns admitting a `POST /segment`
before its body is read, storing an admitted body, listing a job's stored
segments and looking a capability up without minting.
`daedalus_bridge/segment_jobs.py` owns the mint itself. Every one of them
returns `(status, payload)`; `admit_segment` returns an `Admission`
instead when the request may proceed, which is how the request handler
tells an admission from a refusal.

`segment_store` imports `daedalus_bridge.config` for `SEG_DIR` and the
quota defaults, so `DAEDALUS_DIR`, `DAEDALUS_PORT` and the three segment
quota variables are set here before the first load, and the segments
directory these tests pass in is the one that configuration names. Each
test uses its own job name, because that directory is process-wide.
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402

_BASE = tempfile.mkdtemp(prefix='segroutes_base_')
atexit.register(shutil.rmtree, _BASE, ignore_errors=True)
os.environ['DAEDALUS_DIR'] = _BASE
os.environ['DAEDALUS_PORT'] = '0'
# Small enough that a handful of tiny segments reaches every quota edge.
os.environ['DAEDALUS_MAX_SEGMENT_INDEX'] = '5'
os.environ['DAEDALUS_MAX_SEGMENTS_PER_JOB'] = '3'
os.environ['DAEDALUS_MAX_SEGMENT_JOB_SIZE'] = '64'
SEG_DIR = Path(_BASE) / 'segments'
QUOTAS = (5, 3, 64)


def _routes(name):
    SEG_DIR.mkdir(parents=True, exist_ok=True)
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'segment_routes.py', name)


def _jobs(name):
    SEG_DIR.mkdir(parents=True, exist_ok=True)
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'segment_jobs.py', name)


def _mint(jobs, token, job):
    """Mint `job` for `token` and return its capability."""
    status, payload = jobs.mint_job(
        SEG_DIR, token, {'job': job}, jobs.JobQuotas(*QUOTAS))
    assert status == 200, (status, payload)
    return payload['sig']


def _record(job):
    return json.loads(
        (SEG_DIR / f'{job}.json').read_text(encoding='utf-8'))


def _store(routes, sig, job, index, raw):
    """Admit and store one segment, returning the store answer."""
    admitted = routes.admit_segment(
        SEG_DIR, {'job': [job], 'seg': [str(index)]}, sig)
    assert isinstance(admitted, routes.Admission), admitted
    return routes.store_segment(raw, admitted)


def test_admit_refuses_a_request_naming_no_job_or_seg(_tmp):
    routes = _routes('fixture_segment_routes_missing')
    assert routes.admit_segment(SEG_DIR, {'seg': ['0']}, 'sig') == (
        400, {'error': 'missing job or seg'})
    assert routes.admit_segment(SEG_DIR, {'job': ['j']}, 'sig') == (
        400, {'error': 'missing job or seg'})


def test_admit_refuses_an_overlong_decimal_seg(_tmp):
    """A decimal past the digit bound is refused before int() sees it."""
    routes = _routes('fixture_segment_routes_overlong')
    seg = '1' * (routes.SEGMENT_DECIMAL_MAX_DIGITS + 1)
    assert routes.admit_segment(
        SEG_DIR, {'job': ['overlong'], 'seg': [seg]}, 'sig') == (
            400, {'error': 'seg must be a bounded ASCII decimal'})


def test_admit_refuses_a_non_decimal_seg(_tmp):
    routes = _routes('fixture_segment_routes_nondecimal')
    assert routes.admit_segment(
        SEG_DIR, {'job': ['nondec'], 'seg': ['seven']}, 'sig') == (
            400, {'error': 'seg must be a bounded ASCII decimal'})


def test_admit_refuses_a_sig_no_record_matches(_tmp):
    """An unknown job and a wrong sig get one answer: no existence oracle."""
    jobs = _jobs('fixture_segment_jobs_badsig')
    routes = _routes('fixture_segment_routes_badsig')
    _mint(jobs, 'badsigtok', 'badsig-job')
    assert routes.admit_segment(
        SEG_DIR, {'job': ['badsig-job'], 'seg': ['0']}, 'not-the-sig') == (
            403, {'error': 'bad sig'})
    assert routes.admit_segment(
        SEG_DIR, {'job': ['never-minted'], 'seg': ['0']}, 'anything') == (
            403, {'error': 'bad sig'})


def test_admit_refuses_an_index_past_the_records_quota(_tmp):
    """The recorded max index bounds the name a write may take."""
    jobs = _jobs('fixture_segment_jobs_range')
    routes = _routes('fixture_segment_routes_range')
    sig = _mint(jobs, 'rangetok', 'range-job')
    over = QUOTAS[0] + 1
    assert routes.admit_segment(
        SEG_DIR, {'job': ['range-job'], 'seg': [str(over)]}, sig) == (
            400, {'error': 'seg out of range'})
    admitted = routes.admit_segment(
        SEG_DIR, {'job': ['range-job'], 'seg': [str(QUOTAS[0])]}, sig)
    assert isinstance(admitted, routes.Admission), admitted
    assert admitted.job == 'range-job'
    assert admitted.segment_index == QUOTAS[0]
    assert admitted.quota == QUOTAS
    # `path_safety.under` returns the resolved path, so the expected
    # value is resolved too: a temp root reached through a symlink
    # (macOS `/var/folders`) makes the unresolved join a different
    # string for the same directory.
    assert admitted.seg_dir == (SEG_DIR / 'range-job').resolve(), (
        admitted.seg_dir)


def test_store_writes_the_zero_padded_segment_and_its_totals(_tmp):
    jobs = _jobs('fixture_segment_jobs_write')
    routes = _routes('fixture_segment_routes_write')
    sig = _mint(jobs, 'writetok', 'write-job')
    assert _store(routes, sig, 'write-job', 3, b'abcde') == (
        200, {'ok': True})
    stored = SEG_DIR / 'write-job' / '000003.ts'
    assert stored.read_bytes() == b'abcde'
    record = _record('write-job')
    assert (record['stored_count'], record['stored_bytes']) == (1, 5)


def test_store_counts_a_replacement_as_the_same_segment(_tmp):
    """Rewriting an index replaces it; it does not spend another slot."""
    jobs = _jobs('fixture_segment_jobs_replace')
    routes = _routes('fixture_segment_routes_replace')
    sig = _mint(jobs, 'replacetok', 'replace-job')
    assert _store(routes, sig, 'replace-job', 0, b'aaaa') == (
        200, {'ok': True})
    assert _store(routes, sig, 'replace-job', 0, b'bbbbbb') == (
        200, {'ok': True})
    record = _record('replace-job')
    assert (record['stored_count'], record['stored_bytes']) == (1, 6)
    assert (SEG_DIR / 'replace-job' / '000000.ts').read_bytes() == b'bbbbbb'


def test_store_refuses_a_segment_past_the_count_quota(_tmp):
    jobs = _jobs('fixture_segment_jobs_count')
    routes = _routes('fixture_segment_routes_count')
    sig = _mint(jobs, 'counttok', 'count-job')
    for index in range(QUOTAS[1]):
        assert _store(routes, sig, 'count-job', index, b'aa') == (
            200, {'ok': True})
    assert _store(routes, sig, 'count-job', QUOTAS[1], b'aa') == (
        413, {'error': 'segment count limit exceeded'})
    assert not (SEG_DIR / 'count-job' / '000003.ts').exists()


def test_store_refuses_a_segment_past_the_byte_quota(_tmp):
    jobs = _jobs('fixture_segment_jobs_bytes')
    routes = _routes('fixture_segment_routes_bytes')
    sig = _mint(jobs, 'bytestok', 'bytes-job')
    assert _store(routes, sig, 'bytes-job', 0, b'x' * 40) == (
        200, {'ok': True})
    assert _store(routes, sig, 'bytes-job', 1, b'x' * 40) == (
        413, {'error': 'job byte limit exceeded'})
    assert not (SEG_DIR / 'bytes-job' / '000001.ts').exists()


def test_status_lists_the_stored_indices_in_order(_tmp):
    jobs = _jobs('fixture_segment_jobs_status')
    routes = _routes('fixture_segment_routes_status')
    sig = _mint(jobs, 'statustok', 'status-job')
    for index in (2, 0, 1):
        assert _store(routes, sig, 'status-job', index, b'zz') == (
            200, {'ok': True})
    assert routes.segment_status(
        SEG_DIR, {'job': ['status-job']}, sig) == (
            200, {'done': [0, 1, 2], 'count': 3})


def test_status_refuses_a_wrong_sig(_tmp):
    jobs = _jobs('fixture_segment_jobs_statussig')
    routes = _routes('fixture_segment_routes_statussig')
    _mint(jobs, 'statussigtok', 'statussig-job')
    assert routes.segment_status(
        SEG_DIR, {'job': ['statussig-job']}, 'wrong') == (
            403, {'error': 'bad sig'})
    assert routes.segment_status(SEG_DIR, {'job': ['']}, 'wrong') == (
        400, {'error': 'bad job'})


def test_lookup_reports_an_absent_job_as_absent(_tmp):
    """The token-gated route is owed a real answer, not the sig conflation."""
    routes = _routes('fixture_segment_routes_lookup404')
    assert routes.lookup_job(
        SEG_DIR, 'lookuptok', {'job': ['lookup-never-minted']}) == (
            404, {'error': 'no such job'})


def test_lookup_refuses_a_job_another_token_owns(_tmp):
    jobs = _jobs('fixture_segment_jobs_lookup409')
    routes = _routes('fixture_segment_routes_lookup409')
    sig = _mint(jobs, 'owner-a', 'lookup409-job')
    assert routes.lookup_job(
        SEG_DIR, 'owner-a', {'job': ['lookup409-job']}) == (
            200, {'ok': True, 'sig': sig})
    assert routes.lookup_job(
        SEG_DIR, 'owner-b', {'job': ['lookup409-job']}) == (
            409, {'error': 'job owned by a different token'})


def test_mint_is_idempotent_for_the_owning_token(_tmp):
    """A resume re-fetches the capability rather than minting a new one."""
    jobs = _jobs('fixture_segment_jobs_idempotent')
    first = _mint(jobs, 'minttok', 'mint-idempotent')
    assert _mint(jobs, 'minttok', 'mint-idempotent') == first
    assert _record('mint-idempotent')['sig'] == first


def test_mint_refuses_a_job_another_token_owns(_tmp):
    jobs = _jobs('fixture_segment_jobs_mint409')
    sig = _mint(jobs, 'mint409-a', 'mint409-job')
    status, payload = jobs.mint_job(
        SEG_DIR, 'mint409-b', {'job': 'mint409-job'},
        jobs.JobQuotas(*QUOTAS))
    assert (status, payload) == (
        409, {'error': 'job owned by a different token'})
    assert _record('mint409-job')['sig'] == sig


def test_mint_writes_nothing_when_the_name_collides(_tmp):
    """Job names may contain dots, so one flat namespace has collisions.

    With job `x.json` minted, its directory occupies the record path job
    `x` would publish to. The mint fails there, and a refused mint must
    leave neither its temp record nor the directory it created.
    """
    jobs = _jobs('fixture_segment_jobs_collision')
    _mint(jobs, 'colltok', 'coll.json')
    status, payload = jobs.mint_job(
        SEG_DIR, 'colltok', {'job': 'coll'}, jobs.JobQuotas(*QUOTAS))
    assert (status, payload) == (409, {'error': 'job name unavailable'})
    assert not (SEG_DIR / '.coll.json.tmp').exists()
    assert not (SEG_DIR / 'coll').exists()
    assert (SEG_DIR / 'coll.json').is_dir()


def test_the_modules_need_no_configuration_of_their_own(_tmp):
    """Their only configuration-bound import is `segment_store`.

    `segment_store` reads `SEG_DIR` from `daedalus_bridge.config`, so with
    no `DAEDALUS_*` variable set importing it fails — that is the control
    proving the environment really is stripped. With `segment_store`
    standing in, both route modules still import, which is what says they
    bind no configuration of their own.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    refused = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.segment_store'],
        env=env, capture_output=True, text=True, check=False)
    assert refused.returncode != 0, refused.stderr
    assert 'DAEDALUS_DIR' in refused.stderr, refused.stderr
    stubbed = subprocess.run(
        [sys.executable, '-c', _STUBBED_IMPORT],
        env=env, capture_output=True, text=True, check=False)
    assert stubbed.returncode == 0, stubbed.stderr


_STUBBED_IMPORT = """
import sys, types
import daedalus_bridge
stub = types.ModuleType('daedalus_bridge.segment_store')
sys.modules['daedalus_bridge.segment_store'] = stub
daedalus_bridge.segment_store = stub
import daedalus_bridge.segment_routes
import daedalus_bridge.segment_jobs
"""


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='segroutes_')


if __name__ == '__main__':
    raise SystemExit(main())
