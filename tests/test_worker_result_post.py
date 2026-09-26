#!/usr/bin/env python3
"""A refused result POST names itself; the caller never waits in silence."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_gate)
from _worker_chrome_fake import INERT_WORKER_APIS  # noqa: E402
from _worker_sources import (  # noqa: E402
    event_target_stub, import_scripts_stub)

RESULT = 'POST /result'

_POST_RESULT_HARNESS = event_target_stub() + r"""
const fs = require('fs');
// The bridge the worker is configured against; the gate permits this origin.
const SERVER = 'https://bridge.example.com';
const vm = require('vm');
const [backgroundPath] = process.argv.slice(1);
const scenario = __SCENARIO__;
// The plan rides last in both launches (the file launcher splices it in as an
// object literal, this one appends it as JSON text), so the gate reads it from
// the last entry and parses only when it arrived as text.
const gatePlanArg = process.argv[process.argv.length - 1];
const plan = typeof gatePlanArg === 'string'
  ? JSON.parse(gatePlanArg) : gatePlanArg;
// backgroundPath is never read as a script here; it is the free identifier
// import_scripts_stub resolves 'worker/config.js' through (its directory).
// Renaming it crashes every test in this file.
const messageListeners = [];
const errors = [];
const timerDelays = [];

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const found = {};
        for (const key of keys) {
          if (key === 'daedalus-token') found[key] = 'result-token';
          if (key === 'daedalus-server') {
            found[key] = SERVER;
          }
        }
        return found;
      },
    },
    onChanged: eventTarget(),
  },
""" + INERT_WORKER_APIS + r"""
};

// The shared gate's in-scope contract. The gate answers only what the
// scenario declared and records every request it sees; the answers the
// worker sees are the per-attempt sequence the plan declares, and a request
// past the declared count is a recorded 599 refusal, never a re-answer of
// the last row.
const BRIDGE_URL = SERVER;
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];
function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}
function streamResponse(answer) {
  return response(answer, { error: 'disabled' });
}
""" + STRICT_FETCH + r"""

// A thin wrapper that DELEGATES to the gate: the answer, the accounting, the
// refusal and the record are the gate's. It keeps the raw body text the suite
// measures the refused result's size against, which the gate's record (a
// parsed body) cannot carry.
const rawBodies = [];
async function resultPostFetch(target, init = {}) {
  rawBodies.push(init.body || null);
  return bridgeFetch(target, init);
}

const context = vm.createContext({
  chrome,
  fetch: resultPostFetch,
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
  return {
    requests: nonStreamFetches.map((record, index) => ({
      url: record.url, method: 'POST', body: rawBodies[index],
      payload: record.body,
    })),
    errors,
    timerDelays,
    gate: {
      records: nonStreamFetches,
      refused: refusedFetches,
      badOrigins,
      streamAnswered: streamFetches.map((f) => f.answered),
      contractFaults: gateContractFaults,
    },
  };
}

run().then(result => process.stdout.write(JSON.stringify(result)))
  .catch(error => {
    process.stderr.write((error.stack || String(error)) + '\n');
    process.exitCode = 1;
  });
"""
assert '__SCENARIO__' in _POST_RESULT_HARNESS


def _post(*answers, command_id='cmd-result-post', did=None, tab_id=None,
          result=None, extra=None):
    """Drive one postResult call through the shipped worker source.

    The plan declares exactly as many requests as there are answers, so a
    request the worker invents — or a fourth attempt the plan did not
    declare — is refused and recorded, never served the last row again.
    """
    scenario = {
        'commandId': command_id,
        'result': result, 'extra': extra,
    }
    if did:
        scenario['did'] = did
    if tab_id is not None:
        scenario['tabId'] = tab_id
    plan = {
        'planned': [RESULT] * len(answers),
        'answers': {RESULT: list(answers)},
    }
    harness = _POST_RESULT_HARNESS.replace(
        '__SCENARIO__', json.dumps(scenario))
    outcome = run_gate(require_node(), harness,
                       [str(EXTENSION_ROOT / 'background.js')], cwd=ROOT,
                       plan=plan)
    gate = outcome.pop('gate')
    assert_gate_clean(
        contract_faults=gate['contractFaults'],
        records=gate['records'], refused=gate['refused'],
        bad_origins=gate['badOrigins'],
        stream_answered=gate['streamAnswered'],
        planned=list(plan['planned']), planned_stream=[])
    return outcome


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


def test_a_refused_substitute_is_logged_once(tmp):
    """A substitute that is refused for another reason still names itself."""
    del tmp
    seen = _post({'status': 413}, {'status': 503})
    assert len(seen['requests']) == 2, seen
    assert seen['timerDelays'] == [], seen
    assert len(seen['errors']) == 1, seen
    line = seen['errors'][0]
    for name in ('503', 'cmd-result-post'):
        assert name in line, line


def test_a_dead_substitute_network_error_is_logged_once(tmp):
    """A substitute that cannot reach the bridge names that too."""
    del tmp
    seen = _post({'status': 413}, {'throw': 'substitute down'})
    assert len(seen['requests']) == 2, seen
    assert seen['errors'] == [
        '[Daedalus] Substitute result POST failed: TypeError: substitute down',
    ], seen


def test_413_on_the_last_attempt_after_5xx_still_substitutes(tmp):
    """A late 413 gets its substitute once the 5xx retries are spent."""
    del tmp
    seen = _post(
        {'status': 503}, {'status': 502}, {'status': 413}, {'status': 200},
        did='delivery-413-late', tab_id='7',
        result='late oversized é body', extra={'world': 'page:example.com'})
    assert len(seen['requests']) == 4, seen
    assert seen['timerDelays'] == [300, 600], seen
    assert seen['errors'] == [], seen
    refused = seen['requests'][2]
    substitute = seen['requests'][3]['payload']
    assert seen['requests'][3]['url'] == refused['url'], seen
    for key in ('token', 'tabId', 'id', 'world', '_did'):
        assert substitute[key] == refused['payload'][key], (key, seen)
    assert substitute['result'] is None, seen
    assert substitute['error'] == {
        'message': 'result too large',
        'size': len(refused['body'].encode('utf-8')),
    }, seen
    assert substitute['ts'] >= refused['payload']['ts'], seen


def test_network_error_after_5xx_logs_exactly_once(tmp):
    """The catch's status reset keeps the give-up log from doubling."""
    del tmp
    seen = _post(
        {'status': 503}, {'status': 503}, {'throw': 'relay down'})
    assert len(seen['requests']) == 3, seen
    assert seen['timerDelays'] == [300, 600, 900], seen
    assert seen['errors'] == [
        '[Daedalus] Result POST failed: TypeError: relay down'], seen


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
    # Three attempts, as the worker's own retry loop makes them.
    seen = _post({'throw': 'relay down'}, {'throw': 'relay down'},
                 {'throw': 'relay down'})
    assert len(seen['requests']) == 3, seen
    assert seen['errors'] == [
        '[Daedalus] Result POST failed: TypeError: relay down'], seen


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='resultpost_')


if __name__ == '__main__':
    raise SystemExit(main())
