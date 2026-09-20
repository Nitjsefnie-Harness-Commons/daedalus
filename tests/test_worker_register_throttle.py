#!/usr/bin/env python3
"""Title and URL updates share a non-resetting per-tab register window."""
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

_REGISTER_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const [backgroundPath, steps] = process.argv.slice(1);
const messageListeners = [];
const updateListeners = [];
const requests = [];
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

async function bridgeFetch(target, init = {}) {
  const path = new URL(target).pathname;
  requests.push({
    path, method: init.method || 'GET',
    body: init.body ? JSON.parse(init.body) : null,
  });
  const status = path === '/stream' ? 503 : 200;
  return {
    ok: status === 200, status, body: null,
    json: async () => ({ ok: true, updated: true }),
    text: async () => '',
  };
}

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
  for (const step of steps) {
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
      requests: requests.slice(),
    });
  }
  return observations;
}

run().then(result => process.stdout.write(JSON.stringify(result)))
  .catch(error => {
    process.stderr.write((error.stack || String(error)) + '\n');
    process.exitCode = 1;
  });
"""


def _observe(*steps):
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    result = run_node_program(
        node, _REGISTER_HARNESS,
        [str(EXTENSION_ROOT / 'background.js')], cwd=ROOT, payload=steps)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _update(tab_id=7, **change):
    return {'id': tab_id, 'change': change}


def _posts(observation):
    return [request['body'] for request in observation['requests']
            if request['path'] == '/register'
            and request['method'] == 'POST']


def test_title_burst_arms_once_without_resetting(tmp):
    """Direct registration or clearing and re-arming breaks this control."""
    del tmp
    *events, fired = _observe(
        _update(title='A'), _update(title='B'), _update(title='C'),
        {'fire': True})
    for event in events:
        assert event['delays'] == [250], event
        assert event['pending'] == 1, event
        assert event['cleared'] == [], event
        assert _posts(event) == [], event
    assert [post['tabId'] for post in _posts(fired)] == ['7'], fired


def test_register_reads_live_tab_state_when_timer_fires(tmp):
    """Direct calls or payloads taken from changeInfo lose the live state."""
    del tmp
    *_, fired = _observe(
        _update(title='A'), _update(title='B'),
        {'id': 7, 'state': {
            'title': 'B', 'url': 'https://page.example.com/current'}},
        {'fire': True})
    assert _posts(fired) == [{
        'token': 'register-token', 'tabId': '7', 'title': 'B',
        'url': 'https://page.example.com/current',
    }], fired


def test_different_tabs_keep_independent_timers(tmp):
    """Direct registration or a shared timer loses per-tab coalescing."""
    del tmp
    *_, pending, fired = _observe(
        _update(7, title='A'), _update(8, title='X'),
        _update(7, title='B'), _update(8, title='Y'), {'fire': True})
    assert pending['delays'] == [250, 250], pending
    assert pending['pending'] == 2, pending
    assert _posts(pending) == [], pending
    assert sorted(post['tabId'] for post in _posts(fired)) == ['7', '8']


def test_fired_tab_can_arm_a_new_registration(tmp):
    """Direct calls or a map entry never deleted prevent a fresh window."""
    del tmp
    first, fired, again, twice = _observe(
        _update(title='A'), {'fire': True}, _update(title='B'),
        {'fire': True})
    assert first['delays'] == [250], first
    assert len(_posts(fired)) == 1, fired
    assert again['delays'] == [250, 250], again
    assert again['pending'] == 1, again
    assert len(_posts(again)) == 1, again
    assert [post['tabId'] for post in _posts(twice)] == ['7', '7'], twice


def test_url_burst_uses_the_same_coalescing_path(tmp):
    """A direct registerTab call for URL changes bypasses the window."""
    del tmp
    _, pending, fired = _observe(
        _update(url='https://page.example.com/a'),
        _update(url='https://page.example.com/b'), {'fire': True})
    assert pending['delays'] == [250], pending
    assert pending['pending'] == 1, pending
    assert pending['cleared'] == [], pending
    assert _posts(pending) == [], pending
    assert [post['tabId'] for post in _posts(fired)] == ['7'], fired


def test_registration_window_is_250_milliseconds(tmp):
    """Direct registration or a changed scheduler delay violates the window."""
    del tmp
    pending, fired = _observe(_update(title='A'), {'fire': True})
    assert pending['delays'] == [250], pending
    assert len(_posts(fired)) == 1, fired


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='registertab_')


if __name__ == '__main__':
    raise SystemExit(main())
