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
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.update({
        'DAEDALUS_LOCAL_URL': 'http://127.0.0.1:1',
        'DAEDALUS_MCP_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1',
    })
    script = (_util.ROOT / 'daedalus_mcp' / 'server.py').resolve()
    proc = subprocess.Popen(
        [sys.executable, str(script)], cwd=tmp,
        env=_util.child_coverage('scrub', env),
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


def test_the_mcp_server_runs_by_symlinked_file_path(tmp):
    """A direct symlink launch must resolve package imports from the target."""
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.update({
        'DAEDALUS_LOCAL_URL': 'http://127.0.0.1:1',
        'DAEDALUS_MCP_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1',
    })
    script = (_util.ROOT / 'daedalus_mcp' / 'server.py').resolve()
    link = Path(tmp) / 'bin' / 'server.py'
    link.parent.mkdir()
    link.symlink_to(script)
    proc = subprocess.Popen(
        [sys.executable, str(link)], cwd=tmp,
        env=_util.child_coverage('scrub', env),
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


def test_flat_loading_the_mcp_server_does_not_change_sys_path(tmp):
    """The test loader must not trigger direct-execution bootstrapping."""
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    probe = '''
import sys
sys.path.insert(0, sys.argv[1])
import _util
root = str(_util.ROOT)
counts = [sys.path.count(root)]
_util.load(sys.argv[2], 'first_mcp_server')
counts.append(sys.path.count(root))
_util.load(sys.argv[2], 'second_mcp_server')
counts.append(sys.path.count(root))
print('ROOT_COUNTS', *counts)
'''
    server = (_util.ROOT / 'daedalus_mcp' / 'server.py').resolve()
    loaded = subprocess.run(
        [sys.executable, '-c', probe, str(_util.ROOT / 'tests'), str(server)],
        cwd=tmp, env=_util.child_coverage('scrub', env),
        capture_output=True, text=True, timeout=30)
    assert loaded.returncode == 0, loaded.stdout + loaded.stderr
    marker = next(
        line for line in loaded.stdout.splitlines()
        if line.startswith('ROOT_COUNTS '))
    counts = tuple(int(value) for value in marker.split()[1:])
    assert counts == (0, 0, 0), (
        f'flat loads changed the repository root count: {counts}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
