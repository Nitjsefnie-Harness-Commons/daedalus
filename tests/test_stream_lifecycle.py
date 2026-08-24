#!/usr/bin/env python3
"""Regression: an ended SSE stream must close its socket, not go silent.

The bridge ends a `/stream` response when the stream hits its max age or is
replaced by a reconnect. If the socket stays open afterwards the client sees
silence rather than EOF, its fast reconnect path never fires, and recovery
falls through to a multi-second watchdog — a failure that looks like a slow
network rather than like a server that forgot to hang up.

This lived in `scripts/` with a bespoke runner, which meant `run_tests.py`
never ran it and it only ever executed when somebody remembered it existed.
"""
import http.client
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402

MAX_AGE = 3.0
# EOF has to follow the stream's end promptly. Generous against the ~0s
# expected, tight against the 30s watchdog the bug fell through to.
EOF_DEADLINE = MAX_AGE + 3.0


def test_port_zero_binds_an_ephemeral_port_and_announces_it(tmp):
    """DAEDALUS_PORT=0 lets the kernel pick; the Listening line names the port.

    The bridge fixture drives this for every child it starts: with no
    test-chosen number there is no release/rebind window for a concurrent
    process to win.
    """
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(Path(tmp) / 'docroot'),
        'DAEDALUS_PORT': '0',
        'DAEDALUS_MCP_PORT': '0',
        'DAEDALUS_TOKEN': 'lifecycle-test',
        'TOKEN': '',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1',
    })
    proc = subprocess.Popen(
        [sys.executable, str(_util.ROOT / 'server.py')],
        cwd=_util.ROOT, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    try:
        # Read through the shared reader rather than off the first line: it
        # searches the whole output and gives up on a deadline, so a child
        # that prints something else first is read correctly and one that
        # never announces fails here instead of blocking on readline().
        port = _util.await_listening_line(proc, _util.drain_lines(proc))
        assert port, 'no Listening line carrying an actual port'
        status, health = _util.get_json(f'http://127.0.0.1:{port}/health')
        assert status == 200 and health['ok'] is True, (status, health)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _noise_path(tmp, name, body):
    """A PYTHONPATH directory whose sitecustomize runs `body` at startup."""
    directory = Path(tmp) / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'sitecustomize.py').write_text(body, encoding='utf-8')
    return str(directory)


def test_a_line_printed_before_the_announcement_does_not_hide_it(tmp):
    """Startup output the reader does not recognise is skipped, not taken.

    The bridge prints whatever its platform gives it cause to before it
    binds — on a non-glibc host `mallopt` is missing and the malloc-tuning
    diagnostic goes out first, and a checkout without the MCP dependencies
    reports that bootstrap failure too. Those lines are worth keeping, so
    readiness has to be found by searching the output for the announcement.
    """
    noise = ('print("[Daedalus] malloc tuning unavailable: '
             'dlsym(0x0, mallopt): symbol not found", flush=True)\n')
    output = []
    with _util.bridge(tmp, env={'PYTHONPATH': _noise_path(tmp, 'noise', noise)},
                      output=output) as (base, _docroot):
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)
    noise_at = next((index for index, line in enumerate(output)
                     if 'malloc tuning unavailable' in line), None)
    listening_at = next((index for index, line in enumerate(output)
                         if 'Listening on' in line), None)
    assert noise_at is not None and listening_at is not None, output
    assert noise_at < listening_at, output


def test_a_failed_mcp_bootstrap_names_the_extra_that_supplies_it(tmp):
    """The degraded start must be actionable, not just observed.

    Without the optional dependencies the bridge comes up normally and the
    MCP endpoint silently is not there, so a reader following the README's
    MCP section sees a working bridge and a client that cannot connect. The
    line that reports the failure is the one place that can name the install
    that fixes it.
    """
    blocked = 'import sys\nsys.modules["mcp_server"] = None\n'
    output = []
    with _util.bridge(tmp,
                      env={'PYTHONPATH': _noise_path(tmp, 'nomcp', blocked)},
                      output=output) as (base, _docroot):
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)
    reported = [line for line in output if 'MCP bootstrap failed' in line]
    assert reported, output
    assert any('.[mcp]' in line for line in reported), reported


def test_the_announcement_does_not_wait_on_a_reverse_dns_lookup(tmp):
    """Startup must not depend on a name service answering.

    HTTPServer.server_bind resolves the bound host through socket.getfqdn
    once the socket is already listening. Where that lookup hangs — a host
    with no reverse zone for loopback, a resolver that never answers — the
    announcement is the thing that stalls, so every caller waiting on
    readiness times out against a bridge whose port is in fact open.
    """
    stall = ('import socket, time\n'
             'socket.getfqdn = lambda name="": time.sleep(600) or name\n')
    with _util.bridge(
            tmp,
            env={'PYTHONPATH': _noise_path(tmp, 'stalled-dns', stall)},
    ) as (base, _docroot):
        status, health = _util.get_json(base + '/health')
        assert status == 200 and health['ok'] is True, (status, health)


def test_a_unix_socket_subclass_binds_the_way_the_stdlib_binds_it(tmp):
    """The override must accept every address the stdlib method accepts.

    HTTPServer.server_bind assigns the second element of the address as it
    stands. Coercing it with int() changes what the method accepts: an
    AF_UNIX address is its own path, so that element is a character of the
    path, and a subclass the stdlib would have bound raises ValueError under
    the override instead.
    """
    if not hasattr(socket, 'AF_UNIX'):
        _util.skip('no AF_UNIX on this platform')
    docroot = Path(tmp) / 'docroot'
    # Bound by a bare relative name from inside tmp: an AF_UNIX path is
    # limited to about a hundred bytes, and macOS runners hand out temp
    # directories long enough to exceed that on their own.
    probe = (
        'import os, socket\n'
        'from http.server import BaseHTTPRequestHandler\n'
        'import server\n'
        f'os.chdir({str(tmp)!r})\n'
        'class UnixServer(server.ThreadingHTTPServer):\n'
        '    address_family = socket.AF_UNIX\n'
        'srv = UnixServer("s.sock", BaseHTTPRequestHandler)\n'
        'try:\n'
        '    print("bound", repr(srv.server_name), repr(srv.server_port))\n'
        'finally:\n'
        '    srv.server_close()\n'
    )
    env = dict(os.environ)
    env.update({
        'DAEDALUS_DIR': str(docroot),
        # The probe binds its own AF_UNIX socket; this one is never bound.
        'DAEDALUS_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    proc = subprocess.run(
        [sys.executable, '-c', probe], cwd=_util.ROOT, env=env,
        capture_output=True, text=True, timeout=60)
    output = (proc.stdout + proc.stderr).strip()
    assert proc.returncode == 0, output
    expected = "bound 's' '.'"  # the address is the path; [:2] splits it
    assert expected in output, (expected, output)


def test_a_child_that_never_announces_fails_on_the_deadline(tmp):
    """The search is bounded, and says what the child printed instead."""
    del tmp
    # Built before the list rather than inside it: two adjacent literals
    # between commas read as a missing comma, which is a different program.
    program = ('import sys, time; '
               'print("nothing to do with the port", flush=True); '
               'time.sleep(600)')
    proc = subprocess.Popen(
        [sys.executable, '-c', program],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        started = time.time()
        failure = ''
        try:
            _util.await_listening_line(proc, _util.drain_lines(proc), timeout=1)
        except RuntimeError as e:
            failure = str(e)
        elapsed = time.time() - started
        assert failure, 'a silent child was read as an announcement'
        assert elapsed < 10, elapsed
        assert 'did not announce its port in 1s' in failure, failure
        assert 'nothing to do with the port' in failure, failure
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_an_ended_stream_closes_its_socket(tmp):
    env = {
        'DAEDALUS_STREAM_MAX_AGE': str(MAX_AGE),
        'TOKEN': '',
        'DAEDALUS_TOKEN': 'lifecycle-test',
        # The MCP thread would otherwise try the default port, which another
        # bridge in this suite may already hold; 0 lets the kernel pick.
        'DAEDALUS_MCP_PORT': '0',
    }
    with _util.bridge(tmp, env=env) as (base, _docroot):
        port = int(base.rsplit(':', 1)[1])
        conn = http.client.HTTPConnection('127.0.0.1', port,
                                          timeout=EOF_DEADLINE + 5)
        conn.request('GET', '/stream?token=lifecycle-test&tab=probe')
        resp = conn.getresponse()
        assert resp.status == 200, f'/stream returned {resp.status}'

        started = time.time()
        try:
            while resp.read(4096):
                pass
            how = 'clean EOF'
        except (http.client.IncompleteRead, ConnectionError, OSError) as e:
            how = type(e).__name__
        elapsed = time.time() - started
        conn.close()

    assert elapsed <= EOF_DEADLINE, (
        f'stream ended at max age {MAX_AGE}s but the client saw no EOF until '
        f'{elapsed:.1f}s ({how}) — the socket was left open')


def test_the_bridge_refuses_to_start_without_its_docroot(tmp):
    """Both settings are required, and the failure says which one is missing.

    A bridge that defaulted its docroot would write commands and uploads
    somewhere nobody chose — most likely the current directory of whatever
    started it.
    """
    for missing in ('DAEDALUS_DIR', 'DAEDALUS_PORT'):
        env = dict(os.environ)
        # The port is never bound (startup fails first), so 0 reserves nothing.
        env.update({'DAEDALUS_DIR': tmp, 'DAEDALUS_PORT': '0',
                    'PYTHONDONTWRITEBYTECODE': '1'})
        del env[missing]
        proc = subprocess.run(
            [sys.executable, str(_util.ROOT / 'server.py')],
            env=env, capture_output=True, text=True, timeout=60)
        assert proc.returncode != 0, f'the bridge started without {missing}'
        assert missing in (proc.stdout + proc.stderr), (
            f'the failure for a missing {missing} does not name it: '
            f'{(proc.stdout + proc.stderr).strip()[:200]}')


def test_numeric_environment_settings_fail_cleanly_at_startup(tmp):
    """Invalid numeric settings stop startup with a setting-specific error."""
    cases = (
        ('DAEDALUS_PORT', 'not-an-integer', 'integer from 0 to 65535'),
        ('DAEDALUS_PORT', '70000', 'integer from 0 to 65535'),
        ('DAEDALUS_STREAM_MAX_AGE', 'nan', 'finite positive number'),
        ('DAEDALUS_STREAM_KEEPALIVE', 'inf', 'finite positive number'),
        ('DAEDALUS_MAX_BODY_SIZE', '-1', 'non-negative integer'),
        ('DAEDALUS_MAX_SEGMENT_INDEX', '-1', 'non-negative integer'),
        ('DAEDALUS_MAX_SEGMENTS_PER_JOB', '-1', 'non-negative integer'),
        ('DAEDALUS_MAX_SEGMENT_JOB_SIZE', '-1', 'non-negative integer'),
        ('DAEDALUS_CMD_TTL', '0', 'finite positive number'),
        ('DAEDALUS_REQUEST_TIMEOUT', '0', 'finite positive number'),
        ('DAEDALUS_MAX_REQUEST_WORKERS', '0', 'integer from 1 to 4096'),
    )
    failures = []
    for name, value, requirement in cases:
        env = dict(os.environ)
        env.update({
            'DAEDALUS_DIR': str(Path(tmp) / name.lower()),
            # Overridden by the DAEDALUS_PORT cases below; never bound, so 0.
            'DAEDALUS_PORT': '0',
            'PYTHONDONTWRITEBYTECODE': '1',
            name: value,
        })
        proc = subprocess.run(
            [sys.executable, '-c', 'import server'], cwd=_util.ROOT,
            env=env, capture_output=True, text=True, timeout=60)
        output = (proc.stdout + proc.stderr).strip()
        if (proc.returncode == 0 or 'Traceback' in output
                or name not in output or requirement not in output):
            failures.append(
                f'{name}={value!r}: exit={proc.returncode}, output={output!r}')
    assert not failures, '\n'.join(failures)


def test_non_finite_command_ttl_cannot_disable_the_collector(tmp):
    """The real collector must not run with a non-finite expiry bound."""
    failures = []
    for value in ('nan', 'inf'):
        docroot = Path(tmp) / value
        queued = docroot / 'commands' / 'queue' / 'old.json'
        queued.parent.mkdir(parents=True)
        queued.write_text('{"id":"old"}', encoding='utf-8')
        os.utime(queued, (0, 0))
        env = dict(os.environ)
        env.update({
            'DAEDALUS_DIR': str(docroot),
            # Never bound (TTL validation fails at import), so 0.
            'DAEDALUS_PORT': '0',
            'DAEDALUS_CMD_TTL': value,
            'PYTHONDONTWRITEBYTECODE': '1',
        })
        probe = (
            'import server\n'
            'server._collect_expired_commands()\n'
            'artifact = server.CMD_DIR / "queue" / "old.json"\n'
            'print(f"collector-returned retained={artifact.exists()}")\n'
        )
        proc = subprocess.run(
            [sys.executable, '-c', probe], cwd=_util.ROOT, env=env,
            capture_output=True, text=True, timeout=60)
        output = (proc.stdout + proc.stderr).strip()
        if (proc.returncode == 0 or 'DAEDALUS_CMD_TTL' not in output
                or 'finite positive number' not in output):
            failures.append(
                f'DAEDALUS_CMD_TTL={value!r}: exit={proc.returncode}, '
                f'output={output!r}, retained={queued.exists()}')
    assert not failures, '\n'.join(failures)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='streamlifecycle_')


if __name__ == '__main__':
    raise SystemExit(main())
