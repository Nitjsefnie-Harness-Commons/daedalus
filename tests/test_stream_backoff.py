#!/usr/bin/env python3
"""The worker stream connector's retry backoff, its auth-refusal stop,
and the ledger-readiness gate that keeps the stream closed until the
persisted dedup ledger has been read at boot.

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
# The one non-stream request every stream connect and the boot make, and
# the result a dispatched command posts. Scenarios declare how many of
# each their recording shows, so an extra or a dropped call is refused.
SYNC = 'POST /sync-tabs'
RESULT = 'POST /result'


_STREAM_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, plan] = process.argv.slice(1);
const BRIDGE_URL = 'https://bridge.example.com';

const messageListeners = [];
const alarmListeners = [];
const changeListeners = [];
const connectListeners = [];
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const timeoutTimers = [];
const intervalTimers = [];
let nextTimerId = 0;
let clockNow = 0;
const storageStore = {
  'daedalus-token': 'tok-1',
  'daedalus-server': 'https://bridge.example.com',
  'daedalus-seen-dids': ['did-old'],
};
let ledgerHold = null;

function openLedger() {
  if (ledgerHold) {
    ledgerHold.resolve({
      'daedalus-seen-dids': copy(storageStore['daedalus-seen-dids']),
    });
  }
}

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
  if (answer === 'silent') {
    return {
      ok: true,
      status: 200,
      body: {
        getReader() {
          return { read: () => new Promise(() => {}) };
        },
      },
    };
  }
  if (answer === 'ok-data') {
    let reads = 0;
    return {
      ok: true,
      status: 200,
      body: {
        getReader() {
          return {
            async read() {
              reads += 1;
              if (reads === 1) {
                return {
                  done: false,
                  value: new TextEncoder().encode(': ping'),
                };
              }
              return { done: true, value: undefined };
            },
          };
        },
      },
    };
  }
  if (answer === 'kill') {
    return {
      ok: true,
      status: 200,
      body: {
        getReader() {
          return {
            read: () => Promise.reject(new TypeError('killed')),
          };
        },
      },
    };
  }
  if (answer === 'ledger-commands') {
    const commands = [
      ['cmd-old', 'did-old'],
      ['cmd-new', 'did-new'],
    ];
    const chunks = commands.map(([id, did]) => 'event: command\ndata: '
      + JSON.stringify({ id, type: 'no-such-type', _did: did }) + '\n\n');
    let reads = 0;
    return {
      ok: true,
      status: 200,
      body: {
        getReader() {
          return {
            async read() {
              if (reads < chunks.length) {
                const value = chunks[reads];
                reads += 1;
                return {
                  done: false,
                  value: new TextEncoder().encode(value),
                };
              }
              return { done: true, value: undefined };
            },
          };
        },
      },
    };
  }
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

// A planned request is one the scenario's recorded run declared. The
// scenario carries its own multiset so a repeated call is planned only as
// often as the recording shows it happening. A request beyond the declared
// count is refused with a non-ok status and recorded, so the run's Python
// assertion and the worker's own outcome both see it.
function accountRequest(request) {
  const seen = nonStreamFetches.filter((i) => i.request === request).length;
  const planned = (plan.planned || []).filter((i) => i === request).length;
  const refused = seen >= planned;
  nonStreamFetches.push({ request, refused });
  if (refused) refusedFetches.push(request);
  return refused;
}

// A declaration names a route, not a host: the resumed-bridge scenario
// legitimately talks to two hosts on the same route.
function requestKey(url, init) {
  return (init.method || 'GET') + ' ' + url.replace(/^https?:\/\/[^/]+/, '');
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
    if (next === 'down') {
      throw new TypeError('Failed to fetch');
    }
    return streamResponse(next);
  }
  const request = requestKey(url, init);
  if (/\/(register|sync-tabs|unregister)$/.test(url)) {
    return accountRequest(request)
      ? response(599, { ok: false, error: 'unplanned request' })
      : response(200, { ok: true });
  }
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    resultPosts.push({ did: payload._did || null });
    return accountRequest('POST /result')
      ? response(599, { ok: false, error: 'unplanned request' })
      : response(200, { ok: true });
  }
  return accountRequest(request)
    ? response(599, { ok: false, error: 'unplanned request' })
    : response(200, { ok: true });
}

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const wanted = [].concat(keys);
        if (plan.holdLedger && wanted.includes('daedalus-seen-dids')) {
          return new Promise((resolve) => { ledgerHold = { resolve }; });
        }
        if (plan.failLedger && wanted.includes('daedalus-seen-dids')) {
          throw new Error('ledger read failed');
        }
        const out = {};
        for (const key of wanted) {
          if (key in storageStore
            && !(plan.noLedger && key === 'daedalus-seen-dids')) {
            out[key] = copy(storageStore[key]);
          }
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
chrome.runtime.onConnect = eventTarget(connectListeners);

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
  Date: { now: () => clockNow },
  AbortController,
  TextDecoder,
  TextEncoder,
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
  // The fake-probe drives bridgeFetch directly; loading the worker would
  // only spend the plan's allowance on the worker's own boot request.
  if (plan.scenario !== 'fake-probe') {
    vm.runInContext(
      fs.readFileSync(backgroundPath, 'utf8'), context,
      { filename: backgroundPath });
  }
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
  } else if (plan.scenario === 'watchdog') {
    const before = streamFetches.length;
    clockNow = 31000;
    const watchdog = intervalTimers.find((item) => item.delay === 5000);
    watchdog.callback();
    outcome.fetchesAfterWatchdog = streamFetches.length - before;
    await settle();
    outcome.scheduled = timeoutTimers.map((item) => item.delay);
  } else if (plan.scenario === 'killed') {
    const delays = [];
    await waitFor(() => timeoutTimers.length > 0, 'eof timer');
    let timer = timeoutTimers.shift();
    delays.push(timer.delay);
    timer.callback();
    await waitFor(() => streamFetches.length >= 2, 'killed fetch');
    await waitFor(() => timeoutTimers.length > 0, 'kill retry');
    timer = timeoutTimers.shift();
    delays.push(timer.delay);
    timer.callback();
    await waitFor(() => streamFetches.length >= 3, 'killed fetch two');
    await waitFor(() => timeoutTimers.length > 0, 'kill retry two');
    timer = timeoutTimers.shift();
    delays.push(timer.delay);
    outcome.delays = delays;
  } else if (plan.scenario === 'eof-data') {
    const delays = [];
    await waitFor(() => timeoutTimers.length > 0, 'eof timer');
    let timer = timeoutTimers.shift();
    delays.push(timer.delay);
    timer.callback();
    await waitFor(() => streamFetches.length >= 2, 'next fetch');
    await waitFor(() => timeoutTimers.length > 0, 'next retry');
    delays.push(timeoutTimers[0].delay);
    outcome.delays = delays;
  } else if (plan.scenario === 'unreachable') {
    const delays = [];
    for (let round = 0; round < 6; round++) {
      await waitFor(() => timeoutTimers.length > 0, 'retry timer');
      const timer = timeoutTimers.shift();
      delays.push(timer.delay);
      timer.callback();
    }
    outcome.delays = delays;
  } else if (plan.scenario === 'ledger-window') {
    if (plan.trigger === 'connect') {
      for (const listener of connectListeners) {
        listener({
          name: 'keepalive',
          onMessage: eventTarget(),
          onDisconnect: eventTarget(),
        });
      }
    }
    if (plan.trigger === 'alarm') {
      for (const listener of alarmListeners) {
        listener({ name: 'daedalus-heartbeat' });
      }
    }
    await settle();
    outcome.windowFetches = streamFetches.length;
    openLedger();
    await waitFor(() => streamFetches.length >= 1, 'boot fetch');
    await settle();
    outcome.totalFetches = streamFetches.length;
    if (plan.deliver === 'commands') {
      await waitFor(() => resultPosts.length >= 1, 'dispatched result');
      await settle();
      outcome.dispatchedDids = resultPosts.map((item) => item.did);
    }
    if (plan.trigger === 'token-change') {
      const before = streamFetches.length;
      const change = {};
      change[plan.field] = { oldValue: null, newValue: plan.value };
      for (const listener of changeListeners) listener(change, 'local');
      await waitFor(() => streamFetches.length > before, 'resumed fetch');
      outcome.resumedAuth = streamFetches[before].auth;
    }
  } else if (plan.scenario === 'boot-ledger') {
    await settle();
    outcome.fetches = streamFetches.length;
  } else if (plan.scenario === 'fake-probe') {
    // Pins the fake itself: a planned route answers 200, an unplanned
    // one answers 599 and both are recorded — without a worker path in
    // between, so what is refused is the fake's own decision.
    const planned = await bridgeFetch(
      BRIDGE_URL + plan.plannedRoute, { method: 'POST' });
    const unplanned = await bridgeFetch(
      BRIDGE_URL + plan.unplannedRoute, { method: 'POST' });
    outcome.plannedStatus = planned.status;
    outcome.unplannedStatus = unplanned.status;
  }
  outcome.answered = streamFetches.map((item) => item.answered);
  outcome.nonStream = nonStreamFetches.map((item) => item.request);
  outcome.refused = refusedFetches;
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
    """Drive the worker under Node with one plan and read back.

    The oracle is two-sided: the fake refuses any non-stream request the
    plan did not declare (a 599 that the worker sees and the recording
    keeps), and this compares the observed sequence with the declaration
    so a scenario that stops making a planned request is also caught.
    """
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    result = run_node_program(
        node, _STREAM_HARNESS, [str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, payload=plan)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    outcome = json.loads(result.stdout)
    assert outcome['refused'] == [], (
        'unplanned bridge request(s) the scenario did not declare:',
        outcome['refused'], outcome)
    assert sorted(outcome['nonStream']) == sorted(plan.get('planned', [])), (
        'non-stream requests differ from the scenario declaration:',
        outcome['nonStream'], plan.get('planned', []))
    return outcome


def test_consecutive_refusals_back_off_exponentially(tmp):
    del tmp
    outcome = _run({'scenario': 'backoff', 'planned': [SYNC]})
    assert outcome['delays'] == [
        1000, 2000, 4000, 8000, 16000, 32000, 60000], outcome


def test_a_connected_stream_resets_the_backoff(tmp):
    del tmp
    outcome = _run({'scenario': 'reset', 'statuses': [503, 'ok', 503],
                    'planned': [SYNC, SYNC]})
    assert outcome['delays'] == [1000, 1000, 1000], outcome


def test_an_auth_refusal_stops_the_reconnect(tmp):
    """A wrong credential is not transient: stop until it is replaced."""
    del tmp
    for status in (401, 400):
        outcome = _run({'scenario': 'stop', 'statuses': [status],
                        'planned': [SYNC, SYNC]})
        assert outcome['answered'] == [status], (status, outcome)
        assert outcome['fetches'] == 1, (status, outcome)
        assert outcome['pendingTimers'] == 0, (status, outcome)
        assert outcome['fetchesAfterHeartbeat'] == 0, (status, outcome)


def test_the_auth_stop_tears_down_the_watchdog_interval(tmp):
    """The refused attempt's watchdog does not tick into the stop."""
    del tmp
    for status in (401, 400):
        outcome = _run({'scenario': 'stop', 'statuses': [status],
                        'planned': [SYNC, SYNC]})
        assert outcome['intervals'] == [
            {'delay': 20000, 'cleared': False},
            {'delay': 5000, 'cleared': True}], (status, outcome)


def test_a_silent_stream_reconnects_through_the_backoff(tmp):
    """The watchdog counts as a failed attempt, not a free reconnect."""
    del tmp
    outcome = _run({'scenario': 'watchdog', 'statuses': ['silent'],
                    'planned': [SYNC, SYNC]})
    assert outcome['fetchesAfterWatchdog'] == 0, outcome
    assert outcome['scheduled'] == [1000], outcome


def test_a_killed_connection_counts_toward_the_backoff(tmp):
    """A non-abort reader failure counts once from its connect reset."""
    del tmp
    outcome = _run(
        {'scenario': 'killed', 'statuses': ['ok', 'kill', 'kill'],
         'planned': [SYNC, SYNC, SYNC, SYNC]})
    assert outcome['delays'] == [1000, 2000, 2000], outcome


def test_a_clean_data_carrying_eof_still_retries_at_1000(tmp):
    """A good connection keeps its EOF retry at the flat 1 s scale."""
    del tmp
    outcome = _run({'scenario': 'eof-data', 'statuses': ['ok-data', 503],
                    'planned': [SYNC, SYNC]})
    assert outcome['delays'] == [1000, 1000], outcome


def test_rejected_connects_grow_to_the_cap(tmp):
    """An unreachable bridge never connects, so the counter only grows."""
    del tmp
    outcome = _run({'scenario': 'unreachable',
                    'statuses': ['down'] * 6, 'planned': [SYNC]})
    assert outcome['delays'] == [
        2000, 4000, 8000, 16000, 32000, 60000], outcome


def test_a_new_token_resumes_connecting(tmp):
    del tmp
    outcome = _run({'scenario': 'resume', 'statuses': [401, 'ok'],
                    'field': 'daedalus-token', 'value': NEW_TOKEN,
                    'planned': [SYNC, SYNC]})
    assert outcome['bootFetches'] == 1, outcome
    assert outcome['answered'] == [401, 'ok'], outcome
    assert outcome['resumedAuth'] == 'Bearer ' + NEW_TOKEN, outcome
    assert outcome['pending'] == [1000], outcome


def test_a_new_bridge_url_resumes_connecting(tmp):
    del tmp
    outcome = _run({'scenario': 'resume', 'statuses': [401, 'ok'],
                    'field': 'daedalus-server', 'value': NEW_BRIDGE,
                    'planned': [SYNC, SYNC]})
    assert outcome['bootFetches'] == 1, outcome
    assert outcome['answered'] == [401, 'ok'], outcome
    assert outcome['resumedAuth'] == 'Bearer ' + TOKEN, outcome
    assert outcome['pending'] == [1000], outcome


def test_a_connected_stream_reopens_the_stopped_pair(tmp):
    """The success that cleared the stop lets the old pair be retried."""
    del tmp
    outcome = _run({'scenario': 'reopen',
                    'statuses': [401, 'ok', 503],
                    'changes': [NEW_TOKEN, TOKEN],
                    'planned': [SYNC, SYNC]})
    assert outcome['bootFetches'] == 1, outcome
    assert outcome['answered'] == [401, 'ok', 503], outcome
    assert outcome['returnedAuth'] == 'Bearer ' + TOKEN, outcome


def test_a_keepalive_connect_during_the_ledger_read_starts_no_stream(tmp):
    """A keepalive port connect inside the ledger-read window must stay
    idle: the stream it would open dispatches against an empty dedup
    ledger, and boot then opens a second stream over it."""
    del tmp
    outcome = _run({'scenario': 'ledger-window', 'trigger': 'connect',
                    'holdLedger': True, 'statuses': ['silent'],
                    'planned': [SYNC, SYNC]})
    assert outcome['windowFetches'] == 0, outcome
    assert outcome['totalFetches'] == 1, outcome


def test_a_heartbeat_alarm_during_the_ledger_read_starts_no_stream(tmp):
    """The heartbeat alarm races boot the same way a port connect does."""
    del tmp
    outcome = _run({'scenario': 'ledger-window', 'trigger': 'alarm',
                    'holdLedger': True, 'statuses': ['silent'],
                    'planned': [SYNC, SYNC, SYNC]})
    assert outcome['windowFetches'] == 0, outcome
    assert outcome['totalFetches'] == 1, outcome


def test_boot_opens_the_stream_once_with_the_ledger_loaded(tmp):
    """Boot's own start is the one the gate admits: one fetch, and the
    ledger it read decides dedup — a persisted delivery id is skipped,
    a fresh one dispatches."""
    del tmp
    outcome = _run({'scenario': 'ledger-window', 'holdLedger': True,
                    'deliver': 'commands',
                    'statuses': ['ledger-commands'],
                    'planned': [SYNC, SYNC, RESULT]})
    assert outcome['windowFetches'] == 0, outcome
    assert outcome['totalFetches'] == 1, outcome
    assert outcome['dispatchedDids'] == ['did-new'], outcome


def test_a_failed_ledger_read_still_opens_the_stream(tmp):
    """The gate closes on an unread ledger, not a failed one: a rejected
    read is tolerated (the in-memory ledger still dedups), and the
    boot start must go through."""
    del tmp
    outcome = _run({'scenario': 'boot-ledger', 'failLedger': True,
                    'statuses': ['silent'], 'planned': [SYNC, SYNC]})
    assert outcome['fetches'] == 1, outcome


def test_a_worker_without_a_stored_ledger_still_opens_the_stream(tmp):
    """A fresh install has no ledger row at all: the gate must still open
    once the read has answered empty, and never lock the stream out."""
    del tmp
    outcome = _run({'scenario': 'boot-ledger', 'noLedger': True,
                    'statuses': ['silent'], 'planned': [SYNC, SYNC]})
    assert outcome['fetches'] == 1, outcome


def test_a_post_boot_token_change_still_reconnects_after_the_gate(tmp):
    """Once the ledger read has completed, the healthy reconnect paths
    are untouched: a token change still tears down and reopens."""
    del tmp
    outcome = _run({'scenario': 'ledger-window', 'trigger': 'token-change',
                    'holdLedger': True, 'statuses': ['silent'],
                    'field': 'daedalus-token', 'value': NEW_TOKEN,
                    'planned': [SYNC, SYNC]})
    assert outcome['windowFetches'] == 0, outcome
    assert outcome['totalFetches'] == 1, outcome
    assert outcome['resumedAuth'] == 'Bearer ' + NEW_TOKEN, outcome


def test_the_fake_refuses_an_unplanned_request(tmp):
    """The fake's own oracle: a planned route answers 200, an unplanned
    route answers 599, and both land in the recording."""
    del tmp
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    result = run_node_program(
        node, _STREAM_HARNESS, [str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT,
        payload={'scenario': 'fake-probe', 'planned': [SYNC],
                 'plannedRoute': '/sync-tabs', 'unplannedRoute': '/tabs'})
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    outcome = json.loads(result.stdout)
    assert outcome['plannedStatus'] == 200, outcome
    assert outcome['unplannedStatus'] == 599, outcome
    assert outcome['refused'] == ['POST /tabs'], outcome


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='streambackoff_')


if __name__ == '__main__':
    raise SystemExit(main())
