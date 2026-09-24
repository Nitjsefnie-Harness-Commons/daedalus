"""JavaScript scenarios for the extension-boundary harness."""

SCENARIOS = (
    r"""
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
    // vm-load-exempt: probes a published symbol's type, not a file
    const available = vm.runInContext(
      'typeof ' + publishedSymbol + ' === "function"', context);
    if (available) {
      probedOriginals.set(
        // vm-load-exempt: reads a published handler by name, not a file
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
        // vm-load-exempt: writes a sentinel through a published name
        vm.runInContext(
          publishedSymbol + ' = capabilitySentinel', context);
        expectedHandlers.set(
          // vm-load-exempt: reads the handler back by name, not a file
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
          // vm-load-exempt: reads a handler by name, not a file
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
        // vm-load-exempt: restores a handler through its published name
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
      // vm-load-exempt: reads a restored handler by name, not a file
      restored[symbol] = vm.runInContext(symbol, context) === original;
    }
    return { observations, restored };
  }
  return observations;
}

async function runUnknownCommand() {
  context.unknownCommand = JSON.parse(commandText);
  await vm.runInContext('dispatchCommand(unknownCommand)', context);
  return { posted: resultPayloads.map(({ result, error }) => ({
    result: result === undefined ? '<absent>' : result, error })) };
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
    results: bridgeRequests().filter((item) => item.kind === 'result'),
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
    results: bridgeRequests().filter((item) => item.kind === 'result'),
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
    requests: bridgeRequests(),
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
  const uploads = uploadBodies();
  return {
    captured: uploads.length
      ? Buffer.from(uploads[0].data, 'base64').toString() : null,
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
    uploads: bridgeRequests().filter((item) => item.kind === 'upload').length,
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
  outcomes.push({ step: 'after-detach', result: posted.result,"""
    r""" error: posted.error });
  return { outcomes, attachCalls, detachCalls };
}

async function runNetCaptureOwnership() {
  // One attachment owner per tab: a cdp command and a net capture reuse each
  // other's attachment instead of attaching over it, a transient cdp leaves
  // an attachment it found exactly as it was, and stopping a capture never
  // detaches an attachment a kept cdp session still needs.
  const readState = (tabId) => {
    const keys = JSON.parse(vm.runInContext(
      'JSON.stringify([Object.keys(_cdpSessions),'
      + ' Object.keys(_netCaptures)])', context));
    return {
      cdpSession: keys[0].includes(String(tabId)),
      netCapture: keys[1].includes(String(tabId)),
    };
  };
  const run = async (type, tabId, extra) => {
    context.stepCommand = Object.assign(
      { id: type, type, tabId, _did: type + '-' + tabId }, extra);
    await vm.runInContext('dispatchCommand(stepCommand)', context);
    const posted = resultPayloads[resultPayloads.length - 1];
    return { result: posted.result, error: posted.error,
      calls: { attachCalls, detachCalls },
      attached: debuggerAttached.has(tabId),
      state: readState(tabId) };
  };

  // Tab 7: capture, a transient cdp over it, then stop with no other owner.
  const capture = await run('net-capture', 7);
  const cdpOverCapture = await run('cdp', 7, { method: 'Runtime.enable' });
  const stopCapture = await run('net-capture-stop', 7);

  // Tab 8: a kept cdp session, a transient cdp and a capture that reuse it,
  // then stop the capture — the kept session must survive and keep it.
  const keepSession = await run(
    'cdp', 8, { method: 'Runtime.enable', keep_session: true });
  const transientOverKept = await run('cdp', 8, { method: 'Runtime.enable' });
  const captureOverKept = await run('net-capture', 8);
  const stopOverKept = await run('net-capture-stop', 8);

  // Tab 9: a capture, a keep-session cdp that reuses it, then stop — the
  // record must survive the reuse so the stop leaves it standing. Tab 8 is
  // still attached, so this also proves a different tab may attach.
  const captureTab9 = await run('net-capture', 9);
  const keepSessionOverCapture = await run(
    'cdp', 9, { method: 'Runtime.enable', keep_session: true });
  const stopCaptureOverKept = await run('net-capture-stop', 9);

  return { capture, cdpOverCapture, stopCapture,
    keepSession, transientOverKept, captureOverKept, stopOverKept,
    captureTab9, keepSessionOverCapture, stopCaptureOverKept };
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

// The hotfix record's byte bound. Chrome measures QUOTA_BYTES as the JSON
// stringification of every value plus every key's length, and the record is
// ONE key, so its charge is the record's JSON bytes plus the key's length.
// This scenario's own measure is computed from its OWN construction and never
// calls the production measure, so a boundary placed here is placed
// independently: dropping either term in production moves the admission and
// the test disagrees.
const HOTFIX_TEST_KEY = 'daedalus-hotfixes';
const HOTFIX_TEST_TS = 1700000000000;
const enc = new TextEncoder();

function hotfixRecordBytes(version, fixes) {
  return enc.encode(JSON.stringify({ version, fixes })).length;
}

function hotfixRecordCharge(version, fixes) {
  return hotfixRecordBytes(version, fixes) + enc.encode(HOTFIX_TEST_KEY)
    .length;
}

// A fix entry as the store composes it, with the code run at `length`
// characters, is what `compose` builds; the projected charge below is the
// measure a boundary is placed on.
async function runHotfixQuota() {
  const plan = JSON.parse(commandText);
  const version = plan.version;
  const store = storageStore;
  const posted = [];
  function command(fields) {
    context.hotfixCommand = fields;
    return vm.runInContext('dispatchCommand(hotfixCommand)', context);
  }
  // What the operator receives: the payload the worker POSTed for the
  // command, plus what the record holds afterwards.
  function summary() {
    const payload = resultPayloads[resultPayloads.length - 1];
    const fixes = (store[HOTFIX_TEST_KEY] || { fixes: [] }).fixes;
    return {
      id: payload.id,
      error: payload.error,
      result: payload.result === undefined ? null : payload.result,
      fixes: fixes.map((fix) => ({
        id: fix.id, codeLength: fix.code.length, permanent: fix.permanent,
      })),
      charge: hotfixRecordCharge(version, fixes),
    };
  }
  function compose(fixes, spec) {
    const kept = fixes.filter((fix) => fix.id !== spec.id);
    kept.push({ id: spec.id, code: 'x'.repeat(spec.codeLength),
      ts: HOTFIX_TEST_TS, permanent: spec.permanent === true });
    return kept;
  }
  async function step(spec) {
    // A step with a `seed` starts a fresh store; a step without one
    // continues on whatever the previous step left, so a sequence can show
    // that a refused store released the lock (a clear and a further store
    // then both run).
    if (spec.seed) {
      delete store[HOTFIX_TEST_KEY];
      if (spec.seed.length > 0) {
        store[HOTFIX_TEST_KEY] = {
          version,
          fixes: spec.seed.map((fix) => ({ id: fix.id,
            code: 'x'.repeat(fix.codeLength), ts: HOTFIX_TEST_TS,
            permanent: fix.permanent === true })),
        };
      }
    }
    const seeded = (store[HOTFIX_TEST_KEY] || { fixes: [] }).fixes;
    if (spec.clear) {
      await command({ id: spec.id, type: 'clear-hotfix',
        fixId: spec.id, _did: 'did-clear-' + spec.id });
      posted.push(summary());
      return;
    }
    const incoming = { id: spec.id, codeLength: spec.codeLength,
      permanent: spec.permanent === true };
    const projected = hotfixRecordCharge(
      version, compose(seeded, incoming));
    await command({ id: spec.id, type: 'store-hotfix',
      fixId: spec.id, code: 'x'.repeat(spec.codeLength),
      permanent: spec.permanent, _did: 'did-store-' + spec.id });
    posted.push(summary());
    posted[posted.length - 1].projected = projected;
  }
  for (const spec of plan.steps) await step(spec);
  return posted;
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
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), restarted,
    { filename: backgroundPath });
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

async function runStreamTimers() {
  vm.runInContext('stopStream()', context);
  for (const timer of timers) timer.cleared = true;
  timers.length = 0;

  // On the shared gate: the reconnect phase is answered 503 from
  // plan.statuses and the watchdog phase from a 'hang' answer (a connected
  // 200 whose body never yields). The fetch COUNTS come from the gate's own
  // record, so an invented request here is refused and recorded too.
  const beforeReconnect = streamFetches.length;
  await vm.runInContext('startStream()', context);
  const reconnectDelays = timers
    .filter((timer) => !timer.cleared)
    .map((timer) => timer.delay);
  // The find names the retry and only the retry: the first non-OK answer
  // computes its delay while the failure count is still 0 (the increment
  // follows), and the retried attempt is a later generation, so no earlier
  // timer lingers uncleared at 1000 ms.
  const retry = timers.find(
    (timer) => !timer.cleared && timer.delay === 1000);
  retry.callback();
  await settle();
  const reconnectFetches = streamFetches.length - beforeReconnect;

  vm.runInContext('stopStream()', context);
  for (const timer of timers) timer.cleared = true;
  timers.length = 0;

  context.__streamNow = 1000;
  vm.runInContext('Date.now = () => __streamNow', context);
  const beforeWatchdog = streamFetches.length;
  vm.runInContext('startStream()', context);
  await settle();
  const firstController = vm.runInContext('sseAbort', context);
  const watchdogFetches = streamFetches.length - beforeWatchdog;
  const watchdogDelays = timers
    .filter((timer) => !timer.cleared)
    .map((timer) => timer.delay);
  context.__streamNow = 31001;
  const watchdog = timers.find(
    (timer) => !timer.cleared && timer.delay === 5000);
  watchdog.callback();
  await settle();

  vm.runInContext('stopStream(); keepaliveTimer = null', context);
  for (const timer of timers) timer.cleared = true;
  timers.length = 0;
  let keepaliveCalls = 0;
  chrome.runtime.getPlatformInfo = (callback) => {
    keepaliveCalls++;
    callback();
  };
  vm.runInContext('ensureKeepAlive()', context);
  const keepalive = timers.find(
    (timer) => !timer.cleared && timer.delay === 20000);
  keepalive.callback();

  return {
    reconnectDelays,
    reconnectFetches,
    watchdogDelays,
    watchdogAborted: firstController.signal.aborted,
    watchdogFetches,
    keepaliveDelay: keepalive.delay,
    keepaliveCalls,
  };
}

async function runDedupAcrossRestart() {
  const frame = 'event: command\ndata: ' + JSON.stringify({
    id: 'dedup-open', type: 'open-tab', url: 'about:blank',
    _did: 'did-dedup-1',
  }) + '\n\n';
  const deliver = 'parseSSEChunk(' + JSON.stringify(frame) + ')';

  // vm-load-exempt: delivers a canned SSE frame string, not a file
  vm.runInContext(deliver, context);
  for (let turn = 0; turn < 6; turn++) await settle();

  // A fresh worker instance over the SAME extension storage, which is what an
  // MV3 restart is.
  const restarted = makeContext();
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), restarted,
    { filename: backgroundPath });
  await vm.runInContext('loadConfig()', restarted);
  // vm-load-exempt: delivers the same canned frame after the restart
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
    { name: 'raised by opt-in', chunks: 12, maxResponseBytes: 16"""
    r""" * 1024 * 1024 },
    { name: 'binary under the default', chunks: 1, responseType:"""
    r""" 'arraybuffer' },
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
    // vm-load-exempt: asks gmResponseLimit a case-shaped question
    limits[label] = vm.runInContext('gmResponseLimit(' + asked + ')', context);
  }
  const timings = JSON.parse(vm.runInContext(
    'JSON.stringify(_fetchTimings.map((t) =>'
    + ' ({ bodySize: t.bodySize === undefined ? null : t.bodySize,'
    + ' error: t.error || null })))', context));
  return { steps, limits, timings };
}

async function run() {
  vm.runInContext(
    fs.readFileSync(backgroundPath, 'utf8'), context,
    { filename: backgroundPath });
  if (scenario === 'worker-sources') return workerSourcePaths.get(context);
  await vm.runInContext('loadConfig()', context);
  if (scenario === 'capability-routes') return runCapabilityRoutes();
  if (scenario === 'unknown-command') return runUnknownCommand();
  if (scenario === 'capacity') return runCapacity();
  if (scenario === 'expiry') return runExpiry();
  if (scenario === 'route') return runRouteSnapshot();
  if (scenario === 'screenshot-reject') return runScreenshotReject();
  if (scenario === 'screenshot-target') return runScreenshotTarget();
  if (scenario === 'net-capture') return runNetCapture();
  if (scenario === 'net-capture-ownership') return runNetCaptureOwnership();
  if (scenario === 'hotfix-race') return runHotfixRace();
  if (scenario === 'hotfix-quota') return runHotfixQuota();
  if (scenario === 'block-rule-restart') return runBlockRuleRestart();
  if (scenario === 'unblock-zero') return runUnblockZero();
  if (scenario === 'clear-partitioned') return runClearPartitioned();
  if (scenario === 'dedup-restart') return runDedupAcrossRestart();
  if (scenario === 'fetch-bound') return runFetchBound();
  if (scenario === 'stream-timers') return runStreamTimers();
  throw new Error('unknown scenario: ' + scenario);
}

run().then(async (result) => {
  // Drain the event loop first: background.js's boot request
  // (loadConfig().then -> startStream) is asynchronous, so a scenario that
  // returns without awaiting it (worker-sources) would otherwise leave the
  // boot stream fetch out of the snapshot. The scenario's own answer is
  // already captured; this only completes the gate's record.
  for (let turn = 0; turn < 4; turn++) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  // The scenario's own answer, plus the shared gate's record of every bridge
  // request it made. The Python runner asserts the gate and returns `result`,
  // so a request the worker invents is refused and recorded here even when a
  // scenario reads only a projection of the record.
  process.stdout.write(JSON.stringify({
    result,
    gate: {
      contractFaults: gateContractFaults,
      records: nonStreamFetches,
      refused: refusedFetches,
      badOrigins,
      streamAnswered: streamFetches.map((f) => f.answered),
    },
  }));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
""")
