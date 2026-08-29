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
import subprocess
import sys
import threading
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashnode  # noqa: E402
import _util  # noqa: E402
from _jsread import blank_js_comments  # noqa: E402
from _repo import ROOT  # noqa: E402


_CONTENT_KEEPALIVE_HARNESS = _dashnode.DashboardNodeHarness(r"""
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

function eventTarget(listeners) {
  return { addListener(listener) { listeners.push(listener); } };
}

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
const context = vm.createContext({
  window: windowObject,
  chrome,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: '' },
  setTimeout: (callback, delay) => scheduled(timers, callback, delay),
  clearTimeout: (id) => clearScheduled(timers, id),
  setInterval: (callback, delay) => scheduled(intervals, callback, delay),
  clearInterval: (id) => clearScheduled(intervals, id),
  console: { log() {}, error() {} },
});

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


def test_stale_keepalive_disconnect_cannot_clobber_replacement_port(tmp):
    """A retired port callback cannot clear or replace the current port."""
    del tmp
    result = _dashnode.run_dashboard_node(_CONTENT_KEEPALIVE_HARNESS)
    actual = json.loads(result.stdout)
    assert actual == {
        'portCount': 2,
        'port2Pings': 1,
        'interval2Cleared': False,
        'retryTimers': 0,
    }, actual


_DASHBOARD_CONSUME_HARNESS = _dashnode.DashboardNodeHarness(r"""
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

(async () => {
  const tokenKey = 'daedalus-token';
  globalThis.localStorage = {
    getItem: key => key === tokenKey ? 'dashboard-token' : '',
    setItem: () => {},
  };
  globalThis.setTimeout = callback => { callback(); return 0; };

  let commandSent = false;
  globalThis.fetch = async (target, init = {}) => {
    const method = init.method || 'GET';
    if (method === 'PUT') {
      commandSent = true;
      return response(200, { ok: true, did: 'command-delivery' });
    }
    if (String(target).includes('consume=1')) {
      if (!commandSent) return response(200, { pending: true });
      return response(500, { error: 'consume failed' });
    }
    return response(200, {
      id: 'dashboard-command',
      deliveryId: 'command-delivery',
      resultGeneration: 'result-generation',
      result: 'fresh',
      error: null,
      world: 'page:cdp',
    });
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
  if (!rejected) throw new Error('failed consume surfaced as a successful read');
  phase('dashboard call settled');
  phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=2, arguments=(ROOT / 'dashboard' / 'api.js',))


def test_dashboard_failed_consume_is_not_a_success(tmp):
    """The dashboard must reject when its matching-result consume fails."""
    del tmp
    _dashnode.run_dashboard_node(_DASHBOARD_CONSUME_HARNESS)


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


def test_dashboard_labels_eval_world_as_a_channel(tmp):
    """Dashboard text presents `world` only as execution-channel metadata."""
    del tmp
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


def test_dashboard_never_builds_markup_from_a_value(tmp):
    """innerHTML in the dashboard is only ever a constant.

    Several catch paths concatenated an error string — or an
    extension-supplied reason — into innerHTML, so text the dashboard did not
    author could become markup. The rule enforced here is the one that keeps
    itself true: a value reaches the page through text nodes or the `h`
    helper, never through markup, so an assignment that is not a literal is a
    violation whatever the value happens to be today. `+=` never qualifies.
    """
    del tmp
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


_TAB_SELECTOR_HARNESS = _dashnode.DashboardNodeHarness(r"""
import { pathToFileURL } from 'node:url';

phase('dashboard harness started');
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
    if (child.tag === 'option' && child.value === this._value) this._value = '';
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
const api = { get: async () => tabs };
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
  initial, afterUpdate, afterUnregister, afterSync,
}));
phase('dashboard harness finished');
})().catch(leave);
""", bounded_steps=5, module=True, arguments=(
    ROOT / 'dashboard' / 'sections' / '_util.js',))


def _run_tab_selector_harness():
    result = _dashnode.run_dashboard_node(_TAB_SELECTOR_HARNESS)
    return json.loads(result.stdout)


def test_a_tab_selector_follows_every_lifecycle_event(tmp):
    """One selector, refreshed by all three events, offering only live tabs.

    Four sections refreshed on `tabs-synced` alone, so a tab that had been
    retitled or unregistered stayed offered — and selectable — until a full
    sync happened to arrive. A selection also survived unconditionally, which
    is how a command ended up aimed at a tab that no longer existed.
    """
    del tmp
    seen = _run_tab_selector_harness()
    assert seen['initial'] == ['', '11', '22'], seen

    # tab-updated refreshes, and a selection that is still on offer survives.
    assert '11  RETITLED' not in seen['afterUpdate']['labels'], seen
    assert any('RETITLED' in label for label in seen['afterUpdate']['labels']), seen
    assert seen['afterUpdate']['selected'] == '22', seen

    # tab-unregistered refreshes, and the selection does NOT survive its tab.
    assert seen['afterUnregister']['offered'] == ['', '11'], seen
    assert seen['afterUnregister']['selected'] != '22', seen

    # tabs-synced still refreshes, which is the one that always worked.
    assert seen['afterSync'] == ['', '11', '33'], seen


def test_every_tab_selector_uses_the_shared_controller(tmp):
    """No section keeps a private copy to drift out of step again."""
    del tmp
    private = []
    for path in sorted((ROOT / 'dashboard' / 'sections').glob('*.js')):
        text = path.read_text(encoding='utf-8')
        if 'populateTabs' in text and 'bindTabSelector' not in text:
            private.append(path.relative_to(ROOT).as_posix())
    assert not private, private


def test_no_dashboard_export_is_unreferenced(tmp):
    """A public module surface nothing imports is untested code.

    Three of them accumulated — getBinary, evalOn and debounce — and each was
    found the same way, by someone happening to search for the name. This is
    the search, run every time.
    """
    del tmp
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
            if referenced or re.search(r'\b' + re.escape(name) + r'\b', markup):
                continue
            unused.append(f'{path.relative_to(ROOT).as_posix()}: {name}')
    assert not unused, f'exported but referenced nowhere: {unused}'


class _ControlledReader:
    def __init__(self, name, native_id, events):
        self.name, self.native_id, self.events = name, native_id, events
        self.cancelled, self.finished = threading.Event(), threading.Event()

    def cancel(self):
        self.events.append(('reader-cancel', self.name))
        self.cancelled.set()

    def is_alive(self):
        return not self.finished.is_set()

    def join(self, timeout):
        self.events.append(('reader-join', self.name, timeout))
        if self.cancelled.is_set():
            self.finished.set()


class _ControlledPipe:
    def __init__(self, name, reader, events):
        self.name, self.reader, self.events = name, reader, events

    def close(self):
        self.events.append(('pipe-close', self.name))
        assert not self.reader.is_alive(), (
            f'{self.name} closed before its reader finished')


class _ControlledProcess:
    def __init__(self, pid, command, outcomes, events, *, wait_succeeds=False,
                 held_readers=False):
        self.pid, self.command = pid, command
        self.outcomes, self.events = list(outcomes), events
        self.wait_succeeds = wait_succeeds
        self.returncode = self.stdout = self.stderr = None
        if held_readers:
            self.stdout_thread = _ControlledReader('stdout', pid * 2, events)
            self.stderr_thread = _ControlledReader(
                'stderr', pid * 2 + 1, events)
            self.stdout = _ControlledPipe('stdout', self.stdout_thread, events)
            self.stderr = _ControlledPipe('stderr', self.stderr_thread, events)

    def communicate(self, timeout):
        self.events.append(('communicate', self.pid, timeout))
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            return outcome(self)
        kind, self.returncode, stdout, stderr = outcome
        if kind == 'timeout':
            raise subprocess.TimeoutExpired(
                self.command, timeout, output=stdout, stderr=stderr)
        return stdout, stderr

    def kill(self):
        self.events.append(('kill', self.pid))
        self.returncode = -9

    def wait(self, timeout):
        self.events.append(('wait', self.pid, timeout))
        if self.wait_succeeds:
            return self.returncode
        raise subprocess.TimeoutExpired(self.command, timeout)


def _controlled_run(platform, *specs, before_popen=None):
    pending, events, diagnostic = list(specs), [], StringIO()
    clock = iter(value / 10 for value in range(100))

    def popen(command, **_options):
        pid, outcomes, *wait_options = pending.pop(0)
        options = wait_options[0] if wait_options else {}
        if before_popen:
            before_popen(pid, events)
        events.append(('popen', pid, tuple(command)))
        return _ControlledProcess(pid, command, outcomes, events, **options)

    def cancel_reader(thread):
        thread.cancel()

    with patch.object(sys, 'platform', platform), \
            patch.object(_dashnode.shutil, 'which', return_value='/node'), \
            patch.object(_dashnode.subprocess, 'Popen', popen), \
            patch.object(_dashnode, '_cancel_windows_synchronous_io',
                         cancel_reader, create=True), \
            patch.object(_dashnode.time, 'monotonic', lambda: next(clock)), \
            redirect_stderr(diagnostic):
        try:
            outcome = _dashnode.run_dashboard_node(
                _dashnode.DashboardNodeHarness('', 0))
        except AssertionError as failure:
            outcome = str(failure)
    return outcome, events, diagnostic.getvalue()


def _timeout(stdout='', stderr=''):
    return 'timeout', None, stdout, stderr


def _result(code, stdout='', stderr=''):
    return 'result', code, stdout, stderr


def test_windows_retries_one_outer_timeout_then_returns_success(tmp):
    del tmp
    result, events, diagnostic = _controlled_run(
        'win32', (101, [_timeout(), _result(-9, 'first', 'error')]),
        (202, [_result(0, 'second success', 'second stderr')]))
    assert result.stdout == 'second success', result
    assert [event[:2] for event in events] == [
        ('popen', 101), ('communicate', 101), ('kill', 101),
        ('communicate', 101), ('popen', 202), ('communicate', 202)], events
    assert diagnostic.count('\n') == 1, diagnostic
    expected = ('recovered', 'attempt 1', 'pid 101')
    assert all(part in diagnostic for part in expected), diagnostic


def test_two_windows_outer_timeouts_keep_both_attempt_records(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (301, [_timeout(), _result(
            -9, 'complete one', '[phase] dashboard module imported\n')]),
        (302, [_timeout(), _result(
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


def test_non_windows_outer_timeout_does_not_retry(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'linux', (401, [_timeout(), _result(-9)]))
    assert failure.startswith('dashboard node outer timeout after 1 attempt')
    assert 'attempt 1:' in failure and 'pid: 401' in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_windows_deterministic_failure_after_retry_does_not_retry(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (501, [_result(
            7, 'deterministic output', 'deterministic error')]),
        (502, [_result(0, 'wrong retry')]))
    assert isinstance(failure, str), failure
    assert all(part in failure for part in (
        'deterministic output', 'deterministic error')), failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_retry_launch_waits_for_first_child_cleanup(tmp):
    del tmp

    def finish(process):
        process.events.append(('drain-complete', process.pid))
        process.returncode = -9
        return '', ''

    def before_popen(pid, events):
        if pid == 602:
            assert ('drain-complete', 601) in events, events

    result, events, _ = _controlled_run(
        'win32', (601, [_timeout(), finish]),
        (602, [_result(0, 'success')]), before_popen=before_popen)
    assert result.stdout == 'success', result
    assert events.index(('drain-complete', 601)) < next(
        i for i, event in enumerate(events) if event[:2] == ('popen', 602))


def test_windows_does_not_retry_when_child_cleanup_cannot_finish(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (701, [_timeout(), _timeout('partial', 'error')]),
        (702, [_result(0, 'wrong overlap')]))
    assert isinstance(failure, str), failure
    assert 'drain outcome: timed out' in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events


def test_windows_does_not_retry_after_timed_out_drain_is_reaped(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (801, [_timeout(), _timeout('partial', 'error')],
                  {'wait_succeeds': True}),
        (802, [_result(0, 'wrong retry')]))
    assert isinstance(failure, str), failure
    assert 'drain outcome: timed out' in failure, failure
    assert [event[0] for event in events].count('popen') == 1, events
    assert [event[0] for event in events].count('wait') == 1, events


def test_windows_reader_cleanup_settles_before_pipe_close_and_reap(tmp):
    del tmp
    failure, events, _ = _controlled_run(
        'win32', (901, [_timeout(), _timeout('partial', 'error')],
                  {'wait_succeeds': True, 'held_readers': True}),
        (902, [_result(0, 'wrong retry')]))
    assert failure.startswith('dashboard node outer timeout after 1 attempt')
    assert 'drain outcome: timed out' in failure, failure
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
    assert [event[0] for event in events].count('popen') == 1, events


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashbehaviour_')


if __name__ == '__main__':
    raise SystemExit(main())
