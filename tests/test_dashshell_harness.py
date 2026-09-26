#!/usr/bin/env python3
"""What the dashboard shell harness guarantees, proven by breaking it.

The shell is a guard, so a green run on the tree it was written against
proves nothing. Each control drives the shipped modules or the shell
itself through a real Node child and reads the report it printed; the
defect each one plants and the red output it produced are recorded in
the task report for issue 493.

None of this asserts how the dashboard behaves. It asserts that a
scenario can see what it drove -- a refusal recorded rather than thrown,
a settlement recorded rather than implied, a timer that stays parked,
and a surface the shipped entry point actually reaches.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashshell  # noqa: E402
import _util  # noqa: E402
from _dashshell import run_scenario  # noqa: E402


# The origin a scenario declares, and the same-origin target the code
# under test builds when it declares none. A route-shaped match would
# serve the second from the first, which is the hole control 2 is for.
_SERVER = 'https://example.com'
_TOKEN = 'tok-abcdefghijklmnop'
_STREAM = _SERVER + '/stream?tab=dashboard'
_LOCAL_STREAM = '/stream?tab=dashboard'
_TABS = _SERVER + '/tabs'
_APP_MODULES = ('app.js', 'sections/_util.js')

_PRELUDE = """
localStorage.setItem('daedalus-token', 'tok-abcdefghijklmnop');
localStorage.setItem('daedalus-server', 'https://example.com');
"""

_IMPORT_SSE = """const sse = await bounded(load('sse.js'), 'sse import',
  _dashnodeStepTimeoutMs);"""


_UNPLANNED_REQUEST = r"""
(async () => {
""" + _PRELUDE + _IMPORT_SSE + r"""
sse.start();
await bounded(settle(), 'unplanned stream request', _dashnodeStepTimeoutMs);
report();
})().catch(leave);
"""


_ROUTE_ONLY_MATCH = r"""
(async () => {
""" + _PRELUDE + r"""
drive.route('https://example.com/stream?tab=dashboard', { stream: true });
localStorage.setItem('daedalus-server', '');
""" + _IMPORT_SSE + r"""
sse.start();
await bounded(settle(), 'same-origin request', _dashnodeStepTimeoutMs);
report({ planned: drive.planned() });
})().catch(leave);
"""


_REQUEST_LOG = r"""
(async () => {
""" + _PRELUDE + r"""
drive.route('https://example.com/stream?tab=dashboard', { stream: true });
""" + _IMPORT_SSE + r"""
sse.start();
await bounded(settle(), 'stream request', _dashnodeStepTimeoutMs);
report();
})().catch(leave);
"""


# One request of each shape the double records an argument for. A double
# that answered a constant rather than a read would be indistinguishable
# from a correct one if every request carried the same values, so this
# varies all six at once: no signal, a signal, no tab, two different tab
# values, GET and POST, and two different server prefixes.
_REQUEST_LOG_VARIED = r"""
(async () => {
const plain = 'https://example.com/tabs';
const tabbed = 'https://example.com/tabs?tab=alpha';
const elsewhere = 'https://example.com:8443/tabs?tab=beta';
for (const target of [plain, tabbed, elsewhere]) {
  drive.route(target, { json: [] });
}
await bounded(fetch(plain, { headers: { Authorization: 'Bearer one' } }),
  'request with no signal and no tab', _dashnodeStepTimeoutMs);
await bounded(fetch(tabbed, { method: 'POST',
  headers: { Authorization: 'Bearer two' } }),
  'request with a tab and a method', _dashnodeStepTimeoutMs);
await bounded(fetch(elsewhere,
  { headers: { Authorization: 'Bearer three' },
    signal: new AbortController().signal }),
  'request on another origin with a signal', _dashnodeStepTimeoutMs);
report();
})().catch(leave);
"""


_HEADERS_BAG = r"""
(async () => {
drive.route('https://example.com/tabs', { json: [] });
let refusal = null;
try {
  await bounded(fetch('https://example.com/tabs',
    { headers: new Headers({ Authorization: 'Bearer hidden' }) }),
    'headers bag the shell cannot read', _dashnodeStepTimeoutMs);
} catch (error) { refusal = error.message; }
report({ bagRefusal: refusal });
})().catch(leave);
"""


_READER = r"""
(async () => {
""" + _PRELUDE + r"""
drive.route('https://example.com/stream?tab=dashboard', { stream: true });
""" + _IMPORT_SSE + r"""
const frame = (id) => 'event: command\ndata: '
  + JSON.stringify({ kind: 'event', id, type: 'result' }) + '\n\n';
sse.start();
await bounded(settle(), 'first connection', _dashnodeStepTimeoutMs);
const first = drive.lastScript();
first.push(frame('e1'));
first.push(frame('e2'));
await bounded(settle(), 'two frames', _dashnodeStepTimeoutMs);
first.end();
await bounded(settle(), 'stream end', _dashnodeStepTimeoutMs);
sse.start();
await bounded(settle(), 'second connection', _dashnodeStepTimeoutMs);
drive.lastScript().fail('connection reset by peer');
await bounded(settle(), 'stream error', _dashnodeStepTimeoutMs);
sse.start();
await bounded(settle(), 'third connection', _dashnodeStepTimeoutMs);
sse.stop();
await bounded(settle(), 'teardown abort', _dashnodeStepTimeoutMs);
let afterClose = null;
try {
  drive.lastScript().push(frame('e3'));
} catch (error) { afterClose = error.message; }
report({ afterClose });
})().catch(leave);
"""


_WINDOW_STORAGE = r"""
(async () => {
localStorage.setItem('daedalus-token', 'first-token-abcdefgh');
drive.route('/stream?tab=dashboard', { stream: true });
""" + _IMPORT_SSE + r"""
sse.start();
await bounded(settle(), 'first connection', _dashnodeStepTimeoutMs);
localStorage.setItem('daedalus-token', 'second-token-abcdefgh');
window.fire('storage', { key: 'daedalus-token',
  newValue: 'second-token-abcdefgh' });
await bounded(settle(), 'restart after the storage event',
  _dashnodeStepTimeoutMs);
report();
})().catch(leave);
"""


_PARKED_TIMERS = r"""
(async () => {
let ticks = 0;
let carried = null;
setTimeout((arg) => { carried = arg; }, 3000, 'carried');
setInterval(() => { ticks += 1; }, 1000);
setTimeout(() => { carried = 'wrong'; }, 2500);
const before = ticks;
await bounded(pause(20), 'parked timers must not run',
  _dashnodeStepTimeoutMs);
const parked = ticks;
drive.fire(drive.ids((slot) => slot.kind === 'interval')[0]);
const once = ticks;
drive.fire(drive.ids((slot) => slot.kind === 'interval')[0]);
const twice = ticks;
const timeouts = drive.ids((slot) => slot.kind === 'timeout');
drive.fire(timeouts[0]);
clearTimeout(timeouts[1]);
report({ before, parked, once, twice, carried: String(carried),
  live: drive.live() });
})().catch(leave);
"""


_CLASS_LIST = r"""
(async () => {
const el = new El('span');
el.className = 'meta-v dim';
el.classList.toggle('active', true);
const forcedOn = { value: el.className,
  has: el.classList.contains('active') };
el.classList.toggle('active', false);
const forcedOff = { value: el.className,
  has: el.classList.contains('active') };
el.classList.toggle('armed');
report({ forcedOn, forcedOff, bare: el.className,
  length: el.classList.length });
})().catch(leave);
"""


# Two entries, in an order the caller's sort has to undo: a non-
# intersecting one first, then an intersecting one that is NOT the
# nearest to the top. A double that hands over one entry, or hands over
# an entry of its own, cannot pass this.
_OBSERVER = r"""
(async () => {
const target = new El('section');
const seen = [];
const io = new IntersectionObserver((entries) => {
  seen.push(entries.map((e) => e.target.id + ':' + e.isIntersecting
    + ':' + e.boundingClientRect.top));
}, { rootMargin: '-80px 0px -60% 0px', threshold: 0 });
io.observe(target);
io.fire([
  { isIntersecting: false, boundingClientRect: { top: 5 },
    target: { id: 'skipped' } },
  { isIntersecting: true, boundingClientRect: { top: 90 },
    target: { id: 'from-the-caller' } },
]);
let unmodelled = null;
try {
  io.unobserveAll();
} catch (error) { unmodelled = error.message; }
report({ seen, observed: io.observed.length, unmodelled,
  margin: io.options.rootMargin });
})().catch(leave);
"""


_LIVE_TREE = r"""
(async () => {
const util = await bounded(load('sections/_util.js'), 'util import',
  _dashnodeStepTimeoutMs);
const h = util.h;
const panel = h('div', { class: 'panel-b', 'data-section': 'overview' });
const other = h('div', { class: 'panel-b', 'data-section': 'tabs' });
const rail = h('ol', { class: 'rail-list' }, [
  h('li', {}, h('a', { href: '#s00' }, 'OVERVIEW')),
  h('li', {}, h('a', { href: '#s01' }, 'TABS')),
]);
document.body.appendChild(panel);
document.body.appendChild(other);
document.body.appendChild(rail);
panel.appendChild(h('p', {}, 'first'));
panel.appendChild(h('p', {}, 'second'));
const paragraphs = document.querySelectorAll('p');
const before = document.querySelectorAll('[data-section]').length;
const isArray = Array.isArray(paragraphs);
const matched = Array.from(paragraphs).length;
const anchors = document.querySelectorAll('.rail-list a').length;
util.clear(panel);
report({ before, isArray, matched, anchors,
  after: document.querySelectorAll('[data-section]').length,
  gone: document.querySelectorAll('p').length,
  emptied: panel.textContent });
})().catch(leave);
"""


_RESPONSE_DOUBLE = r"""
(async () => {
drive.route('https://example.com/tabs', { json: [] });
const response = await bounded(fetch('https://example.com/tabs'),
  'planned json response', _dashnodeStepTimeoutMs);
let header = null;
try {
  response.headers.get('x-not-a-header');
} catch (error) { header = error.message; }
let blob = null;
try {
  await response.blob();
} catch (error) { blob = error.message; }
let absent = null;
try {
  response.formData();
} catch (error) { absent = error.message; }
let buffer = null;
try {
  await response.arrayBuffer();
} catch (error) { buffer = error.message; }
let copy = null;
try {
  response.clone();
} catch (error) { copy = error.message; }
report({ contentType: response.headers.get('content-type'),
  header, blob, absent, buffer, copy });
})().catch(leave);
"""


# A `console.error` argument whose `String()` throws. `String(symbol)`
# does not -- it is the one implicit conversion that is special-cased --
# so the thrower here is an object with a `toString` of its own that
# throws. The recorder is read by every control that asserts a logged
# line, so a throw inside it would take the call site with it.
_UNPRINTABLE = r"""
(async () => {
const unprintable = { toString() { throw new Error('nope'); } };
let escaped = null;
try { console.error('[sse] listener error', unprintable); }
catch (error) { escaped = error.message; }
report({ escaped });
})().catch(leave);
"""

_CONSOLE_ERROR = r"""
(async () => {
console.error('[mount] overview failed', new Error('boom'));
report();
})().catch(leave);
"""


# The shell against the real entry point. `boot()` runs wireMetaBar,
# wireStatusLine, wireRailHighlight, then mountSections, so the first
# interval and the first observer parked belong to app.js itself.
_APP_BOOT = r"""
(async () => {
const util = await bounded(load('sections/_util.js'), 'util import',
  _dashnodeStepTimeoutMs);
const h = util.h;
const bar = h('div', { class: 'meta-bar' }, [
  h('span', { class: 'meta-v', 'data-meta': 'server' }, '-'),
  h('span', { class: 'meta-v', 'data-meta': 'token' }, '-'),
  h('span', { class: 'sse-dot', 'data-meta': 'sse-dot',
    'data-status': 'idle' }),
  h('span', { class: 'meta-v', 'data-meta': 'sse-text' }, 'idle'),
]);
const status = h('div', { class: 'sl' }, [
  h('span', { class: 'sl-meta', 'data-meta': 'token-short' }, '-'),
  h('span', { class: 'sl-meta', 'data-meta': 'sse-text2' }, 'idle'),
  h('span', { class: 'sl-meta', 'data-meta': 'last-event' }, '-'),
]);
const rail = h('ol', { class: 'rail-list' }, [
  h('li', {}, h('a', { href: '#s00' }, 'OVERVIEW')),
  h('li', {}, h('a', { href: '#s01' }, 'TABS')),
]);
const panel = h('div', { class: 'panel-b', 'data-section': 'overview' });
for (const el of [bar, status, rail, h('section', { id: 's00' }),
  h('section', { id: 's01' }), panel]) {
  document.body.appendChild(el);
}
localStorage.setItem('daedalus-token', 'tok-abcdefghijklmnop');
localStorage.setItem('daedalus-server', 'https://example.com');
drive.route('https://example.com/stream?tab=dashboard', { stream: true });
drive.route('https://example.com/tabs', { json: [] });
document.readyState = 'loading';
await bounded(load('app.js'), 'app import', _dashnodeStepTimeoutMs);
await bounded(settle(), 'import with boot deferred', _dashnodeStepTimeoutMs);
const deferred = document.querySelector('[data-meta="token"]').textContent;
document.fire('DOMContentLoaded');
await bounded(settle(), 'boot', _dashnodeStepTimeoutMs);
const links = Array.from(document.querySelectorAll('.rail-list a'));
drive.fire(drive.ids((slot) => slot.kind === 'interval')[0]);
const io = drive.observers()[0];
io.fire([{ isIntersecting: true, boundingClientRect: { top: 40 },
  target: document.querySelector('#s01') }]);
const afterObserver = links.map((a) => a.classList.contains('active'));
links[0].click();
const counts = {};
for (const selector of ['[data-meta="token"]',
  '[data-meta="token-short"]', '[data-meta="server"]',
  '[data-meta="sse-dot"]', '[data-meta="sse-text"]',
  '[data-meta="sse-text2"]', '[data-meta="last-event"]',
  '[data-section]', '.rail-list a']) {
  counts[selector] = document.querySelectorAll(selector).length;
}
report({ deferred, counts,
  token: document.querySelector('[data-meta="token"]').textContent,
  dot: document.querySelector('[data-meta="sse-dot"]').dataset.status,
  sseText: document.querySelector('[data-meta="sse-text"]').textContent,
  lastEvent: document.querySelector('[data-meta="last-event"]').textContent,
  mounted: panel.textContent.length > 0
    && !panel.textContent.includes('no module for'),
  afterObserver,
  afterClick: links.map((a) => a.classList.contains('active')),
  observed: io.observed.length, observers: drive.observers().length });
})().catch(leave);
"""


def test_an_unplanned_request_is_refused_and_recorded(_tmp):
    """A request the scenario never planned is refused, and the refusal is
    readable back. `sse.js` wraps its fetch in a try/catch and reads a
    thrown error as an ordinary stream error, so a throwing double
    answers this control with a green run: the child exits 0 and the
    report carries no refusal at all. Making the double throw leaves
    `unplanned` empty and `planned` true."""
    report = run_scenario(_UNPLANNED_REQUEST, modules=('sse.js',))
    assert len(report['requests']) == 1, report
    request = report['requests'][0]
    assert request['target'] == _STREAM, request
    assert request['planned'] is False, request
    assert report['unplanned'] == [{'n': 1, 'target': _STREAM}], report
    assert report['settlements'] == [], report
    assert report['errors'] == [
        '[sse] stream error HTTP ' + str(_dashshell.UNPLANNED_STATUS)
    ], report['errors']


def test_an_undeclared_origin_is_refused(_tmp):
    """The plan is keyed on the whole request target. The scenario
    declares the bridge origin and the code then requests the same route
    relatively, which a route-shaped match would serve from the declared
    route. Matching the route empties `unplanned`."""
    report = run_scenario(_ROUTE_ONLY_MATCH, modules=('sse.js',))
    assert report['planned'] == [_STREAM], report
    request = report['requests'][0]
    assert request['target'] == _LOCAL_STREAM, request
    assert request['server'] == '', request
    assert request['planned'] is False, request
    assert report['unplanned'] == [{'n': 1, 'target': _LOCAL_STREAM}], report


def test_the_request_log_carries_the_bearer_value_and_the_target(_tmp):
    """The log records the arguments the real API chooses behaviour by:
    the Authorization header's value, the tab query, the server prefix
    and the signal. Recording `''` for the header turns this red."""
    report = run_scenario(_REQUEST_LOG, modules=('sse.js',))
    assert len(report['requests']) == 1, report
    request = report['requests'][0]
    assert request['target'] == _STREAM, request
    assert request['authorization'] == 'Bearer ' + _TOKEN, request
    assert request['tab'] == 'dashboard', request
    assert request['server'] == _SERVER, request
    assert request['hasSignal'] is True, request
    assert request['planned'] is True, request


def test_the_request_log_records_what_the_caller_varied(_tmp):
    """The same six arguments, read rather than answered. Every other
    request in this suite is a GET carrying `tab=dashboard` and a
    signal, so a constant would be indistinguishable from a read; here
    each is pinned in both directions. Hardcoding `hasSignal: true`,
    `tab: 'dashboard'`, `method: 'GET'` or one server prefix turns this
    red on the request that varies it."""
    report = run_scenario(_REQUEST_LOG_VARIED)
    plain, tabbed, elsewhere = report['requests']
    assert plain == {'n': 1, 'target': _TABS, 'method': 'GET',
                     'authorization': 'Bearer one', 'tab': None,
                     'server': _SERVER, 'hasSignal': False,
                     'planned': True}, plain
    assert tabbed == {'n': 2, 'target': _TABS + '?tab=alpha',
                      'method': 'POST', 'authorization': 'Bearer two',
                      'tab': 'alpha', 'server': _SERVER,
                      'hasSignal': False, 'planned': True}, tabbed
    assert elsewhere == {'n': 3, 'target': 'https://example.com:8443/tabs'
                         + '?tab=beta', 'method': 'GET',
                         'authorization': 'Bearer three', 'tab': 'beta',
                         'server': 'https://example.com:8443',
                         'hasSignal': True, 'planned': True}, elsewhere
    assert report['unplanned'] == [], report


def test_a_headers_bag_the_shell_cannot_read_is_refused(_tmp):
    """A real `Headers` carries the credential just as truly, so reading
    it as absent would assert the opposite of the truth. The bag is
    refused by name, and nothing is recorded for the request, because a
    recorded request would carry `authorization: null`."""
    report = run_scenario(_HEADERS_BAG)
    assert report['requests'] == [], report
    assert report['bagRefusal'] is not None, report
    assert 'plain header object' in report['bagRefusal'], report
    assert 'Headers' in report['bagRefusal'], report


def test_the_reader_exposes_every_settlement(_tmp):
    """Two frames, a stream driven to its end, a failed stream and a
    teardown's abort are each recorded as they happen. Recording
    nothing leaves three empty lists. The abort proves itself by its
    silence: sse.js stays quiet on AbortError, so one error is logged."""
    report = run_scenario(_READER, modules=('sse.js',))
    first, second, third = report['settlements']
    assert [s['kind'] for s in first] == ['chunk', 'chunk', 'done'], first
    assert 'e1' in first[0]['text'] and 'e2' in first[1]['text'], first
    assert second == [{'kind': 'error',
                       'message': 'connection reset by peer'}], second
    assert third == [{'kind': 'abort'}], third
    assert len(report['errors']) == 1, report['errors']
    assert 'connection reset' in report['errors'][0], report['errors']
    assert report['afterClose'] is not None, report
    assert 'reader already settled' in report['afterClose'], report


def test_a_window_storage_event_reaches_the_sse_client(_tmp):
    """`sse.js`'s `storage` listener registers at module-evaluation
    time, so the shell has to hold a window listener a scenario can
    fire. Dropping the registration leaves the first connection as the
    only one."""
    report = run_scenario(_WINDOW_STORAGE, modules=('sse.js',))
    assert len(report['requests']) == 2, report
    assert report['requests'][0]['authorization'] == (
        'Bearer first-token-abcdefgh'), report
    assert report['requests'][1]['authorization'] == (
        'Bearer second-token-abcdefgh'), report
    assert report['unplanned'] == [], report


def test_a_parked_timer_runs_only_when_driven(_tmp):
    """sse.js's 3s retry and app.js's 1s clock park, and driving one
    runs its callback exactly once, with the arguments real setTimeout
    would have passed it. Running short timers for real leaves `parked`
    at 1; not spending a fired timeout leaves it in `live`."""
    report = run_scenario(_PARKED_TIMERS)
    assert report['before'] == 0, report
    assert report['parked'] == 0, report
    assert report['once'] == 1, report
    assert report['twice'] == 2, report
    assert report['carried'] == 'carried', report
    assert report['live'] == [{'id': 2, 'kind': 'interval',
                               'delay': 1000}], report


def test_class_list_toggle_reflects_force_in_both_directions(_tmp):
    """`app.js` activates a rail link with the two-argument toggle, and
    a scenario reads the class set back off `className`. A toggle that
    ignores `force` leaves `forcedOff.has` true."""
    report = run_scenario(_CLASS_LIST)
    assert report['forcedOn'] == {'value': 'meta-v dim active',
                                  'has': True}, report
    assert report['forcedOff'] == {'value': 'meta-v dim',
                                   'has': False}, report
    assert report['bare'] == 'meta-v dim armed', report
    assert report['length'] == 3, report


def test_the_observer_refuses_an_unmodelled_member(_tmp):
    """An unmodelled observer member fails by name; `undefined` would
    make the use a silent no-op. The callback must receive the caller's
    entries -- both of them, in order, with the caller's own values --
    because app.js filters on `isIntersecting`, sorts on
    `boundingClientRect.top` and picks on `target.id` over a list it
    did not make. Truncating the list to one entry, or substituting an
    entry of the double's own, both turn this red."""
    report = run_scenario(_OBSERVER)
    assert report['seen'] == [['skipped:false:5',
                               'from-the-caller:true:90']], report
    assert report['observed'] == 1, report
    assert report['unmodelled'] is not None, report
    assert 'not modelled: unobserveAll' in report['unmodelled'], report
    assert report['margin'] == '-80px 0px -60% 0px', report


def test_queries_walk_the_live_tree(_tmp):
    """`mountSections` clears a section and re-renders into it, so a
    query has to see the tree as it is now, and the result is a
    NodeList rather than an Array because app.js wraps it in
    Array.from. Caching a query leaves `gone` at 2."""
    report = run_scenario(_LIVE_TREE, modules=('sections/_util.js',))
    assert report['before'] == 2, report
    assert report['isArray'] is False, report
    assert report['matched'] == 2, report
    assert report['anchors'] == 2, report
    assert report['after'] == 2, report
    assert report['gone'] == 0, report
    assert report['emptied'] == '', report


def test_the_response_double_refuses_what_it_does_not_model(_tmp):
    """`api.js`'s `req` branches on `content-type` and can only
    reach its `r.text()` branch if some scenario models a non-JSON
    response, so the header answers for the one name it has and refuses
    the rest. A response member the double does not have is refused by
    name, as the observer's is, not left to fail as
    `blob is not a function`."""
    report = run_scenario(_RESPONSE_DOUBLE)
    assert report['contentType'] == 'application/json', report
    assert report['header'] is not None, report
    assert 'header not modelled: x-not-a-header' in report['header'], report
    assert report['blob'] is not None, report
    assert 'blob not modelled' in report['blob'], report
    assert report['absent'] is not None, report
    assert 'member not modelled: formData' in report['absent'], report
    assert report['buffer'] is not None, report
    assert 'member not modelled: arrayBuffer' in report['buffer'], report
    assert report['copy'] is not None, report
    assert 'member not modelled: clone' in report['copy'], report


def test_an_unprintable_console_error_is_recorded_not_thrown(_tmp):
    """`describe()`'s guard, which had none. A value whose `String()`
    throws would leave the `console.error` that called it -- from inside
    `sse.js`'s own `catch`, where it would end the stream instead of
    logging a listener. Both halves are asserted: the throw did not
    escape the call, and the line is on the recorder."""
    report = run_scenario(_UNPRINTABLE)
    assert report['escaped'] is None, report
    assert report['errors'] == [
        '[sse] listener error [unprintable value]'], report['errors']


def test_console_error_is_recorded_as_well_as_printed(_tmp):
    """`app.js` reports a failed mount and a throwing bus listener
    through `console.error` and nothing else, so a scenario can only
    read them off the recorder. Not recording leaves `errors` empty."""
    report = run_scenario(_CONSOLE_ERROR)
    assert report['errors'] == ['[mount] overview failed boom'], report


def test_app_js_boots_against_the_shell(_tmp):
    """The whole point of the scaffold: the shipped entry point reaches
    every surface the brief enumerates, and the shell answered or
    refused nothing it could not do. Nine selectors resolve, the token
    and server labels render, the section mounts through
    `dataset.section`, the observer and the rail click move the
    highlight, and no request went unplanned. A scaffold that cannot do
    one of those fails this control instead of passing it quietly."""
    report = run_scenario(_APP_BOOT, modules=_APP_MODULES)
    assert report['unplanned'] == [], report
    assert report['errors'] == [], report
    assert report['deferred'] == '-', report
    assert report['counts'] == {
        '[data-meta="token"]': 1,
        '[data-meta="token-short"]': 1,
        '[data-meta="server"]': 1,
        '[data-meta="sse-dot"]': 1,
        '[data-meta="sse-text"]': 1,
        '[data-meta="sse-text2"]': 1,
        '[data-meta="last-event"]': 1,
        '[data-section]': 1,
        '.rail-list a': 2,
    }, report['counts']
    assert report['token'] == 'tok-abcd…mnop', report
    assert report['dot'] == 'connected', report
    assert report['sseText'] == 'connected', report
    assert report['lastEvent'] == 'now', report
    assert report['mounted'] is True, report
    assert report['observed'] == 2, report
    assert report['afterObserver'] == [False, True], report
    assert report['afterClick'] == [True, False], report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashshell_')


if __name__ == '__main__':
    raise SystemExit(main())
