#!/usr/bin/env python3
"""Resetting the timer would starve registration during continuous updates."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_gate)
from _worker_chrome_fake import INERT_WORKER_APIS  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402

REGISTER = 'POST /register'
SYNC = 'POST /sync-tabs'
# Every scenario's recording, from a run of the shipped worker: boot opens the
# stream, syncs the tab list, then one register post per fired timer. No route
# is special-cased, so an invented call lands outside the plan and is refused.
ONE = [SYNC, REGISTER]
TWO = [SYNC, REGISTER, REGISTER]

_REGISTER_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const [backgroundPath, plan] = process.argv.slice(1);
const messageListeners = [];
const updateListeners = [];
const timers = [];
const cleared = [];
const tabs = new Map([
  [7, { id: 7, title: 'Initial', url: 'https://page.example.com/7' }],
  [8, { id: 8, title: 'Other', url: 'https://page.example.com/8' }],
]);

function eventTarget(listeners = []) {
  return { addListener: listener => listeners.push(listener) };
}

function schedule(callback, delay) {
  const timer = { id: timers.length + 1, callback, delay, pending: true };
  timers.push(timer);
  return timer.id;
}

function clearScheduled(id) {
  cleared.push(id);
  const timer = timers.find(item => item.id === id);
  if (timer) timer.pending = false;
}

const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'register-token',
        'daedalus-server': 'https://bridge.example.com',
      }),
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(updateListeners),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    get: async id => ({ ...tabs.get(id) }),
    query(_query, callback) { callback([...tabs.values()]); },
  },
""" + INERT_WORKER_APIS + r"""
};

// The shared gate's in-scope contract. The gate answers only what the
// scenario declared and records every request it sees.
const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
function response(status, data) {
  return {
    ok: status >= 200 && status < 300, status, body: null,
    json: async () => data, text: async () => JSON.stringify(data),
  };
}
function streamResponse(answer) {
  return response(answer, { error: 'disabled' });
}
""" + STRICT_FETCH + r"""

const context = vm.createContext({
  chrome, fetch: bridgeFetch, AbortController, TextDecoder, URL,
  performance, atob, btoa,
  setTimeout: schedule, clearTimeout: clearScheduled,
  setInterval: () => 1, clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
""" + import_scripts_stub('context') + r"""

function settle() {
  return new Promise(resolve => setImmediate(resolve));
}

async function run() {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await settle();
  const baseline = timers.length;
  const baselineClears = cleared.length;
  const observations = [];
  for (const step of plan.steps) {
    if (step.state) {
      tabs.set(step.id, { ...tabs.get(step.id), ...step.state });
    }
    if (step.change) {
      for (const listener of updateListeners) listener(step.id, step.change);
    }
    if (step.fire) {
      for (const timer of timers.slice(baseline)) {
        if (!timer.pending) continue;
        timer.pending = false;
        timer.callback();
      }
    }
    await settle();
    observations.push({
      delays: timers.slice(baseline).map(timer => timer.delay),
      pending: timers.slice(baseline).filter(timer => timer.pending).length,
      cleared: cleared.slice(baselineClears),
      requests: nonStreamFetches.map(
        (item) => ({ request: item.request, body: item.body })),
    });
  }
  return { observations, refused: refusedFetches, badOrigins };
}

run().then(result => process.stdout.write(JSON.stringify(result)))
  .catch(error => {
    process.stderr.write((error.stack || String(error)) + '\n');
    process.exitCode = 1;
  });
"""


def _observe(*steps, planned):
    outcome = run_gate(
        require_node(), _REGISTER_HARNESS,
        [str(EXTENSION_ROOT / 'background.js')], cwd=ROOT,
        plan={'steps': list(steps), 'planned': list(planned)})
    last = outcome['observations'][-1]
    assert_gate_clean([r['request'] for r in last['requests']],
                      outcome['refused'], outcome['badOrigins'],
                      list(planned))
    return outcome['observations']


def _update(tab_id=7, **change):
    return {'id': tab_id, 'change': change}


def _posts(observation):
    return [r['body'] for r in observation['requests']
            if r['request'] == REGISTER]


def test_title_burst_arms_once_without_resetting(tmp):
    """Catches direct registration or a resetting timer."""
    del tmp
    *events, fired = _observe(
        _update(title='A'), _update(title='B'), _update(title='C'),
        {'fire': True}, planned=ONE)
    for event in events:
        assert event['delays'] == [250], event
        assert event['pending'] == 1, event
        assert event['cleared'] == [], event
        assert _posts(event) == [], event
    assert [post['tabId'] for post in _posts(fired)] == ['7'], fired


def test_register_reads_live_tab_state_when_timer_fires(tmp):
    """Catches direct calls or payloads built from changeInfo."""
    del tmp
    *_, fired = _observe(
        _update(title='A'), _update(title='B'),
        {'id': 7, 'state': {
            'title': 'B', 'url': 'https://page.example.com/current'}},
        {'fire': True}, planned=ONE)
    assert _posts(fired) == [{
        'token': 'register-token', 'tabId': '7', 'title': 'B',
        'url': 'https://page.example.com/current',
    }], fired


def test_different_tabs_keep_independent_timers(tmp):
    """Catches direct registration or a shared timer."""
    del tmp
    *_, pending, fired = _observe(
        _update(7, title='A'), _update(8, title='X'),
        _update(7, title='B'), _update(8, title='Y'), {'fire': True},
        planned=TWO)
    assert pending['delays'] == [250, 250], pending
    assert pending['pending'] == 2, pending
    assert _posts(pending) == [], pending
    assert sorted(post['tabId'] for post in _posts(fired)) == ['7', '8']


def test_fired_tab_can_arm_a_new_registration(tmp):
    """Catches direct calls or a map entry that is never deleted."""
    del tmp
    first, fired, again, twice = _observe(
        _update(title='A'), {'fire': True}, _update(title='B'),
        {'fire': True}, planned=TWO)
    assert first['delays'] == [250], first
    assert len(_posts(fired)) == 1, fired
    assert again['delays'] == [250, 250], again
    assert again['pending'] == 1, again
    assert len(_posts(again)) == 1, again
    assert [post['tabId'] for post in _posts(twice)] == ['7', '7'], twice


def test_url_burst_uses_the_same_coalescing_path(tmp):
    """Catches direct registerTab calls for URL changes."""
    del tmp
    _, pending, fired = _observe(
        _update(url='https://page.example.com/a'),
        _update(url='https://page.example.com/b'), {'fire': True},
        planned=ONE)
    assert pending['delays'] == [250], pending
    assert pending['pending'] == 1, pending
    assert pending['cleared'] == [], pending
    assert _posts(pending) == [], pending
    assert [post['tabId'] for post in _posts(fired)] == ['7'], fired


def test_registration_window_is_250_milliseconds(tmp):
    """Catches direct registration or a changed scheduler delay."""
    del tmp
    pending, fired = _observe(_update(title='A'), {'fire': True},
                              planned=ONE)
    assert pending['delays'] == [250], pending
    assert len(_posts(fired)) == 1, fired


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='registertab_')


if __name__ == '__main__':
    raise SystemExit(main())
