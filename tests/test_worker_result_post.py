#!/usr/bin/env python3
"""A refused result POST names itself; the caller never waits in silence."""
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

_POST_RESULT_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const [backgroundPath, scenario] = process.argv.slice(1);
const messageListeners = [];
const requests = [];
const errors = [];
const timerDelays = [];

function eventTarget(listeners = []) {
  return { addListener: listener => listeners.push(listener) };
}

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const found = {};
        for (const key of keys) {
          if (key === 'daedalus-token') found[key] = 'result-token';
          if (key === 'daedalus-server') {
            found[key] = 'https://bridge.example.com';
          }
        }
        return found;
      },
    },
    onChanged: eventTarget(),
  },
""" + INERT_WORKER_APIS + r"""
};

async function bridgeFetch(target, init = {}) {
  const entry = scenario.plan[
    Math.min(requests.length, scenario.plan.length - 1)];
  requests.push({
    url: String(target), method: init.method,
    body: init.body || null,
    payload: init.body ? JSON.parse(init.body) : null,
  });
  if (entry.networkError !== undefined) {
    throw new Error(entry.networkError);
  }
  return {
    ok: entry.status >= 200 && entry.status < 300,
    status: entry.status,
  };
}

const context = vm.createContext({
  chrome,
  fetch: bridgeFetch,
  DEFAULT_SERVER: 'https://default.example.com',
  _loadSeenDids: async () => {},
  stopStream() {},
  startStream() {},
  bridgeHeaders: (token) => ({ Authorization: 'Bearer ' + token }),
  TextEncoder,
  setTimeout(callback, delay) {
    timerDelays.push(delay);
    setImmediate(callback);
  },
  console: {
    log() {}, warn() {},
    error(...parts) { errors.push(parts.map(String).join(' ')); },
  },
});
""" + import_scripts_stub('context') + r"""

function settle() {
  return new Promise(resolve => setImmediate(resolve));
}

async function run() {
  vm.runInContext("importScripts('worker/config.js');", context,
    { filename: 'extension/background.js (harness)' });
  await settle();
  await vm.runInContext('loadConfig()', context);
  context.scenario = scenario;
  await vm.runInContext(`
    (async () => {
      const command = { id: scenario.commandId };
      if (scenario.did) command._did = scenario.did;
      await postResult(_executionContext(command), scenario.result || null,
        null, scenario.tabId, scenario.extra || {});
    })()
`, context, { filename: 'harness-driver' });
  await settle();
  return { requests, errors, timerDelays };
}

run().then(result => process.stdout.write(JSON.stringify(result)))
  .catch(error => {
    process.stderr.write((error.stack || String(error)) + '\n');
    process.exitCode = 1;
  });
"""


def _post(*plan, command_id='cmd-result-post', did=None, tab_id=None,
          result=None, extra=None):
    """Drive one postResult call through the shipped worker source."""
    scenario = {
        'plan': list(plan), 'commandId': command_id,
        'result': result, 'extra': extra,
    }
    if did:
        scenario['did'] = did
    if tab_id is not None:
        scenario['tabId'] = tab_id
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    outcome = run_node_program(
        node, _POST_RESULT_HARNESS,
        [str(EXTENSION_ROOT / 'background.js')], cwd=ROOT,
        payload=scenario)
    assert outcome.returncode == 0, (
        outcome.returncode, outcome.stdout, outcome.stderr)
    return json.loads(outcome.stdout)


def test_ok_answers_with_one_request_and_no_log(tmp):
    """A 2xx needs exactly the one POST and says nothing."""
    del tmp
    seen = _post({'status': 200})
    assert len(seen['requests']) == 1, seen
    assert seen['requests'][0]['method'] == 'POST', seen
    assert seen['requests'][0]['url'] == 'https://bridge.example.com/result'
    assert seen['errors'] == [], seen
    assert seen['timerDelays'] == [], seen


def test_401_is_named_and_never_retried(tmp):
    """A credential refusal cannot heal, so the caller must hear of it."""
    del tmp
    seen = _post({'status': 401}, did='delivery-refused')
    assert len(seen['requests']) == 1, seen
    assert seen['timerDelays'] == [], seen
    assert len(seen['errors']) == 1, seen
    line = seen['errors'][0]
    for name in ('401', 'cmd-result-post', 'delivery-refused'):
        assert name in line, line


def test_400_is_named_and_never_retried(tmp):
    """A malformed-request refusal is named once, without a delivery id."""
    del tmp
    seen = _post({'status': 400}, command_id='cmd-bad-shape')
    assert len(seen['requests']) == 1, seen
    assert seen['timerDelays'] == [], seen
    assert len(seen['errors']) == 1, seen
    line = seen['errors'][0]
    for name in ('400', 'cmd-bad-shape'):
        assert name in line, line


def test_413_gets_a_substitute_result_in_one_more_post(tmp):
    """The oversized body is the worker's own doing; it answers anyway."""
    del tmp
    seen = _post(
        {'status': 413}, {'status': 200}, did='delivery-413', tab_id='7',
        result='oversized result body é', extra={'world': 'page:example.com'})
    assert len(seen['requests']) == 2, seen
    assert seen['timerDelays'] == [], seen
    refused = seen['requests'][0]
    substitute = seen['requests'][1]['payload']
    assert seen['requests'][1]['url'] == refused['url'], seen
    for key in ('token', 'tabId', 'id', 'world', '_did'):
        assert substitute[key] == refused['payload'][key], (key, seen)
    assert substitute['token'] == 'result-token', seen
    assert substitute['result'] is None, seen
    assert substitute['error'] == {
        'message': 'result too large',
        'size': len(refused['body'].encode('utf-8')),
    }, seen
    assert substitute['ts'] >= refused['payload']['ts'], seen
    assert seen['errors'] == [], seen


def test_5xx_exhaustion_names_the_last_status(tmp):
    """Three refused attempts give up out loud, naming the last status."""
    del tmp
    seen = _post({'status': 503}, {'status': 502}, {'status': 500})
    assert len(seen['requests']) == 3, seen
    assert seen['timerDelays'] == [300, 600, 900], seen
    assert len(seen['errors']) == 1, seen
    line = seen['errors'][0]
    for name in ('500', 'cmd-result-post'):
        assert name in line, line


def test_5xx_retry_that_succeeds_stays_silent(tmp):
    """A restart-window 5xx resolves on retry, without a give-up log."""
    del tmp
    seen = _post({'status': 503}, {'status': 502}, {'status': 200})
    assert len(seen['requests']) == 3, seen
    assert seen['timerDelays'] == [300, 600], seen
    assert seen['errors'] == [], seen


def test_network_error_keeps_its_existing_final_log(tmp):
    """Three dead attempts log the existing line exactly once."""
    del tmp
    seen = _post({'networkError': 'relay down'})
    assert len(seen['requests']) == 3, seen
    assert seen['errors'] == [
        '[Daedalus] Result POST failed: Error: relay down'], seen


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='resultpost_')


if __name__ == '__main__':
    raise SystemExit(main())
