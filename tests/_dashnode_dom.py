"""The DOM scaffold the dashboard node harnesses mount a module into.

`_dashnode.py` owns the PROCESS boundary: the harness class, the bounded
await, the outer timeout, the Windows pipe work. The JavaScript DOM the child
runs against is a different thing with a different consumer list, and
holding both in one file put this one at its 700-line ceiling with no room
for a surface a shipped section reads. The scaffold moved here verbatim and
`_dashnode` imports it, so every existing consumer keeps writing
`_dashnode.DOM` and nothing else about any of them changed.

Which half owns which name, because the modules that follow compose this one
and a reader has to tell them apart: `phase`, `bounded`, `leave` and
`clicks` come from the PRELUDE in `_dashnode`. `pathToFileURL`, `El`,
`dataSelector`, `textNode`, `Node`, `document`, `jsonResponse`, `token`,
`localStorage`, `setTimeout`, `clearTimeout`, `setInterval` and `settle`
are defined here.

`El` is permissive about a member it does not have -- an absent one answers
`undefined` where a browser answers a node -- so a section that reads one
gets a wrong answer rather than a refusal. The section shell in
`_dashsection.py` is where the suite for a shipped section composes this
scaffold, and `tests/test_dashsection_harness.py` holds the refusals.
"""

DOM = r"""
import { pathToFileURL } from 'node:url';
phase('dashboard harness started');
const clicks = [];
// The shipped dashboard both WRITES `[data-stat=rate]` on an element it has
// just built and READS `[data-meta="tab-count"]` off elements the page owns,
// so one grammar answers both and the quoted and unquoted spellings are the
// same selector. A matcher that took only one of them would answer the
// other's call with a refusal a reader cannot tell from a miss, which is
// the one confusion this double exists to prevent.
//
// The grammar is deliberately narrow, and narrowness is the refusal: an
// existence test (`[data-sub]`), a class or descendant selector, a name
// this one does not spell, and a quoted value with no closing quote are
// all shapes it declines, because answering one of those wrongly is the
// failure this double is for. `dataset` keys are camelCase and every
// attribute name in the shipped dashboard is lowercase, so a lowercase
// name is the whole of the domain rather than a shortcut.
//
// String operations, not a regular expression: `_dashnode`'s bound-count
// check refuses a slash token in the harness source, and every other
// parser in these two modules parses that way for the same reason.
function dataSelector(selector) {
  const text = String(selector).trim();
  const eq = text.indexOf('=');
  if (text.slice(0, 6) !== '[data-' || text.slice(-1) !== ']'
      || eq < 7) return null;
  const name = text.slice(6, eq);
  for (const ch of name) {
    if (ch !== '-' && (ch < 'a' || ch > 'z')) return null;
  }
  let value = text.slice(eq + 1, -1);
  if (value.slice(0, 1) === '"') {
    if (value.length < 2 || value.slice(-1) !== '"') return null;
    value = value.slice(1, -1);
  } else if (value.indexOf('"') >= 0) {
    return null;
  }
  return { name, value };
}

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.text = '';
    this.attrs = {};
    this.listeners = {};
    this.style = {};
    this.dataset = {};
    this.className = '';
    this.id = '';
    this.disabled = false;
    this._value = '';
    this.classList = { add() {}, remove() {} };
  }
  get firstChild() { return this.children[0] || null; }
  get lastChild() {
    const kids = this.children;
    return kids.length ? kids[kids.length - 1] : null;
  }
  get options() { return this.children.filter((c) => c.tag === 'option'); }
  get value() { return this._value; }
  set value(v) { this._value = String(v); }
  get textContent() {
    return this.text + this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) { this.children = v === '' ? [] : [textNode(v)]; }
  set innerHTML(v) { this.children = []; this.text = ''; }
  appendChild(child) { this.children.push(child); return child; }
  append(...items) {
    for (const item of items) {
      this.children.push(item instanceof El ? item : textNode(item));
    }
  }
  removeChild(child) {
    this.children.splice(this.children.indexOf(child), 1);
    if (child.tag === 'option' && child.value === this._value) {
      this._value = '';
    }
    return child;
  }
  remove() {}
  focus() {}
  setAttribute(name, v) {
    this.attrs[name] = String(v);
    if (name === 'value') this._value = String(v);
  }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  click() {
    clicks.push({ tag: this.tag, text: this.textContent,
                  href: this.attrs.href, download: this.attrs.download });
    for (const fn of this.listeners.click || []) {
      fn({ currentTarget: this, preventDefault() {} });
    }
  }
  all() {
    const out = [];
    for (const c of this.children) out.push(c, ...c.all());
    return out;
  }
  find(selector) {
    const spec = dataSelector(selector);
    if (!spec) throw new Error('unsupported selector ' + selector);
    const match = this.all().filter(
      (el) => el.dataset[spec.name] === spec.value);
    return match.length ? match[0] : null;
  }
  querySelector(selector) { return this.find(selector); }
  byText(text) {
    return this.all().find(
      (el) => el.tag !== '#text' && el.textContent === text) || null;
  }
}
function textNode(value) {
  const node = new El('#text');
  node.text = String(value);
  return node;
}
globalThis.Node = El;
globalThis.document = {
  body: new El('body'),
  createElement: (tag) => new El(tag),
  createTextNode: textNode,
  getElementById: () => null,
  querySelector: () => new El('span'),
  // The page's own furniture, which a section reaches from outside its
  // container. It is walked rather than registered: `dashboard/index.html`
  // carries the status-line count the dashboard writes to, and a registry
  // would make a scenario's fixture indistinguishable from the page it
  // stands for. A selector the grammar does not parse refuses by name.
  querySelectorAll: (selector) => {
    const spec = dataSelector(selector);
    if (!spec) throw new Error('unsupported selector ' + selector);
    return globalThis.document.body.all().filter(
      (el) => el.dataset[spec.name] === spec.value);
  },
};
function jsonResponse(data) {
  return {
    ok: true, status: 200,
    headers: { get: () => 'application/json' },
    json: async () => data, text: async () => JSON.stringify(data),
  };
}
let token = 'dashboard-token';
globalThis.localStorage = {
  getItem: (key) => key === 'daedalus-token' ? token : '',
  setItem() {},
};
globalThis.setTimeout = (callback) => { callback(); return 0; };
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 0;
const settle = () => new Promise((resolve) => setImmediate(resolve));
"""
