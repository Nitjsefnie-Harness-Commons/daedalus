#!/usr/bin/env python3
"""Coming up, saying so, and serving the dashboard that reads it.

A bridge that cannot start has to say why on its own stdout, because that is
all a fixture or an operator has to go on; one that starts answers /health,
and serves the dashboard from the repository without letting a path leave it.
"""
import importlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import types
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _noglibc  # noqa: E402
import _util  # noqa: E402


def _observation_child(code, thread):
    return types.SimpleNamespace(
        poll=lambda: code,
        _daedalus_drain_thread=thread)


class _DrainThreadDouble:
    def __init__(self, drained=None, line=None, alive=False,
                 fail_on_join=False):
        self.drained = drained
        self.line = line
        self.alive = alive
        self.fail_on_join = fail_on_join
        self.join_timeouts = []

    def join(self, timeout=None):
        if self.fail_on_join:
            raise AssertionError('live child drain was joined')
        self.join_timeouts.append(timeout)
        if self.line is not None:
            self.drained.append(self.line)

    def is_alive(self):
        return self.alive


class _WaitableLines(list):
    def __init__(self, awaited):
        super().__init__()
        self.awaited = awaited
        self.ready = threading.Event()

    def append(self, line):
        super().append(line)
        if line == self.awaited:
            self.ready.set()


def test_child_exit_during_startup_reports_exit_code(tmp):
    """A child that dies before announcing reports its process exit code."""
    del tmp
    proc = subprocess.Popen(
        [sys.executable, '-c', 'import sys; sys.exit(23)'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    proc.wait(timeout=10)
    failure = ''
    try:
        _util.await_listening_line(proc, _util.drain_lines(proc), timeout=1)
    except RuntimeError as exc:
        failure = str(exc)
    assert 'bridge exited during startup' in failure, failure
    assert 'exited with code 23' in failure, failure


def test_drain_lines_records_pump_thread_on_child(tmp):
    """The renderer can find the real pump that fills its observation list."""
    del tmp
    proc = types.SimpleNamespace(stdout=('wired pump marker\n',))
    drained = _util.drain_lines(proc)
    thread = getattr(proc, '_daedalus_drain_thread', None)
    assert isinstance(thread, threading.Thread), thread
    thread.join()
    assert not thread.is_alive()
    assert drained == ['wired pump marker\n']


def test_exited_child_drain_join_is_bounded_and_reported(tmp):
    """A stuck exited-child drain is bounded and marked incomplete."""
    del tmp
    assert hasattr(_util, 'DRAIN_JOIN_TIMEOUT'), (
        'fixture has no named drain join timeout')
    thread = _DrainThreadDouble(alive=True)
    proc = _observation_child(23, thread)
    message = _util._startup_observations(proc, [], 0)
    assert thread.join_timeouts == [_util.DRAIN_JOIN_TIMEOUT]
    assert 'drain timed out before EOF' in message, message


def test_exited_child_observations_wait_for_drain(tmp):
    """A line completed by the exited-child join reaches the snapshot."""
    del tmp
    drained = []
    marker = 'delayed drain marker\n'
    thread = _DrainThreadDouble(drained=drained, line=marker)
    proc = _observation_child(23, thread)
    message = _util._startup_observations(proc, drained, 0)
    assert marker in message, message
    assert 'drain timed out before EOF' not in message, message


def test_exited_child_unfinished_drain_is_reported(tmp):
    """An exited child's unfinished drain is disclosed in the snapshot."""
    del tmp
    thread = _DrainThreadDouble(alive=True)
    proc = _observation_child(23, thread)
    message = _util._startup_observations(proc, [], 0)
    assert 'drain timed out before EOF' in message, message


def test_first_bridge_start_gets_cold_allowance_then_marks_warm(tmp):
    """The first successful bridge start consumes the cold allowance.

    The allowance is read off what the fixture PASSES to the wait, through a
    stand-in that records it and refuses. Pinning it by patching the
    allowance to a fraction of a second and requiring the real start to miss
    it made the assertion depend on the bridge being slow, so it stopped
    holding on the first host where startup got fast enough.
    """
    assert hasattr(_util, '_bridge_started'), 'fixture has no start state'
    saved_started = _util._bridge_started
    real_await = _util.await_listening_line
    spent = []

    def refusing_await(proc, drained, timeout=None):
        """Record the allowance the fixture chose, and refuse to wait."""
        del proc, drained
        spent.append(timeout)
        raise RuntimeError('the stand-in refused to wait')

    try:
        _util._bridge_started = False
        assert _util.startup_timeout() == _util.COLD_START_TIMEOUT
        _util._bridge_started = True
        assert _util.startup_timeout() == _util.WARM_START_TIMEOUT
        assert _util.COLD_START_TIMEOUT > _util.WARM_START_TIMEOUT

        _util.await_listening_line = refusing_await
        _util._bridge_started = False
        failure = ''
        try:
            with _util.bridge(tmp):
                pass
        except RuntimeError as exc:
            failure = str(exc)
        assert failure == 'the stand-in refused to wait', failure
        assert spent == [_util.COLD_START_TIMEOUT], spent
        assert _util._bridge_started is False, 'a failed start marked warm'

        _util._bridge_started = True
        try:
            with _util.bridge(tmp):
                pass
        except RuntimeError:
            # The stand-in is still installed, so entering the fixture can
            # only fail; the warm allowance it recorded is the assertion.
            pass
        assert spent[-1] == _util.WARM_START_TIMEOUT, spent
        _util.await_listening_line = real_await

        _util._bridge_started = False
        with _util.bridge(tmp):
            pass
        assert _util._bridge_started is True
    finally:
        _util.await_listening_line = real_await
        _util._bridge_started = saved_started


def test_live_child_observations_never_wait_for_drain(tmp):
    """A live child's pump cannot delay rendering its observations."""
    del tmp
    thread = _DrainThreadDouble(fail_on_join=True)
    proc = _observation_child(None, thread)
    message = _util._startup_observations(proc, [], 0)
    assert thread.join_timeouts == []
    assert 'child still running' in message, message
    assert 'drain timed out before EOF' not in message, message


def test_live_child_startup_timeout_reports_observations(tmp):
    """A live child timeout reports its state, wait, and captured output."""
    del tmp
    program = ('import threading; '
               'print("recognisable startup line", flush=True); '
               'threading.Event().wait()')
    proc = subprocess.Popen(
        [sys.executable, '-c', program],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        marker = 'recognisable startup line\n'
        drained = _WaitableLines(marker)
        _util.drain_lines(proc, drained)
        drained.ready.wait()
        assert marker in drained, drained
        failure = ''
        try:
            _util.await_listening_line(
                proc, drained, timeout=1)
        except RuntimeError as exc:
            failure = str(exc)
        assert 'did not announce its port in 1s' in failure, failure
        assert 'child still running' in failure, failure
        assert '1 line(s) captured' in failure, failure
        assert re.search(r'waited \d+\.\ds', failure), failure
        assert 'recognisable startup line' in failure, failure
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_server_uses_the_shared_log_safe_function(tmp):
    """The bridge entry point must use the contract-tested shared renderer."""
    settings = {'DAEDALUS_DIR': str(tmp), 'DAEDALUS_PORT': '0'}
    saved = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    root = str(_util.ROOT)
    added_root = root not in sys.path
    if added_root:
        sys.path.insert(0, root)
    try:
        shared_log_safe = importlib.import_module('daedalus_bridge.log_safe')
        mod = _util.load(
            _util.ROOT / 'server.py', 'server_shared_log_safe_binding')
    finally:
        if added_root:
            sys.path.remove(root)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert mod.log_safe is shared_log_safe.log_safe


def test_log_safe_import_has_no_daedalus_environment_reads(tmp):
    """The shared renderer must not inspect bridge configuration."""
    del tmp
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith('DAEDALUS_') and key != 'TOKEN'
    }
    code = """
import os
import pathlib
import sys

class EnvironmentWithoutDaedalusReads(dict):
    def get(self, key, default=None):
        assert not key.startswith('DAEDALUS_'), key
        return super().get(key, default)

os.environ = EnvironmentWithoutDaedalusReads(os.environ)
from daedalus_bridge import log_safe

root = pathlib.Path.cwd().resolve()
loaded = []
for name, module in sys.modules.items():
    source = getattr(module, '__file__', None)
    if source is None:
        continue
    try:
        pathlib.Path(source).resolve().relative_to(root)
    except ValueError:
        continue
    loaded.append(name)
assert loaded == ['daedalus_bridge', 'daedalus_bridge.log_safe'], loaded
assert callable(log_safe.log_safe)
"""
    loaded = subprocess.run(
        [sys.executable, '-c', code], cwd=str(_util.ROOT), env=env,
        capture_output=True, text=True, check=False)
    assert loaded.returncode == 0, loaded.stderr


def test_bridge_configuration_is_resolvable_as_a_unit(tmp):
    """Configuration imports with documented paths, defaults, and guards."""
    root = Path(tmp) / 'config-root'
    root.mkdir()
    names = [
        'DAEDALUS_DIR', 'DAEDALUS_PORT', 'DAEDALUS_STREAM_MAX_AGE',
        'DAEDALUS_STREAM_KEEPALIVE', 'DAEDALUS_MAX_BODY_SIZE',
        'DAEDALUS_MAX_DELIVERY_RESULTS', 'DAEDALUS_MAX_JSON_DEPTH',
        'DAEDALUS_MAX_UNAUTHENTICATED_BODY', 'DAEDALUS_DEBUG_TIMING',
        'DAEDALUS_REQUEST_TIMEOUT', 'DAEDALUS_MAX_REQUEST_WORKERS',
        'DAEDALUS_MAX_SEGMENT_INDEX', 'DAEDALUS_MAX_SEGMENTS_PER_JOB',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE', 'DAEDALUS_CMD_TTL',
    ]
    env = {key: value for key, value in os.environ.items()
           if key not in names}
    env.update({'DAEDALUS_DIR': str(root), 'DAEDALUS_PORT': '0'})
    code = """
import json
from daedalus_bridge import config
print(json.dumps({
    'base': str(config.BASE),
    'cmd': str(config.CMD_DIR),
    'results': str(config.RES_DIR),
    'deliveries': str(config.DELIVERY_DIR),
    'uploads': str(config.UPLOAD_DIR),
    'segments': str(config.SEG_DIR),
    'dashboard': str(config.DASHBOARD_DIR),
    'port': config.PORT,
    'stream_max_age': config.STREAM_MAX_AGE,
    'stream_keepalive': config.STREAM_KEEPALIVE,
    'max_body_size': config.MAX_BODY_SIZE,
    'max_delivery_results': config.MAX_DELIVERY_RESULTS,
    'max_json_depth': config.MAX_JSON_DEPTH,
    'max_unauthenticated_body': config.MAX_UNAUTHENTICATED_BODY,
    'debug_timing': config.DEBUG_TIMING,
    'request_timeout': config.REQUEST_TIMEOUT,
    'max_request_workers': config.MAX_REQUEST_WORKERS,
    'max_segment_index': config.MAX_SEGMENT_INDEX,
    'max_segments_per_job': config.MAX_SEGMENTS_PER_JOB,
    'max_segment_job_size': config.MAX_SEGMENT_JOB_SIZE,
    'cmd_ttl': config.CMD_TTL,
}, sort_keys=True))
"""
    loaded = subprocess.run(
        [sys.executable, '-c', code], cwd=str(_util.ROOT), env=env,
        capture_output=True, text=True, check=False)
    assert loaded.returncode == 0, loaded.stderr
    values = json.loads(loaded.stdout)
    assert values == {
        'base': str(root),
        'cmd': str(root / 'commands'),
        'results': str(root / 'results'),
        'deliveries': str(root / 'results' / 'deliveries'),
        'uploads': str(root / 'uploads'),
        'segments': str(root / 'segments'),
        'dashboard': str(_util.ROOT / 'dashboard'),
        'port': 0,
        'stream_max_age': 3600.0,
        'stream_keepalive': 15.0,
        'max_body_size': 64 * 1024 * 1024,
        'max_delivery_results': 1024,
        'max_json_depth': 100,
        'max_unauthenticated_body': 64 * 1024,
        'debug_timing': False,
        'request_timeout': 60.0,
        'max_request_workers': 256,
        'max_segment_index': 99999,
        'max_segments_per_job': 10000,
        'max_segment_job_size': 4 * 1024 * 1024 * 1024,
        'cmd_ttl': 90.0,
    }

    # A set-but-off value: a reader keyed on presence reports the switch
    # on, and `env_flag` decides the same switch for `segment_store`.
    off_env = dict(env)
    off_env['DAEDALUS_DEBUG_TIMING'] = '0'
    off = subprocess.run(
        [sys.executable, '-c', code], cwd=str(_util.ROOT), env=off_env,
        capture_output=True, text=True, check=False)
    assert off.returncode == 0, off.stderr
    assert json.loads(off.stdout)['debug_timing'] is False, off.stdout

    missing_env = dict(env)
    del missing_env['DAEDALUS_DIR']
    refused = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.config'],
        cwd=str(_util.ROOT), env=missing_env,
        capture_output=True, text=True, check=False)
    assert refused.returncode == 1
    assert (refused.stdout + refused.stderr).strip() == (
        'DAEDALUS_DIR env var required (e.g. /srv/daedalus)')


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


def test_startup_reports_malloc_tuning_it_could_not_apply(tmp):
    """The diagnostic still reaches an operator, from the entry point.

    The transport holds the message instead of printing it at import, so
    the only thing that proves the report survived is a real child: it goes
    out before the readiness line, where a reader watching startup sees it.
    """
    output = []
    env = {'PYTHONPATH': _noglibc.no_glibc_pythonpath(tmp)}
    with _util.bridge(tmp, env=env, output=output) as (base, _docroot):
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)
    note_at = next((index for index, line in enumerate(output)
                    if 'malloc tuning unavailable' in line), None)
    listening_at = next((index for index, line in enumerate(output)
                         if 'Listening on' in line), None)
    assert note_at is not None, output
    assert listening_at is not None and note_at < listening_at, output


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


def test_shared_log_safe_never_raises_and_stays_useful(tmp):
    """The log-line safeguard must not itself be able to raise.

    str(value) was evaluated outside any fallback: a conversion-limited huge
    int raises ValueError under the default int-to-string limit, an exception
    object whose __str__ fails propagates its own error, and a str subclass
    can hand .encode() to code that raises or .decode() to one returning a
    non-string — the same failure class the helper exists to close, one
    layer inside it.
    """
    del tmp
    mod = _util.load(_util.ROOT / 'daedalus_bridge' / 'log_safe.py',
                     'shared_log_safe_contract')

    for value, expected in _util.log_safe_cases():
        assert mod.log_safe(value) == expected, (
            f'log_safe({type(value).__name__}) disagrees')
    # Ordinary values pass through in full, ASCII and non-ASCII alike.
    assert mod.log_safe('plain ascii') == 'plain ascii'
    assert mod.log_safe('héllo — 世界') == 'héllo — 世界'


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
        assert raw != server_py and b'BaseHTTPRequestHandler' not in raw


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='bridgestartup_')


if __name__ == '__main__':
    raise SystemExit(main())
