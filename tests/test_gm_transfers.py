#!/usr/bin/env python3
"""What crosses the GM bridge: a fetch, a clipboard write, a download.

Each of these ends in a browser API that can refuse, redirect or time out,
and each refusal has its own event. A timeout that arrives as an error, a
redirect that reports the URL asked for rather than the one answered, or a
refused clipboard write reported as success would each be a caller acting on
something that did not happen.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402


_FETCH_RELAY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [contentPath, pagePath, responseText] = process.argv.slice(1);
const backgroundResponse = JSON.parse(responseText);
const listeners = {};
const messages = [];
const posted = [];
const sent = [];

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    posted.push(message);
    messages.push(message);
  },
};

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage(payload, callback) {
      sent.push(payload);
      // content.js asks for a hotfix replay at load with no callback at all.
      if (typeof callback === 'function') callback(backgroundResponse);
    },
    getManifest() { return { version: '0.0.0' }; },
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
      get(keys, callback) { callback({}); },
      set(values, callback) { callback(); },
      remove(keys, callback) { callback(); },
    },
  },
};

const context = {
  window: windowObject,
  document: { documentElement: {}, addEventListener() {} },
  chrome,
  location: { hostname: 'page.invalid', href: 'about:blank' },
  setTimeout, clearTimeout, setInterval, clearInterval,
  performance,
  console: { log() {}, error() {} },
};
context.globalThis = context;
vm.runInNewContext(fs.readFileSync(contentPath, 'utf8'), context);
vm.runInNewContext(fs.readFileSync(pagePath, 'utf8'), context);

function flushMessages() {
  let guard = 0;
  while (messages.length && guard++ < 100) {
    const data = messages.shift();
    for (const listener of listeners.message || []) {
      listener({ source: windowObject, data });
    }
  }
}

const events = [];
let loadDetail = null;
windowObject.GM.xmlhttpRequest({
  url: 'about:blank#slow',
  timeout: 50,
  onload: (detail) => { loadDetail = detail; events.push('load'); },
  onerror: (detail) => events.push('error:' + (detail && detail.error)),
  ontimeout: () => events.push('timeout'),
});
flushMessages();

// The content script arms a keepalive timer that would hold the event loop
// open forever; exit once the answer has actually been flushed.
process.stdout.write(JSON.stringify({
  events,
  loadDetail,
  relayed: posted
    .filter((m) => m.direction === 'daedalus-bg-to-page')
    .map((m) => m.event),
  requestedTimeout: (sent.find((m) => m.type === 'fetch') || {}).timeout,
}), () => process.exit(0));
"""


def _run_fetch_relay_harness(background_response):
    """Drive GM.xmlhttpRequest through content.js and page.js under Node."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension fetch relay'
    result = subprocess.run(
        [node, '-e', _FETCH_RELAY_HARNESS,
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js'),
         json.dumps(background_response)],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_fetch_timeout_reaches_ontimeout_and_not_onerror(tmp):
    """A timeout is its own event, not an error that happens to say so.

    page.js has had an `ontimeout` branch all along, and nothing could reach
    it: the background flattened an aborted fetch into an error string, and
    content.js relays anything with an `error` as `event: 'error'`. A caller
    that distinguished the two saw every timeout as a generic failure.
    """
    del tmp
    timed_out = _run_fetch_relay_harness(
        {'error': 'fetch timeout after 50ms', 'timedOut': True})
    assert timed_out['events'] == ['timeout'], timed_out
    assert timed_out['relayed'] == ['timeout'], timed_out
    assert timed_out['requestedTimeout'] == 50, timed_out

    # An ordinary failure still arrives as one.
    failed = _run_fetch_relay_harness({'error': 'network unreachable'})
    assert failed['events'] == ['error:network unreachable'], failed
    assert failed['relayed'] == ['error'], failed


def test_a_redirected_fetch_reports_where_the_body_came_from(tmp):
    """finalUrl is the response's URL, not the one the caller asked for.

    The relay filled finalUrl in from the request, so a caller following a
    redirect chain was told no redirect had happened — and statusText was
    never carried at all, so the page API always reported an empty one.
    """
    del tmp
    loaded = _run_fetch_relay_harness({
        'status': 200, 'statusText': 'OK', 'data': 'body', 'headers': {},
        'finalUrl': 'https://redirected.example.com/final'})
    assert loaded['events'] == ['load'], loaded
    detail = loaded['loadDetail']
    assert detail['finalUrl'] == 'https://redirected.example.com/final', detail
    assert detail['statusText'] == 'OK', detail

    # A background that reports neither still works: the request URL is the
    # fallback it always was, rather than the answer it used to be.
    plain = _run_fetch_relay_harness(
        {'status': 200, 'data': 'body', 'headers': {}})
    assert plain['loadDetail']['finalUrl'] == 'about:blank#slow', plain
    assert plain['loadDetail']['statusText'] == '', plain


def test_the_background_relays_the_response_url_and_status_text(tmp):
    """The other half of the same contract, at its source.

    The relay test above starts from what the background answered. This one
    pins that the background actually reads them off the Response rather than
    off the request it was handed.
    """
    del tmp
    source = (_util.ROOT / 'extension' / 'background.js').read_text(
        encoding='utf-8')
    _, marker, after = source.partition('sendResponse({ status: resp.status,')
    assert marker, 'the fetch success response is not shaped as this test finds it'
    response, _, _ = after.partition('}\n')
    for field in ('statusText: resp.statusText', 'finalUrl: resp.url'):
        assert field in response, (field, response)


_CLIPBOARD_RELAY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [contentPath, pagePath, mode] = process.argv.slice(1);
const listeners = {};
const messages = [];
const posted = [];
const writes = [];

const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) {
    posted.push(message);
    messages.push(message);
  },
};

const navigatorObject = {
  clipboard: {
    writeText(text) {
      writes.push(text);
      return mode === 'reject'
        ? Promise.reject(new Error('NotAllowedError'))
        : Promise.resolve();
    },
  },
};

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    sendMessage(payload, callback) {
      if (typeof callback === 'function') callback({});
    },
    getManifest() { return { version: '0.0.0' }; },
    connect() {
      return {
        disconnect() {}, postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
  },
  storage: { local: {
    get(keys, cb) { cb({}); }, set(v, cb) { cb(); }, remove(k, cb) { cb(); },
  } },
};

const context = {
  window: windowObject,
  document: { documentElement: {}, addEventListener() {} },
  navigator: navigatorObject,
  chrome,
  location: { hostname: 'page.invalid', href: 'about:blank' },
  setTimeout, clearTimeout, setInterval, clearInterval,
  performance,
  console: { log() {}, error() {} },
};
context.globalThis = context;
vm.runInNewContext(fs.readFileSync(contentPath, 'utf8'), context);
vm.runInNewContext(fs.readFileSync(pagePath, 'utf8'), context);

function flushMessages() {
  let guard = 0;
  while (messages.length && guard++ < 100) {
    const data = messages.shift();
    for (const listener of listeners.message || []) {
      listener({ source: windowObject, data });
    }
  }
}

(async () => {
  let settled = 'pending';
  let reported = null;
  const returned = windowObject.GM.setClipboard('replacement');
  const isPromise = !!(returned && typeof returned.then === 'function');
  if (isPromise) {
    returned.then(() => { settled = 'resolved'; },
      (error) => { settled = 'rejected'; reported = String(error && error.message); });
  }
  // Let the clipboard promise settle, then deliver whatever the content
  // script posted back in response to it.
  for (let turn = 0; turn < 10; turn++) {
    await Promise.resolve();
    flushMessages();
  }
  await Promise.resolve();

  process.stdout.write(JSON.stringify({
    isPromise, settled, reported, writes,
    acknowledged: posted
      .filter((m) => m.direction === 'daedalus-bg-to-page'
        && m.handler === 'setClipboard')
      .map((m) => ({ error: m.error || null })),
  }), () => process.exit(0));
})();
"""


def _run_clipboard_relay_harness(mode):
    """Drive GM.setClipboard through content.js and page.js under Node."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension clipboard relay'
    result = subprocess.run(
        [node, '-e', _CLIPBOARD_RELAY_HARNESS,
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js'), mode],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_refused_clipboard_write_reaches_the_caller(tmp):
    """A clipboard write that the browser refuses must not report success.

    The content script called writeText, swallowed the rejection with an empty
    catch, and posted its acknowledgement without waiting for the promise at
    all — so a page without user activation, where Chromium rejects the write
    with NotAllowedError, was told the clipboard had been set.
    """
    del tmp
    refused = _run_clipboard_relay_harness('reject')
    assert refused['writes'] == ['replacement'], refused
    assert refused['isPromise'] is True, refused
    assert refused['settled'] == 'rejected', refused
    assert refused['acknowledged'] == [{'error': 'NotAllowedError'}], refused

    accepted = _run_clipboard_relay_harness('resolve')
    assert accepted['settled'] == 'resolved', accepted
    assert accepted['acknowledged'] == [{'error': None}], accepted


def test_the_extension_declares_the_permission_its_clipboard_write_needs(tmp):
    """A documented operation must ship the permission it depends on."""
    del tmp
    manifest = json.loads(
        (EXTENSION_ROOT / 'manifest.json').read_text(encoding='utf-8'))
    assert 'clipboardWrite' in manifest.get('permissions', []), manifest


_DOWNLOAD_RELAY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

// Argument is the background outcome to simulate: "lastError" (the worker
// never answered), "empty" (answered without a downloadId), "error" (answered
// with one), or "ok".
const [contentPath, pagePath, mode] = process.argv.slice(1);
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

const chrome = {
  runtime: {
    lastError: null,
    onMessage: { addListener() {} },
    getManifest() { return { version: '0.18.0' }; },
    connect() {
      return {
        disconnect() {},
        postMessage() {},
        onDisconnect: { addListener() {} },
      };
    },
    sendMessage(message, callback) {
      if (!callback) return;
      if (mode === 'lastError') {
        // Chrome's shape for an undelivered message: lastError set, and the
        // callback invoked with no response at all.
        chrome.runtime.lastError = { message: 'Could not establish connection.' };
        try { callback(undefined); } finally { chrome.runtime.lastError = null; }
        return;
      }
      if (mode === 'empty') return callback({});
      if (mode === 'error') return callback({ error: 'Invalid filename' });
      callback({ downloadId: 7 });
    },
  },
};

const context = {
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: 'download-test.invalid' },
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  console: { log() {}, error() {} },
};
vm.runInNewContext(fs.readFileSync(contentPath, 'utf8'), context);
vm.runInNewContext(fs.readFileSync(pagePath, 'utf8'), context);

function flushMessages() {
  while (messages.length) {
    const data = messages.shift();
    for (const listener of listeners.message) {
      listener({ source: windowObject, data });
    }
  }
}

const events = [];
windowObject.GM.download({
  url: 'about:blank',
  name: 'file.bin',
  onload: () => events.push('load'),
  onerror: (detail) => events.push('error:' + (detail && detail.error)),
});
flushMessages();

process.stdout.write(JSON.stringify({ events }), () => process.exit(0));
"""


def _run_download_relay_harness(mode):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension download boundary'
    result = subprocess.run(
        [node, '-e', _DOWNLOAD_RELAY_HARNESS,
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js'), mode],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_download_that_never_started_reaches_onerror(tmp):
    """An absent response is a failure, not a success with nothing in it.

    The relay tested `resp && resp.error`, so the one case where Chrome
    passes NO response — a sendMessage that never reached the worker,
    reported through lastError — skipped the error branch entirely and the
    page was handed a load event for a download that was never started.
    """
    del tmp
    undelivered = _run_download_relay_harness('lastError')
    assert undelivered['events'] == [
        'error:Could not establish connection.'], undelivered

    # Answered, but with no download to point at.
    empty = _run_download_relay_harness('empty')
    assert empty['events'] == ['error:background started no download'], empty

    # The failure the relay always did report still reports.
    refused = _run_download_relay_harness('error')
    assert refused['events'] == ['error:Invalid filename'], refused

    # And a real download is still a load.
    started = _run_download_relay_harness('ok')
    assert started['events'] == ['load'], started


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gmtransfers_')


if __name__ == '__main__':
    raise SystemExit(main())
