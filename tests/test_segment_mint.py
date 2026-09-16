#!/usr/bin/env python3
"""The worker's origin-allowlisted segment-job mint and its allowlist.

A page reaches `POST /segment-job` only through the service worker, and the
worker mints only for an origin an operator has allowed. These run the
shipped worker in a Node VM against a fake browser that records every fetch
the worker makes, every result it posts, and what it leaves in storage.
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary_env import run_node_program  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402

ORIGINS_KEY = 'daedalus-segment-origins'
ALLOWED = 'https://allowed.example.com'
OTHER = 'https://other.example.com'
SERVER = 'https://bridge.example.com'
TOKEN = 'mint-token'
INVALID_ORIGIN = 'Missing or invalid origin (http(s) origin required)'

_MINT_HARNESS = (r"""
const fs = require('fs');
const vm = require('vm');

// run_node_program pushes the payload as an object literal, not text.
const [backgroundPath, plan] = process.argv.slice(1);
const messageListeners = [];
const fetches = [];
const resultPayloads = [];
let storageBroken = false;
const storageStore = Object.assign({
  'daedalus-token': '__TOKEN__',
  'daedalus-server': '__SERVER__',
}, plan.store || {});

function copy(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => {
      if (data === null) throw new SyntaxError('not JSON');
      return data;
    },
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

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        if (storageBroken) throw new Error('storage unavailable');
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
  scripting: {
    executeScript: async () => { throw new Error('unavailable'); },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => { throw new Error('unavailable'); },
    detach: async () => {},
    sendCommand: async () => ({}),
  },
  runtime: {
    lastError: null,
    onMessage: eventTarget(messageListeners),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.0.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
};

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.endsWith('/result') && init.method === 'POST') {
    resultPayloads.push(JSON.parse(init.body));
    return response(200, { ok: true });
  }
  if (url.includes('/stream?')) return response(503, { error: 'disabled' });
  // The tab registry's control plane runs at boot; it is not under test.
  if (/\/(register|sync-tabs|unregister)$/.test(url)) {
    return response(200, { ok: true });
  }
  fetches.push({
    url,
    method: init.method || null,
    headers: init.headers || {},
    body: init.body === undefined ? null : init.body,
  });
  const bridge = plan.bridge || { status: 200, body: { ok: true } };
  if (bridge.throw) throw new Error(bridge.throw);
  return response(bridge.status, bridge.body);
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

// One message, one answer, as content.js relays for a page. The sender is
// the plan's, because what the worker trusts is exactly what Chrome puts
// there — a test that supplied its own tab URL would be testing itself.
function send(message) {
  return new Promise((resolve, reject) => {
    try {
      let answered = false;
      for (const listener of messageListeners) {
        listener(message, plan.sender, (answer) => {
          answered = true;
          resolve(answer);
        });
      }
      setImmediate(() => {
        if (!answered) reject(new Error('no listener answered'));
      });
    } catch (error) {
      reject(error);
    }
  });
}

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);
  storageBroken = Boolean(plan.storageThrows);
  const answers = [];
  for (const message of plan.messages || []) {
    answers.push(await send(message));
  }
  context.plannedCommands = plan.commands || [];
  for (let i = 0; i < context.plannedCommands.length; i++) {
    await vm.runInContext(
      'dispatchCommand(plannedCommands[' + i + '])', context);
  }
  context.concurrentCommands = plan.concurrent || [];
  await vm.runInContext(
    'Promise.all(concurrentCommands.map(dispatchCommand))', context);
  const stored = storageStore['__ORIGINS_KEY__'];
  return {
    fetches,
    answers,
    posted: resultPayloads.map((item) => ({
      id: item.id, result: item.result, error: item.error,
    })),
    stored: stored === undefined ? null : stored,
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""").replace('__TOKEN__', TOKEN).replace('__SERVER__', SERVER).replace(
    '__ORIGINS_KEY__', ORIGINS_KEY)


def _run_mint(plan):
    """Drive the worker under Node with one plan and read back."""
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    result = run_node_program(
        node, _MINT_HARNESS, [str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, payload=plan)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _sender(origin=ALLOWED):
    sender = {'tab': {'id': 7}, 'url': ALLOWED + '/play'}
    if origin is not None:
        sender['origin'] = origin
    return sender


def _mint_plan(allowlist, sender, message, bridge=None, **extra):
    plan = {
        'store': {} if allowlist is None else {ORIGINS_KEY: allowlist},
        'sender': sender,
        'messages': [message],
    }
    if bridge is not None:
        plan['bridge'] = bridge
    plan.update(extra)
    return plan


def _command(command_type, **fields):
    command = {'id': 'x', 'type': command_type, '_did': 'did-x'}
    command.update(fields)
    return command


def _refused(outcome, message):
    """A refusal is exactly `{error}` and never reached the bridge."""
    assert outcome['fetches'] == [], outcome
    assert len(outcome['answers']) == 1, outcome
    answer = outcome['answers'][0]
    assert list(answer) == ['error'], answer
    assert answer['error'] == message, answer


def test_a_listed_origin_gets_a_sig_through_one_bridge_post(tmp):
    """The round trip: allowlisted sender, one POST, answer exactly {sig}."""
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
        bridge={'status': 200, 'body': {'ok': True, 'sig': 'SIG1'}}))
    assert len(outcome['fetches']) == 1, outcome
    request = outcome['fetches'][0]
    assert request['url'] == SERVER + '/segment-job', request
    assert request['method'] == 'POST', request
    assert request['headers']['Authorization'] == 'Bearer ' + TOKEN, request
    assert json.loads(request['body']) == {
        'token': TOKEN, 'job': 'job_1'}, request
    assert outcome['answers'] == [{'sig': 'SIG1'}], outcome
    assert list(outcome['answers'][0]) == ['sig'], outcome


def test_an_unlisted_origin_is_refused_without_a_fetch(tmp):
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(OTHER), {'type': 'segmentJob', 'job': 'job_1'}))
    _refused(outcome, 'origin not allowed')


def test_an_empty_or_absent_allowlist_refuses_every_origin(tmp):
    del tmp
    for allowlist in ([], None):
        outcome = _run_mint(_mint_plan(
            allowlist, _sender(), {'type': 'segmentJob', 'job': 'job_1'}))
        _refused(outcome, 'origin not allowed')


def test_a_sender_without_an_origin_is_refused(tmp):
    """No fallback to the sender's tab or URL: absent origin fails closed."""
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(None), {'type': 'segmentJob', 'job': 'job_1'}))
    _refused(outcome, 'origin not allowed')


def test_a_stored_entry_is_compared_verbatim_never_canonicalised(tmp):
    """The store is canonical by construction; the reader trusts it as is.

    Chrome sends canonical origins, so a non-canonical spelling can only be
    on the stored side. An entry with a path or an uppercase host is not
    what the sender's origin will ever equal, and the reader must not
    rescue it by canonicalising what it read.
    """
    del tmp
    for spelling in ('https://Allowed.example.com', ALLOWED + '/',
                     ALLOWED + '/play'):
        outcome = _run_mint(_mint_plan(
            [spelling], _sender(), {'type': 'segmentJob', 'job': 'job_1'}))
        _refused(outcome, 'origin not allowed')


def test_a_missing_empty_or_non_string_job_is_refused(tmp):
    del tmp
    for message in ({'type': 'segmentJob'},
                    {'type': 'segmentJob', 'job': ''},
                    {'type': 'segmentJob', 'job': 42},
                    {'type': 'segmentJob', 'job': ['job_1']}):
        outcome = _run_mint(_mint_plan([ALLOWED], _sender(), message))
        _refused(outcome, 'Missing job')


def test_a_bridge_refusal_is_reported_with_its_status(tmp):
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
        bridge={'status': 403, 'body': {'error': 'forbidden'}}))
    assert len(outcome['fetches']) == 1, outcome
    assert outcome['answers'] == [{'error': 'segment-job refused (403)'}], (
        outcome)


def test_a_bridge_answer_without_a_sig_is_an_error(tmp):
    del tmp
    for body in ({'ok': True}, {'ok': True, 'sig': ''},
                 {'ok': True, 'sig': 7}, None):
        outcome = _run_mint(_mint_plan(
            [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
            bridge={'status': 200, 'body': body}))
        assert len(outcome['fetches']) == 1, outcome
        assert outcome['answers'] == [
            {'error': 'segment-job answered no sig'}], (body, outcome)


def test_an_unreachable_bridge_is_reported_with_its_reason(tmp):
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
        bridge={'throw': 'Failed to fetch'}))
    assert outcome['answers'] == [
        {'error': 'segment-job unreachable: Failed to fetch'}], outcome


def test_a_storage_failure_answers_an_error_not_silence(tmp):
    """A throwing store still answers: a page waiting forever is the bug."""
    del tmp
    outcome = _run_mint(_mint_plan(
        [ALLOWED], _sender(), {'type': 'segmentJob', 'job': 'job_1'},
        storageThrows=True))
    assert outcome['fetches'] == [], outcome
    assert len(outcome['answers']) == 1, outcome
    assert list(outcome['answers'][0]) == ['error'], outcome


def _commands(commands, store=None, concurrent=None):
    plan = {'store': store or {}, 'commands': commands}
    if concurrent:
        plan['concurrent'] = concurrent
    return _run_mint(plan)


def test_allow_canonicalises_and_reports_the_addition(tmp):
    del tmp
    outcome = _commands([_command(
        'allow-segment-origin',
        origin='https://Allowed.example.com/some/path?q=1')])
    assert outcome['posted'] == [{
        'id': 'x', 'error': None,
        'result': {'origin': ALLOWED, 'origins': [ALLOWED], 'added': True},
    }], outcome
    assert outcome['stored'] == [ALLOWED], outcome


def test_allowing_a_listed_origin_again_changes_nothing(tmp):
    del tmp
    outcome = _commands(
        [_command('allow-segment-origin', origin=ALLOWED + '/')],
        store={ORIGINS_KEY: [ALLOWED]})
    assert outcome['posted'] == [{
        'id': 'x', 'error': None,
        'result': {'origin': ALLOWED, 'origins': [ALLOWED], 'added': False},
    }], outcome
    assert outcome['stored'] == [ALLOWED], outcome


def test_allow_refuses_anything_but_an_http_origin(tmp):
    """Each refusal names the exact error and leaves the store as it was."""
    del tmp
    cases = [{'origin': value} for value in (
        'ftp://x', 'allowed.example.com', 'chrome-extension://abc', '', 42)]
    cases.append({})
    for fields in cases:
        outcome = _commands(
            [_command('allow-segment-origin', **fields)],
            store={ORIGINS_KEY: [ALLOWED]})
        assert outcome['posted'] == [{
            'id': 'x', 'result': None, 'error': INVALID_ORIGIN,
        }], (fields, outcome)
        assert outcome['stored'] == [ALLOWED], (fields, outcome)
        # The same refusal on an empty store creates nothing.
        outcome = _commands([_command('allow-segment-origin', **fields)])
        assert outcome['posted'][0]['error'] == INVALID_ORIGIN, (
            fields, outcome)
        assert outcome['stored'] is None, (fields, outcome)


def test_revoke_removes_a_listed_origin_and_reports_found(tmp):
    del tmp
    outcome = _commands(
        [_command('revoke-segment-origin', origin=ALLOWED + '/x')],
        store={ORIGINS_KEY: [ALLOWED, OTHER]})
    assert outcome['posted'] == [{
        'id': 'x', 'error': None,
        'result': {'origin': ALLOWED, 'origins': [OTHER], 'found': True},
    }], outcome
    assert outcome['stored'] == [OTHER], outcome


def test_revoking_an_absent_origin_reports_not_found(tmp):
    del tmp
    outcome = _commands(
        [_command('revoke-segment-origin', origin=OTHER)],
        store={ORIGINS_KEY: [ALLOWED]})
    assert outcome['posted'] == [{
        'id': 'x', 'error': None,
        'result': {'origin': OTHER, 'origins': [ALLOWED], 'found': False},
    }], outcome
    assert outcome['stored'] == [ALLOWED], outcome


def test_revoke_refuses_an_invalid_origin(tmp):
    del tmp
    for fields in ({'origin': 'ftp://x'}, {'origin': ''}, {}):
        outcome = _commands(
            [_command('revoke-segment-origin', **fields)],
            store={ORIGINS_KEY: [ALLOWED]})
        assert outcome['posted'] == [{
            'id': 'x', 'result': None, 'error': INVALID_ORIGIN,
        }], (fields, outcome)
        assert outcome['stored'] == [ALLOWED], (fields, outcome)


def test_list_reports_the_stored_origins(tmp):
    del tmp
    outcome = _commands(
        [_command('list-segment-origins')],
        store={ORIGINS_KEY: [ALLOWED, OTHER]})
    assert outcome['posted'] == [{
        'id': 'x', 'error': None, 'result': {'origins': [ALLOWED, OTHER]},
    }], outcome
    outcome = _commands([_command('list-segment-origins')])
    assert outcome['posted'] == [{
        'id': 'x', 'error': None, 'result': {'origins': []},
    }], outcome


def test_origins_added_out_of_order_are_stored_sorted(tmp):
    del tmp
    outcome = _commands([
        _command('allow-segment-origin', origin=OTHER, id='first'),
        _command('allow-segment-origin', origin=ALLOWED, id='second'),
    ])
    assert outcome['stored'] == [ALLOWED, OTHER], outcome
    assert outcome['posted'][1]['result']['origins'] == [ALLOWED, OTHER], (
        outcome)


def test_two_allows_in_flight_at_once_both_land(tmp):
    """Two read-modify-write cycles dispatched together must serialise.

    Without the lock both read the empty store, both answer `added`, and
    only the later write survives.
    """
    del tmp
    outcome = _commands([], concurrent=[
        _command('allow-segment-origin', origin=ALLOWED, id='a'),
        _command('allow-segment-origin', origin=OTHER, id='b'),
    ])
    assert outcome['stored'] == [ALLOWED, OTHER], outcome
    assert sorted(item['id'] for item in outcome['posted']) == ['a', 'b'], (
        outcome)
    assert all(item['error'] is None for item in outcome['posted']), outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='segmentmint_')


if __name__ == '__main__':
    raise SystemExit(main())
