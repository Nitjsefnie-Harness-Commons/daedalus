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
    event_target_stub)


_RELAY_ADMISSION_HARNESS = event_target_stub() + r"""
const fs = require('fs'), vm = require('vm');
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
vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context,
  { filename: backgroundPath });
const state = () => vm.runInContext(
  '[_relayInFlight, _relayWaiters.length, _pendingCommands]', context);
function send(message) {
  return new Promise((resolve) => {
    const listener = chrome.runtime.onMessage.listeners[0];
    const sender = { tab: { id: 7 }, documentId: 'd7' };
    assert.equal(listener(message, sender, (value) => {
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
const abort = (id) => chrome.runtime.onMessage.listeners[0](
  { type: 'abortFetch', fetchId: String(id) }, {}, () => {});
function relay(timeout = 60000 + answers.length) {
  const id = String(answers.length);
  answers.push(send({ type: 'fetch', fetchId: id,
    url: 'https://example.com/relay', responseType: 'arraybuffer',
    timeout, headers: { 'X-Relay-Id': id } }));
}
const finish = (index, fail = false) => (fail ? started[index].reject(
  Error('relay failed')) : started[index].resolve(new Response('body')));
async function finishAll() {
  for (let turn = 0; turn < 12; turn++) {
    for (let i = 0; i < started.length; i++) finish(i); await delay();
  }
  assert.equal(completed.length, answers.length, 'terminal answer count');
  assert.equal(new Set(completed.map((r) => r.id)).size, answers.length);
  assert.deepEqual(Array.from(state()), [0, 0, 0], 'retained work');
  assert.equal(timers.size, 0, 'retained timers');
  assert.equal(vm.runInContext('_fetchControllers.size', context), 0);
}
async function withdrawal(kind) {
  if (kind === 'pre' || kind === 'pre-held') {
    const held = kind === 'pre-held', count = held ? 8 : 0;
    for (let i = 0; i < count; i++) relay(); await delay();
    const pending = held ? commandRun(() => command.promise) : null;
    assert.equal(vm.runInContext('_relayHold', context), held);
    context.AbortController = class extends AbortController {
      constructor() { super(); this.abort(); }
    };
    relay(); await delay();
    assert.equal(completed.length, 1, 'pre-aborted request did not answer');
    assert.equal(started.length, count, 'pre-aborted request fetched');
    assert.deepEqual(completed[0], { id: String(count),
      error: 'fetch relay queue wait exceeded',
      timedOut: false, aborted: false, tooLarge: false });
    assert.deepEqual(Array.from(state()), [count, 0, Number(held)]);
    assert.equal(vm.runInContext('_relayHold', context), held);
    if (pending) { command.resolve(); await pending; }
  } else {
    for (let i = 0; i < 8; i++) relay(); await delay();
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
        error: kind === 'timeout' ? 'fetch relay queue wait exceeded'
          : 'aborted by the caller', timedOut: false,
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
      // An admitted request's expiry is the fetch's timeout, not the queue's.
      if (kind === 'timeout') [...timers].find((t) => t.ms === 60007).fn();
    }
  }
  await finishAll();
  assert.equal(started.some((r) => r.id === 8), false);
  const late = completed.find((r) => r.id === '7' && kind === 'timeout');
  if (late) assert.equal(late.error, 'fetch timeout after 60007ms');
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
    const b = commandRun(() => second.promise); relay();
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
    const next = commandRun(() => first.promise); relay(); await delay();
    assert.equal(started.length, 3, 'fresh command batch did not hold');
    first.resolve(); await next; await delay();
    assert.equal(started.length, 4, 'fresh batch did not drain');
  }
  await finishAll();
}
async function evalPending() {
  // Four 4 MiB bodies land while an eval waits: each slice's encode is
  // followed by its own send, and the eval reaches its page before the end.
  const log = [], inject = [], got = [], shapes = new Set(), big = 4 << 20;
  const body = Uint8Array.from({ length: big }, (_, i) => i % 251);
  context.log = log;
  vm.runInContext('const encode = bytesToBase64; bytesToBase64 = (b) => '
    + '(log.push(b.byteLength), encode(b))', context);
  // Relay 1's page never takes its second slice, relay 2's send rejects, and
  // relay 3's caller aborts after its first.
  chrome.tabs.sendMessage = (tabId, slice, target) => {
    log.push('slice');
    shapes.add(JSON.stringify([slice.type, slice.size, target]));
    if (slice.fetchId === '0') got.push([slice.at, slice.data]);
    if (slice.fetchId === '3') abort(3);
    const take = slice.at === 0 || [true, null, false, true][slice.fetchId];
    return new Promise((yes, no) => (take === false ? no(Error('gone'))
      : take && setImmediate(() => yes(true))));
  };
  chrome.scripting.executeScript = ({ args }) => (log.push(args && args[0]
    ? 'eval' : 'probe'), inject[inject.push(deferred()) - 1].promise);
  relay(); relay(); relay(); relay(); await delay();
  const posted = vm.runInContext("dispatchCommand({id: 'e', tabId: '7', "
    + "chromeTab: 7, code: 'Date.now()'})", context).then(
    () => log.push('posted'));
  started.forEach((r) => r.resolve(new Response(body, { statusText: 'Ok' })));
  await delay(); inject[0].resolve([{ result: true }]); await delay();
  inject[1].resolve([{ result: { r: 1, ms: 0 } }]); await posted;
  for (let t = 0; t < 400 && completed.length < 3; t++) await delay();
  const sizes = log.filter(Number.isInteger);
  assert.ok(Math.max(...sizes) <= 1 << 20, 'one encode held ' + sizes);
  assert.deepEqual([...shapes], [JSON.stringify(['gm-slice', big,
    { documentId: 'd7' }])], 'slice shape');
  assert.ok(log.every((e, i) => !sizes.includes(e) || log[i + 1] === 'slice'),
    'encodes ran ahead of their sends: ' + log.join(' '));
  assert.ok(log.indexOf('eval') < log.lastIndexOf('slice'),
    'the eval waited behind whole deliveries: ' + log.join(' '));
  const parts = got.map(([at, data]) => [at, Buffer.from(data, 'base64')]);
  assert.deepEqual(parts.map(([at]) => at), parts.map((_, i) => parts
    .slice(0, i).reduce((n, [, b]) => n + b.length, 0)), 'slice offsets');
  assert.ok(Buffer.concat(parts.map(([, b]) => b)).equals(body), 'body');
  [...timers].find((t) => t.ms === 10000)?.fn(); await delay();
  // finalUrl and statusText are the response's: '' is resp.url, not msg.url.
  assert.deepEqual(completed.map((r) => [r.id, r.chunked || r.error,
    r.statusText, r.finalUrl]).sort(), [['0', big, 'Ok', ''],
    ['1', 'page took no relay slice in 10000 ms', undefined, undefined],
    ['2', 'page refused a relay slice', undefined, undefined],
    ['3', 'aborted by the caller', undefined, undefined]]);
  await finishAll();
}
(async () => {
  await delay();
  // The delays the worker arms for a fetch the page sent with no timeout,
  // then for a page that never takes that fetch's first body slice.
  if (mode === 'default') {
    relay(0); await delay();
    chrome.tabs.sendMessage = () => deferred().promise;
    started[0].resolve(new Response(new Uint8Array(1 << 20))); await delay();
    return process.stdout.write(JSON.stringify([...timers].map((t) => t.ms)));
  }
  if (mode.includes(':')) {
    const [group, kind] = mode.split(':');
    await (group === 'withdraw' ? withdrawal(kind)
      : group === 'eval' ? evalPending() : priority(kind));
    process.stdout.write('{}'); return;
  }
  const full = ['ceiling', 'error', 'abort'].includes(mode);
  const n = full ? 8 : mode === 'idle' ? 0 : 2;
  for (let i = 0; i < n; i++) relay(); await delay();
  let settled = false, pending;
  if (!full) {
    pending = mode === 'replay' ? send({ type: 'replayHotfixes' })
      : vm.runInContext("dispatchCommand({type:'reload',tabId:7})", context);
    pending.then(() => { settled = true; });
  }
  relay(); if (mode !== 'settled') relay(); await delay();
  const queued = started.length;
  if (mode === 'settled') {
    command.resolve({}); await pending; await delay();
    assert.equal(started.length, 3, 'settlement did not drain the waiter');
  }
  if (n && mode === 'abort') abort(0);
  else if (n) finish(0, mode === 'error');
  await delay(); const one = started.length;
  if (mode === 'ceiling') relay();
  for (let i = 1; i < n; i++) finish(i); await delay();
  const drained = started.length, pendingAtDrain = !!pending && !settled;
  if (mode === 'overlap') {
    pending = Promise.all([pending, vm.runInContext(
      "dispatchCommand({type:'reload',tabId:7})", context)]);
    relay(); await delay();
    assert.equal(started.length, 5, 'commands rearmed a drained hold');
  }
  if (pending) { command.resolve({}); await pending; } await delay();
  for (let i = n; i < started.length; i++) finish(i); await delay();
  process.stdout.write(JSON.stringify({ queued, one, drained, pendingAtDrain,
    startOrder: started.map(({ id }) => id), completed: completed.length,
    errors: completed.filter((value) => value.error).map((v) => v.error) }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""


def test_relay_admission_yields_to_one_command_drain(tmp):
    del tmp
    failures = []
    for mode, queued, one, drained, pending, errors in (
        ('ceiling', 8, 9, 11, False, []),
        *((mode, 2, 2, 4, True, []) for mode in (
            'command', 'replay', 'overlap')),
        ('settled', 2, 3, 3, False, []),
        ('idle', 2, 2, 2, True, []),
        ('error', 8, 9, 10, False, ['relay failed']),
        ('abort', 8, 9, 10, False, ['aborted by the caller']),
        *((mode, 0, 0, 0, False, []) for mode in (
            'withdraw:abort', 'withdraw:timeout', 'withdraw:grant',
            'withdraw:pre', 'withdraw:pre-held', 'withdraw:full',
            'priority:first', 'priority:last', 'priority:throw',
            'priority:reject', 'priority:sync', 'eval:pending')),
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
            'pendingAtDrain': pending, 'errors': errors,
            'completed': drained + (mode == 'overlap'),
            'startOrder': list(range(drained + (mode == 'overlap'))),
        }
        if actual != expected: failures.append((mode, actual, expected))
    assert not failures, failures


_PAGE_RELAY_SETUP = CONTENT_SCRIPT_PAGE + r"""
const fs = require('fs'), vm = require('vm');
const listeners = {}, messages = [], posted = [], sent = [], writes = [];
const hooks = [], acks = [], transfers = []; let held;
// Letters, with a two-byte UTF-8 character across the 1 MiB slice boundary.
const sliceBody = () => Buffer.alloc(5 << 19, 'abcdefghijklmnopqrstuvwxyz')
  .fill('é', (1 << 20) - 1, (1 << 20) + 1);
const windowObject = {
  addEventListener: (type, l) => (listeners[type] ||= []).push(l),
  postMessage(message, origin, moved = []) { transfers.push(...moved);
    posted.push(message); messages.push(message); },
};
const chrome = {
  runtime: {
    lastError: null, onMessage: { addListener: (l) => hooks.push(l) },
    connect() { return { disconnect() {}, postMessage() {},
      onDisconnect: { addListener() {} } }; },
    sendMessage(payload, callback) {
      sent.push(payload);
      if (typeof callback !== 'function') return; held = callback;
      if (mode === 'lastError') {
        chrome.runtime.lastError = {
          message: 'Could not establish connection.' };
        try { callback(undefined); }
        finally { chrome.runtime.lastError = null; }
      } else if (mode.startsWith('sliced') && payload.type === 'fetch') {
        const whole = sliceBody(), size = whole.length;
        for (let at = 0; at < size; at += 1 << 20) {
          const data = whole.subarray(at, at + (1 << 20)).toString('base64');
          hooks.forEach((hook) => hook({ type: 'gm-slice', at, size, data,
            fetchId: payload.fetchId }, {}, (ack) => acks.push(ack)));
          if (mode === 'sliced-abort') handle.abort();
          // Just inside the re-arm: slice wait or timeout, plus the margin.
          advance(Math.max(sliceMs, timeout) + 4999
            + (mode === 'sliced-expire'));
        }
        callback({ status: 200, headers: {},
          chunked: size + (mode === 'sliced-short') });
      } else if (!mode.startsWith('lost')) callback(backgroundAnswer(payload));
    },
  },
};
const context = {
  window: windowObject, chrome,
  navigator: { clipboard: { writeText(text) {
    writes.push(text);
    return mode === 'reject' ? Promise.reject(Error('NotAllowedError'))
      : Promise.resolve();
  } } },
  location: { hostname: 'page.invalid', href: 'about:blank' },
  setTimeout, clearTimeout, setInterval, clearInterval, performance,
  atob, TextDecoder, console: { log() {}, error() {} }, ...contentScriptPage(),
};
context.globalThis = context;
function loadPage(extra = {}) {
  Object.assign(context, extra);
  for (const path of [contentPath, pagePath]) vm.runInNewContext(
    fs.readFileSync(path, 'utf8'), context, { filename: path });
}
function flushMessages() {
  for (let guard = 0; messages.length && guard++ < 100;) {
    const data = messages.shift();
    for (const l of listeners.message || []) l({ source: windowObject, data });
  }
}
"""


def _run_page_harness(harness, *args):
    """Drive one page-relay harness through content.js and page.js."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension page relay'
    result = run_node_argv(node, [
        '-e', harness, str(ROOT / 'extension' / 'content.js'),
        str(ROOT / 'extension' / 'page.js'), *args], ROOT)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


_FETCH_RELAY_HARNESS = _PAGE_RELAY_SETUP + r"""
const [contentPath, pagePath, responseText, mode, relayMs, sliceMs] =
  process.argv.slice(1);
function backgroundAnswer() { return JSON.parse(responseText); }
// A fake clock reading a delay as int32, as browsers do; advance(ms) fires
// each timer falling due, in due order.
const timers = new Map(); let now = 0, timerIds = 0;
function advance(ms) {
  for (const end = now + ms; ; ) {
    flushMessages();
    const [id, due] = [...timers].sort((a, b) => a[1].at - b[1].at)[0] || [];
    if (!due || due.at > end) { now = end; return; }
    timers.delete(id); now = due.at; due.fn();
  }
}
loadPage({ clearTimeout: (id) => timers.delete(id), setTimeout: (fn, ms) =>
  (timers.set(++timerIds, { fn, at: now + Math.max(ms | 0, 0) }), timerIds) });
const events = []; let loadDetail = null, body = null, early;
// No timeout and one the relay does not read as one (both its default),
// the largest delay a timer takes, and one past the 10 s slice wait.
const timeouts = { 'lost-default': undefined, 'lost-text': '50',
  'lost-max': 2 ** 31 - 1, 'sliced-text': 30000 };
const timeout = mode in timeouts ? timeouts[mode] : 50;
var handle = windowObject.GM.xmlhttpRequest({
  url: 'about:blank#slow', timeout,
  responseType: mode === 'sliced' ? 'arraybuffer' : undefined,
  // Each settling callback calls abort(), which must then do nothing.
  onabort: () => events.push('abort'),
  onload: (detail) => {
    loadDetail = detail; events.push('load'); handle.abort();
    const text = typeof detail.response === 'string';
    body = { text, same: Buffer.from(text ? detail.response
      : new Uint8Array(detail.response || 0)).equals(sliceBody()) };
  },
  onerror: (detail) => {
    events.push('error:' + (detail && detail.error)); handle.abort(); },
  ontimeout: () => (events.push('timeout'), handle.abort()),
});
flushMessages();
if (mode.startsWith('lost')) {
  // Unanswered: pending just inside its bound, settled once at it.
  const bound = { lost: 5050, 'lost-max': 2 ** 31 - 1 }[mode];
  advance((bound || Number(relayMs) + 5000) - 1);
  early = events.length; advance(1); handle.abort(); flushMessages();
  held({ status: 200, data: 'late', headers: {} }); flushMessages();
}
advance(1e5);

// The content script arms a keepalive timer that would hold the event loop
// open forever; exit once the answer has actually been flushed.
process.stdout.write(JSON.stringify({ events, loadDetail,
  relayed: posted.filter((m) => m.direction === 'daedalus-bg-to-page')
    .map((m) => m.event),
  requestedTimeout: (sent.find((m) => m.type === 'fetch') || {}).timeout,
  body, acks, transferred: transfers.length, early, aborts: [
    posted.filter((m) => m.handler === 'abortRequest').length,
    sent.filter((m) => m.type === 'abortFetch').length],
}), () => process.exit(0));
"""


# Each worker answer and what the page must see: a timeout is its own
# event, an undelivered request is an error, finalUrl is the response's URL
# (the request URL only as a fallback), a sliced body lands byte for byte,
# an abort refuses the slices after it, and a request unanswered past its
# bound gets one ontimeout, one cancellation and nothing after.
_SLICED = {'text': False, 'same': True}
_PAGE_ANSWERS = (
    ({'error': 'fetch timeout after 50ms', 'timedOut': True}, 'answer',
     {'events': ['timeout'], 'relayed': ['timeout']}),
    ({'error': 'network unreachable'}, 'answer',
     {'events': ['error:network unreachable'], 'relayed': ['error']}),
    (None, 'lastError', {'events': [
        'error:Could not establish connection.'], 'relayed': ['error']}),
    ({'status': 200, 'statusText': 'OK', 'data': 'body', 'headers': {},
      'finalUrl': 'https://redirected.example.com/final'}, 'answer',
     {'events': ['load'], 'finalUrl': 'https://redirected.example.com/final',
      'statusText': 'OK'}),
    ({'status': 200, 'data': 'body', 'headers': {}}, 'answer',
     {'events': ['load'], 'finalUrl': 'about:blank#slow', 'statusText': ''}),
    (None, 'sliced', {'events': ['load'], 'body': _SLICED,
                      'acks': [True] * 3, 'transferred': 1}),
    (None, 'sliced-text', {'events': ['load'], 'acks': [True] * 3,
                           'body': {**_SLICED, 'text': True},
                           'transferred': 0, 'requestedTimeout': 30000}),
    (None, 'sliced-short', {
        'events': ['error:relay body arrived incomplete']}),
    (None, 'sliced-abort', {'events': ['abort'], 'aborts': [1, 1],
                            'acks': [True, False, False]}),
    *((None, mode, {'events': ['timeout'], 'early': 0, 'aborts': [1, 1],
                    'requestedTimeout': sent}) for mode, sent in (
        ('lost', 50), ('lost-default', 0), ('lost-text', '50'),
        ('lost-max', 2 ** 31 - 1))),
    (None, 'sliced-expire', {'events': ['timeout'], 'aborts': [1, 1],
                             'acks': [True, False, False]}),
)


def test_each_worker_answer_reaches_the_page_as_its_own_event(tmp):
    del tmp
    relay_ms, slice_ms = json.loads(run_node_argv(shutil.which('node'), [
        '-e', _RELAY_ADMISSION_HARNESS,
        str(EXTENSION_ROOT / 'background.js'), 'default'], ROOT).stdout)
    for answer, mode, expected in _PAGE_ANSWERS:
        out = _run_page_harness(
            _FETCH_RELAY_HARNESS, json.dumps(answer), mode, str(relay_ms),
            str(slice_ms))
        seen = {**out, **(out['loadDetail'] or {})}
        expected = {'requestedTimeout': 50, 'aborts': [0, 0], **expected}
        assert {key: seen[key] for key in expected} == expected, (mode, out)


_CLIPBOARD_RELAY_HARNESS = _PAGE_RELAY_SETUP + r"""
const [contentPath, pagePath, mode] = process.argv.slice(1);
function backgroundAnswer() { return {}; }
loadPage();
(async () => {
  let settled = 'pending';
  const returned = windowObject.GM.setClipboard('replacement');
  const isPromise = !!(returned && typeof returned.then === 'function');
  if (isPromise) returned.then(() => { settled = 'resolved'; },
    () => { settled = 'rejected'; });
  // Let the write settle, then deliver what the content script posted back.
  for (let t = 0; t < 10; t++) { await Promise.resolve(); flushMessages(); }
  await Promise.resolve();
  process.stdout.write(JSON.stringify({
    isPromise, settled, writes,
    acknowledged: posted.filter((m) => m.direction === 'daedalus-bg-to-page'
      && m.handler === 'setClipboard')
      .map((m) => ({ error: m.error || null })),
  }), () => process.exit(0));
})();
"""


def test_a_refused_clipboard_write_reaches_the_caller(tmp):
    """A clipboard write that the browser refuses must not report success."""
    del tmp
    for mode, settled, error in (('reject', 'rejected', 'NotAllowedError'),
                                 ('resolve', 'resolved', None)):
        out = _run_page_harness(_CLIPBOARD_RELAY_HARNESS, mode)
        assert (out['writes'], out['isPromise'], out['settled'],
                out['acknowledged']) == (
            ['replacement'], True, settled, [{'error': error}]), out


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
  url: 'about:blank', name: 'file.bin', onload: () => events.push('load'),
  onerror: (detail) => events.push('error:' + (detail && detail.error)),
});
flushMessages();

const outcome = mode === 'fire-and-forget' ? {
  sent: sent.filter((m) => ['openTab', 'notification'].includes(m.type)),
  acknowledged: posted.filter((m) => m.direction === 'daedalus-bg-to-page')
    .map((m) => ({ handler: m.handler, error: m.error || null })),
} : { events };
process.stdout.write(JSON.stringify(outcome), () => process.exit(0));
"""


def test_a_download_that_never_started_reaches_onerror(tmp):
    """An absent response is a failure, not a success with nothing in it;
    GM.openInTab and GM.notification cross the relay with their payload."""
    del tmp
    for mode, expected in (
        ('lastError', 'error:Could not establish connection.'),
        ('empty', 'error:background started no download'),
        ('error', 'error:Invalid filename'), ('ok', 'load'),
    ):
        actual = _run_page_harness(_DOWNLOAD_RELAY_HARNESS, mode)
        assert actual['events'] == [expected], (mode, actual)
    actual = _run_page_harness(_DOWNLOAD_RELAY_HARNESS, 'fire-and-forget')
    assert actual == {
        'sent': [{'type': 'openTab', 'url': 'about:blank', 'active': False},
                 {'type': 'notification', 'title': 'Run finished',
                  'text': 'Nothing failed.'}],
        'acknowledged': [{'handler': handler, 'error': None}
                         for handler in ('openInTab', 'notification')],
    }, actual


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gmtransfers_')


if __name__ == '__main__':
    raise SystemExit(main())
