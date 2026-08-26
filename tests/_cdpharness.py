"""The Node VM harness for CDP handle lifecycle.

Not a suite itself — run_tests.py only loads `test_*.py`.

Every CDP evaluation leaves a remote handle on the inspector side, and a
session that is kept for a capture keeps the attachment too — so the release
has to happen on the compile, throw, reject and pending paths alike. This
harness makes each of those observable.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402


_CDP_HANDLE_LIFECYCLE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const backgroundPath = process.argv[1];
const released = [];
const postedResults = [];
const finalAwaitPromise = [];
const timers = [];
let pendingResolve;

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

const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'lifecycle-token',
        'daedalus-server': 'test-bridge',
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
      const tabs = [{ id: 7, url: '', title: 'Page' }];
      if (callback) {
        callback(tabs);
        return undefined;
      }
      return Promise.resolve(tabs);
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => {},
    detach: async () => {},
    sendCommand: async (_target, method, params) => {
      if (method === 'Runtime.releaseObject') {
        released.push(params.objectId);
        if (params.objectId === 'pending-original' && pendingResolve) {
          const resolve = pendingResolve;
          pendingResolve = null;
          setImmediate(() => resolve({ result: { objectId: 'pending-late' } }));
        }
        return {};
      }
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
        finalAwaitPromise.push(params.awaitPromise);
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
    },
  },
  scripting: { executeScript: async () => [{ result: false }] },
  runtime: {
    onMessage: eventTarget(),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: { onAlarm: eventTarget(), create() {} },
};

const context = vm.createContext({
  chrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.endsWith('/result') && init.method === 'POST') {
      postedResults.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    if (url.includes('/stream?')) return new Promise(() => {});
    return response(200, { ok: true });
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
""" + import_scripts_stub('context') + r"""

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
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context);
  await delay();
  vm.runInContext('_cdpSessions[7] = true', context);

  await runEval('compile', 'return compile-case');
  await runEval('throw', 'throw-case');
  await runEval('reject', 'reject-case');

  context.pendingRemote = {
    objectId: 'pending-original',
    subtype: 'promise',
  };
  const pending = vm.runInContext('_cdpSettle(7, pendingRemote)', context);
  await delay();
  const timer = timers.find((item) => item.active && item.ms === 10000);
  const pendingHasTimeout = Boolean(timer);
  if (timer) {
    timer.active = false;
    timer.callback();
    try { await pending; } catch (_) {}
    await delay();
    await delay();
  }

  process.stdout.write(JSON.stringify({
    released: [...new Set(released)].sort(),
    finalAwaitPromise,
    pendingHasTimeout,
    resultWorlds: postedResults.map((item) => item.world),
  }));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def run_cdp_handle_lifecycle():
    node = shutil.which('node')
    assert node, 'node is required to execute the CDP lifecycle harness'
    result = subprocess.run(
        [node, '-e', _CDP_HANDLE_LIFECYCLE_HARNESS,
         str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)
