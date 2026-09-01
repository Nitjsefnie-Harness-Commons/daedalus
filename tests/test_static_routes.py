#!/usr/bin/env python3
"""Dashboard static serving as a plain function.

`daedalus_bridge/static_routes.py` answers `GET /dashboard[/<asset>]` with a
`BytesAnswer` carrying the framing headers, and takes the dashboard directory
as a parameter rather than importing `config`.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402


def _load(name):
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'static_routes.py', name)


def _asset(root, rel, data=b'<html>'):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_dashboard_root_serves_index_html_with_both_frame_headers(tmp):
    static = _load('fixture_static_routes_root')
    _asset(tmp, 'index.html', b'<html>dash</html>')
    answer = static.dashboard_asset(Path(tmp), '/dashboard')
    assert answer.data == b'<html>dash</html>', answer
    assert answer.mime == 'text/html; charset=utf-8', answer
    headers = dict(answer.headers)
    assert headers['Content-Security-Policy'] == "frame-ancestors 'none'"
    assert headers['X-Frame-Options'] == 'DENY', headers


def test_dashboard_index_path_serves_the_same_answer(tmp):
    static = _load('fixture_static_routes_index')
    _asset(tmp, 'index.html', b'<html>dash</html>')
    answer = static.dashboard_asset(Path(tmp), '/dashboard/index.html')
    assert answer.data == b'<html>dash</html>', answer
    assert dict(answer.headers)['X-Frame-Options'] == 'DENY', answer


def test_dashboard_asset_carries_its_own_mime_and_the_frame_headers(tmp):
    static = _load('fixture_static_routes_asset')
    _asset(tmp, 'app.js', b'export const x = 1;')
    answer = static.dashboard_asset(Path(tmp), '/dashboard/app.js')
    assert answer.mime == 'application/javascript; charset=utf-8', answer
    assert answer.data == b'export const x = 1;', answer
    headers = dict(answer.headers)
    assert headers['X-Frame-Options'] == 'DENY', headers
    assert headers['Content-Security-Policy'] == "frame-ancestors 'none'"


def test_dashboard_asset_falls_back_to_octet_stream(tmp):
    static = _load('fixture_static_routes_octet')
    _asset(tmp, 'blob.bin', b'\x00\x01')
    answer = static.dashboard_asset(Path(tmp), '/dashboard/blob.bin')
    assert answer.mime == 'application/octet-stream', answer


def test_dashboard_asset_answers_404_for_a_missing_file(tmp):
    static = _load('fixture_static_routes_missing')
    assert static.dashboard_asset(Path(tmp), '/dashboard/nope.js') == (
        404, {'error': 'not found'})


def test_dashboard_asset_refuses_a_traversal(tmp):
    static = _load('fixture_static_routes_traversal')
    _asset(tmp, 'index.html')
    for path in ('/dashboard/../server.py',
                 '/dashboard/sub/../../server.py'):
        assert static.dashboard_asset(Path(tmp), path) == (
            400, {'error': 'bad path'}), path


def test_dashboard_asset_refuses_a_directory_as_not_found(tmp):
    static = _load('fixture_static_routes_directory')
    _asset(tmp, 'sub/app.js')
    assert static.dashboard_asset(Path(tmp), '/dashboard/sub') == (
        404, {'error': 'not found'})


def test_the_module_imports_without_daedalus_configuration(_tmp):
    """A route module never imports config, so it needs no environment."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    done = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.static_routes'],
        env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='staticroutes_')


if __name__ == '__main__':
    raise SystemExit(main())
