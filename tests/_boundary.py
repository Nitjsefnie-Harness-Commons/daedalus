"""The extension-boundary scenarios, and how one is run.

Not a suite itself — run_tests.py only loads `test_*.py`.

Each scenario drives the shipped background script through one boundary —
relay capacity, delivery-id dedup across a restart, a rejected upload, a
partitioned cookie — inside the fake browser from _boundary_env, and returns
what the worker did as JSON.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boundary_env import ENVIRONMENT  # noqa: E402
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402

SCENARIOS = r"""
async function runCapabilityRoutes() {
  const routes = JSON.parse(commandText);
  const sameDescriptor = (left, right) => {
    if (!left || !right) return left === right;
    return left.configurable === right.configurable
      && left.enumerable === right.enumerable
      && left.writable === right.writable
      && left.value === right.value
      && left.get === right.get
      && left.set === right.set;
  };
  const publishedSymbols = new Set();
  for (const route of routes) {
    publishedSymbols.add(route.symbol);
    for (const symbol of route.publishedSymbols || []) {
      publishedSymbols.add(symbol);
    }
  }
  const probedOriginals = new Map();
  const originalDescriptors = new Map();
  const handlerStates = new Map();
  for (const publishedSymbol of publishedSymbols) {
    if (!/^[A-Za-z_$][\w$]*$/.test(publishedSymbol)) {
      throw new Error('invalid published symbol: ' + publishedSymbol);
    }
    const available = vm.runInContext(
      'typeof ' + publishedSymbol + ' === "function"', context);
    if (available) {
      probedOriginals.set(
        publishedSymbol, vm.runInContext(publishedSymbol, context));
      const descriptor = Object.getOwnPropertyDescriptor(
        context, publishedSymbol);
      if (descriptor && descriptor.configurable && descriptor.writable) {
        const state = { value: descriptor.value, writes: 0 };
        Object.defineProperty(context, publishedSymbol, {
          configurable: descriptor.configurable,
          enumerable: descriptor.enumerable,
          get() { return state.value; },
          set(value) {
            state.writes++;
            state.value = value;
          },
        });
        originalDescriptors.set(publishedSymbol, descriptor);
        handlerStates.set(publishedSymbol, state);
      }
    }
  }
  const verificationOriginals = new Map(probedOriginals);
  const expectedHandlers = new Map(probedOriginals);
  const observations = [];
  try {
    for (const route of routes) {
      const publishedSymbol = route.symbol;
      if (!probedOriginals.has(publishedSymbol)) {
        observations.push({
          symbol: publishedSymbol, available: false, replaceable: false,
        });
        continue;
      }
      const calls = [];
      const sentinelAnswer = Object.freeze({ sentinel: publishedSymbol });
      context.capabilitySentinel = (cmd) => {
        calls.push(cmd);
        return sentinelAnswer;
      };
      try {
        vm.runInContext(
          publishedSymbol + ' = capabilitySentinel', context);
        expectedHandlers.set(
          publishedSymbol, vm.runInContext(publishedSymbol, context));
      } catch (error) {
        delete context.capabilitySentinel;
        observations.push({
          symbol: publishedSymbol, available: true, replaceable: false,
          assignmentError: error.message,
        });
        continue;
      }
      for (const state of handlerStates.values()) state.writes = 0;
      const expectedDescriptors = new Map();
      for (const symbol of probedOriginals.keys()) {
        expectedDescriptors.set(
          symbol, Object.getOwnPropertyDescriptor(context, symbol));
      }
      context.capabilityCommand = route.command;
      let answer;
      let dispatchError;
      try {
        answer = await vm.runInContext(
          'dispatchCommand(capabilityCommand)', context);
      } catch (error) {
        dispatchError = error;
      } finally {
        delete context.capabilityCommand;
        delete context.capabilitySentinel;
      }
      const mutatedSymbols = [];
      for (const [symbol, expectedDescriptor] of expectedDescriptors) {
        const descriptor = Object.getOwnPropertyDescriptor(context, symbol);
        const state = handlerStates.get(symbol);
        const wrote = state && state.writes;
        const sameIdentity = descriptor
          && vm.runInContext(symbol, context) === expectedHandlers.get(symbol);
        if (symbol !== publishedSymbol
            && (wrote || !sameIdentity
                || !sameDescriptor(descriptor, expectedDescriptor))) {
          mutatedSymbols.push(symbol);
        }
      }
      for (const [symbol, descriptor] of expectedDescriptors) {
        Object.defineProperty(context, symbol, descriptor);
        const state = handlerStates.get(symbol);
        if (state) state.value = expectedHandlers.get(symbol);
      }
      if (dispatchError) throw dispatchError;
      const observation = {
        symbol: publishedSymbol,
        available: true,
        replaceable: true,
        callCount: calls.length,
        calledType: calls.length ? calls[0].type : null,
        answered: answer === sentinelAnswer,
      };
      if (mutatedSymbols.length) {
        observation.mutatedSymbols = mutatedSymbols;
      }
      observations.push(observation);
    }
  } finally {
    for (const [publishedSymbol, original] of probedOriginals) {
      if (originalDescriptors.has(publishedSymbol)) {
        Object.defineProperty(
          context, publishedSymbol,
          originalDescriptors.get(publishedSymbol));
      } else {
        context.capabilityOriginal = original;
        vm.runInContext(
          publishedSymbol + ' = capabilityOriginal', context);
      }
    }
    delete context.capabilityOriginal;
    delete context.capabilityCommand;
    delete context.capabilitySentinel;
  }
  if (routes.some((route) => route.verifyBatchRestoration)) {
    const restored = {};
    for (const [symbol, original] of verificationOriginals) {
      restored[symbol] = vm.runInContext(symbol, context) === original;
    }
    return { observations, restored };
  }
  return observations;
}

async function runCapacity() {
  context.prefill = Array.from({ length: 1000 }, (_unused, index) => ({
    id: 'existing-' + index,
    _did: 'did-existing-' + index,
  }));
  const relayIds = vm.runInContext(
    "prefill.map((command) => _registerEvalRelay("
      + "_executionContext(command), '7'))",
    context);
  context.nextCommand = {
    id: 'new-at-capacity',
    type: 'eval',
    code: '42',
    chromeTab: 7,
    _did: 'did-new-at-capacity',
  };
  await vm.runInContext('dispatchCommand(nextCommand)', context);
  context.firstRelay = relayIds[0];
  const first = vm.runInContext(
    "_takeEvalRelay(firstRelay, '7')", context);
  return {
    firstId: first && first.id,
    sentMessages: sentMessages.length,
    results: requests.filter((item) => item.kind === 'result'),
  };
}

async function runExpiry() {
  context.slowCommand = {
    id: 'slow-eval',
    _did: 'did-slow-eval',
  };
  const relayId = vm.runInContext(
    "_registerEvalRelay(_executionContext(slowCommand), '7')", context);
  const expiry = timers.find((timer) => timer.delay === 300000);
  if (!expiry) throw new Error('missing 300000 ms relay expiry');
  expiry.callback();
  expiry.callback();
  await delay();
  context.expiredRelay = relayId;
  return {
    stillPending: Boolean(vm.runInContext(
      "_takeEvalRelay(expiredRelay, '7')", context)),
    results: requests.filter((item) => item.kind === 'result'),
  };
}

async function runRouteSnapshot() {
  context.screenshotCommand = {
    id: 'route-snapshot',
    type: 'screenshot',
    _did: 'did-route-snapshot',
  };
  const execution = vm.runInContext(
    'dispatchCommand(screenshotCommand)', context);
  context.blockCommand = {
    id: 'block-route-snapshot',
    type: 'block-requests',
    pattern: '*://media.example.com/*',
    _did: 'did-block-route-snapshot',
  };
  const blockExecution = vm.runInContext(
    'dispatchCommand(blockCommand)', context);
  await waitFor(
    () => Boolean(captureResolver) && Boolean(tabQueryResolver),
    'side operations to start');
  for (const listener of changeListeners) {
    listener({
      'daedalus-token': { newValue: 'replacement-token' },
      'daedalus-server': {
        newValue: 'https://replacement.example.com',
      },
    }, 'local');
  }
  captureResolver('data:image/png;base64,AA==');
  await execution;
  tabQueryResolver([{ id: 7 }]);
  await blockExecution;
  return {
    requests,
    excludedRequestDomains: rules[0]
      ? rules[0].condition.excludedRequestDomains
      : null,
  };
}

async function runScreenshotTarget() {
  context.screenshotCommand = {
    id: 'targeted',
    type: 'screenshot',
    tabId: 8,
    _did: 'did-targeted',
  };
  await vm.runInContext('dispatchCommand(screenshotCommand)', context);
  return {
    captured: uploadedData.length
      ? Buffer.from(uploadedData[0], 'base64').toString() : null,
    activeAfter: (windowTabs.find((tab) => tab.active) || {}).id,
    activations,
    posted: resultPayloads.map((item) => ({
      tabUrl: item.result && item.result.tabUrl, error: item.error,
    })),
  };
}

async function runScreenshotReject() {
  context.screenshotCommand = {
    id: 'bad/id',
    type: 'screenshot',
    _did: 'did-bad-id',
  };
  await vm.runInContext('dispatchCommand(screenshotCommand)', context);
  return {
    uploads: requests.filter((item) => item.kind === 'upload').length,
    posted: resultPayloads.map((item) => ({
      result: item.result === undefined ? '<absent>' : item.result,
      error: item.error,
    })),
  };
}

async function runNetCapture() {
  const outcomes = [];
  for (const step of ['attach-fails', 'enable-fails', 'succeeds']) {
    context.captureCommand = {
      id: 'net-' + step,
      type: 'net-capture',
      tabId: 7,
      _did: 'did-net-' + step,
    };
    await vm.runInContext('dispatchCommand(captureCommand)', context);
    const posted = resultPayloads[resultPayloads.length - 1];
    outcomes.push({ step, result: posted.result, error: posted.error });
  }
  // Chrome detaches us (DevTools opened, target crashed): the capture is over
  // whether or not anything told the worker to stop it.
  for (const listener of detachListeners) listener({ tabId: 7 });
  context.captureCommand = {
    id: 'net-after-detach',
    type: 'net-capture',
    tabId: 7,
    _did: 'did-net-after-detach',
  };
  await vm.runInContext('dispatchCommand(captureCommand)', context);
  const posted = resultPayloads[resultPayloads.length - 1];
  outcomes.push({ step: 'after-detach', result: posted.result, error: posted.error });
  return { outcomes, attachCalls, detachCalls };
}

async function runHotfixRace() {
  context.storeCommands = ['fix-a', 'fix-b'].map((fixId) => ({
    id: 'store-' + fixId,
    type: 'store-hotfix',
    fixId,
    code: 'console.log("' + fixId + '")',
    _did: 'did-store-' + fixId,
  }));
  await vm.runInContext(
    'Promise.all([dispatchCommand(storeCommands[0]),'
    + ' dispatchCommand(storeCommands[1])])', context);
  const stored = storageStore['daedalus-hotfixes'] || { fixes: [] };
  return {
    posted: resultPayloads.map((item) => ({
      result: item.result, error: item.error,
    })),
    storedIds: stored.fixes.map((fix) => fix.id).sort(),
  };
}

async function runBlockRuleRestart() {
  context.blockCommand = {
    id: 'block-first',
    type: 'block-requests',
    pattern: '*://a.example.com/*',
    tabId: 7,
    _did: 'did-block-first',
  };
  await vm.runInContext('dispatchCommand(blockCommand)', context);

  // A restarted worker re-reads the shipped script with a zeroed counter while
  // the session rules it installed earlier are still present.
  const restarted = makeContext();
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), restarted);
  await vm.runInContext('loadConfig()', restarted);
  restarted.blockCommand = {
    id: 'block-after-restart',
    type: 'block-requests',
    pattern: '*://b.example.com/*',
    tabId: 7,
    _did: 'did-block-after-restart',
  };
  await vm.runInContext('dispatchCommand(blockCommand)', restarted);

  // Two adds in flight at once must not settle on one id either.
  restarted.concurrentCommands = ['c', 'd'].map((name) => ({
    id: 'block-' + name,
    type: 'block-requests',
    pattern: '*://' + name + '.example.com/*',
    tabId: 7,
    _did: 'did-block-' + name,
  }));
  await vm.runInContext(
    'Promise.all([dispatchCommand(concurrentCommands[0]),'
    + ' dispatchCommand(concurrentCommands[1])])', restarted);

  return {
    posted: resultPayloads.map((item) => ({
      ruleId: item.result && item.result.ruleId, error: item.error,
    })),
    installedIds: rules.map((rule) => rule.id),
  };
}

async function runUnblockZero() {
  // Three rules already installed, as an operator would have.
  rules.push({ id: 9001 }, { id: 9002 }, { id: 9003 });
  context.unblockCommand = {
    id: 'unblock-zero',
    type: 'unblock-requests',
    ruleId: 0,
    _did: 'did-unblock-zero',
  };
  await vm.runInContext('dispatchCommand(unblockCommand)', context);
  return {
    installedIds: rules.map((rule) => rule.id),
    posted: resultPayloads.map((item) => ({
      removed: item.result && item.result.removed, error: item.error,
    })),
  };
}

function settle() {
  // parseSSEChunk dispatches without awaiting, so let the real event loop
  // drain before looking at what the handler did.
  return new Promise((resolve) => setImmediate(resolve));
}

async function runDedupAcrossRestart() {
  const frame = 'event: command\ndata: ' + JSON.stringify({
    id: 'dedup-open', type: 'open-tab', url: 'about:blank',
    _did: 'did-dedup-1',
  }) + '\n\n';
  const deliver = 'parseSSEChunk(' + JSON.stringify(frame) + ')';

  vm.runInContext(deliver, context);
  for (let turn = 0; turn < 6; turn++) await settle();

  // A fresh worker instance over the SAME extension storage, which is what an
  // MV3 restart is.
  const restarted = makeContext();
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), restarted);
  await vm.runInContext('loadConfig()', restarted);
  vm.runInContext(deliver, restarted);
  for (let turn = 0; turn < 6; turn++) await settle();

  return {
    created: createdTabs.length,
    posted: resultPayloads.map((item) => item._did || null),
  };
}

async function runClearPartitioned() {
  cookieJar.push(
    { name: 'ordinary', domain: 'example.test', path: '/', secure: false },
    { name: 'chips', domain: 'example.test', path: '/', secure: false,
      partitionKey: { topLevelSite: 'http://example.test' } });
  context.clearCommand = {
    id: 'clear-partitioned',
    type: 'clear-cookies',
    url: 'http://example.test/',
    _did: 'did-clear-partitioned',
  };
  await vm.runInContext('dispatchCommand(clearCommand)', context);
  return {
    remaining: cookieJar.map((cookie) => cookie.name),
    posted: resultPayloads.map((item) => ({
      result: item.result, error: item.error,
    })),
    removeCalls: removeCalls.map((details) => ({
      name: details.name, partitionKey: details.partitionKey || null,
    })),
  };
}

function relayFetch(request) {
  return new Promise((resolve) => {
    const message = Object.assign({
      type: 'fetch',
      fetchId: 'bounded-' + (++relaySequence),
      method: 'GET',
      responseType: 'text',
    }, request);
    for (const listener of messageListeners) listener(message, {}, resolve);
  });
}

async function runFetchBound() {
  const steps = [];
  const cases = [
    // Exactly the 8 MiB default: a ceiling, not a threshold the last
    // permitted byte trips.
    { name: 'at the default', chunks: 8 },
    { name: 'over the default', chunks: 9 },
    // The opt-in raises the default for a caller that asks for more.
    { name: 'raised by opt-in', chunks: 12, maxResponseBytes: 16 * 1024 * 1024 },
    { name: 'binary under the default', chunks: 1, responseType: 'arraybuffer' },
  ];
  for (const item of cases) {
    const request = Object.assign({}, item);
    delete request.name;
    delete request.chunks;
    request.url = 'https://big.example.com/blob?chunks=' + item.chunks;
    const answer = await relayFetch(request);
    steps.push({
      name: item.name,
      error: answer.error || null,
      tooLarge: answer.tooLarge === true,
      dataLength: typeof answer.data === 'string' ? answer.data.length : null,
      chunksRead: streamPlan.handed,
      chunksOffered: streamPlan.chunkCount,
      cancelled: streamPlan.cancelled,
    });
  }
  // Showing the clamp by streaming would mean allocating past the ceiling,
  // so it is asked directly instead.
  const limits = {};
  for (const [label, asked] of [
      ['omitted', 'undefined'], ['zero', '0'], ['negative', '-1'],
      ['fractional', '1.5'], ['text', '"8000000"'],
      ['below the default', '1024'],
      ['above the ceiling', String(1024 * 1024 * 1024 * 1024)]]) {
    limits[label] = vm.runInContext('gmResponseLimit(' + asked + ')', context);
  }
  const timings = JSON.parse(vm.runInContext(
    'JSON.stringify(_fetchTimings.map((t) =>'
    + ' ({ bodySize: t.bodySize === undefined ? null : t.bodySize,'
    + ' error: t.error || null })))', context));
  return { steps, limits, timings };
}

async function run() {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context);
  if (scenario === 'worker-sources') return workerSourcePaths.get(context);
  await vm.runInContext('loadConfig()', context);
  if (scenario === 'capability-routes') return runCapabilityRoutes();
  if (scenario === 'capacity') return runCapacity();
  if (scenario === 'expiry') return runExpiry();
  if (scenario === 'route') return runRouteSnapshot();
  if (scenario === 'screenshot-reject') return runScreenshotReject();
  if (scenario === 'screenshot-target') return runScreenshotTarget();
  if (scenario === 'net-capture') return runNetCapture();
  if (scenario === 'hotfix-race') return runHotfixRace();
  if (scenario === 'block-rule-restart') return runBlockRuleRestart();
  if (scenario === 'unblock-zero') return runUnblockZero();
  if (scenario === 'clear-partitioned') return runClearPartitioned();
  if (scenario === 'dedup-restart') return runDedupAcrossRestart();
  if (scenario === 'fetch-bound') return runFetchBound();
  throw new Error('unknown scenario: ' + scenario);
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""

HARNESS = ENVIRONMENT + SCENARIOS


def run_extension_result_boundary(scenario):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension result path'
    result = subprocess.run(
        [node, '-e', HARNESS,
         str(EXTENSION_ROOT / 'background.js'), scenario],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def run_extension_capability_routes(routes, background_path=None):
    """Probe a module's published command routes in one worker process."""
    node = shutil.which('node')
    assert node, 'node is required to execute the extension command route'
    if background_path is None:
        background_path = EXTENSION_ROOT / 'background.js'
    result = subprocess.run(
        [node, '-e', HARNESS,
         str(background_path), 'capability-routes',
         json.dumps(routes)],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def observe_extension_worker_paths():
    """Return the honest worker's loader trace of requested module paths.

    This consumes a Node vm trace, not a security boundary. Host functions
    expose host-realm intrinsics to deliberately hostile worker source, which
    can therefore forge the recorded array. The inventory guard uses this to
    catch honest split drift only.
    """
    node = shutil.which('node')
    assert node, 'node is required to observe extension worker modules'
    result = subprocess.run(
        [node, '-e', HARNESS,
         str(EXTENSION_ROOT / 'background.js'), 'worker-sources'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return tuple(Path(item) for item in json.loads(result.stdout))
