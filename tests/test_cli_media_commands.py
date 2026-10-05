#!/usr/bin/env python3
"""Suite for daedalus_cli — the media subcommands' round trips.

uploads, screenshot and the segment subcommands against a real bridge:
the list/delete round trip, the encoding delimiter-bearing ids survive,
the saved bytes being this capture's rather than the next capture's,
the missing-file refusal carrying the bridge's own sentence, and the
segment capability's mint, status and refusals. The CLI is always run
as a subprocess, the way a shell would run it.
"""
import base64
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402
from _cli_helpers import BRIDGE_ENV, CLI, TOK, cli_env, run_cli  # noqa: E402
from _queueread import queued_command  # noqa: E402


def test_uploads_list_and_delete(tmp):
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'up9', 'filename': 'f.txt',
            'data': base64.b64encode(b'data').decode()})
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['uploads'], env)
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert 'up9/f.txt' in r.stdout and '1 files' in r.stdout, r.stdout
        r = run_cli(['uploads', '--delete', '--id', 'up9'], env)
        assert r.returncode == 0 and 'Deleted' in r.stdout, (
            r.returncode, r.stdout)
        assert not (Path(docroot) / 'uploads' / TOK / 'up9').exists()
        r = run_cli(['uploads'], env)
        assert r.returncode == 0 and 'No uploads' in r.stdout, r.stdout


def test_upload_listing_encodes_delimiter_and_unicode_id(tmp):
    """An upload filter reaches the exact delimiter-bearing upload id."""
    upload_id = 'upload&branch#café'
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        status, body = _util.post_json(base + '/upload', {
            'token': TOK,
            'id': upload_id,
            'filename': 'payload.txt',
            'data': base64.b64encode(b'query-safe').decode(),
        })
        assert status == 200, (status, body)
        result = run_cli(
            ['uploads', '--id', upload_id],
            cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert result.returncode == 0, (result.returncode, result.stderr)
        wanted = f'{upload_id}/payload.txt'
        assert wanted in result.stdout, (
            repr(wanted), repr(result.stdout))


def test_screenshot_download_encodes_delimiter_and_unicode_id(tmp):
    """The screenshot download query preserves the
    command/upload id exactly."""
    screenshot_id = 'shot&branch#café'
    output = Path(tmp) / 'captured.png'
    # The bridge's own log goes into the failure. This test times out on
    # Windows waiting for a result the test itself posted, and from a host
    # where it passes there is no way to tell whether the bridge stored the
    # result, stored it somewhere else, or never saw the request at all.
    served = []
    with _util.bridge(tmp, output=served, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['screenshot', '--id', screenshot_id,
                   '--output', str(output), '--timeout', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            command = queued_command(qdir, 'the screenshot command')
            assert command['id'] == screenshot_id, command
            status, body = _util.post_json(base + '/upload', {
                'token': TOK,
                'id': screenshot_id,
                'filename': 'screenshot.png',
                'data': base64.b64encode(b'encoded-screenshot').decode(),
            })
            assert status == 200, (status, body)
            status, body = _util.post_json(base + '/result', {
                'token': TOK,
                'tabId': 'extension',
                'id': screenshot_id,
                'result': {'path': f'{TOK}/{screenshot_id}/screenshot.png',
                           'size': len(b'encoded-screenshot')},
                'error': None,
                'ts': 1,
                '_did': command['_did'],
            })
            assert status == 200, (status, body)
            stdout, stderr = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
        assert proc.returncode == 0, (
            proc.returncode, repr(stdout), repr(stderr),
            repr(screenshot_id), ''.join(served))
        assert output.read_bytes() == b'encoded-screenshot'


def test_a_screenshot_download_ignores_a_later_capture_under_its_id(tmp):
    """The saved bytes are this capture's, not the next one's.

    `_ss` is the default screenshot id, so overlapping captures share an
    upload directory; a download that asked for the id got whichever file
    was newest when it asked, which is the other invocation's whenever one
    landed in between.
    """
    output = Path(tmp) / 'captured.png'
    served = []
    with _util.bridge(tmp, output=served, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['screenshot', '--output', str(output), '--timeout', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            command = queued_command(qdir, 'the screenshot command')
            assert command['id'] == '_ss', command
            for name, payload in (('mine.png', b'this-invocation'),
                                  ('later.png', b'the-next-invocation')):
                status, body = _util.post_json(base + '/upload', {
                    'token': TOK, 'id': '_ss', 'filename': name,
                    'data': base64.b64encode(payload).decode()})
                assert status == 200, (status, body)
            # The overlapping capture is strictly newer on disk, so a fetch
            # by id would answer with it. Stamped rather than assumed: two
            # writes can share a timestamp.
            shot_dir = Path(docroot) / 'uploads' / TOK / '_ss'
            os.utime(shot_dir / 'mine.png', (1_700_000_000, 1_700_000_000))
            os.utime(shot_dir / 'later.png', (1_700_000_100, 1_700_000_100))
            status, body = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': '_ss',
                'result': {'path': f'{TOK}/_ss/mine.png',
                           'size': len(b'this-invocation')},
                'error': None, 'ts': 1, '_did': command['_did'],
            })
            assert status == 200, (status, body)
            stdout, stderr = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
    assert proc.returncode == 0, (
        proc.returncode, repr(stdout), repr(stderr), ''.join(served))
    assert output.read_bytes() == b'this-invocation', output.read_bytes()


def test_a_missing_stored_screenshot_names_what_the_bridge_said(tmp):
    """The raw download must report the refusal, not just its status.

    The command round trip succeeds and only the follow-up fetch of the
    stored image fails, so the status number is the operator's only clue
    unless the body travels with it.
    """
    screenshot_id = 'shot-' + uuid.uuid4().hex[:8]
    output = Path(tmp) / 'captured.png'
    served = []
    with _util.bridge(tmp, output=served, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['screenshot', '--id', screenshot_id,
                   '--output', str(output), '--timeout', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            command = queued_command(qdir, 'the screenshot command')
            status, body = _util.post_json(base + '/result', {
                'token': TOK,
                'tabId': 'extension',
                'id': screenshot_id,
                'result': {'path': f'{TOK}/{screenshot_id}/screenshot.png',
                           'size': 18},
                'error': None,
                'ts': 1,
                '_did': command['_did'],
            })
            assert status == 200, (status, body)
            _stdout, stderr = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
    assert proc.returncode != 0, (proc.returncode, ''.join(served))
    assert 'HTTP 404' in stderr, (stderr, ''.join(served))
    assert 'no screenshot' in stderr, (stderr, ''.join(served))


def test_segment_job_subcommand_prints_a_working_capability(tmp):
    job = 'clijob-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['segment-job', job], env)
        assert r.returncode == 0, (r.returncode, r.stderr)
        sig = r.stdout.strip()
        assert sig, 'segment-job printed nothing'
        status, _ = _util.request(
            base + f'/segment?job={job}&seg=0&total=1&sig={sig}', 'POST',
            body=b'\x47', headers={'Content-Type': 'application/octet-stream'})
        assert status == 200, status
        # Re-running re-fetches the same capability (idempotent for the owner).
        r = run_cli(['segment-job', job], env)
        assert r.returncode == 0 and r.stdout.strip() == sig, (
            r.returncode, r.stdout)


def test_segment_status_subcommand(tmp):
    job = 'cliseg-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        status, body = _util.post_json(base + '/segment-job',
                                       {'token': TOK, 'job': job})
        assert status == 200, (status, body)
        status, _ = _util.request(
            base + f'/segment?job={job}&seg=0&total=2&sig={body["sig"]}',
            'POST',
            body=b'\x47', headers={'Content-Type': 'application/octet-stream'})
        assert status == 200, status
        r = run_cli(['segment-status', job],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert f'Job: {job}' in r.stdout and 'Segments: 1' in r.stdout, (
            r.stdout)


def test_segment_status_subcommand_encodes_job_and_capability(tmp):
    job = 'cliseg & hash# caf\u00e9-' + uuid.uuid4().hex[:12]
    sig = 'sig&part#tail'
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, body = _util.post_json(base + '/segment-job',
                                       {'token': TOK, 'job': job})
        assert status == 200, (status, body)
        record_path = docroot / 'segments' / f'{job}.json'
        record = json.loads(record_path.read_text(encoding='utf-8'))
        record['sig'] = sig
        record_path.write_text(json.dumps(record))

        r = run_cli(['segment-status', job],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode == 0, (r.returncode, r.stderr)
        wanted = f'Job: {job}  Segments: 0'
        assert wanted in r.stdout, (repr(wanted), repr(r.stdout))


def test_segment_status_subcommand_reports_a_foreign_job_cleanly(tmp):
    """A job owned by another token is the CLI's own sentence, not the bare
    'HTTP 409: ...' that the generic api() error path would have exited with.
    """
    job = 'cliforeign-' + uuid.uuid4().hex[:12]
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        segment_root = Path(docroot) / 'segments'
        (segment_root / job).mkdir()
        (segment_root / f'{job}.json').write_text(json.dumps({
            'token': 'earlierconfigured',
            'sig': 'persistedforeigncapability',
            'max_segment_index': 10,
            'max_segment_count': 10,
            'max_bytes': 100,
        }))
        r = run_cli(['segment-status', job],
                    cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK))
        assert r.returncode != 0, (r.returncode, r.stdout)
        assert 'owned by a different token' in r.stderr, r.stderr
        assert 'HTTP 409' not in r.stderr, r.stderr


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
