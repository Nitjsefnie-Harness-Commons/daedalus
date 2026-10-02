#!/usr/bin/env python3
"""What the dashboard does with what the bridge tells it.

The dashboard is the token-bearing control surface, so its own state machine
is a contract: a retired keepalive must not clobber the port that replaced
it, a consume that failed is not a result, every tab selector reads one
controller, and no value reaches innerHTML. These run the shipped modules in
a Node VM rather than reading them where a run can answer instead.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashfetch  # noqa: E402
import _dashnode  # noqa: E402
import _dashnode_retry_control as retry  # noqa: E402
import _util  # noqa: E402
from _jsread import blank_js_comments  # noqa: E402
from _repo import ROOT  # noqa: E402
from _worker_sources import event_target_stub  # noqa: E402


_CONTENT_KEEPALIVE_HARNESS = _dashnode.DashboardNodeHarness(
    r"""
phase('dashboard harness started');
const fs = require('fs');
const vm = require('vm');
const timers = [];
const intervals = [];
const ports = [];
let nextId = 0;
function scheduled(collection, callback, delay) {
  const item = { id: ++nextId, callback, delay, cleared: false };
  collection.push(item);
  return item.id;
}
function clearScheduled(collection, id) {
  const item = collection.find((candidate) => candidate.id === id);
  if (item) item.cleared = true;
}
""" + event_target_stub() + r"""
const windowObject = {
  addEventListener() {},
  postMessage() {},
};
const chrome = {
  runtime: {
    lastError: null,
    onMessage: eventTarget([]),
    sendMessage() {},
    connect() {
      const disconnectListeners = [];
      const port = {
        messages: [],
        disconnectListeners,
        postMessage(message) { port.messages.push(message); },
        disconnect() {},
        onDisconnect: eventTarget(disconnectListeners),
      };
      ports.push(port);
      return port;
    },
    getManifest: () => ({ version: '0.18.0' }),
  },
  storage: {
    local: {
      get(_keys, callback) { callback({}); },
      set(_data, callback) { if (callback) callback(); },
      remove(_keys, callback) { if (callback) callback(); },
    },
  },
};
const context = vm.createContext(Object.assign({
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: '' },
  setTimeout: (callback, delay) => scheduled(timers, callback, delay),
  clearTimeout: (id) => clearScheduled(timers, id),
  setInterval: (callback, delay) => scheduled(intervals, callback, delay),
  clearInterval: (id) => clearScheduled(intervals, id),
  console: { log() {}, error() {} },
}, contentScriptPage()));
phase('dashboard module import started');
vm.runInContext(
  fs.readFileSync(process.argv[1], 'utf8'), context,
  { filename: process.argv[1] });
phase('dashboard module imported');
phase('dashboard call started');
const firstProactive = timers.find((item) => item.delay === 4 * 60 * 1000);
firstProactive.callback();
const secondInterval = intervals[intervals.length - 1];
for (const listener of ports[0].disconnectListeners) listener();
secondInterval.callback();
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  portCount: ports.length,
  port2Pings: ports[1].messages.length,
  interval2Cleared: secondInterval.cleared,
  retryTimers: timers.filter((item) => item.delay === 500).length,
}));
phase('dashboard harness finished');
""", bounded_steps=0, arguments=(ROOT / 'extension' / 'content.js',))


def test_stale_keepalive_disconnect_cannot_clobber_replacement_port(_tmp):
    result = _dashnode.run_dashboard_node(_CONTENT_KEEPALIVE_HARNESS)
    actual = json.loads(result.stdout)
    assert actual == {
        'portCount': 2,
        'port2Pings': 1,
        'interval2Cleared': False,
        'retryTimers': 0,
    }, actual


# The stale port is what the harness above fires, so every handler in it
# returns at its first line. These fire the port that IS live, and a realm
# whose `chrome.runtime.connect` refuses as a revoked context does.
_KEEPALIVE_LIFECYCLE_HARNESS = _dashnode.DashboardNodeHarness(
    r"""
phase('dashboard harness started');
const fs = require('fs');
const vm = require('vm');
""" + event_target_stub() + r"""
// One content-script realm with controllable timers and RETAINED disconnect
// listeners. `invalidated` makes chrome.runtime.connect throw, which is what
// a service worker answers with once its extension context is revoked.
function realm(invalidated) {
  const timers = [];
  const intervals = [];
  const ports = [];
  let nextId = 0;
  function scheduled(collection, callback, delay) {
    const item = { id: ++nextId, callback, delay, cleared: false };
    collection.push(item);
    return item.id;
  }
  function cancel(collection, id) {
    const item = collection.find((candidate) => candidate.id === id);
    if (item) item.cleared = true;
  }
  const chrome = {
    runtime: {
      lastError: null,
      onMessage: eventTarget([]),
      sendMessage() {},
      connect() {
        if (invalidated) throw new Error('Extension context invalidated.');
        const disconnectListeners = [];
        const port = {
          messages: [],
          disconnectListeners,
          postMessage(message) { port.messages.push(message); },
          disconnect() {},
          onDisconnect: eventTarget(disconnectListeners),
        };
        ports.push(port);
        return port;
      },
      getManifest: () => ({ version: '0.18.0' }),
    },
  };
  const context = vm.createContext(Object.assign({
    window: { addEventListener() {}, postMessage() {} },
    chrome,
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    location: { hostname: '' },
    setTimeout: (callback, delay) => scheduled(timers, callback, delay),
    clearTimeout: (id) => cancel(timers, id),
    setInterval: (callback, delay) => scheduled(intervals, callback, delay),
    clearInterval: (id) => cancel(intervals, id),
    console: { log() {}, error() {} },
  }, contentScriptPage()));
  vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context,
    { filename: process.argv[1] });
  return { timers, intervals, ports };
}

function armed(collection, delay) {
  return collection.filter((item) => item.delay === delay);
}
// Fire what a handler armed, and report an absence rather than throwing on
// it: a relay that armed nothing is the defect, so it reaches the
// assertion as a value rather than ending the child.
function fire(collection, delay) {
  for (const item of armed(collection, delay)) item.callback();
}
phase('dashboard module import started');
const live = realm(false);
phase('dashboard module imported');
phase('dashboard call started');
const livePort = live.ports[0];
for (const listener of livePort.disconnectListeners) listener();
const afterDisconnect = { intervalCleared: live.intervals[0].cleared,
  proactiveCleared: armed(live.timers, 4 * 60 * 1000)[0].cleared,
  retries: armed(live.timers, 500).length };
// Only the retry the disconnect armed reconnects, so firing it is the test.
fire(live.timers, 500);
live.intervals[live.intervals.length - 1].callback();
const afterRetry = { ports: live.ports.length,
  firstPortPings: livePort.messages.length,
  secondPortPings: live.ports.length > 1 ? live.ports[1].messages.length : 0 };
const dead = realm(true);
const backoff = armed(dead.timers, 5000);
fire(dead.timers, 5000);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  afterDisconnect, afterRetry,
  invalidated: { ports: dead.ports.length, before: backoff.length,
    after: armed(dead.timers, 5000).length },
}));
phase('dashboard harness finished');
""", bounded_steps=0, arguments=(ROOT / 'extension' / 'content.js',))


def test_a_live_keepalive_disconnect_reconnects_and_a_dead_one_backs_off(_tmp):
    result = _dashnode.run_dashboard_node(_KEEPALIVE_LIFECYCLE_HARNESS)
    actual = json.loads(result.stdout)
    assert actual == {
        'afterDisconnect': {
            'intervalCleared': True, 'proactiveCleared': True, 'retries': 1},
        # A second port exists and the ping lands on IT: the retired port's
        # interval is gone, so a listener that kept the old one pings a port
        # nobody holds open.
        'afterRetry': {
            'ports': 2, 'firstPortPings': 0, 'secondPortPings': 1},
        # No port at all, and firing the backoff armed another -- proof the
        # retry ran connectKeepAlive again.
        'invalidated': {'ports': 0, 'before': 1, 'after': 2},
    }, actual


_DASHBOARD_CONSUME_HARNESS = _dashnode.DashboardNodeHarness(
    r"""
phase('dashboard harness started');
const fs = require('fs');
function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}
""" + _dashfetch.DOOR + r"""
(async () => {
  const tokenKey = 'daedalus-token';
  globalThis.localStorage = {
    getItem: key => key === tokenKey ? 'dashboard-token' : '',
    setItem: () => {},
  };
  globalThis.setTimeout = callback => { callback(); return 0; };
  let commandSent = false;
  // `runCommand` polls the tab it was given and consumes with the
  // generation this fake handed out, so the third query member is one
  // the scenario knows before the first request. GET is the only method
  // either leg is sent with, so a POST to the same target is a different
  // request.
  const PEEK = '/result?tab=extension';
  const CONSUME = PEEK + '&consume=1&expected=result-generation';
  const ENVELOPE = {
    id: 'dashboard-command',
    deliveryId: 'command-delivery',
    resultGeneration: 'result-generation',
    result: 'fresh',
    error: null,
    world: 'page:cdp',
  };
  globalThis.fetch = async (target, init = {}) => {
    const where = String(target);
    const method = init.method || 'GET';
    if (method === 'PUT') {
      if (where !== '/command') return refuse(where);
      commandSent = true;
      return response(200, { ok: true, did: 'command-delivery' });
    }
    if (method !== 'GET') return refuse(where);
    if (where === PEEK) return response(200, ENVELOPE);
    if (where === CONSUME) {
      if (!commandSent) return response(200, { pending: true });
      return response(500, { error: 'consume failed' });
    }
    return refuse(where);
  };
  phase('dashboard module import started');
  const source = fs.readFileSync(process.argv[1], 'utf8');
  const moduleUrl = 'data:text/javascript;base64,'
    + Buffer.from(source).toString('base64');
  const dashboard = await bounded(
    import(moduleUrl), 'dashboard module import', _dashnodeStepTimeoutMs);
  phase('dashboard module imported');
  phase('dashboard call started');
  let rejected = false;
  try {
    await bounded(
      dashboard.runCommand({
        type: 'cookies', id: 'dashboard-command', timeout: 1000,
      }),
      'dashboard call', _dashnodeStepTimeoutMs,
    );
  } catch (error) {
    rejected = true;
    if (!String(error.message).includes('HTTP 500')) throw error;
  }
  if (!rejected) throw new Error('failed consume surfaced as a"""
    r""" successful read');
  process.stdout.write(JSON.stringify({ unplanned: UNPLANNED }));
  phase('dashboard call settled');
  phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=2, arguments=(ROOT / 'dashboard' / 'api.js',))


def test_dashboard_failed_consume_is_not_a_success(_tmp):
    result = _dashnode.run_dashboard_node(_DASHBOARD_CONSUME_HARNESS)
    # Every faked route is an EXACT target the scenario names -- the one
    # command and both result legs, at the generation this fake handed
    # out -- so an empty record is a claim that the module asked for
    # nothing else.
    assert json.loads(result.stdout)['unplanned'] == [], result.stdout


_DASHBOARD_WORLD_HARNESS = _dashnode.DashboardNodeHarness(r"""
phase('dashboard harness started');
const fs = require('fs');
(async () => {
  phase('dashboard module import started');
  const source = fs.readFileSync(process.argv[1], 'utf8');
  const moduleUrl = 'data:text/javascript;base64,'
    + Buffer.from(source).toString('base64');
  const dashboard = await bounded(
    import(moduleUrl), 'dashboard module import', _dashnodeStepTimeoutMs);
  phase('dashboard module imported');
  phase('dashboard call started');
  process.stdout.write(JSON.stringify([
    dashboard.formatEvalWorld('cdp'),
    dashboard.formatEvalWorld('page-main'),
    dashboard.formatEvalWorld('page:cdp'),
    dashboard.formatEvalWorld('extension'),
    dashboard.formatEvalWorld('module-main'),
  ]));
  phase('dashboard call settled');
  phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=1, arguments=(
    ROOT / 'dashboard' / 'sections' / '_util.js',))


def test_dashboard_labels_eval_world_as_a_channel(_tmp):
    result = _dashnode.run_dashboard_node(_DASHBOARD_WORLD_HARNESS)
    assert json.loads(result.stdout) == [
        'channel=cdp',
        'channel=page-main',
        'channel=page:cdp',
        'channel=extension',
        'channel=module-main',
    ]


def _expression_after(source, start):
    """Return the text from `start` to the statement's terminating `;`."""
    index, end = start, len(source)
    depth = 0
    quote = None
    while index < end:
        char = source[index]
        if quote:
            if char == '\\':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in '\'"`':
            quote = char
        elif char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ';' and depth == 0:
            return source[start:index]
        index += 1
    return source[start:]


_CONSTANT_MARKUP = re.compile(
    r"^(?:'(?:[^'\\\n]|\\.)*'"
    r'|"(?:[^"\\\n]|\\.)*"'
    r'|`(?:[^`\\$]|\\.|\$(?!\{))*`)$')


def test_dashboard_never_builds_markup_from_a_value(_tmp):
    violations = []
    sources = sorted((ROOT / 'dashboard').rglob('*.js'))
    assert sources, 'no dashboard sources found'
    for path in sources:
        blanked = blank_js_comments(path.read_text(encoding='utf-8'))
        for match in re.finditer(r'\.innerHTML\s*(\+?=)(?!=)', blanked):
            line = blanked.count('\n', 0, match.start()) + 1
            expression = _expression_after(blanked, match.end()).strip()
            if match.group(1) == '=' and _CONSTANT_MARKUP.match(expression):
                continue
            violations.append(
                f'{path.relative_to(ROOT)}:{line}: '
                f'innerHTML {match.group(1)} {expression[:80]}')
    assert not violations, '\n'.join(violations)


_TAB_SELECTOR_HARNESS = _dashnode.DashboardNodeHarness(
    r"""
import { pathToFileURL } from 'node:url';
phase('dashboard harness started');
""" + _dashfetch.DOOR + r"""
(async () => {
// Enough DOM for `h` and `clear`; the controller under test is real.
class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.text = '';
    this._value = '';
    this.style = {};
    this.dataset = {};
  }
  get firstChild() { return this.children[0] || null; }
  get options() { return this.children.filter((c) => c.tag === 'option'); }
  get value() { return this._value; }
  set value(v) { this._value = String(v); }
  get label() { return this.children.map((c) => c.text || c.label).join(''); }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) {
    this.children.splice(this.children.indexOf(child), 1);
    // A real select drops its value when the selected option goes away.
    if (child.tag === 'option' && child.value === this._value)"""
    r""" this._value = '';
    return child;
  }
  setAttribute(name, v) { if (name === 'value') this._value = String(v); }
  addEventListener() {}
}
globalThis.document = {
  createElement: (tag) => new El(tag),
  createTextNode: (t) => ({ tag: '#text', text: String(t), children: [] }),
};
// pathToFileURL, not the bare path: Node's ESM loader accepts only file://
// URLs, and on Windows an absolute path starts with a drive letter it reads
// as an unsupported URL scheme ('d:').
phase('dashboard module import started');
const { bindTabSelector } = await bounded(
  import(pathToFileURL(process.argv[1]).href),
  'dashboard module import', _dashnodeStepTimeoutMs,
);
phase('dashboard module imported');
let tabs = [{ tabId: '11', title: 'first' }, { tabId: '22', title: 'second' }];
const listeners = [];
const select = new El('select');
// The one path `bindTabSelector` reads. `api.get` taking no argument
// answered every path with the tab list, so a call the module invented
// was served (#1238). The seam is an injected object rather than
// `globalThis.fetch`, so the refusal takes the shape `api.js` itself
// produces there -- a REJECTED promise carrying `HTTP 599` -- rather than
// the 599 response a fetch returns one layer below, which an `api.get`
// caller would iterate as if it were the list. `populate()` wraps this
// call in its own catch, so the record is the half that survives it.
const asked = [];
const api = {
  get: async (path) => {
    asked.push(String(path));
    if (String(path) !== '/tabs') {
      refuse(String(path));
      throw new Error('HTTP 599: unplanned request');
    }
    return tabs;
  },
};
const bus = { on: (fn) => listeners.push(fn) };
function emit(type) {
  for (const fn of listeners) fn({ type });
}
const settle = () => new Promise((r) => setTimeout(r, 0));
phase('dashboard call started');
bindTabSelector(select, {
  getToken: () => 'tok', api, bus, placeholder: '(active tab)',
});
await bounded(
  settle(), 'initial tab selector render', _dashnodeStepTimeoutMs);
const initial = select.options.map((o) => o.value);
select.value = '22';
tabs = [{ tabId: '11', title: 'first' }, { tabId: '22', title: 'RETITLED' }];
emit('tab-updated');
await bounded(
  settle(), 'tab update refresh', _dashnodeStepTimeoutMs);
const afterUpdate = {
  labels: select.options.map((o) => o.label),
  selected: select.value,
};
tabs = [{ tabId: '11', title: 'first' }];
emit('tab-unregistered');
await bounded(
  settle(), 'tab unregister refresh', _dashnodeStepTimeoutMs);
const afterUnregister = {
  offered: select.options.map((o) => o.value),
  selected: select.value,
};
tabs = [{ tabId: '11', title: 'first' }, { tabId: '33', title: 'third' }];
emit('tabs-synced');
await bounded(
  settle(), 'tab sync refresh', _dashnodeStepTimeoutMs);
const afterSync = select.options.map((o) => o.value);
phase('dashboard call settled');
process.stdout.write(JSON.stringify({
  initial, afterUpdate, afterUnregister, afterSync, asked,
  unplanned: UNPLANNED,
}));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=5, module=True, arguments=(
        ROOT / 'dashboard' / 'sections' / '_util.js',))


def _run_tab_selector_harness():
    result = _dashnode.run_dashboard_node(_TAB_SELECTOR_HARNESS)
    return json.loads(result.stdout)


def test_a_tab_selector_follows_every_lifecycle_event(_tmp):
    seen = _run_tab_selector_harness()
    assert seen['unplanned'] == [], seen
    assert seen['asked'] and set(seen['asked']) == {'/tabs'}, seen
    assert seen['initial'] == ['', '11', '22'], seen
    assert '11  RETITLED' not in seen['afterUpdate']['labels'], seen
    assert any('RETITLED' in label
               for label in seen['afterUpdate']['labels']), seen
    assert seen['afterUpdate']['selected'] == '22', seen
    assert seen['afterUnregister']['offered'] == ['', '11'], seen
    assert seen['afterUnregister']['selected'] != '22', seen
    assert seen['afterSync'] == ['', '11', '33'], seen


def test_every_tab_selector_uses_the_shared_controller(_tmp):
    private = []
    for path in sorted((ROOT / 'dashboard' / 'sections').glob('*.js')):
        text = path.read_text(encoding='utf-8')
        if 'populateTabs' in text and 'bindTabSelector' not in text:
            private.append(path.relative_to(ROOT).as_posix())
    assert not private, private


def test_no_dashboard_export_is_unreferenced(_tmp):
    root = ROOT / 'dashboard'
    sources = {path: path.read_text(encoding='utf-8')
               for path in sorted(root.rglob('*.js'))}
    assert sources, 'no dashboard sources found'
    markup = (root / 'index.html').read_text(encoding='utf-8')
    unused = []
    for path, text in sources.items():
        declarations = re.finditer(
            r'^export\s+(?:async\s+)?(?:function|const|let|class)\s+'
            r'([A-Za-z_$][\w$]*)', blank_js_comments(text), re.M)
        for match in declarations:
            name = match.group(1)
            referenced = any(
                re.search(r'\b' + re.escape(name) + r'\b', other)
                for other_path, other in sources.items() if other_path != path)
            if referenced or re.search(
                    r'\b' + re.escape(name) + r'\b', markup):
                continue
            unused.append(f'{path.relative_to(ROOT).as_posix()}: {name}')
    assert not unused, f'exported but referenced nowhere: {unused}'


# A `test_` function in a non-suite module never runs, which is why the
# cases read their doubles from `tests/_dashnode_retry_control.py`
# instead of living beside them.
def test_windows_retries_one_outer_timeout_then_returns_success(_tmp):
    result, events, diagnostic = retry._controlled_run(
        'win32', (101, [retry._timeout(),
                        retry._outcome(-9, 'first', 'error')]),
        (202, [retry._outcome(0, 'second success', 'second stderr')]))
    assert result.stdout == 'second success', result
    assert [event[:2] for event in events] == [
        ('popen', 101), ('communicate', 101), ('kill', 101),
        ('communicate', 101), ('popen', 202), ('communicate', 202)], events
    assert diagnostic.count('\n') == 1, diagnostic
    expected = ('recovered', 'attempt 1', 'pid 101')
    assert all(part in diagnostic for part in expected), diagnostic


def test_two_windows_outer_timeouts_keep_both_attempt_records(_tmp):
    failure, events, _ = retry._controlled_run(
        'win32', (301, [retry._timeout(), retry._outcome(
            -9, 'complete one', '[phase] dashboard module imported\n')]),
        (302, [retry._timeout(), retry._outcome(
            -9, 'complete two', '[phase] dashboard call settled\n')]))
    expected = ('after 2 attempts', 'attempt 1:', 'attempt 2:', 'pid: 301',
                'pid: 302', "executable: '/node'", "argv: ('/node',",
                'outer timeout: 5s', 'kill issued: yes',
                'drain outcome: completed', 'return code: -9',
                'last phase: dashboard module imported',
                'last phase: dashboard call settled', 'complete one',
                'complete two')
    assert all(part in failure for part in expected), failure
    assert [event[0] for event in events].count('popen') == 2, events


def test_cumulative_byte_timeout_output_is_decoded_once(_tmp):
    failure, _, _ = retry._controlled_run('linux', (402, [
        retry._timeout(b'A\xe2'), retry._timeout(b'A\xe2\x82\xacB')]))
    assert "stdout: 'A€B'; stderr: ''" in failure and '�' not in failure
    invalid, _, _ = retry._controlled_run('linux', (403, [
        retry._timeout(b'A\xff'), retry._timeout(b'A\xffB')]))
    assert "stdout: 'A�B'; stderr: ''" in invalid, invalid


def test_windows_deterministic_failure_after_retry_does_not_retry(_tmp):
    failure, events, _ = retry._controlled_run(
        'win32', (501, [retry._outcome(
            7, 'deterministic output', 'deterministic error')]),
        (502, [retry._outcome(0, 'wrong retry')]))
    assert isinstance(failure, str), failure
    assert all(part in failure for part in (
        'deterministic output', 'deterministic error')), failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_retry_launch_waits_for_first_child_cleanup(_tmp):
    def finish(process):
        process.events.append(('drain-complete', process.pid))
        process.returncode = -9
        return '', ''

    def before_popen(pid, events):
        if pid == 602:
            assert ('drain-complete', 601) in events, events

    result, events, _ = retry._controlled_run(
        'win32', (601, [retry._timeout(), finish]),
        (602, [retry._outcome(0, 'success')]), before_popen=before_popen)
    assert result.stdout == 'success', result
    assert events.index(('drain-complete', 601)) < next(
        i for i, event in enumerate(events) if event[:2] == ('popen', 602))


def test_windows_does_not_retry_when_child_cleanup_cannot_finish(_tmp):
    failure, events, _ = retry._controlled_run(
        'win32', (701, [retry._timeout(), retry._timeout('partial', 'error')]),
        (702, [retry._outcome(0, 'wrong overlap')]))
    assert isinstance(failure, str), failure
    assert 'drain outcome: timed out' in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_windows_retries_when_timed_out_drain_is_reaped(_tmp):
    # The first child was fully cleaned up, so the "second child beside an
    # uncleaned first one" rationale for declining no longer holds and the
    # platform's one transient-stall retry must still be available.
    result, events, diagnostic = retry._controlled_run(
        'win32', (801, [retry._timeout(), retry._timeout('partial', 'error')],
                  {'wait_succeeds': True}),
        (802, [retry._outcome(0, 'recovered')]))
    assert [event[0] for event in events].count('popen') == 2, (result, events)
    assert [event[0] for event in events].count('wait') == 1, events
    assert result.stdout == 'recovered', result
    assert 'recovered' in diagnostic and 'drain timed out' in diagnostic, (
        diagnostic)


def test_windows_reader_cleanup_settles_before_pipe_close_and_reap(_tmp):
    # Cleanup settling now enables the retry instead of declining it, so the
    # ordering pin rides on the retried run and the second launch waits for it.
    result, events, _ = retry._controlled_run(
        'win32', (901, [retry._timeout(), retry._timeout('partial', 'error')],
                  {'wait_succeeds': True, 'held_readers': True}),
        (902, [retry._outcome(0, 'recovered')]))
    assert [event[0] for event in events].count('popen') == 2, (result, events)
    steps = [event[:2] for event in events]
    required = [
        ('kill', 901), ('reader-cancel', 'stdout'),
        ('reader-cancel', 'stderr'), ('reader-join', 'stdout'),
        ('reader-join', 'stderr'), ('pipe-close', 'stdout'),
        ('pipe-close', 'stderr'), ('wait', 901)]
    positions = [steps.index(step) for step in required]
    assert positions == sorted(positions), events
    budgets = [event[2] for event in events
               if event[0] in ('reader-join', 'wait')]
    assert budgets[0] > budgets[1] > budgets[2] >= 0, budgets
    assert result.stdout == 'recovered', result


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashbehaviour_')


if __name__ == '__main__':
    raise SystemExit(main())
