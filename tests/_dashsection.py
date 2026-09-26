"""The strict harness every `dashboard/sections/*.js` suite shares.

`_dashnode.DOM` mounts one section into a fresh container. Six of the
shipped sections read surface that double does not carry -- a
document-wide selector, sibling links, a real class set, a clock the
scenario drives, and an observable `innerHTML` -- and every one of those
gaps is silent: an unmodelled member answers `undefined`, the section
misbehaves, and the suite pins the misbehaviour as though it were
behaviour. Each gap is filled here and each fill is controlled by a case
in `tests/test_dashsection_harness.py`.

The house rule is `_dashdom`'s and it is the reason the fills refuse
rather than answer: a surface that is not modelled fails by name, because
"the scaffold did not understand" and "nothing matched" are different
situations that a permissive double renders identical.

`SHELL` is `_dashnode.DOM` followed by the two halves below, which share
one module scope in the child exactly as `_dashshell` composes
`_dashdom.DOM` with its transport. The real `dashboard/api.js` is
imported off disk and runs for real: the token header, the
`URLSearchParams` assembly, the `/command` + `/result` two-leg retry and
the per-tab serialisation are the shipped code, never a re-implementation.
The transport decides only what a planned request answers.

The plan is keyed on the WHOLE request target, which is the answer to
issue #1083: a route-shaped match would serve an origin the scenario
never declared, and a relative target is not the absolute one that was
planned. A target the scenario did not plan is recorded on `unplanned`
and then refused with a throw, because no section wraps its fetch in a
catch that turns a throw into a stream error the way `sse.js` does, so a
thrown refusal is readable and a swallowed one is not. The double never
answers an unrecognised request: every branch either matches a plan or
refuses.

The clock parks every timer for the scenario to fire, and a parked
poll sleep would strand every command inside its first attempt:
`api.js` waits 250 ms between result polls and no scenario can drive
that from outside its own `await`. So a command window opens when a
`/command` is answered, and a pump spends one timeout parked inside it
per turn against a virtual clock. One `settle()` is then all a scenario
needs, and a two-click `armedAction` and a toast that has not faded stay
assertable because nothing outside a window runs.
"""
import json
from pathlib import Path, PurePosixPath

from _dashnode import DOM as _DOM
from _dashnode import (DashboardNodeHarness, _BOUNDED_AWAIT,
                       run_dashboard_node)
from _jsread import blank_js_comments
from _repo import ROOT

__all__ = ['SHELL', 'build_harness', 'run_scenario', 'section_path']


# The six gaps `_dashnode.DOM` carries for a section, and the bus the
# section's second argument is. Everything here either answers a name the
# shipped sections read or refuses by naming itself.
_ELEMENTS = r"""
import { setImmediate as hostImmediate, setTimeout as realSetTimeout }
  from 'node:timers';

const STORAGE = new Map();
const CLICKS = clicks;
const ERRORS = [];
const SELECTORS = new Map();
const LISTENERS = [];
const PARKED = [];

// Only the pump moves this, and only while a command window is open, so a
// scenario that never runs a command reads the host clock unchanged.
const hostNow = Date.now;
let SPENT = 0;
Date.now = () => hostNow() + SPENT;

function unmodelled(what, value) {
  return new Error('dashboard section harness does not model ' + what
    + ': ' + String(value));
}

function describe(value) {
  try {
    if (typeof value === 'string') return value;
    if (value && typeof value.message === 'string') return value.message;
    return String(value);
  } catch (failure) {
    return '[unprintable value]';
  }
}

const realConsoleError = console.error.bind(console);
console.error = (...args) => {
  ERRORS.push(args.map(describe).join(' '));
  realConsoleError(...args);
};

class DashEl extends El {
  constructor(tag) {
    super(tag);
    this._classes = this._classes || [];
    this._parent = null;
  }
  get className() { return this._classes.join(' '); }
  set className(v) { this._classes = String(v).split(' ').filter(Boolean); }
  // The base constructor assigns its no-op class set; swallowing that one
  // assignment is what keeps it off the instance, where it would shadow
  // the getter below and make every `classList` read answer the stub.
  set classList(_stub) { return undefined; }
  get classList() {
    if (!this._classList) {
      const el = this;
      this._classList = {
        add(name) {
          const key = String(name);
          if (!el._classes.includes(key)) el._classes.push(key);
        },
        remove(name) {
          const key = String(name);
          const at = el._classes.indexOf(key);
          if (at >= 0) el._classes.splice(at, 1);
        },
        contains(name) { return el._classes.includes(String(name)); },
        get value() { return el._classes.join(' '); },
        get length() { return el._classes.length; },
      };
    }
    return this._classList;
  }
  get parentNode() { return this._parent; }
  get nextSibling() {
    const kids = this._parent ? this._parent.children : null;
    if (!kids) return null;
    const at = kids.indexOf(this);
    if (at < 0 || at + 1 >= kids.length) return null;
    return kids[at + 1];
  }
  appendChild(child) {
    const done = super.appendChild(child);
    child._parent = this;
    return done;
  }
  append(...items) {
    for (const item of items) {
      this.appendChild(item instanceof El ? item : textNode(item));
    }
  }
  removeChild(child) {
    if (this.children.indexOf(child) < 0) {
      throw unmodelled('removeChild of a non-child', child.tag);
    }
    const done = super.removeChild(child);
    child._parent = null;
    return done;
  }
  insertBefore(node, reference) {
    if (!(node instanceof El)) throw unmodelled('an insertBefore node', node);
    const kids = this.children;
    let at = kids.length;
    if (reference !== undefined && reference !== null) {
      at = kids.indexOf(reference);
      if (at < 0) {
        throw unmodelled('an insertBefore reference outside the parent',
                         reference.tag);
      }
    }
    kids.splice(at, 0, node);
    node._parent = this;
    return node;
  }
  remove() {
    if (!this._parent) throw unmodelled('a remove outside the tree', this.tag);
    this._parent.removeChild(this);
  }
  get innerHTML() { throw unmodelled('an innerHTML read', this.tag); }
  set innerHTML(v) {
    const parsed = parseFragment(v);
    this.children = [];
    this.text = '';
    if (parsed) this.appendChild(parsed);
  }
}
El = DashEl;

// The three modules that reset a list host assign this one bare div and
// nothing else, so the parser is held to that shape and refuses the rest
// by naming the assignment rather than producing an empty list.
function parseFragment(html) {
  const text = String(html).trim();
  if (text === '') return null;
  const end = text.lastIndexOf('</div>');
  if (!text.startsWith('<div') || end !== text.length - 6) {
    throw unmodelled('an innerHTML assignment of', text);
  }
  const inner = text.slice(5, end);
  const gt = inner.indexOf('>');
  if (gt < 0) throw unmodelled('an innerHTML assignment of', text);
  const body = inner.slice(gt + 1);
  const attrs = inner.slice(0, gt);
  if (body.indexOf('<') >= 0 || body.indexOf('>') >= 0) {
    throw unmodelled('an innerHTML assignment of', text);
  }
  const eq = attrs.indexOf('=');
  const name = eq < 0 ? '' : attrs.slice(0, eq).trim();
  const raw = eq < 0 ? '' : attrs.slice(eq + 1).trim();
  if (name !== 'class' || raw.length < 2 || raw[0] !== '"'
      || raw[raw.length - 1] !== '"' || raw.indexOf('=') >= 0) {
    throw unmodelled('an innerHTML assignment of', text);
  }
  const node = new El('div');
  node.className = raw.slice(1, -1);
  if (body !== '') node.appendChild(textNode(body));
  return node;
}

globalThis.document.body = new El('body');
globalThis.document.querySelector = (selector) => {
  const key = String(selector);
  if (!SELECTORS.has(key)) throw new Error('unmodeled selector ' + key);
  return SELECTORS.get(key);
};
globalThis.localStorage = {
  getItem: (key) => (STORAGE.has(String(key)) ? STORAGE.get(String(key))
    : null),
  setItem: (key, value) => { STORAGE.set(String(key), String(value)); },
  removeItem: (key) => { STORAGE.delete(String(key)); },
  clear: () => { STORAGE.clear(); },
};

function park(kind, callback, delay, ...extra) {
  PARKED.push({ id: PARKED.length + 1, kind, callback, extra,
                delay: Number(delay) || 0 });
  return PARKED.length;
}

function clearParked(id) {
  if (!(id >= 1 && id <= PARKED.length)) return false;
  if (!PARKED[id - 1]) return false;
  PARKED[id - 1] = null;
  return true;
}

globalThis.setTimeout = (callback, delay, ...extra) => (
  park('timeout', callback, delay, ...extra));
globalThis.clearTimeout = (id) => { clearParked(id); };
globalThis.setInterval = (callback, delay, ...extra) => (
  park('interval', callback, delay, ...extra));
globalThis.clearInterval = (id) => { clearParked(id); };

// A parked clock would strand every command inside its first poll:
// `api.js` waits 250 ms between result attempts and a scenario can never
// drive that from outside its own `await`. So a command window opens when
// a `/command` is answered, and the pump spends one timeout parked inside
// it per turn, adding its delay to the virtual clock. The shipped loop
// then spends its 15 s budget the way it spends it -- sixty 250 ms
// attempts -- instead of fifteen thousand attempts that take as long as
// the budget itself. Outside a window nothing runs, which is what makes
// a two-click `armedAction` and a toast that has not faded assertable.
const PUMP = { open: false, from: 0 };

function spendOne() {
  if (!PUMP.open) return false;
  for (let at = PUMP.from; at < PARKED.length; at += 1) {
    const slot = PARKED[at];
    if (!slot || slot.kind !== 'timeout') continue;
    PARKED[at] = null;
    SPENT += slot.delay;
    slot.callback(...slot.extra);
    return true;
  }
  return false;
}

// Nothing left to spend is how a loop that gave up announces itself: the
// window closes rather than spinning on a turn that can never advance.
function pumpStep() {
  if (spendOne()) { hostImmediate(pumpStep); return; }
  PUMP.open = false;
}

function openPump() {
  PUMP.from = PARKED.length;
  PUMP.open = true;
  hostImmediate(pumpStep);
}

// The bus the dashboard hands every section as its second argument. It
// records its listeners so a scenario can drive a tab event.
const bus = {
  on(fn) {
    LISTENERS.push(fn);
    return () => {
      const at = LISTENERS.indexOf(fn);
      if (at >= 0) LISTENERS.splice(at, 1);
    };
  },
  emit(event) {
    for (const fn of LISTENERS.slice()) fn(event);
  },
};
"""


# The strict transport, the driver and the report. Every response member
# and every plan a scenario reads is answered by name; a request the
# scenario never planned is recorded and then refused.
_TRANSPORT = r"""
const ROUTES = new Map();
const REQUESTS = [];
const REFUSALS = [];
const LEDGER = { command: null, generation: 0 };

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
function commandAnswer(spec, body) {
  if (spec.status !== undefined && spec.status !== 200) {
    const failure = spec.json === undefined
      ? { error: spec.error === undefined ? 'command refused' : spec.error }
      : spec.json;
    return jsonAnswer(failure, spec.status);
  }
  LEDGER.command = { id: body.id, did: spec.did === undefined
    ? null : String(spec.did) };
  LEDGER.generation += 1;
  openPump();
  return jsonAnswer(spec.did === undefined ? { ok: true }
    : { did: String(spec.did) });
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
  if (!spec) {
    REFUSALS.push({ n: REQUESTS.length, target: whole });
    throw new Error('unexpected request ' + whole);
  }
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


SHELL = _DOM + _ELEMENTS + _TRANSPORT


def section_path(name: str) -> Path:
    """The path `build_harness` passes to the child for a dashboard module."""
    parts = PurePosixPath(name)
    if parts.is_absolute() or '..' in parts.parts or not parts.parts:
        raise ValueError(f'dashboard module name escapes dashboard: {name}')
    return ROOT / 'dashboard' / Path(*parts.parts)


def build_harness(scenario: str, *,
                  sections: tuple[str, ...] = ()
                  ) -> DashboardNodeHarness:
    """Wrap one scenario in the shell and the shipped process boundary.

    The bounded-step count is derived from the assembled source, so a
    scenario cannot disagree with the bound it declares.
    """
    source = 'const MODULES = ' + json.dumps(list(sections)) + ';\n'
    source += SHELL + '\n' + scenario
    steps = len(_BOUNDED_AWAIT.findall(blank_js_comments(source)))
    return DashboardNodeHarness(
        source, bounded_steps=steps, module=True,
        arguments=tuple(section_path(name) for name in sections))


def run_scenario(scenario: str, *,
                 sections: tuple[str, ...] = ()) -> dict:
    """Run one scenario through the shell and return the report it printed."""
    result = run_dashboard_node(build_harness(scenario, sections=sections))
    return json.loads(result.stdout)
