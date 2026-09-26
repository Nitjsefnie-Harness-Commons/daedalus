"""The dashboard shell harness for `dashboard/app.js`.

`app.js` is the dashboard's entry point and nothing ran it. The shared
`DOM` in `_dashnode` is sized for one section mounted into a fresh
container; the entry point reaches for surface that scaffold does not
have -- document-wide selectors, a class set, an `IntersectionObserver`,
a parked clock and a live stream.

`SHELL` supplies that surface and `build_harness` reuses `_dashnode`'s
process boundary, so a suite writes a scenario and reads the report it
printed:

    report = _dashshell.run_scenario(scenario, modules=('app.js',))

Nothing here decides how the dashboard should behave; it only makes a
behaviour assertable. A surface that is not modelled fails by name
instead of answering, because `app.js` behaves completely differently
between "nothing matched" and "the scaffold did not understand".

The shell is a JavaScript string, so it obeys the bound-count rules
`_dashnode` enforces: no slash token outside a string or comment and no
template expression, which is why every selector is parsed by hand. A
scenario shares the shell's scope, so it wraps itself in an IIFE.
"""
import json
from pathlib import Path, PurePosixPath

from _dashnode import (DashboardNodeHarness, _BOUNDED_AWAIT,  # noqa: F401
                       dashboard_child_timeout, run_dashboard_node)
from _jsread import blank_js_comments
from _repo import ROOT

__all__ = ['SHELL', 'UNPLANNED_STATUS', 'build_harness',
           'dashboard_child_timeout', 'dashboard_module', 'run_scenario']


# The status a request the scenario never planned is answered with. The
# double records the refusal and keeps serving, because a throwing double
# is swallowed by sse.js's catch and reads as an ordinary stream error.
UNPLANNED_STATUS = 599


SHELL = r"""
// The dashboard shell scaffold: a DOM, a parked clock, a strict transport
// and an observer double, for the modules the dashboard serves.
import { setTimeout as realSetTimeout } from 'node:timers';
import { pathToFileURL } from 'node:url';
phase('dashboard shell started');

const STORAGE = new Map();
const CLICKS = [];
const ERRORS = [];
const PARKED = [];
const ROUTES = new Map();
const REQUESTS = [];
const REFUSALS = [];
const SCRIPTS = [];
const OBSERVERS = [];
const DOC_LISTENERS = {};
const WINDOW_LISTENERS = {};
const NAME_STOP = '#.[ ';

function textNode(value) {
  const node = new El('#text');
  node.text = String(value);
  return node;
}

function camelCase(name) {
  return String(name).split('-').map((part, i) => (
    i === 0 ? part : part.charAt(0).toUpperCase() + part.slice(1)
  )).join('');
}

function classListFor(el) {
  return {
    add(name) { el.classes.add(String(name)); },
    remove(name) { el.classes.delete(String(name)); },
    contains(name) { return el.classes.has(String(name)); },
    toggle(name, force) {
      const key = String(name);
      const on = force === undefined ? !el.classes.has(key) : !!force;
      if (on) el.classes.add(key); else el.classes.delete(key);
      return on;
    },
    get value() { return Array.from(el.classes).join(' '); },
    get length() { return el.classes.size; },
    toString() { return this.value; },
  };
}

// A NodeList is iterable and indexed but is NOT an Array: a scaffold
// handing one back would pass a build that dropped app.js's Array.from.
class NodeList {
  constructor(items) {
    this._items = items;
    this.length = items.length;
    for (let i = 0; i < items.length; i += 1) this[i] = items[i];
  }
  [Symbol.iterator]() { return this._items[Symbol.iterator](); }
  forEach(fn, thisArg) { this._items.forEach(fn, thisArg); }
  values() { return this._items.values(); }
}

function first(list) { return list.length ? list[0] : null; }

class El {
  constructor(tag) {
    this.tag = String(tag);
    this.parent = null;
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.classes = new Set();
    this.text = '';
    this.disabled = false;
    this.checked = false;
    this.focused = false;
    this.selected = false;
    this._value = '';
    this.classList = classListFor(this);
  }
  get id() { return this.attrs.id === undefined ? '' : this.attrs.id; }
  set id(v) { this.attrs.id = String(v); }
  get className() { return Array.from(this.classes).join(' '); }
  set className(v) {
    this.classes = new Set(String(v).split(' ').filter(Boolean));
  }
  get firstChild() { return this.children.length ? this.children[0] : null; }
  get options() {
    return new NodeList(this.children.filter((c) => c.tag === 'option'));
  }
  get value() { return this._value; }
  set value(v) { this._value = String(v); }
  get textContent() {
    return this.text + this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) {
    this.text = '';
    this.children = v === '' ? [] : [textNode(v)];
  }
  all() {
    const out = [];
    for (const child of this.children) out.push(child, ...child.all());
    return out;
  }
  appendChild(child) {
    this.children.push(child);
    child.parent = this;
    return child;
  }
  append(...items) {
    for (const item of items) {
      this.appendChild(item instanceof El ? item : textNode(item));
    }
  }
  removeChild(child) {
    const at = this.children.indexOf(child);
    if (at < 0) throw new Error('removeChild: not a child of this element');
    this.children.splice(at, 1);
    child.parent = null;
    if (child.tag === 'option' && child.value === this._value) {
      this._value = '';
    }
    return child;
  }
  replaceWith(next) {
    if (!this.parent) throw new Error('replaceWith outside the tree');
    const at = this.parent.children.indexOf(this);
    this.parent.children[at] = next;
    next.parent = this.parent;
    this.parent = null;
  }
  remove() {
    if (!this.parent) throw new Error('remove outside the tree');
    this.parent.removeChild(this);
  }
  setAttribute(name, v) {
    const key = String(name);
    if (key.startsWith('data-')) {
      this.dataset[camelCase(key.slice(5))] = String(v);
      return;
    }
    this.attrs[key] = String(v);
    if (key === 'value') this._value = String(v);
  }
  getAttribute(name) {
    const key = String(name);
    if (key.startsWith('data-')) {
      const slot = camelCase(key.slice(5));
      return slot in this.dataset ? this.dataset[slot] : null;
    }
    return key in this.attrs ? this.attrs[key] : null;
  }
  addEventListener(type, fn) {
    const key = String(type);
    if (!this.listeners[key]) this.listeners[key] = [];
    this.listeners[key].push(fn);
  }
  fire(type, event) {
    const key = String(type);
    const shaped = event === undefined
      ? { type: key, currentTarget: this, target: this } : event;
    for (const fn of (this.listeners[key] || []).slice()) fn(shaped);
  }
  click() {
    CLICKS.push({ tag: this.tag, text: this.textContent,
      href: this.getAttribute('href') });
    this.fire('click', { type: 'click', currentTarget: this, target: this,
      preventDefault() {}, stopPropagation() {} });
  }
  focus() { this.focused = true; }
  select() { this.selected = true; }
  querySelectorAll(selector) { return selectAll(this, selector); }
  querySelector(selector) { return first(selectAll(this, selector)); }
}

function unsupported(selector, why) {
  return new Error(
    'dashboard shell does not implement selector (' + why + '): ' + selector);
}

function parseAttr(body, selector) {
  const eq = body.indexOf('=');
  if (eq < 0) {
    if (!body.trim()) throw unsupported(selector, 'empty attribute name');
    return { name: body.trim(), value: null };
  }
  const name = body.slice(0, eq).trim();
  let value = body.slice(eq + 1).trim();
  const quoted = value.length > 1 && value[0] === '"'
    && value[value.length - 1] === '"';
  if (quoted) value = value.slice(1, -1);
  if (!name) throw unsupported(selector, 'empty attribute name');
  return { name, value };
}

function plainName(name) {
  return Array.from(name).every((c) => (
    (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
    || (c >= '0' && c <= '9') || c === '-'));
}

function parsePart(part, selector) {
  if (part === '') throw unsupported(selector, 'empty selector part');
  const spec = { tag: null, id: null, classes: [], attrs: [] };
  let i = 0;
  while (i < part.length) {
    const ch = part[i];
    if (ch === '#' || ch === '.') {
      let j = i + 1;
      while (j < part.length && NAME_STOP.indexOf(part[j]) < 0) j += 1;
      const name = part.slice(i + 1, j);
      if (!name) throw unsupported(selector, 'empty name after ' + ch);
      if (ch === '#') spec.id = name; else spec.classes.push(name);
      i = j;
      continue;
    }
    if (ch === '[') {
      const j = part.indexOf(']', i);
      if (j < 0) throw unsupported(selector, 'unclosed attribute');
      spec.attrs.push(parseAttr(part.slice(i + 1, j), selector));
      i = j + 1;
      continue;
    }
    let j = i;
    while (j < part.length && NAME_STOP.indexOf(part[j]) < 0) j += 1;
    const name = part.slice(i, j);
    const named = spec.tag !== null || spec.id !== null
      || spec.classes.length || spec.attrs.length;
    if (named) throw unsupported(selector, 'two names in one part');
    if (!name || !plainName(name)) {
      throw unsupported(selector, 'unknown syntax');
    }
    spec.tag = name.toLowerCase();
    i = j;
  }
  return spec;
}

function compile(selector) {
  if (typeof selector !== 'string' || selector.trim() === '') {
    throw new Error(
      'dashboard shell selector is not a selector: ' + String(selector));
  }
  const trimmed = selector.trim();
  return trimmed.split(' ').map((part) => parsePart(part, trimmed));
}

function matchesPart(el, spec) {
  if (spec.tag !== null && el.tag !== spec.tag) return false;
  if (spec.id !== null && el.id !== spec.id) return false;
  for (const cls of spec.classes) {
    if (!el.classes.has(cls)) return false;
  }
  for (const attr of spec.attrs) {
    const value = el.getAttribute(attr.name);
    if (value === null) return false;
    if (attr.value !== null && value !== attr.value) return false;
  }
  return true;
}

function hasAncestors(el, specs, from) {
  let node = el.parent;
  let i = from;
  while (i >= 0) {
    let matched = null;
    while (node) {
      if (matchesPart(node, specs[i])) { matched = node; break; }
      node = node.parent;
    }
    if (!matched) return false;
    node = matched.parent;
    i -= 1;
  }
  return true;
}

function selectAll(root, selector) {
  const specs = compile(selector);
  const last = specs.length - 1;
  const out = [];
  for (const el of root.all()) {
    if (!matchesPart(el, specs[last])) continue;
    if (hasAncestors(el, specs, last - 1)) out.push(el);
  }
  return new NodeList(out);
}

function on(store, type, fn) {
  const key = String(type);
  if (!store[key]) store[key] = [];
  store[key].push(fn);
}

function fireAll(store, type, event) {
  for (const fn of (store[String(type)] || []).slice()) fn(event);
}

const BODY = new El('body');
globalThis.Node = El;
globalThis.document = {
  readyState: 'complete',
  body: BODY,
  createElement: (tag) => new El(tag),
  createTextNode: (value) => textNode(value),
  getElementById: (id) => {
    const want = String(id);
    return BODY.all().find((el) => el.id === want) || null;
  },
  querySelectorAll: (selector) => selectAll(BODY, selector),
  querySelector: (selector) => first(selectAll(BODY, selector)),
  addEventListener: (type, fn) => on(DOC_LISTENERS, type, fn),
  fire: (type, event) => fireAll(DOC_LISTENERS, type, { type }),
};
globalThis.window = {
  addEventListener: (type, fn) => on(WINDOW_LISTENERS, type, fn),
  fire: (type, event) => fireAll(WINDOW_LISTENERS, type, event),
};
globalThis.localStorage = {
  getItem: (key) => (STORAGE.has(String(key)) ? STORAGE.get(String(key))
    : null),
  setItem: (key, value) => { STORAGE.set(String(key), String(value)); },
  removeItem: (key) => { STORAGE.delete(String(key)); },
  clear: () => { STORAGE.clear(); },
};

// Every timer parks: app.js's 1s clock and sse.js's 3s retry are driven
// by the scenario, never reached by wall-clock time.
function park(kind, callback, delay) {
  PARKED.push({ id: PARKED.length + 1, kind, callback,
    delay: Number(delay) || 0 });
  return PARKED.length;
}

function clearParked(id) {
  if (!(id >= 1 && id <= PARKED.length)) return false;
  if (!PARKED[id - 1]) return false;
  PARKED[id - 1] = null;
  return true;
}

globalThis.setTimeout = (callback, delay) => park('timeout', callback, delay);
globalThis.setInterval = (callback, delay) => park('interval',
  callback, delay);
globalThis.clearTimeout = (id) => { clearParked(id); };
globalThis.clearInterval = (id) => { clearParked(id); };

const OBSERVER_MEMBERS = new Set([
  'callback', 'options', 'observed', 'observe', 'unobserve', 'disconnect',
  'takeRecords', 'fire', 'then',
]);

// An unmodelled member fails by name; undefined would be a silent no-op.
const observerGuard = {
  get(target, prop) {
    if (typeof prop === 'symbol' || OBSERVER_MEMBERS.has(prop)) {
      return Reflect.get(target, prop);
    }
    throw new Error(
      'IntersectionObserver member not modelled: ' + String(prop));
  },
};

function newObserver(callback, options) {
  const self = {
    callback,
    options,
    observed: [],
    observe(el) { self.observed.push(el); },
    unobserve(el) {
      const at = self.observed.indexOf(el);
      if (at >= 0) self.observed.splice(at, 1);
    },
    disconnect() { self.observed = []; },
    takeRecords() { return []; },
    fire(entries) { self.callback(entries, self.agent); },
  };
  self.agent = new Proxy(self, observerGuard);
  OBSERVERS.push(self.agent);
  return self.agent;
}

globalThis.IntersectionObserver = newObserver;

function serverPrefix(target) {
  const marks = [target.indexOf('?'), target.indexOf('#')]
    .filter((at) => at >= 0);
  const stop = marks.length ? Math.min.apply(null, marks) : target.length;
  const scheme = target.indexOf('://');
  const slash = target.indexOf('/', scheme < 0 ? 0 : scheme + 3);
  if (slash < 0 || slash > stop) return '';
  return target.slice(0, slash);
}

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

function abortError() {
  const error = new Error('the request was aborted');
  error.name = 'AbortError';
  return error;
}

function settleWith(script, result) {
  if (script.waiting) {
    const waiting = script.waiting;
    script.waiting = null;
    waiting.resolve(result);
    return;
  }
  script.queued.push(result);
}

// Every settlement is recorded, so a frame split across two reads, a
// stream reaching its end and a teardown's abort stay assertable.
function newReaderScript() {
  const script = {
    settlements: [], queued: [], waiting: null, closed: false, why: '',
  };
  const refuse = (what) => {
    throw new Error('reader already settled (' + script.why + '): ' + what);
  };
  script.push = (text) => {
    if (script.closed) refuse('push');
    script.settlements.push({ kind: 'chunk', text: String(text) });
    settleWith(script, {
      done: false, value: new TextEncoder().encode(String(text)),
    });
  };
  script.end = () => {
    if (script.closed) refuse('end');
    script.closed = true;
    script.why = 'done';
    script.settlements.push({ kind: 'done' });
    settleWith(script, { done: true, value: undefined });
  };
  script.fail = (message) => {
    if (script.closed) refuse('fail');
    script.closed = true;
    script.why = 'error';
    script.settlements.push({ kind: 'error', message: String(message) });
    if (script.waiting) {
      const waiting = script.waiting;
      script.waiting = null;
      waiting.reject(new Error(String(message)));
    }
  };
  script.read = (signal) => {
    if (script.closed) {
      return Promise.resolve({ done: true, value: undefined });
    }
    if (script.queued.length) return Promise.resolve(script.queued.shift());
    if (script.waiting) {
      throw new Error('the reader double has two concurrent reads');
    }
    return new Promise((resolve, reject) => {
      const waiting = { resolve, reject };
      script.waiting = waiting;
      if (signal && typeof signal.addEventListener === 'function') {
        signal.addEventListener('abort', () => {
          if (script.waiting !== waiting) return;
          script.waiting = null;
          script.closed = true;
          script.why = 'abort';
          script.settlements.push({ kind: 'abort' });
          reject(abortError());
        }, { once: true });
      }
    });
  };
  return script;
}

function jsonResponse(data, status) {
  const code = status === undefined ? 200 : status;
  return {
    ok: code >= 200 && code < 300,
    status: code,
    statusText: '',
    headers: { get: () => 'application/json' },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function streamResponse(signal) {
  const script = newReaderScript();
  SCRIPTS.push(script);
  return {
    ok: true,
    status: 200,
    statusText: '',
    headers: { get: () => 'text/event-stream' },
    body: { getReader: () => ({ read: () => script.read(signal) }) },
  };
}

function refusedResponse() {
  return Object.assign(jsonResponse({}, UNPLANNED_STATUS), {
    statusText: 'unplanned request',
    headers: { get: () => 'text/plain' },
    body: null,
  });
}

// The plan is keyed on the WHOLE request target: a route-shaped match
// would serve an origin the scenario never declared, and a relative
// target is not the absolute one that was planned.
globalThis.fetch = async (target, init) => {
  const options = init === undefined ? {} : init;
  const headers = options.headers || {};
  const whole = String(target);
  const auth = headers.Authorization;
  REQUESTS.push({
    n: REQUESTS.length + 1,
    target: whole,
    method: options.method ? String(options.method) : 'GET',
    authorization: auth === undefined ? null : String(auth),
    tab: queryValue(whole, 'tab'),
    server: serverPrefix(whole),
    hasSignal: Boolean(options.signal),
    planned: ROUTES.has(whole),
  });
  const spec = ROUTES.get(whole);
  if (!spec) {
    REFUSALS.push({ n: REQUESTS.length, target: whole });
    return refusedResponse();
  }
  if (spec.stream) return streamResponse(options.signal);
  return jsonResponse(spec.json, spec.status);
};

const drive = {
  route(target, spec) {
    const key = String(target);
    if (ROUTES.has(key)) throw new Error('route already planned: ' + key);
    ROUTES.set(key, spec || {});
    return key;
  },
  planned() { return Array.from(ROUTES.keys()); },
  live() {
    return PARKED.filter(Boolean).map((slot) => (
      { id: slot.id, kind: slot.kind, delay: slot.delay }));
  },
  fire(id) {
    const slot = PARKED[id - 1];
    if (!slot) throw new Error('no parked timer with id ' + id);
    slot.callback();
    return id;
  },
  ids(predicate) {
    return PARKED.filter((slot) => slot && (!predicate || predicate(slot)))
      .map((slot) => slot.id);
  },
  fireMatching(predicate) {
    const ids = drive.ids(predicate);
    if (ids.length !== 1) {
      throw new Error('parked timer selection matched ' + ids.length);
    }
    return drive.fire(ids[0]);
  },
  lastScript() {
    if (!SCRIPTS.length) throw new Error('no stream has been requested');
    return SCRIPTS[SCRIPTS.length - 1];
  },
  observers() { return OBSERVERS.slice(); },
};

function describe(value) {
  if (typeof value === 'string') return value;
  if (value && typeof value.message === 'string') return value.message;
  try {
    return String(value);
  } catch (error) { return '<unprintable>'; }
}

const realError = console.error.bind(console);
console.error = (...args) => {
  ERRORS.push(args.map(describe).join(' '));
  realError(...args);
};

const settle = () => new Promise((resolve) => setImmediate(resolve));
const pause = (ms) => new Promise((resolve) => realSetTimeout(resolve, ms));

function load(name) {
  const at = MODULES.indexOf(name);
  if (at < 0) throw new Error('no module argument for ' + name);
  return import(pathToFileURL(process.argv[at + 1]).href);
}

function report(extra) {
  process.stdout.write(JSON.stringify(Object.assign({
    requests: REQUESTS,
    unplanned: REFUSALS,
    settlements: SCRIPTS.map((script) => script.settlements),
    timers: drive.live(),
    observers: OBSERVERS.map((io) => io.observed.length),
    clicks: CLICKS,
    errors: ERRORS,
    storage: Object.fromEntries(STORAGE),
  }, extra || {})));
}
"""


def dashboard_module(name: str) -> Path:
    """The path `build_harness` passes to the child for a dashboard module."""
    parts = PurePosixPath(name)
    if parts.is_absolute() or '..' in parts.parts or not parts.parts:
        raise ValueError(f'dashboard module name escapes dashboard: {name}')
    return ROOT / 'dashboard' / Path(*parts.parts)


def build_harness(scenario: str, *,
                  modules: tuple[str, ...] = ('app.js',)
                  ) -> DashboardNodeHarness:
    """Wrap one scenario in the shell and the shipped process boundary.

    The bounded-step count is derived from the assembled source, so a
    scenario cannot disagree with the bound it declares. A scenario
    still writes every awaited step out as `await bounded(...)`, because
    a named bound says what was being waited for.
    """
    source = 'const MODULES = ' + json.dumps(list(modules)) + ';\n'
    source += 'const UNPLANNED_STATUS = ' + str(UNPLANNED_STATUS) + ';\n'
    source += SHELL + '\n' + scenario
    steps = len(_BOUNDED_AWAIT.findall(blank_js_comments(source)))
    return DashboardNodeHarness(
        source, bounded_steps=steps, module=True,
        arguments=tuple(dashboard_module(name) for name in modules))


def run_scenario(scenario: str, *,
                 modules: tuple[str, ...] = ('app.js',)) -> dict:
    """Run one scenario through the shell and return the report it printed."""
    result = run_dashboard_node(build_harness(scenario, modules=modules))
    return json.loads(result.stdout)
