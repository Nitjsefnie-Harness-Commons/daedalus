#!/usr/bin/env python3
"""The /upload and /screenshot routes as plain functions.

`daedalus_bridge/upload_routes.py` owns the upload namespace walk and the
five routes that read or write it. Each takes the upload directory as a
parameter rather than importing `config`, so every control here pins the
route against the root it was handed: a route reading somewhere else would
still answer 200.
"""
import base64
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402

# The answer types live in the transport module, which still refuses to import
# without these two. Nothing is written under the root, so the system temp
# directory serves and no fixture tree is left behind.
os.environ.setdefault('DAEDALUS_DIR', tempfile.gettempdir())
os.environ.setdefault('DAEDALUS_PORT', '0')


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


def test_store_upload_writes_a_timestamped_screenshot(tmp):
    routes = _load('fixture_upload_routes_store')
    body = {'token': 'tok', 'id': 'id1', 'format': 'png',
            'data': base64.b64encode(b'PNGDATA').decode('ascii')}
    status, payload = routes.store_upload(Path(tmp), body)
    assert status == 200, (status, payload)
    rel = payload['path']
    assert rel.startswith('tok/id1/') and rel.endswith('.png'), rel
    assert payload['size'] == 7, payload
    assert (Path(tmp) / rel).read_bytes() == b'PNGDATA'


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


def test_the_module_imports_without_the_bridge_server(tmp):
    """A route module stands alone: no `server.py`, no config of its own.

    It reaches `daedalus_bridge.config` only through the transport module the
    answer types live in, so the subprocess gets the two variables that
    module's import chain requires and nothing else.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    env['DAEDALUS_DIR'] = str(tmp)
    env['DAEDALUS_PORT'] = '0'
    done = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.upload_routes'],
        env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    source = (_util.ROOT / 'daedalus_bridge' / 'upload_routes.py').read_text(
        encoding='utf-8')
    assert 'daedalus_bridge.config' not in source, source
    assert 'import server' not in source, source


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='uploadroutes_')


if __name__ == '__main__':
    raise SystemExit(main())
