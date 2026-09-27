"""The uploads pager harnesses: the clamp case and the emptied-list case.

Not a suite itself -- `run_tests.py` only loads `test_*.py`.

Two harnesses, one question each, both driven by the same parked confirm
timer. They moved out of `tests/test_dashboard_sections.py`, which naming
every target exactly pushed past the 700-line ceiling. Every target each
fake models is one the scenario can name -- a listing page, or `/upload`
with a DELETE -- and anything else is recorded on `unplanned` and
answered 599.
"""
import _dashfetch
import _dashnode
from _repo import ROOT


_DOM = _dashnode.DOM


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

_PAGER_CLAMP_HARNESS = _dashnode.DashboardNodeHarness(
    _PAGER_PREFIX + _dashfetch.DOOR + r"""
(async () => {
const fetched = [];
const metaSnapshots = [];
let total = 51;
// The two listing pages the pager can reach, and the one delete this
// scenario clicks. A third page is not a page the module can ask for
// here, so a request for one is a request nobody planned.
const LISTINGS = ['/upload?limit=50&offset=0', '/upload?limit=50&offset=50'];
const file = (n) => ({ id: 'up' + n, filename: 'f' + n + '.txt', size: 1,
  mtime: 1, path: token + '/up' + n + '/f' + n + '.txt' });
globalThis.fetch = async (target, init) => {
  const where = String(target);
  if (LISTINGS.includes(where)) {
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
  if (where === '/upload' && init && init.method === 'DELETE') {
    total = 50;
    return jsonResponse({});
  }
  return refuse(where);
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
  unplanned: UNPLANNED,
}));
phase('dashboard harness finished');
})().catch(leave);
    """, bounded_steps=4, module=True, arguments=(
        ROOT / 'dashboard' / 'sections' / 'uploads.js',))


_PAGER_EMPTY_HARNESS = _dashnode.DashboardNodeHarness(
    _PAGER_PREFIX + _dashfetch.DOOR + r"""
(async () => {
const fetched = [];
let total = 1;
const LISTING = '/upload?limit=50&offset=0';
globalThis.fetch = async (target, init) => {
  const where = String(target);
  if (where === LISTING) {
    fetched.push(where);
    return jsonResponse({ total,
      items: total ? [{ id: 'up1', filename: 'a.txt', size: 1, mtime: 1,
        path: token + '/up1/a.txt' }] : [] });
  }
  if (where === '/upload' && init && init.method === 'DELETE') {
    total = 0;
    return jsonResponse({});
  }
  return refuse(where);
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
process.stdout.write(JSON.stringify({ oneRow, afterEmpty,
  unplanned: UNPLANNED }));
phase('dashboard harness finished');
})().catch(leave);
    """, bounded_steps=3, module=True, arguments=(
        ROOT / 'dashboard' / 'sections' / 'uploads.js',))
