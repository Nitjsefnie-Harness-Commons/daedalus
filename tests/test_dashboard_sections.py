#!/usr/bin/env python3
"""What a dashboard section puts on the wire, run rather than read.

The eval panel's mistake leaves the page: a command aimed at a tab the
operator never chose. The section is mounted into a small DOM in Node,
driven through its own buttons, and judged on the commands it sends.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


# Enough DOM for `h`, `field`, `clear` and the selectors each section uses.
# Every click on an element is recorded with the href it carried, so a
# synthesized download anchor is seen the same way a rendered one is.
_DOM = r"""
import { pathToFileURL } from 'node:url';
phase('dashboard harness started');
const clicks = [];
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
    if (!selector.startsWith('[data-role=')) {
      throw new Error('unsupported selector ' + selector);
    }
    const role = selector.slice(11, -1);
    return this.all().find((el) => el.dataset.role === role) || null;
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
};
function jsonResponse(data) {
  return {
    ok: true, status: 200,
    headers: { get: () => 'application/json' },
    json: async () => data, text: async () => JSON.stringify(data),
  };
}
const token = 'dashboard-token';
globalThis.localStorage = {
  getItem: (key) => key === 'daedalus-token' ? token : '',
  setItem() {},
};
globalThis.setTimeout = (callback) => { callback(); return 0; };
globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 0;
const settle = () => new Promise((resolve) => setImmediate(resolve));
"""


_EVAL_HARNESS = _dashnode.DashboardNodeHarness(_DOM + r"""
(async () => {
let tabs = [];
const puts = [];
const listeners = [];
const bus = { on: (fn) => listeners.push(fn) };
globalThis.fetch = async (target, init) => {
  const options = init || {};
  const where = String(target);
  if (options.method === 'PUT') {
    puts.push(JSON.parse(options.body));
    return jsonResponse({ ok: true, did: 'delivery' });
  }
  if (where.startsWith('/tabs')) return jsonResponse(tabs);
  if (where.includes('consume=1')) {
    return jsonResponse({ consumed: true, resultGeneration: 'gen' });
  }
  if (where.startsWith('/result')) {
    return jsonResponse({
      id: puts[puts.length - 1].id, deliveryId: 'delivery',
      resultGeneration: 'gen', result: 'ran', world: 'page',
    });
  }
  throw new Error('unexpected fetch ' + where);
};
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container, bus);
await bounded(settle(), 'eval tab selector render', _dashnodeStepTimeoutMs);
const sel = container.find('[data-role=tab-select]');
const runBtn = container.find('[data-role=run]');
const metaEl = container.find('[data-role=meta]');
const untargeted = container.all().find(
  (el) => el.tag === 'button' && el !== runBtn
    && el.dataset.role !== 'clear-history');
container.find('[data-role=code]').value = 'document.title';
runBtn.click();
await bounded(settle(), 'run with no target', _dashnodeStepTimeoutMs);
const refused = {
  selected: sel.value, puts: puts.length, status: metaEl.textContent,
};
untargeted.click();
await bounded(settle(), 'run in the active tab', _dashnodeStepTimeoutMs);
const activeTab = {
  puts: puts.length, tab: puts.length ? puts[puts.length - 1].tab : null,
  label: untargeted.textContent, title: untargeted.attrs.title || '',
};
tabs = [{ tabId: '11', title: 'first' }];
for (const fn of listeners) fn({ type: 'tabs-synced' });
await bounded(settle(), 'tab sync refresh', _dashnodeStepTimeoutMs);
sel.value = '11';
runBtn.click();
await bounded(settle(), 'run against a chosen tab', _dashnodeStepTimeoutMs);
const targeted = {
  puts: puts.length, tab: puts.length ? puts[puts.length - 1].tab : null,
};
phase('dashboard call settled');
process.stdout.write(JSON.stringify({ refused, activeTab, targeted }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=6, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'eval.js',))


def test_run_refuses_an_empty_target_and_names_where_untargeted_code_runs(
        _tmp):
    """An empty selection is not a target; only the explicit button sends
    none, and it says the code runs in the browser's active tab."""
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['refused']['selected'] == '', seen
    assert seen['refused']['puts'] == 0, seen
    assert seen['refused']['status'].strip(), seen
    assert seen['activeTab']['puts'] == 1, seen
    assert seen['activeTab']['tab'] == '', seen
    for text in (seen['activeTab']['label'], seen['activeTab']['title']):
        assert 'active tab' in text.lower(), seen
        assert 'all tabs' not in text.lower(), seen
    assert seen['targeted'] == {'puts': 2, 'tab': '11'}, seen


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashsections_')


if __name__ == '__main__':
    raise SystemExit(main())
