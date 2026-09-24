"""The Node VM harness for CDP handle lifecycle.

Not a suite itself — run_tests.py only loads `test_*.py`.

Every CDP evaluation leaves a remote handle on the inspector side, and a
session that is kept for a capture keeps the attachment too — so the release
has to happen on the compile, throw, reject and pending paths alike. This
harness makes each of those observable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import EXTENSION_ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_inline_gate)
from _util import child_coverage  # noqa: E402
from _worker_sources import (  # noqa: E402
    chrome_stub, import_scripts_stub)

# The coverage guard cannot prove EXTENSION_ROOT is the checkout root
# through the inline driver, so this call site declares its child env.
_ENV = child_coverage('scrub')

SYNC = 'POST /sync-tabs'
RESULT = 'POST /result'

# The recording on this tree, not a wish. A `hang` stream is a connected
# body that never settles, so the worker's stream loop retries and each
# connect re-syncs: two `POST /sync-tabs`, not the one the old
# never-settling fetch hid. The three results are the compile, throw and
# reject evals. The stream itself is fetched once, answered `hang`.
_PLAN = {
    'planned': [SYNC, SYNC, RESULT, RESULT, RESULT],
    'statuses': ['hang'],
}


_CDP_HANDLE_LIFECYCLE_HARNESS = (
    r"""
const fs = require('fs');
const vm = require('vm');

const backgroundPath = process.argv[1];
// The plan rides last on the command line; the inline driver appends it as
// JSON text, so read it here and parse only when it arrived as text.
const gatePlanArg = process.argv[process.argv.length - 1];
const plan = typeof gatePlanArg === 'string'
  ? JSON.parse(gatePlanArg) : gatePlanArg;
const released = [];
const resultWorlds = [];
const submittedTransports = { eval: [], hotfix: [] };
const timers = [];
let pendingResolve;
let activeRoute = 'eval';

// The inspector commands this scenario drives; the shared release
// bookkeeping lives in the chrome stub. This handles the rest.
async function sendCommand(_target, method, params) {
  if (method === 'Runtime.evaluate') {
    if (params.expression.startsWith('typeof (function')) {
      return {
        result: { objectId: 'compile-result' },
        exceptionDetails: {
          text: 'compile failed',
          exception: {
            objectId: 'compile-exception',
            description: 'compile failed',
          },
        },
      };
    }
    submittedTransports[activeRoute].push({
      replModeEnabled: params.replMode === true,
      awaitPromiseEnabled: params.awaitPromise === true,
      returnByValueEnabled: params.returnByValue === true,
    });
    if (params.expression.includes('throw-case')) {
      return {
        result: { objectId: 'throw-result' },
        exceptionDetails: {
          text: 'throw failed',
          exception: {
            objectId: 'throw-exception',
            description: 'throw failed',
          },
        },
      };
    }
    if (params.expression.includes('reject-case')) {
      return {
        result: {
          objectId: 'reject-original',
          subtype: 'promise',
        },
      };
    }
    return { result: { value: 1 } };
  }
  if (method === 'Runtime.awaitPromise') {
    if (params.promiseObjectId === 'reject-original') {
      return {
        result: { objectId: 'reject-result' },
        exceptionDetails: {
          text: 'promise rejected',
          exception: {
            objectId: 'reject-exception',
            description: 'promise rejected',
          },
        },
      };
    }
    if (params.promiseObjectId === 'pending-original') {
      return new Promise((resolve) => { pendingResolve = resolve; });
    }
  }
  if (method === 'Runtime.callFunctionOn') {
    return { result: { value: 'settled' } };
  }
  return {};
}

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function eventTarget() {
  return { addListener() {} };
}

const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];

function streamResponse(answer) {
  if (answer === 'hang') {
    return {
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: () => new Promise(() => {}),
          cancel: () => Promise.resolve(),
        }),
      },
    };
  }
  return response(answer, { error: 'disabled' });
}
""" + STRICT_FETCH + r"""

""" + chrome_stub("'lifecycle-token'", 'BRIDGE_URL', 'sendCommand') + r"""
const context = vm.createContext({
  chrome,
  // A thin wrapper that DELEGATES to the gate and then does this harness's
  // own attribution. Every request — the answer, the accounting, the
  // refusal, the record — is the gate's; `resultWorlds` is the harness's own
  // note of which dispatched command produced which result, which the gate
  // cannot know because it sees a fetch, not the command behind it. The
  // wrapper decides no status and counts nothing.
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.endsWith('/result') && init && init.method === 'POST') {
      resultWorlds.push(JSON.parse(init.body).world);
    }
    return bridgeFetch(target, init);
  },
  crypto: { randomUUID: () => 'lifecycle-id' },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout(callback, ms) {
    const timer = { callback, ms, active: true };
    timers.push(timer);
    return timers.length;
  },
  clearTimeout(id) {
    if (timers[id - 1]) timers[id - 1].active = false;
  },
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
""") + import_scripts_stub('context') + r"""

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function runEval(id, code) {
  context.command = { id, code, tabId: '7', _did: id };
  await vm.runInContext(
    '_evalViaCdp({...command, _execution: _executionContext(command)}, 7)',
    context);
}

(async () => {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await delay();
  vm.runInContext('_cdpSessions[7] = true', context);

  await runEval('compile', 'return compile-case');
  await runEval('throw', 'throw-case');
  await runEval('reject', 'reject-case');
  activeRoute = 'hotfix';
  await vm.runInContext("_replayViaCdp(7, 'hotfix-case')", context);
  activeRoute = 'eval';

  context.pendingRemote = {
    objectId: 'pending-original',
    subtype: 'promise',
  };
  // The settlement guard is bounded by serviced time, so the clock only
  // moves when the sampler is fired: one fire credits one interval.
  let clockMs = 0;
  context._cdpNow = () => { clockMs += 100; return clockMs; };
  const pending = vm.runInContext('_cdpSettle(7, pendingRemote)', context);
  await delay();
  const timer = timers.find((item) => item.active && item.ms === 100);
  const pendingHasTimeout = Boolean(timer);
  if (timer) {
    let rejected = false;
    pending.catch(() => { rejected = true; });
    for (let fired = 0; fired < 300 && !rejected; fired++) {
      const sampler = timers.find((item) => item.active && item.ms === 100);
      if (!sampler) break;
      sampler.active = false;
      sampler.callback();
      await delay();
    }
    try { await pending; } catch (_) {}
    await delay();
    await delay();
  }

  process.stdout.write(JSON.stringify({
    released: [...new Set(released)].sort(),
    evalTransports: submittedTransports.eval,
    hotfixTransports: submittedTransports.hotfix,
    pendingHasTimeout,
    // Every race's sampler must be torn down once the race resolves, on
    // the settle path and the reject path alike; a sampler left armed
    // keeps rescheduling and delays the service worker's suspend.
    armedSamplers: timers.filter((item) => item.active && item.ms === 100)
      .length,
    resultWorlds,
    // The gate's own record, so the Python side compares the whole recorded
    // list against the plan this scenario declared.
    gate: {
      records: nonStreamFetches,
      refused: refusedFetches,
      badOrigins,
      streamAnswered: streamFetches.map((f) => f.answered),
      contractFaults: gateContractFaults,
    },
  }));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def run_cdp_handle_lifecycle():
    """Drive the CDP lifecycle child under one plan and read its answer.

    The stream is answered `hang` — a connected body that never settles —
    which is the faithful model of the session this scenario holds open. The
    plan declares the sync count that answer actually produces, measured on
    this tree, not a wish.
    """
    plan = _PLAN
    outcome = run_inline_gate(
        require_node(), _CDP_HANDLE_LIFECYCLE_HARNESS,
        [str(EXTENSION_ROOT / 'background.js')],
        cwd=EXTENSION_ROOT, plan=plan, env=_ENV)
    gate = outcome.pop('gate')
    assert_gate_clean(
        contract_faults=gate['contractFaults'],
        records=gate['records'], refused=gate['refused'],
        bad_origins=gate['badOrigins'],
        stream_answered=gate['streamAnswered'],
        planned=list(plan['planned']), planned_stream=list(plan['statuses']))
    return outcome
