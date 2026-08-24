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
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsread import blank_js_comments  # noqa: E402
from _repo import ROOT  # noqa: E402


_CONTENT_KEEPALIVE_HARNESS = r"""
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

vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const firstProactive = timers.find((item) => item.delay === 4 * 60 * 1000);
firstProactive.callback();
const secondInterval = intervals[intervals.length - 1];
for (const listener of ports[0].disconnectListeners) listener();
secondInterval.callback();

process.stdout.write(JSON.stringify({
  portCount: ports.length,
  port2Pings: ports[1].messages.length,
  interval2Cleared: secondInterval.cleared,
  retryTimers: timers.filter((item) => item.delay === 500).length,
}));
"""


def test_stale_keepalive_disconnect_cannot_clobber_replacement_port(tmp):
    """A retired port callback cannot clear or replace the current port."""
    del tmp
    node = shutil.which('node')
    assert node, 'node is required to execute the content keep-alive lifecycle'
    result = subprocess.run(
        [node, '-e', _CONTENT_KEEPALIVE_HARNESS,
         str(ROOT / 'extension' / 'content.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    actual = json.loads(result.stdout)
    assert actual == {
        'portCount': 2,
        'port2Pings': 1,
        'interval2Cleared': False,
        'retryTimers': 0,
    }, actual


_DASHBOARD_CONSUME_HARNESS = r"""
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

  const source = fs.readFileSync(process.argv[1], 'utf8');
  const moduleUrl = 'data:text/javascript;base64,'
    + Buffer.from(source).toString('base64');
  const dashboard = await import(moduleUrl);
  let rejected = false;
  try {
    await dashboard.runCommand({
      type: 'cookies', id: 'dashboard-command', timeout: 1000,
    });
  } catch (error) {
    rejected = true;
    if (!String(error.message).includes('HTTP 500')) throw error;
  }
  if (!rejected) throw new Error('failed consume surfaced as a successful read');
})().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""


def test_dashboard_failed_consume_is_not_a_success(tmp):
    """The dashboard must reject when its matching-result consume fails."""
    node = shutil.which('node')
    assert node, 'node is required to execute the dashboard API boundary'
    result = subprocess.run(
        [node, '-e', _DASHBOARD_CONSUME_HARNESS,
         str(ROOT / 'dashboard' / 'api.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)


_DASHBOARD_WORLD_HARNESS = r"""
const fs = require('fs');

(async () => {
  const source = fs.readFileSync(process.argv[1], 'utf8');
  const moduleUrl = 'data:text/javascript;base64,'
    + Buffer.from(source).toString('base64');
  const dashboard = await import(moduleUrl);
  process.stdout.write(JSON.stringify([
    dashboard.formatEvalWorld('cdp'),
    dashboard.formatEvalWorld('page-main'),
    dashboard.formatEvalWorld('page:cdp'),
    dashboard.formatEvalWorld('extension'),
    dashboard.formatEvalWorld('module-main'),
  ]));
})().catch(error => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""


def test_dashboard_labels_eval_world_as_a_channel(tmp):
    """Dashboard text presents `world` only as execution-channel metadata."""
    del tmp
    node = shutil.which('node')
    assert node, 'node is required to execute the dashboard world formatter'
    result = subprocess.run(
        [node, '-e', _DASHBOARD_WORLD_HARNESS,
         str(ROOT / 'dashboard' / 'sections' / '_util.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
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


_TAB_SELECTOR_HARNESS = r"""
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
const { pathToFileURL } = await import('node:url');
const { bindTabSelector } = await import(pathToFileURL(process.argv[1]).href);

let tabs = [{ tabId: '11', title: 'first' }, { tabId: '22', title: 'second' }];
const listeners = [];
const select = new El('select');
const api = { get: async () => tabs };
const bus = { on: (fn) => listeners.push(fn) };

function emit(type) {
  for (const fn of listeners) fn({ type });
}
const settle = () => new Promise((r) => setTimeout(r, 0));

bindTabSelector(select, {
  getToken: () => 'tok', api, bus, placeholder: '(active tab)',
});
await settle();
const initial = select.options.map((o) => o.value);

select.value = '22';
tabs = [{ tabId: '11', title: 'first' }, { tabId: '22', title: 'RETITLED' }];
emit('tab-updated');
await settle();
const afterUpdate = {
  labels: select.options.map((o) => o.label),
  selected: select.value,
};

tabs = [{ tabId: '11', title: 'first' }];
emit('tab-unregistered');
await settle();
const afterUnregister = {
  offered: select.options.map((o) => o.value),
  selected: select.value,
};

tabs = [{ tabId: '11', title: 'first' }, { tabId: '33', title: 'third' }];
emit('tabs-synced');
await settle();
const afterSync = select.options.map((o) => o.value);

process.stdout.write(JSON.stringify({
  initial, afterUpdate, afterUnregister, afterSync,
}));
"""


def _run_tab_selector_harness():
    node = shutil.which('node')
    assert node, 'node is required to execute the dashboard tab selector'
    result = subprocess.run(
        [node, '--input-type=module', '--eval', _TAB_SELECTOR_HARNESS,
         str(ROOT / 'dashboard' / 'sections' / '_util.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dashbehaviour_')


if __name__ == '__main__':
    raise SystemExit(main())
