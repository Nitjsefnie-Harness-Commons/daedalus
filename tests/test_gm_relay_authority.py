#!/usr/bin/env python3
"""What authority a page gets through the GM relay, and what it does not.

The shim is injected into every matching page, so the service worker's
relay answers to any site the user visits, not to a userscript the user
installed. What the worker does on the page's behalf is therefore bounded
here: a relayed request goes out without the user's cookies. These run the
shipped worker in a Node VM against a fake browser that records what the
worker asked it to do.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402


_RELAY_AUTHORITY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, mode] = process.argv.slice(1);
const messageListeners = [];
const fetches = [];

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
      get: async () => ({
        'daedalus-token': 'relay-token',
        'daedalus-server': '',
      }),
      set: async () => {},
      remove: async () => {},
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

const context = vm.createContext({
  chrome,
  fetch: async (target, init = {}) => {
    fetches.push({
      url: String(target),
      method: init.method || null,
      credentials: init.credentials === undefined ? null : init.credentials,
    });
    return {
      ok: true, status: 200, statusText: 'OK', url: String(target),
      headers: { forEach() {} }, body: null,
    };
  },
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

// One message, one answer, as content.js relays for a page. A handler that
// throws instead of answering rejects here, and the rejection is recorded
// rather than hidden: from the page, a callback that throws is
// indistinguishable from an answer that never came.
function send(message) {
  return new Promise((resolve, reject) => {
    try {
      for (const listener of messageListeners) {
        listener(message, { tab: { id: 7 } }, resolve);
      }
    } catch (error) {
      reject(error);
    }
  });
}

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  if (mode === 'fetch') {
    const answer = await send({
      type: 'fetch', fetchId: 'page-1', url: 'https://example.com/account',
      method: 'POST', headers: {}, body: '{}', responseType: 'text',
    });
    return {
      fetches,
      answer: {
        status: answer.status === undefined ? null : answer.status,
        error: answer.error || null,
      },
    };
  }
  throw new Error('unknown mode: ' + mode);
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def _run_relay_authority(mode):
    """Drive the worker's page-facing relay under Node and read back."""
    node = shutil.which('node')
    assert node, 'node is required to execute the GM relay'
    result = subprocess.run(
        [node, '-e', _RELAY_AUTHORITY_HARNESS,
         str(EXTENSION_ROOT / 'background.js'), mode],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_relayed_page_request_carries_no_cookies(tmp):
    """A page's GM.xmlhttpRequest goes out with `credentials: 'omit'`.

    The relay called `fetch(url, opts)` with the default credentials mode.
    The extension holds host permission for every URL, so from the service
    worker that fetch is same-origin to every host and Chrome attaches the
    user's cookies to it. Tampermonkey grants that authority to installed
    userscripts; here every matching page had it, and could read and post to
    the user's logged-in sessions on any other site through the worker.

    There is no page-controllable opt-in: a flag the page sets is not a
    boundary, and the relay has no way to tell a userscript from the site.
    """
    del tmp
    outcome = _run_relay_authority('fetch')
    assert len(outcome['fetches']) == 1, outcome
    request = outcome['fetches'][0]
    assert request['url'] == 'https://example.com/account', request
    assert request['method'] == 'POST', request
    assert request['credentials'] == 'omit', request
    # Still a working relay: the request went out and its answer came back.
    assert outcome['answer'] == {'status': 200, 'error': None}, outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gmauthority_')


if __name__ == '__main__':
    raise SystemExit(main())
