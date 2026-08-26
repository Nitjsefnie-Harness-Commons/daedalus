"""The Node VM harness the eval-relay tests run the shipped scripts in.

Not a suite itself — run_tests.py only loads `test_*.py`.

The background, content and page scripts are loaded into one VM with a fake
browser under them, so an evaluation can be watched all the way through:
which channel took the source, what the page was able to answer with, and
which invocation each result belonged to.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _worker_sources import import_scripts_stub  # noqa: E402


_EVAL_RELAY_OVERLAP_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, contentPath, pagePath, orderText, mode = 'overlap',
  relayHostname = '', cdpText = ''] = process.argv.slice(1);
const cdpEnabled = cdpText === '1' || cdpText === 'midflight';
const cdpFailsMidFlight = cdpText === 'midflight';
let cdpSideEffects = 0;
const completionOrder = JSON.parse(orderText);
let scriptingCalls = 0;
let injectionShape = '';
const backgroundListeners = [];
const contentListeners = [];
const windowListeners = [];
const windowMessages = [];
const postedResults = [];
const evalResolvers = {};
const slowSignals = [];
let relaySequence = 0;

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

const backgroundChrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'eval-token',
        'daedalus-server': 'test-bridge',
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
      const tabs = [{ id: 7, url: '', title: 'Page' }];
      if (callback) {
        callback(tabs);
        return undefined;
      }
      return Promise.resolve(tabs);
    },
    get: async (tabId) => ({
      id: tabId,
      url: '',
      title: 'Page',
    }),
    async sendMessage(_tabId, message) {
      for (const listener of contentListeners) listener(message);
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => {
      if (!cdpEnabled) throw new Error('debugger unavailable in relay test');
    },
    detach: async () => {},
    // Stand-in for the V8 inspector channel. The marker records that channel;
    // it makes no claim about a value the submitted source obtained from page
    // state or page promise machinery.
    sendCommand: async (_target, method, params) => {
      if (method !== 'Runtime.evaluate') return {};
      if (cdpFailsMidFlight) {
        // The inspector started the source and then went away. Nothing can
        // prove the side effect did not happen, so no other evaluator may run.
        cdpSideEffects++;
        throw new Error('inspector detached mid-evaluation');
      }
      try {
        return { result: { value: await vm.runInNewContext(params.expression, {}) } };
      } catch (error) {
        return { exceptionDetails: { exception: { description: String(error) } } };
      }
    },
  },
  scripting: {
    async executeScript(injection) {
      scriptingCalls++;
      if (mode === 'injection-shapes') {
        if (injection.func.name === '_canUseMainWorldEval') {
          return [{ result: true }];
        }
        if (injectionShape === 'reject') {
          throw new Error('executeScript rejected');
        }
        if (injectionShape === 'empty') return [];
        if (injectionShape === 'frame-error') {
          return [{ error: 'frame exception' }];
        }
        if (injectionShape === 'missing-result') return [{}];
        if (injectionShape === 'bare-null') return [{ result: null }];
        if (injectionShape === 'genuine-null') {
          return [{ result: { r: null, ms: 1 } }];
        }
        if (injectionShape === 'eval-exception') {
          return [{ result: { e: 'operator exception', ms: 1 } }];
        }
        if (injectionShape === 'page-substitution') {
          return [{ result: 'PAGE-SUBSTITUTED' }];
        }
        throw new Error('unknown injection shape ' + injectionShape);
      }
      if (mode !== 'preemption' && mode !== 'poisoned') {
        throw new Error('scripting unavailable in relay overlap test');
      }
      relayContext.__injectionArgs = injection.args || [];
      const source = '(' + injection.func.toString()
        + ')(...__injectionArgs)';
      const result = await vm.runInContext(source, relayContext);
      delete relayContext.__injectionArgs;
      return [{ result }];
    },
  },
  runtime: {
    onMessage: eventTarget(backgroundListeners),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
};

const backgroundContext = vm.createContext({
  chrome: backgroundChrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.includes('/slow')) {
      // Never settles on its own. The only way out is the AbortSignal, which
      // is the whole question: a relay whose abort reaches nothing leaves this
      // request running until its timeout.
      slowSignals.push(init.signal);
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          const error = new Error('aborted');
          error.name = 'AbortError';
          reject(error);
        });
      });
    }
    if (url.endsWith('/result') && init.method === 'POST') {
      postedResults.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    if (url.includes('/stream?')) return response(503, { error: 'disabled' });
    return response(200, { ok: true });
  },
  crypto: { randomUUID: () => 'relay-' + (++relaySequence) },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});
""" + import_scripts_stub('backgroundContext') + r"""

const windowObject = {
  addEventListener(type, listener) {
    if (type === 'message') windowListeners.push(listener);
  },
  postMessage(data) {
    windowMessages.push(data);
    for (const listener of [...windowListeners]) {
      listener({ source: windowObject, data });
    }
  },
};

const relayChrome = {
  runtime: {
    lastError: null,
    onMessage: eventTarget(contentListeners),
    sendMessage(message) {
      for (const listener of backgroundListeners) {
        listener(message, { tab: { id: 7 } }, () => {});
      }
    },
    connect() {
      return {
        name: 'keepalive',
        postMessage() {},
        disconnect() {},
        onDisconnect: eventTarget(),
      };
    },
    getManifest: () => ({ version: '0.18.0' }),
  },
  storage: {
    local: {
      get(_keys, callback) { callback({}); },
      set(_data, callback) { if (callback) callback(); },
      remove(_keys, callback) { if (callback) callback(); },
    },
  },
};

const documentObject = {
  head: { appendChild() {} },
  documentElement: { appendChild() {} },
  addEventListener() {},
  removeEventListener() {},
  createElement() {
    return {
      remove() {},
      set onload(_listener) {},
      set onerror(_listener) {},
    };
  },
};

const relayContext = vm.createContext({
  window: windowObject,
  chrome: relayChrome,
  document: documentObject,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: relayHostname },
  performance,
  evalResolvers,
  Blob,
  URL,
  Uint8Array,
  ArrayBuffer,
  TextEncoder,
  atob,
  btoa,
  setTimeout: () => 1,
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, error() {} },
});

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function waitFor(predicate, label) {
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (predicate()) return;
    await delay();
  }
  throw new Error('timed out waiting for ' + label);
}

async function run() {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), backgroundContext);
  await vm.runInContext('loadConfig()', backgroundContext);
  vm.runInContext(fs.readFileSync(contentPath, 'utf8'), relayContext);
  vm.runInContext(fs.readFileSync(pagePath, 'utf8'), relayContext);

  if (mode === 'injection-shapes') {
    const shapes = ['reject', 'empty', 'frame-error', 'missing-result',
      'bare-null', 'genuine-null', 'eval-exception', 'page-substitution'];
    const outcomes = {};
    for (const shape of shapes) {
      injectionShape = shape;
      backgroundContext.command = {
        id: '_eval',
        type: 'eval',
        code: shape === 'genuine-null' ? 'null' : '2 + 2',
        chromeTab: 7,
        _did: 'did-' + shape,
      };
      const before = postedResults.length;
      await vm.runInContext('dispatchCommand(command)', backgroundContext);
      await waitFor(
        () => postedResults.length === before + 1,
        'injection result for ' + shape);
      const posted = postedResults[before];
      outcomes[shape] = {
        hasResult: Object.prototype.hasOwnProperty.call(posted, 'result'),
        result: posted.result === undefined ? null : posted.result,
        error: posted.error === undefined ? null : posted.error,
        world: posted.world || null,
      };
    }
    return outcomes;
  }

  if (mode === 'poisoned') {
    // A hostile page replaces both evaluator primitives before the command
    // arrives. Everything the injected MAIN-world function resolves — `eval`
    // and `Function` alike — comes from these page-owned globals.
    relayContext.eval = (source) => 'FORGED-EVAL:' + source;
    relayContext.Function = function () {
      return function () { return 'FORGED-FUNCTION'; };
    };
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: '2 + 2',
      chromeTab: 7,
      _did: 'did-poisoned',
    };
    await vm.runInContext('dispatchCommand(command)', backgroundContext);
    await waitFor(() => postedResults.length === 1, 'poisoned eval result');
    return {
      result: postedResults[0].result,
      world: postedResults[0].world,
      deliveryId: postedResults[0]._did || null,
      scriptingCalls,
    };
  }

  if (mode === 'midflight') {
    relayContext.eval = (source) => 'FORGED-EVAL:' + source;
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: '2 + 2',
      chromeTab: 7,
      _did: 'did-midflight',
    };
    await vm.runInContext('dispatchCommand(command)', backgroundContext);
    await waitFor(() => postedResults.length === 1, 'mid-flight eval result');
    return {
      result: postedResults[0].result === undefined
        ? null : postedResults[0].result,
      error: postedResults[0].error,
      world: postedResults[0].world || null,
      cdpSideEffects,
      scriptingCalls,
    };
  }

  if (mode === 'marker') {
    windowObject.addEventListener('message', (event) => {
      const message = event.data;
      if (!message || message.direction !== 'daedalus-eval') return;
      windowObject.postMessage({
        direction: 'daedalus-eval-result',
        id: message.id,
        relayId: message.relayId,
        r: 'FORGED',
        world: 'scripting',
        hostname: 'cdp',
      });
    });
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: 'await new Promise(() => {})',
      _did: 'did-marker',
    };
    await vm.runInContext('dispatchCommand(command)', backgroundContext);
    await waitFor(() => postedResults.length === 1, 'forged page result');
    return {
      result: postedResults[0].result,
      world: postedResults[0].world,
      deliveryId: postedResults[0]._did || null,
    };
  }

  if (mode === 'gm-abort') {
    relayContext.abortProbe = {};
    vm.runInContext(
      'abortProbe.handle = window.GM.xmlhttpRequest({'
      + ' url: "https://example.com/slow",'
      + ' onload: function() { abortProbe.load = true; },'
      + ' onerror: function() { abortProbe.error = true; },'
      + ' ontimeout: function() { abortProbe.timeout = true; },'
      + ' onabort: function() { abortProbe.abort = true; },'
      + '})', relayContext);
    await waitFor(() => slowSignals.length === 1, 'the relayed fetch to start');
    const inFlight = vm.runInContext('_fetchControllers.size', backgroundContext);
    vm.runInContext('abortProbe.handle.abort()', relayContext);
    vm.runInContext('abortProbe.handle.abort()', relayContext);
    await waitFor(() => slowSignals[0].aborted, 'the fetch to be cancelled');
    await delay();
    await delay();
    return {
      inFlight,
      aborted: slowSignals[0].aborted,
      onabort: Boolean(relayContext.abortProbe.abort),
      onload: Boolean(relayContext.abortProbe.load),
      onerror: Boolean(relayContext.abortProbe.error),
      ontimeout: Boolean(relayContext.abortProbe.timeout),
      abortMessages: windowMessages.filter(
        (message) => message.handler === 'abortRequest').length,
      controllers: vm.runInContext('_fetchControllers.size', backgroundContext),
    };
  }

  if (mode === 'preemption') {
    windowObject.addEventListener('message', (event) => {
      const message = event.data;
      if (!message || message.direction !== 'daedalus-eval') return;
      windowObject.postMessage({
        direction: 'daedalus-eval-result',
        id: message.id,
        relayId: message.relayId,
        r: 'FORGED',
      });
    });
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: 'await new Promise((resolve) => {'
        + ' evalResolvers.legit = () => resolve("LEGIT");'
        + ' })',
      _did: 'did-legit',
    };
    const execution = vm.runInContext(
      'dispatchCommand(command)', backgroundContext);
    await waitFor(() => Boolean(evalResolvers.legit), 'evaluation to start');
    evalResolvers.legit();
    await execution;
    await delay();
    return {
      pageEvalMessages: windowMessages.filter(
        (message) => message.direction === 'daedalus-eval').length,
      results: postedResults.map((item) => ({
        result: item.result,
        deliveryId: item._did || null,
      })),
    };
  }

  const commands = ['owner-a', 'owner-b'].map((owner) => ({
    id: '_eval',
    type: 'eval',
    code: 'await new Promise((resolve) => {'
      + ' evalResolvers["' + owner + '"] = () => resolve("' + owner + '");'
      + ' })',
    _did: owner === 'owner-a' ? 'did-a' : 'did-b',
  }));
  backgroundContext.commands = commands;
  vm.runInContext('dispatchCommand(commands[0])', backgroundContext);
  vm.runInContext('dispatchCommand(commands[1])', backgroundContext);
  await waitFor(
    () => Object.keys(evalResolvers).length === 2,
    'both page evaluations to start');

  const evalMessages = windowMessages.filter(
    (message) => message.direction === 'daedalus-eval');
  const firstRelay = evalMessages[0] && evalMessages[0].relayId;
  for (const listener of backgroundListeners) {
    listener({
      type: 'result', id: '_eval', relayId: firstRelay,
      result: 'wrong-tab', error: null, world: '',
    }, { tab: { id: 8 } }, () => {});
  }
  await delay();

  for (const owner of completionOrder) {
    evalResolvers[owner]();
    await waitFor(
      () => postedResults.some((item) => item.result === owner),
      'page result for ' + owner);
  }

  windowObject.postMessage({
    direction: 'daedalus-eval-result',
    id: '_eval',
    relayId: 'not-pending',
    r: 'unrecognised',
  });
  await delay();

  return {
    relayIds: evalMessages.map((message) => message.relayId || null),
    results: postedResults.map((item) => ({
      result: item.result,
      deliveryId: item._did || null,
    })),
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def run_eval_relay_overlap(order):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval relay'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(ROOT / 'extension' / 'background.js'),
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js'), json.dumps(order)],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_eval_same_tab_preemption():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'preemption'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_gm_abort():
    node = shutil.which('node')
    assert node, 'node is required to execute the GM relay'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'gm-abort'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_eval_relay_marker(hostname):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'marker', hostname],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_eval_after_cdp_fails_mid_flight():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'midflight', '', 'midflight'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_eval_with_poisoned_page_globals(cdp_available):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'poisoned', '',
         '1' if cdp_available else '0'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_main_world_injection_shapes():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'injection-shapes'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)
