#!/usr/bin/env python3
"""Regression suite for the standalone MCP file entry point."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402


def _await_mcp_listener(proc, output, timeout):
    started = time.time()
    deadline = started + timeout
    while time.time() <= deadline:
        for line in output:
            match = re.search(
                r'\[MCP\] streamable-http on 127\.0\.0\.1:(\d+)', line)
            if match:
                return int(match.group(1))
        if proc.poll() is not None:
            raise AssertionError(
                'direct-file MCP entry point exited before listening: '
                + _util._startup_observations(
                    proc, output, time.time() - started))
        time.sleep(0.05)
    raise AssertionError(
        'direct-file MCP entry point did not listen: '
        + _util._startup_observations(proc, output, timeout))


def test_the_mcp_server_runs_by_absolute_file_path(tmp):
    """Direct-file execution must bootstrap package imports from any cwd."""
    env = _util.coverage_free_environment(os.environ)
    env.pop('PYTHONPATH', None)
    env.update({
        'DAEDALUS_LOCAL_URL': 'http://127.0.0.1:1',
        'DAEDALUS_MCP_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1',
    })
    script = (_util.ROOT / 'daedalus_mcp' / 'server.py').resolve()
    proc = subprocess.Popen(
        [sys.executable, str(script)], cwd=tmp, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output = _util.drain_lines(proc)
    try:
        port = _await_mcp_listener(
            proc, output, _util.startup_timeout())
        assert port > 0, output
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
