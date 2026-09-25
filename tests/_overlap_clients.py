"""The overlap harness's client-process diagnostics.

Not a suite itself — run_tests.py only loads `test_*.py`.

The helpers here keep the subprocesses of an overlap run observable: a
scripted result server that answers a chosen status, the environment and argv
of a real `cookies` client, and the same-id client overlap that drives two
real clients and a real bridge and reports both client and harness evidence.
They live beside the Node harness itself in `_overlap`, which holds the
driver they call back into; every suite imports each name from the module
that now owns it.
"""
import contextlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cmdqueue  # noqa: E402
import _drain  # noqa: E402
import _util  # noqa: E402
from _clientstate import assert_clients_exited, client_states  # noqa: E402

# `_overlap_clients` drives the harness in `_overlap`, so importing that
# module at the top would be a cycle (`pylint` reads R0401 as fatal, and CI
# treats it so). It is imported inside the one function that calls back.

# Publication and healthy exits may move together: expiry on a killed client's
# pipes means a broken drain, not a busy runner; the parameter only forces it.
_CLIENT_COMMAND_WAIT_S = 15
_FAILED_CLIENT_GRACE_S = 1


def _wait_for_client_commands(queue, count):
    commands = _cmdqueue.wait_for_commands(
        queue, count, _CLIENT_COMMAND_WAIT_S)
    if commands is None:
        raise AssertionError(
            'timed out waiting for both same-id client commands')
    return commands


@contextlib.contextmanager
def _slow_result_server(post_delay=0, post_status=200, post_statuses=None,
                        post_body=None):
    statuses = list(post_statuses or [post_status])
    status_lock = threading.Lock()
    status_index = 0

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            if post_delay:
                time.sleep(post_delay)
            nonlocal status_index
            with status_lock:
                index = min(status_index, len(statuses) - 1)
                status_index += 1
            status = statuses[index]
            body = post_body
            if body is None:
                body = b'{}' if status == 200 else b'{"error":"no"}'
            if isinstance(body, str):
                body = body.encode('utf-8')
            try:
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                # A delayed POST can outlive the child the backstop killed,
                # so writing to its closed socket is expected.
                pass

        def do_GET(self):
            time.sleep(60)
            body = b'{"pending":false}'
            try:
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                # The test deliberately ends the peer while this is pending,
                # so its closed socket is expected to reset here.
                pass

        # pylint: disable-next=redefined-builtin
        def log_message(self, format, *args):
            del format, args

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def client_env():
    """A client environment, minus any bridge coordinates this process has."""
    env = {name: value for name, value in os.environ.items()
           if not name.startswith('DAEDALUS_')}
    for key in ('TOKEN', 'ID'):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def cookie_client_argv(owner):
    """The argv of a real `cookies` client for one owner."""
    return [
        sys.executable, '-c',
        'from daedalus_cli.cli import main; main()',
        'cookies', '--domain', owner, '--timeout', '120',
    ]


def _client_failure_diagnostics(bridge_log, docroot):
    """The announcement, the log tail and the deliveries, for one diagnosis.

    The announcement is selected out of the whole log rather than left to the
    tail: it names which bridge this was, and a client dying mid-request
    makes the bridge print enough afterwards to push it out of the window.
    """
    announced = _util.listening_line(bridge_log) or 'no announcement captured'
    tail = ''.join(bridge_log[-40:]).strip() or 'no bridge log captured'
    root = Path(docroot) / 'results' / 'deliveries'
    lines = []
    for path in sorted(root.rglob('*.json')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            # A client's consume deleted it after listing: retained, not lost.
            continue
        relative = path.relative_to(root).as_posix()
        lines.append(
            f"{relative}: deliveryId {record['deliveryId']}")
    delivery = '\n'.join(lines) or 'no delivery retained'
    return (f'bridge announcement:\n{announced.strip()}\n'
            f'bridge log tail:\n{tail}\ndelivery state:\n{delivery}')


def run_same_id_client_overlap(tmp, completion_order, client_argv, env,
                               token, background, *,
                               stop_clients_after_enqueue=False):
    """Drive real same-id CLI clients and preserve both failure surfaces.

    With `stop_clients_after_enqueue` the clients are stopped once their
    commands are queued, so a manufactured diagnosis cannot race a consume.
    """
    import _overlap  # noqa: E402  pylint: disable=import-outside-toplevel

    owners = ('owner-a', 'owner-b')
    bridge_env = {'TOKEN': '', 'DAEDALUS_TOKEN': token}
    bridge_log = []
    with _util.bridge(
            tmp, env=bridge_env, output=bridge_log) as (base, docroot):
        client_env = dict(env)
        client_env.update({
            'DAEDALUS_URL': base,
            'DAEDALUS_TOKEN': token,
        })
        processes = {
            owner: subprocess.Popen(
                client_argv(owner), cwd=str(_util.ROOT), env=client_env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8')
            for owner in owners
        }
        try:
            queue = Path(docroot) / 'commands' / f'{token}_extension'
            queued = _wait_for_client_commands(queue, len(owners))
            by_owner = {command['domain']: command for command in queued}
            assert set(by_owner) == set(owners), by_owner
            commands = [by_owner[owner] for owner in owners]
            if stop_clients_after_enqueue:
                for process in processes.values():
                    _drain.kill_and_drain(process)
                alive = [owner for owner, process in processes.items()
                         if process.poll() is None]
                assert not alive, f'clients survived their stop: {alive}'
            try:
                posted = _overlap.run_background_overlap(
                    background, commands, completion_order,
                    result_base=base, token=token, wait_between=False)
            except AssertionError as failure:
                states = client_states(
                    processes, grace=_FAILED_CLIENT_GRACE_S)
                raise AssertionError(
                    f'{failure}; clients: '
                    f'{states}\n'
                    f'{_client_failure_diagnostics(bridge_log, docroot)}'
                ) from failure
            # The client's own `--timeout` bounds it, so waiting here needs no
            # wall-clock margin of its own: one that outlived its result would
            # only be killed while about to finish on its own.
            states = client_states(processes, grace=None)
            try:
                assert_clients_exited(states, posted)
            except AssertionError as failure:
                raise AssertionError(
                    f'{failure}\n'
                    f'{_client_failure_diagnostics(bridge_log, docroot)}'
                ) from failure
            results = {}
            for owner, state in states.items():
                foreign = owners[1] if owner == owners[0] else owners[0]
                results[owner] = {
                    'returncode': state['returncode'],
                    'ownResult': owner in state['stdout'],
                    'foreignResult': foreign in state['stdout'],
                    'stderr': state['stderr'],
                }
            return results
        finally:
            for process in processes.values():
                _drain.kill_and_drain(process)
