"""The same-id overlap harness and its client-process diagnostics.

Not a suite itself — run_tests.py only loads `test_*.py`.

The Node VM drives concurrent cookie commands through the shipped background
worker, while the Python helpers keep its subprocesses observable when an
overlap stalls.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cmdqueue  # noqa: E402
import _util  # noqa: E402


_BACKGROUND_OVERLAP_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const nodeCrypto = require('crypto');

const [backgroundPath, commandsText, orderText, resultBase, token,
  waitBetweenText, innerWaitText] = process.argv.slice(1);
const commands = JSON.parse(commandsText);
const completionOrder = JSON.parse(orderText);
const waitBetween = waitBetweenText === '1';
const innerWaitMs = Number(innerWaitText);
const bridgeUrl = resultBase || 'test-bridge';
const pendingCookies = new Map();
const postAttempts = [];
const settledDispatches = new Set();
const nativeFetch = globalThis.fetch;

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function attemptRecord(payload, result, body) {
  return {
    id: payload.id,
    owner: payload.result[0].value,
    deliveryId: payload._did || null,
    ok: result.ok,
    status: result.status,
    // Clipped so a refusal of any size stays one readable diagnostic line.
    body: body.length > 200 ? body.slice(0, 200) + '...' : body,
  };
}

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    if (resultBase) {
      const result = await nativeFetch(target, init);
      const body = await result.text();
      const record = attemptRecord(payload, result, body);
      postAttempts.push(record);
      if (!record.ok) {
        process.stderr.write('[post-failure] owner=' + record.owner
          + ' status ' + record.status + ' body ' + record.body + '\n');
      }
      return result;
    }
    postAttempts.push(attemptRecord(payload, response(200, { ok: true }), ''));
    return response(200, { ok: true });
  }
  if (url.includes('/stream?')) return response(503, { error: 'disabled' });
  return response(200, { ok: true });
}

function eventTarget() {
  return { addListener() {} };
}

const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': token,
        'daedalus-server': bridgeUrl,
      }),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query(_query, callback) {
      if (callback) {
        callback([]);
        return undefined;
      }
      return Promise.resolve([]);
    },
  },
  cookies: {
    getAll(details) {
      return new Promise((resolve) => {
        pendingCookies.set(details.domain, () => resolve([{
          domain: details.domain,
          name: 'owner',
          value: details.domain,
        }]));
      });
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
  },
  runtime: {
    onMessage: eventTarget(),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
};

const context = vm.createContext({
  chrome,
  fetch: bridgeFetch,
  crypto: { randomUUID: nodeCrypto.randomUUID },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
__IMPORT_SCRIPTS_STUB__

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function step(label) {
  process.stderr.write('[step] ' + label + '\n');
}

function bounded(work, label, timeoutMs) {
  let timer;
  const guard = new Promise((_resolve, reject) => {
    timer = setTimeout(
      () => reject(new Error('timed out waiting for ' + label)), timeoutMs);
  });
  return Promise.race([Promise.resolve(work), guard])
    .finally(() => clearTimeout(timer));
}

async function waitFor(predicate, label, timeoutMs = innerWaitMs) {
  step(label);
  // null disables this deadline; the caller's backstop bounds the wait.
  if (timeoutMs === null) {
    for (;;) {
      if (await predicate()) return;
      await delay(10);
    }
  }
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const left = deadline - Date.now();
    if (left <= 0) throw new Error('timed out waiting for ' + label);
    if (await bounded(predicate(), label, left)) return;
    await delay(10);
  }
}

async function waitForResultConsume() {
  const query = resultBase + '/result?token=' + encodeURIComponent(token)
    + '&tab=extension';
  await waitFor(async () => {
    const result = await nativeFetch(query);
    const body = await result.json();
    return body.pending === true;
  }, 'the first result to be consumed');
}

// An owner's dispatch settles when the worker stops trying to post, so only
// then is a run with no recorded 2xx a failure rather than a retry in flight.
function ownerPosted(owner) {
  const mine = postAttempts.filter((item) => item.owner === owner);
  if (mine.some((item) => item.ok)) return true;
  if (!settledDispatches.has(owner)) return false;
  throw new Error('the result POST for ' + owner + ' failed: '
    + (mine.filter((item) => !item.ok).map((item) =>
        'status ' + item.status + ' body ' + item.body).join('; ')
      || 'no POST was recorded'));
}

(async () => {
  step('the worker script to initialize');
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  const configLabel = 'the worker to load its config';
  step(configLabel);
  await bounded(
    vm.runInContext('loadConfig()', context), configLabel, innerWaitMs);
  context.commands = commands;
  step('the dispatchCommand calls to start');
  const executions = commands.map((_command, index) =>
    // vm-load-exempt: dispatches a queued command by index, not a file
    vm.runInContext('dispatchCommand(commands[' + index + '])', context));
  // A settled dispatch is the only signal that an owner's post sequence is
  // over; the harness records it beside the attempts it can then judge.
  commands.forEach((command, index) => executions[index].then(
    () => settledDispatches.add(command.domain),
    () => settledDispatches.add(command.domain)));
  await waitFor(
    () => pendingCookies.size === commands.length,
    'all cookie handlers to start');

  for (let index = 0; index < completionOrder.length; index++) {
    const owner = completionOrder[index];
    const complete = pendingCookies.get(owner);
    if (!complete) throw new Error('missing cookie completion for ' + owner);
    complete();
    // The POST round-trip is incidental; only the outer backstop bounds it.
    await waitFor(
      () => ownerPosted(owner), 'result POST for ' + owner, null);
    if (waitBetween && index + 1 < completionOrder.length) {
      await waitForResultConsume();
    }
  }
  const settleLabel = 'all dispatchCommand calls to settle';
  step(settleLabel);
  await bounded(Promise.all(executions), settleLabel, innerWaitMs);
  process.stdout.write(JSON.stringify(postAttempts.filter(
    (item) => item.ok).map((item) => ({
    id: item.id,
    owner: item.owner,
    deliveryId: item.deliveryId,
  }))));
  step('the overlap harness finished');
})().catch((error) => {
  const text = (error.stack || String(error)) + '\n';
  process.stderr.write(text, () => process.exit(1));
});
"""


_OVERLAP_INNER_WAIT_S = 15

# Publication and healthy exits may move together. A killed client's pipes get
# enough time that expiry means a broken drain, not a busy runner; the explicit
# parameter exists only to force that diagnostic branch deterministically.
_CLIENT_COMMAND_WAIT_S = 15
_FAILED_CLIENT_GRACE_S = 1
_KILLED_CLIENT_PIPE_RELEASE_S = 20


def overlap_child_timeout(order, wait_between,
                          inner_wait=_OVERLAP_INNER_WAIT_S):
    """How long to let the overlap harness run before killing it.

    Every wait names what it was waiting for and has its own bound except the
    result POST wait. That round-trip is incidental, so only this backstop
    bounds it. The backstop preserves the child's pipes and last step, and it
    still has to outlast the bounded stages — config load, handler startup,
    each requested gap, and dispatch settlement — with one inner interval of
    slack per result. Those inner failures report first. A genuinely stuck
    result POST instead reaches the backstop, making its diagnosis take the
    outer bound rather than an inner one.
    """
    waits = 3 + len(order) + (len(order) - 1 if wait_between else 0)
    return inner_wait * (waits + 1)


def run_background_overlap(background, commands, order, result_base='',
                           token='overlap-token', wait_between=False,
                           inner_wait=_OVERLAP_INNER_WAIT_S):
    """Run same-id cookie commands through the shipped background worker."""
    # Fabricated suite-runner trees copy _util.py without this helper.
    from _worker_sources import import_scripts_stub

    node = shutil.which('node')
    if not node:
        raise AssertionError(
            'node is required to execute the extension worker')
    timeout = overlap_child_timeout(order, wait_between, inner_wait)
    harness = _BACKGROUND_OVERLAP_HARNESS.replace(
        '__IMPORT_SCRIPTS_STUB__', import_scripts_stub('context'))
    process = subprocess.Popen(
        [node, '-e', harness, str(background),
         json.dumps(commands), json.dumps(order), result_base, token,
         '1' if wait_between else '0', str(round(inner_wait * 1000))],
        cwd=_util.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as failure:
        process.kill()
        stdout, stderr = process.communicate()
        steps = re.findall(r'^\[step\] (.+)$', stderr, re.MULTILINE)
        last_step = steps[-1] if steps else 'none recorded'
        raise AssertionError(
            f'overlap harness outer backstop timed out after {timeout}s; '
            f'last step: {last_step}; stdout: {stdout!r}; stderr: {stderr!r}'
        ) from failure
    if process.returncode != 0:
        raise AssertionError((process.returncode, stdout, stderr))
    return json.loads(stdout)


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
    """Raise at most one diagnostic assertion: the first failing kind.

    A client that outlived its grace and one that exited non-zero having
    written nothing are different failures: diagnosing the second as the first
    sends the reader looking for a stall that is not there.
    """
    running = [owner for owner, state in states.items()
               if state['stillRunning']]
    if running:
        raise AssertionError(
            f'clients still running after grace: {running}; '
            f'harness posted: {posted}; client states: {states}')
    silent = [owner for owner, state in states.items()
              if state['returncode'] and not state['stdout']
              and not state['stderr']]
    if silent:
        raise AssertionError(
            f'clients exited non-zero with no output: {silent}; '
            f'harness posted: {posted}; client states: {states}')


def _wait_for_client_commands(queue, count):
    commands = _cmdqueue.wait_for_commands(
        queue, count, _CLIENT_COMMAND_WAIT_S)
    if commands is None:
        raise AssertionError(
            'timed out waiting for both same-id client commands')
    return commands


def client_env():
    """A client environment, minus any bridge coordinates this process has."""
    env = dict(os.environ)
    for key in ('DAEDALUS_URL', 'DAEDALUS_TOKEN', 'TOKEN', 'ID'):
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
    """The bridge log tail and the retained deliveries, for one diagnosis."""
    tail = ''.join(bridge_log[-40:]).strip() or 'no bridge log captured'
    root = Path(docroot) / 'results' / 'deliveries'
    lines = []
    for path in sorted(root.rglob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        lines.append(
            f"{path.relative_to(root)}: deliveryId {record['deliveryId']}")
    delivery = '\n'.join(lines) or 'no delivery retained'
    return f'bridge log tail:\n{tail}\ndelivery state:\n{delivery}'


def run_same_id_client_overlap(tmp, completion_order, client_argv, env,
                               token, background):
    """Drive real same-id CLI clients and preserve both failure surfaces."""
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
            try:
                posted = run_background_overlap(
                    background, commands, completion_order,
                    result_base=base, token=token, wait_between=False)
            except AssertionError as failure:
                raise AssertionError(
                    f'{failure}; clients: '
                    f'{client_states(processes, grace=_FAILED_CLIENT_GRACE_S)}'
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
                if process.poll() is None:
                    process.kill()
                    process.communicate()
