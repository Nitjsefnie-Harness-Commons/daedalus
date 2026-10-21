#!/usr/bin/env python3
"""What crosses the GM bridge: a fetch, a clipboard write, a download."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _noderun import run_node_argv  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import (  # noqa: E402
    CONTENT_SCRIPT_PAGE, DELAY_STUB, RELAY_CONTEXT, chrome_stub,
    event_target_stub, worker_source_paths)


_RELAY_ADMISSION_HARNESS = event_target_stub() + r"""
const fs = require('fs');
const vm = require('vm');
const [backgroundPath, mode] = process.argv.slice(1);
const assert = require('assert/strict');
const started = [], answers = [], completed = [], timers = new Set();
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const command = deferred();
""" + chrome_stub(
    "'relay-token'", "''", "(() => { throw Error('CDP'); })") + r"""
chrome.tabs.reload = () => command.promise;
const readStorage = chrome.storage.local.get;
chrome.storage.local.get = (keys) => keys.includes('daedalus-hotfixes')
  ? command.promise : readStorage(keys);
function bridgeFetch(url, opts) {
  if (url === '/result') return Promise.resolve(new Response('{}'));
  assert.equal(url, 'https://example.com/relay');
  const relay = { ...deferred(), id: Number(opts.headers['X-Relay-Id']) };
  started.push(relay);
  const abort = () => relay.reject(
    Object.assign(Error('aborted'), { name: 'AbortError' }));
  if (opts.signal.aborted) abort();
  else opts.signal.addEventListener('abort', abort, { once: true });
  return relay.promise;
}
""" + RELAY_CONTEXT + DELAY_STUB + r"""
context.setTimeout = (fn, ms) => {
  const timer = { fn, ms }; timers.add(timer); return timer;
};
context.clearTimeout = (timer) => timers.delete(timer);
if (mode === 'withdraw:pre') context.AbortController = class
  extends AbortController { constructor() { super(); this.abort(); } };
vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context,
  { filename: backgroundPath });
const state = () => vm.runInContext(
  '[_relayInFlight, _relayWaiters.length, _pendingCommands]', context);
function send(message) {
  return new Promise((resolve) => {
    const listener = chrome.runtime.onMessage.listeners[0];
    assert.equal(listener(message, { tab: { id: 7 } }, (value) => {
      if (message.type === 'fetch') {
        completed.push({ ...value, id: message.fetchId });
        if (mode === 'withdraw:grant' && message.fetchId === '0') {
          queueMicrotask(() => {
            assert.deepEqual(Array.from(state()).slice(0, 2), [8, 1]);
            abort(8);
          });
        }
      }
      resolve(value);
    }), true);
  });
}
function abort(id) {
  chrome.runtime.onMessage.listeners[0](
    { type: 'abortFetch', fetchId: String(id) }, {}, () => {});
}
function relay() {
  const id = String(answers.length);
  answers.push(send({ type: 'fetch', fetchId: id,
    url: 'https://example.com/relay', responseType: 'arraybuffer',
    timeout: 60000 + Number(id), headers: { 'X-Relay-Id': id } }));
}
function finish(index, fail = false) {
  if (fail) started[index].reject(Error('relay failed'));
  else started[index].resolve(new Response('body'));
}
async function finishAll() {
  for (let turn = 0; turn < 12; turn++) {
    for (let i = 0; i < started.length; i++) finish(i);
    await delay();
  }
  assert.equal(completed.length, answers.length, 'terminal answer count');
  assert.equal(new Set(completed.map((r) => r.id)).size, answers.length);
  assert.deepEqual(Array.from(state()), [0, 0, 0], 'retained work');
  assert.equal(timers.size, 0, 'retained timers');
  assert.equal(vm.runInContext('_fetchControllers.size', context), 0);
}
async function withdrawal(kind) {
  if (kind === 'pre') {
    relay(); await delay();
    assert.equal(started.length, 0, 'pre-aborted request fetched');
    assert.equal(completed[0].timedOut, true);
  } else {
    for (let i = 0; i < 8; i++) relay();
    await delay();
    for (let i = 0; i < (kind === 'full' ? 65 : 2); i++) relay();
    await delay();
    if (kind === 'full') {
      assert.equal(completed.length, 1, 'queue overflow did not answer');
      assert.deepEqual(completed[0], { id: '72',
        error: 'fetch relay queue full (64 waiting)',
        timedOut: false, aborted: false, tooLarge: false });
      assert.deepEqual(Array.from(state()), [8, 64, 0]);
      abort(8); await delay(); relay(); relay(); await delay();
      assert.equal(completed.at(-1).id, '74');
      assert.equal(completed.at(-1).error, completed[0].error);
      assert.deepEqual(Array.from(state()), [8, 64, 0]);
    } else {
      if (kind === 'grant') finish(0);
      else if (kind === 'timeout') {
        [...timers].find((t) => t.ms === 60008).fn();
      } else { abort(8); abort(8); }
      await delay();
      const cancelled = completed.filter((r) => r.id === '8');
      assert.equal(cancelled.length, 1, 'queued cancellation did not answer');
      assert.deepEqual(cancelled[0], { id: '8',
        error: kind === 'timeout' ? 'fetch timeout after 60008ms'
          : 'aborted by the caller', timedOut: kind === 'timeout',
        aborted: kind !== 'timeout', tooLarge: false });
      assert.equal([...timers].some((t) => t.ms === 60008), false);
      if (kind !== 'grant') {
        assert.deepEqual(Array.from(state()), [8, 1, 0]);
        finish(0); await delay();
      }
      assert.deepEqual(started.map((r) => r.id), [0, 1, 2, 3, 4, 5, 6, 7, 9]);
      relay(); finish(1); await delay();
      assert.equal(started.at(-1).id, 10);
      assert.deepEqual(Array.from(state()), [8, 0, 0], 'slot ownership');
    }
  }
  await finishAll();
  assert.equal(started.some((r) => r.id === 8), false);
}
function commandRun(run) {
  context.commandHandler = run;
  vm.runInContext('handleReload = commandHandler', context);
  return vm.runInContext("dispatchCommand({type:'reload',tabId:7})", context);
}
async function priority(kind) {
  relay(); relay(); await delay();
  const first = deferred(), second = deferred();
  if (kind === 'first' || kind === 'last') {
    const a = commandRun(() => first.promise);
    const b = commandRun(() => second.promise);
    relay();
    const early = kind === 'first' ? first : second;
    early.resolve(); await (kind === 'first' ? a : b); await delay();
    assert.equal(started.length, 2, 'one command released another hold');
    (kind === 'first' ? second : first).resolve();
    await Promise.all([a, b]); await delay();
    assert.equal(started.length, 3, 'last command did not drain');
  } else {
    const result = await commandRun(() => {
      if (kind === 'throw') throw Error('handler failed');
      if (kind === 'reject') return Promise.reject(Error('handler failed'));
      return 42;
    }).catch((e) => e.message);
    assert.equal(result, kind === 'sync' ? 42 : 'handler failed');
    relay(); await delay();
    assert.equal(started.length, 3, 'handler settlement retained hold');
    const next = commandRun(() => first.promise);
    relay(); await delay();
    assert.equal(started.length, 3, 'fresh command batch did not hold');
    first.resolve(); await next; await delay();
    assert.equal(started.length, 4, 'fresh batch did not drain');
  }
  await finishAll();
}
(async () => {
  await delay();
  if (mode.includes(':')) {
    const [group, kind] = mode.split(':');
    await (group === 'withdraw' ? withdrawal(kind) : priority(kind));
    process.stdout.write('{}'); return;
  }
  const full = ['ceiling', 'error', 'abort'].includes(mode);
  const n = full ? 8 : mode === 'idle' ? 0 : 2;
  for (let i = 0; i < n; i++) relay();
  await delay();
  let settled = false, pending;
  if (!full) {
    pending = mode === 'replay' ? send({ type: 'replayHotfixes' })
      : vm.runInContext("dispatchCommand({type:'reload',tabId:7})", context);
    pending.then(() => { settled = true; });
  }
  relay();
  if (mode !== 'settled') relay();
  await delay();
  const queued = started.length;
  if (mode === 'settled') {
    command.resolve({});
    await pending;
    await delay();
    assert.equal(started.length, 3, 'settlement did not drain the waiter');
  }
  if (n) {
    if (mode === 'abort') {
      chrome.runtime.onMessage.listeners[0](
        { type: 'abortFetch', fetchId: '0' }, {}, () => {});
    } else finish(0, mode === 'error');
  }
  await delay();
  const one = started.length;
  if (mode === 'ceiling') relay();
  for (let i = 1; i < n; i++) finish(i);
  await delay();
  const drained = started.length, pendingAtDrain = !!pending && !settled;
  if (mode === 'overlap') {
    const next = vm.runInContext(
      "dispatchCommand({type:'reload',tabId:7})", context);
    pending = Promise.all([pending, next]);
    relay();
    await delay();
    assert.equal(started.length, 5, 'commands rearmed a drained hold');
  }
  if (pending) {
    command.resolve({});
    await pending;
  }
  await delay();
  for (let i = n; i < started.length; i++) finish(i);
  await delay();
  process.stdout.write(JSON.stringify({ queued, one, drained, pendingAtDrain,
    startOrder: started.map(({ id }) => id),
    completed: completed.length,
    errors: completed.filter((value) => value.error).map((v) => v.error) }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""


def test_relay_admission_yields_to_one_command_drain(tmp):
    del tmp
    failures = []
    for mode, queued, one, drained, pending, errors in (
        ('ceiling', 8, 9, 11, False, []),
        ('command', 2, 2, 4, True, []),
        ('replay', 2, 2, 4, True, []),
        ('overlap', 2, 2, 4, True, []),
        ('settled', 2, 3, 3, False, []),
        ('idle', 2, 2, 2, True, []),
        ('error', 8, 9, 10, False, ['relay failed']),
        ('abort', 8, 9, 10, False, ['aborted by the caller']),
        *((mode, 0, 0, 0, False, []) for mode in (
            'withdraw:abort', 'withdraw:timeout', 'withdraw:grant',
            'withdraw:pre', 'withdraw:full', 'priority:first', 'priority:last',
            'priority:throw', 'priority:reject', 'priority:sync')),
    ):
        result = run_node_argv(shutil.which('node'), [
            '-e', _RELAY_ADMISSION_HARNESS,
            str(EXTENSION_ROOT / 'background.js'), mode], ROOT)
        if result.returncode:
            failures.append((mode, result.stderr))
            continue
        actual = json.loads(result.stdout)
        if ':' in mode:
            assert actual == {}, (mode, actual)
            continue
        expected = {
            'queued': queued, 'one': one, 'drained': drained,
            'pendingAtDrain': pending,
            'completed': drained + (mode == 'overlap'),
            'startOrder': list(range(drained + (mode == 'overlap'))),
            'errors': errors,
        }
        if actual != expected: failures.append((mode, actual, expected))
    assert not failures, failures


_PAGE_RELAY_SETUP = CONTENT_SCRIPT_PAGE + r"""
const fs = require('fs'), vm = require('vm');
const listeners = {}, messages = [], posted = [], sent = [], writes = [];
const windowObject = {
  addEventListener(type, listener) {
    (listeners[type] ||= []).push(listener);
  },
  postMessage(message) { posted.push(message); messages.push(message); },
};
const chrome = {
  runtime: {
    lastError: null, onMessage: { addListener() {} },
    getManifest() { return { version: '0.0.0' }; },
    connect() { return { disconnect() {}, postMessage() {},
      onDisconnect: { addListener() {} } }; },
    sendMessage(payload, callback) {
      sent.push(payload);
      if (typeof callback !== 'function') return;
      if (mode === 'lastError') {
        chrome.runtime.lastError = {
          message: 'Could not establish connection.' };
        try { callback(undefined); }
        finally { chrome.runtime.lastError = null; }
      } else callback(backgroundAnswer(payload));
    },
  },
  storage: { local: {
    get(k, cb) { cb({}); }, set(v, cb) { cb(); }, remove(k, cb) { cb(); },
  } },
};
const context = {
  window: windowObject, chrome,
  document: { documentElement: {}, addEventListener() {} },
  navigator: { clipboard: { writeText(text) {
    writes.push(text);
    return mode === 'reject' ? Promise.reject(Error('NotAllowedError'))
      : Promise.resolve();
  } } },
  location: { hostname: 'page.invalid', href: 'about:blank' },
  setTimeout, clearTimeout, setInterval, clearInterval, performance,
  console: { log() {}, error() {} }, ...contentScriptPage(),
};
context.globalThis = context;
function loadPage(extra = {}) {
  Object.assign(context, extra);
  for (const path of [contentPath, pagePath]) {
    vm.runInNewContext(fs.readFileSync(path, 'utf8'), context,
      { filename: path });
  }
}
function flushMessages() {
  let guard = 0;
  while (messages.length && guard++ < 100) {
    const data = messages.shift();
    for (const listener of listeners.message || []) {
      listener({ source: windowObject, data });
    }
  }
}
"""


_FETCH_RELAY_HARNESS = _PAGE_RELAY_SETUP + r"""
const [contentPath, pagePath, responseText, mode] = process.argv.slice(1);
function backgroundAnswer() { return JSON.parse(responseText); }
loadPage();
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


def _run_fetch_relay_harness(background_response, mode='answer'):
    """Drive GM.xmlhttpRequest through content.js and page.js under Node."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension fetch relay'
    result = run_node_argv(
        node, ['-e', _FETCH_RELAY_HARNESS,
               str(ROOT / 'extension' / 'content.js'),
               str(ROOT / 'extension' / 'page.js'),
               json.dumps(background_response), mode],
        ROOT)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_fetch_timeout_reaches_ontimeout_and_not_onerror(tmp):
    """A timeout is its own event, not an error that happens to say so."""
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


def test_a_fetch_the_worker_never_answered_reaches_onerror(tmp):
    """An undelivered request is an error, not a load with no body."""
    del tmp
    undelivered = _run_fetch_relay_harness(None, 'lastError')
    assert undelivered['events'] == [
        'error:Could not establish connection.'], undelivered
    assert undelivered['relayed'] == ['error'], undelivered


def test_a_redirected_fetch_reports_where_the_body_came_from(tmp):
    """finalUrl is the response's URL, not the one the caller asked for."""
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
    """The other half of the same contract, at its source."""
    del tmp
    marker = 'sendResponse({ status: resp.status,'
    matches = [
        path.read_text(encoding='utf-8')
        for path in worker_source_paths()
        if marker in path.read_text(encoding='utf-8')
    ]
    assert len(matches) == 1, (
        'the fetch success response is not uniquely shaped as this test '
        f'finds it: {len(matches)} matches')
    _, marker, after = matches[0].partition(marker)
    response, _, _ = after.partition('}\n')
    for field in ('statusText: resp.statusText', 'finalUrl: resp.url'):
        assert field in response, (field, response)


_CLIPBOARD_RELAY_HARNESS = _PAGE_RELAY_SETUP + r"""
const [contentPath, pagePath, mode] = process.argv.slice(1);
function backgroundAnswer() { return {}; }
loadPage();
(async () => {
  let settled = 'pending';
  let reported = null;
  const returned = windowObject.GM.setClipboard('replacement');
  const isPromise = !!(returned && typeof returned.then === 'function');
  if (isPromise) {
    returned.then(() => { settled = 'resolved'; },
      (error) => {
        settled = 'rejected'; reported = String(error && error.message);
      });
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
    result = run_node_argv(
        node, ['-e', _CLIPBOARD_RELAY_HARNESS,
               str(ROOT / 'extension' / 'content.js'),
               str(ROOT / 'extension' / 'page.js'), mode],
        ROOT)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_refused_clipboard_write_reaches_the_caller(tmp):
    """A clipboard write that the browser refuses must not report success."""
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


_DOWNLOAD_RELAY_HARNESS = _PAGE_RELAY_SETUP + r"""
const [contentPath, pagePath, mode] = process.argv.slice(1);
function backgroundAnswer() {
  if (mode === 'fire-and-forget') return { tabId: 3 };
  if (mode === 'empty') return {};
  if (mode === 'error') return { error: 'Invalid filename' };
  return { downloadId: 7 };
}
loadPage({ setTimeout: () => 1, setInterval: () => 1, clearInterval() {} });
const events = [];
if (mode === 'fire-and-forget') {
  windowObject.GM.openInTab('about:blank', { active: false });
  windowObject.GM.notification(
    { title: 'Run finished', text: 'Nothing failed.' });
} else windowObject.GM.download({
  url: 'about:blank',
  name: 'file.bin',
  onload: () => events.push('load'),
  onerror: (detail) => events.push('error:' + (detail && detail.error)),
});
flushMessages();

const outcome = mode === 'fire-and-forget' ? {
  sent: sent.filter((message) => message.type === 'openTab'
    || message.type === 'notification'),
  acknowledged: posted
    .filter((m) => m.direction === 'daedalus-bg-to-page')
    .map((m) => ({ handler: m.handler, error: m.error || null })),
} : { events };
process.stdout.write(JSON.stringify(outcome), () => process.exit(0));
"""


def _run_download_relay_harness(mode):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension download boundary'
    result = run_node_argv(
        node, ['-e', _DOWNLOAD_RELAY_HARNESS,
               str(ROOT / 'extension' / 'content.js'),
               str(ROOT / 'extension' / 'page.js'), mode],
        ROOT)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_download_that_never_started_reaches_onerror(tmp):
    """An absent response is a failure, not a success with nothing in it."""
    del tmp
    for mode, expected in (
        ('lastError', 'error:Could not establish connection.'),
        ('empty', 'error:background started no download'),
        ('error', 'error:Invalid filename'),
        ('ok', 'load'),
    ):
        actual = _run_download_relay_harness(mode)
        assert actual['events'] == [expected], (mode, actual)


def test_the_fire_and_forget_page_apis_reach_the_worker_and_are_acked(tmp):
    """GM.openInTab and GM.notification cross the relay with their payload."""
    del tmp
    actual = _run_download_relay_harness('fire-and-forget')
    assert actual == {
        'sent': [
            {'type': 'openTab', 'url': 'about:blank', 'active': False},
            {'type': 'notification', 'title': 'Run finished',
             'text': 'Nothing failed.'},
        ],
        'acknowledged': [
            {'handler': 'openInTab', 'error': None},
            {'handler': 'notification', 'error': None},
        ],
    }, actual


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gmtransfers_')


if __name__ == '__main__':
    raise SystemExit(main())
