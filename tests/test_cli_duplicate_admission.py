#!/usr/bin/env python3
"""Suite for daedalus_cli — what a retried command id admits.

A sibling of tests/test_cli.py, which is size-frozen. Same fixture style:
the CLI is always run as a subprocess, the way a shell would run it, against
a real bridge() no extension is draining.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402

# Keep bridge children off the fixed MCP port (see tests/_bridge.py).
os.environ.setdefault('DAEDALUS_MCP_PORT', '0')

CLI = [sys.executable, '-c', 'from daedalus_cli.cli import main; main()']

# The result header's marker is whichever glyph the console can encode, so
# what is pinned is that a marker immediately precedes the id (see
# tests/test_cli.py).
IN_MARKS = ('←', '<-')
TOK = 'clitok'
os.environ['TOKEN'] = ''
os.environ['DAEDALUS_TOKEN'] = TOK
BRIDGE_ENV = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}


def cli_env(**overrides):
    """A clean environment: none of the CLI's config vars leak in from ours."""
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    for k in ('TOKEN', 'ID', 'PYTHONIOENCODING'):
        env.pop(k, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.update(overrides)
    return env


def run_cli(args, env):
    return subprocess.run(CLI + args, cwd=str(_util.ROOT), env=env,
                          capture_output=True, text=True, encoding='utf-8',
                          timeout=60)


def test_a_retried_exec_announces_and_reuses_the_live_delivery(tmp):
    """A timed-out retry reuses the queued delivery instead of queueing
    a second execution.

    Both invocations time out against a bridge no extension is draining:
    the second PUT must answer with the first delivery's id and carry the
    duplicate notice, and a result filed for that delivery completes a
    later retry waiting on it.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        argv = ['exec', 'same-id', 'document.title', '-t', '1']
        first = run_cli(argv, env)
        assert first.returncode == 1, (
            first.returncode, first.stdout, first.stderr)
        assert 'Timeout (1s)' in first.stderr, first.stderr
        assert 'already queued' not in first.stdout, first.stdout
        qdir = Path(docroot) / 'commands' / TOK
        published = sorted(qdir.glob('*.json'))
        assert len(published) == 1, published
        first_did = json.loads(
            published[0].read_text(encoding='utf-8'))['_did']

        second = run_cli(argv, env)
        assert second.returncode == 1, (
            second.returncode, second.stdout, second.stderr)
        assert 'Timeout (1s)' in second.stderr, second.stderr
        assert 'already queued' in second.stdout, second.stdout
        published = sorted(qdir.glob('*.json'))
        assert len(published) == 1, published
        assert json.loads(
            published[0].read_text(encoding='utf-8'))['_did'] == first_did

        waiter = subprocess.Popen(
            CLI + ['exec', 'same-id', 'document.title', '-t', '20'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'id': 'same-id', 'result': 'Hello Title',
                'error': None, 'ts': 1, '_did': first_did})
            assert status == 200, status
            out, err = waiter.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(waiter)
        assert waiter.returncode == 0, (waiter.returncode, out, err)
        assert any(f'{m} same-id' in out for m in IN_MARKS), out
        assert 'already queued' in out, out
        assert 'Hello Title' in out, out


def test_a_no_wait_retry_names_what_it_did_not_wait_on(tmp):
    """A --no-result retry announces the coalescing without a wait claim."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        argv = ['exec', 'same-id', 'document.title', '--no-result']
        first = run_cli(argv, env)
        assert first.returncode == 0, (
            first.returncode, first.stdout, first.stderr)
        assert 'already queued' not in first.stdout, first.stdout
        qdir = Path(docroot) / 'commands' / TOK
        published = sorted(qdir.glob('*.json'))
        assert len(published) == 1, published

        second = run_cli(argv, env)
        assert second.returncode == 0, (
            second.returncode, second.stdout, second.stderr)
        assert 'already queued' in second.stdout, second.stdout
        assert 'not waiting' in second.stdout, second.stdout
        assert 'waiting on that delivery' not in second.stdout, second.stdout


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
