#!/usr/bin/env python3
"""Suite for daedalus_cli — the result wait against a real bridge.

A foreign result is neither consumed nor returned as ours; the waiter
keeps polling and completes on its own delivery id; two same-id clients
stay correlated in either completion order; and a negative timeout is
refused before any command is sent. The CLI is always run as a
subprocess, the way a shell would run it.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _overlap_clients  # noqa: E402
import _util  # noqa: E402
from _cli_helpers import BRIDGE_ENV, CLI, TOK, cli_env, run_cli  # noqa: E402
from _queueread import queued_command  # noqa: E402


def _wait_for(predicate, timeout=15, what='condition'):
    left_ms = timeout * 1000 + 50
    while (left_ms := left_ms - 50) > 0:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f'timed out waiting for {what}')


def test_waiter_leaves_a_foreign_result_in_place(tmp):
    """A waiter that sees another caller's result must not consume it.

    The foreign result is posted while the CLI waiter is already polling, the
    waiter's own result never arrives, and the foreign result remains readable
    afterwards. Driven through `cookies` because typed extension commands use
    the shared extension result slot.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            _wait_for(lambda: qdir.is_dir() and any(qdir.glob('*.json')),
                      what='the enqueued command file')
            # Another caller's result lands while our waiter is polling.
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': 'theirs',
                'result': 'not yours', 'error': None, 'ts': 1})
            assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
        # Our result never arrived, so the waiter times out ...
        assert proc.returncode != 0, (proc.returncode, out, err)
        assert 'Timeout' in err, (out, err)
        # ... and the foreign result is still there to be read.
        status, body = _util.get_json(
            base + f'/result?token={TOK}&tab=extension')
        assert status == 200, status
        assert body.get('id') == 'theirs', body
        assert body.get('result') == 'not yours', body


def test_waiter_skips_a_foreign_result_and_finds_its_own(tmp):
    """A foreign result seen mid-wait is neither returned as ours nor fatal:
    the waiter keeps polling and completes when its own result arrives."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'],
            cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            queued = queued_command(qdir, 'the enqueued command file')
            # A foreign result first (results share one slot per tab, so the
            # own result posted after it overwrites the slot) ...
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': 'theirs',
                'result': 'not yours', 'error': None, 'ts': 1})
            assert status == 200, status
            # let the waiter see the foreign result at least once
            time.sleep(0.6)
            # ... then our own.
            status, _ = _util.post_json(base + '/result', {
                'token': TOK, 'tabId': 'extension', 'id': queued['id'],
                'result': [], 'error': None, 'ts': 2,
                '_did': queued['_did']})
            assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
        assert proc.returncode == 0, (proc.returncode, out, err)
        assert '0 cookies' in out, out
        assert 'not yours' not in out, out
        # The waiter consumed its own result.
        assert not (Path(docroot) / 'results'
                    / f'{TOK}_extension.json').exists()


def test_typed_command_does_not_return_a_stale_fixed_id_result(tmp):
    """A prior `_cookies` result cannot satisfy a new cookies invocation."""
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': '_cookies',
            'result': [{'domain': 'stale.invalid', 'name': 'stale',
                        'value': 'old'}],
            'error': None, 'ts': 1, '_did': '1000000000000_000001'})
        assert status == 200, status

        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        proc = subprocess.Popen(
            CLI + ['cookies'], cwd=str(_util.ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        try:
            qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
            queued = queued_command(qdir, 'the fresh cookies command')
            # Let the first poll observe the stale result before answering the
            # newly queued invocation as the extension would.
            time.sleep(0.7)
            if proc.poll() is None:
                status, _ = _util.post_json(base + '/result', {
                    'token': TOK, 'tabId': 'extension', 'id': queued['id'],
                    'result': [{'domain': 'fresh.invalid', 'name': 'fresh',
                                'value': 'new'}],
                    'error': None, 'ts': 2, '_did': queued['_did']})
                assert status == 200, status
            out, err = proc.communicate(timeout=30)
        finally:
            _drain.kill_and_drain(proc)
        assert proc.returncode == 0, (proc.returncode, out, err)
        assert 'fresh.invalid' in out, out
        assert 'stale.invalid' not in out, out


def test_two_same_id_clients_receive_only_their_own_results(tmp):
    """Two CLI callers stay correlated in either completion order."""
    def client_argv(owner):
        # The client's patience has to cover this fixture's whole setup -- a
        # node spawn and the harness's own waits -- and the default ten
        # seconds does not on a loaded Windows runner: both clients exited
        # with `Timeout (10s)` before the first result was posted, leaving
        # nobody to consume it.
        return CLI + [
            'cookies', '--domain', owner, '--timeout', '120',
        ]

    background = _util.ROOT / 'extension' / 'background.js'
    actual = {
        'a-first': _overlap_clients.run_same_id_client_overlap(
            Path(tmp) / 'a-first', ['owner-a', 'owner-b'], client_argv,
            cli_env(), TOK, background),
        'b-first': _overlap_clients.run_same_id_client_overlap(
            Path(tmp) / 'b-first', ['owner-b', 'owner-a'], client_argv,
            cli_env(), TOK, background),
    }
    per_owner = {
        owner: {
            'returncode': 0,
            'ownResult': True,
            'foreignResult': False,
            'stderr': '',
        }
        for owner in ('owner-a', 'owner-b')
    }
    assert actual == {
        'a-first': per_owner,
        'b-first': per_owner,
    }, actual


def test_a_negative_timeout_is_refused_before_the_command_is_sent(tmp):
    """Refusing the wait is only useful if nothing was admitted first.

    The command was PUT before the deadline was evaluated, so a negative
    timeout polled zero times, told the caller it had timed out, and left a
    command the browser was still free to execute. Retrying after that
    report runs the side effect twice.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        env = cli_env(DAEDALUS_URL=base, DAEDALUS_TOKEN=TOK)
        r = run_cli(['screenshot', '--timeout', '-1'], env)
        assert r.returncode != 0, (r.returncode, r.stdout, r.stderr)
        assert 'timeout must not be negative' in r.stderr, r.stderr
        qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
        queued = sorted(qdir.glob('*.json')) if qdir.is_dir() else []
        assert queued == [], queued

        # Zero keeps the meaning it already had — "unset", so the
        # subcommand's own default applies — and is not refused.
        r = run_cli(['screenshot', '--timeout', '0', '--help'], env)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
