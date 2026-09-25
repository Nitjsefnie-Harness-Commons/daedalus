"""The same-id overlap harness, and its clients' diagnostics beside it.

Not a suite itself — run_tests.py only loads `test_*.py`.

The Node VM drives concurrent cookie commands through the shipped background
worker on the shared gate; the Python helpers keep its subprocesses
observable when an overlap stalls. The client-process half — the scripted
result server, the real same-id client overlap and the evidence they report —
lives in `_overlap_clients`, which imports this module's driver.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain  # noqa: E402
import _util  # noqa: E402
# The kill-release floor stays reachable at the spelling the suite pins.
# pylint: disable-next=unused-import
from _clientstate import _KILLED_CLIENT_PIPE_RELEASE_S  # noqa: E402,F401
from _stream_fake import STRICT_FETCH, assert_gate_clean  # noqa: E402
from _worker_sources import STREAM_RESPONSE  # noqa: E402

_STEP_LINE = re.compile(r'^\[step\] (.+)$', re.MULTILINE)

# The bridge origin a child is handed when no real result server is
# configured. The gate refuses a request whose origin it does not permit, and
# the old fake's relative `test-bridge` server URL has no origin at all, so
# the synthetic path needs a real one for the gate to accept it.
SYNTHETIC_BRIDGE = 'https://synthetic-bridge.example.com'
SYNC = 'POST /sync-tabs'
RESULT = 'POST /result'


_BACKGROUND_OVERLAP_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const nodeCrypto = require('crypto');

const [backgroundPath, commandsText, orderText, resultBase, token,
  waitBetweenText, innerWaitText] = process.argv.slice(1, 8);
const commands = JSON.parse(commandsText);
const completionOrder = JSON.parse(orderText);
const waitBetween = waitBetweenText === '1';
const innerWaitMs = Number(innerWaitText);
const bridgeUrl = resultBase || 'SYNTHETIC_BRIDGE';
const pendingCookies = new Map();
const postAttempts = [];
const forwardedBodies = new Map();
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

// The shared gate's in-scope contract. The gate answers only what the
// scenario declared and records every request it sees; `workerFetch` below
// hands the gate a bridge-relative or absolute URL.
const BRIDGE_URL = bridgeUrl;
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
// The plan rides last on the command line (the driver appends it as JSON
// text), so read it here and parse only when it arrived as text.
const gatePlanArg = process.argv[process.argv.length - 1];
const plan = typeof gatePlanArg === 'string'
  ? JSON.parse(gatePlanArg) : gatePlanArg;
""" + STREAM_RESPONSE + r"""
""" + STRICT_FETCH + r"""

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

// A declared forward is answered by the real server, not by the gate: the
// attempt goes out through the real `fetch` and the server's own answer is
// what the worker sees. The record's status is the real one, stamped only
// once the answer is in. The attempt's own attribution (which owner, which
// id) is the wrapper's, because the gate sees a fetch, not the owner behind
// it.
async function forwardRequest(target, init, entry) {
  const result = await nativeFetch(target, init);
  const body = await result.text();
  forwardedBodies.set(result, body);
  entry.status = result.status;
  return result;
}

// The worker's server URL is what `config.serverUrl` holds, and a child with
// no real result server is handed a real https origin for it. A URL the gate
// derives from config is already absolute; the temporary workers in the
// suites that reuse this harness post to `test-bridge/result`, the old fake's
// placeholder server with no origin, so a relative target is resolved
// against the bridge origin the gate permits, with the placeholder segment
// dropped — the request is the same `/result` route the worker would make
// against a real bridge, and the gate must see it as that route.
// Every result POST is recorded as an attempt whichever path answered it —
// the gate's own status, forwarded real status, or a 599 refusal — because
// the harness's completion wait decides "the work for this owner is over" on
// a recorded 2xx, and a refusal must read as a failure, not as silence.
async function workerFetch(target, init = {}) {
  const raw = String(target);
  const url = /^https?:\/\//.test(raw)
    ? raw : BRIDGE_URL + '/' + raw.replace(/^test-bridge\//, '');
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    const answer = await bridgeFetch(url, init);
    const record = attemptRecord(
      payload, answer, forwardedBodies.get(answer) || '');
    if (!record.ok) {
      process.stderr.write('[post-failure] owner=' + record.owner
        + ' id=' + record.id + ' _did=' + record.deliveryId
        + ' status ' + record.status + ' body ' + record.body + '\n');
    }
    postAttempts.push(record);
    return answer;
  }
  return bridgeFetch(url, init);
}

function eventTarget() {
  return { addListener() {} };
}

function vmSetTimeout(callback, delay) {
  const timer = globalThis.setTimeout(
    callback, Math.min(Number(delay) || 0, 10));
  timer.unref();
  return timer;
}

function vmClearTimeout(timer) {
  globalThis.clearTimeout(timer);
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
  fetch: workerFetch,
  crypto: { randomUUID: nodeCrypto.randomUUID },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout: vmSetTimeout,
  clearTimeout: vmClearTimeout,
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

// The serviced bound mirrors tests/_dashnode.py's crediting; the preludes
// stay independent, so the mirroring is kept by hand.
const OVERLAP_SAMPLE_MS = 100;

// One timebase for both bounds. The cap keeps a starved child from being
// charged the wall time it never ran in.
const clock = {
  ms: 0,
  sampledAt: Date.now(),
  lastCreditMs: 0,
  now() {
    const wall = Date.now();
    const credit = Math.min(wall - this.sampledAt, 2 * OVERLAP_SAMPLE_MS);
    this.sampledAt = wall;
    this.lastCreditMs = credit;
    this.ms += credit;
    return this.ms;
  },
};

function bounded(work, label, timeoutMs) {
  const start = clock.now();
  let sampler;
  let samples = 0;
  let servicedMs = 0;
  let maxCreditMs = 0;
  const guard = new Promise((_resolve, reject) => {
    const sample = () => {
      samples += 1;
      servicedMs = clock.now() - start;
      if (clock.lastCreditMs > maxCreditMs) maxCreditMs = clock.lastCreditMs;
      if (servicedMs >= timeoutMs) {
        reject(new Error('timed out waiting for ' + label));
        return;
      }
      sampler = setTimeout(sample, OVERLAP_SAMPLE_MS);
    };
    sampler = setTimeout(sample, OVERLAP_SAMPLE_MS);
  });
  // What the crediting did, on its own stderr prefix: the numbers a test
  // asserts on cannot be recovered from a wall clock outside the child.
  return Promise.race([Promise.resolve(work), guard]).finally(() => {
    clearTimeout(sampler);
    process.stderr.write('[bound] ' + JSON.stringify(
      { label, samples, servicedMs, maxCreditMs }) + '\n');
  });
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
  const deadline = clock.now() + timeoutMs;
  for (;;) {
    const left = deadline - clock.now();
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
        'id=' + item.id + ' _did=' + item.deliveryId
        + ' status ' + item.status + ' body ' + item.body).join('; ')
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
  process.stdout.write(JSON.stringify({
    posted: postAttempts.filter(
      (item) => item.ok).map((item) => ({
        id: item.id,
        owner: item.owner,
        deliveryId: item.deliveryId,
      })),
    // The gate's own record, so the Python side compares the whole recorded
    // list against the plan this run declared. A run that fails before here
    // (a stall, a missing completion) has no record, and the harness's own
    // failure is the report.
    gate: {
      records: nonStreamFetches,
      refused: refusedFetches,
      badOrigins,
      streamAnswered: streamFetches.map((f) => f.answered),
      contractFaults: gateContractFaults,
    },
  }));
  step('the overlap harness finished');
})().catch((error) => {
  const text = (error.stack || String(error)) + '\n';
  process.stderr.write(text, () => process.exit(1));
});
"""


_OVERLAP_INNER_WAIT_S = 15

# The recording this harness's scenarios make today, on the pre-change tree,
# measured with a temporary recorder (no tracked file changed): the shipped
# background's boot opens the stream once, syncs the tab list once itself and
# once on the stream's connect, then each dispatched command posts one
# result. A child with a real result base forwards that one result route to
# the real server; a child without one gets the gate's ordinary 200. The
# stream is answered `hang` — a connected 200 whose body never yields — so the
# boot fetch is one fetch, the count is the same on every run, and the stream
# loop parks instead of reconnecting on a wall clock. The temporary workers the
# suites splice in place of the background (a `loadConfig` that resolves, a
# `dispatchCommand` that posts) make no stream or sync call at all, so a
# caller running one of those declares `boot=False`.
BOOT = [SYNC, SYNC]


def overlap_plan(commands, result_base='', boot=True, results=None):
    """The gate plan for one overlap run, from the recording above.

    `commands` is the command list the harness dispatches; one result POST per
    command is what the worker makes. `results` overrides that count for a
    worker that retries its POST (the shipped worker retries a 5xx three
    times, so a scenario that answers the first attempt 5xx declares three).
    `result_base` names a real local server, so `POST /result` is declared as
    a forward: recorded and debited like any other request, but answered by
    the real server rather than the gate. The stream fetch is declared as the
    stream answer queue, not as a route: the gate keeps stream fetches out of
    the non-stream record.
    """
    plan = {
        'planned': (BOOT if boot else []) + [RESULT] * (
            len(commands) if results is None else results),
        'statuses': ['hang'] if boot else [],
    }
    if result_base:
        plan['hosts'] = [result_base]
        plan['forwards'] = {RESULT: result_base}
    else:
        plan['hosts'] = [SYNTHETIC_BRIDGE]
    return plan


def overlap_child_timeout(order, wait_between,
                          inner_wait=_OVERLAP_INNER_WAIT_S, outer_slack=0):
    """How long to let the overlap harness run before killing it.

    Every wait names what it was waiting for and has its own bound except the
    result POST wait. That round-trip is incidental, so only this backstop
    bounds it. The backstop preserves the child's pipes and last step, and it
    still has to outlast the bounded stages — config load, handler startup,
    each requested gap, and dispatch settlement — with one inner interval of
    slack per result. Those inner failures report first. A genuinely stuck
    result POST instead reaches the backstop, making its diagnosis take the
    outer bound rather than an inner one.

    The inner bounds expire on serviced event-loop time rather than the wall
    clock, so a child starved of the CPU earns them more slowly than the wall
    clock measured here; starvation deep enough to outlast every inner bound
    is reported by this backstop instead, which preserves the child's pipes
    and last step.

    Outer slack is added once, on top of those allowances, so a caller whose
    inner bounds were shrunk can keep the backstop it had without paying the
    old inner waits again.
    """
    waits = 3 + len(order) + (len(order) - 1 if wait_between else 0)
    return inner_wait * (waits + 1) + outer_slack


def run_background_overlap(background, commands, order, result_base='',
                           token='overlap-token', wait_between=False,
                           inner_wait=_OVERLAP_INNER_WAIT_S, outer_slack=0,
                           boot=True, results=None):
    """Run same-id cookie commands through the shipped background worker.

    Every run is driven on the shared gate: the plan is this run's recording
    (`overlap_plan`), the child appends it to its own command line, and the
    record the gate kept is checked with `assert_gate_clean` before the
    posted results are handed back, so a request outside the plan — an
    invented route, a result POST past the one per command, a foreign origin
    — is refused, recorded, and fails the caller.
    """
    # Fabricated suite-runner trees copy _util.py without this helper.
    from _worker_sources import import_scripts_stub

    node = shutil.which('node')
    if not node:
        raise AssertionError(
            'node is required to execute the extension worker')
    harness = _BACKGROUND_OVERLAP_HARNESS.replace(
        '__IMPORT_SCRIPTS_STUB__', import_scripts_stub('context')).replace(
            'SYNTHETIC_BRIDGE', SYNTHETIC_BRIDGE)
    plan = overlap_plan(commands, result_base, boot, results)
    attempts = 2
    records = []
    for attempt in range(1, attempts + 1):
        timeout = overlap_child_timeout(
            order, wait_between, inner_wait * attempt, outer_slack)
        process = subprocess.Popen(
            [node, '-e', harness, str(background),
             json.dumps(commands), json.dumps(order), result_base, token,
             '1' if wait_between else '0',
             str(round(inner_wait * attempt * 1000)), json.dumps(plan)],
            cwd=_util.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as failure:
            drain_timed_out, out, err = _drain.kill_and_drain(process)
            stdout, stderr = _drain_text(out), _drain_text(err)
            steps = _STEP_LINE.findall(stderr)
            last_step = steps[-1] if steps else 'none recorded'
            record = (f'attempt {attempt} (pid {process.pid}): last step: '
                      f'{last_step}; stdout: {stdout!r}; stderr: {stderr!r}; '
                      f'drain timed out: {"yes" if drain_timed_out else "no"}')
            retrying = attempt < attempts
            if retrying and not stdout and not stderr and not drain_timed_out:
                records.append(record)
                continue
            note = ('\nretry declined: the post-kill drain did not '
                    'complete (drain outcome: timed out)'
                    if drain_timed_out and retrying else '')
            if not retrying:
                records.append(record)
            prior = ''.join(f'\n{item}' for item in records)
            raise AssertionError(
                f'overlap harness outer backstop timed out after {timeout}s; '
                f'last step: {last_step}; stdout: {stdout!r}; '
                f'stderr: {stderr!r}{note}{prior}'
            ) from failure
        break
    if process.returncode != 0:
        raise AssertionError((process.returncode, stdout, stderr))
    if records:
        sys.stderr.write(
            f'overlap harness recovered after outer timeout: {records[0]}\n')
    outcome = json.loads(stdout)
    gate = outcome['gate']
    assert_gate_clean(
        contract_faults=gate['contractFaults'],
        records=gate['records'], refused=gate['refused'],
        bad_origins=gate['badOrigins'],
        stream_answered=gate['streamAnswered'],
        planned=list(plan['planned']), planned_stream=list(plan['statuses']))
    return outcome['posted']


def _assert_step_trace(failure, labels):
    marker = '[step] '
    trace_start = failure.find(marker)
    trace_text = failure[trace_start:] if trace_start >= 0 else ''
    trace_text = trace_text.replace('\\n', '\n')
    actual = _STEP_LINE.findall(trace_text)
    position = 0
    for expected in labels:
        try:
            position = actual.index(expected, position) + 1
        except ValueError as mismatch:
            reason = 'out of order' if expected in actual else 'missing'
            raise AssertionError(
                f'expected step {expected!r} was {reason}; '
                f'actual step labels: {actual}'
            ) from mismatch


def _harness_failure(background, inner_wait=1, commands=None, order=None,
                     result_base='', wait_between=False, outer_slack=0,
                     boot=True, results=None):
    commands = commands or [{'id': '_cookies', 'domain': 'owner-a'}]
    order = order or ['owner-a']
    try:
        run_background_overlap(
            background, commands, order, result_base=result_base,
            wait_between=wait_between, inner_wait=inner_wait,
            outer_slack=outer_slack, boot=boot, results=results)
    except AssertionError as failure:
        return str(failure)
    except subprocess.TimeoutExpired as failure:
        raise AssertionError(
            f'bare TimeoutExpired after {failure.timeout}s') from failure
    raise AssertionError('the stalled overlap harness unexpectedly succeeded')


def _drain_text(value):
    """A drained pipe's bytes or None, as the str the messages below embed.

    A timed-out drain hands back the still-unread bytes, or None for a pipe
    with nothing unread; these strings are rendered into failure messages the
    step-trace reader recovers labels from, so the trailing newline of a
    captured stream is kept rather than stripped.
    """
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return value
