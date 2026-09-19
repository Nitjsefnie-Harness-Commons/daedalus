#!/usr/bin/env python3
"""The worker's close-tab shape validation and per-tab result contract."""
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

TOKEN = 'close-token'
SERVER = 'https://bridge.example.com'

_CLOSE_TAB_HARNESS = (r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, plan] = process.argv.slice(1);
const resultPayloads = [];
const removeCalls = [];
const messageListeners = [];
const storageStore = {
  'daedalus-token': '__TOKEN__',
  'daedalus-server': '__SERVER__',
};

function copy(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
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
    query(_query, callback) {
      if (callback) {
        callback([]);
        return undefined;
      }
      return Promise.resolve([]);
    },
    get: async (tabId) => ({ id: tabId, url: '', title: '' }),
    remove: async (tabId) => {
      removeCalls.push(tabId);
      const rejects = plan.reject || {};
      const message = rejects[String(tabId)];
      if (message !== undefined) throw new Error(message);
    },
    sendMessage: async () => {},
    create(_details, callback) { callback({ id: 101 }); },
  },
""" + INERT_WORKER_APIS + r"""
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

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', context);
  const outcomes = [];
  for (const command of plan.commands || []) {
    context.nextCommand = command;
    try {
      await vm.runInContext('dispatchCommand(nextCommand)', context);
      outcomes.push({ settled: 'resolved' });
    } catch (error) {
      outcomes.push({ settled: 'rejected', message: error.message });
    }
  }
  return {
    removes: removeCalls,
    posted: resultPayloads,
    outcomes,
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""").replace('__TOKEN__', TOKEN).replace('__SERVER__', SERVER)


def _run_close_tab(command, reject=None):
    node = shutil.which('node')
    assert node, 'node is required to execute the worker'
    plan = {'commands': [command]}
    if reject is not None:
        plan['reject'] = reject
    result = run_node_program(
        node, _CLOSE_TAB_HARNESS,
        [str(EXTENSION_ROOT / 'background.js')], cwd=ROOT, payload=plan)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _command(**fields):
    command = {'id': 'close-1', 'type': 'close-tab', '_did': 'did-close'}
    command.update(fields)
    return command


def _assert_wrong_shape(value):
    outcome = _run_close_tab(_command(tabIds=value))
    assert outcome == {
        'removes': [],
        'posted': [{
            'id': 'close-1',
            'tabId': 'extension',
            'result': None,
            'error': 'tabIds must be an array',
        }],
        'outcomes': [{'settled': 'resolved'}],
    }, outcome


def test_string_tab_ids_is_rejected_without_removing_tabs(tmp):
    del tmp
    _assert_wrong_shape('7')


def test_zero_tab_ids_is_rejected_as_present_wrong_shape(tmp):
    del tmp
    _assert_wrong_shape(0)


def test_false_tab_ids_is_rejected_as_present_wrong_shape(tmp):
    del tmp
    _assert_wrong_shape(False)


def test_true_tab_ids_is_rejected_as_present_wrong_shape(tmp):
    del tmp
    _assert_wrong_shape(True)


def test_nonzero_tab_ids_is_rejected_as_present_wrong_shape(tmp):
    del tmp
    _assert_wrong_shape(7)


def test_object_tab_ids_is_rejected_without_removing_tabs(tmp):
    del tmp
    _assert_wrong_shape({})


def test_wrong_shape_tab_ids_is_rejected_even_with_tab_id(tmp):
    del tmp
    outcome = _run_close_tab(_command(tabId=5, tabIds='x'))
    assert outcome['removes'] == [], outcome
    assert outcome['outcomes'] == [{'settled': 'resolved'}], outcome
    assert outcome['posted'] == [{
        'id': 'close-1',
        'tabId': 'extension',
        'result': None,
        'error': 'tabIds must be an array',
    }], outcome


def test_mixed_type_tab_ids_are_parsed_and_closed_in_order(tmp):
    del tmp
    outcome = _run_close_tab(_command(tabIds=[1, '2']))
    assert outcome['removes'] == [1, 2], outcome
    assert outcome['outcomes'] == [{'settled': 'resolved'}], outcome
    assert outcome['posted'] == [{
        'id': 'close-1',
        'tabId': 'extension',
        'result': {'closed': [1, 2], 'errors': []},
        'error': None,
    }], outcome


def test_empty_tab_ids_answers_empty_close_result(tmp):
    del tmp
    outcome = _run_close_tab(_command(tabIds=[]))
    assert outcome['removes'] == [], outcome
    assert outcome['posted'] == [{
        'id': 'close-1',
        'tabId': 'extension',
        'result': {'closed': [], 'errors': []},
        'error': None,
    }], outcome


def test_tab_id_alone_is_closed(tmp):
    del tmp
    outcome = _run_close_tab(_command(tabId=5))
    assert outcome['removes'] == [5], outcome
    assert outcome['posted'] == [{
        'id': 'close-1',
        'tabId': 'extension',
        'result': {'closed': [5], 'errors': []},
        'error': None,
    }], outcome


def test_null_tab_ids_falls_back_to_tab_id(tmp):
    del tmp
    outcome = _run_close_tab(_command(tabId=5, tabIds=None))
    assert outcome['removes'] == [5], outcome
    assert outcome['posted'] == [{
        'id': 'close-1',
        'tabId': 'extension',
        'result': {'closed': [5], 'errors': []},
        'error': None,
    }], outcome


def test_missing_tab_ids_and_tab_id_answers_missing_error(tmp):
    del tmp
    outcome = _run_close_tab(_command())
    assert outcome['removes'] == [], outcome
    assert outcome['posted'] == [{
        'id': 'close-1',
        'tabId': 'extension',
        'result': None,
        'error': 'Missing tabId or tabIds',
    }], outcome


def test_one_remove_error_is_reported_while_other_tabs_close(tmp):
    del tmp
    outcome = _run_close_tab(
        _command(tabIds=[1, '2', 3]), reject={'2': 'cannot close 2'})
    assert outcome['removes'] == [1, 2, 3], outcome
    assert outcome['outcomes'] == [{'settled': 'resolved'}], outcome
    assert outcome['posted'] == [{
        'id': 'close-1',
        'tabId': 'extension',
        'result': {
            'closed': [1, 3],
            'errors': [{'id': 2, 'error': 'cannot close 2'}],
        },
        'error': None,
    }], outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='closetab_')


if __name__ == '__main__':
    raise SystemExit(main())
