#!/usr/bin/env python3
"""The /upload and /screenshot routes as plain functions, each pinned to the
upload root passed by the caller rather than configured storage.
"""
import base64
import contextlib
import io
import os
import re
import subprocess
import sys
import time
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
    assert [item['path'] for item in payload] == ['id1/a.png'], payload
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


def test_named_upload_serves_the_relative_selector_of_the_same_file(tmp):
    """The upload answer's relative path names the same file token-led.

    The relative `<id>/<file>` form is what POST /upload now answers and
    the extension forwards, and what the token-led form resolves to once
    the leading component is checked against the caller's own credential.
    A relative selector to a stored non-screenshot is still refused: only
    a screenshot type is answered here.
    """
    routes = _load('fixture_upload_routes_named_rel')
    target = _store(tmp, 'tok', 'id1', 'shot.png', b'IMG')
    _store(tmp, 'tok', 'id1', 'notes.txt', b'text')
    answer = routes.named_upload(Path(tmp), 'tok', 'id1/shot.png')
    assert answer.path == target, answer
    assert answer.mime == 'image/png', answer
    by_ledger = routes.named_upload(Path(tmp), 'tok', 'tok/id1/shot.png')
    assert by_ledger.path == target, by_ledger
    assert routes.named_upload(Path(tmp), 'tok', 'id1/notes.txt') == (
        404, {'error': 'no screenshot'})


def test_named_upload_refuses_a_one_component_selector(tmp):
    """A selector with no separator has no id directory to resolve under."""
    routes = _load('fixture_upload_routes_named_one')
    _store(tmp, 'tok', 'id1', 'shot.png')
    assert routes.named_upload(Path(tmp), 'tok', 'shot.png') == (
        400, {'error': 'path must be <id>/<file>'})
    assert routes.named_upload(Path(tmp), 'tok', 'tok') == (
        400, {'error': 'path must be <id>/<file>'})


def test_a_token_led_selector_deeper_than_three_components_still_resolves(tmp):
    """Today's resolution of longer token-led selectors is preserved.

    The legacy form checks the leading component against the token and
    resolves whatever follows under the caller's namespace; the new
    relative branch takes exactly two components, so the deep form cannot
    become a refusal without shrinking the stored paths it already names.
    """
    routes = _load('fixture_upload_routes_named_deep')
    nested = Path(tmp) / 'tok' / 'a' / 'b'
    nested.mkdir(parents=True)
    (nested / 'c.png').write_bytes(b'IMG')
    answer = routes.named_upload(Path(tmp), 'tok', 'tok/a/b/c.png')
    assert answer.path == nested / 'c.png', answer
    assert routes.named_upload(Path(tmp), 'tok', 'other/a/b/c.png') == (
        404, {'error': 'no screenshot'})


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
