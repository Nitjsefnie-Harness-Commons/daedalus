"""Running the real CLI against a fixture bridge, and answering what it sent.

Not a suite itself — run_tests.py only loads `test_*.py`.

The CLI is always run as a subprocess, the way a shell would run it, and it
reads its configuration from the environment. Both of those are one decision
rather than a per-suite one: a suite that spawns the CLI with the runner's own
`DAEDALUS_*` inherited, or that answers the command out of a different queue
than the one the subcommand wrote to, is testing a bridge the operator never
meets. So the env the CLI runs under, the token naming its queue, and the
answer half live here, and a suite that drives the CLI reads this copy rather
than carrying its own. The two environments built elsewhere are named where
they are built — tests/_overlap_clients.py and tests/test_cli_result_wait.py —
because each strips a different set than this one.
tests/test_cli_helpers.py pins what `cli_env` strips and sets.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402
from _cmdqueue import clear_command_queue, wait_for_command  # noqa: E402

CLI = [sys.executable, '-c', 'from daedalus_cli.cli import main; main()']

TOK = 'clitok'
BRIDGE_ENV = {'DAEDALUS_TOKEN': TOK, 'TOKEN': ''}


def cli_env(**overrides):
    """A clean environment: none of the CLI's config vars leak in from ours."""
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    # PYTHONIOENCODING goes too, so the CLI applies its own rule for a piped
    # stream instead of inheriting whatever this runner was started with. A
    # test that wants a specific one passes it back through overrides.
    for k in ('TOKEN', 'ID', 'PYTHONIOENCODING'):
        env.pop(k, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.update(overrides)
    return env


def run_cli(args, env, timeout=60):
    return subprocess.run(CLI + args, cwd=str(_util.ROOT), env=env,
                          capture_output=True, text=True, encoding='utf-8',
                          timeout=timeout)


def _answer_one_ext_command(base, docroot, argv, result, env):
    """Run one typed subcommand and answer the command it enqueues.

    Returns (returncode, stdout, stderr, the payload the bridge received).
    The payload is the point: every one of these subcommands is a wire
    contract: the `type` the extension dispatches on, and the fields it reads
    off the command. The repository already carries a guard for confusing
    `tab` with a browser `tabId`, and this pins the senders themselves.
    """
    # Nothing consumes this queue — there is no extension here — so a command
    # from an earlier case may remain. Clearing and excluding refused survivors
    # makes the file this case waits for unambiguously its own.
    qdir = Path(docroot) / 'commands' / f'{TOK}_extension'
    ignored_names = clear_command_queue(qdir)
    proc = subprocess.Popen(
        CLI + argv, cwd=str(_util.ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding='utf-8')
    try:
        queued = wait_for_command(qdir, 15, ignored_names=ignored_names)
        if queued is None:
            raise AssertionError(
                f'timed out waiting for the command {argv[0]} enqueues')
        status, _ = _util.post_json(base + '/result', {
            'token': TOK, 'tabId': 'extension', 'id': queued['id'],
            'result': result, 'error': None, 'ts': 1, '_did': queued['_did']})
        assert status == 200, (argv, status)
        out, err = proc.communicate(timeout=60)
    finally:
        _drain.kill_and_drain(proc)
    return proc.returncode, out, err, queued
