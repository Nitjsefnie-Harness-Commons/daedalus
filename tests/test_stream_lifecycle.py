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
import re
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
        port = None
        deadline = time.time() + 20
        while port is None and time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(
                    'bridge exited with DAEDALUS_PORT=0: '
                    + proc.stdout.read()[:400])
            match = re.search(
                r'\[Daedalus\] Listening on 127\.0\.0\.1:(\d+)',
                proc.stdout.readline())
            if match:
                port = int(match.group(1))
        assert port, 'no Listening line carrying an actual port'
        status, health = _util.get_json(f'http://127.0.0.1:{port}/health')
        assert status == 200 and health['ok'] is True, (status, health)
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
