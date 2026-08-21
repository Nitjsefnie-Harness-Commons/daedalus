#!/usr/bin/env python3
"""Caller-supplied values must never become path components unchecked.

Every test here failed against the code as it stood before these fixes, which
is the only reason to trust them. The bug they pin was real: `POST /result`
validated the token and nothing else, so a `tabId` of `../../x` wrote
attacker-controlled JSON wherever it pointed, and `GET /result?tab=` read it
back. The same shape existed on `GET /upload?id=` and `GET /screenshot?id=`.

The assertion is deliberately not just the status code. A handler can answer
400 and still have written the file, so each test checks the filesystem
OUTSIDE the docroot as well — that is the part that actually matters.
"""
import json
import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402

TOKEN = 'toksafety'
os.environ['TOKEN'] = ''
os.environ['DAEDALUS_TOKEN'] = TOKEN
# One component up from the docroot, plus a name no legitimate handler writes.
ESCAPES = ('../escaped', '..', 'a/b', 'a\\b')
WINDOWS_UNSAFE = (
    'x:y', 'x<y', 'x>y', 'x"y', 'x|y', 'x?y', 'x*y',
    'x\x01y', 'x\x7fy', 'x\x85y', 'CON', 'con.txt', 'alias.', 'alias ',
)


def _outside(docroot):
    """Paths a traversal would land on, and must not create."""
    parent = os.path.dirname(str(docroot))
    return [os.path.join(parent, name) for name in os.listdir(parent)
            if name != os.path.basename(str(docroot))]


def test_a_tab_id_cannot_escape_the_results_directory(tmp):
    with _util.bridge(tmp) as (base, docroot):
        before = set(_outside(docroot))
        for tab in ESCAPES:
            status, _ = _util.post_json(
                base + '/result',
                {'token': TOKEN, 'tabId': tab, 'id': 'x', 'result': 'y'})
            assert status == 400, (
                f'POST /result accepted tabId {tab!r} with {status}')
        after = set(_outside(docroot))
        assert after == before, (
            f'a rejected tabId still created {sorted(after - before)}')
        # And nothing landed inside either, under any spelling.
        written = [p for p in (docroot / 'results').rglob('*') if p.is_file()]
        assert not written, f'rejected results were still written: {written}'


def test_an_embedded_nul_tab_id_is_answered_not_dropped(tmp):
    """A NUL must be refused before pathlib passes it to the filesystem."""
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(
            base + '/result',
            {'token': TOKEN, 'tabId': 'tab\x00suffix', 'id': 'x', 'result': 'y'})
        assert status == 400, f'a NUL tabId returned {status}: {body}'
        assert body['error'] == 'invalid path component', body
        assert not list((docroot / 'results').rglob('*')), (
            'a rejected NUL tabId still wrote a result')

        # The refusal is narrow: a normal component still completes the same
        # end-to-end write, and the server stays responsive after the bad call.
        status, body = _util.post_json(
            base + '/result',
            {'token': TOKEN, 'tabId': 'tab-01', 'id': 'ok', 'result': 'kept'})
        assert status == 200 and body == {'ok': True}, (status, body)
        stored = docroot / 'results' / f'{TOKEN}_tab-01.json'
        assert json.loads(stored.read_text())['result'] == 'kept'
        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


def test_a_tab_query_cannot_read_outside_the_results_directory(tmp):
    with _util.bridge(tmp) as (base, docroot):
        # Plant a file one level up that a traversal would reach.
        planted = os.path.join(os.path.dirname(str(docroot)), 'escaped.json')
        with open(planted, 'w', encoding='utf-8') as fh:
            json.dump({'secret': 'do not serve me'}, fh)
        try:
            status, body = _util.get(
                base + f'/result?token={TOKEN}&tab=../escaped')
            assert status == 400, f'GET /result served a traversing tab: {status}'
            assert b'do not serve me' not in body, 'the planted file was served'
        finally:
            os.unlink(planted)


def test_an_upload_id_cannot_escape_on_read(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        for path in ('/upload', '/screenshot'):
            for bad in ('..', '../..', 'a/b', 'a\\b'):
                status, _ = _util.get(
                    base + f'{path}?token={TOKEN}&id={bad}')
                assert status == 400, (
                    f'GET {path} accepted id {bad!r} with {status}')


def test_a_token_may_not_contain_a_backslash(tmp):
    """Inert on POSIX, a nested path on Windows — so it is rejected on both.

    The check listed `/` and `.` and not `\\`, which meant the same token was a
    plain name on one platform and a directory separator on another.
    """
    with _util.bridge(tmp) as (base, docroot):
        status, _ = _util.post_json(
            base + '/result', {'token': 'a\\b', 'id': 'x', 'result': 'y'})
        assert status == 400, f'a backslash token was accepted: {status}'
        assert not list((docroot / 'results').rglob('*')), (
            'a backslash token still wrote a result')


def test_an_ascii_letter_segment_is_answered_not_dropped(tmp):
    """The ordinary non-decimal syntax branch answers 400 before conversion."""
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.request(
            base + '/segment?job=j&seg=notanumber&total=1',
            'POST', body=b'\x00\x01',
            headers={'Content-Type': 'application/octet-stream'})
        assert status == 400, f'a non-numeric seg returned {status}'
        assert b'seg' in body.lower(), f'the refusal does not mention seg: {body!r}'


def _mint_segment_capability(base, job):
    status, body = _util.post_json(
        base + '/segment-job', {'token': TOKEN, 'job': job})
    assert status == 200 and body['sig'], (status, body)
    return body['sig']


def _post_segment(base, job, sig, segment):
    return _util.request(
        base + f'/segment?job={job}&seg={quote(segment, safe="")}&total=1&sig={sig}',
        'POST', body=b'bytes',
        headers={'Content-Type': 'application/octet-stream'})


def test_a_superscript_digit_segment_is_answered_not_dropped(tmp):
    """Unicode digits outside ASCII must not reach integer conversion."""
    with _util.bridge(tmp) as (base, docroot):
        job = 'superscript-segment'
        sig = _mint_segment_capability(base, job)
        status, body = _post_segment(base, job, sig, '²')
        assert status == 400, f'a superscript digit seg returned {status}'
        assert b'seg' in body.lower(), body
        assert not list((docroot / 'segments' / job).glob('*.ts'))


def test_a_five_thousand_digit_segment_is_answered_not_dropped(tmp):
    """An overlong ASCII decimal must be bounded before integer conversion."""
    with _util.bridge(tmp) as (base, docroot):
        job = 'overlong-segment'
        sig = _mint_segment_capability(base, job)
        status, body = _post_segment(base, job, sig, '9' * 5000)
        assert status == 400, f'a 5,000-digit seg returned {status}'
        assert b'seg' in body.lower(), body
        assert not list((docroot / 'segments' / job).glob('*.ts'))


def test_a_malformed_body_is_refused_on_every_verb(tmp):
    """PUT and DELETE parsed the body unguarded; POST already answered 400."""
    with _util.bridge(tmp) as (base, _docroot):
        for verb, path in (('PUT', '/command'), ('DELETE', '/upload'),
                           ('POST', '/result')):
            status, _ = _util.request(base + path, verb, body=b'{not json',
                                      headers={'Content-Type': 'application/json'})
            assert status == 400, (
                f'{verb} {path} did not refuse a malformed body: {status}')


def test_non_alphabet_base64_is_refused_not_stored_empty(tmp):
    """`b64decode` without validate=True silently decoded non-alphabet input to b''.

    A 200 and a zero-byte file is the worst answer available: the caller
    believes the upload succeeded and the bytes are simply gone.
    """
    with _util.bridge(tmp) as (base, docroot):
        status, _ = _util.post_json(
            base + '/upload',
            # Four non-alphabet characters: a valid LENGTH, so the lenient
            # decoder discards them all and returns b'' instead of raising.
            # An input that also breaks padding would be refused either way and
            # would pin nothing.
            {'token': TOKEN, 'id': 'shot', 'data': '!!!!'})
        assert status == 400, f'non-alphabet base64 returned {status}'
        stored = [p for p in (docroot / 'uploads').rglob('*') if p.is_file()]
        assert not stored, f'a rejected upload was still stored: {stored}'


def test_a_command_tab_cannot_escape_the_queue_directory(tmp):
    """`PUT /command` joined `tab` into the queue path with nothing checked.

    Found by an audit that reproduced it: `tab='q/../../../escaped'` resolved a
    queue outside the docroot and wrote a real command file there. The read
    side is worse than the write — `/stream` drains that queue and UNLINKS what
    it serves, so the same value deletes files it reached.
    """
    with _util.bridge(tmp) as (base, docroot):
        before = set(_outside(docroot))
        for tab in ('q/../../../escaped', '..', 'a/b', 'a\\b'):
            status, _ = _util.request(
                base + '/command', 'PUT',
                body={'token': TOKEN, 'tab': tab, 'id': 'proof', 'code': '1'})
            assert status == 400, (
                f'PUT /command accepted tab {tab!r} with {status}')
        assert set(_outside(docroot)) == before, 'a rejected tab still wrote outside'
        assert not list((docroot / 'commands').rglob('*.json')), (
            'a rejected tab still queued a command')


def test_a_stream_tab_cannot_select_a_queue_outside_the_docroot(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        status, _ = _util.get(
            base + f'/stream?token={TOKEN}&tab=../../escaped', timeout=15)
        assert status == 400, f'GET /stream accepted a traversing tab: {status}'


def test_a_screenshot_format_cannot_become_a_path_fragment(tmp):
    """`format` was appended to the stored filename with nothing checked."""
    with _util.bridge(tmp) as (base, docroot):
        for fmt in ('../../escaped', 'png/../..', 'a\\b'):
            status, _ = _util.post_json(
                base + '/upload',
                {'token': TOKEN, 'id': 'shot', 'data': 'AAAA', 'format': fmt})
            assert status == 400, f'upload accepted format {fmt!r} with {status}'
        assert not [p for p in (docroot / 'uploads').rglob('*') if p.is_file()], (
            'a rejected format still stored a file')


def test_a_windows_drive_component_cannot_escape_the_data_root(tmp):
    """`C:escape` is drive-qualified on Windows, and joining it discards the root.

    Inert on POSIX, which is why it survived three passes: the check listed
    `..`, `/` and `\\` and stopped there.
    """
    with _util.bridge(tmp) as (base, docroot):
        for value in ('C:escape', 'D:', 'x:y'):
            status, _ = _util.post_json(
                base + '/result',
                {'token': TOKEN, 'tabId': value, 'id': 'x', 'result': 'y'})
            assert status == 400, f'tabId {value!r} accepted with {status}'
            status, _ = _util.post_json(
                base + '/result', {'token': value, 'id': 'x', 'result': 'y'})
            assert status == 400, f'token {value!r} accepted with {status}'
        assert not list((docroot / 'results').rglob('*')), 'a drive-qualified value wrote a result'


def test_upload_path_components_reject_windows_aliases(tmp):
    """Upload and delete reject drive, device, and Windows-normalized names."""
    with _util.bridge(tmp) as (base, docroot):
        good = 'eA=='
        for bad in WINDOWS_UNSAFE:
            status, body = _util.post_json(
                base + '/upload',
                {'token': bad, 'id': 'normal-id', 'data': good})
            assert status == 400 and body['error'] == 'bad token', (
                'POST /upload accepted token', bad, status, body)

            status, body = _util.post_json(
                base + '/upload',
                {'token': TOKEN, 'id': bad, 'data': good})
            assert status == 400 and body['error'] == 'invalid path component', (
                'POST /upload accepted id', bad, status, body)

            status, body = _util.post_json(
                base + '/upload',
                {'token': TOKEN, 'id': 'normal-id', 'filename': bad,
                 'data': good})
            assert status == 400 and body['error'] == 'invalid path component', (
                'POST /upload accepted filename', bad, status, body)

            status, body = _util.request(
                base + '/upload', 'DELETE',
                body={'token': TOKEN, 'id': bad, 'filename': 'normal.txt'})
            assert status == 400, ('DELETE /upload accepted id', bad, status, body)

            status, body = _util.request(
                base + '/upload', 'DELETE',
                body={'token': TOKEN, 'id': 'normal-id', 'filename': bad})
            assert status == 400, (
                'DELETE /upload accepted filename', bad, status, body)

        status, body = _util.post_json(
            base + '/upload',
            {'token': TOKEN, 'id': 'normal-id', 'filename': 'normal.txt',
             'data': good})
        assert status == 200, ('normal upload was rejected', status, body)
        assert (docroot / 'uploads' / TOKEN / 'normal-id' / 'normal.txt').is_file()


def test_segment_job_rejects_windows_aliases(tmp):
    """Job names are validated before the capability check, so a Windows
    alias is a 400 even from a request that carries no sig — never a write."""
    with _util.bridge(tmp) as (base, _docroot):
        for job in ('bad:job', 'CON', 'alias.', 'alias '):
            status, body = _util.request(
                base + '/segment?job=' + quote(job, safe='') + '&seg=1&total=2', 'POST',
                body=b'bytes',
                headers={'Content-Type': 'application/octet-stream'})
            assert status == 400, ('POST /segment accepted job', job, status, body)


def test_one_token_cannot_read_another_tokens_results(tmp):
    """`<token>_<tab>` is ambiguous unless the token cannot contain `_`.

    Token `victim_x` and the pair (token `victim`, tab `x`) name the same file,
    so one principal's result is readable by another and both write the same
    command queue. Reproduced against the real server before the fix.
    """
    with _util.bridge(tmp) as (base, _docroot):
        status, _ = _util.post_json(
            base + '/result', {'token': 'victim_x', 'id': 'x', 'result': 'SECRET'})
        assert status == 400, f'an underscored token was accepted with {status}'
        status, body = _util.get(base + '/result?token=victim&tab=x')
        assert b'SECRET' not in body, 'one token read another token\'s result'


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='pathsafety_')


if __name__ == '__main__':
    raise SystemExit(main())
