#!/usr/bin/env python3
"""GM.setValue and its neighbours: what the page may store, and what it may not.

The storage relay is the one GM surface a page can drive with arbitrary keys,
so these run the shipped content and page scripts in a Node VM and pin what
the relay refuses before storage is touched — and that a write Chrome refused
rejects rather than resolving, since Chrome reports that only through
lastError.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_STORAGE_RELAY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [contentPath, pagePath] = process.argv.slice(1);
const listeners = {};
const messages = [];
const posted = [];
const storageCalls = [];
const store = Object.create(null);

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    posted.push(message);
    messages.push(message);
  },
};

function storedValues(keys) {
  const values = {};
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(store, key)) values[key] = store[key];
  }
  return values;
}

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage() {},
    getManifest() { return { version: '0.18.0' }; },
    connect() {
      return {
        disconnect() {},
        postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: {
    local: {
      get(keys, callback) {
        storageCalls.push('get');
        callback(keys === null ? { ...store } : storedValues(keys));
      },
      set(values, callback) {
        storageCalls.push('set');
        Object.assign(store, values);
        callback();
      },
      remove(keys, callback) {
        storageCalls.push('remove');
        for (const key of keys) delete store[key];
        callback();
      },
    },
  },
};

const context = {
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: 'storage-test.invalid' },
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  console: { log() {}, error() {} },
};
vm.runInNewContext(
  fs.readFileSync(contentPath, 'utf8'), context,
  { filename: contentPath });
vm.runInNewContext(
  fs.readFileSync(pagePath, 'utf8'), context,
  { filename: pagePath });

function reset(initial = {}) {
  for (const key of Reflect.ownKeys(store)) delete store[key];
  Object.assign(store, initial);
  messages.length = 0;
  posted.length = 0;
  storageCalls.length = 0;
}

function flushMessages() {
  while (messages.length) {
    const data = messages.shift();
    for (const listener of listeners.message) {
      listener({ source: windowObject, data });
    }
  }
}

function responseFor(reqId) {
  return posted.find((message) =>
    message.direction === 'daedalus-bg-to-page' && message.reqId === reqId);
}

let directReqId = 1000;
function dispatch(handler, key, initial = {}) {
  reset(initial);
  const reqId = ++directReqId;
  const data = {
    direction: 'daedalus-page-to-bg',
    reqId,
    handler,
    key,
    value: 'attacker',
    defaultValue: 'default',
  };
  for (const listener of listeners.message) {
    listener({ source: windowObject, data });
  }
  flushMessages();
  const response = responseFor(reqId);
  return {
    error: response && response.error || null,
    value: response && response.value,
    calls: [...storageCalls],
    storedKeys: Object.keys(store).sort(),
    protectedValue: store['daedalus-server'],
  };
}

async function gmSet(label, key) {
  reset();
  const pending = windowObject.GM.setValue(key, 'attacker');
  flushMessages();
  let status = 'resolved';
  let error = null;
  try {
    await pending;
  } catch (caught) {
    status = 'rejected';
    error = caught && caught.message || String(caught);
  }
  return {
    label,
    status,
    error,
    calls: [...storageCalls],
    storedKeys: Object.keys(store).sort(),
  };
}

async function main() {
  const gmSetCases = [];
  for (const key of ['daedalus-server', 'daedalus-hotfixes', 'daedalus-token']) {
    gmSetCases.push(await gmSet(`array:${key}`, [key]));
  }
  gmSetCases.push(await gmSet('string:daedalus-server', 'daedalus-server'));
  gmSetCases.push(await gmSet('string:ordinary', 'ordinary'));

  const invalidHandlers = {};
  for (const handler of ['getValue', 'setValue', 'deleteValue']) {
    invalidHandlers[handler] = dispatch(
      handler, ['daedalus-server'], { 'daedalus-server': 'protected' });
  }

  const coercibleKeys = {
    number: 7,
    object: { toString() { return 'daedalus-server'; } },
    nestedArray: [['daedalus-server']],
    symbol: Symbol('daedalus-server'),
  };
  const coercible = {};
  for (const [label, key] of Object.entries(coercibleKeys)) {
    coercible[label] = dispatch(
      'setValue', key, { 'daedalus-server': 'protected' });
  }

  const ordinaryHandlers = {
    getValue: dispatch('getValue', 'ordinary', { ordinary: 'kept' }),
    setValue: dispatch('setValue', 'ordinary'),
    deleteValue: dispatch('deleteValue', 'ordinary', { ordinary: 'remove-me' }),
  };

  reset({
    ordinary: 'visible',
    'daedalus-server': 'hidden',
    'daedalus-hotfixes': 'hidden',
    'daedalus-token': 'hidden',
  });
  const listPending = windowObject.GM.listValues();
  flushMessages();
  const listed = await listPending;

  process.stdout.write(JSON.stringify({
    gmSetCases,
    invalidHandlers,
    coercible,
    ordinaryHandlers,
    listValues: { keys: listed, calls: [...storageCalls] },
  }));
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
"""


_STORAGE_FAILURE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

// Every chrome.storage call fails the way Chrome fails one: the callback is
// invoked exactly as on success, the store is left alone, and the only trace
// is chrome.runtime.lastError — which Chrome clears once the callback
// returns, so it is set around the call and cleared after it.
const [contentPath, pagePath] = process.argv.slice(1);
const FAILURE = 'QUOTA_BYTES quota exceeded';
const listeners = {};
const messages = [];

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    messages.push(message);
  },
};

function failing(callback, value) {
  chrome.runtime.lastError = { message: FAILURE };
  try {
    callback(value);
  } finally {
    chrome.runtime.lastError = null;
  }
}

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage() {},
    getManifest() { return { version: '0.18.0' }; },
    connect() {
      return {
        disconnect() {},
        postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: {
    local: {
      get(keys, callback) { failing(callback, {}); },
      set(values, callback) { failing(callback); },
      remove(keys, callback) { failing(callback); },
    },
  },
};

const context = {
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: 'storage-failure.invalid' },
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  console: { log() {}, error() {} },
};
vm.runInNewContext(
  fs.readFileSync(contentPath, 'utf8'), context,
  { filename: contentPath });
vm.runInNewContext(
  fs.readFileSync(pagePath, 'utf8'), context,
  { filename: pagePath });

function flushMessages() {
  while (messages.length) {
    const data = messages.shift();
    for (const listener of listeners.message) {
      listener({ source: windowObject, data });
    }
  }
}

const outcomes = {};
const settled = [];
for (const [name, call] of [
  ['getValue', () => windowObject.GM.getValue('ordinary', 'fallback')],
  ['setValue', () => windowObject.GM.setValue('ordinary', 'value')],
  ['deleteValue', () => windowObject.GM.deleteValue('ordinary')],
  ['listValues', () => windowObject.GM.listValues()],
]) {
  settled.push(call().then(
    (value) => { outcomes[name] = { settled: 'resolved', value: value ?? null }; },
    (error) => { outcomes[name] = { settled: 'rejected', error: String(error && error.message) }; },
  ));
}
flushMessages();

Promise.all(settled).then(() => {
  process.stdout.write(JSON.stringify(outcomes), () => process.exit(0));
});
"""


def _run_storage_failure_harness():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension storage boundary'
    result = subprocess.run(
        [node, '-e', _STORAGE_FAILURE_HARNESS,
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_failed_storage_write_rejects_instead_of_resolving(tmp):
    """Chrome reports a storage failure only through lastError.

    The callbacks fire on failure exactly as on success, with the store
    unchanged, so a relay that did not read chrome.runtime.lastError could
    not tell the two apart — GM.setValue resolved successfully having stored
    nothing, and the page had no way to find out.
    """
    del tmp
    outcomes = _run_storage_failure_harness()
    assert set(outcomes) == {
        'getValue', 'setValue', 'deleteValue', 'listValues'}, outcomes
    for name, outcome in sorted(outcomes.items()):
        assert outcome['settled'] == 'rejected', (name, outcome)
        assert 'QUOTA_BYTES quota exceeded' in outcome['error'], (name, outcome)


def _run_storage_relay_harness():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension storage boundary'
    result = subprocess.run(
        [node, '-e', _STORAGE_RELAY_HARNESS,
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_page_storage_rejects_coercible_reserved_keys(tmp):
    """GM.setValue rejects coercible reserved keys before storage is called."""
    result = _run_storage_relay_harness()
    cases = {case['label']: case for case in result['gmSetCases']}
    for key in ('daedalus-server', 'daedalus-hotfixes', 'daedalus-token'):
        assert cases[f'array:{key}'] == {
            'label': f'array:{key}',
            'status': 'rejected',
            'error': 'invalid key',
            'calls': [],
            'storedKeys': [],
        }, cases[f'array:{key}']
    assert cases['string:daedalus-server'] == {
        'label': 'string:daedalus-server',
        'status': 'rejected',
        'error': 'reserved key',
        'calls': [],
        'storedKeys': [],
    }, cases['string:daedalus-server']
    assert cases['string:ordinary'] == {
        'label': 'string:ordinary',
        'status': 'resolved',
        'error': None,
        'calls': ['set'],
        'storedKeys': ['ordinary'],
    }, cases['string:ordinary']


def test_page_storage_validates_every_keyed_handler(tmp):
    """Every keyed relay rejects non-strings without touching storage."""
    result = _run_storage_relay_harness()
    for handler in ('getValue', 'setValue', 'deleteValue'):
        case = result['invalidHandlers'][handler]
        assert case['error'] == 'invalid key', (handler, case)
        assert case['calls'] == [], (handler, case)
        assert case['storedKeys'] == ['daedalus-server'], (handler, case)
        assert case['protectedValue'] == 'protected', (handler, case)
    for label, case in result['coercible'].items():
        assert case['error'] == 'invalid key', (label, case)
        assert case['calls'] == [], (label, case)
        assert case['protectedValue'] == 'protected', (label, case)


def test_page_storage_allows_string_keys_and_filters_list_values(tmp):
    """Ordinary strings work and listValues omits every reserved string key."""
    result = _run_storage_relay_harness()
    handlers = result['ordinaryHandlers']
    assert handlers['getValue']['value'] == 'kept', handlers['getValue']
    assert handlers['getValue']['calls'] == ['get'], handlers['getValue']
    assert handlers['setValue']['storedKeys'] == ['ordinary'], handlers['setValue']
    assert handlers['setValue']['calls'] == ['set'], handlers['setValue']
    assert handlers['deleteValue']['storedKeys'] == [], handlers['deleteValue']
    assert handlers['deleteValue']['calls'] == ['remove'], handlers['deleteValue']
    assert result['listValues'] == {
        'keys': ['ordinary'],
        'calls': ['get'],
    }, result['listValues']


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gmstorage_')


if __name__ == '__main__':
    raise SystemExit(main())
