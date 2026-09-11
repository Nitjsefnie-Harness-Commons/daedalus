#!/usr/bin/env python3
"""What authority a page gets through the GM relay, and what it does not.

The shim is injected into every matching page, so the service worker's
relay answers to any site the user visits, not to a userscript the user
installed. What the worker does on the page's behalf is therefore bounded
here: a relayed request goes out without the user's cookies, and a relayed
tab open reaches only web URLs. These run the shipped worker in a Node VM
against a fake browser that records what the worker asked it to do.
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
const created = [];

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
    // Callback-style, as messaging.js calls it. Chrome reports a refused
    // creation through lastError, with the callback invoked on no tab.
    create(details, callback) {
      created.push(details);
      if (mode === 'create-refused') {
        chrome.runtime.lastError = { message: 'Tabs cannot be edited' };
        try {
          callback(undefined);
        } finally {
          chrome.runtime.lastError = null;
        }
        return;
      }
      callback({ id: 100 + created.length });
    },
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
  const urls = mode === 'create-refused'
    ? ['https://example.com/']
    : ['chrome://settings', 'javascript:alert(1)', 'not a url',
      'https://example.com/'];
  const outcomes = [];
  for (const url of urls) {
    try {
      const answer = await send({ type: 'openTab', url, active: true });
      outcomes.push({
        url,
        tabId: answer.tabId === undefined ? null : answer.tabId,
        error: answer.error || null,
        threw: null,
      });
    } catch (error) {
      outcomes.push({ url, tabId: null, error: null, threw: error.message });
    }
  }
  return { outcomes, created: created.map((details) => details.url) };
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


def test_a_page_can_open_only_web_urls(tmp):
    """`GM.openInTab` refuses anything but http: and https:.

    `chrome.tabs.create` accepts URLs a page could never navigate to itself,
    and the relay handed it the page's URL unread — so a page could open
    `chrome://` pages, or a `javascript:` URL, with the extension's authority.
    A refused URL is answered `{error}` and never reaches the browser.
    """
    del tmp
    outcome = _run_relay_authority('open')
    by_url = {item['url']: item for item in outcome['outcomes']}
    for url in ('chrome://settings', 'javascript:alert(1)', 'not a url'):
        refused = by_url[url]
        assert refused['error'], refused
        assert refused['tabId'] is None, refused
        assert refused['threw'] is None, refused
    opened = by_url['https://example.com/']
    assert opened['error'] is None, opened
    assert opened['tabId'] == 101, opened
    assert outcome['created'] == ['https://example.com/'], outcome


def test_a_refused_tab_creation_answers_an_error(tmp):
    """A `tabs.create` that fails answers `{error}` rather than throwing.

    Chrome reports a refused creation by setting `chrome.runtime.lastError`
    and invoking the callback with no tab. The callback read `tab.id` off
    that undefined, so it threw, `sendResponse` never fired, and the error
    went unchecked — the page waited on an answer nothing would send.
    """
    del tmp
    outcome = _run_relay_authority('create-refused')
    assert outcome['outcomes'] == [{
        'url': 'https://example.com/',
        'tabId': None,
        'error': 'Tabs cannot be edited',
        'threw': None,
    }], outcome
    assert outcome['created'] == ['https://example.com/'], outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gmauthority_')


if __name__ == '__main__':
    raise SystemExit(main())
