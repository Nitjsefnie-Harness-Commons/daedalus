#!/usr/bin/env python3
"""The upload routes' concurrent and in-progress-name behavior.

Split from test_upload_routes.py when both works' growth pushed the
suite past the size policy's 700-line test ceiling: the size policy's
remedy is relocation, and these tests are the cohesive half -- every
mock-injected filesystem race, temp-sibling publication, and reserved-
name refusal. Same harness, same fixtures, no behavior change.
"""
import base64
import os
import re
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
            'id2/survivor.png'], payload

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
    assert [item['path'] for item in payload] == ['kept/b.png'], payload


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
            'kept/b.png'], payload

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
    assert [item['path'] for item in payload] == ['id1/kept.png'], payload


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
            assert re.fullmatch(r'id1/\d{13}_\d{6}\.png', rel), rel
            assert (Path(tmp) / 'tok' / rel).is_file(), rel
            assert (Path(tmp) / 'tok' / rel).read_bytes() == data, rel


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
        published = Path(tmp) / 'tok' / payload['path']
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
            200, {'ok': True, 'path': 'id1/a.bin', 'size': 10}), outer
        assert inner == [
            (200, {'ok': True, 'path': 'id1/a.bin', 'size': 1})], inner
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
        200, {'ok': True, 'path': 'id1/x.tmpx', 'size': 1})
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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='uploadraces_')


if __name__ == '__main__':
    raise SystemExit(main())
