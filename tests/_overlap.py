"""The same-id overlap harness and its client-process diagnostics.

Not a suite itself — run_tests.py only loads `test_*.py`.

The Node VM drives concurrent cookie commands through the shipped background
worker, while the Python helpers keep its subprocesses observable when an
overlap stalls.
"""
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
const postedResults = [];
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

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    if (resultBase) {
      const result = await nativeFetch(target, init);
      postedResults.push(payload);
      return result;
    }
    postedResults.push(payload);
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

(async () => {
  step('the worker script to initialize');
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context);
  const configLabel = 'the worker to load its config';
  step(configLabel);
  await bounded(
    vm.runInContext('loadConfig()', context), configLabel, innerWaitMs);
  context.commands = commands;
  step('the dispatchCommand calls to start');
  const executions = commands.map((_command, index) =>
    vm.runInContext('dispatchCommand(commands[' + index + '])', context));
  await waitFor(
    () => pendingCookies.size === commands.length,
    'all cookie handlers to start');

  for (let index = 0; index < completionOrder.length; index++) {
    const owner = completionOrder[index];
    const complete = pendingCookies.get(owner);
    if (!complete) throw new Error('missing cookie completion for ' + owner);
    const postedBefore = postedResults.length;
    complete();
    await waitFor(
      () => postedResults.length === postedBefore + 1,
      'result POST for ' + owner);
    if (waitBetween && index + 1 < completionOrder.length) {
      await waitForResultConsume();
    }
  }
  const settleLabel = 'all dispatchCommand calls to settle';
  step(settleLabel);
  await bounded(Promise.all(executions), settleLabel, innerWaitMs);
  process.stdout.write(JSON.stringify(postedResults.map((item) => ({
    id: item.id,
    owner: item.result[0].value,
    deliveryId: item._did || null,
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
_SUCCESSFUL_CLIENT_GRACE_S = 20
_FAILED_CLIENT_GRACE_S = 1
_KILLED_CLIENT_PIPE_RELEASE_S = 20


def overlap_child_timeout(order, wait_between,
                          inner_wait=_OVERLAP_INNER_WAIT_S):
    """How long to let the overlap harness run before killing it.

    Every wait inside the harness is bounded and names what it was waiting
    for; this backstop preserves the child's pipes and last step, but it still
    has to outlast the worst inner path — config load, handler startup, one
    wait per result, one per requested gap, and dispatch settlement — so the
    more specific inner failure gets to report first.
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

    Each client gets `grace` seconds to exit normally. A client still running
    is killed and gets `killed_pipe_release` seconds for inherited pipes to
    close; even a second drain timeout is recorded in that client's state
    instead of escaping and hiding every diagnostic collected.
    """
    states = {}
    for owner, proc in processes.items():
        still_running = False
        drain_timed_out = False
        try:
            out, err = proc.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            still_running = True
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
            'stillRunning': still_running,
            'returncode': proc.returncode,
            'stdout': _output_text(out),
            'stderr': _output_text(err),
            'drainTimedOut': drain_timed_out,
        }
    return states


def assert_clients_exited(states, posted):
    """Raise one diagnostic assertion when clients miss their exit grace."""
    running = [owner for owner, state in states.items()
               if state['stillRunning']]
    if running:
        raise AssertionError(
            f'clients still running after grace: {running}; '
            f'harness posted: {posted}; client states: {states}')


def _wait_for_client_commands(queue, count):
    deadline = time.time() + _CLIENT_COMMAND_WAIT_S
    while time.time() < deadline:
        if queue.is_dir() and len(list(queue.glob('*.json'))) == count:
            return
        time.sleep(0.05)
    raise AssertionError('timed out waiting for both same-id client commands')


def run_same_id_client_overlap(tmp, completion_order, client_argv, env,
                               token, background):
    """Drive real same-id CLI clients and preserve both failure surfaces."""
    owners = ('owner-a', 'owner-b')
    bridge_env = {'TOKEN': '', 'DAEDALUS_TOKEN': token}
    with _util.bridge(tmp, env=bridge_env) as (base, docroot):
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
            _wait_for_client_commands(queue, len(owners))
            queued = [json.loads(path.read_text(encoding='utf-8'))
                      for path in sorted(queue.glob('*.json'))]
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
            states = client_states(
                processes, grace=_SUCCESSFUL_CLIENT_GRACE_S)
            assert_clients_exited(states, posted)
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
