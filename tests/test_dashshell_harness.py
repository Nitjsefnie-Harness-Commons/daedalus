#!/usr/bin/env python3
"""What the dashboard shell harness guarantees, proven by breaking it.

The shell is a guard, so a green run on the tree it was written against
proves nothing. Each control drives the shipped `sse.js` or the shell
itself through a real Node child and reads the report it printed; the
defect each one plants and the red output it produced are recorded in
the task report for issue 493.

None of this asserts how the dashboard behaves. It asserts that a
scenario can see what it drove -- a refusal recorded rather than thrown,
a settlement recorded rather than implied, a timer that stays parked.
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


_PARKED_TIMERS = r"""
(async () => {
let ticks = 0;
setTimeout(() => { ticks += 1; }, 3000);
setInterval(() => { ticks += 1; }, 1000);
const ids = drive.ids();
const before = ticks;
await bounded(pause(20), 'parked timers must not run',
  _dashnodeStepTimeoutMs);
const parked = ticks;
drive.fire(drive.ids((slot) => slot.kind === 'interval')[0]);
const once = ticks;
drive.fire(drive.ids((slot) => slot.kind === 'interval')[0]);
const twice = ticks;
clearTimeout(ids[0]);
report({ before, parked, once, twice, live: drive.live() });
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


_SELECTORS = r"""
(async () => {
const empty = document.querySelectorAll('[data-meta="nothing-here"]');
let combinator = null;
try {
  document.querySelectorAll('.rail-list > li');
} catch (error) { combinator = error.message; }
let notAString = null;
try {
  document.querySelectorAll(null);
} catch (error) { notAString = error.message; }
report({ empty: empty.length, isArray: Array.isArray(empty),
  combinator, notAString });
})().catch(leave);
"""


_OBSERVER = r"""
(async () => {
const target = new El('section');
const seen = [];
const io = new IntersectionObserver((entries) => {
  seen.push(entries.map((e) => e.target.id + ':' + e.isIntersecting));
}, { rootMargin: '-80px 0px -60% 0px', threshold: 0 });
io.observe(target);
io.fire([{ isIntersecting: true, boundingClientRect: { top: 12 },
  target: { id: 'from-the-caller' } }]);
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


_CONSOLE_ERROR = r"""
(async () => {
console.error('[mount] overview failed', new Error('boom'));
report();
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
    assert 'reader already settled' in report['afterClose'], report


def test_a_parked_timer_runs_only_when_driven(_tmp):
    """sse.js's 3s retry and app.js's 1s clock park, and driving one
    runs its callback exactly once. Running short timers for real --
    what `_dashnode`'s shared scaffold does -- leaves `parked` at 1."""
    report = run_scenario(_PARKED_TIMERS)
    assert report['before'] == 0, report
    assert report['parked'] == 0, report
    assert report['once'] == 1, report
    assert report['twice'] == 2, report
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


def test_an_unimplemented_selector_fails_by_name(_tmp):
    """A selector the shell does not implement raises, naming it; one
    that matches nothing returns an empty list. Answering an
    unimplemented selector with an empty list makes "the scaffold did
    not understand" look like "nothing matched". Permissive parsing
    leaves `combinator` null."""
    report = run_scenario(_SELECTORS)
    assert report['empty'] == 0, report
    assert report['isArray'] is False, report
    assert 'does not implement selector' in report['combinator'], report
    assert '.rail-list > li' in report['combinator'], report
    assert 'is not a selector' in report['notAString'], report


def test_the_observer_refuses_an_unmodelled_member(_tmp):
    """An unmodelled observer member fails by name; `undefined` would
    make the use a silent no-op. The entry list is the caller's, so
    `seen` proves the callback ran on what the scenario supplied."""
    report = run_scenario(_OBSERVER)
    assert report['seen'] == [['from-the-caller:true']], report
    assert report['observed'] == 1, report
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


def test_console_error_is_recorded_as_well_as_printed(_tmp):
    """`app.js` reports a failed mount and a throwing bus listener
    through `console.error` and nothing else, so a scenario can only
    read them off the recorder. Not recording leaves `errors` empty."""
    report = run_scenario(_CONSOLE_ERROR)
    assert report['errors'] == ['[mount] overview failed boom'], report


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashshell_')


if __name__ == '__main__':
    raise SystemExit(main())
