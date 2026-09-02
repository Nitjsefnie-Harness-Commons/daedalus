#!/usr/bin/env python3
"""The record beside a segments directory: minting, ownership and status.

A job exists because `POST /segment-job` minted it, and the record it writes
carries the owning token, the capability and the quotas. These tests pin that
a lookup never mints, that a name another token owns is a 409 rather than a
silent re-mint, that a record which cannot be read is never replaced by a
fresh one, and what `GET /segment-status` reports back.
"""
import http.client
import json
import os
import subprocess
import sys
import urllib.parse
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _segments import TOK, mint_job, seg_job  # noqa: E402


def test_looking_up_a_segment_job_creates_nothing(tmp):
    """Asking about a job must not be what brings it into existence.

    The capability /segment-status needs was only handed out by POST
    /segment-job, which mints on a name it has not seen — so a status query
    for a mistyped job created that job, left a permanent record beside the
    segments directory, and answered zero segments as though the name had
    been right.
    """
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        record = docroot / 'segments' / f'{job}.json'
        query = urllib.parse.urlencode({'token': TOK, 'job': job})
        status, body = _util.get_json(f'{base}/segment-job?{query}')
        assert status == 404 and body == {'error': 'no such job'}, (status, body)
        assert not record.exists(), 'the lookup created the job'

        status, minted = mint_job(base, TOK, job)
        assert status == 200 and record.is_file(), (status, minted)
        status, body = _util.get_json(f'{base}/segment-job?{query}')
        assert status == 200 and body == {
            'ok': True, 'sig': minted['sig']}, (status, body)

        # A job someone else owns is a 409, not a silent re-mint.
        record.write_text(json.dumps({
            'token': 'someoneelse', 'sig': 'their-capability',
            'max_segment_index': 10, 'max_segment_count': 10,
            'max_bytes': 100}), encoding='utf-8')
        status, body = _util.get_json(f'{base}/segment-job?{query}')
        assert status == 409, (status, body)

    # The lookup is token-gated, so it answers a wrong token before it
    # answers anything about the job.
    with _util.bridge(tmp) as (base, _docroot):
        query = urllib.parse.urlencode({'token': 'wrong', 'job': 'anything'})
        status, body = _util.get_json(f'{base}/segment-job?{query}')
        assert status == 401 and body == {'error': 'unauthorized'}, (status, body)


def test_segment_job_mint_idempotent_and_owned(tmp):
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        status, body = mint_job(base, TOK, job)
        assert status == 200 and body['ok'] is True and body['sig'], (status, body)
        sig = body['sig']
        # Idempotent for the owner: the same sig comes back, so resume works.
        status, again = mint_job(base, TOK, job)
        assert status == 200 and again['sig'] == sig, (status, again)
        # A different request token cannot reach ownership checks.
        status, body = mint_job(base, 'othertok', job)
        assert status == 401 and 'sig' not in body, (status, body)
        # A record owned by an earlier configured token remains protected after
        # token rotation, when the current configured token reaches the handler.
        foreign_job = seg_job()
        segment_root = Path(docroot) / 'segments'
        (segment_root / foreign_job).mkdir()
        (segment_root / f'{foreign_job}.json').write_text(json.dumps({
            'token': 'earlierconfigured',
            'sig': 'persistedforeigncapability',
            'max_segment_index': 10,
            'max_segment_count': 10,
            'max_bytes': 100,
        }))
        status, body = mint_job(base, TOK, foreign_job)
        assert status == 409 and 'sig' not in body, (status, body)
        # Validation: the job name, and the token check of the shared JSON
        # POST path (bad_token runs before the handler).
        status, _ = mint_job(base, TOK, 'a/b')
        assert status == 400, status
        status, _ = mint_job(base, 'a/b', seg_job())
        assert status == 400, status
        # The record sits beside the job's directory, both under the docroot.
        seg_dir = Path(docroot) / 'segments' / job
        assert seg_dir.is_dir(), os.listdir(Path(docroot) / 'segments')
        record = json.loads((Path(docroot) / 'segments' / f'{job}.json').read_text(encoding='utf-8'))
        assert record == {
            'token': TOK,
            'sig': sig,
            'max_segment_index': 99_999,
            'max_segment_count': 10_000,
            'max_bytes': 4 * 1024 * 1024 * 1024,
            'stored_count': 0,
            'stored_bytes': 0,
        }, record


def test_a_mint_seeds_totals_from_a_directory_it_did_not_create(tmp):
    """A record can be gone while its segments are not, and zero would lie.

    Seeding a fresh mint with zero would hand such a job a budget it has
    already spent. The mint counts instead, which is also where a temp left
    behind by a crashed write is swept -- off the per-segment path.
    """
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        seg_dir = Path(docroot) / 'segments' / job
        seg_dir.mkdir(parents=True)
        (seg_dir / '000000.ts').write_bytes(b'abcd')
        (seg_dir / '000001.ts').write_bytes(b'ef')
        (seg_dir / '.000009.ts.tmp').write_bytes(b'crashed')

        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        record = json.loads(
            (Path(docroot) / 'segments' / f'{job}.json').read_text(
                encoding='utf-8'))
        assert (record['stored_count'], record['stored_bytes']) == (2, 6), record
        # The crashed write's temp is gone, and it never counted toward bytes.
        assert not list(seg_dir.glob('.*.tmp')), sorted(seg_dir.iterdir())


def test_a_corrupt_job_record_is_not_replaced_by_a_fresh_mint(tmp):
    """A record that cannot be read is not a job that does not exist.

    Both arrived as None, and the mint reads None as "not minted yet", so a
    truncated record was overwritten with a fresh owner and capability — the
    job's resume identity destroyed, and the caller told the mint succeeded.
    """
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        status, minted = mint_job(base, TOK, job)
        assert status == 200, (status, minted)
        record_path = Path(docroot) / 'segments' / f'{job}.json'
        corrupt = '{"token": "' + TOK + '", "sig": "'
        record_path.write_text(corrupt, encoding='utf-8')

        status, body = mint_job(base, TOK, job)
        assert status == 500, (status, body)
        assert record_path.read_text(encoding='utf-8') == corrupt

        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


def test_a_segment_job_that_escapes_through_a_symlink_is_refused(tmp):
    """A job name can be spotless and still name somewhere else.

    `escape` passes every component rule, so the only thing that can notice a
    symlink out of the segment root is a check on where the path landed. The
    mint would otherwise create a record and a directory outside the bridge's
    own tree, under a name the caller chose.
    """
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        status, _ = mint_job(base, TOK, job)
        assert status == 200, status

        outside = Path(docroot) / 'not-segments'
        outside.mkdir()
        try:
            (Path(docroot) / 'segments' / 'escape').symlink_to(
                outside, target_is_directory=True)
        except (OSError, NotImplementedError) as why:
            _util.skip(f'this filesystem will not hold a symlink: {why}')

        status, raw = mint_job(base, TOK, 'escape')
        assert (status, raw.get('error')) == (400, 'bad job'), (status, raw)
        assert sorted(outside.iterdir()) == [], list(outside.iterdir())

        # The record is a sibling of the directory, `<job>.json`, so it is a
        # second join and escapes independently of the first.
        elsewhere = outside / 'stolen.json'
        elsewhere.write_text('{}', encoding='utf-8')
        (Path(docroot) / 'segments' / 'record.json').symlink_to(elsewhere)
        query = urllib.parse.urlencode({'token': TOK, 'job': 'record'})
        status, raw = _util.get_json(f'{base}/segment-job?{query}')
        assert (status, raw.get('error')) == (400, 'bad job'), (status, raw)
        assert elsewhere.read_text(encoding='utf-8') == '{}', 'it was rewritten'


def test_segment_job_dotted_name_collision_is_a_clean_409(tmp):
    """Job names may contain dots, so '<name>' and '<name>.json' collide on
    disk whichever is minted first: one side's record file is the other
    side's directory. The second mint must be a clean 409 that writes
    nothing — not an uncaught IsADirectoryError that drops the connection,
    orphans the tmp record and half-creates the job directory.
    """
    with _util.bridge(tmp) as (base, docroot):
        seg_root = Path(docroot) / 'segments'

        # Plain name first, then the dotted one.
        plain = 'zz-' + uuid.uuid4().hex[:8]
        status, _ = mint_job(base, TOK, plain)
        assert status == 200, status
        status, body = mint_job(base, TOK, plain + '.json')
        assert status == 409 and body['error'] == 'job name unavailable', (status, body)

        # Dotted name first, then the plain one — the ordering whose
        # os.replace targets a directory.
        dotted = 'zz-' + uuid.uuid4().hex[:8] + '.json'
        owner = dotted[:-len('.json')]
        status, _ = mint_job(base, TOK, dotted)
        assert status == 200, status
        try:
            status, body = mint_job(base, TOK, owner)
        except Exception as e:
            raise AssertionError(
                f'minting {owner!r} after {dotted!r} dropped the connection: '
                f'{type(e).__name__}: {e}') from e
        assert status == 409 and body['error'] == 'job name unavailable', (status, body)

        # Neither refused mint wrote anything: only the two successful jobs'
        # directories and records exist — no orphaned tmp file, no
        # half-created job directory.
        assert sorted(p.name for p in seg_root.iterdir()) == sorted(
            [plain, f'{plain}.json', dotted, f'{dotted}.json']), \
            sorted(p.name for p in seg_root.iterdir())


def test_segment_resume_contract(tmp):
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        sig = minted['sig']
        seg_dir = Path(docroot) / 'segments' / job

        def post_seg(n, payload):
            return _util.request(
                base + f'/segment?job={job}&seg={n}&total=4&sig={sig}', 'POST',
                body=payload,
                headers={'Content-Type': 'application/octet-stream'})

        for n in (0, 1, 3):
            status, body = post_seg(n, f'segment-{n}-bytes'.encode())
            assert status == 200, (n, status, body)
            assert json.loads(body) == {'ok': True}
        assert (seg_dir / '000000.ts').read_bytes() == b'segment-0-bytes'
        assert (seg_dir / '000003.ts').is_file()

        status, body = _util.get_json(base + f'/segment-status?job={job}&sig={sig}')
        assert status == 200, (status, body)
        assert body == {'done': [0, 1, 3], 'count': 3}, body

        # A re-post (resume retry) is idempotent: same status, new bytes.
        status, _ = post_seg(1, b'segment-1-RETRY')
        assert status == 200, status
        status, body = _util.get_json(base + f'/segment-status?job={job}&sig={sig}')
        assert body == {'done': [0, 1, 3], 'count': 3}, body
        assert (seg_dir / '000001.ts').read_bytes() == b'segment-1-RETRY'


def test_segment_status_validation(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.get_json(base + '/segment-status?job=..')
        assert status == 400 and body['error'] == 'bad job', (status, body)
        status, _ = _util.get_json(base + '/segment-status?job=a/b')
        assert status == 400, status
        status, _ = _util.get_json(base + '/segment-status')
        assert status == 400, status
        # An unknown job has no capability to check against: same 403 as a
        # wrong sig, not a 404 that would reveal which jobs exist.
        status, _ = _util.get_json(base + '/segment-status?job=never-seen')
        assert status == 403, status
        # Minted job + its capability: 200 with an empty list.
        _, minted = mint_job(base, TOK, 'never-seen')
        status, body = _util.get_json(
            base + f'/segment-status?job=never-seen&sig={minted["sig"]}')
        assert status == 200 and body == {'done': [], 'count': 0}, (status, body)


def test_segment_status_ignores_non_ascii_digit_filenames(tmp):
    """Only ASCII decimal segment stems are converted to result indices."""
    with _util.bridge(tmp) as (base, docroot):
        job = seg_job()
        _, minted = mint_job(base, TOK, job)
        seg_dir = Path(docroot) / 'segments' / job
        (seg_dir / '000001.ts').write_bytes(b'valid')
        (seg_dir / '\u00b2.ts').write_bytes(b'local artifact')

        try:
            status, body = _util.get_json(
                base + f'/segment-status?job={job}&sig={minted["sig"]}')
        except http.client.RemoteDisconnected as exc:
            raise AssertionError(
                'a non-ASCII digit filename ended /segment-status') from exc
        assert status == 200 and body == {'done': [1], 'count': 1}, (status, body)


def test_segment_status_enumeration_error_is_answered(tmp):
    """A job-directory enumeration failure returns a segment storage error."""
    fault_dir = Path(tmp) / 'segment-status-fault'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_iterdir = pathlib.Path.iterdir\n'
        'def _fail_segment_status_iterdir(path):\n'
        '    if path.parent.name == "segments" and path.name == "status-fault":\n'
        '        raise OSError("injected segment status failure")\n'
        '    return _real_iterdir(path)\n'
        'pathlib.Path.iterdir = _fail_segment_status_iterdir\n',
        encoding='utf-8')
    with _util.bridge(
            tmp, env={'PYTHONPATH': str(fault_dir)}) as (base, _docroot):
        job = 'status-fault'
        status, minted = mint_job(base, TOK, job)
        assert status == 200, (status, minted)

        try:
            status, body = _util.get_json(
                base + f'/segment-status?job={job}&sig={minted["sig"]}')
        except http.client.RemoteDisconnected as exc:
            raise AssertionError(
                'a segment status enumeration error ended GET') from exc
        assert status == 500, (status, body)
        assert body == {'error': 'segment storage failure'}, body

        health_status, health = _util.get_json(base + '/health')
        assert health_status == 200 and health['ok'] is True, (
            health_status, health)


def test_the_record_loader_answers_a_collision_and_a_corruption_apart(tmp):
    """Absent, name collision and corrupt are three answers, on every platform.

    The first version of this separation asked which exception the read
    raised, and that answer is per-platform: reading a directory raises
    IsADirectoryError on Linux and PermissionError on Windows, so the
    dotted-name collision — whose record path is another job's directory —
    stayed a clean 409 here and became a storage failure there. The question
    is about the path, so it is asked of the path.
    """
    probe = (
        'import os, server\n'
        'from daedalus_bridge import segment_store\n'
        'os.makedirs(server.SEG_DIR / "collide.json", exist_ok=True)\n'
        'print("collision:", segment_store.load_record("collide"))\n'
        '(server.SEG_DIR / "broken.json").write_text("{", encoding="utf-8")\n'
        'try:\n'
        '    segment_store.load_record("broken")\n'
        'except segment_store.SegmentRecordError:\n'
        '    print("corrupt: raised")\n'
        'print("absent:", segment_store.load_record("nothing"))\n')
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(Path(tmp) / 'docroot'),
        'DAEDALUS_PORT': '0',
        'DAEDALUS_TOKEN': TOK,
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    proc = subprocess.run(
        [sys.executable, '-c', probe], cwd=_util.ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    output = (proc.stdout + proc.stderr).strip()
    assert proc.returncode == 0, output
    assert 'collision: None' in output, output
    assert 'corrupt: raised' in output, output
    assert 'absent: None' in output, output


def test_a_legacy_conversion_that_cannot_read_the_directory_is_answered(tmp):
    """A legacy job whose directory cannot be enumerated is a storage error.

    The conversion has to measure what the job already holds before it can
    adopt it under the current quotas, so a scan that cannot run is a 500
    rather than an exception that would drop the connection or, worse, a
    silent adoption of an unknown amount.
    """
    fault_dir = Path(tmp) / 'segment-fault'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_iterdir = pathlib.Path.iterdir\n'
        'def _fail_legacy_conversion_iterdir(path):\n'
        '    if path.name == "legacy-fault" '
        'and path.parent.name == "segments":\n'
        '        raise OSError("injected legacy conversion failure")\n'
        '    return _real_iterdir(path)\n'
        'pathlib.Path.iterdir = _fail_legacy_conversion_iterdir\n',
        encoding='utf-8')
    with _util.bridge(
            tmp, env={'PYTHONPATH': str(fault_dir)}) as (base, docroot):
        job = 'legacy-fault'
        seg_root = Path(docroot) / 'segments'
        seg_dir = seg_root / job
        seg_dir.mkdir()
        (seg_dir / '000000.ts').write_bytes(b'abc')
        record_path = seg_root / f'{job}.json'
        legacy = {'token': TOK, 'sig': 'legacy-capability'}
        record_path.write_text(json.dumps(legacy), encoding='utf-8')

        try:
            status, body = mint_job(base, TOK, job)
        except http.client.RemoteDisconnected as exc:
            raise AssertionError(
                'a legacy conversion that cannot enumerate the job '
                'directory ended the connection') from exc
        assert status == 500 and body == {
            'error': 'segment storage failure'}, (status, body)
        assert json.loads(
            record_path.read_text(encoding='utf-8')) == legacy, 'unchanged'


def test_a_legacy_upgrade_that_cannot_write_its_record_answers_500(tmp):
    """A refused upgrade write is a 500 that leaves the record alone.

    The retry the temp write carries is bounded, so a refusal that never
    clears ends the conversion; the answer is the same 500 the other
    storage failures give, with neither a half-written temp nor a record
    that lost its owner behind it.
    """
    fault_dir = Path(tmp) / 'segment-fault'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        '_real_write_text = pathlib.Path.write_text\n'
        'def _refuse_every_record_write(path, data, **kw):\n'
        '    if path.name.endswith(".json.tmp"):\n'
        '        raise OSError("injected record write failure")\n'
        '    return _real_write_text(path, data, **kw)\n'
        'pathlib.Path.write_text = _refuse_every_record_write\n',
        encoding='utf-8')
    with _util.bridge(
            tmp, env={'PYTHONPATH': str(fault_dir)}) as (base, docroot):
        job = 'legacy-upgrade-fault'
        seg_root = Path(docroot) / 'segments'
        seg_dir = seg_root / job
        seg_dir.mkdir()
        (seg_dir / '000000.ts').write_bytes(b'abc')
        record_path = seg_root / f'{job}.json'
        legacy = {'token': TOK, 'sig': 'legacy-capability'}
        record_path.write_text(json.dumps(legacy), encoding='utf-8')

        try:
            status, body = mint_job(base, TOK, job)
        except http.client.RemoteDisconnected as exc:
            raise AssertionError(
                'a legacy upgrade whose record write was refused ended '
                'the connection') from exc
        assert status == 500 and body == {
            'error': 'segment storage failure'}, (status, body)
        assert json.loads(
            record_path.read_text(encoding='utf-8')) == legacy
        assert sorted(path.name for path in seg_root.glob('*.tmp')) == [], \
            sorted(path.name for path in seg_root.glob('*.tmp'))


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='segjobs_')


if __name__ == '__main__':
    raise SystemExit(main())
