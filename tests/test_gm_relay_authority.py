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
from _worker_chrome_fake import INERT_WORKER_APIS  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402


_RELAY_AUTHORITY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, mode] = process.argv.slice(1);
const messageListeners = [];
const fetches = [];
const created = [];
let createCalls = 0;
const downloaded = [];
let downloadCalls = 0;

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
    // Callback-style, as messaging.js calls it. The callback is delivered
    // one microtask after create() returns, as the shipped API delivers its
    // callback asynchronously: a listener that returned before the callback
    // fired only still holds the response channel if it returned true. The
    // real API validates argument types before doing anything, so a
    // non-string url is refused with a synchronous TypeError exactly as the
    // shipped API refuses one.
    create(details, callback) {
      createCalls += 1;
      if (typeof details.url !== 'string') {
        throw new TypeError('url: expected string');
      }
      created.push(details);
      if (mode === 'create-sync-throw') {
        throw new Error('synchronous refusal');
      }
      if (mode === 'create-refused') {
        queueMicrotask(() => {
          chrome.runtime.lastError = { message: 'Tabs cannot be edited' };
          try {
            callback(undefined);
          } finally {
            chrome.runtime.lastError = null;
          }
        });
        return;
      }
      if (mode !== 'open' && mode !== 'open-guard') {
        throw new Error('unmodeled tab-create mode: ' + mode);
      }
      queueMicrotask(() => callback({ id: 100 + created.length }));
    },
  },
  downloads: {
    // Callback-style, as messaging.js calls it. The callback is delivered
    // one microtask after download() returns, as the shipped API delivers
    // its callback asynchronously: a listener that returned before the
    // callback fired only still holds the response channel if it returned
    // true. The real API validates its arguments before doing anything, so
    // a non-string url is refused with a synchronous TypeError exactly as
    // the shipped API refuses one.
    download(details, callback) {
      downloadCalls += 1;
      if (typeof details.url !== 'string') {
        throw new TypeError('url: expected string');
      }
      downloaded.push(details);
      if (mode === 'download-sync-throw') {
        throw new Error('synchronous refusal');
      }
      if (mode === 'download-refused') {
        queueMicrotask(() => {
          chrome.runtime.lastError = { message: 'Download refused' };
          try {
            callback(undefined);
          } finally {
            chrome.runtime.lastError = null;
          }
        });
        return;
      }
      if (mode !== 'download-guard' && mode !== 'download-scheme') {
        throw new Error('unmodeled download mode: ' + mode);
      }
      queueMicrotask(() => callback(5000 + downloaded.length));
    },
  },
""" + INERT_WORKER_APIS + r"""
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
// indistinguishable from an answer that never came. The response channel
// closes when the listener returns unless it kept it open with true, so a
// callback that fires after a falsy return answers nothing — the shipped
// runtime drops it and content.js surfaces a port-closed error, which is
// the rejection the send answers with.
function send(message) {
  return new Promise((resolve, reject) => {
    const responses = [];
    let open = true;
    const respond = (payload) => {
      if (!open) {
        reject(new Error(
          'The message port closed before a response was received'));
        return;
      }
      responses.push(payload);
      if (responses.length === 1) {
        resolve({ answer: payload, responses });
      }
    };
    try {
      for (const listener of messageListeners) {
        if (listener(message, { tab: { id: 7 } }, respond) !== true) {
          open = false;
          break;
        }
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
    const relayed = await send({
      type: 'fetch', fetchId: 'page-1', url: 'https://example.com/account',
      method: 'POST', headers: {}, body: '{}', responseType: 'text',
    });
    return {
      fetches,
      answer: {
        status: relayed.answer.status === undefined
          ? null : relayed.answer.status,
        error: relayed.answer.error || null,
      },
    };
  }
  if (mode === 'download-guard' || mode === 'download-refused'
    || mode === 'download-sync-throw' || mode === 'download-scheme') {
    const downloadTargets = mode === 'download-scheme'
      ? [['file:///etc/passwd', 'f.bin'],
        ['javascript:alert(1)', 'f.bin'],
        ['not a url', 'f.bin'],
        ['https://example.com/f', 'f.bin'],
        ['http://example.com/f', 'f-http.bin']]
      : mode === 'download-guard'
        ? [[['https://example.com/f'], 'f.bin'],
          ['https://example.com/f', 'f.bin']]
        : [['https://example.com/f', 'f.bin']];
    const outcomes = [];
    for (const [url, filename] of downloadTargets) {
      try {
        const relayed = await send({ type: 'download', url, filename });
        outcomes.push({
          url,
          downloadId: relayed.answer.downloadId === undefined
            ? null : relayed.answer.downloadId,
          error: relayed.answer.error || null,
          threw: null,
          responses: relayed.responses.length,
        });
      } catch (error) {
        outcomes.push({
          url, downloadId: null, error: null, threw: error.message,
          responses: 0,
        });
      }
    }
    return {
      outcomes,
      downloaded: downloaded.map((details) => details.url),
      downloadedFilenames: downloaded.map((details) => details.filename),
      downloadCalls,
    };
  }
  const requests = mode === 'open-guard'
    ? [
        [['https://example.com/'], true],
        [{ toString: () => 'https://example.com/toString' }, true],
        ['https://example.com/guard', true],
      ]
    : mode === 'create-refused' || mode === 'create-sync-throw'
      ? [['https://example.com/', true]]
      : [['chrome://settings', true], ['javascript:alert(1)', true],
        ['not a url', true], ['https://example.com/', true],
        ['http://example.com/plain', false]];
  const outcomes = [];
  for (const [url, active] of requests) {
    try {
      const relayed = await send({ type: 'openTab', url, active });
      outcomes.push({
        url,
        tabId: relayed.answer.tabId === undefined
          ? null : relayed.answer.tabId,
        error: relayed.answer.error || null,
        threw: null,
        responses: relayed.responses.length,
      });
    } catch (error) {
      outcomes.push({
        url, tabId: null, error: null, threw: error.message, responses: 0,
      });
    }
  }
  return {
    outcomes,
    created: created.map((details) => details.url),
    createDetails: created.map(
      (details) => ({ url: details.url, active: details.active })),
    createCalls,
  };
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
    A refused URL is answered `{error}` and never reaches the browser, and
    every outcome answers exactly once. Each accepted web scheme drives its
    own control, so a gate narrowed to `https:` alone fails, and the
    `active` each control sends is observed at the create call, so a relay
    that forces the tab to the foreground fails too.
    """
    del tmp
    outcome = _run_relay_authority('open')
    by_url = {item['url']: item for item in outcome['outcomes']}
    for url in ('chrome://settings', 'javascript:alert(1)', 'not a url'):
        refused = by_url[url]
        assert refused['error'], refused
        assert refused['tabId'] is None, refused
        assert refused['threw'] is None, refused
        assert refused['responses'] == 1, refused
    for url, tab_id in (('https://example.com/', 101),
                        ('http://example.com/plain', 102)):
        opened = by_url[url]
        assert opened['error'] is None, opened
        assert opened['tabId'] == tab_id, opened
        assert opened['responses'] == 1, opened
    assert outcome['created'] == [
        'https://example.com/', 'http://example.com/plain'], outcome
    assert outcome['createDetails'] == [
        {'url': 'https://example.com/', 'active': True},
        {'url': 'http://example.com/plain', 'active': False}], outcome
    assert outcome['createCalls'] == 2, outcome


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
        'responses': 1,
    }], outcome
    assert outcome['created'] == ['https://example.com/'], outcome


def test_a_non_string_url_is_refused_before_tab_creation(tmp):
    """A non-string `openTab` URL answers an error and never reaches create.

    `new URL` stringifies whatever it is handed, so the protocol gate read
    the issue's one-element array as a web URL and handed that array itself
    to `chrome.tabs.create`. The real API refuses a non-string url argument
    with a synchronous TypeError, the listener propagated it, and the page
    was left with no answer at all.
    """
    del tmp
    outcome = _run_relay_authority('open-guard')
    array_shape, object_shape = outcome['outcomes'][:2]
    for refused in (array_shape, object_shape):
        assert refused['threw'] is None, refused
        assert refused['responses'] == 1, refused
        assert refused['error'], refused
        assert refused['tabId'] is None, refused
    assert outcome['created'] == ['https://example.com/guard'], outcome
    assert outcome['createCalls'] == 1, outcome


def test_a_synchronous_tab_create_refusal_answers_an_error(tmp):
    """A synchronous `tabs.create` refusal answers `{error}`, not a throw.

    Chrome can refuse the call itself, before any callback is scheduled.
    That exception escaped the listener, so `sendResponse` never fired and
    the page waited on an answer nothing would send — the same zero-answer
    outcome the callback refusal path had already been fixed not to produce.
    """
    del tmp
    outcome = _run_relay_authority('create-sync-throw')
    assert outcome['outcomes'] == [{
        'url': 'https://example.com/',
        'tabId': None,
        'error': 'synchronous refusal',
        'threw': None,
        'responses': 1,
    }], outcome
    assert outcome['created'] == ['https://example.com/'], outcome


def test_a_web_url_still_opens_with_one_answer(tmp):
    """A valid web URL opens exactly as before: one create, one answer.

    Guards the success path while the failure paths around it gain the
    string gate and the invocation guard: the URL travels to create as the
    string the page sent, and the success shape stays `{tabId}`.
    """
    del tmp
    outcome = _run_relay_authority('open-guard')
    assert outcome['outcomes'][2] == {
        'url': 'https://example.com/guard',
        'tabId': 101,
        'error': None,
        'threw': None,
        'responses': 1,
    }, outcome
    assert outcome['created'] == ['https://example.com/guard'], outcome
    assert outcome['createCalls'] == 1, outcome


def test_a_non_string_url_is_refused_before_download(tmp):
    """A non-string `download` URL answers an error and never downloads.

    The worker handed `msg.url` to `chrome.downloads.download` unread, and
    the real API refuses a non-string url argument with a synchronous
    TypeError. The listener propagated that exception, so `sendResponse`
    was never called — the download twin of the openTab hole issue 712
    closed.
    """
    del tmp
    outcome = _run_relay_authority('download-guard')
    array_shape, control = outcome['outcomes']
    assert array_shape['threw'] is None, array_shape
    assert array_shape['responses'] == 1, array_shape
    assert array_shape['error'] == 'download requires a string URL', (
        array_shape)
    assert array_shape['downloadId'] is None, array_shape
    # Only the string control reached the API: the gate answers the array
    # before an invocation that cannot succeed.
    assert outcome['downloaded'] == ['https://example.com/f'], outcome
    assert outcome['downloadCalls'] == 1, outcome
    assert control == {
        'url': 'https://example.com/f',
        'downloadId': 5001,
        'error': None,
        'threw': None,
        'responses': 1,
    }, outcome


def test_a_synchronous_download_refusal_answers_an_error(tmp):
    """A synchronous `downloads.download` refusal answers `{error}`.

    Chrome can refuse the call itself, before any callback is scheduled,
    so the callback that answers `lastError` refusals never runs. That
    exception escaped the listener, and `sendResponse` never fired.
    """
    del tmp
    outcome = _run_relay_authority('download-sync-throw')
    assert outcome['outcomes'] == [{
        'url': 'https://example.com/f',
        'downloadId': None,
        'error': 'synchronous refusal',
        'threw': None,
        'responses': 1,
    }], outcome
    assert outcome['downloaded'] == ['https://example.com/f'], outcome


def test_a_refused_download_answers_an_error(tmp):
    """A `lastError` download refusal answers `{error}` exactly once.

    The callback path already answered correctly before the fix; the fix
    wraps its invocation in the try that answers synchronous refusals.
    This pins the wrapped callback's answer so the guard cannot regress
    it.
    """
    del tmp
    outcome = _run_relay_authority('download-refused')
    assert outcome['outcomes'] == [{
        'url': 'https://example.com/f',
        'downloadId': None,
        'error': 'Download refused',
        'threw': None,
        'responses': 1,
    }], outcome
    assert outcome['downloaded'] == ['https://example.com/f'], outcome


def test_a_download_url_outside_the_web_schemes_is_refused(tmp):
    """`GM.download` refuses anything but http: and https:.

    A download runs with the profile's cookies, so a page-chosen URL the
    page could not fetch itself borrows the extension's authority — the
    same authority the openTab twin already gates. A URL the parser refuses
    folds into the same refusal, the API is never invoked, and every
    outcome answers exactly once. Each accepted web scheme drives its own
    control under a distinct filename, so a gate narrowed to `https:`
    alone fails the suite.
    """
    del tmp
    outcome = _run_relay_authority('download-scheme')
    file_refusal, script_refusal, unparseable, https_control, http_control = (
        outcome['outcomes'])
    for refused in (file_refusal, script_refusal, unparseable):
        assert refused['threw'] is None, refused
        assert refused['responses'] == 1, refused
        assert refused['error'] == (
            'download accepts http: and https: URLs only'), refused
        assert refused['downloadId'] is None, refused
    assert outcome['downloaded'] == [
        'https://example.com/f', 'http://example.com/f'], outcome
    assert outcome['downloadedFilenames'] == ['f.bin', 'f-http.bin'], outcome
    assert outcome['downloadCalls'] == 2, outcome
    assert https_control == {
        'url': 'https://example.com/f',
        'downloadId': 5001,
        'error': None,
        'threw': None,
        'responses': 1,
    }, outcome
    assert http_control == {
        'url': 'http://example.com/f',
        'downloadId': 5002,
        'error': None,
        'threw': None,
        'responses': 1,
    }, outcome


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gmauthority_')


if __name__ == '__main__':
    raise SystemExit(main())
