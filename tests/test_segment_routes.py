#!/usr/bin/env python3
"""The segment routes and job minting as functions returning answers.

`daedalus_bridge/segment_routes.py` owns admitting a `POST /segment`
before its body is read, storing an admitted body, listing a job's stored
segments and looking a capability up without minting.
`daedalus_bridge/segment_jobs.py` owns the mint itself. Every one of them
returns `(status, payload)`; `admit_segment` returns an `Admission`
instead when the request may proceed, which is how the request handler
tells an admission from a refusal.

`DAEDALUS_DIR`, `DAEDALUS_PORT` and the three segment quota variables
are set here so the segments directory most of these tests pass in is the
one configuration names; the controls for the root parameter deliberately
pass a different one. Each test uses its own job name, because the
configured directory is process-wide.
"""
import atexit
import contextlib
import json
import os
import pathlib
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


def _write_record(job, record):
    (SEG_DIR / f'{job}.json').write_text(
        json.dumps(record), encoding='utf-8')


@contextlib.contextmanager
def _refused(method, name):
    """Refuse one pathlib call inside the block, restoring it afterwards.

    This suite runs as root on some machines, where a permission bit is not
    a portable way to make one filesystem call fail, so the call is named
    and refused for exactly the path the test is about.

    The call is matched on the path's final component, not on the whole
    path. Every path these routes touch comes back resolved from
    `path_safety.under`, and the temp root this suite builds is spelled
    differently from its own resolution -- macOS aliases /var to
    /private/var, a Windows runner hands out RUNNER~1 for the user
    directory -- so a whole-path comparison held on Linux and nowhere
    else. The aliases rename ancestors only: both spellings end in the job
    directory this test chose, and each job name here is unique to the
    suite. `fired` records the calls that matched, so a test asserts the
    injection happened rather than discovering its absence through a
    route that unexpectedly succeeded.
    """
    real = getattr(pathlib.Path, method)
    fired = []

    def refusing(self, *args, **kwargs):
        if self.name == name:
            fired.append(self.name)
            raise OSError(f'injected {method} failure')
        return real(self, *args, **kwargs)

    setattr(pathlib.Path, method, refusing)
    try:
        yield fired
    finally:
        setattr(pathlib.Path, method, real)


def _symlink_or_skip(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as why:
        _util.skip(f'this filesystem will not hold a symlink: {why}')


def _store(routes, sig, job, index, raw, root=SEG_DIR):
    """Admit and store one segment, returning the store answer."""
    admitted = routes.admit_segment(
        root, {'job': [job], 'seg': [str(index)]}, sig)
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


def test_admit_and_status_refuse_a_job_whose_directory_escapes_the_root(_tmp):
    """A spotless job name can still name a directory outside the root.

    `under` is the only check that can see the symlink, and each of these
    routes joins a second path — the record — that would escape on its own,
    so each answers its own 400 rather than letting the join raise.
    """
    routes = _routes('fixture_segment_routes_escape')
    outside = Path(_BASE) / 'outside'
    outside.mkdir(exist_ok=True)
    _symlink_or_skip(SEG_DIR / 'escape-job', outside)
    assert routes.admit_segment(
        SEG_DIR, {'job': ['escape-job'], 'seg': ['0']}, 'sig') == (
            400, {'error': 'invalid param'})
    assert routes.segment_status(
        SEG_DIR, {'job': ['escape-job']}, 'sig') == (
            400, {'error': 'bad job'})


def test_a_write_still_lands_when_the_record_cannot_be_read(_tmp):
    """Storage keeps going against what the directory really holds.

    Admission authorizes from a readable record; the record can still break
    before the write reaches it. A record that exists and cannot be read is
    not one that never existed, and it is not the write path's to answer
    for either: the write recounts the directory, keeps the segment inside
    what is actually stored, and leaves the broken record exactly where it
    was for the mint to answer.
    """
    jobs = _jobs('fixture_segment_jobs_write_corrupt')
    routes = _routes('fixture_segment_routes_write_corrupt')
    job = 'write-corrupt-job'
    sig = _mint(jobs, 'writetok', job)

    def admit(index):
        answer = routes.admit_segment(
            SEG_DIR, {'job': [job], 'seg': [str(index)]}, sig)
        assert isinstance(answer, routes.Admission), answer
        return answer

    admitted = [admit(0), admit(1)]
    (SEG_DIR / f'{job}.json').write_text('{', encoding='utf-8')
    assert routes.store_segment(b'abcde', admitted[0]) == (200, {'ok': True})
    assert (SEG_DIR / job / '000000.ts').read_bytes() == b'abcde'
    assert (SEG_DIR / f'{job}.json').read_text(encoding='utf-8') == '{'
    assert routes.store_segment(b'xy', admitted[1]) == (200, {'ok': True})
    assert sorted(
        path.name for path in (SEG_DIR / job).glob('*.ts')) == [
            '000000.ts', '000001.ts']

    # Admission reads the record it authorizes against, so nothing new is
    # admitted until the mint has answered for the broken one.
    assert routes.admit_segment(
        SEG_DIR, {'job': [job], 'seg': ['2']}, sig) == (
            403, {'error': 'bad sig'})


def test_a_write_that_cannot_scan_the_directory_is_answered(_tmp):
    """A record without totals makes the write recount; a refused scan is a
    500 rather than an exception that would drop the connection."""
    jobs = _jobs('fixture_segment_jobs_write_scan')
    routes = _routes('fixture_segment_routes_write_scan')
    job = 'write-scan-job'
    sig = _mint(jobs, 'scantok', job)
    _write_record(job, {
        'token': 'scantok', 'sig': sig,
        'max_segment_index': QUOTAS[0], 'max_segment_count': QUOTAS[1],
        'max_bytes': QUOTAS[2]})
    seg_dir = SEG_DIR / job
    with _refused('iterdir', job) as fired:
        assert _store(routes, sig, job, 0, b'abc') == (
            500, {'error': 'segment storage failure'})
    assert fired == [job], fired
    assert not (seg_dir / '000000.ts').exists()


def test_lookup_answers_for_every_shape_a_job_record_can_take(_tmp):
    """The token-gated lookup owes an answer to every state a record is in."""
    jobs = _jobs('fixture_segment_jobs_lookupshapes')
    routes = _routes('fixture_segment_routes_lookupshapes')
    job = 'lookup-shapes'
    _mint(jobs, 'shapetok', job)
    assert routes.lookup_job(SEG_DIR, 'shapetok', {'job': ['']}) == (
        400, {'error': 'bad job'})
    assert routes.lookup_job(
        SEG_DIR, 'shapetok', {'job': ['a/b']}) == (400, {'error': 'bad job'})
    assert routes.lookup_job(
        SEG_DIR, 'shapetok', {'job': ['lookup-never']}) == (
            404, {'error': 'no such job'})
    (SEG_DIR / f'{job}.json').write_text('{', encoding='utf-8')
    assert routes.lookup_job(SEG_DIR, 'shapetok', {'job': [job]}) == (
        500, {'error': 'segment storage failure'})
    _write_record(job, {'token': 'shapetok', 'sig': 6})
    assert routes.lookup_job(SEG_DIR, 'shapetok', {'job': [job]}) == (
        409, {'error': 'job record cannot resume'})
    _write_record(job, {'token': 'shapetok', 'sig': 'kept-capability'})
    assert routes.lookup_job(SEG_DIR, 'shapetok', {'job': [job]}) == (
        200, {'ok': True, 'sig': 'kept-capability'})


def test_mint_refuses_a_resume_record_it_cannot_resume(_tmp):
    """A record that names the owner but not a usable capability is stuck.

    Re-minting is documented as the resume, so the answer has to be the
    same 409 a foreign owner gets rather than a fresh capability silently
    handed out over a record nobody can vouch for.
    """
    jobs = _jobs('fixture_segment_jobs_resume')
    job = 'resume-shapes'
    for broken in ({'token': 'resumetok'},
                   {'token': 'resumetok', 'sig': 6},
                   {'token': 'resumetok', 'sig': 'sí'}):
        _write_record(job, broken)
        assert jobs.mint_job(
            SEG_DIR, 'resumetok', {'job': job},
            jobs.JobQuotas(*QUOTAS)) == (
                409, {'error': 'job record cannot resume'}), broken
    _write_record(job, {
        'token': 'resumetok', 'sig': 'kept-capability', 'max_bytes': '64'})
    assert jobs.mint_job(
        SEG_DIR, 'resumetok', {'job': job}, jobs.JobQuotas(*QUOTAS)) == (
            409, {'error': 'job record cannot resume'})
    _write_record(job, {'token': 'resumetok', 'sig': 'kept-capability'})
    assert jobs.mint_job(
        SEG_DIR, 'resumetok', {'job': job}, jobs.JobQuotas(-1, 3, 64)) == (
            409, {'error': 'job record cannot resume'})

    # A legacy record whose directory is gone has nothing to convert, and
    # the conversion refuses it rather than minting an empty job over it.
    _write_record('resume-gone', {
        'token': 'resumetok', 'sig': 'kept-capability'})
    assert jobs.mint_job(
        SEG_DIR, 'resumetok', {'job': 'resume-gone'},
        jobs.JobQuotas(*QUOTAS)) == (
            409, {'error': 'job record cannot resume'})
    assert not (SEG_DIR / 'resume-gone').exists()


def test_a_legacy_job_that_exceeds_current_quotas_is_not_resumed(_tmp):
    """Segments stored under older limits do not buy budget under new ones.

    The conversion reads the directory to decide, so a job holding more
    files, more bytes or a higher index than the quotas it would be
    converted against is refused instead of being silently adopted.
    """
    jobs = _jobs('fixture_segment_jobs_legacyquota')
    job = 'legacy-quota'
    seg_dir = SEG_DIR / job
    seg_dir.mkdir()
    _write_record(job, {'token': 'legtok', 'sig': 'legacy-capability'})
    for index in range(QUOTAS[1] + 1):
        (seg_dir / f'{index:06d}.ts').write_bytes(b'ab')
    status, payload = jobs.mint_job(
        SEG_DIR, 'legtok', {'job': job}, jobs.JobQuotas(*QUOTAS))
    assert (status, payload) == (
        409, {'error': 'legacy job exceeds current quotas'})
    assert _record(job) == {
        'token': 'legtok', 'sig': 'legacy-capability'}

    for path in seg_dir.glob('*.ts'):
        path.unlink()
    (seg_dir / '000000.ts').write_bytes(b'x' * (QUOTAS[2] + 1))
    status, payload = jobs.mint_job(
        SEG_DIR, 'legtok', {'job': job}, jobs.JobQuotas(*QUOTAS))
    assert (status, payload) == (
        409, {'error': 'legacy job exceeds current quotas'})

    (seg_dir / '000000.ts').unlink()
    (seg_dir / f'{QUOTAS[0] + 1:06d}.ts').write_bytes(b'ab')
    status, payload = jobs.mint_job(
        SEG_DIR, 'legtok', {'job': job}, jobs.JobQuotas(*QUOTAS))
    assert (status, payload) == (
        409, {'error': 'legacy job exceeds current quotas'})
    assert sorted(path.name for path in seg_dir.iterdir()) == [
        f'{QUOTAS[0] + 1:06d}.ts']


def test_a_resume_reconciles_totals_the_record_has_lost(_tmp):
    """The resume is the one moment the record is measured against disk.

    A crash between publishing a segment and recording it leaves a record
    that understates the job; the mint is the right place to notice, since
    it runs once per resume rather than once per segment, and a write that
    could not confirm landing is healed here rather than trusted.
    """
    jobs = _jobs('fixture_segment_jobs_reconcile')
    job = 'reconcile-job'
    sig = _mint(jobs, 'recitok', job)
    seg_dir = SEG_DIR / job
    (seg_dir / '000004.ts').write_bytes(b'abcde')
    (seg_dir / '000000.ts').write_bytes(b'xy')
    _write_record(job, {
        'token': 'recitok', 'sig': sig,
        'max_segment_index': QUOTAS[0], 'max_segment_count': QUOTAS[1],
        'max_bytes': QUOTAS[2], 'stored_count': 0, 'stored_bytes': 0})
    status, payload = jobs.mint_job(
        SEG_DIR, 'recitok', {'job': job}, jobs.JobQuotas(*QUOTAS))
    assert status == 200 and payload['sig'] == sig, (status, payload)
    record = _record(job)
    assert (record['stored_count'], record['stored_bytes']) == (2, 7), record
    assert not (SEG_DIR / f'.{job}.json.dirty').exists(), 'the mark cleared'
    assert record['max_segment_count'] == QUOTAS[1], record


def test_a_refused_mint_that_cannot_remove_its_directory_still_answers(_tmp):
    """The rollback degrades instead of raising through the mint.

    Only the directory this call created is removed, and only while empty;
    when even that is refused the caller still gets the 409 and the
    leftover directory is left for whatever filled it.
    """
    jobs = _jobs('fixture_segment_jobs_rmdir')
    _mint(jobs, 'rmdirtok', 'rmdir-job.json')
    created = SEG_DIR / 'rmdir-job'
    with _refused('rmdir', 'rmdir-job') as fired:
        status, payload = jobs.mint_job(
            SEG_DIR, 'rmdirtok', {'job': 'rmdir-job'},
            jobs.JobQuotas(*QUOTAS))
    assert (status, payload) == (409, {'error': 'job name unavailable'})
    assert fired == ['rmdir-job'], fired
    assert (SEG_DIR / 'rmdir-job.json').is_dir()
    assert created.is_dir() and not any(created.iterdir())


def test_the_modules_need_no_configuration_of_their_own(_tmp):
    """Neither route module nor anything either imports reads `config`.

    `daedalus_bridge.config` still exits without `DAEDALUS_DIR`, which is
    what proves the environment is stripped. Both route modules then import
    for real, so nothing on their graph — `segment_store` included — binds
    configuration.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    refused = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.config'],
        env=env, capture_output=True, text=True, check=False)
    assert refused.returncode != 0, refused.stderr
    assert 'DAEDALUS_DIR' in refused.stderr, refused.stderr
    imported = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.segment_routes\n'
                               'import daedalus_bridge.segment_jobs'],
        env=env, capture_output=True, text=True, check=False)
    assert imported.returncode == 0, imported.stderr


def test_the_segments_root_governs_the_whole_write_path(tmp):
    """Admission and storage both write under the root they were handed.

    The negative half is the control, and it is scoped to the whole
    configured tree: a wrong root leaks under whatever name the misrouted
    call builds, not only under this job's own two.
    """
    routes = _routes('fixture_segment_routes_altroot')
    jobs = _jobs('fixture_segment_jobs_altroot')
    before = sorted(path.name for path in SEG_DIR.iterdir())
    root = Path(tmp) / 'alt-segments'
    root.mkdir(parents=True)
    job = 'altroot-job'
    status, payload = jobs.mint_job(
        root, 'altroottok', {'job': job}, jobs.JobQuotas(*QUOTAS))
    assert status == 200, (status, payload)
    admitted = routes.admit_segment(
        root, {'job': [job], 'seg': ['0']}, payload['sig'])
    assert isinstance(admitted, routes.Admission), admitted
    # Both sides resolved: `under` hands back the path it checked,
    # and this suite's root is an unresolved mkdtemp.
    assert os.path.realpath(admitted.seg_dir_root) == os.path.realpath(root)
    assert routes.store_segment(b'abcd', admitted) == (200, {'ok': True})
    assert (root / job / '000000.ts').read_bytes() == b'abcd'
    record = json.loads((root / f'{job}.json').read_text(encoding='utf-8'))
    assert (record['stored_count'], record['stored_bytes']) == (1, 4), record
    after = sorted(path.name for path in SEG_DIR.iterdir())
    assert after == before, (after, before)


def test_a_failed_write_leaves_its_recount_mark_under_the_passed_root(tmp):
    """A refused write keeps its mark, and the mark follows the root.

    A successful write clears the mark, so a refused one is where a
    misrouted mark stays visible — and it is named for the record rather
    than the job, so only the whole listing sees it arrive.
    """
    routes = _routes('fixture_segment_routes_markroot')
    jobs = _jobs('fixture_segment_jobs_markroot')
    before = sorted(path.name for path in SEG_DIR.iterdir())
    root = Path(tmp) / 'mark-segments'
    root.mkdir(parents=True)
    job = 'markroot-job'
    status, payload = jobs.mint_job(
        root, 'marktok', {'job': job}, jobs.JobQuotas(*QUOTAS))
    assert status == 200, (status, payload)
    admitted = routes.admit_segment(
        root, {'job': [job], 'seg': ['0']}, payload['sig'])
    assert isinstance(admitted, routes.Admission), admitted
    with _refused('write_bytes', '.000000.ts.tmp') as fired:
        assert routes.store_segment(b'abcd', admitted) == (
            500, {'error': 'segment storage failure'})
    assert fired == ['.000000.ts.tmp'], fired
    after = sorted(path.name for path in SEG_DIR.iterdir())
    assert after == before, (after, before)
    assert (root / f'.{job}.json.dirty').exists()


def test_the_passed_quota_is_the_one_the_write_path_enforces(tmp):
    """A count limit of 1 refuses the second segment.

    This drives the limit through admission and storage, where the
    suite's configured ceiling of 3 would let both segments through.
    """
    routes = _routes('fixture_segment_routes_altquota')
    jobs = _jobs('fixture_segment_jobs_altquota')
    root = Path(tmp) / 'quota-segments'
    root.mkdir(parents=True)
    job = 'altquota-job'
    status, payload = jobs.mint_job(
        root, 'altquotatok', {'job': job}, jobs.JobQuotas(5, 1, 64))
    assert status == 200, (status, payload)
    assert _store(routes, payload['sig'], job, 0, b'ab', root) == (
        200, {'ok': True})
    assert _store(routes, payload['sig'], job, 1, b'cd', root) == (
        413, {'error': 'segment count limit exceeded'})


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='segroutes_')


if __name__ == '__main__':
    raise SystemExit(main())
