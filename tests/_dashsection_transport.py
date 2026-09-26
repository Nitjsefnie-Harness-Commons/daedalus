"""The transport half of the section harness.

`_dashsection.SHELL` is `_dashnode.DOM` and the elements half followed
by this string, and the two share one module scope in the child, so
this half reads `PARKED`, `SELECTORS`, `LISTENERS` and `hostImmediate`
from `_dashsection._ELEMENTS` and writes into them. The split is the
size ceiling's own remedy -- a file over the 700-line test ceiling is
relocated, never given an entry -- and it puts each half's code with
the half that owns it.

Every response member and every plan a scenario reads is answered by
name; a request the scenario never planned is recorded and then
refused.
"""
# The strict transport, the driver and the report. Every response member
# and every plan a scenario reads is answered by name; a request the
# scenario never planned is recorded and then refused.
TRANSPORT = r"""
const ROUTES = new Map();
const REQUESTS = [];
const REFUSALS = [];
const LEDGER = { command: null, generation: 0, wrong: 0, stale: 0 };

function queryValue(target, name) {
  const at = target.indexOf('?');
  if (at < 0) return null;
  for (const pair of target.slice(at + 1).split('&')) {
    const eq = pair.indexOf('=');
    const key = eq < 0 ? pair : pair.slice(0, eq);
    if (key === name) return eq < 0 ? '' : pair.slice(eq + 1);
  }
  return null;
}

// `api.js` adds `consume` and `expected` to the poll target to consume
// what it peeked, so the plan a scenario writes for the poll answers
// both legs and a generation it could not have known in advance.
function pollTarget(target) {
  if (queryValue(target, 'consume') !== '1') return target;
  const at = target.indexOf('?');
  const kept = [];
  for (const pair of target.slice(at + 1).split('&')) {
    const eq = pair.indexOf('=');
    const key = pair.slice(0, eq < 0 ? pair.length : eq);
    if (key !== 'consume' && key !== 'expected') kept.push(pair);
  }
  return target.slice(0, at) + '?' + kept.join('&');
}

// The plan is keyed on the whole target, so this reads the planned key
// only to choose the shape of its answer; a scenario's two plans for
// `/command` and `/result` are still two exact targets.
function routeRole(key) {
  const at = key.indexOf('?');
  const path = at < 0 ? key : key.slice(0, at);
  if (path.endsWith('/command')) return 'command';
  if (path.endsWith('/result')) return 'result';
  return 'plain';
}

function readAuthorization(headers) {
  if (headers === undefined || headers === null) return null;
  if (typeof headers !== 'object' || typeof headers.get === 'function'
      || typeof headers.forEach === 'function') {
    throw unmodelled('a Headers bag rather than a plain header object',
                     Object.prototype.toString.call(headers));
  }
  const auth = headers.Authorization;
  return auth === undefined ? null : String(auth);
}

function parseBody(init, target) {
  if (init.body === undefined || init.body === null) return null;
  if (typeof init.body !== 'string') {
    throw unmodelled('a non-string request body for', target);
  }
  try {
    return JSON.parse(init.body);
  } catch (failure) {
    throw new Error('dashboard section harness cannot read the request body '
      + 'for ' + target + ': ' + failure.message);
  }
}

function jsonAnswer(data, status) {
  if (status === undefined) return jsonResponse(data);
  return { ok: status >= 200 && status < 300, status,
           headers: { get: () => 'application/json' },
           json: async () => data, text: async () => JSON.stringify(data) };
}

// The delivery id is the one number the poll legs have to agree on, so a
// command answer that carries none is answered without one and lets the
// shipped `runCommand` throw its own "no delivery id".
// A request the scenario never planned is recorded and then refused, and
// the throw is the refusal: no section wraps its fetch in a catch that
// turns one into a stream error, so a thrown refusal is readable and a
// swallowed one is not. `byType` refuses through this same door rather
// than a second one, so there is one strictness here and one control on
// it.
function refuse(target) {
  REFUSALS.push({ n: REQUESTS.length, target: String(target) });
  throw new Error('unexpected request ' + String(target));
}

function commandAnswer(spec, body) {
  if (spec.status !== undefined && spec.status !== 200) {
    const failure = spec.json === undefined
      ? { error: spec.error === undefined ? 'command refused' : spec.error }
      : spec.json;
    return jsonAnswer(failure, spec.status);
  }
  LEDGER.command = { id: body.id, did: spec.did === undefined
    ? null : String(spec.did), type: body.type };
  LEDGER.generation += 1;
  openPump();
  return jsonAnswer(spec.did === undefined ? { ok: true }
    : { did: String(spec.did) });
}

// The anchored envelope for the first N polls a plan says to skip, or
// null once they are spent. One counter per member, because a plan uses
// one at a time and a shared counter would let the second one's count
// answer for the first.
function leading(spec, key, shape, anchored) {
  if (spec[key] === undefined) return null;
  LEDGER[key] += 1;
  return LEDGER[key] <= spec[key]
    ? Object.assign({}, anchored, shape) : null;
}

// Anchored on the command the transport actually received, so a default
// envelope is a result the section can match. An `envelope` in the plan
// is answered verbatim, which is how a scenario plants a wrong one: a
// fake that repaired it would not be able to tell a wrong result from a
// right one, and the section under test would be the only thing that
// could.
function resultAnswer(target, spec) {
  const command = LEDGER.command;
  const anchored = command === null ? null : {
    id: command.id,
    deliveryId: command.did,
    resultGeneration: LEDGER.generation,
  };
  if (queryValue(target, 'consume') === '1') {
    PUMP.open = false;
    const expected = queryValue(target, 'expected');
    const answered = { consumed: expected !== null
      && String(LEDGER.generation) === expected,
      resultGeneration: LEDGER.generation };
    if (anchored) Object.assign(answered, anchored);
    return jsonAnswer(Object.assign(answered, spec.envelope || {}));
  }
  if (spec.envelope) return jsonAnswer(spec.envelope);
  if (!anchored) {
    throw unmodelled('a result poll before any command for', target);
  }
  // `byType` is how one `/result` plan answers a different result for a
  // different command TYPE: the type is read off the command the
  // transport received, and the patch is applied to the envelope it
  // anchored, so the delivery id and generation the loop matched on stay
  // the real ones. A type the scenario did not name is refused like an
  // unplanned target -- a double that answered it would be answering
  // something the scenario never declared.
  if (spec.byType !== undefined) {
    if (!(command.type in spec.byType)) refuse(target);
    return jsonAnswer(Object.assign({}, anchored, spec.byType[command.type]));
  }
  // A plan may declare how many leading polls carry an envelope the
  // shipped loop has to skip, which is what the two shared-slot states
  // look like before the result is the caller's: `wrong` is a result left
  // by another command, `stale` is one that has not been stamped with a
  // generation yet. Each is the anchored envelope with ONE member
  // changed, so the loop reaches the branch that member is read in --
  // `api.js:150` for `wrong`, `api.js:152` for `stale` -- and the count
  // says how many times it looked. The case that uses these reads the
  // envelopes back, because the count cannot say which member differed.
  const skipped = leading(spec, 'wrong', { id: 'a command this is not' },
                          anchored)
    || leading(spec, 'stale', { resultGeneration: 0 }, anchored);
  if (skipped) return jsonAnswer(skipped);
  if (spec.pending
      || (spec.result === undefined && spec.error === undefined)) {
    return jsonAnswer({ pending: true });
  }
  return jsonAnswer(Object.assign(anchored, {
    result: spec.result,
    error: spec.error === undefined ? null : spec.error,
  }));
}

globalThis.fetch = async (target, init) => {
  const options = init === undefined ? {} : init;
  const whole = String(target);
  const authorization = readAuthorization(options.headers);
  const body = parseBody(options, whole);
  REQUESTS.push({
    n: REQUESTS.length + 1,
    target: whole,
    method: options.method ? String(options.method) : 'GET',
    authorization,
    body,
  });
  const key = pollTarget(whole);
  const spec = ROUTES.get(key);
  if (!spec) refuse(whole);
  const role = routeRole(key);
  if (role === 'command') return commandAnswer(spec, body);
  if (role === 'result') return resultAnswer(whole, spec);
  return jsonAnswer(spec.json, spec.status);
};

const drive = {
  route(target, spec) {
    const key = String(target);
    if (ROUTES.has(key)) throw new Error('route already planned: ' + key);
    ROUTES.set(key, spec || {});
    return key;
  },
  selector(selector, element) {
    const key = String(selector);
    if (SELECTORS.has(key)) {
      throw new Error('selector already registered: ' + key);
    }
    SELECTORS.set(key, element);
    return key;
  },
  planned() { return Array.from(ROUTES.keys()); },
  live() { return PARKED.filter(Boolean).map((slot) => slot.id); },
  fire(id) {
    const slot = PARKED[id - 1];
    if (!slot) throw new Error('no parked timer with id ' + id);
    slot.callback(...slot.extra);
    if (slot.kind === 'timeout') PARKED[id - 1] = null;
    return id;
  },
};

const pause = (ms) => new Promise((resolve) => realSetTimeout(resolve, ms));

// `settle()` is the base's own `setImmediate`, and a command in flight is
// a parked poll sleep no scenario can reach from outside its own `await`.
// So an immediate keeps turning the pump while a command window is open
// and runs its callback only once there is nothing left to spend. One
// `settle()` is then what a scenario needs to let a command finish: the
// sleep is spent, the poll and consume legs resolve in the microtask
// cascade that follows it, and that cascade is what renders.
globalThis.setImmediate = (callback) => hostImmediate(function turn() {
  if (PUMP.open && spendOne()) { hostImmediate(turn); return; }
  callback();
});

// The two import checkpoints belong to the shell because `load` is the
// only way a scenario reaches a module, and a scenario that emitted them
// itself could emit them out of order or not at all.
function load(name) {
  const at = MODULES.indexOf(name);
  if (at < 0) throw new Error('no module argument for ' + name);
  phase('dashboard module import started');
  return import(pathToFileURL(process.argv[at + 1]).href)
    .then((loaded) => {
      phase('dashboard module imported');
      return loaded;
    });
}

function report(extra) {
  phase('dashboard call settled');
  phase('dashboard harness finished');
  process.stdout.write(JSON.stringify(Object.assign({
    requests: REQUESTS,
    unplanned: REFUSALS,
    timers: drive.live(),
    clicks: CLICKS,
    errors: ERRORS,
    storage: Object.fromEntries(STORAGE),
  }, extra || {})));
}
"""
