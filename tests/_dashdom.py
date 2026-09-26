"""The DOM half of the dashboard shell harness.

`_dashshell.SHELL` is this string followed by the transport, driver and
report half, and the two halves share one module scope in the child, so
the transport half reads `PARKED`, `OBSERVERS`, `STORAGE` and `CLICKS`
from here. The split is the size baseline's own remedy -- a file over the
700-line test ceiling is relocated, never given an entry -- and it puts
each half's code with the half that owns it.

Nothing here decides how the dashboard should behave. A surface that is
not modelled fails by name rather than answering, because `app.js`
behaves completely differently between "nothing matched" and "the
scaffold did not understand", and a permissive scaffold makes the first
look like the second.

The scaffold is a JavaScript string, so it obeys the bound-count rules
`_dashnode` enforces: no slash token outside a string or comment and no
template expression, which is why every selector is parsed by hand.
"""

DOM = r"""
// A document whose queries walk the live tree, a class set, a parked
// clock, and an observer that refuses a member it does not model.
phase('dashboard shell DOM ready');

const STORAGE = new Map();
const CLICKS = [];
const PARKED = [];
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

function plainName(name) {
  return Array.from(name).every((c) => (
    (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
    || (c >= '0' && c <= '9') || c === '-'));
}

// A quoted value is either quote character; a bare one is an identifier.
// Anything else -- an unbalanced quote, a stray `"` -- is refused by name,
// because a value it cannot read is a selector that matches nothing, and
// "matched nothing" is a different situation to the code under test.
function quotedValue(value) {
  if (value.length < 2) return null;
  const quote = value[0];
  if (quote !== '"' && quote !== "'") return null;
  if (value[value.length - 1] !== quote) return null;
  return value.slice(1, -1);
}

function parseAttr(body, selector) {
  const eq = body.indexOf('=');
  if (eq < 0) {
    if (!body.trim()) throw unsupported(selector, 'empty attribute name');
    return { name: body.trim(), value: null };
  }
  const name = body.slice(0, eq).trim();
  if (!name) throw unsupported(selector, 'empty attribute name');
  const raw = body.slice(eq + 1).trim();
  const quoted = quotedValue(raw);
  if (quoted !== null) return { name, value: quoted };
  if (!raw || !plainName(raw)) {
    throw unsupported(selector, 'unreadable attribute value');
  }
  return { name, value: raw };
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
  for (let i = from; i >= 0; i -= 1) {
    while (node && !matchesPart(node, specs[i])) node = node.parent;
    if (!node) return false;
    node = node.parent;
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
// by the scenario, never reached by wall-clock time. The extra arguments
// real setTimeout passes to the callback are kept, so a scenario can
// drive a callback that takes one.
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

globalThis.setTimeout = (cb, d, ...x) => park('timeout', cb, d, ...x);
globalThis.setInterval = (cb, d, ...x) => park('interval', cb, d, ...x);
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
"""
