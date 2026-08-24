#!/usr/bin/env python3
"""Stored files: uploads, the screenshots that are uploads, and deletion.

`POST /upload` is the one route that writes caller-named paths, so most of
what these tests assert is refusal — a component that escapes its directory,
a byte sequence too long to store, a format nothing can serve back — beside
the listing, paging and delete surfaces that read what was stored.
"""
import base64
import http.client
import json
import os
import urllib.parse
import urllib.request
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _bridge import PNG, TOK  # noqa: E402


def test_upload_list_screenshot_delete(tmp):
    with _util.bridge(tmp) as (base, docroot):
        # Screenshot form: no filename, stored as <ts>.png
        payload = {'token': TOK, 'id': 'up1',
                   'data': base64.b64encode(PNG).decode()}
        status, body = _util.post_json(base + '/upload', payload)
        assert status == 200, (status, body)
        assert body['ok'] is True and body['size'] == len(PNG)
        assert body['path'].startswith(f'{TOK}/up1/') and body['path'].endswith('.png')
        stored = Path(docroot) / 'uploads' / body['path']
        assert stored.is_file() and stored.read_bytes() == PNG

        # Named-file form.
        text = b'hello upload'
        payload = {'token': TOK, 'id': 'up1', 'filename': 'note.txt',
                   'data': base64.b64encode(text).decode()}
        status, body = _util.post_json(base + '/upload', payload)
        assert status == 200, (status, body)
        assert (Path(docroot) / 'uploads' / TOK / 'up1' / 'note.txt').read_bytes() == text

        # Listing, bare-array back-compat form.
        status, body = _util.get_json(base + f'/upload?token={TOK}')
        assert status == 200 and isinstance(body, list) and len(body) == 2, body
        names = {e['filename'] for e in body}
        assert names == {'note.txt', stored.name}, names
        entry = next(e for e in body if e['filename'] == 'note.txt')
        assert entry['id'] == 'up1' and entry['size'] == len(text)
        assert entry['path'] == f'{TOK}/up1/note.txt'
        assert isinstance(entry['mtime'], int)

        # Listing filtered by id.
        status, body = _util.get_json(base + f'/upload?token={TOK}&id=up1')
        assert status == 200 and len(body) == 2, body
        status, body = _util.get_json(base + f'/upload?token={TOK}&id=missing')
        assert status == 200 and body == [], body

        # Paginated form returns the envelope.
        status, body = _util.get_json(base + f'/upload?token={TOK}&limit=1')
        assert status == 200, (status, body)
        assert body['total'] == 2 and body['limit'] == 1 and body['offset'] == 0
        assert len(body['items']) == 1
        status, body = _util.get_json(base + f'/upload?token={TOK}&limit=x')
        assert status == 400, status

        # Screenshot serving, per id and latest-across-ids.
        status, raw = _util.get(base + f'/screenshot?token={TOK}&id=up1')
        assert status == 200 and raw == PNG, (status, raw[:40])
        status, raw = _util.get(base + f'/screenshot?token={TOK}')
        assert status == 200 and raw == PNG, (status, raw[:40])

        # DELETE one file, then the id dir, then the token dir.
        status, body = _util.request(base + '/upload', 'DELETE',
                                     body={'token': TOK, 'id': 'up1',
                                           'filename': 'note.txt'})
        assert status == 200, (status, body)
        assert not (Path(docroot) / 'uploads' / TOK / 'up1' / 'note.txt').exists()
        status, body = _util.request(base + '/upload', 'DELETE',
                                     body={'token': TOK, 'id': 'up1'})
        assert status == 200, (status, body)
        assert not (Path(docroot) / 'uploads' / TOK / 'up1').exists()
        status, body = _util.get_json(base + f'/screenshot?token={TOK}')
        assert status == 404, (status, body)
        status, body = _util.request(base + '/upload', 'DELETE', body={'token': TOK})
        assert status == 200, (status, body)
        assert not (Path(docroot) / 'uploads' / TOK).exists()
        status, body = _util.request(base + '/upload', 'DELETE', body={'token': TOK})
        assert status == 404, (status, body)


def test_upload_validation_and_traversal(tmp):
    with _util.bridge(tmp) as (base, docroot):
        docroot = Path(docroot)
        good = base64.b64encode(b'x').decode()
        # Missing parameters.
        status, body = _util.post_json(base + '/upload', {'token': TOK, 'data': good})
        assert status == 400 and body['error'] == 'missing id', (status, body)
        status, body = _util.post_json(base + '/upload', {'token': TOK, 'id': 'u'})
        assert status == 400 and body['error'] == 'missing data', (status, body)
        status, body = _util.post_json(base + '/upload',
                                       {'token': TOK, 'id': 'u', 'data': 'a'})
        assert status == 400 and body['error'] == 'invalid base64', (status, body)

        # Path components containing .., / or \ are refused before any write.
        escapes = [
            {'id': '../x', 'data': good},
            {'id': 'a\\b', 'data': good},
            {'id': 'a/b', 'data': good},
            {'id': 'u', 'data': good, 'filename': '../evil.txt'},
            {'id': 'u', 'data': good, 'filename': '..\\evil.txt'},
            {'id': 'u', 'data': good, 'filename': 'sub/f.txt'},
        ]
        for fields in escapes:
            status, body = _util.post_json(base + '/upload', {'token': TOK, **fields})
            assert status == 400, (fields, status, body)
            assert body['error'] == 'invalid path component', body
        # Token traversal is caught earlier, at dispatch.
        status, body = _util.post_json(base + '/upload',
                                       {'token': 'a/b', 'id': 'u', 'data': good})
        assert status == 400 and body['error'] == 'bad token', (status, body)

        # The point of the exercise: nothing was written, inside or outside the
        # docroot. tmp held only docroot/ before this test and must still.
        assert os.listdir(tmp) == ['docroot'], os.listdir(tmp)
        uploads = docroot / 'uploads'
        created = [str(p.relative_to(uploads)) for p in uploads.rglob('*')] \
            if uploads.is_dir() else []
        assert created == [], created


def test_upload_path_component_byte_boundaries(tmp):
    """Upload ids and filenames are capped by encoded bytes, not characters."""
    data = base64.b64encode(b'edge').decode()
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': 'i' * 256,
                               'filename': 'edge.bin', 'data': data})
        assert status == 400, (status, body)

        boundary_id = 'i' * 240
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': boundary_id,
                               'filename': 'edge.bin', 'data': data})
        assert status == 200, (status, body)
        assert (docroot / 'uploads' / TOK / boundary_id / 'edge.bin').is_file()

        boundary_filename = 'é' * 120
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': 'encoded-boundary',
                               'filename': boundary_filename, 'data': data})
        assert status == 200, (status, body)
        assert (docroot / 'uploads' / TOK / 'encoded-boundary'
                / boundary_filename).is_file()

        for fields in (
                {'id': 'i' * 241, 'filename': 'edge.bin'},
                {'id': 'encoded-over', 'filename': 'é' * 121}):
            status, body = _util.post_json(
                base + '/upload', {'token': TOK, **fields, 'data': data})
            assert status == 400, (fields, status, body)


def test_delete_upload_path_component_byte_boundaries(tmp):
    """Delete accepts the encoded ceiling and refuses longer components."""
    data = base64.b64encode(b'edge').decode()
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.request(
            base + '/upload', 'DELETE',
            body={'token': TOK, 'id': 'i' * 256})
        assert status == 400, (status, body)

        boundary_id = 'i' * 240
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': boundary_id,
                               'filename': 'edge.bin', 'data': data})
        assert status == 200, (status, body)
        status, body = _util.request(
            base + '/upload', 'DELETE',
            body={'token': TOK, 'id': boundary_id})
        assert status == 200, (status, body)

        boundary_filename = 'f' * 240
        status, body = _util.post_json(
            base + '/upload', {'token': TOK, 'id': 'delete-file',
                               'filename': boundary_filename, 'data': data})
        assert status == 200, (status, body)
        status, body = _util.request(
            base + '/upload', 'DELETE',
            body={'token': TOK, 'id': 'delete-file',
                  'filename': boundary_filename})
        assert status == 200, (status, body)

        for fields in (
                {'id': 'i' * 241},
                {'id': 'delete-file', 'filename': 'f' * 241}):
            status, body = _util.request(
                base + '/upload', 'DELETE', body={'token': TOK, **fields})
            assert status == 400, (fields, status, body)


def test_delete_upload_validation(tmp):
    with _util.bridge(tmp) as (base, docroot):
        for fields in ({'id': '../x'}, {'id': 'u', 'filename': 'a\\b'},
                       {'id': 'u', 'filename': 'a/b'}):
            status, body = _util.request(base + '/upload', 'DELETE',
                                         body={'token': TOK, **fields})
            assert status == 400, (fields, status, body)
            assert json.loads(body)['error'] == 'invalid path component'
        status, body = _util.request(base + '/upload', 'DELETE',
                                     body={'token': TOK, 'id': 'ghost'})
        assert status == 404, (status, body)
        assert json.loads(body)['error'] == 'id not found'
        # Nothing was created by any of that.
        assert os.listdir(tmp) == ['docroot'], os.listdir(tmp)
        uploads = Path(docroot) / 'uploads'
        assert list(uploads.iterdir()) == [], list(uploads.iterdir())


def test_a_filename_without_an_id_deletes_nothing(tmp):
    """The narrowest delete must not fall through to the widest one.

    `{token, filename}` matched neither the file branch nor the id branch and
    landed in the one that removes the token's entire upload namespace, so
    naming a single file deleted every upload the token had — and answered
    that as a success.
    """
    with _util.bridge(tmp) as (base, docroot):
        for upload_id, name in (('alpha', 'one.txt'), ('beta', 'two.txt')):
            status, body = _util.post_json(base + '/upload', {
                'token': TOK, 'id': upload_id, 'filename': name,
                'data': base64.b64encode(b'keep me').decode()})
            assert status == 200, (status, body)

        status, body = _util.request(
            base + '/upload', 'DELETE',
            body={'token': TOK, 'filename': 'one.txt'})
        assert status == 400, (status, body)

        root = Path(docroot) / 'uploads' / TOK
        assert (root / 'alpha' / 'one.txt').is_file(), sorted(root.rglob('*'))
        assert (root / 'beta' / 'two.txt').is_file(), sorted(root.rglob('*'))


def test_an_unhashable_upload_format_is_refused_not_dropped(tmp):
    """A format that is not a string must never reach a membership test.

    `[] in SCREENSHOT_TYPES` raises TypeError instead of answering False, and
    the exception killed the request thread — so an authenticated caller got a
    dropped connection where the same line already knew how to write a 400.
    """
    with _util.bridge(tmp) as (base, _docroot):
        for value in ([], {}, ['png'], 5, None, True):
            status, body = _util.post_json(base + '/upload', {
                'token': TOK, 'id': 'fmt', 'filename': 'shot.png',
                'data': base64.b64encode(PNG).decode(), 'format': value})
            assert status == 400, (value, status, body)
            assert body == {'error': 'unsupported format'}, (value, body)
        # The bridge is still answering: no request thread was lost.
        status, body = _util.get_json(base + '/health')
        assert status == 200 and body['ok'] is True, (status, body)


def test_an_upload_path_that_escapes_through_a_symlink_is_refused(tmp):
    """Component validation cannot answer where a path ended up.

    `_unsafe_component` is a shape check on one string: `escape` passes it,
    because there is nothing wrong with the name. If `escape` is a symlink out
    of the token's directory, every component is harmless and the path still
    leaves the namespace — which is the one thing the check exists to prevent.

    The containment check asks the other question, about the result rather
    than the parts, so the delete is refused and the file outside survives.
    """
    with _util.bridge(tmp) as (base, docroot):
        status, _ = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'real', 'filename': 'keep.txt',
            'data': base64.b64encode(b'inside').decode()})
        assert status == 200, status

        outside = Path(docroot) / 'outside'
        outside.mkdir()
        secret = outside / 'secret.txt'
        secret.write_text('do not delete me', encoding='utf-8')
        token_dir = Path(docroot) / 'uploads' / TOK
        try:
            (token_dir / 'escape').symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as why:
            _util.skip(f'this filesystem will not hold a symlink: {why}')

        status, raw = _util.request(
            base + '/upload', 'DELETE',
            body=json.dumps({'token': TOK, 'id': 'escape',
                             'filename': 'secret.txt'}).encode(),
            headers={'Content-Type': 'application/json'})
        assert (status, json.loads(raw).get('error')) == (
            400, 'invalid path component'), (status, raw)
        assert secret.is_file(), 'the file outside the namespace was removed'
        assert secret.read_text(encoding='utf-8') == 'do not delete me'

        # The ordinary path still works, so the guard is not simply refusing.
        status, raw = _util.request(
            base + '/upload', 'DELETE',
            body=json.dumps({'token': TOK, 'id': 'real',
                             'filename': 'keep.txt'}).encode(),
            headers={'Content-Type': 'application/json'})
        assert status == 200, (status, raw)


def test_upload_pagination_bounds_the_work_not_only_the_answer(tmp):
    """A page of one must not cost a stat of every file in the namespace.

    Pagination bounded the response and nothing else: every upload directory
    was enumerated, every file statted twice to build a record, the whole list
    materialized, and only then sliced. The count still has to visit every
    entry — `total` says how many pages there are — but counting an entry is
    what the kernel already told us, and describing one is a syscall.

    Measured rather than asserted about the code: sitecustomize counts every
    os.stat under the uploads root, so the number is what the handler actually
    did.
    """
    fault_dir = Path(tmp) / 'stat-counter'
    fault_dir.mkdir()
    counts = Path(tmp) / 'stat-calls'
    (fault_dir / 'sitecustomize.py').write_text(
        'import os\n'
        '_real = os.stat\n'
        '_log = open(os.environ["STAT_LOG"], "ab", buffering=0)\n'
        'def _counted(path, *args, **kwargs):\n'
        '    try:\n'
        '        if "uploads" in os.fspath(path):\n'
        '            _log.write(b".")\n'
        '    except TypeError:\n'
        '        pass\n'
        '    return _real(path, *args, **kwargs)\n'
        'os.stat = _counted\n',
        encoding='utf-8')
    env = {'PYTHONPATH': str(fault_dir), 'STAT_LOG': str(counts)}
    ids, per_id = 4, 15
    with _util.bridge(tmp, env=env) as (base, _docroot):
        for id_index in range(ids):
            for file_index in range(per_id):
                status, _ = _util.post_json(base + '/upload', {
                    'token': TOK, 'id': f'batch{id_index}',
                    'filename': f'file{file_index}.txt',
                    'data': base64.b64encode(b'x').decode()})
                assert status == 200, status

        before = counts.stat().st_size
        query = urllib.parse.urlencode({'token': TOK, 'limit': 1, 'offset': 0})
        status, body = _util.get_json(f'{base}/upload?{query}')
        after = counts.stat().st_size
    assert status == 200, (status, body)
    assert body['total'] == ids * per_id, body
    assert len(body['items']) == 1, body

    stats = after - before
    # One per id directory to order them, one for the file being described.
    # The old shape was two per file across the whole namespace.
    assert stats <= ids + 4, (
        f'listing one upload cost {stats} stats over {ids * per_id} files')


def test_upload_pagination_is_validated_before_the_directory_is_looked_at(tmp):
    """The same query must get the same answer whether or not files exist.

    The missing-directory shortcut returned before limit and offset were
    parsed, so a malformed `limit` answered 200 on an empty data root and 400
    once any upload had created the directory — the validity of a request
    depended on unrelated filesystem state. The empty page also reported
    limit 0 and offset 0 rather than what was asked for.
    """
    with _util.bridge(tmp) as (base, _docroot):
        malformed = base + '/upload?' + urllib.parse.urlencode(
            {'token': TOK, 'limit': 'not-an-int'})
        status, body = _util.get_json(malformed)
        assert status == 400, (status, body)

        status, body = _util.get_json(base + '/upload?' + urllib.parse.urlencode(
            {'token': TOK, 'limit': 17, 'offset': 9, 'id': 'absent'}))
        assert status == 200, (status, body)
        assert body == {'items': [], 'total': 0, 'limit': 17, 'offset': 9}, body

        # The same two answers once the directory exists.
        status, _ = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'up1',
            'data': base64.b64encode(PNG).decode()})
        assert status == 200, status
        status, body = _util.get_json(malformed)
        assert status == 400, (status, body)


def test_a_screenshot_path_serves_the_file_that_result_named(tmp):
    """Fetching by id returns the newest file; a capture wants its own.

    Screenshot ids are reused — `_ss` is the default — so a second capture
    under the same id lands beside the first. A client that correlated its
    own result and then fetched by id downloaded whichever file was newest
    at that moment, which is the next invocation's whenever one overlapped.
    """
    with _util.bridge(tmp) as (base, docroot):
        for name, payload in (('capture-a.png', PNG + b'-A'),
                              ('capture-b.png', PNG + b'-B')):
            status, body = _util.post_json(base + '/upload', {
                'token': TOK, 'id': '_ss', 'filename': name,
                'data': base64.b64encode(payload).decode()})
            assert status == 200, (status, body)
            assert body['path'] == f'{TOK}/_ss/{name}', body
        # Order the two by mtime explicitly: which one an id fetch picks is
        # the whole point, and a same-millisecond tie would decide it by
        # directory order instead.
        shot_dir = docroot / 'uploads' / TOK / '_ss'
        os.utime(shot_dir / 'capture-a.png', (1_700_000_000, 1_700_000_000))
        os.utime(shot_dir / 'capture-b.png', (1_700_000_100, 1_700_000_100))

        status, newest = _util.get(base + f'/screenshot?token={TOK}&id=_ss')
        assert status == 200 and newest == PNG + b'-B', (status, newest[:32])
        for name, payload in (('capture-a.png', PNG + b'-A'),
                              ('capture-b.png', PNG + b'-B')):
            named = urllib.parse.urlencode(
                {'token': TOK, 'path': f'{TOK}/_ss/{name}'})
            status, served = _util.get(f'{base}/screenshot?{named}')
            assert status == 200 and served == payload, (
                name, status, served[:32])


def test_a_screenshot_path_cannot_leave_its_own_token(tmp):
    """The named path is a component list, checked the way every other is."""
    with _util.bridge(tmp) as (base, docroot):
        status, body = _util.post_json(base + '/upload', {
            'token': TOK, 'id': 'mine', 'filename': 'shot.png',
            'data': base64.b64encode(PNG).decode()})
        assert status == 200, (status, body)
        (docroot / 'uploads' / 'othertok' / 'theirs').mkdir(parents=True)
        (docroot / 'uploads' / 'othertok' / 'theirs' / 'shot.png').write_bytes(
            PNG + b'-THEIRS')
        (docroot / 'uploads' / TOK / 'mine' / 'notes.txt').write_bytes(b'text')

        for path, expected in (
                ('../othertok/theirs/shot.png', 400),
                (f'{TOK}/../othertok/theirs/shot.png', 400),
                ('othertok/theirs/shot.png', 404),
                (f'{TOK}/mine/notes.txt', 404),
                (f'{TOK}/mine/absent.png', 404)):
            query = urllib.parse.urlencode({'token': TOK, 'path': path})
            status, body = _util.get(f'{base}/screenshot?{query}')
            assert status == expected, (path, status, body[:120])


def test_every_accepted_screenshot_format_can_be_served_back(tmp):
    """A format the upload route accepts must be one /screenshot can return.

    `webp` was accepted, stored and answered 200, and then /screenshot said
    `no screenshot` because discovery listed three suffixes and the accepted
    set had four. The two lists are now one list, so they cannot drift again;
    this walks every accepted format rather than naming the one that was
    missing.
    """
    with _util.bridge(tmp) as (base, _docroot):
        for index, fmt in enumerate(('png', 'jpeg', 'jpg', 'webp')):
            upload_id = f'shot{index}'
            status, body = _util.post_json(base + '/upload', {
                'token': TOK, 'id': upload_id,
                'data': base64.b64encode(PNG).decode(), 'format': fmt})
            assert status == 200, (fmt, status, body)
            query = urllib.parse.urlencode({'token': TOK, 'id': upload_id})
            request = urllib.request.Request(base + '/screenshot?' + query)
            with urllib.request.urlopen(request, timeout=10) as reply:
                served = reply.read()
                content_type = reply.headers.get('Content-Type')
            assert served == PNG, (fmt, len(served))
            assert content_type and content_type.startswith('image/'), (
                fmt, content_type)


def test_result_upload_delete_filesystem_errors_are_answered(tmp):
    """Residual result, upload, and delete OSError paths return HTTP status."""
    fault_dir = Path(tmp) / 'path-fault-injection'
    fault_dir.mkdir()
    (fault_dir / 'sitecustomize.py').write_text(
        'import pathlib\n'
        'import shutil\n'
        '_real_write_bytes = pathlib.Path.write_bytes\n'
        'def _fail_storage_write(path, data):\n'
        '    if path.parent.name == "results":\n'
        '        raise OSError("injected result write failure")\n'
        '    if path.name == "fault.bin":\n'
        '        raise OSError("injected upload write failure")\n'
        '    return _real_write_bytes(path, data)\n'
        'pathlib.Path.write_bytes = _fail_storage_write\n'
        '_real_rmtree = shutil.rmtree\n'
        'def _fail_upload_delete(path, *args, **kwargs):\n'
        '    if pathlib.Path(path).name == "delete-fault":\n'
        '        raise OSError("injected upload delete failure")\n'
        '    return _real_rmtree(path, *args, **kwargs)\n'
        'shutil.rmtree = _fail_upload_delete\n',
        encoding='utf-8')
    data = base64.b64encode(b'edge').decode()
    with _util.bridge(tmp, env={'PYTHONPATH': str(fault_dir)}) as (base, docroot):
        delete_dir = docroot / 'uploads' / TOK / 'delete-fault'
        delete_dir.mkdir(parents=True)
        (delete_dir / 'kept.bin').write_bytes(b'kept')

        calls = (
            lambda: _util.post_json(
                base + '/result',
                {'token': TOK, 'tabId': 'ordinary-tab', 'id': 'fault',
                 'result': 'x'}),
            lambda: _util.post_json(
                base + '/upload',
                {'token': TOK, 'id': 'ordinary-id', 'filename': 'fault.bin',
                 'data': data}),
            lambda: _util.request(
                base + '/upload', 'DELETE',
                body={'token': TOK, 'id': 'delete-fault'}),
        )
        statuses = []
        for call in calls:
            try:
                statuses.append(call()[0])
            except http.client.RemoteDisconnected:
                statuses.append('dropped')

        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)
        assert statuses == [500, 500, 500], statuses
        assert delete_dir.is_dir() and (delete_dir / 'kept.bin').is_file()


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='bridgeuploads_')


if __name__ == '__main__':
    raise SystemExit(main())
