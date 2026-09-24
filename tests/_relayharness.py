"""The Node VM harness the eval-relay tests run the shipped scripts in.

Not a suite itself — run_tests.py only loads `test_*.py`.

The background, content and page scripts are loaded into one VM with a fake
browser under them, so an evaluation can be watched all the way through:
which channel took the source, what the page was able to answer with, and
which invocation each result belonged to.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402
from _stream_fake import (  # noqa: E402
    STRICT_FETCH, assert_gate_clean, require_node, run_inline_gate)
from _worker_sources import (  # noqa: E402
    STREAM_RESPONSE, import_scripts_stub)

_BRIDGE = (
    str(EXTENSION_ROOT / 'background.js'),
    str(EXTENSION_ROOT / 'content.js'),
    str(EXTENSION_ROOT / 'page.js'),
)
SYNC = 'POST /sync-tabs'
REGISTER = 'POST /register'
RESULT = 'POST /result'
RELAY_HOST = 'https://example.com'
SLOW = 'GET ' + RELAY_HOST + '/slow'


_EVAL_RELAY_OVERLAP_HARNESS = (
    r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, contentPath, pagePath, orderText, mode = 'overlap',
  relayHostname = '', cdpText = ''] = process.argv.slice(1);
// The plan rides last on the command line; the inline driver appends it as
// JSON text, so read it here and parse only when it arrived as text.
const gatePlanArg = process.argv[process.argv.length - 1];
const plan = typeof gatePlanArg === 'string'
  ? JSON.parse(gatePlanArg) : gatePlanArg;
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
const evalResolvers = {};
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

const BRIDGE_URL = 'https://bridge.example.com';
const streamFetches = [];
const resultPosts = [];
const nonStreamFetches = [];
const refusedFetches = [];
const badOrigins = [];

""" + STREAM_RESPONSE + r"""
""" + STRICT_FETCH + r"""

const backgroundChrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'eval-token',
        'daedalus-server': BRIDGE_URL,
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
        // vm-load-exempt: evaluates the CDP expression handed in
        return { result: { value: await vm.runInNewContext("""
    r"""params.expression, {}) } };
      } catch (error) {
        return { exceptionDetails: { exception: { description:"""
    r""" String(error) } } };
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
      // vm-load-exempt: runs the function the test injected, not a file
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
  fetch: bridgeFetch,
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
""") + import_scripts_stub('backgroundContext') + (
    r"""

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
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), backgroundContext,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', backgroundContext);
  vm.runInContext(
    fs.readFileSync(contentPath, 'utf8'), relayContext,
    { filename: contentPath });
  vm.runInContext(
    fs.readFileSync(pagePath, 'utf8'), relayContext,
    { filename: pagePath });

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
      const before = resultPosts.length;
      await vm.runInContext('dispatchCommand(command)', backgroundContext);
      await waitFor(
        () => resultPosts.length === before + 1,
        'injection result for ' + shape);
      const posted = resultPosts[before];
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
    await waitFor(() => resultPosts.length === 1, 'poisoned eval result');
    return {
      result: resultPosts[0].result,
      world: resultPosts[0].world,
      deliveryId: resultPosts[0]._did || null,
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
    await waitFor(() => resultPosts.length === 1, 'mid-flight eval result');
    return {
      result: resultPosts[0].result === undefined
        ? null : resultPosts[0].result,
      error: resultPosts[0].error,
      world: resultPosts[0].world || null,
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
    await waitFor(() => resultPosts.length === 1, 'forged page result');
    return {
      result: resultPosts[0].result,
      world: resultPosts[0].world,
      deliveryId: resultPosts[0]._did || null,
    };
  }

  if (mode === 'gm-abort') {
    relayContext.abortProbe = {};
    // vm-load-exempt: issues a literal xmlhttpRequest probe, not a file
    vm.runInContext(
      'abortProbe.handle = window.GM.xmlhttpRequest({'
      + ' url: "https://example.com/slow",'
      + ' onload: function() { abortProbe.load = true; },'
      + ' onerror: function() { abortProbe.error = true; },'
      + ' ontimeout: function() { abortProbe.timeout = true; },'
      + ' onabort: function() { abortProbe.abort = true; },'
      + '})', relayContext);
    // The gate's record for the relayed request carries its own AbortSignal,
    // so the abort test reads the signal the request really carried rather
    // than a bespoke array the wrapper kept beside the gate.
    const slowRecord = () => nonStreamFetches.find(
      (r) => r.url.includes('/slow'));
    await waitFor(() => Boolean(slowRecord()), 'the relayed"""
    r""" fetch to start');
    const inFlight = vm.runInContext('_fetchControllers.size',"""
    r""" backgroundContext);
    vm.runInContext('abortProbe.handle.abort()', relayContext);
    vm.runInContext('abortProbe.handle.abort()', relayContext);
    await waitFor(() => slowRecord().signal.aborted,
      'the fetch to be cancelled');
    await delay();
    await delay();
    return {
      inFlight,
      aborted: slowRecord().signal.aborted,
      onabort: Boolean(relayContext.abortProbe.abort),
      onload: Boolean(relayContext.abortProbe.load),
      onerror: Boolean(relayContext.abortProbe.error),
      ontimeout: Boolean(relayContext.abortProbe.timeout),
      abortMessages: windowMessages.filter(
        (message) => message.handler === 'abortRequest').length,
      controllers: vm.runInContext('_fetchControllers.size',"""
    r""" backgroundContext),
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
      results: resultPosts.map((item) => ({
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
      () => resultPosts.some((item) => item.result === owner),
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
    results: resultPosts.map((item) => ({
      result: item.result,
      deliveryId: item._did || null,
    })),
  };
}

run().then((result) => {
  // Carry the gate's own record beside the mode's specific answer, so the
  // Python side compares the whole recorded list against the declared plan.
  result.gate = {
    records: nonStreamFetches,
    refused: refusedFetches,
    badOrigins,
    streamAnswered: streamFetches.map((f) => f.answered),
    contractFaults: gateContractFaults,
  };
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""")


def _observe(plan, *args):
    """Drive one relay mode under a plan and read back its answer.

    Every request the mode makes is accounted against `plan`, and the whole
    recorded list is compared through the shared gate's check, so an
    undeclared, missing or extra request is loud even when the worker
    swallowed it. No wall bound: the child bounds itself by attempt counts
    (see waitFor in the harness above), so a genuine deadlock surfaces as a
    hung job under the runner's own suite ceiling.
    """
    outcome = run_inline_gate(
        require_node(), _EVAL_RELAY_OVERLAP_HARNESS, list(args),
        cwd=ROOT, plan=plan)
    gate = outcome.pop('gate')
    assert_gate_clean(
        contract_faults=gate['contractFaults'],
        records=gate['records'], refused=gate['refused'],
        bad_origins=gate['badOrigins'],
        stream_answered=gate['streamAnswered'],
        planned=list(plan['planned']), planned_stream=list(plan['statuses']))
    return outcome


def _one_result(count=1):
    """The recording every single-result relay mode makes.

    Boot opens the stream (503) and syncs the tab list, the content
    script's boot registers the tab, and each dispatched eval posts one
    result — all recorded, all declared here, none special-cased.
    """
    return {'planned': [SYNC, REGISTER] + [RESULT] * count, 'statuses': [503]}


def run_eval_relay_overlap(order):
    return _observe(_one_result(2), *_BRIDGE, json.dumps(order))


def run_eval_same_tab_preemption():
    return _observe(_one_result(1), *_BRIDGE, '[]', 'preemption')


def run_gm_abort():
    plan = {
        'planned': [SYNC, REGISTER, SLOW],
        'statuses': [503],
        'relayHosts': [RELAY_HOST],
        'answers': {SLOW: {'hang': True}},
    }
    return _observe(plan, *_BRIDGE, '[]', 'gm-abort')


def run_eval_relay_marker(hostname):
    return _observe(_one_result(1), *_BRIDGE, '[]', 'marker', hostname)


def run_eval_after_cdp_fails_mid_flight():
    return _observe(_one_result(1), *_BRIDGE, '[]', 'midflight', '',
                    'midflight')


def run_eval_with_poisoned_page_globals(cdp_available):
    return _observe(_one_result(1), *_BRIDGE, '[]', 'poisoned', '',
                    '1' if cdp_available else '0')


def run_main_world_injection_shapes():
    return _observe(_one_result(8), *_BRIDGE, '[]', 'injection-shapes')
