#!/usr/bin/env python3
"""What three dashboard sections put on the wire, run rather than read.

The uploads browser, the eval panel and the settings pane are the
sections whose mistakes leave the page: a link or a request target that
carries the token into browser history and the proxy access log, an
object URL that is never revoked, a command aimed at a tab the operator
never chose, or a caveat that stops saying where an untargeted command
runs. Each shipped module is mounted into a small DOM in Node, driven
through its own buttons, and judged on the fetches it makes, the hrefs
it renders, the object URLs it lets go of, the commands it sends and
the status lines it paints about them.
"""
import json
import re
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


_UPLOADS_HARNESS = _dashnode.DashboardNodeHarness(_DOM + r"""
(async () => {
const fetched = [];
const listing = { total: 2, items: [
  { id: 'up1', filename: 'a&b#c.txt', size: 3, mtime: 1,
    path: token + '/up1/a&b#c.txt' },
  { id: 'up1', filename: 'shot.png', size: 4, mtime: 2,
    path: token + '/up1/shot.png' },
] };
globalThis.fetch = async (target, init) => {
  const headers = (init && init.headers) || {};
  fetched.push({ target: String(target), auth: headers.Authorization || '' });
  if (String(target).startsWith('/upload?limit=')) {
    return jsonResponse(listing);
  }
  return {
    ok: true, status: 200,
    headers: { get: () => 'application/octet-stream' },
    blob: async () => ({ from: String(target) }), json: async () => ({}),
  };
};
let held = 0;
URL.createObjectURL = () => 'blob:held-' + (++held);
URL.revokeObjectURL = () => {};
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container);
await bounded(settle(), 'uploads listing render', _dashnodeStepTimeoutMs);
const rendered = container.all().map((el) => el.attrs.href)
  .filter((href) => href !== undefined);
const afterRender = fetched.length;
container.byText('download').click();
await bounded(settle(), 'download fetch', _dashnodeStepTimeoutMs);
const downloadFetches = fetched.slice(afterRender);
container.byText('preview').click();
await bounded(settle(), 'preview fetch', _dashnodeStepTimeoutMs);
const previewFetches = fetched.slice(afterRender + downloadFetches.length);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  rendered, fetched, downloadFetches, previewFetches, clicks,
}));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=4, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'uploads.js',))


def test_uploads_carry_the_token_in_a_header_and_never_in_a_link(_tmp):
    """Every file reaches the browser through the header-authenticated
    object-URL path, so no href on the page names the token or a route
    the bridge does not have, no request target names the token either,
    and an operator-named filename survives the trip percent-encoded."""
    result = _dashnode.run_dashboard_node(_UPLOADS_HARNESS)
    seen = json.loads(result.stdout)
    token = 'dashboard-token'
    hrefs = seen['rendered'] + [
        click['href'] for click in seen['clicks'] if click.get('href')]
    leaking = [href for href in hrefs
               if token in href or '/uploads/' in href]
    assert not leaking, leaking
    # The request target, not only the href: a blob anchor keeps the
    # token out of history, but the fetched URL is what a proxy logs.
    targets = [fetch['target'] for fetch in seen['fetched']]
    assert targets and not [t for t in targets if token in t], targets

    text_path = '/upload?path=up1%2Fa%26b%23c.txt'
    assert [f['target'] for f in seen['downloadFetches']] == [text_path], seen
    image_path = '/upload?path=up1%2Fshot.png'
    assert [f['target'] for f in seen['previewFetches']] == [image_path], seen
    for fetch in seen['downloadFetches'] + seen['previewFetches']:
        assert fetch['auth'] == f'Bearer {token}', fetch

    saved = [click for click in seen['clicks'] if click.get('download')]
    assert [click['download'] for click in saved] == ['a&b#c.txt'], seen
    assert all(click['href'].startswith('blob:') for click in saved), seen


# A file fetch settles only when the test says so, so a stale fetch can be
# rejected after a re-render has already replaced its cache entry.
_LIFECYCLE_HARNESS = _dashnode.DashboardNodeHarness(_DOM + r"""
(async () => {
const fetched = [];
const deferred = [];
const revoked = [];
const listing = { total: 1, items: [
  { id: 'up1', filename: 'a.txt', size: 1, mtime: 1,
    path: token + '/up1/a.txt' },
] };
globalThis.fetch = (target, init) => {
  const headers = (init && init.headers) || {};
  fetched.push({ target: String(target), auth: headers.Authorization || '' });
  if (String(target).startsWith('/upload?limit=')) {
    return Promise.resolve(jsonResponse(listing));
  }
  return new Promise((resolve, reject) => {
    deferred.push({ target: String(target), resolve, reject });
  });
};
const blobResponse = {
  ok: true, status: 200,
  headers: { get: () => 'application/octet-stream' },
  blob: async () => ({}), json: async () => ({}),
};
let held = 0;
URL.createObjectURL = () => 'blob:held-' + (++held);
URL.revokeObjectURL = (url) => { revoked.push(url); };
const fileFetches = () => fetched.filter(
  (f) => !f.target.startsWith('/upload?limit=')).length;
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container);
await bounded(settle(), 'first listing render', _dashnodeStepTimeoutMs);
container.byText('download').click();
await bounded(settle(), 'first file fetch', _dashnodeStepTimeoutMs);
container.find('[data-role=refresh]').click();
await bounded(settle(), 'second listing render', _dashnodeStepTimeoutMs);
container.byText('download').click();
await bounded(settle(), 'second file fetch', _dashnodeStepTimeoutMs);
const beforeRejection = fileFetches();
deferred[0].reject(new Error('stale fetch'));
await bounded(settle(), 'stale rejection', _dashnodeStepTimeoutMs);
container.byText('download').click();
await bounded(settle(), 'download after rejection', _dashnodeStepTimeoutMs);
const afterRejection = fileFetches();
deferred[1].resolve(blobResponse);
await bounded(settle(), 'second fetch settled', _dashnodeStepTimeoutMs);
const revokedBeforeRelease = revoked.slice();
container.find('[data-role=refresh]').click();
await bounded(settle(), 'third listing render', _dashnodeStepTimeoutMs);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  deferredTargets: deferred.map((d) => d.target),
  beforeRejection, afterRejection, clicks, revokedBeforeRelease, revoked,
}));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=9, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'uploads.js',))


def test_a_stale_fetch_rejecting_leaves_the_newer_entry_held(_tmp):
    """A fetch that fails after a re-render replaced its cache entry must
    not delete the replacement.

    The catch handler of the first fetch used to delete whatever the map
    held for that path, which by then was the second fetch: its object
    URL was created outside the cache and so was never revoked, and the
    next download fetched the file a third time. The stale rejection is
    delivered only once the second fetch exists, and the download after
    it is expected to reuse the second fetch rather than start another.
    """
    result = _dashnode.run_dashboard_node(_LIFECYCLE_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['deferredTargets'][:2] == ['/upload?path=up1%2Fa.txt'] * 2
    assert seen['beforeRejection'] == 2, seen
    assert seen['afterRejection'] == 2, seen['deferredTargets']
    saved = [click['href'] for click in seen['clicks']
             if click.get('download')]
    assert saved == ['blob:held-1', 'blob:held-1'], seen['clicks']
    # Held until the next release, and then let go exactly once.
    assert seen['revokedBeforeRelease'] == [], seen
    assert seen['revoked'] == ['blob:held-1'], seen


# The listing can be made to fail and the token to vanish between loads.
_REFRESH_HARNESS = _dashnode.DashboardNodeHarness(_DOM + r"""
(async () => {
const revoked = [];
const listing = { total: 1, items: [
  { id: 'up1', filename: 'a.txt', size: 1, mtime: 1,
    path: token + '/up1/a.txt' },
] };
let listingFails = false;
globalThis.fetch = async (target) => {
  if (String(target).startsWith('/upload?limit=')) {
    if (listingFails) {
      return {
        ok: false, status: 500,
        headers: { get: () => 'application/json' },
        json: async () => ({ error: 'listing down' }),
        text: async () => '',
      };
    }
    return jsonResponse(listing);
  }
  return {
    ok: true, status: 200,
    headers: { get: () => 'application/octet-stream' },
    blob: async () => ({}), json: async () => ({}),
  };
};
let held = 0;
URL.createObjectURL = () => 'blob:held-' + (++held);
URL.revokeObjectURL = (url) => { revoked.push(url); };
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container);
await bounded(settle(), 'first listing render', _dashnodeStepTimeoutMs);
container.byText('download').click();
await bounded(settle(), 'first download', _dashnodeStepTimeoutMs);
token = '';
container.find('[data-role=refresh]').click();
await bounded(settle(), 'tokenless refresh', _dashnodeStepTimeoutMs);
const afterTokenless = revoked.slice();
token = 'dashboard-token';
container.find('[data-role=refresh]').click();
await bounded(settle(), 'listing render again', _dashnodeStepTimeoutMs);
container.byText('download').click();
await bounded(settle(), 'second download', _dashnodeStepTimeoutMs);
listingFails = true;
container.find('[data-role=refresh]').click();
await bounded(settle(), 'failed refresh', _dashnodeStepTimeoutMs);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({ afterTokenless, revoked, clicks }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=7, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'uploads.js',))


def test_a_refresh_that_removes_the_rows_revokes_their_object_urls(_tmp):
    """A refresh with no token, and one whose listing fails, both replace
    the rows with a notice; the object URLs those rows held used to stay
    alive, because only a successful render released them."""
    result = _dashnode.run_dashboard_node(_REFRESH_HARNESS)
    seen = json.loads(result.stdout)
    saved = [click['href'] for click in seen['clicks']
             if click.get('download')]
    assert saved == ['blob:held-1', 'blob:held-2'], seen['clicks']
    assert seen['afterTokenless'] == ['blob:held-1'], seen
    assert seen['revoked'] == ['blob:held-1', 'blob:held-2'], seen


# An armed delete fires on its second click only while its confirm timer is
# still pending, and the DOM above runs setTimeout immediately, so the pager
# harnesses park that timer in a queue the harness never runs.
_PAGER_PREFIX = _DOM + r"""
const timers = [];
globalThis.setTimeout = (callback) => {
  timers.push(callback);
  return timers.length;
};
globalThis.clearTimeout = (id) => { timers[id - 1] = null; };
"""

_PAGER_CLAMP_HARNESS = _dashnode.DashboardNodeHarness(_PAGER_PREFIX + r"""
(async () => {
const fetched = [];
const metaSnapshots = [];
let total = 51;
const file = (n) => ({ id: 'up' + n, filename: 'f' + n + '.txt', size: 1,
  mtime: 1, path: token + '/up' + n + '/f' + n + '.txt' });
globalThis.fetch = async (target, init) => {
  const where = String(target);
  if (where.startsWith('/upload?limit=')) {
    fetched.push(where);
    // Whatever the previous round-trip painted is on screen when the next
    // request goes out, so each listing fetch records the frame it saw.
    metaSnapshots.push(container.find('[data-role=meta]').textContent);
    const query = new URLSearchParams(where.split('?')[1]);
    const offset = Number(query.get('offset'));
    const items = [];
    for (let n = offset + 1; n <= offset + 50 && n <= total; n++) {
      items.push(file(n));
    }
    return jsonResponse({ total, items });
  }
  if (init && init.method === 'DELETE') total = 50;
  return jsonResponse({});
};
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container);
await bounded(settle(), 'first listing render', _dashnodeStepTimeoutMs);
const metaEl = container.find('[data-role=meta]');
const prevBtn = container.find('[data-role=prev]');
const nextBtn = container.find('[data-role=next]');
const pagerState = () => ({
  meta: metaEl.textContent,
  prevDisabled: prevBtn.disabled,
  nextDisabled: nextBtn.disabled,
  rows: container.all().filter((el) => el.tag === 'tr').length - 1,
});
const firstPage = pagerState();
nextBtn.click();
await bounded(settle(), 'last page render', _dashnodeStepTimeoutMs);
const lastPage = pagerState();
const delBtn = container.byText('delete');
delBtn.click();
delBtn.click();
await bounded(settle(), 'delete settles', _dashnodeStepTimeoutMs);
const afterDelete = pagerState();
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  firstPage, lastPage, afterDelete, listingTargets: fetched, metaSnapshots,
}));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=4, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'uploads.js',))


def test_a_delete_that_shrinks_total_clamps_the_pager_to_the_last_page(_tmp):
    """The issue's reproduction: the last page shows 51–51 / 51, the
    last-page file is deleted, and the pager must land on the last valid
    page of the shrunken total instead of an impossible range with rows
    from a page the listing no longer has. The mid-list page is pinned
    with its exact range end, and the over-range frame must not exist
    even transiently between the listing round-trips."""
    result = _dashnode.run_dashboard_node(_PAGER_CLAMP_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['firstPage'] == {
        'meta': '1–50 / 51', 'prevDisabled': True, 'nextDisabled': False,
        'rows': 50}, seen
    assert seen['lastPage'] == {
        'meta': '51–51 / 51', 'prevDisabled': False, 'nextDisabled': True,
        'rows': 1}, seen
    assert seen['afterDelete'] == {
        'meta': '1–50 / 50', 'prevDisabled': True, 'nextDisabled': True,
        'rows': 50}, seen
    assert seen['listingTargets'] == [
        '/upload?limit=50&offset=0', '/upload?limit=50&offset=50',
        '/upload?limit=50&offset=50', '/upload?limit=50&offset=0'], seen
    assert seen['metaSnapshots'] == [
        '', '1–50 / 51', '51–51 / 51', '51–51 / 51'], seen['metaSnapshots']


_PAGER_EMPTY_HARNESS = _dashnode.DashboardNodeHarness(_PAGER_PREFIX + r"""
(async () => {
const fetched = [];
let total = 1;
globalThis.fetch = async (target, init) => {
  const where = String(target);
  if (where.startsWith('/upload?limit=')) {
    fetched.push(where);
    return jsonResponse({ total,
      items: total ? [{ id: 'up1', filename: 'a.txt', size: 1, mtime: 1,
        path: token + '/up1/a.txt' }] : [] });
  }
  if (init && init.method === 'DELETE') total = 0;
  return jsonResponse({});
};
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container);
await bounded(settle(), 'listing with one row render', _dashnodeStepTimeoutMs);
const metaEl = container.find('[data-role=meta]');
const listEl = container.find('[data-role=list]');
const prevBtn = container.find('[data-role=prev]');
const nextBtn = container.find('[data-role=next]');
const oneRow = { meta: metaEl.textContent, list: listEl.textContent,
  prevDisabled: prevBtn.disabled, nextDisabled: nextBtn.disabled };
const delBtn = container.byText('delete');
delBtn.click();
delBtn.click();
await bounded(settle(), 'delete settles', _dashnodeStepTimeoutMs);
const afterEmpty = { meta: metaEl.textContent, list: listEl.textContent,
  prevDisabled: prevBtn.disabled, nextDisabled: nextBtn.disabled };
phase('dashboard call settled');
process.stdout.write(JSON.stringify({ oneRow, afterEmpty }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=3, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'uploads.js',))


def test_an_emptied_list_reads_zero_slash_zero(_tmp):
    """A list with no rows cannot start at row 1: the header reads 0 / 0,
    the list says there are no uploads rather than no matches, and both
    pager buttons are out of the picture — a clamped-away offset that
    went negative would leave prev enabled over an impossible page."""
    result = _dashnode.run_dashboard_node(_PAGER_EMPTY_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['oneRow']['meta'] == '1–1 / 1', seen
    assert seen['oneRow']['prevDisabled'] is True, seen
    assert seen['oneRow']['nextDisabled'] is True, seen
    assert seen['afterEmpty'] == {
        'meta': '0 / 0', 'list': 'no uploads.', 'prevDisabled': True,
        'nextDisabled': True}, seen


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
// The pre-flight status stands only between the click and the await.
const statusBefore = metaEl.textContent;
await bounded(settle(), 'run in the active tab', _dashnodeStepTimeoutMs);
const activeTab = {
  puts: puts.length, tab: puts.length ? puts[puts.length - 1].tab : null,
  label: untargeted.textContent, title: untargeted.attrs.title || '',
  statusBefore, statusAfter: metaEl.textContent,
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
untargeted.click();
await bounded(settle(), 'active-tab run with a tab selected',
  _dashnodeStepTimeoutMs);
const chosen = puts[puts.length - 1] || {};
const activeTabChosen = {
  puts: puts.length, tab: chosen.tab, token: chosen.token, code: chosen.code,
  id: typeof chosen.id === 'string' && chosen.id.length > 0,
  keys: Object.keys(chosen).sort(),
};
phase('dashboard call settled');
process.stdout.write(JSON.stringify(
  { refused, activeTab, targeted, activeTabChosen }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=7, module=True, arguments=(
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


def test_an_active_tab_run_sends_no_tab_even_with_a_tab_selected(_tmp):
    """Selecting tab 11 changes nothing for the explicit active-tab
    button: its command still names no tab and carries every field the
    section always sends, while plain RUN driven with the same selection
    sends '11' — the control that holds the selected half of the
    targeting conditional. The repro mutation that drops the
    conditional (`const tabId = sel.value`) turns the empty-target
    assertion below into tab '11' and fails here."""
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['targeted'] == {'puts': 2, 'tab': '11'}, seen
    assert seen['activeTabChosen'] == {
        'puts': 3, 'tab': '', 'token': 'dashboard-token',
        'code': 'document.title', 'id': True,
        'keys': ['code', 'id', 'tab', 'token']}, seen


def test_eval_status_names_the_active_tab_on_both_untargeted_surfaces(_tmp):
    """The two status surfaces of an untargeted run both name the active
    tab: the pre-flight line the section paints the moment the run
    starts, and the post-run line the settled envelope repaints. The two
    fallbacks are separate strings in the section, so each is asserted,
    and either regressing to the old 'broadcast' reading fails its own
    assertion."""
    result = _dashnode.run_dashboard_node(_EVAL_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['activeTab']['statusBefore'] == (
        'tab=active tab  timeout=10000ms'), seen
    assert re.fullmatch(r'tab=active tab  channel=page  \d+ms',
                        seen['activeTab']['statusAfter']), seen


_SETTINGS_HARNESS = _dashnode.DashboardNodeHarness(_DOM + r"""
(async () => {
globalThis.window = { addEventListener() {} };
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container);
const caveat = container.all().find(
  (el) => el.tag === 'p' && el.textContent.startsWith('Caveat:'));
if (!caveat) throw new Error('settings caveat paragraph not found');
phase('dashboard call settled');
process.stdout.write(JSON.stringify({ caveat: caveat.textContent }));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=1, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / 'settings.js',))


def test_settings_caveat_says_where_an_untargeted_command_runs(_tmp):
    """The settings caveat must keep saying an untargeted command runs
    once, in whichever tab is active — the sentence the issue's
    mutation replaced with an all-tabs claim, which would tell an
    operator that one command fans out over every tab. The sentence is
    asserted whole, and both all-tabs spellings are refused beside it."""
    result = _dashnode.run_dashboard_node(_SETTINGS_HARNESS)
    seen = json.loads(result.stdout)
    assert ('A command that names no tab (exec -b, or "run in active tab"'
            ' above) runs once, in whichever tab is active — this one, if'
            ' the dashboard is in front.') in seen['caveat'], seen
    lowered = seen['caveat'].lower()
    assert 'all tabs' not in lowered, seen
    assert 'every tab' not in lowered, seen


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashsections_')


if __name__ == '__main__':
    raise SystemExit(main())
