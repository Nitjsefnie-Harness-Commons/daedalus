#!/usr/bin/env python3
"""The /upload and /screenshot routes as plain functions, each pinned to the
upload root passed by the caller rather than configured storage.
"""
import base64
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402


def _load(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'upload_routes.py', name)


def _store(root, token, upload_id, name, data=b'x'):
    """Put a file into the upload namespace without going through a route."""
    target = Path(root) / token / upload_id
    target.mkdir(parents=True, exist_ok=True)
    path = target / name
    path.write_bytes(data)
    return path


def test_list_uploads_answers_a_bare_array_without_paging(tmp):
    routes = _load('fixture_upload_routes_list')
    _store(tmp, 'tok', 'id1', 'a.png', b'abc')
    status, payload = routes.list_uploads(Path(tmp), 'tok', {})
    assert status == 200, (status, payload)
    assert [item['path'] for item in payload] == ['tok/id1/a.png'], payload
    assert payload[0]['size'] == 3, payload


def test_list_uploads_answers_a_page_shape_when_paging_is_asked_for(tmp):
    routes = _load('fixture_upload_routes_page')
    _store(tmp, 'tok', 'id1', 'a.png')
    _store(tmp, 'tok', 'id1', 'b.png')
    status, payload = routes.list_uploads(
        Path(tmp), 'tok', {'limit': ['1'], 'offset': ['1']})
    assert status == 200, (status, payload)
    assert payload['total'] == 2, payload
    assert payload['limit'] == 1 and payload['offset'] == 1, payload
    assert [item['filename'] for item in payload['items']] == ['b.png']


def test_list_uploads_skips_an_entry_deleted_during_the_walk(tmp):
    routes = _load('fixture_upload_routes_vanished_entry')

    def check_case(remove_id):
        root = Path(tmp) / str(remove_id)
        gone = _store(root, 'tok', 'id1', 'a.png')
        _store(root, 'tok', 'id2', 'survivor.png')
        real_stat = routes.os.stat
        removed = []

        def delete_then_stat(path, *args, **kwargs):
            if Path(path) == gone and not removed:
                removed.append(True)
                gone.unlink()
                if remove_id:
                    gone.parent.rmdir()
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(routes.os, 'stat', delete_then_stat):
            status, payload = routes.list_uploads(
                root, 'tok', {'limit': ['10']})
        assert removed, remove_id
        assert status == 200, (status, payload)
        assert [item['path'] for item in payload['items']] == [
            'tok/id2/survivor.png'], payload

    for remove_id in (False, True):
        check_case(remove_id)


def test_list_uploads_answers_empty_when_token_vanishes_before_scan(tmp):
    routes = _load('fixture_upload_routes_vanished_token')

    def check_case(params, expected):
        _store(tmp, 'tok', 'id1', 'a.png')
        token_dir = Path(tmp) / 'tok'
        real_scan = routes.os.scandir
        removed = []

        def delete_then_scan(path):
            if path == token_dir and not removed:
                removed.append(True)
                shutil.rmtree(token_dir)
            return real_scan(path)

        with mock.patch.object(routes.os, 'scandir', delete_then_scan):
            answer = routes.list_uploads(Path(tmp), 'tok', params)
        assert removed, params
        assert answer == (200, expected), answer

    for params, expected in (({}, []), ({'limit': ['10']}, {
            'items': [], 'total': 0, 'limit': 10, 'offset': 0})):
        check_case(params, expected)


def test_list_uploads_skips_an_id_deleted_before_sorting(tmp):
    routes = _load('fixture_upload_routes_vanished_id')
    _store(tmp, 'tok', 'gone', 'a.png')
    _store(tmp, 'tok', 'kept', 'b.png')
    gone = Path(tmp) / 'tok' / 'gone'
    real_stat = routes.os.stat
    removed = []

    def delete_then_stat(path, *args, **kwargs):
        if Path(path) == gone and not removed:
            removed.append(True)
            shutil.rmtree(gone)
        return real_stat(path, *args, **kwargs)

    with mock.patch.object(routes.os, 'stat', delete_then_stat):
        status, payload = routes.list_uploads(Path(tmp), 'tok', {})
    assert removed
    assert status == 200, (status, payload)
    assert [item['path'] for item in payload] == ['tok/kept/b.png'], payload


def test_list_uploads_skips_an_id_deleted_before_its_scan(tmp):
    routes = _load('fixture_upload_routes_vanished_id_scan')

    def check_case(error):
        root = Path(tmp) / error.__name__
        _store(root, 'tok', 'gone', 'a.png')
        _store(root, 'tok', 'kept', 'b.png')
        gone = root / 'tok' / 'gone'
        real_scan = routes.os.scandir
        removed = []

        def delete_then_scan(path):
            if path == gone and not removed:
                removed.append(True)
                shutil.rmtree(gone)
                if error is not FileNotFoundError:
                    raise error('injected deletion race')
            return real_scan(path)

        with mock.patch.object(routes.os, 'scandir', delete_then_scan):
            status, payload = routes.list_uploads(root, 'tok', {})
        assert removed, error
        assert status == 200, (status, payload)
        assert [item['path'] for item in payload] == [
            'tok/kept/b.png'], payload

    for error in (PermissionError, NotADirectoryError, FileNotFoundError):
        check_case(error)


def test_upload_reads_answer_absent_when_token_is_not_a_directory(tmp):
    routes = _load('fixture_upload_routes_token_not_directory')

    def check_case(route, expected, error):
        _store(tmp, 'tok', 'id1', 'a.png')
        token_dir = Path(tmp) / 'tok'
        assert token_dir.is_dir()
        real_is_dir = routes.pathlib.Path.is_dir

        def token_is_not_a_directory(path, *args, **kwargs):
            if path == token_dir:
                if error:
                    raise error('injected deletion race')
                return False
            return real_is_dir(path, *args, **kwargs)

        with mock.patch.object(routes.pathlib.Path, 'is_dir',
                               token_is_not_a_directory):
            answer = route(Path(tmp), 'tok', {})
        assert answer == expected, answer

    for route, expected in (
            (routes.list_uploads, (200, [])),
            (routes.latest_screenshot, (404, {'error': 'no uploads'}))):
        for error in (None, PermissionError):
            check_case(route, expected, error)


def test_list_uploads_skips_only_the_entry_whose_type_check_fails(tmp):
    routes = _load('fixture_upload_routes_entry_type')
    gone = _store(tmp, 'tok', 'id1', 'gone.png')
    _store(tmp, 'tok', 'id1', 'kept.png')
    real_scan = routes.os.scandir

    def delete_then_check():
        gone.unlink()
        raise PermissionError('injected deletion race')

    @contextmanager
    def scan(path):
        with real_scan(path) as entries:
            files = []
            for entry in entries:
                if Path(entry.path) == gone:
                    entry = mock.Mock(wraps=entry, path=entry.path)
                    entry.name = gone.name
                    entry.is_file.side_effect = delete_then_check
                files.append(entry)
            yield iter(files)

    with mock.patch.object(routes.os, 'scandir', scan):
        status, payload = routes.list_uploads(Path(tmp), 'tok', {})
    assert not gone.exists()
    assert status == 200, (status, payload)
    assert [item['path'] for item in payload] == ['tok/id1/kept.png'], payload


def test_list_uploads_refuses_a_bad_limit_before_the_directory_exists(tmp):
    """Well-formedness is decided without looking at storage."""
    routes = _load('fixture_upload_routes_badlimit')
    empty = Path(tmp) / 'nothing-here'
    status, payload = routes.list_uploads(
        empty, 'tok', {'limit': ['nope']})
    assert (status, payload) == (
        400, {'error': 'invalid limit/offset'}), (status, payload)


def test_list_uploads_refuses_an_unsafe_id(tmp):
    routes = _load('fixture_upload_routes_unsafe_id')
    status, payload = routes.list_uploads(
        Path(tmp), 'tok', {'id': ['../elsewhere']})
    assert (status, payload) == (
        400, {'error': 'invalid path component'}), (status, payload)


def test_latest_screenshot_skips_a_file_deleted_during_the_scan(tmp):
    routes = _load('fixture_upload_routes_vanished_screenshot')

    def check_case(survivor, params):
        root = Path(tmp) / str(survivor) / str(bool(params))
        gone = _store(root, 'tok', 'id1', 'gone.png')
        if survivor:
            kept = _store(root, 'tok', 'id1', 'kept.png')
        real_stat = routes.pathlib.Path.stat
        removed = []

        def delete_then_stat(path, *args, **kwargs):
            if path == gone and not removed:
                removed.append(True)
                gone.unlink()
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(routes.pathlib.Path, 'stat', delete_then_stat):
            answer = routes.latest_screenshot(root, 'tok', params)
        assert removed, (survivor, params)
        if survivor:
            assert answer.path == kept, answer
            assert answer.mime == 'image/png', answer
        else:
            assert answer == (404, {'error': 'no screenshot'}), answer

    for survivor in (True, False):
        for params in ({}, {'id': ['id1']}):
            check_case(survivor, params)


def test_latest_screenshot_answers_no_uploads_when_token_vanishes(tmp):
    routes = _load('fixture_upload_routes_screenshot_token')
    token_dir = Path(tmp) / 'tok'
    real_is_dir = routes.pathlib.Path.is_dir
    removed = []

    def is_dir_then_delete(path, *args, **kwargs):
        is_dir = real_is_dir(path, *args, **kwargs)
        if path == token_dir and not removed:
            removed.append(True)
            shutil.rmtree(token_dir)
        return is_dir

    for params in ({}, {'id': ['id1']}):
        _store(tmp, 'tok', 'id1', 'gone.png')
        removed.clear()
        with mock.patch.object(routes.pathlib.Path, 'is_dir',
                               is_dir_then_delete):
            answer = routes.latest_screenshot(Path(tmp), 'tok', params)
        assert removed, params
        assert answer == (404, {'error': 'no uploads'}), (params, answer)


def test_latest_screenshot_skips_an_id_deleted_before_scan(tmp):
    routes = _load('fixture_upload_routes_screenshot_id')

    def check_case(survivor):
        root = Path(tmp) / str(survivor)
        _store(root, 'tok', 'gone', 'a.png')
        gone = root / 'tok' / 'gone'
        if survivor:
            kept = _store(root, 'tok', 'kept', 'b.png')
        real_is_dir = routes.pathlib.Path.is_dir
        removed = []

        def is_dir_then_delete(path, *args, **kwargs):
            is_dir = real_is_dir(path, *args, **kwargs)
            if path == gone and not removed:
                removed.append(True)
                shutil.rmtree(gone)
            return is_dir

        # Patch the route's call; is_dir need not delegate to Path.stat.
        with mock.patch.object(routes.pathlib.Path, 'is_dir',
                               is_dir_then_delete):
            answer = routes.latest_screenshot(root, 'tok', {})
        assert removed, survivor
        if survivor:
            assert answer.path == kept, answer
        else:
            assert answer == (404, {'error': 'no screenshot'}), answer

    for survivor in (True, False):
        check_case(survivor)


def test_store_upload_writes_a_timestamped_screenshot(tmp):
    routes = _load('fixture_upload_routes_store')
    body = {'token': 'tok', 'id': 'id1', 'format': 'png',
            'data': base64.b64encode(b'PNGDATA').decode('ascii')}
    status, payload = routes.store_upload(Path(tmp), body)
    assert status == 200, (status, payload)
    rel = payload['path']
    assert rel.startswith('id1/') and rel.endswith('.png'), rel
    assert payload['size'] == 7, payload
    assert (Path(tmp) / 'tok' / rel).read_bytes() == b'PNGDATA'


def test_store_upload_logs_and_answers_the_token_free_path(tmp):
    """The answer's path and its log line stop at the token directory.

    The answer's `path` is what the extension forwards into the result
    envelope it posts, so a token-led path there was a second copy of the
    credential in every stored screenshot result, and the log line printed
    it on every upload. Relative to the token directory, both agree with
    the GET /upload?path= selector and with the client sanitizers' no-op
    case.
    """
    routes = _load('fixture_upload_routes_store_log')
    output = io.StringIO()
    body = {'token': 'tok-verify', 'id': 'shot', 'filename': 'a.txt',
            'data': base64.b64encode(b'hi').decode('ascii')}
    with contextlib.redirect_stdout(output):
        status, payload = routes.store_upload(Path(tmp), body)
    assert status == 200, (status, payload)
    assert payload['path'] == 'shot/a.txt', payload
    assert payload['size'] == 2, payload
    assert output.getvalue() == '[UPLOAD] shot/a.txt (2 bytes)\n', (
        output.getvalue())


def test_store_upload_names_same_millisecond_captures_distinctly(tmp):
    routes = _load('fixture_upload_routes_same_ms')
    for clock in (1700000000.5, 1.5):
        paths = []
        with mock.patch.object(routes.time, 'time', return_value=clock):
            for data in (b'FIRST', b'SECOND'):
                body = {'token': 'tok', 'id': 'id1', 'format': 'png',
                        'data': base64.b64encode(data).decode('ascii')}
                status, payload = routes.store_upload(Path(tmp), body)
                assert status == 200, (status, payload)
                paths.append(payload['path'])
        assert paths[0] != paths[1], paths
        for rel, data in zip(paths, (b'FIRST', b'SECOND')):
            assert re.fullmatch(r'tok/id1/\d{13}_\d{6}\.png', rel), rel
            assert (Path(tmp) / rel).is_file(), rel
            assert (Path(tmp) / rel).read_bytes() == data, rel


def test_store_upload_publishes_through_a_temp_sibling(tmp):
    routes = _load('fixture_upload_routes_temp_sibling')
    real = routes.atomic_file.replace_atomically
    calls = []

    def recorder(src, dst):
        calls.append((Path(src), Path(dst), Path(dst).exists()))
        real(src, dst)
    for upload_id, extra in (('shot', {}), ('named', {'filename': 'a.bin'})):
        calls.clear()
        body = {'token': 'tok', 'id': upload_id, 'format': 'png',
                'data': base64.b64encode(b'DATA').decode('ascii'), **extra}
        with mock.patch.object(
                routes.atomic_file, 'replace_atomically', recorder):
            status, payload = routes.store_upload(Path(tmp), body)
        assert status == 200, (status, payload)
        published = Path(tmp) / payload['path']
        assert len(calls) == 1, (upload_id, calls)
        src, dst, dst_existed = calls[0]
        assert dst == published and not dst_existed, (upload_id, calls)
        assert src.parent == published.parent, (upload_id, src)
        assert re.fullmatch(
            rf'\.{re.escape(published.name)}\.\d+\.tmp', src.name), src
        assert published.read_bytes() == b'DATA', published
        leftovers = [p for p in published.parent.iterdir()
                     if p.name.endswith('.tmp')]
        assert not leftovers, leftovers


def test_store_upload_same_filename_interleaved_writes_publish_whole(tmp):
    """Two stores of one `filename` whose writes interleave each publish
    their own complete bytes: a shared temp let the first replace publish
    the other writer's bytes under its own answer."""
    routes = _load('fixture_upload_routes_interleave')
    real_write = routes.atomic_file.write_bytes_retrying
    real_replace = routes.atomic_file.replace_atomically

    def body(data):
        return {'token': 'tok', 'id': 'id1', 'filename': 'a.bin',
                'data': base64.b64encode(data).decode('ascii')}
    started, inner, deferred = [], [], []

    def write_then_interleave(path, data):
        real_write(path, data)
        if not started:
            started.append(True)
            inner.append(routes.store_upload(Path(tmp), body(b'B')))

    def defer_the_inner_replace(src, dst):
        if not deferred:
            deferred.append((src, dst))
            return
        real_replace(src, dst)
    published = Path(tmp) / 'tok' / 'id1' / 'a.bin'
    with mock.patch.object(routes.atomic_file, 'write_bytes_retrying',
                           write_then_interleave), \
            mock.patch.object(routes.atomic_file, 'replace_atomically',
                              defer_the_inner_replace):
        outer = routes.store_upload(Path(tmp), body(b'A' * 10))
        assert outer == (
            200, {'ok': True, 'path': 'tok/id1/a.bin', 'size': 10}), outer
        assert inner == [
            (200, {'ok': True, 'path': 'tok/id1/a.bin', 'size': 1})], inner
        assert published.read_bytes() == b'A' * 10, published.read_bytes()
        real_replace(*deferred[0])
    assert published.read_bytes() == b'B', published.read_bytes()
    leftovers = [p for p in published.parent.iterdir()
                 if p.name.endswith('.tmp')]
    assert not leftovers, leftovers


def test_store_upload_answers_500_and_leaves_no_temp_when_publish_fails(tmp):
    routes = _load('fixture_upload_routes_publish_fails')

    def refuse(src, dst):
        raise OSError('injected publish failure')
    body = {'token': 'tok', 'id': 'id1', 'format': 'png',
            'data': base64.b64encode(b'DATA').decode('ascii')}
    with mock.patch.object(routes.atomic_file, 'replace_atomically', refuse):
        status, payload = routes.store_upload(Path(tmp), body)
    assert (status, payload) == (
        500, {'error': 'upload storage failure'}), (status, payload)
    leftovers = list((Path(tmp) / 'tok' / 'id1').iterdir())
    assert not leftovers, leftovers


def test_named_file_refuses_a_temp_name(tmp):
    routes = _load('fixture_upload_routes_named_file_tmp')
    for name in ('.abc.png.tmp', '.abc.png.TMP', '.abc.png.Tmp'):
        _store(tmp, 'tok', 'id1', name, b'part')
        answer = routes.named_file(Path(tmp), 'tok', f'id1/{name}')
        assert answer == (400, {'error': 'invalid path component'}), answer


def test_delete_upload_refuses_a_temp_name(tmp):
    routes = _load('fixture_upload_routes_delete_tmp')
    for name in ('.abc.png.tmp', '.abc.png.TMP', '.abc.png.Tmp'):
        temp = _store(tmp, 'tok', 'id1', name, b'part')
        answer = routes.delete_upload(
            Path(tmp), {'token': 'tok', 'id': 'id1', 'filename': name})
        assert answer == (400, {'error': 'invalid path component'}), answer
        assert temp.exists(), 'an in-progress temp was unlinked'


def test_store_upload_refuses_a_caller_name_ending_in_tmp(tmp):
    routes = _load('fixture_upload_routes_tmp_name')
    for name in ('x.tmp', 'x.TMP', 'x.Tmp'):
        body = {'token': 'tok', 'id': 'id1', 'filename': name,
                'data': base64.b64encode(b'x').decode('ascii')}
        answer = routes.store_upload(Path(tmp), body)
        assert answer == (400, {'error': 'invalid path component'}), answer
        assert not (Path(tmp) / 'tok').exists(), 'something was written'
    body['filename'] = 'x.tmpx'
    assert routes.store_upload(Path(tmp), body) == (
        200, {'ok': True, 'path': 'tok/id1/x.tmpx', 'size': 1})
    assert (Path(tmp) / 'tok' / 'id1' / 'x.tmpx').read_bytes() == b'x'


def test_listing_skips_an_in_progress_temp_sibling(tmp):
    routes = _load('fixture_upload_routes_skip_tmp')
    real = _store(tmp, 'tok', 'id1', 'abc.png', b'done')
    now = time.time()
    os.utime(real, (now - 100, now - 100))
    for name in ('.abc.png.tmp', '.abc.png.TMP', '.abc.png.Tmp'):
        temp = _store(tmp, 'tok', 'id1', name, b'part')
        os.utime(temp, (now, now))
    status, payload = routes.list_uploads(Path(tmp), 'tok', {})
    assert status == 200, (status, payload)
    assert [item['filename'] for item in payload] == ['abc.png'], payload
    status, payload = routes.list_uploads(
        Path(tmp), 'tok', {'limit': ['10']})
    assert status == 200, (status, payload)
    assert payload['total'] == 1, payload
    answer = routes.latest_screenshot(Path(tmp), 'tok', {})
    assert answer.path == real, answer


def test_store_upload_refuses_a_non_string_format(tmp):
    """`[] in SCREENSHOT_TYPES` raises rather than answering False."""
    routes = _load('fixture_upload_routes_badformat')
    body = {'token': 'tok', 'id': 'id1', 'format': [],
            'data': base64.b64encode(b'x').decode('ascii')}
    status, payload = routes.store_upload(Path(tmp), body)
    assert (status, payload) == (
        400, {'error': 'unsupported format'}), (status, payload)


def test_store_upload_refuses_a_missing_id_and_missing_data(tmp):
    routes = _load('fixture_upload_routes_missing')
    encoded = base64.b64encode(b'x').decode('ascii')
    assert routes.store_upload(
        Path(tmp), {'token': 'tok', 'data': encoded}) == (
            400, {'error': 'missing id'})
    assert routes.store_upload(
        Path(tmp), {'token': 'tok', 'id': 'id1'}) == (
            400, {'error': 'missing data'})


def test_store_upload_refuses_undecodable_base64(tmp):
    routes = _load('fixture_upload_routes_base64')
    status, payload = routes.store_upload(
        Path(tmp), {'token': 'tok', 'id': 'id1', 'data': 'not base64!!'})
    assert (status, payload) == (
        400, {'error': 'invalid base64'}), (status, payload)


def test_store_upload_refuses_an_unsafe_component(tmp):
    routes = _load('fixture_upload_routes_store_unsafe')
    body = {'token': 'tok', 'id': '../escape',
            'data': base64.b64encode(b'x').decode('ascii')}
    status, payload = routes.store_upload(Path(tmp), body)
    assert (status, payload) == (
        400, {'error': 'invalid path component'}), (status, payload)


def test_delete_upload_logs_the_token_free_relative_path(tmp):
    """Each delete arm names its target inside the token directory only.

    The whole-namespace arm has no relative path left to name, so it uses
    the `[STREAM] CONNECT` convention: the credential's 8-character
    prefix, without an ellipsis.
    """
    routes = _load('fixture_upload_routes_delete_log')
    _store(tmp, 'tok-verify', 'shot', 'a.txt')
    _store(tmp, 'tok-verify', 'other', 'b.txt')
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert routes.delete_upload(
            Path(tmp), {'token': 'tok-verify', 'id': 'shot',
                        'filename': 'a.txt'}) == (200, {'ok': True})
        assert routes.delete_upload(
            Path(tmp), {'token': 'tok-verify',
                        'id': 'other'}) == (200, {'ok': True})
        assert routes.delete_upload(
            Path(tmp), {'token': 'tok-verify'}) == (200, {'ok': True})
    assert output.getvalue() == (
        '[DELETE] shot/a.txt\n'
        '[DELETE] other/\n'
        '[DELETE] token=tok-veri/\n'), output.getvalue()


def test_delete_upload_refuses_a_filename_without_an_id(tmp):
    """A filename names a file inside an id, never the token namespace."""
    routes = _load('fixture_upload_routes_delete_filename')
    _store(tmp, 'tok', 'id1', 'a.png')
    status, payload = routes.delete_upload(
        Path(tmp), {'token': 'tok', 'filename': 'a.png'})
    assert (status, payload) == (
        400, {'error': 'filename requires id'}), (status, payload)
    assert (Path(tmp) / 'tok').is_dir(), 'the token namespace was removed'


def test_delete_upload_removes_one_named_file(tmp):
    routes = _load('fixture_upload_routes_delete_file')
    kept = _store(tmp, 'tok', 'id1', 'keep.png')
    gone = _store(tmp, 'tok', 'id1', 'gone.png')
    status, payload = routes.delete_upload(
        Path(tmp), {'token': 'tok', 'id': 'id1', 'filename': 'gone.png'})
    assert (status, payload) == (200, {'ok': True}), (status, payload)
    assert not gone.exists() and kept.exists()


def test_delete_upload_removes_an_id_and_then_a_token(tmp):
    routes = _load('fixture_upload_routes_delete_tree')
    _store(tmp, 'tok', 'id1', 'a.png')
    _store(tmp, 'tok', 'id2', 'b.png')
    assert routes.delete_upload(
        Path(tmp), {'token': 'tok', 'id': 'id1'}) == (200, {'ok': True})
    assert not (Path(tmp) / 'tok' / 'id1').exists()
    assert routes.delete_upload(
        Path(tmp), {'token': 'tok'}) == (200, {'ok': True})
    assert not (Path(tmp) / 'tok').exists()


def test_delete_upload_answers_404_for_an_absent_target(tmp):
    routes = _load('fixture_upload_routes_delete_absent')
    assert routes.delete_upload(
        Path(tmp), {'token': 'tok'}) == (404, {'error': 'token not found'})
    _store(tmp, 'tok', 'id1', 'a.png')
    assert routes.delete_upload(
        Path(tmp), {'token': 'tok', 'id': 'nope'}) == (
            404, {'error': 'id not found'})
    assert routes.delete_upload(
        Path(tmp), {'token': 'tok', 'id': 'id1', 'filename': 'x.png'}) == (
            404, {'error': 'file not found'})


def test_latest_screenshot_serves_the_newest_file_with_its_mime(tmp):
    routes = _load('fixture_upload_routes_latest')
    older = _store(tmp, 'tok', 'id1', '1.png', b'old')
    newer = _store(tmp, 'tok', 'id2', '2.jpg', b'new')
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))
    answer = routes.latest_screenshot(Path(tmp), 'tok', {})
    assert answer.path == newer, answer
    assert answer.mime == 'image/jpeg', answer


def test_latest_screenshot_answers_404_without_uploads(tmp):
    routes = _load('fixture_upload_routes_latest_absent')
    assert routes.latest_screenshot(Path(tmp), 'tok', {}) == (
        404, {'error': 'no uploads'})
    _store(tmp, 'tok', 'id1', 'notes.txt')
    assert routes.latest_screenshot(Path(tmp), 'tok', {}) == (
        404, {'error': 'no screenshot'})


def test_named_upload_serves_exactly_the_path_a_result_carried(tmp):
    routes = _load('fixture_upload_routes_named')
    target = _store(tmp, 'tok', 'id1', 'shot.png', b'IMG')
    _store(tmp, 'tok', 'id1', 'zzz-newer.png', b'NEWER')
    answer = routes.named_upload(Path(tmp), 'tok', 'tok/id1/shot.png')
    assert answer.path == target, answer
    assert answer.mime == 'image/png', answer


def test_named_upload_refuses_another_tokens_path(tmp):
    """The leading component must be the caller's own token."""
    routes = _load('fixture_upload_routes_named_other')
    _store(tmp, 'other', 'id1', 'shot.png', b'IMG')
    status, payload = routes.named_upload(
        Path(tmp), 'tok', 'other/id1/shot.png')
    assert (status, payload) == (
        404, {'error': 'no screenshot'}), (status, payload)


def test_named_upload_refuses_an_unsafe_component(tmp):
    routes = _load('fixture_upload_routes_named_unsafe')
    status, payload = routes.named_upload(
        Path(tmp), 'tok', 'tok/../server.py')
    assert (status, payload) == (
        400, {'error': 'invalid path component'}), (status, payload)


def test_named_upload_refuses_a_non_screenshot_suffix(tmp):
    routes = _load('fixture_upload_routes_named_suffix')
    _store(tmp, 'tok', 'id1', 'notes.txt', b'text')
    assert routes.named_upload(Path(tmp), 'tok', 'tok/id1/notes.txt') == (
        404, {'error': 'no screenshot'})


def test_an_uppercase_suffix_is_typed_the_same_by_each_route(tmp):
    """Every suffix the routes accept is typed case-insensitively, by both
    screenshot selections.

    Both routes discover a file through a lowercased membership test, so
    both must also lowercase the suffix before mapping it to a MIME type;
    otherwise the same stored file serves as an image on the `path=`
    selection and as a byte stream on the latest-by-id selection. The
    sweep drives every accepted member in three spellings so a fix that
    lowercases only one spelling or one member still fails.
    """
    routes = _load('fixture_upload_routes_uppercase')
    mimes = {'png': 'image/png', 'jpeg': 'image/jpeg',
             'jpg': 'image/jpeg', 'webp': 'image/webp'}
    for member in sorted(routes.SCREENSHOT_TYPES):
        mime = mimes[member.lower()]
        for spelling in (member, member.upper(), member.capitalize()):
            name = 'SHOT.' + spelling
            _store(tmp, 'tok', 'id1', name, b'IMG')
            named = routes.named_upload(Path(tmp), 'tok', f'tok/id1/{name}')
            assert named.mime == mime, (name, named)
            all_ids = routes.latest_screenshot(Path(tmp), 'tok', {})
            assert all_ids.path.name == name, (name, all_ids)
            assert all_ids.mime == mime, (name, all_ids)
            by_id = routes.latest_screenshot(
                Path(tmp), 'tok', {'id': ['id1']})
            assert by_id.path.name == name, (name, by_id)
            assert by_id.mime == mime, (name, by_id)
            (Path(tmp) / 'tok' / 'id1' / name).unlink()


def test_screenshot_mime_maps_every_served_format(_tmp):
    routes = _load('fixture_upload_routes_mime')
    assert routes.screenshot_mime('png') == 'image/png'
    assert routes.screenshot_mime('jpg') == 'image/jpeg'
    assert routes.screenshot_mime('webp') == 'image/webp'
    assert routes.screenshot_mime('json') == 'application/json'
    assert routes.screenshot_mime('txt') == 'text/plain'
    assert routes.screenshot_mime('bin') == 'application/octet-stream'


def test_stored_uploads_orders_newest_id_first_and_names_within(tmp):
    routes = _load('fixture_upload_routes_stored')
    _store(tmp, 'tok', 'old', 'b.png')
    _store(tmp, 'tok', 'old', 'a.png')
    _store(tmp, 'tok', 'new', 'c.png')
    now = time.time()
    os.utime(Path(tmp) / 'tok' / 'old', (now - 100, now - 100))
    os.utime(Path(tmp) / 'tok' / 'new', (now, now))
    listed = [(id_name, entry.name) for id_name, entry
              in routes.stored_uploads(Path(tmp) / 'tok', '')]
    assert listed == [('new', 'c.png'), ('old', 'a.png'), ('old', 'b.png')]


def test_the_module_imports_without_daedalus_configuration(_tmp):
    """A route module never imports config, so it needs no environment."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    done = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.upload_routes'],
        env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='uploadroutes_')


if __name__ == '__main__':
    raise SystemExit(main())
