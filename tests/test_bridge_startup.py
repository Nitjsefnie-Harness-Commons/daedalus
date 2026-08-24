#!/usr/bin/env python3
"""Coming up, saying so, and serving the dashboard that reads it.

A bridge that cannot start has to say why on its own stdout, because that is
all a fixture or an operator has to go on; one that starts answers /health,
and serves the dashboard from the repository without letting a path leave it.
"""
import os
import socket
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def test_health_payload(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        status, body = _util.get_json(base + '/health')
        assert status == 200, status
        assert body['ok'] is True
        assert isinstance(body['uptime_s'], (int, float)) and body['uptime_s'] >= 0
        assert body['active_streams'] == 0
        assert body['stream_tabs'] == []
        assert body['registry'] == {'tokens': 0, 'tabs': 0}
        assert body['last_delivery_s_ago'] is None  # nothing delivered yet
        assert body['cmd_ttl_s'] == 90
        assert body['stream_max_age_s'] == 3600


def test_startup_survives_an_undecodable_byte_in_the_data_root(tmp):
    """The startup log line prints the configured data root.

    A root whose name carries an undecodable byte arrives through
    surrogateescape; where sys.stdout.errors is strict
    (PYTHONIOENCODING=utf-8:strict forces it here, because this box's
    C.UTF-8 stdio would mask it), printing it raised UnicodeEncodeError and
    the server exited 1 before ever serving a request.
    """
    _util.require_undecodable_names(tmp)
    bad_root = os.fsencode(tmp) + b'/\xffdocroot'
    os.mkdir(bad_root)
    env = {'PYTHONIOENCODING': 'utf-8:strict',
           'DAEDALUS_DIR': os.fsdecode(bad_root)}
    with _util.bridge(tmp, env=env) as (base, _docroot):
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_an_explicit_port_collision_surfaces_verbatim(tmp):
    """A caller that forces an occupied port still gets the original error.

    Port 0 removed the window for the fixture's own draws, but an explicit
    DAEDALUS_PORT is still honored, and its bind failure must arrive as
    itself — not retried into a fresh port or flattened into a timeout.
    """
    squatter = socket.socket()
    squatter.bind(('127.0.0.1', 0))
    squatter.listen(1)
    taken = squatter.getsockname()[1]
    try:
        with _util.bridge(tmp, env={'DAEDALUS_PORT': str(taken)}):
            pass
    except RuntimeError as failure:
        assert _util.is_bind_error(str(failure)), failure
    else:
        raise AssertionError('a squatted explicit port started a bridge')
    finally:
        squatter.close()


def test_a_lost_draw_no_longer_kills_the_bridge_fixture(tmp):
    """A drawn port taken by another process used to fail the bridge child.

    The fixture handed the child a number it had already released; a squatter
    binding it first produced EADDRINUSE under an innocent test's name — the
    intermittent that surfaced under arbitrary tests in this suite and never
    reproduced. The fixture now asks the kernel for port 0, so there is no
    number to lose.
    """
    squatter = socket.socket()
    squatter.bind(('127.0.0.1', 0))
    squatter.listen(1)
    taken = squatter.getsockname()[1]
    real_free_port = _util.free_port
    _util.free_port = lambda: taken
    try:
        with _util.bridge(tmp) as (base, _docroot):
            status, health = _util.get_json(base + '/health')
            assert status == 200 and health['ok'] is True, (status, health)
    finally:
        _util.free_port = real_free_port
        squatter.close()


def test_log_safe_never_raises_and_stays_useful(tmp):
    """The log-line safeguard must not itself be able to raise.

    str(value) was evaluated outside any fallback: a conversion-limited huge
    int raises ValueError under the default int-to-string limit, an exception
    object whose __str__ fails propagates its own error, and a str subclass
    can hand .encode() to code that raises or .decode() to one returning a
    non-string — the same failure class the helper exists to close, one
    layer inside it.
    """
    env = {'DAEDALUS_DIR': str(tmp), 'DAEDALUS_PORT': '8081'}
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    root = str(_util.ROOT)
    added_root = root not in sys.path
    if added_root:
        sys.path.insert(0, root)
    try:
        mod = _util.load(_util.ROOT / 'server.py', 'server_log_safe_unit')
    finally:
        if added_root:
            sys.path.remove(root)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    for value, expected in _util.log_safe_cases():
        assert mod._log_safe(value) == expected, (
            f'_log_safe({type(value).__name__}) disagrees')
    # Ordinary values pass through in full, ASCII and non-ASCII alike.
    assert mod._log_safe('plain ascii') == 'plain ascii'
    assert mod._log_safe('héllo — 世界') == 'héllo — 世界'


def test_dashboard_static_serving(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        dash = _util.ROOT / 'dashboard'
        status, raw = _util.get(base + '/dashboard')
        assert status == 200, status
        assert raw == (dash / 'index.html').read_bytes()
        status, raw = _util.get(base + '/dashboard/style.css')
        assert status == 200, status
        assert raw == (dash / 'style.css').read_bytes()
        status, raw = _util.get(base + '/dashboard/app.js')
        assert status == 200, status
        assert raw == (dash / 'app.js').read_bytes()
        status, _ = _util.get(base + '/dashboard/no-such-asset.js')
        assert status == 404, status


def test_dashboard_responses_refuse_cross_origin_framing(tmp):
    """The token-bearing control surface must not be embeddable.

    /dashboard drives the browser and carries the bridge token, so a page
    that can frame it can overlay its controls. Neither frame-ancestors nor
    X-Frame-Options was sent on any dashboard response.
    """
    with _util.bridge(tmp) as (base, _docroot):
        for path in ('/dashboard', '/dashboard/app.js'):
            request = urllib.request.Request(base + path)
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.status == 200, (path, response.status)
                headers = response.headers
            assert headers.get('X-Frame-Options') == 'DENY', (path, dict(headers))
            assert headers.get('Content-Security-Policy') == (
                "frame-ancestors 'none'"), (path, dict(headers))


def test_dashboard_refuses_traversal(tmp):
    with _util.bridge(tmp) as (base, _docroot):
        server_py = (_util.ROOT / 'server.py').read_bytes()
        for path in ('/dashboard/../server.py',
                     '/dashboard/../../server.py',
                     '/dashboard/sub/../../server.py'):
            status, raw = _util.get(base + path)
            assert status == 400, (path, status)
            assert raw != server_py and b'BaseHTTPRequestHandler' not in raw
        # Percent-encoded dots never decode to a traversal either: refused or
        # simply absent, but never served.
        status, raw = _util.get(base + '/dashboard/%2e%2e/server.py')
        assert status in (400, 404), status
        assert b'BaseHTTPRequestHandler' not in raw


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='bridgestartup_')


if __name__ == '__main__':
    raise SystemExit(main())
