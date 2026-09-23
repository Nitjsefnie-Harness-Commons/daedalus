"""What each same-id client was doing when the overlap harness gave up.

The overlap harness reports only its own timeout, so these helpers read the
client processes at the moment it matters: a client still running past its
grace is killed and its held pipes given a bounded release, and any failed
client raises at most one diagnostic assertion naming every owner involved.
"""
import subprocess


# A killed client's inherited pipes can be held by a grandchild; the bound
# leaves room for a slow release without letting a broken drain stall the
# diagnosis. It stands alone so tuning another timeout cannot weaken it.
_KILLED_CLIENT_PIPE_RELEASE_S = 20


def _output_text(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace').strip()
    return value.strip()


def client_states(processes, grace,
                  killed_pipe_release=_KILLED_CLIENT_PIPE_RELEASE_S):
    """What each same-id client was doing when the harness gave up.

    The harness reports only its own timeout, and the `finally` below kills
    both clients and discards what they said — so a run where a client left
    before its result arrived is indistinguishable from one where the result
    never came. This is the difference, read at the moment it matters.

    Each client gets `grace` seconds to exit normally, or waits unboundedly
    when `grace` is `None`, which is what a caller whose client self-bounds
    passes. A client still running at expiry is killed and gets
    `killed_pipe_release` seconds for inherited pipes to close; even a second
    drain timeout is recorded in that client's state instead of escaping and
    hiding every diagnostic collected.

    A client the helper killed records no `returncode`. The status read after
    that kill is the kill's own — Windows `Popen.kill()` is
    `TerminateProcess(handle, 1)`, POSIX's raises SIGKILL and records `-9` —
    so reporting it as the client's outcome is how one process came to be
    described as both still running and exited non-zero.
    """
    states = {}
    for owner, proc in processes.items():
        killed = False
        drain_timed_out = False
        try:
            out, err = proc.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            killed = True
            proc.kill()
            try:
                out, err = proc.communicate(timeout=killed_pipe_release)
            except subprocess.TimeoutExpired as failure:
                drain_timed_out = True
                out, err = failure.stdout, failure.stderr
                if proc.stdout is not None:
                    proc.stdout.close()
                if proc.stderr is not None:
                    proc.stderr.close()
                try:
                    proc.wait(timeout=killed_pipe_release)
                except subprocess.TimeoutExpired:
                    # Preserve the recorded drain failure instead of replacing
                    # it with another exception from this diagnostic helper.
                    pass
        states[owner] = {
            'stillRunning': killed,
            'returncode': None if killed else proc.returncode,
            'stdout': _output_text(out),
            'stderr': _output_text(err),
            'drainTimedOut': drain_timed_out,
        }
    return states


def assert_clients_exited(states, posted):
    """Raise at most one diagnostic assertion for any failed client.

    A client that outlived its grace and one that exited non-zero having
    written output are different failures, but both are not clean exits.
    """
    running = [owner for owner, state in states.items()
               if state['stillRunning']]
    if running:
        raise AssertionError(
            f'clients still running after grace: {running}; '
            f'harness posted: {posted}; client states: {states}')
    nonzero = [owner for owner, state in states.items()
               if state['returncode'] not in (None, 0)]
    if nonzero:
        silent = [owner for owner in nonzero
                  if not states[owner]['stdout']
                  and not states[owner]['stderr']]
        if silent and len(silent) == len(nonzero):
            label = 'clients exited non-zero with no output'
            owners = silent
        else:
            label = 'clients exited non-zero'
            owners = nonzero
        raise AssertionError(
            f'{label}: {owners}; '
            f'harness posted: {posted}; client states: {states}')
