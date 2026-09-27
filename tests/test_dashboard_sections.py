#!/usr/bin/env python3
"""What the uploads browser puts on the wire, run rather than read.

The uploads browser is the section whose mistakes leave the page: a
link or a request target that carries the token into browser history
and the proxy access log, an object URL that is never revoked, or a
stale cache entry that outlives the listing it came from. Each harness
mounts dashboard/sections/uploads.js into a small DOM in Node, drives
its own buttons, and judges the fetches it makes, the hrefs it renders
and the object URLs it lets go of. The capture harnesses mount their own
sections the same way.
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashfetch  # noqa: E402
import _dashnode  # noqa: E402
import _dashpager  # noqa: E402
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_DOM = _dashnode.DOM

_UPLOADS_HARNESS = _dashnode.DashboardNodeHarness(
    _DOM + _dashfetch.DOOR + r"""
(async () => {
const fetched = [];
const listing = { total: 2, items: [
  { id: 'up1', filename: 'a&b#c.txt', size: 3, mtime: 1,
    path: token + '/up1/a&b#c.txt' },
  { id: 'up1', filename: 'shot.png', size: 4, mtime: 2,
    path: token + '/up1/shot.png' },
] };
// The file selectors are the listed rows' own id and filename, built
// the way the module builds them.
const LISTING = '/upload?limit=50&offset=0';
const FILES = new Set(listing.items.map(
  (f) => '/upload?path=' + encodeURIComponent(f.id + '/' + f.filename)));
globalThis.fetch = async (target, init) => {
  const headers = (init && init.headers) || {};
  const where = String(target);
  fetched.push({ target: where, auth: headers.Authorization || '' });
  if (where === LISTING) return jsonResponse(listing);
  if (FILES.has(where)) {
    return {
      ok: true, status: 200,
      headers: { get: () => 'application/octet-stream' },
      blob: async () => ({ from: where }), json: async () => ({}),
    };
  }
  return refuse(where);
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
const previewAnchor = container.all().find(
  (el) => el.tag === 'a' && el.attrs.target === '_blank');
const previewImage = previewAnchor &&
  previewAnchor.all().find((el) => el.tag === 'img');
const previewNode = {
  anchorHref: previewAnchor ? previewAnchor.attrs.href || null : null,
  imageSrc: previewImage ? previewImage.attrs.src || null : null,
};
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  rendered, fetched, downloadFetches, previewFetches, previewNode, clicks,
  unplanned: UNPLANNED,
}));
phase('dashboard harness finished');
})().catch(leave);
    """, bounded_steps=4, module=True, arguments=(
        ROOT / 'dashboard' / 'sections' / 'uploads.js',))


def test_uploads_carry_the_token_in_a_header_and_never_in_a_link(_tmp):
    """Every file reaches the browser through the header-authenticated
    object-URL path, so no href on the page names the token or a route
    the bridge does not have, no request target names the token either,
    and an operator-named filename survives the trip percent-encoded.

    The preview anchor and its image render only once the preview fetch
    has settled, so the harness reads them at that point and requires
    each of them to carry that object URL rather than a web URL."""
    result = _dashnode.run_dashboard_node(_UPLOADS_HARNESS)
    seen = json.loads(result.stdout)
    # Every faked route below is an EXACT target the scenario names --
    # a listing page, a listed row's own selector, a minted capture path
    # -- never a prefix, so an empty record is a claim that the module
    # asked for nothing outside them.
    assert seen['unplanned'] == [], seen
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

    preview = seen['previewNode']
    assert preview['anchorHref'] == 'blob:held-2', preview
    assert preview['imageSrc'] == 'blob:held-2', preview


# A file fetch settles only when the test says so, so a stale fetch can be
# rejected after a re-render has already replaced its cache entry.
_LIFECYCLE_HARNESS = _dashnode.DashboardNodeHarness(
    _DOM + _dashfetch.DOOR + r"""
(async () => {
const fetched = [];
const deferred = [];
const revoked = [];
const listing = { total: 1, items: [
  { id: 'up1', filename: 'a.txt', size: 1, mtime: 1,
    path: token + '/up1/a.txt' },
] };
const LISTING = '/upload?limit=50&offset=0';
const FILE = '/upload?path=' + encodeURIComponent('up1/a.txt');
globalThis.fetch = (target, init) => {
  const headers = (init && init.headers) || {};
  const where = String(target);
  fetched.push({ target: where, auth: headers.Authorization || '' });
  if (where === LISTING) return Promise.resolve(jsonResponse(listing));
  if (where !== FILE) return Promise.resolve(refuse(where));
  return new Promise((resolve, reject) => {
    deferred.push({ target: where, resolve, reject });
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
  unplanned: UNPLANNED,
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
    assert seen['unplanned'] == [], seen
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
_REFRESH_HARNESS = _dashnode.DashboardNodeHarness(
    _DOM + _dashfetch.DOOR + r"""
(async () => {
const revoked = [];
const listing = { total: 1, items: [
  { id: 'up1', filename: 'a.txt', size: 1, mtime: 1,
    path: token + '/up1/a.txt' },
] };
let listingFails = false;
const LISTING = '/upload?limit=50&offset=0';
const FILE = '/upload?path=' + encodeURIComponent('up1/a.txt');
globalThis.fetch = async (target) => {
  const where = String(target);
  if (where === LISTING) {
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
  if (where === FILE) {
    return {
      ok: true, status: 200,
      headers: { get: () => 'application/octet-stream' },
      blob: async () => ({}), json: async () => ({}),
    };
  }
  return refuse(where);
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
process.stdout.write(JSON.stringify({ afterTokenless, revoked, clicks,
  unplanned: UNPLANNED }));
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
    assert seen['unplanned'] == [], seen
    saved = [click['href'] for click in seen['clicks']
             if click.get('download')]
    assert saved == ['blob:held-1', 'blob:held-2'], seen['clicks']
    assert seen['afterTokenless'] == ['blob:held-1'], seen
    assert seen['revoked'] == ['blob:held-1', 'blob:held-2'], seen


# The two pager cases read their harnesses from `tests/_dashpager.py`.
def test_a_delete_that_shrinks_total_clamps_the_pager_to_the_last_page(_tmp):
    """The issue's reproduction: the last page shows 51–51 / 51, the
    last-page file is deleted, and the pager must land on the last valid
    page of the shrunken total instead of an impossible range with rows
    from a page the listing no longer has. The mid-list page is pinned
    with its exact range end, and the over-range frame must not exist
    even transiently between the listing round-trips."""
    result = _dashnode.run_dashboard_node(_dashpager._PAGER_CLAMP_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['unplanned'] == [], seen
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


def test_an_emptied_list_reads_zero_slash_zero(_tmp):
    """A list with no rows cannot start at row 1: the header reads 0 / 0,
    the list says there are no uploads rather than no matches, and both
    pager buttons are out of the picture — a clamped-away offset that
    went negative would leave prev enabled over an impossible page."""
    result = _dashnode.run_dashboard_node(_dashpager._PAGER_EMPTY_HARNESS)
    seen = json.loads(result.stdout)
    assert seen['unplanned'] == [], seen
    assert seen['oneRow']['meta'] == '1–1 / 1', seen
    assert seen['oneRow']['prevDisabled'] is True, seen
    assert seen['oneRow']['nextDisabled'] is True, seen
    assert seen['afterEmpty'] == {
        'meta': '0 / 0', 'list': 'no uploads.', 'prevDisabled': True,
        'nextDisabled': True}, seen


_CAPTURE_HARNESS = _DOM + _dashfetch.DOOR + r"""
(async () => {
const commands = [];
const uploads = [];
// The section's pager is never clicked, so one listing page is the
// only listing it can ask for.
const LISTING = '/upload?limit=200&offset=0';
const imageSelector = (path) => '/screenshot?path=' + encodeURIComponent(path);
const PEEK = '/result?tab=extension';
let envelope;
// Every capture this fake minted, because the section fetches the
// whole recent grid rather than only the newest.
const mintedPaths = new Set();
const imageTargets = [];
let consumeTarget = '';
globalThis.fetch = async (target, init = {}) => {
  const method = init.method || 'GET';
  if (target === '/tabs' && method === 'GET') {
    return jsonResponse([
      { tabId: '11', title: 'first', url: '', age: 0 },
      { tabId: '22', title: 'second', url: '', age: 0 },
    ]);
  }
  if (target === LISTING && method === 'GET') {
    return jsonResponse({ items: uploads.slice(), total: uploads.length,
      limit: 200, offset: 0 });
  }
  if (target === '/command' && method === 'PUT') {
    const command = JSON.parse(init.body);
    commands.push(command);
    envelope = {
      id: command.id, deliveryId: 'delivery-' + commands.length,
      resultGeneration: 'generation-' + commands.length,
      result: { path: token + '/' + command.id + '/capture-'
        + commands.length + '.png', size: 3, format: 'png', tabUrl: '' },
      error: null, world: 'extension',
    };
    consumeTarget = PEEK + '&consume=1&expected='
      + encodeURIComponent(envelope.resultGeneration);
    mintedPaths.add(imageSelector(envelope.result.path));
    uploads.push({ id: command.id,
      filename: 'capture-' + commands.length + '.png', size: 3,
      mtime: commands.length, path: envelope.result.path });
    return jsonResponse({ ok: true, did: envelope.deliveryId });
  }
  if ((target === PEEK || target === consumeTarget) && method === 'GET') {
    return jsonResponse({ ...envelope, consumed: true });
  }
  if (mintedPaths.has(target) && method === 'GET') {
    imageTargets.push(target);
    return { ok: true, blob: async () => ({}) };
  }
  return refuse(target);
};
URL.createObjectURL = () => 'blob:capture';
URL.revokeObjectURL = () => {};
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
phase('dashboard call started');
const bus = { on() {} };
let container = new El('div');
mount(container, bus);
await bounded(settle(), 'initial mount', _dashnodeStepTimeoutMs);
function capture(tabId) {
  const panelButton = container.find('[data-role=capture]');
  if (panelButton) {
    container.find('[data-role=tab]').value = tabId;
    container.find('[data-role=fmt]').value = 'png';
    panelButton.click();
  } else {
    container.all().find((el) => el.dataset.tid === tabId)
      .byText('shot').click();
  }
}
capture('11');
await bounded(settle(), 'first capture', _dashnodeStepTimeoutMs);
capture('11');
await bounded(settle(), 'repeat capture', _dashnodeStepTimeoutMs);
capture('22');
await bounded(settle(), 'different tab capture', _dashnodeStepTimeoutMs);
container = new El('div');
mount(container, bus);
await bounded(settle(), 'remount', _dashnodeStepTimeoutMs);
capture('11');
await bounded(settle(), 'remounted capture', _dashnodeStepTimeoutMs);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({ commands,
  imageTargets, unplanned: UNPLANNED }));
phase('dashboard harness finished');
})().catch(leave);
"""


def _repeated_captures(section):
    harness = _dashnode.DashboardNodeHarness(
        _CAPTURE_HARNESS, bounded_steps=7, module=True, arguments=(
            ROOT / 'dashboard' / 'sections' / (section + '.js'),))
    seen = json.loads(_dashnode.run_dashboard_node(harness).stdout)
    assert seen['unplanned'] == [], seen
    commands = seen['commands']
    assert [cmd['tabId'] for cmd in commands] == [11, 11, 22, 11], seen
    assert all(cmd['type'] == 'screenshot' for cmd in commands), seen
    assert all(cmd['tab'] == 'extension' for cmd in commands), seen
    ids = [cmd['id'] for cmd in commands]
    assert all(ids) and len(set(ids)) == 1, ids
    return seen


def test_screenshot_panel_reuses_one_upload_id(_tmp):
    seen = _repeated_captures('screenshot')
    assert all(cmd['format'] == 'png' for cmd in seen['commands']), seen
    for index in range(1, 5):
        assert any(target.endswith(f'%2Fcapture-{index}.png')
                   for target in seen['imageTargets']), seen


def test_tab_row_captures_reuse_one_upload_id(_tmp):
    _repeated_captures('tabs')


_RECENT_HARNESS = _DOM + _dashfetch.DOOR + r"""
import { readFileSync } from 'node:fs';
(async () => {
const fixture = JSON.parse(readFileSync(process.argv[2], 'utf8'));
// The thumbnail selectors the listing itself names, handed over by the
// side that produced the listing.
const previews = new Set(fixture.previews);
globalThis.fetch = async (target) => {
  if (target === '/tabs') return jsonResponse([]);
  if (target in fixture.pages) return jsonResponse(fixture.pages[target]);
  if (previews.has(target)) {
    const path = new URLSearchParams(target.split('?')[1]).get('path');
    return { ok: true, blob: async () => ({ path }) };
  }
  return refuse(target);
};
URL.createObjectURL = (blob) => 'blob:' + blob.path;
URL.revokeObjectURL = () => {};
phase('dashboard module import started');
const { mount } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs);
phase('dashboard module imported');
phase('dashboard call started');
const container = new El('div');
mount(container, { on() {} });
await bounded(settle(), 'recent listing', _dashnodeStepTimeoutMs);
const paths = container.find('[data-role=recent]').all()
  .filter(el => el.tag === 'img').map(el => el.attrs.src.slice(5));
phase('dashboard call settled');
process.stdout.write(JSON.stringify({ paths, unplanned: UNPLANNED }));
phase('dashboard harness finished');
})().catch(leave);
"""


def _recent_captures(tmp, count, ids):
    routes = _util.load(ROOT / 'daedalus_bridge' / 'upload_routes.py',
                        'recent_upload_routes')
    upload_dir = Path(tmp) / 'uploads'
    captures = []
    for index in range(count + 3):
        stamp = 1_700_000_000_000 + index * 250
        suffix = 'png' if index < count else 'txt'
        path = (upload_dir / 'dashboard-token' / ids[index % len(ids)]
                / f'{stamp}.{suffix}')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'capture')
        os.utime(path, (stamp / 1000, stamp / 1000))
        os.utime(path.parent, (stamp / 1000, stamp / 1000))
        if index < count:
            # The listing row's own shape: relative to the token directory.
            captures.append(
                path.relative_to(upload_dir / 'dashboard-token').as_posix())
    pages = {}
    previews = set()
    for offset in range(0, count + 3, 200):
        status, page = routes.list_uploads(upload_dir, 'dashboard-token', {
            'limit': ['200'], 'offset': [str(offset)]})
        assert status == 200, page
        pages[f'/upload?limit=200&offset={offset}'] = page
        for item in page['items']:
            if item['filename'].lower().endswith(('.png', '.jpg', '.jpeg')):
                previews.add('/screenshot?path='
                             + quote(item['path'], safe=''))
    fixture = Path(tmp) / 'listing.json'
    fixture.write_text(
        json.dumps({'pages': pages, 'previews': sorted(previews)}),
        encoding='utf-8')
    harness = _dashnode.DashboardNodeHarness(
        _RECENT_HARNESS, bounded_steps=2, module=True, arguments=(
            ROOT / 'dashboard' / 'sections' / 'screenshot.js', fixture))
    report = json.loads(_dashnode.run_dashboard_node(harness).stdout)
    assert report['unplanned'] == [], report
    expected = list(reversed(captures[-24:]))
    assert report['paths'] == expected, report


def test_recent_captures_show_newest_24_within_one_id(tmp):
    _recent_captures(tmp, 25, ['_ss'])


def test_recent_captures_find_newest_beyond_the_first_page(tmp):
    _recent_captures(tmp, 225, ['_ss'])


def test_recent_captures_merge_surface_ids_across_all_pages(tmp):
    _recent_captures(tmp, 450, ['_ss', '_screenshot'])


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashsections_')


if __name__ == '__main__':
    raise SystemExit(main())
