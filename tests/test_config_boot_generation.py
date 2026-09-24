#!/usr/bin/env python3
"""Boot's loadConfig runs at most one generation at a time.

Boot and a restart's heartbeat alarm can both reach loadConfig while the
first is parked on its first storage read (config.token is still ''), so on a
fresh install two independent generations would each auto-generate a token and
churn the credential. loadConfig memoizes the generation: a second caller joins
the first one's result instead of starting a second.

The harness holds boot's FIRST config read open, fires the daemon-heartbeat
alarm into that window, and records what the code DID — how many generations
ran (one config get each), how many tokens were minted and written, and which
token the shared config ends up holding. No wall-clock margin: the interleaving
is driven by releasing the held read and draining microtasks, never a timeout.

The assertions are exact counts and exact list contents (with their lengths),
so none of them can pass vacuously over an empty collection, and none of the
observed values pass through the bridge `fetch` fake.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary_env import run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_chrome_fake import INERT_WORKER_APIS  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402


_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

// A generation that fails rejects the memo; boot's `.then` and the alarm's
// `await` have no catch (shipped background.js), so swallow the resulting
// unhandled rejections here instead of letting Node crash the harness.
process.on('unhandledRejection', () => {});

const [backgroundPath, plan] = process.argv.slice(1);

const messageListeners = [];
const alarmListeners = [];
const storageStore = { 'daedalus-server': 'https://bridge.example.com' };
// A hold on boot's FIRST storage read — the config get for
// ['daedalus-token', 'daedalus-server']. Holding it parks boot's generation
// with config.token still '', the window a restart's alarm lands in. One
// loadConfig generation issues exactly one config get, so configGets counts
// generations.
let configHold = null;
let configGets = 0;
let tokenWrites = 0;
let uuidCalls = 0;
const mintedTokens = [];

// Settle boot's held config read with a FRESH-INSTALL result: no
// 'daedalus-token' key, so config.js's `|| ''` leaves config.token empty and
// the `!config.token` branch is reachable.
function openConfig() {
  if (configHold) {
    configHold.resolve({});
    configHold = null;
  }
}

function copy(v) {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
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

function eventTarget(listeners = null) {
  return {
    addListener(l) { if (listeners) listeners.push(l); },
  };
}

async function bridgeFetch(target) {
  const url = String(target);
  // A refusal, not a catch-all success: startStream records a retry and
  // stops. No assertion reads the stream, but nothing is answered 200 here
  // that a plan did not place.
  if (url.includes('/stream?')) return { ok: false, status: 503, body: null };
  if (url.endsWith('/result')) return response(200, { ok: true });
  return response(200, { ok: true });
}

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const wanted = [].concat(keys);
        if (wanted.includes('daedalus-token')) {
          configGets += 1;
          // Hold only the FIRST config get (boot's); a second generation's
          // get resolves immediately, modelling a fast independent read.
          if (plan.holdConfig && !configHold) {
            return new Promise((resolve) => { configHold = { resolve }; });
          }
          if (plan.failConfigFirst && configGets === 1) {
            throw new Error('config read failed');
          }
        }
        const out = {};
        for (const key of wanted) {
          if (key in storageStore
            && !(plan.noToken && key === 'daedalus-token')) {
            out[key] = copy(storageStore[key]);
          }
        }
        return out;
      },
      set: async (entries) => {
        for (const key of Object.keys(entries)) {
          if (key === 'daedalus-token') tokenWrites += 1;
          storageStore[key] = copy(entries[key]);
        }
      },
      remove: async (keys) => {
        for (const key of [].concat(keys)) delete storageStore[key];
      },
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query(_q, cb) {
      if (cb) { cb([]); return undefined; }
      return Promise.resolve([]);
    },
    get: async (tabId) => ({ id: tabId, url: '', title: '' }),
    sendMessage: async () => {},
    create(_d, cb) { cb({ id: 101 }); },
  },
""" + INERT_WORKER_APIS + r"""
};
chrome.alarms.onAlarm = eventTarget(alarmListeners);
chrome.runtime.onConnect = eventTarget();

const context = vm.createContext({
  chrome,
  fetch: bridgeFetch,
  crypto: { randomUUID: () => {
    uuidCalls += 1;
    const minted = 'relay-' + uuidCalls;
    mintedTokens.push(minted);
    return minted;
  } },
  Date: { now: () => 0 },
  AbortController,
  TextDecoder,
  TextEncoder,
  URL,
  performance,
  atob,
  btoa,
  setTimeout: (cb) => { setImmediate(cb); return 0; },
  clearTimeout() {},
  setInterval: () => 0,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
""" + import_scripts_stub('context') + r"""

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function settle() {
  for (let i = 0; i < 25; i++) await delay();
}

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await settle();
  const outcome = {};
  if (plan.scenario === 'boot-generation') {
    // Boot's config read is held open (config.token still ''). Fire the
    // heartbeat alarm into that window: on a worker restart the alarm
    // survived the MV3 kill, so it reaches loadConfig while boot's own
    // generation is still in flight. Let any second generation reach its
    // token branch, then release boot's held read (fresh-install: empty).
    for (const listener of alarmListeners) {
      listener({ name: 'daedalus-heartbeat' });
    }
    await settle();
    openConfig();
    await settle();
    // A later caller, now that the generation has settled, must still join
    // the memo rather than start a fresh generation: the memo outlives the
    // in-flight window. On a fresh install a fresh generation would re-read
    // the empty token and mint a second UUID. Started inside the context and
    // settled, never awaited across the vm boundary (a pending cross-context
    // promise is not driven by a host await).
    vm.runInContext('globalThis.__recalled = loadConfig();', context);
    await settle();
    outcome.configGets = configGets;
    outcome.tokenWrites = tokenWrites;
    outcome.mintedTokens = mintedTokens.slice();
    outcome.finalToken = vm.runInContext('config.token', context);
  } else if (plan.scenario === 'config-retry') {
    // Boot's first generation failed on its config read (failConfigFirst).
    // The memo must not cache that rejection: a later caller starts a fresh
    // generation, which now succeeds and auto-generates a token.
    const getsAfterFail = configGets;
    vm.runInContext('globalThis.__recalled = loadConfig();', context);
    await settle();
    outcome.getsAfterFailed = configGets - getsAfterFail;
    outcome.tokenWrites = tokenWrites;
    outcome.finalToken = vm.runInContext('config.token', context);
  }
  return outcome;
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def _run(plan):
    """Drive the worker under Node with one plan and read back."""
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    result = run_node_program(
        node, _HARNESS, [str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, payload=plan)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_heartbeat_alarm_during_the_config_read_joins_boot(tmp):
    """The alarm's loadConfig joins boot's in-flight generation.

    On a worker restart the alarm survives the MV3 kill and reaches loadConfig
    while boot's generation is parked on its first storage read (config.token
    still ''). loadConfig runs at most ONE generation. Held on the CONFIG
    read and on a fresh install (no stored token, so the auto-generate branch
    is reachable). Observations are exact counts — a generation count, a
    storage-write count, the distinct minted token, and the surviving
    credential — never a wall-clock margin.
    """
    del tmp
    outcome = _run({'scenario': 'boot-generation', 'holdConfig': True,
                    'noToken': True})
    # One generation: exactly one config get ran, and the alarm's call joined
    # it rather than issuing a second.
    assert outcome['configGets'] == 1, outcome
    # One generation auto-generates and writes a token exactly once.
    assert outcome['mintedTokens'] == ['relay-1'], outcome
    assert outcome['tokenWrites'] == 1, outcome
    # The one generation's token is what the shared config ends up holding.
    assert outcome['finalToken'] == 'relay-1', outcome


def test_a_failed_config_generation_does_not_poison_a_later_caller(tmp):
    """A rejected generation must not wedge the worker's memo.

    Boot's first config read fails, so the generation rejects. Caching that
    rejected promise forever would leave every later caller joining a failure
    that can never be retried. The memo must clear on rejection: a later
    loadConfig starts a fresh generation, which succeeds and auto-generates a
    token. Both callers already waiting on the failed generation still share
    that one failure — they await the same promise, which is what the join
    guarantees.
    """
    del tmp
    outcome = _run({'scenario': 'config-retry', 'failConfigFirst': True,
                    'noToken': True})
    # A fresh generation ran after the failure (a new config get), rather than
    # the later caller joining the cached rejection.
    assert outcome['getsAfterFailed'] == 1, outcome
    assert outcome['tokenWrites'] == 1, outcome
    assert outcome['finalToken'] == 'relay-1', outcome


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='configbootgen_')


if __name__ == '__main__':
    raise SystemExit(main())
