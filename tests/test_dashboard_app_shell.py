#!/usr/bin/env python3
"""What the dashboard's entry point does, each test pinned by the
production change that turns it red."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _dashshell import run_scenario  # noqa: E402
from _repo import ROOT  # noqa: E402

_SERVER = 'https://example.com'
_TOKEN = 'tok-abcdefghijklmnop'
_SHORT = 'short-tokenx'
# One past the boundary `maskToken` draws, so `<= 12` written `<= 13`
# cannot pass: twelve characters is the valid member, thirteen is the
# member that has to be masked.
_OVER = 'short-tokenxy'
_ELLIPSIS = '…'
_DASH = '—'
# Truthy on an object literal, so it takes the mount path; not
# callable, so the call throws in the try. Chosen against the CURRENT
# lookup: a null-prototype table or an `Object.hasOwn` guard would send
# it down the no-module path, and the test would fail on the tag.
_THROWS = '__proto__'

_APP = ('app.js', 'sections/_util.js')
_APP_SSE = ('app.js', 'sections/_util.js', 'sse.js')

# Every scenario opens the same way: `h`, the storage, one stream.
_OPEN = r"""
(async () => {
const util = await bounded(load('sections/_util.js'), 'util import',
  _dashnodeStepTimeoutMs);
const h = util.h;
"""

_IMPORT = r"""
document.readyState = 'loading';
await bounded(load('app.js'), 'app import', _dashnodeStepTimeoutMs);
await bounded(settle(), 'import with boot deferred', _dashnodeStepTimeoutMs);
"""

# The same import, keeping the namespace for `bus`.
_IMPORT_BUS = r"""
document.readyState = 'loading';
const app = await bounded(load('app.js'), 'app import',
  _dashnodeStepTimeoutMs);
await bounded(settle(), 'import with boot deferred', _dashnodeStepTimeoutMs);
"""

_IMPORT_NOW = r"""
await bounded(load('app.js'), 'app import', _dashnodeStepTimeoutMs);
await bounded(settle(), 'import booted immediately', _dashnodeStepTimeoutMs);
"""

# A module script is deferred, so `index.html:124` presents this, not
# `complete`.
_IMPORT_INTERACTIVE = r"""
document.readyState = 'interactive';
await bounded(load('app.js'), 'app import', _dashnodeStepTimeoutMs);
await bounded(settle(), 'import booted while interactive',
  _dashnodeStepTimeoutMs);
"""

_FIRE = r"""
document.fire('DOMContentLoaded');
await bounded(settle(), 'boot', _dashnodeStepTimeoutMs);
"""

_CLOSE = """
})().catch(leave);
"""

# The meta bar as `dashboard/index.html` spells it, every cell seeded.
_BAR = r"""
const bar = h('div', { class: 'meta-bar' }, [
  h('span', { class: 'meta-v', 'data-meta': 'server' }, 'SEED'),
  h('span', { class: 'meta-v', 'data-meta': 'token' }, 'SEED'),
  h('span', { class: 'sse-dot', 'data-meta': 'sse-dot',
    'data-status': 'seeded' }, 'seeded'),
  h('span', { class: 'meta-v', 'data-meta': 'sse-text' }, 'seeded'),
  h('span', { class: 'meta-v', 'data-meta': 'sse-text2' }, 'seeded'),
]);
const status = h('div', { class: 'sl' }, [
  h('span', { class: 'sl-meta', 'data-meta': 'token-short' }, 'SEED'),
  h('span', { class: 'sl-meta', 'data-meta': 'last-event' }, 'SEED'),
]);
document.body.appendChild(bar);
document.body.appendChild(status);
"""

# The rail the shell highlights, and the sections its links name.
_RAIL = r"""
const rail = h('ol', { class: 'rail-list' }, [
  h('li', {}, h('a', { href: '#s00' }, 'ONE')),
  h('li', {}, h('a', { href: '#s01' }, 'TWO')),
  h('li', {}, h('a', { href: '#s02' }, 'THREE')),
]);
document.body.appendChild(rail);
for (const id of ['s00', 's01', 's02']) {
  document.body.appendChild(h('section', { id }));
}
"""

_READ_CELL = (
    "const cell = (selector) => "
    "document.querySelector(selector).textContent;\n"
)

# `relTime`'s bands at each boundary and one step inside it.
_BANDS = [
    (0, 'now'), (1999, 'now'), (2000, '2s ago'), (59999, '59s ago'),
    (60000, '1m ago'), (3599999, '59m ago'), (3600000, '1h ago'),
]


def _storage(token=_TOKEN, server=_SERVER):
    return (
        "localStorage.setItem('daedalus-token', '" + token + "');\n"
        "localStorage.setItem('daedalus-server', '" + server + "');\n"
        "drive.route('" + server + "/stream?tab=dashboard',\n"
        "  { stream: true });\n"
    )


def _run(body, storage=None, modules=_APP):
    """Opener, storage, body, closer -- then run it."""
    scenario = (_OPEN + (_storage() if storage is None else storage)
                + body + _CLOSE)
    return run_scenario(scenario, modules=modules)


def _bar(body, storage=None, modules=_APP):
    return _run(_BAR + body, storage, modules)


def _masked(token):
    """`maskToken`: verbatim at twelve or fewer, masked above that."""
    if len(token) <= 12:
        return token
    return token[:8] + _ELLIPSIS + token[-4:]


def test_a_loading_document_defers_the_boot_to_domcontentloaded(_tmp):
    """`app.js`'s `readyState` check inverted: the cells are masked
    before the event."""
    report = _bar(_READ_CELL + _IMPORT
                  + "const before = cell('[data-meta=\"token\"]');\n"
                  + _FIRE
                  + "const after = cell('[data-meta=\"token\"]');\n"
                  + "report({ before, after });\n")
    assert report['before'] == 'SEED', report
    assert report['after'] == _masked(_TOKEN), report


def test_a_ready_document_boots_without_waiting_for_the_event(_tmp):
    """`app.js` always deferring its boot: the seeded cells survive."""
    report = _bar(_READ_CELL + _IMPORT_NOW + r"""
report({ readyState: document.readyState,
  after: cell('[data-meta="token"]'),
  short: cell('[data-meta="token-short"]') });
""")
    assert report['readyState'] == 'complete', report
    assert report['after'] == _masked(_TOKEN), report
    assert report['short'] == _TOKEN[:8] + _ELLIPSIS, report


def test_an_interactive_document_boots_before_the_event_too(_tmp):
    """A document `interactive` boots on the import, as `complete` does.

    `app.js`'s `readyState` check reads one value, not one direction:
    gating on
    `complete` alone leaves the shipped page unbooted, green here."""
    report = _bar(_READ_CELL + _IMPORT_INTERACTIVE + r"""
report({ readyState: document.readyState,
  after: cell('[data-meta="token"]'),
  short: cell('[data-meta="token-short"]') });
""")
    assert report['readyState'] == 'interactive', report
    assert report['after'] == _masked(_TOKEN), report
    assert report['short'] == _TOKEN[:8] + _ELLIPSIS, report


def test_each_section_is_mounted_with_its_own_element_and_the_bus(_tmp):
    """`mountSections` passing no bus, or one element to every mount."""
    report = _run(r"""
const a = h('div', { class: 'panel-b', 'data-section': 'overview' }, 'OWN-A');
const b = h('div', { class: 'panel-b', 'data-section': 'settings' }, 'OWN-B');
document.body.appendChild(b);
document.body.appendChild(a);
""" + _IMPORT + _FIRE + r"""
const seen = (el, own) => {
  const err = el.querySelector('pre.pane.err');
  return { seeded: el.textContent.includes(own),
    overview: el.textContent.includes('LIVE EVENT STREAM'),
    settings: el.textContent.includes('bridge token'),
    failed: err === null ? null : err.textContent };
};
report({ a: seen(a, 'OWN-A'), b: seen(b, 'OWN-B') });
""")
    # `seeded` certifies the fixture; "its own element" is the
    # cross-assertions -- a mount landing in the other container puts
    # its marker there.
    assert report['a']['seeded'] is True, report
    assert report['b']['seeded'] is True, report
    assert report['a']['failed'] is None, report
    assert report['b']['failed'] is None, report
    assert report['a']['overview'] is True, report
    assert report['b']['overview'] is False, report
    assert report['b']['settings'] is True, report
    assert report['a']['settings'] is False, report


def test_a_section_with_no_module_renders_the_message_after_a_clear(_tmp):
    """`mountSections`'s no-module arm dropping the message, or `clear`."""
    report = _run(r"""
const el = h('div', { class: 'panel-b', 'data-section': 'nonesuch' },
  'SEED-CHILD');
document.body.appendChild(el);
""" + _IMPORT + _FIRE + r"""
const child = el.firstChild;
report({ children: el.children.length,
  tag: child === null ? null : child.tag,
  className: child === null ? null : child.className,
  text: el.textContent });
""")
    assert report['children'] == 1, report
    assert report['tag'] == 'div', report
    assert report['className'] == 'dim italic', report
    assert report['text'] == '(no module for "nonesuch")', report


def test_a_section_that_throws_is_reported_and_the_next_still_mounts(_tmp):
    """`mountSections`'s catch swallowing the throw, or the loop
    breaking."""
    head = r"""
const bad = h('div', { class: 'panel-b', 'data-section': '%s' },
  'SEED-CHILD');
const good = h('div', { class: 'panel-b', 'data-section': 'settings' },
  'OWN');
document.body.appendChild(bad);
document.body.appendChild(good);
""" % _THROWS
    report = _run(head + _IMPORT + _FIRE + r"""
const pane = bad.querySelector('pre.pane.err');
report({ tag: pane === null ? null : pane.tag,
  className: pane === null ? null : pane.className,
  text: bad.textContent,
  after: good.textContent.includes('bridge token') });
""")
    assert report['tag'] is not None, report
    assert report['className'] == 'pane err', report
    assert report['text'].startswith('mount failed: '), report
    assert len(report['text']) > len('mount failed: '), report
    assert 'SEED-CHILD' not in report['text'], report
    assert report['after'] is True, report


def test_the_meta_bar_masks_the_token_once_and_the_short_token_apart(_tmp):
    """`wireMetaBar` swapping the selectors, `maskToken` dropping the
    mask."""
    long_token = _bar(_READ_CELL + _IMPORT_NOW + r"""
report({ token: cell('[data-meta="token"]'),
  short: cell('[data-meta="token-short"]'),
  server: cell('[data-meta="server"]') });
""")
    assert long_token['token'] == _masked(_TOKEN), long_token
    assert long_token['short'] == _TOKEN[:8] + _ELLIPSIS, long_token
    assert long_token['server'] == _SERVER, long_token
    short = _bar(_READ_CELL + _IMPORT_NOW + r"""
report({ token: cell('[data-meta="token"]'),
  short: cell('[data-meta="token-short"]') });
""", _storage(_SHORT))
    assert short['token'] == _SHORT, short
    assert short['short'] == _SHORT[:8] + _ELLIPSIS, short
    absent = _bar(_READ_CELL + _IMPORT_NOW + r"""
report({ token: cell('[data-meta="token"]'),
  short: cell('[data-meta="token-short"]'),
  server: cell('[data-meta="server"]') });
""", _storage('', ''))
    assert absent['token'] == '(none)', absent
    assert absent['short'] == _DASH, absent
    assert absent['server'] == '(same origin)', absent


# The settings panel mounted, its token field saved unchanged, and the
# three bar cells read either side of the save.
_SAVE_PANEL = _BAR + r"""
const panel = h('div', { class: 'panel-b', 'data-section': 'settings' });
document.body.appendChild(panel);
drive.route('https://example.com/tabs', { json: [] });
""" + _READ_CELL + _IMPORT + _FIRE + r"""
const read = () => ({
  token: cell('[data-meta="token"]'),
  short: cell('[data-meta="token-short"]'),
  server: cell('[data-meta="server"]'),
});
const before = read();
panel.querySelector('[data-role=save]').click();
await bounded(settle(), 'the save and its server probe',
  _dashnodeStepTimeoutMs);
report({ before, after: read() });
"""


def test_a_saved_token_reads_the_same_in_the_bar_as_it_did_at_boot(_tmp):
    """`settings.js`'s own mask, which had no length guard: a token of
    twelve or fewer reads verbatim when the bar is wired and masked
    behind an ellipsis after a save. The panel delegates to `app.js`'s
    mask, so a mutation in that mask turns this red; a copy kept in
    `settings.js` would not. The short cell and the server label are
    read beside it because the same three writes are one delegation."""
    for token in (_SHORT, _OVER):
        report = _run(_SAVE_PANEL, storage=_storage(token))
        assert report['before'] == {'token': _masked(token),
                                    'short': token[:8] + _ELLIPSIS,
                                    'server': _SERVER}, (token, report)
        # The restart is the liveness of the assertion: two stream
        # requests and the server probe is what a save actually did, so a
        # handler that never ran cannot leave the two readings equal.
        assert [r['target'] for r in report['requests']] == [
            _SERVER + '/stream?tab=dashboard',
            _SERVER + '/stream?tab=dashboard',
            _SERVER + '/tabs'], (token, report['requests'])
        assert report['after'] == report['before'], (token, report)
        assert report['unplanned'] == [], (token, report)


def test_the_settings_panel_does_not_import_the_entry_point(_tmp):
    """`sections/settings.js` reaching `app.js` closes a cycle: the entry
    point imports the panel, so the panel importing the entry point makes
    which body runs first depend on who was loaded first. It did, once,
    for the meta-bar writer, and the writer now lives beside the panel in
    `sections/_util.js`.

    The specifiers are read rather than the text searched, so the whole
    list is what a failure prints and a mention in a comment cannot
    satisfy or break it."""
    source = (ROOT / 'dashboard' / 'sections' / 'settings.js').read_text(
        encoding='utf-8')
    specifiers = re.findall(r"^import\s[^;]*?from\s+'([^']+)';", source, re.M)
    assert '../app.js' not in specifiers, specifiers
    assert './_util.js' in specifiers, specifiers


def test_an_internal_sse_status_reaches_the_dot_and_both_status_texts(_tmp):
    """`wireStatusLine`'s status subscriber dropping the guard, the
    dot or `txt2`."""
    report = _bar(_READ_CELL + _IMPORT_BUS + r"""
const sse = await bounded(load('sse.js'), 'sse import',
  _dashnodeStepTimeoutMs);
const delivered = [];
app.bus.on((ev) => delivered.push(ev.type + '/' + (ev.__internal === true)));
""" + _FIRE + r"""
const read = () => ({
  status: bar.querySelector('[data-meta="sse-dot"]').dataset.status,
  one: cell('[data-meta="sse-text"]'),
  two: cell('[data-meta="sse-text2"]'),
});
const frame = (payload) => drive.lastScript().push('event: command\ndata: '
  + JSON.stringify(payload) + '\n\n');
const connected = read();
frame({ kind: 'event', id: 'e1', type: 'sse-status', status: 'rogue' });
await bounded(settle(), 'rogue status frame', _dashnodeStepTimeoutMs);
const rogue = read();
const rogueDelivered = delivered.slice();
frame({ kind: 'event', id: 'e2', type: 'tab-updated', tabId: 7 });
await bounded(settle(), 'page event frame', _dashnodeStepTimeoutMs);
const page = read();
const pageDelivered = delivered.slice();
sse.stop();
report({ connected, rogue, page, idle: read(),
  rogueDelivered, pageDelivered });
""", modules=_APP_SSE)
    assert report['connected'] == {'status': 'connected', 'one': 'connected',
                                   'two': 'connected'}, report
    # An absence is worth nothing unless the frame arrived, so the
    # bus says so first: the anchors above travel another channel.
    assert 'sse-status/false' in report['rogueDelivered'], report
    assert 'tab-updated/false' in report['pageDelivered'], report
    assert report['rogue'] == report['connected'], report
    assert report['page'] == report['connected'], report
    assert report['idle'] == {'status': 'idle', 'one': 'idle',
                              'two': 'idle'}, report


def test_the_last_event_clock_writes_nothing_until_its_interval_runs(_tmp):
    """`wireStatusLine`'s interval called at wire time, or dropped."""
    report = _bar(_READ_CELL + _IMPORT + _FIRE + r"""
const live = drive.live();
const before = cell('[data-meta="last-event"]');
if (live.length !== 1) { return report({ live, before }); }
drive.fire(live[0].id);
report({ live, before, after: cell('[data-meta="last-event"]') });
""")
    assert report['before'] == 'SEED', report
    assert report['live'] == [{'id': 1, 'kind': 'interval', 'delay': 1000}], \
        report
    assert report['after'] == 'now', report


def test_rel_time_reports_each_band_from_its_nearest_miss_boundary(_tmp):
    """`relTime`'s `<` bands: one `<` becoming `<=` moves one boundary."""
    ticks = ''.join(
        f"clock = start + {off};\ndrive.fire(id);\n"
        f"""seen.push([{off}, cell('[data-meta="last-event"]')]);\n"""
        for off, _ in _BANDS)
    bands = _bar(_READ_CELL + r"""
const start = 1700000000000;
let clock = start;
Date.now = () => clock;
""" + _IMPORT + _FIRE + r"""
const id = drive.ids((slot) => slot.kind === 'interval')[0];
if (id === undefined) { return report({ id: null }); }
const seen = [];
""" + ticks + r"""
report({ id, seen, live: drive.live() });
""")
    assert bands['id'] is not None, bands
    assert bands['live'] == [{'id': 1, 'kind': 'interval', 'delay': 1000}], \
        bands
    assert bands['seen'] == [[off, text] for off, text in _BANDS], bands
    missing = _bar(_READ_CELL + _IMPORT + _FIRE + r"""
const id = drive.ids((slot) => slot.kind === 'interval')[0];
if (id === undefined) { return report({ id: null }); }
drive.fire(id);
report({ id, text: cell('[data-meta="last-event"]') });
""", _storage('', ''))
    assert missing['id'] is not None, missing
    assert missing['text'] == _DASH, missing


def test_the_observer_activates_the_intersecting_link_nearest_the_top(_tmp):
    """`wireRailHighlight`'s sort taking the last visible or dropping
    it.

    The `rootMargin` asserted below is DISPUTED, not endorsed: a -60%
    bottom margin shrinks the root to the top 40% of the viewport, so
    at the bottom of a long page the last section may stop being
    highlighted. The shell cannot settle it -- its observer double
    never reads the margin -- and it is filed as its own issue.
    Fixing it turns the assertion red: that is it reporting that it
    moved."""
    report = _run(_RAIL + _IMPORT + _FIRE + r"""
const links = Array.from(document.querySelectorAll('.rail-list a'));
const active = () => links.map((a) => a.classList.contains('active'));
const io = drive.observers()[0];
const start = active();
links[0].click();
const afterClick = active();
io.fire([
  { isIntersecting: true, boundingClientRect: { top: 200 },
    target: document.querySelector('#s00') },
  { isIntersecting: true, boundingClientRect: { top: 30 },
    target: document.querySelector('#s01') },
]);
report({ start, afterClick, nearest: active(),
  rootMargin: io.options.rootMargin, threshold: io.options.threshold,
  observed: io.observed.length });
""")
    assert report['start'] == [False, False, False], report
    assert report['afterClick'] == [True, False, False], report
    assert report['nearest'] == [False, True, False], report
    assert report['rootMargin'] == '-80px 0px -60% 0px', report
    assert report['threshold'] == 0, report
    assert report['observed'] == 3, report
    assert [click['href'] for click in report['clicks']] == ['#s00'], report


def test_the_observer_activates_nothing_when_no_entry_is_intersecting(_tmp):
    """`wireRailHighlight`'s empty-list guard removed: an empty list
    throws or clears the rail."""
    report = _run(_RAIL + _IMPORT + _FIRE + r"""
const links = Array.from(document.querySelectorAll('.rail-list a'));
const active = () => links.map((a) => a.classList.contains('active'));
const io = drive.observers()[0];
links[2].click();
const before = active();
let refusal = '';
try {
  io.fire([
    { isIntersecting: false, boundingClientRect: { top: 30 },
      target: document.querySelector('#s01') },
    { isIntersecting: false, boundingClientRect: { top: 60 },
      target: document.querySelector('#s02') },
  ]);
} catch (error) { refusal = error.message; }
report({ before, after: active(), refusal });
""")
    assert report['before'] == [False, False, True], report
    assert report['refusal'] == '', report
    assert report['after'] == report['before'], report


def test_the_bus_reaches_every_listener_and_contains_a_throwing_one(_tmp):
    """`bus.emit` reaching one listener, an escaping throw, a no-op
    unsubscribe."""
    report = _run(_IMPORT_BUS + r"""
const bus = app.bus;
const seen = [];
const offOne = bus.on((ev) => seen.push('one:' + ev.type));
const offTwo = bus.on(() => { throw new Error('listener blew up'); });
const offThree = bus.on((ev) => seen.push('three:' + ev.type));
let escaped = '';
try { bus.emit({ type: 'alpha' }); } catch (e) { escaped = e.message; }
const emitted = seen.slice();
offOne();
offTwo();
offThree();
bus.emit({ type: 'beta' });
report({ emitted, escaped, after: seen.slice(), offType: typeof offOne });
""")
    assert report['offType'] == 'function', report
    assert report['emitted'] == ['one:alpha', 'three:alpha'], report
    assert report['escaped'] == '', report
    assert report['errors'] == ['listener blew up'], report
    assert report['after'] == ['one:alpha', 'three:alpha'], report


def test_a_listener_added_during_a_dispatch_joins_it_in_app_js(_tmp):
    """A listener added during a dispatch joins that dispatch.

    `bus.emit` iterates the `Set` itself, and a `Set` iterator visits
    entries added while it runs -- so the snapshot the brief expected
    is not what the code does. This pins what it does, and why."""
    report = _run(_IMPORT_BUS + r"""
const order = [];
app.bus.on(() => {
  order.push('first');
  app.bus.on((ev) => order.push('joined:' + ev.type));
});
app.bus.emit({ type: 'mid' });
report({ order });
""")
    assert report['order'] == ['first', 'joined:mid'], report


def test_an_event_sse_dispatches_reaches_the_bus(_tmp):
    """`app.js`'s bus forwarder dropped leaves the listener nothing."""
    report = _run(_IMPORT_BUS + r"""
const sse = await bounded(load('sse.js'), 'sse import',
  _dashnodeStepTimeoutMs);
const seen = [];
app.bus.on((ev) => seen.push([ev.type, ev.__internal === true]));
sse.start();
await bounded(settle(), 'sse start', _dashnodeStepTimeoutMs);
drive.lastScript().push('event: command\ndata: '
  + JSON.stringify({ kind: 'event', id: 'x1', type: 'tab-updated', tabId: 3 })
  + '\n\n');
await bounded(settle(), 'page event frame', _dashnodeStepTimeoutMs);
report({ seen });
""", modules=_APP_SSE)
    assert report['seen'] == [['sse-status', True], ['sse-status', True],
                              ['tab-updated', False]], report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='app_shell_')


if __name__ == '__main__':
    raise SystemExit(main())
