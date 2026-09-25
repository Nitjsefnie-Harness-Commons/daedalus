"""Shared Node-VM harness for the extension/worker/tabs.js suites.

Not a suite itself — run_tests.py only loads `test_*.py`. It owns the whole
worker control plane the tab suites share: build a `chrome` fake from a
plan-driven domain literal spliced with INERT_WORKER_APIS, load background.js
through import_scripts_stub, await loadConfig(), dispatch commands through
dispatchCommand, and hand back the posted postResult payloads alongside every
recorded chrome call. The suites supply the plan and assert the answer and the
calls together; this module never decides what a handler should do.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boundary_env import run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_chrome_fake import INERT_WORKER_APIS  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402

TOKEN = 'tabs-token'
SERVER = 'https://bridge.example.com'

_TABS_HARNESS = (r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, plan] = process.argv.slice(1);
const resultPayloads = [];
const calls = [];
const messageListeners = [];
const storageStore = {
  'daedalus-token': '__TOKEN__',
  'daedalus-server': '__SERVER__',
};
let createdCount = 0;

function copy(value) {
  return value === undefined
    ? undefined
    : JSON.parse(JSON.stringify(value));
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
    addListener(listener) {
      if (listeners) listeners.push(listener);
    },
  };
}

function record(api, args) {
  calls.push({ api, args: copy(args) });
}

const DEFAULT_ACTIVE_TABS = [
  { id: 7, windowId: 3, url: 'https://page.example.com', title: 'Page' },
];

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const out = {};
        for (const key of [].concat(keys)) {
          if (key in storageStore) out[key] = copy(storageStore[key]);
        }
        return out;
      },
      set: async (entries) => {
        for (const key of Object.keys(entries)) {
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
    query(query, callback) {
      // Boot's registerAllTabs calls query with a callback and an empty
      // query. Answer it off the record so only the handlers' own
      // promise-form queries appear in the call log.
      if (typeof callback === 'function') {
        callback([]);
        return undefined;
      }
      record('tabs.query', [query]);
      const tabs = plan.activeTabs || DEFAULT_ACTIVE_TABS;
      return Promise.resolve(tabs.map((tab) => ({ ...tab })));
    },
    create: async (details) => {
      record('tabs.create', [details]);
      const reject = (plan.createReject || {})[details.url];
      if (reject !== undefined) throw new Error(reject);
      createdCount += 1;
      return { id: 99 + createdCount, windowId: 1, url: details.url };
    },
    update: async (tabId, changes) => {
      record('tabs.update', [tabId, changes]);
      return { id: tabId, windowId: 3 };
    },
    reload: async (tabId, options) => {
      record('tabs.reload', [tabId, options]);
    },
    get: async () => {
      throw new Error('unmodelled chrome.tabs.get');
    },
    sendMessage: async () => {
      throw new Error('unmodelled chrome.tabs.sendMessage');
    },
  },
  windows: {
    update: async (windowId, details) => {
      record('windows.update', [windowId, details]);
      return {};
    },
  },
""" + INERT_WORKER_APIS + r"""
  scripting: {
    executeScript: async () => { throw new Error('unavailable'); },
    insertCSS: async (details) => {
      record('scripting.insertCSS', [details]);
    },
    removeCSS: async (details) => {
      record('scripting.removeCSS', [details]);
    },
  },
};

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.endsWith('/result') && init.method === 'POST') {
    const item = JSON.parse(init.body);
    resultPayloads.push({
      id: item.id,
      tabId: item.tabId,
      result: item.result,
      error: item.error,
    });
    return response(200, { ok: true });
  }
  if (url.includes('/stream?')) return response(503, { error: 'disabled' });
  if (/\/(register|sync-tabs|unregister)$/.test(url)) {
    return response(200, { ok: true });
  }
  throw new Error('unexpected fetch: ' + url);
}

const context = vm.createContext({
  chrome,
  fetch: bridgeFetch,
  crypto: { randomUUID: () => 'relay-1' },
  AbortController,
  TextDecoder,
  URL,
  performance,
  atob,
  btoa,
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
""" + import_scripts_stub('context') + r"""

async function dispatch(command) {
  context.nextCommand = command;
  await vm.runInContext('dispatchCommand(nextCommand)', context);
}

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);
  const outcomes = [];
  for (const command of plan.commands || []) {
    try {
      await dispatch(command);
      outcomes.push({ settled: 'resolved' });
    } catch (error) {
      outcomes.push({ settled: 'rejected', message: error.message });
    }
  }
  return { calls, posted: resultPayloads, outcomes };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""").replace('__TOKEN__', TOKEN).replace('__SERVER__', SERVER)


def run_tabs(commands, **plan):
    """Run the worker VM over `commands`; return calls/posted/outcomes.

    `plan` carries the fake chrome's behaviour: `activeTabs` (what
    tabs.query resolves), `createReject` (url -> rejection message). Every
    recorded chrome call and every posted postResult payload comes back.
    """
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    payload = dict(plan)
    payload['commands'] = commands
    result = run_node_program(
        node, _TABS_HARNESS,
        [str(EXTENSION_ROOT / 'background.js')], cwd=ROOT, payload=payload)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def command(**fields):
    cmd = {'id': 'cmd-1', '_did': 'did-1'}
    cmd.update(fields)
    return cmd


def apis(outcome, *names):
    """Ordered [api, args] pairs for the named chrome surfaces."""
    return [[c['api'], c['args']] for c in outcome['calls']
            if c['api'] in names]
