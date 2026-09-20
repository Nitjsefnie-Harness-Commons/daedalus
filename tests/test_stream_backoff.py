#!/usr/bin/env python3
"""The worker stream connector's retry backoff and auth-refusal stop.

Each test loads the shipped worker modules in a Node VM with a stubbed
chrome and drives the real boot path (loadConfig, then startStream)
against a bridge fake whose /stream answers come from the plan. The
harness records the timers stream.js schedules, so assertions read the
delays the code chose — never a wall-clock margin.
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

TOKEN = 'tok-1'
BRIDGE = 'https://bridge.example.com'
NEW_TOKEN = 'tok-2'
NEW_BRIDGE = 'https://other.example.com'


_STREAM_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, plan] = process.argv.slice(1);

const messageListeners = [];
const alarmListeners = [];
const changeListeners = [];
const streamFetches = [];
const timeoutTimers = [];
const intervalTimers = [];
let nextTimerId = 0;
const storageStore = {
  'daedalus-token': 'tok-1',
  'daedalus-server': 'https://bridge.example.com',
};

function copy(value) {
  return value === undefined ? undefined :
    JSON.parse(JSON.stringify(value));
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

function streamResponse(answer) {
  if (answer === 'ok') {
    return {
      ok: true,
      status: 200,
      body: {
        getReader() {
          return {
            async read() { return { done: true, value: undefined }; },
          };
        },
      },
    };
  }
  return { ok: false, status: answer, body: null };
}

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.includes('/stream?')) {
    const next = plan.statuses && plan.statuses.length
      ? plan.statuses.shift()
      : 503;
    streamFetches.push({
      auth: (init.headers || {}).Authorization || null,
      answered: next,
    });
    return streamResponse(next);
  }
  if (/\/(register|sync-tabs|unregister)$/.test(url)) {
    return response(200, { ok: true });
  }
  return response(200, { ok: true });
}

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
    onChanged: eventTarget(changeListeners),
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
    get: async (tabId) => ({ id: tabId, url: '', title: '' }),
    sendMessage: async () => {},
    create(_details, callback) { callback({ id: 101 }); },
  },
""" + INERT_WORKER_APIS + r"""
};
chrome.alarms.onAlarm = eventTarget(alarmListeners);

function scheduleTimeout(callback, ms) {
  const timer = { id: ++nextTimerId, callback, delay: ms };
  timeoutTimers.push(timer);
  return timer.id;
}

function scheduleInterval(callback, ms) {
  const timer = {
    id: ++nextTimerId, callback, delay: ms, cleared: false,
  };
  intervalTimers.push(timer);
  return timer.id;
}

function clearScheduledInterval(id) {
  const timer = intervalTimers.find((item) => item.id === id);
  if (timer) timer.cleared = true;
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
  setTimeout: scheduleTimeout,
  clearTimeout() {},
  setInterval: scheduleInterval,
  clearInterval: clearScheduledInterval,
  console: { log() {}, warn() {}, error() {} },
});
""" + import_scripts_stub('context') + r"""

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function waitFor(predicate, label) {
  for (let attempt = 0; attempt < 2000; attempt++) {
    if (predicate()) return;
    await delay();
  }
  throw new Error('timed out waiting for ' + label);
}

async function settle() {
  for (let i = 0; i < 25; i++) await delay();
}

async function nextRetryDelay(fetchCount) {
  await waitFor(() => streamFetches.length >= fetchCount, 'stream fetch');
  await waitFor(() => timeoutTimers.length > 0, 'retry timer');
  const timer = timeoutTimers.shift();
  timer.callback();
  return timer.delay;
}

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await settle();
  const outcome = {};
  if (plan.scenario === 'backoff') {
    const delays = [];
    for (let round = 1; round <= 7; round++) {
      delays.push(await nextRetryDelay(round));
    }
    outcome.delays = delays;
  } else if (plan.scenario === 'reset') {
    const delays = [];
    for (let round = 1; round <= 3; round++) {
      delays.push(await nextRetryDelay(round));
    }
    outcome.delays = delays;
  } else if (plan.scenario === 'stop') {
    await settle();
    outcome.fetches = streamFetches.length;
    outcome.pendingTimers = timeoutTimers.length;
    const before = streamFetches.length;
    for (const listener of alarmListeners) {
      listener({ name: 'daedalus-heartbeat' });
    }
    await settle();
    outcome.fetchesAfterHeartbeat = streamFetches.length - before;
    outcome.intervals = intervalTimers.map((item) => ({
      delay: item.delay, cleared: item.cleared,
    }));
  } else if (plan.scenario === 'resume') {
    await settle();
    outcome.bootFetches = streamFetches.length;
    const change = {};
    change[plan.field] = { oldValue: null, newValue: plan.value };
    for (const listener of changeListeners) listener(change, 'local');
    await waitFor(
      () => streamFetches.length > outcome.bootFetches, 'resumed fetch');
    outcome.resumedAuth = streamFetches[outcome.bootFetches].auth;
    await settle();
    outcome.pending = timeoutTimers.map((item) => item.delay);
  } else if (plan.scenario === 'reopen') {
    await settle();
    outcome.bootFetches = streamFetches.length;
    let seen = streamFetches.length;
    for (const value of plan.changes) {
      const change = {};
      change['daedalus-token'] = { oldValue: null, newValue: value };
      for (const listener of changeListeners) listener(change, 'local');
      seen += 1;
      await waitFor(() => streamFetches.length >= seen, 'changed fetch');
      await settle();
    }
    outcome.returnedAuth = streamFetches[seen - 1].auth;
    await settle();
  }
  outcome.answered = streamFetches.map((item) => item.answered);
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
        node, _STREAM_HARNESS, [str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, payload=plan)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_consecutive_refusals_back_off_exponentially(tmp):
    del tmp
    outcome = _run({'scenario': 'backoff'})
    assert outcome['delays'] == [
        1000, 2000, 4000, 8000, 16000, 32000, 60000], outcome


def test_a_connected_stream_resets_the_backoff(tmp):
    del tmp
    outcome = _run({'scenario': 'reset', 'statuses': [503, 'ok', 503]})
    assert outcome['delays'] == [1000, 1000, 1000], outcome


def test_an_auth_refusal_stops_the_reconnect(tmp):
    """A wrong credential is not transient: stop until it is replaced."""
    del tmp
    for status in (401, 400):
        outcome = _run({'scenario': 'stop', 'statuses': [status]})
        assert outcome['answered'] == [status], (status, outcome)
        assert outcome['fetches'] == 1, (status, outcome)
        assert outcome['pendingTimers'] == 0, (status, outcome)
        assert outcome['fetchesAfterHeartbeat'] == 0, (status, outcome)


def test_the_auth_stop_tears_down_the_watchdog_interval(tmp):
    """The refused attempt's watchdog does not tick into the stop."""
    del tmp
    for status in (401, 400):
        outcome = _run({'scenario': 'stop', 'statuses': [status]})
        assert outcome['intervals'] == [
            {'delay': 20000, 'cleared': False},
            {'delay': 5000, 'cleared': True}], (status, outcome)


def test_a_new_token_resumes_connecting(tmp):
    del tmp
    outcome = _run({'scenario': 'resume', 'statuses': [401, 'ok'],
                    'field': 'daedalus-token', 'value': NEW_TOKEN})
    assert outcome['bootFetches'] == 1, outcome
    assert outcome['answered'] == [401, 'ok'], outcome
    assert outcome['resumedAuth'] == 'Bearer ' + NEW_TOKEN, outcome
    assert outcome['pending'] == [1000], outcome


def test_a_new_bridge_url_resumes_connecting(tmp):
    del tmp
    outcome = _run({'scenario': 'resume', 'statuses': [401, 'ok'],
                    'field': 'daedalus-server', 'value': NEW_BRIDGE})
    assert outcome['bootFetches'] == 1, outcome
    assert outcome['answered'] == [401, 'ok'], outcome
    assert outcome['resumedAuth'] == 'Bearer ' + TOKEN, outcome
    assert outcome['pending'] == [1000], outcome


def test_a_connected_stream_reopens_the_stopped_pair(tmp):
    """The success that cleared the stop lets the old pair be retried."""
    del tmp
    outcome = _run({'scenario': 'reopen',
                    'statuses': [401, 'ok', 503],
                    'changes': [NEW_TOKEN, TOKEN]})
    assert outcome['bootFetches'] == 1, outcome
    assert outcome['answered'] == [401, 'ok', 503], outcome
    assert outcome['returnedAuth'] == 'Bearer ' + TOKEN, outcome


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='streambackoff_')


if __name__ == '__main__':
    raise SystemExit(main())
