#!/usr/bin/env python3
"""Repository invariants that protect the public release.

Most tests read the tree. The storage-relay tests execute the shipped content
and page scripts in a Node VM with browser API fakes. The properties pinned
here are the ones a private deployment tends to leak: version drift, console
scripts that point at nothing, a default server URL baked into the extension,
and deployment-specific strings in shipped files.
"""
import contextlib
import http.server
import importlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
sys.path.insert(0, str(_util.ROOT))
CLI_MODULE = importlib.import_module('daedalus_cli.cli')

ROOT = _util.ROOT
EXTENSION_ROOT = ROOT / 'extension'


def test_extension_same_id_overlap_keeps_each_delivery_id(tmp):
    """Both completion orders preserve each command's server delivery id."""
    del tmp
    commands = [
        {'id': '_cookies', 'type': 'cookies', 'domain': 'owner-a',
         '_did': 'did-a'},
        {'id': '_cookies', 'type': 'cookies', 'domain': 'owner-b',
         '_did': 'did-b'},
    ]
    actual = {
        'a-first': _util.run_background_overlap(
            ROOT / 'extension' / 'background.js', commands,
            ['owner-a', 'owner-b']),
        'b-first': _util.run_background_overlap(
            ROOT / 'extension' / 'background.js', commands,
            ['owner-b', 'owner-a']),
    }
    expected = {
        'a-first': [
            {'id': '_cookies', 'owner': 'owner-a', 'deliveryId': 'did-a'},
            {'id': '_cookies', 'owner': 'owner-b', 'deliveryId': 'did-b'},
        ],
        'b-first': [
            {'id': '_cookies', 'owner': 'owner-b', 'deliveryId': 'did-b'},
            {'id': '_cookies', 'owner': 'owner-a', 'deliveryId': 'did-a'},
        ],
    }
    assert actual == expected, actual


_CDP_CALL_HARNESS = r"""
const [target, method, paramsText] = process.argv.slice(1);
const socket = new WebSocket(target);
const timer = setTimeout(() => {
  process.stderr.write('CDP response timed out\n');
  socket.close();
  process.exitCode = 1;
}, 10000);

socket.addEventListener('open', () => {
  socket.send(JSON.stringify({ id: 1, method, params: JSON.parse(paramsText) }));
});
socket.addEventListener('message', (event) => {
  const message = JSON.parse(String(event.data));
  if (message.id !== 1) return;
  clearTimeout(timer);
  if (message.error) {
    process.stderr.write(JSON.stringify(message.error) + '\n');
    process.exitCode = 1;
  } else {
    process.stdout.write(JSON.stringify(message.result || {}));
  }
  socket.close();
});
socket.addEventListener('error', () => {
  clearTimeout(timer);
  process.stderr.write('CDP websocket failed\n');
  process.exitCode = 1;
});
"""

_HOSTILE_EVAL_SCRIPT = r"""
(() => {
  try {
    const FORGED = 'FORGED-BY-PAGE';
    const forged = function () { return 'FORGED'; };
    // Descriptors are null-prototype: once `Object.prototype` carries a
    // `value` accessor, an ordinary descriptor literal inherits it and
    // `defineProperty` rejects the whole poison.
    const define = (target, key, descriptor) => Object.defineProperty(
      target, key, Object.assign({ __proto__: null }, descriptor));

    // Evaluator bindings: `eval`, `Function`, the four function-constructor
    // prototypes, both same-origin iframe access routes and `Worker`.
    const constructors = [
      Function,
      (async function () {}).constructor,
      (function* () {}).constructor,
      (async function* () {}).constructor,
    ];
    for (const constructor of constructors) {
      define(constructor.prototype, 'constructor', {
        configurable: true,
        value: forged,
        writable: true,
      });
    }
    const fakeWindow = { eval: forged, Function: forged };
    define(HTMLIFrameElement.prototype, 'contentWindow', {
      configurable: true,
      get() { return fakeWindow; },
    });
    define(HTMLIFrameElement.prototype, 'contentDocument', {
      configurable: true,
      get() { return { defaultView: fakeWindow }; },
    });
    const contentWindowFrame = document.createElement('iframe');
    const defaultViewFrame = document.createElement('iframe');
    document.body.append(contentWindowFrame, defaultViewFrame);
    void contentWindowFrame.contentWindow;
    void defaultViewFrame.contentDocument.defaultView;
    globalThis.eval = forged;
    globalThis.Function = forged;
    globalThis.Worker = forged;
    document.title = 'Hostile eval page';

    // Retrieval bindings. Promise resolution reads `constructor` and `then`
    // off page-writable prototypes and assimilates anything callable it finds
    // there, so an evaluator whose value rides back through page promise
    // machinery is forgeable even when its compilation is not.
    const poisonedThen = function (resolve) {
      if (typeof resolve === 'function') resolve(FORGED);
      return this;
    };
    function Poisoned() {}
    Poisoned[Symbol.species] = function (executor) {
      executor(function () {}, function () {});
      return this;
    };
    define(Promise.prototype, 'constructor', {
      configurable: true,
      value: Poisoned,
      writable: true,
    });
    const valueProtos = [Object.prototype, Number.prototype, String.prototype,
      Boolean.prototype, Array.prototype, Function.prototype, Error.prototype];
    for (const proto of [Promise.prototype].concat(valueProtos)) {
      define(proto, 'then', {
        configurable: true,
        value: poisonedThen,
        writable: true,
      });
    }
    for (const proto of valueProtos) {
      define(proto, Symbol.toPrimitive, {
        configurable: true,
        value: function () { return FORGED; },
        writable: true,
      });
    }
    define(Array.prototype, Symbol.iterator, {
      configurable: true,
      writable: true,
      value: function () {
        let spent = false;
        return {
          next() {
            const done = spent;
            spent = true;
            return { value: done ? undefined : FORGED, done };
          },
        };
      },
    });
    JSON.parse = function () { return FORGED; };
    JSON.stringify = function () { return '"' + FORGED + '"'; };

    // Accessors on every property name a result envelope is read through,
    // then `defineProperty` itself, both last so the poison above still ran
    // with working primitives.
    for (const name of ['r', 'e', 'ok', 'message', 'csp', 'ms',
      'value', 'title', 'result', 'world', 'code']) {
      define(Object.prototype, name, {
        configurable: true,
        get() { return FORGED; },
        set() {},
      });
    }
    Object.defineProperty = function (target) { return target; };
    Object.freeze = function (target) { return target; };
  } catch (error) {
    globalThis.__poisonError = (error && error.message) || 'poison failed';
  }
  globalThis.__evalPageReady = true;
})();
"""

_STRICT_CSP_EVAL_SCRIPT = r"""
globalThis.__dataUrlBlocks = 0;
globalThis.__evalBlocks = 0;
globalThis.__userSideEffects = 0;
document.addEventListener('securitypolicyviolation', (event) => {
  if (event.blockedURI === 'data') globalThis.__dataUrlBlocks++;
  if (event.blockedURI === 'eval') globalThis.__evalBlocks++;
});
document.title = 'Strict CSP eval page';
globalThis.__evalPageReady = true;
"""

_PERFORMANCE_POISON_EVAL_SCRIPT = r"""
performance.now = function () {
  throw new Error('page killed performance.now');
};
document.title = 'Performance poison eval page';
globalThis.__evalPageReady = true;
"""

_PLAIN_EVAL_SCRIPT = r"""
document.title = 'Plain eval page';
globalThis.__evalPageReady = true;
"""


class _EvalPageHandler(http.server.BaseHTTPRequestHandler):
    """Serve the real-page evaluator fixtures over one loopback origin."""

    def do_GET(self):
        pages = {
            '/hostile.html': (
                b'<title>loading</title><body><script src="/hostile.js"></script></body>',
                'text/html', None),
            '/hostile.js': (
                _HOSTILE_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/strict.html': (
                b'<title>loading</title><body><script src="/strict.js"></script></body>',
                'text/html', "default-src 'self'; script-src 'self'"),
            '/strict.js': (
                _STRICT_CSP_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/performance-poison.html': (
                b'<title>loading</title><body><script src="/performance-poison.js"></script></body>',
                'text/html', None),
            '/performance-poison.js': (
                _PERFORMANCE_POISON_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/plain.html': (
                b'<title>loading</title><body><script src="/plain.js"></script></body>',
                'text/html', None),
            '/plain.js': (
                _PLAIN_EVAL_SCRIPT.encode(), 'text/javascript', None),
        }
        fixture = pages.get(urllib.parse.urlsplit(self.path).path)
        if fixture is None:
            self.send_error(404)
            return
        body, content_type, csp = fixture
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        if csp:
            self.send_header('Content-Security-Policy', csp)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        del format, args


@contextlib.contextmanager
def _eval_page_server():
    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), _EvalPageHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _cdp_call(node, target, method, params):
    result = subprocess.run(
        [node, '-e', _CDP_CALL_HARNESS, target, method, json.dumps(params)],
        cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout or '{}')


def _cdp_eval(node, target, expression):
    response = _cdp_call(node, target, 'Runtime.evaluate', {
        'expression': expression,
        'awaitPromise': True,
        'returnByValue': True,
    })
    assert not response.get('exceptionDetails'), response
    return response.get('result', {}).get('value')


def _browser_version(browser):
    """What the browser calls itself, for a skip that has to be actionable.

    A leg that skips the real-browser tests says nothing useful unless it
    says which browser refused: the fixture works on one Chromium and not on
    whatever a runner image happens to ship, and that difference is the
    whole question.
    """
    try:
        reported = subprocess.run(
            [browser, '--version'], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as why:
        return f'{browser} (version unreadable: {type(why).__name__})'
    return f'{browser} ({reported.stdout.strip() or reported.stderr.strip()})'


def _browser_requirements():
    node = shutil.which('node')
    browser = next((path for name in (
        'chromium', 'chromium-browser', 'google-chrome',
        'google-chrome-stable', 'chrome')
        if (path := shutil.which(name))), None)
    if not node or not browser:
        _util.skip('Chromium and Node are required for the real-page eval test')
    websocket = subprocess.run(
        [node, '-e',
         "process.exit(typeof WebSocket === 'function' ? 0 : 1)"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    if websocket.returncode != 0:
        _util.skip('this Node runtime has no WebSocket client for CDP')
    return node, browser


_WORKER_READY_PROBE = (
    '(() => { try { return typeof loadConfig === "function" '
    '&& typeof ensureKeepAlive === "function" '
    '&& typeof startStream === "function"; } '
    'catch (_) { return false; } })()')

_WORKER_STATE_PROBE = (
    '(() => { try { return JSON.stringify({'
    'id: (typeof chrome !== "undefined" && chrome.runtime)'
    ' ? chrome.runtime.id : null,'
    'loadConfig: typeof loadConfig,'
    'ensureKeepAlive: typeof ensureKeepAlive,'
    'startStream: typeof startStream,'
    'version: typeof VERSION !== "undefined" ? VERSION '
    ': null}); } catch (e) { return "probe failed: " '
    '+ (e && e.message); } })()')


def _devtools_targets(port):
    with urllib.request.urlopen(
            f'http://127.0.0.1:{port}/json/list', timeout=2) as reply:
        return json.load(reply)


def _worker_targets(targets):
    """Every service worker that could be an extension's background worker.

    More than one extension can be loaded at once, and a CI runner's browser
    carries one of its own, so this is a list rather than the first match:
    which of them is this extension's is a question only the worker's own
    declarations answer, and DevTools happens to list the other one first.
    """
    return [item for item in targets
            if item.get('type') == 'service_worker'
            and item.get('url', '').endswith('/background.js')]


def _wait_for_devtools(profile, process):
    """Wait for the DevTools endpoint, the page, and a background worker.

    Everything this waits on is the browser starting, not the extension
    behaving, so an environment where it does not arrive skips rather than
    fails — see _real_extension_page for where that boundary sits.
    """
    port_file = Path(profile) / 'DevToolsActivePort'
    # A cold runner's first browser start is slower than every later one: the
    # ubuntu legs timed out here on the first browser test of the run and
    # reached the same browser without trouble in the ones after it.
    deadline = time.time() + 30
    seen = 'it never wrote a DevTools port'
    while time.time() < deadline:
        if process.poll() is not None:
            _util.skip('Chromium exited before DevTools became available')
        if port_file.exists():
            lines = port_file.read_text(encoding='utf-8').splitlines()
            if lines:
                port = lines[0]
                try:
                    targets = _devtools_targets(port)
                    page = next((item for item in targets
                                 if item.get('type') == 'page'), None)
                    workers = _worker_targets(targets)
                    if page and workers:
                        return page, workers, port
                    seen = (f'{len(targets)} targets on port {port}, '
                            f'a page: {page is not None}, '
                            f'background workers: {len(workers)}')
                except (OSError, ValueError) as why:
                    seen = f'listing its targets failed: {why}'
        time.sleep(0.05)
    # `raise` rather than `_util.skip(...)` at this one site. The helper is
    # annotated NoReturn and pylint honours it, but a reader — and at least
    # one scanner — sees a function that returns a triple on one path and
    # falls off the end on the other. The raise says what happens without
    # asking anyone to resolve an annotation in another module.
    raise _util.Skipped(
        'this browser never exposed the fixture page and an '
        f'extension service worker over DevTools — {seen}')


def _ready_worker(node, workers):
    """The worker among `workers` whose script declares this extension.

    Returns (target, reached, error). `reached` says whether any of them
    could be evaluated in at all, which is the line between a machine that
    cannot talk to a worker and an extension that did not load.

    What is asserted is that the worker's own script ran to the end: a script
    that threw defines none of these, and neither does another extension's
    worker. Waiting on keepaliveTimer instead waited on the async boot chain,
    and a worker that answers with the timer still unset is a different thing
    from a worker that never loaded — the caller configures the extension
    explicitly, so it does not need that chain to have finished.
    """
    reached = False
    error = 'no service worker target is listed'
    for candidate in workers:
        target = candidate['webSocketDebuggerUrl']
        try:
            if _cdp_eval(node, target, _WORKER_READY_PROBE) is True:
                return target, True, None
            reached = True
            error = 'the worker answered without its declarations'
        except AssertionError as failure:
            error = f'evaluating in the worker failed: {failure}'
    return None, reached, error


def _worker_state(node, target):
    """What one worker says about itself, for a failure that names which."""
    try:
        return _cdp_eval(node, target, _WORKER_STATE_PROBE)
    except AssertionError as why:
        return f'could not be read back: {why}'


@contextlib.contextmanager
def _real_extension_page(tmp, bridge_url, token, page_url,
                         extension_root=None, extra_extensions=()):
    """Yield (node, page target, tab id) for a real page under the extension.

    Every test that reaches a real browser comes through here, so this is
    where the one boundary lives. Getting a usable browser is an environment
    question: the binaries have to exist, and Chromium has to start, expose
    DevTools and run the unpacked extension's service worker. None of that
    is a claim about this repository, so where it does not happen the test
    skips with the reason — a browser that cannot be launched, cannot run an
    MV3 service worker, or is refused a profile is a property of the machine.

    From the configuration step on, the browser has demonstrably worked and
    everything asserted is the extension's own behaviour, so those stay hard
    failures. Loading the fixture page is on that side of the line: the page
    and the script that sets __evalPageReady are files in this repository,
    served by this suite's own origin, so a page that never reports ready is
    a defect here rather than a property of the machine. Skipping the environment costs no coverage of the extension
    source itself: this suite also runs background.js, content.js and page.js
    under Node, which does not need a browser and fails outright if that
    source is broken.
    """
    node, browser = _browser_requirements()
    profile = Path(tmp) / 'chromium-profile'
    extension = (extension_root or EXTENSION_ROOT).resolve()
    # Ours last: a browser that carries an extension of its own lists that
    # one first, which is the order the CI legs see.
    loaded = ','.join(str(Path(item).resolve())
                      for item in (*extra_extensions, extension))
    process = subprocess.Popen(
        [
            browser,
            '--headless=new',
            '--no-sandbox',
            '--disable-gpu',
            '--disable-dev-shm-usage',
            '--no-first-run',
            '--no-default-browser-check',
            '--remote-allow-origins=*',
            '--remote-debugging-port=0',
            '--disable-extensions-except=' + loaded,
            '--load-extension=' + loaded,
            '--user-data-dir=' + str(profile),
            'about:blank',
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    try:
        page, workers, devtools_port = _wait_for_devtools(profile, process)
        page_target = page['webSocketDebuggerUrl']
        # Load the page before waiting on the worker. An MV3 worker goes
        # dormant on its own after about thirty idle seconds, and evaluating
        # in it over CDP does not wake it — on a slow machine the worker
        # that DevTools listed a moment ago can already be gone, and the
        # wait then polls a target nothing will ever answer. The content
        # script's keepalive port is an event the worker listens for, so
        # loading the page is what revives it, exactly as an ordinary
        # browsing session does.
        _cdp_call(node, page_target, 'Page.navigate', {'url': page_url})
        deadline = time.time() + 30
        last_error = 'no evaluation was attempted'
        answered = False
        worker_target = None
        while time.time() < deadline:
            worker_target, reached, error = _ready_worker(node, workers)
            answered = answered or reached
            if worker_target:
                break
            last_error = error
            try:
                workers = _worker_targets(_devtools_targets(devtools_port))
            except (OSError, ValueError) as why:
                last_error = f'listing DevTools targets failed: {why}'
            # Every attempt spawns one node process per candidate to
            # speak CDP, so this polls twice a second rather than twenty
            # times. Polling faster bought nothing on the runner that was
            # failing here, and a two-core machine pays for every spawn.
            time.sleep(0.5)
        if worker_target is None:
            # Two different states wear the same timeout. A worker that
            # answers is one this machine can reach, so what it says about
            # itself is the extension's own behaviour and stays a failure —
            # that is the case the injected-fault test drives. A worker that
            # never answers at all has not been reached: the browser lists a
            # target and refuses every debugger connection to it, which is a
            # property of the machine and skips like the launch steps do.
            if answered:
                states = [_worker_state(node, item['webSocketDebuggerUrl'])
                          for item in workers]
                raise AssertionError(
                    'the extension service worker never finished loading: '
                    'DevTools exposed its target and the fixture page was '
                    f'loaded to wake it. Last: {last_error}. Worker states: '
                    f'{states}')
            # Never reached at all: the target vanished, or every
            # debugger connection to it was refused. Both say the browser
            # did not get as far as running the extension, which is where
            # the environment boundary sits.
            _util.skip(
                'this browser never let the extension worker be reached '
                f'over the debugger: {_browser_version(browser)} — '
                f'{last_error}')

        storage = json.dumps({
            'daedalus-token': token,
            'daedalus-server': bridge_url,
        })
        configure = (
            '(async () => { await chrome.storage.local.set(' + storage
            + '); await loadConfig(); ensureKeepAlive(); stopStream(); '
            + 'startStream(); '
            + 'return config.token === ' + json.dumps(token)
            + ' && config.serverUrl === ' + json.dumps(bridge_url)
            + '; })()')
        deadline = time.time() + 30
        while True:
            try:
                configured = _cdp_eval(node, worker_target, configure)
            except AssertionError as failure:
                configured = f'the call failed: {failure}'
            if configured is True or time.time() >= deadline:
                break
            # An MV3 worker stops when it goes idle and the next event starts
            # a fresh one, so the worker that answered a moment ago can be
            # gone by the time it is configured. Look it up again rather than
            # reporting the browser's own lifecycle as this extension's
            # failure to take its configuration.
            time.sleep(0.5)
            try:
                workers = _worker_targets(_devtools_targets(devtools_port))
            except (OSError, ValueError):
                workers = []
            replacement, _reached, _error = _ready_worker(node, workers)
            if replacement:
                worker_target = replacement
        assert configured is True, (
            f'extension worker did not load test configuration ({configured!r}'
            f'). Worker state: {_worker_state(node, worker_target)}')
        _cdp_call(node, page_target, 'Page.navigate', {'url': page_url})

        deadline = time.time() + 15
        while time.time() < deadline:
            if _cdp_eval(
                    node, page_target,
                    'globalThis.__evalPageReady === true') is True:
                break
            time.sleep(0.25)  # one node process per attempt; see above
        else:
            raise AssertionError(
                'the fixture page never set __evalPageReady: ' + page_url)

        _cdp_eval(node, worker_target, 'registerAllTabs()')
        deadline = time.time() + 15
        last_tabs = None
        while time.time() < deadline:
            status, tabs = _util.get_json(
                bridge_url + '/tabs?' + urllib.parse.urlencode({'token': token}))
            last_tabs = (status, tabs)
            match = next((item for item in tabs
                          if page_url in item.get('url', '')), None) \
                if status == 200 and isinstance(tabs, list) else None
            if match:
                yield node, page_target, match['tabId']
                return
            time.sleep(0.05)
        raise AssertionError(
            f'extension did not register the eval fixture tab: {last_tabs!r}')
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _real_ext_command(bridge_url, token, cmd_id, payload):
    """Send a typed extension command and return its delivered result."""
    body = {'token': token, 'tab': 'extension', 'id': cmd_id, **payload}
    status, raw = _util.request(bridge_url + '/command', 'PUT', body=body)
    assert status == 200, (status, raw)
    sent = json.loads(raw)
    deadline = time.time() + 20
    query = urllib.parse.urlencode({'token': token, 'tab': 'extension'})
    while time.time() < deadline:
        result_status, result = _util.get_json(bridge_url + '/result?' + query)
        if (result_status == 200 and isinstance(result, dict)
                and result.get('deliveryId') == sent.get('did')):
            return result
        time.sleep(0.05)
    raise AssertionError(f'{cmd_id!r} did not return its delivery result')


def _real_eval(bridge_url, token, tab_id, cmd_id, code):
    status, raw = _util.request(
        bridge_url + '/command', 'PUT', body={
            'token': token,
            'tab': tab_id,
            'id': cmd_id,
            'code': code,
        })
    assert status == 200, (status, raw)
    sent = json.loads(raw)
    deadline = time.time() + 20
    query = urllib.parse.urlencode({'token': token, 'tab': tab_id})
    while time.time() < deadline:
        result_status, body = _util.get_json(
            bridge_url + '/result?' + query)
        if (result_status == 200 and isinstance(body, dict)
                and body.get('deliveryId') == sent.get('did')):
            generation = body.get('resultGeneration')
            if generation:
                consume = urllib.parse.urlencode({
                    'token': token,
                    'tab': tab_id,
                    'consume': '1',
                    'expected': generation,
                })
                consumed_status, _consumed = _util.get_json(
                    bridge_url + '/result?' + consume)
                assert consumed_status == 200, consumed_status
            return body
        time.sleep(0.05)
    raise AssertionError(f'eval {cmd_id!r} did not return its delivery result')


def _hostile_eval_matrix(tmp):
    """Return five eval shapes from the fully poisoned real page."""
    token = 'cdpevaltok'
    matrix = {}
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            page_url = pages + '/hostile.html'
            with _real_extension_page(
                    tmp, bridge_url, token, page_url) as (node, page, tab_id):
                poison = _cdp_eval(
                    node, page, 'globalThis.__poisonError || "none"')
                assert poison == 'none', poison
                cases = (
                    ('expression', '2 + 2'),
                    ('function-body', 'const value = 4; return value'),
                    ('top-level-await', 'await 0, 4'),
                    ('object-result', '({ value: 4 })'),
                    ('page-promise', 'await Promise.resolve(4)'),
                )
                for label, code in cases:
                    actual = _real_eval(
                        bridge_url, token, tab_id, 'cdp-' + label, code)
                    matrix[label] = actual
    return matrix


def test_hostile_page_eval_matrix_has_descriptive_channels_only(tmp):
    """A page-selected value never gains a trust claim from its channel.

    The fixture poisons eval, Function, all four function-constructor
    prototypes, both same-origin iframe routes, Worker, and the page's Promise
    machinery. The last case deliberately routes a primitive through that
    hostile Promise rather than merely placing `await` beside a direct value.
    It does not claim that Promise-prototype poison alone changes a direct
    object; that narrower reproduction does not occur.
    """
    matrix = _hostile_eval_matrix(tmp)
    for label, actual in matrix.items():
        assert 'result' in actual, (label, actual)
        world = actual.get('world')
        assert isinstance(world, str) and world, (label, actual)
        rendered = CLI_MODULE._format_eval_world(world)
        assert rendered == f'channel={world}', (label, actual, rendered)
        assert 'privileged' not in rendered, (label, actual, rendered)
        assert 'untrusted' not in rendered, (label, actual, rendered)
    assert matrix['page-promise'].get('result') == 'FORGED-BY-PAGE', matrix


def test_main_world_transport_failure_and_genuine_null_are_distinct(tmp):
    """A failed injection is an error while evaluated `null` is a value."""
    token = 'mainworldtok'
    actual = {}
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            cases = (
                ('performance-poison', '/performance-poison.html', '2 + 2'),
                ('genuine-null', '/plain.html', 'null'),
            )
            for label, path, code in cases:
                case_tmp = Path(tmp) / label
                case_tmp.mkdir()
                with _real_extension_page(
                        case_tmp, bridge_url, token,
                        pages + path) as (_node, _page, tab_id):
                    actual[label] = _real_eval(
                        bridge_url, token, tab_id, label, code)

    poisoned = actual['performance-poison']
    assert poisoned.get('result') is None, poisoned
    assert 'page killed performance.now' in (poisoned.get('error') or ''), poisoned
    assert poisoned.get('world') == 'page-main', poisoned

    genuine_null = actual['genuine-null']
    assert 'result' in genuine_null, genuine_null
    assert genuine_null['result'] is None, genuine_null
    assert genuine_null.get('error') is None, genuine_null
    assert genuine_null.get('world') == 'page-main', genuine_null


def test_a_worker_that_loads_broken_is_a_failure_not_a_skip(tmp):
    """A broken extension must not be reported as a broken machine.

    The fixture skipped when the worker did not come up ready, so a real MV3
    defect passed CI in silence. The Node-based tests do not cover what that
    skip hides: they run the same source against fakes with no
    chrome.runtime.id, so a fault conditioned on being a real worker is
    invisible there too — which is why this mutation is exactly that fault.

    A worker that answers is one the browser has reached, so what it says
    about itself is the extension's own behaviour and fails. A worker that
    cannot be reached at all is still the machine's business and skips.
    """
    _browser_requirements()  # skips honestly where no browser exists
    broken = Path(tmp) / 'broken-extension'
    shutil.copytree(EXTENSION_ROOT, broken)
    worker = broken / 'background.js'
    # Appended, and conditioned on being a real MV3 worker: the script still
    # installs and answers, and what breaks is the extension's own state.
    # A top-level throw instead makes Chrome retire the registration, which
    # is indistinguishable from a machine that cannot reach the worker at
    # all — the fault has to be one the extension survives loading.
    worker.write_text(
        worker.read_text(encoding='utf-8')
        + "\nif (chrome.runtime.id) { startStream = undefined; }\n",
        encoding='utf-8')

    token = 'workerboottok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            reported = None
            try:
                with _real_extension_page(
                        tmp, bridge_url, token, pages + '/plain.html',
                        extension_root=broken):
                    raise AssertionError(
                        'the fixture yielded with a worker that cannot boot')
            except _util.Skipped as skipped:
                raise AssertionError(
                    'a broken extension was reported as an environment skip: '
                    + str(skipped)) from skipped
            except AssertionError as failure:
                reported = str(failure)
            assert reported and 'service worker' in reported, reported


def test_a_page_that_never_reports_ready_is_a_failure_not_a_skip(tmp):
    """Past the fixture's own boundary, a page that will not load is a bug.

    By the time readiness is awaited, the browser has started, exposed
    DevTools, booted the extension's service worker and taken its
    configuration — the fixture's own docstring says everything from there
    on is the extension's behaviour and stays a hard failure. The fixture
    page and the script that sets __evalPageReady are repository files, so a
    skip there hides a defect in them behind an environment excuse.

    The check itself has to distinguish the two skips: a machine with no
    browser skips honestly, and only a skip raised AT the readiness step is
    the defect under test.
    """
    token = 'readyboundarytok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            # Served as a 404, so nothing ever sets __evalPageReady.
            page_url = pages + '/never-ready.html'
            reported = None
            try:
                with _real_extension_page(
                        tmp, bridge_url, token, page_url):
                    raise AssertionError(
                        'the fixture yielded a page that never reported ready')
            except _util.Skipped as skipped:
                if 'never finished loading the fixture page' in str(skipped):
                    raise AssertionError(
                        'page readiness was reported as an environment skip: '
                        + str(skipped)) from skipped
                raise
            except AssertionError as failure:
                reported = str(failure)
            assert reported and '__evalPageReady' in reported, reported


def test_the_fixture_reaches_its_own_worker_past_another_extension(tmp):
    """A second extension's background worker is not mistaken for ours.

    Every ubuntu CI leg runs a browser that carries an extension of its own,
    so DevTools lists two service workers whose URL ends in /background.js —
    and it lists the other one first. A fixture that took the first match
    attached to it and polled it for declarations it does not have, which is
    what the legs reported: a worker answering with none of them, or nothing
    at all once that worker's target had stopped.
    """
    _browser_requirements()  # skips honestly where no browser exists
    decoy = Path(tmp) / 'decoy-extension'
    decoy.mkdir()
    (decoy / 'manifest.json').write_text(json.dumps({
        'manifest_version': 3,
        'name': 'decoy',
        'version': '1.0',
        'background': {'service_worker': 'background.js'},
    }), encoding='utf-8')
    (decoy / 'background.js').write_text(
        'globalThis.__decoyWorker = true;\n', encoding='utf-8')

    token = 'decoyworkertok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            with _real_extension_page(
                    tmp, bridge_url, token, pages + '/plain.html',
                    extra_extensions=(decoy,)) as (_node, _page, tab_id):
                # Reaching a value back through the bridge is what proves the
                # configured worker was this extension's: the decoy has no
                # stream to carry the command.
                answer = _real_eval(bridge_url, token, tab_id, 'decoy-eval',
                                    'return 1 + 1')
                assert answer.get('error') is None, answer
                assert answer.get('result') == 2, answer


def test_a_hotfix_replays_on_a_page_that_forbids_eval_and_blob(tmp):
    """A stored hotfix reaches a page whose CSP refuses the page relay.

    Replay used to run in the page: the page's own `eval`, then a blob
    <script>. A CSP with neither `unsafe-eval` nor `blob:` — github.com's,
    and this fixture's strict page — refuses both, and the blocked blob load
    reported nothing back, so the fix simply never applied. The background
    can reach the page by the same route ordinary eval uses when the page
    refuses dynamic compilation, so replay goes through it.
    """
    token = 'hotfixcsptok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            with _real_extension_page(
                    tmp, bridge_url, token,
                    pages + '/plain.html') as (node, page, _tab_id):
                stored = _real_ext_command(bridge_url, token, 'store-csp-fix', {
                    'type': 'store-hotfix',
                    'fixId': 'csp-fix',
                    'code': 'globalThis.__hotfixApplied = true;',
                    'permanent': True,
                })
                assert stored.get('error') is None, stored

                # A load of the strict page replays it: script-src 'self',
                # so neither page-side path the old relay had is available.
                _cdp_call(node, page, 'Page.navigate',
                          {'url': pages + '/strict.html'})
                deadline = time.time() + 20
                applied = None
                while time.time() < deadline:
                    if _cdp_eval(node, page,
                                 'globalThis.__evalPageReady === true') is True:
                        applied = _cdp_eval(
                            node, page, 'globalThis.__hotfixApplied === true')
                        if applied is True:
                            break
                    time.sleep(0.1)
                assert applied is True, (
                    'the hotfix never applied on a page whose CSP forbids '
                    f'eval and blob scripts (last read: {applied!r})')


def test_strict_csp_page_uses_cdp_once_after_source_free_preflight(tmp):
    """A source-free CSP probe falls back before the command runs once."""
    token = 'cspevaltok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            page_url = pages + '/strict.html'
            with _real_extension_page(
                    tmp, bridge_url, token, page_url) as (node, page, tab_id):
                actual = _real_eval(
                    bridge_url, token, tab_id, 'csp-eval',
                    'globalThis.__userSideEffects++; '
                    'return globalThis.__userSideEffects')
                state = _cdp_eval(node, page, '({'
                                  'blocks: globalThis.__dataUrlBlocks,'
                                  'evalBlocks: globalThis.__evalBlocks,'
                                  'sideEffects: globalThis.__userSideEffects'
                                  '})')
                assert actual.get('error') is None, actual
                assert actual.get('result') == 1, actual
                assert actual.get('world') == 'cdp', actual
                # The constant probe is the only page evaluation CSP rejects;
                # submitted source goes to CDP once and no data URL is tried.
                assert state['blocks'] == 0, state
                assert state['evalBlocks'] == 1, state
                assert state['sideEffects'] == 1, state


def test_cdp_eval_throw_is_terminal(tmp):
    """An exception is returned once and never retried on another evaluator."""
    token = 'cdpthrowtok'
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with _eval_page_server() as pages:
            page_url = pages + '/strict.html'
            with _real_extension_page(
                    tmp, bridge_url, token, page_url) as (node, page, tab_id):
                actual = _real_eval(
                    bridge_url, token, tab_id, 'cdp-throw',
                    'globalThis.__throwSideEffects = '
                    '(globalThis.__throwSideEffects || 0) + 1; '
                    'throw new Error("callable failed")')
                side_effects = _cdp_eval(
                    node, page, 'globalThis.__throwSideEffects')
                assert 'callable failed' in (actual.get('error') or ''), actual
                assert actual.get('world') == 'cdp', actual
                assert side_effects == 1, side_effects


_EVAL_RELAY_OVERLAP_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, contentPath, pagePath, orderText, mode = 'overlap',
  relayHostname = '', cdpText = ''] = process.argv.slice(1);
const cdpEnabled = cdpText === '1' || cdpText === 'midflight';
const cdpFailsMidFlight = cdpText === 'midflight';
let cdpSideEffects = 0;
const completionOrder = JSON.parse(orderText);
let scriptingCalls = 0;
let injectionShape = '';
const backgroundListeners = [];
const contentListeners = [];
const windowListeners = [];
const windowMessages = [];
const postedResults = [];
const evalResolvers = {};
const slowSignals = [];
let relaySequence = 0;

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function eventTarget(listeners = null) {
  return {
    addListener(listener) {
      if (listeners) listeners.push(listener);
    },
  };
}

const backgroundChrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'eval-token',
        'daedalus-server': 'test-bridge',
      }),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query(_query, callback) {
      const tabs = [{ id: 7, url: '', title: 'Page' }];
      if (callback) {
        callback(tabs);
        return undefined;
      }
      return Promise.resolve(tabs);
    },
    get: async (tabId) => ({
      id: tabId,
      url: '',
      title: 'Page',
    }),
    async sendMessage(_tabId, message) {
      for (const listener of contentListeners) listener(message);
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => {
      if (!cdpEnabled) throw new Error('debugger unavailable in relay test');
    },
    detach: async () => {},
    // Stand-in for the V8 inspector channel. The marker records that channel;
    // it makes no claim about a value the submitted source obtained from page
    // state or page promise machinery.
    sendCommand: async (_target, method, params) => {
      if (method !== 'Runtime.evaluate') return {};
      if (cdpFailsMidFlight) {
        // The inspector started the source and then went away. Nothing can
        // prove the side effect did not happen, so no other evaluator may run.
        cdpSideEffects++;
        throw new Error('inspector detached mid-evaluation');
      }
      try {
        return { result: { value: await vm.runInNewContext(params.expression, {}) } };
      } catch (error) {
        return { exceptionDetails: { exception: { description: String(error) } } };
      }
    },
  },
  scripting: {
    async executeScript(injection) {
      scriptingCalls++;
      if (mode === 'injection-shapes') {
        if (injection.func.name === '_canUseMainWorldEval') {
          return [{ result: true }];
        }
        if (injectionShape === 'reject') {
          throw new Error('executeScript rejected');
        }
        if (injectionShape === 'empty') return [];
        if (injectionShape === 'frame-error') {
          return [{ error: 'frame exception' }];
        }
        if (injectionShape === 'missing-result') return [{}];
        if (injectionShape === 'bare-null') return [{ result: null }];
        if (injectionShape === 'genuine-null') {
          return [{ result: { r: null, ms: 1 } }];
        }
        if (injectionShape === 'eval-exception') {
          return [{ result: { e: 'operator exception', ms: 1 } }];
        }
        if (injectionShape === 'page-substitution') {
          return [{ result: 'PAGE-SUBSTITUTED' }];
        }
        throw new Error('unknown injection shape ' + injectionShape);
      }
      if (mode !== 'preemption' && mode !== 'poisoned') {
        throw new Error('scripting unavailable in relay overlap test');
      }
      relayContext.__injectionArgs = injection.args || [];
      const source = '(' + injection.func.toString()
        + ')(...__injectionArgs)';
      const result = await vm.runInContext(source, relayContext);
      delete relayContext.__injectionArgs;
      return [{ result }];
    },
  },
  runtime: {
    onMessage: eventTarget(backgroundListeners),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
};

const backgroundContext = vm.createContext({
  chrome: backgroundChrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.includes('/slow')) {
      // Never settles on its own. The only way out is the AbortSignal, which
      // is the whole question: a relay whose abort reaches nothing leaves this
      // request running until its timeout.
      slowSignals.push(init.signal);
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          const error = new Error('aborted');
          error.name = 'AbortError';
          reject(error);
        });
      });
    }
    if (url.endsWith('/result') && init.method === 'POST') {
      postedResults.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    if (url.includes('/stream?')) return response(503, { error: 'disabled' });
    return response(200, { ok: true });
  },
  crypto: { randomUUID: () => 'relay-' + (++relaySequence) },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout: () => 1,
  clearTimeout() {},
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});

const windowObject = {
  addEventListener(type, listener) {
    if (type === 'message') windowListeners.push(listener);
  },
  postMessage(data) {
    windowMessages.push(data);
    for (const listener of [...windowListeners]) {
      listener({ source: windowObject, data });
    }
  },
};

const relayChrome = {
  runtime: {
    lastError: null,
    onMessage: eventTarget(contentListeners),
    sendMessage(message) {
      for (const listener of backgroundListeners) {
        listener(message, { tab: { id: 7 } }, () => {});
      }
    },
    connect() {
      return {
        name: 'keepalive',
        postMessage() {},
        disconnect() {},
        onDisconnect: eventTarget(),
      };
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

const documentObject = {
  head: { appendChild() {} },
  documentElement: { appendChild() {} },
  addEventListener() {},
  removeEventListener() {},
  createElement() {
    return {
      remove() {},
      set onload(_listener) {},
      set onerror(_listener) {},
    };
  },
};

const relayContext = vm.createContext({
  window: windowObject,
  chrome: relayChrome,
  document: documentObject,
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { hostname: relayHostname },
  performance,
  evalResolvers,
  Blob,
  URL,
  Uint8Array,
  ArrayBuffer,
  TextEncoder,
  atob,
  btoa,
  setTimeout: () => 1,
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, error() {} },
});

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function waitFor(predicate, label) {
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (predicate()) return;
    await delay();
  }
  throw new Error('timed out waiting for ' + label);
}

async function run() {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), backgroundContext);
  await vm.runInContext('loadConfig()', backgroundContext);
  vm.runInContext(fs.readFileSync(contentPath, 'utf8'), relayContext);
  vm.runInContext(fs.readFileSync(pagePath, 'utf8'), relayContext);

  if (mode === 'injection-shapes') {
    const shapes = ['reject', 'empty', 'frame-error', 'missing-result',
      'bare-null', 'genuine-null', 'eval-exception', 'page-substitution'];
    const outcomes = {};
    for (const shape of shapes) {
      injectionShape = shape;
      backgroundContext.command = {
        id: '_eval',
        type: 'eval',
        code: shape === 'genuine-null' ? 'null' : '2 + 2',
        chromeTab: 7,
        _did: 'did-' + shape,
      };
      const before = postedResults.length;
      await vm.runInContext('dispatchCommand(command)', backgroundContext);
      await waitFor(
        () => postedResults.length === before + 1,
        'injection result for ' + shape);
      const posted = postedResults[before];
      outcomes[shape] = {
        hasResult: Object.prototype.hasOwnProperty.call(posted, 'result'),
        result: posted.result === undefined ? null : posted.result,
        error: posted.error === undefined ? null : posted.error,
        world: posted.world || null,
      };
    }
    return outcomes;
  }

  if (mode === 'poisoned') {
    // A hostile page replaces both evaluator primitives before the command
    // arrives. Everything the injected MAIN-world function resolves — `eval`
    // and `Function` alike — comes from these page-owned globals.
    relayContext.eval = (source) => 'FORGED-EVAL:' + source;
    relayContext.Function = function () {
      return function () { return 'FORGED-FUNCTION'; };
    };
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: '2 + 2',
      chromeTab: 7,
      _did: 'did-poisoned',
    };
    await vm.runInContext('dispatchCommand(command)', backgroundContext);
    await waitFor(() => postedResults.length === 1, 'poisoned eval result');
    return {
      result: postedResults[0].result,
      world: postedResults[0].world,
      deliveryId: postedResults[0]._did || null,
      scriptingCalls,
    };
  }

  if (mode === 'midflight') {
    relayContext.eval = (source) => 'FORGED-EVAL:' + source;
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: '2 + 2',
      chromeTab: 7,
      _did: 'did-midflight',
    };
    await vm.runInContext('dispatchCommand(command)', backgroundContext);
    await waitFor(() => postedResults.length === 1, 'mid-flight eval result');
    return {
      result: postedResults[0].result === undefined
        ? null : postedResults[0].result,
      error: postedResults[0].error,
      world: postedResults[0].world || null,
      cdpSideEffects,
      scriptingCalls,
    };
  }

  if (mode === 'marker') {
    windowObject.addEventListener('message', (event) => {
      const message = event.data;
      if (!message || message.direction !== 'daedalus-eval') return;
      windowObject.postMessage({
        direction: 'daedalus-eval-result',
        id: message.id,
        relayId: message.relayId,
        r: 'FORGED',
        world: 'scripting',
        hostname: 'cdp',
      });
    });
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: 'await new Promise(() => {})',
      _did: 'did-marker',
    };
    await vm.runInContext('dispatchCommand(command)', backgroundContext);
    await waitFor(() => postedResults.length === 1, 'forged page result');
    return {
      result: postedResults[0].result,
      world: postedResults[0].world,
      deliveryId: postedResults[0]._did || null,
    };
  }

  if (mode === 'gm-abort') {
    relayContext.abortProbe = {};
    vm.runInContext(
      'abortProbe.handle = window.GM.xmlhttpRequest({'
      + ' url: "https://example.com/slow",'
      + ' onload: function() { abortProbe.load = true; },'
      + ' onerror: function() { abortProbe.error = true; },'
      + ' ontimeout: function() { abortProbe.timeout = true; },'
      + ' onabort: function() { abortProbe.abort = true; },'
      + '})', relayContext);
    await waitFor(() => slowSignals.length === 1, 'the relayed fetch to start');
    const inFlight = vm.runInContext('_fetchControllers.size', backgroundContext);
    vm.runInContext('abortProbe.handle.abort()', relayContext);
    vm.runInContext('abortProbe.handle.abort()', relayContext);
    await waitFor(() => slowSignals[0].aborted, 'the fetch to be cancelled');
    await delay();
    await delay();
    return {
      inFlight,
      aborted: slowSignals[0].aborted,
      onabort: Boolean(relayContext.abortProbe.abort),
      onload: Boolean(relayContext.abortProbe.load),
      onerror: Boolean(relayContext.abortProbe.error),
      ontimeout: Boolean(relayContext.abortProbe.timeout),
      abortMessages: windowMessages.filter(
        (message) => message.handler === 'abortRequest').length,
      controllers: vm.runInContext('_fetchControllers.size', backgroundContext),
    };
  }

  if (mode === 'preemption') {
    windowObject.addEventListener('message', (event) => {
      const message = event.data;
      if (!message || message.direction !== 'daedalus-eval') return;
      windowObject.postMessage({
        direction: 'daedalus-eval-result',
        id: message.id,
        relayId: message.relayId,
        r: 'FORGED',
      });
    });
    backgroundContext.command = {
      id: '_eval',
      type: 'eval',
      code: 'await new Promise((resolve) => {'
        + ' evalResolvers.legit = () => resolve("LEGIT");'
        + ' })',
      _did: 'did-legit',
    };
    const execution = vm.runInContext(
      'dispatchCommand(command)', backgroundContext);
    await waitFor(() => Boolean(evalResolvers.legit), 'evaluation to start');
    evalResolvers.legit();
    await execution;
    await delay();
    return {
      pageEvalMessages: windowMessages.filter(
        (message) => message.direction === 'daedalus-eval').length,
      results: postedResults.map((item) => ({
        result: item.result,
        deliveryId: item._did || null,
      })),
    };
  }

  const commands = ['owner-a', 'owner-b'].map((owner) => ({
    id: '_eval',
    type: 'eval',
    code: 'await new Promise((resolve) => {'
      + ' evalResolvers["' + owner + '"] = () => resolve("' + owner + '");'
      + ' })',
    _did: owner === 'owner-a' ? 'did-a' : 'did-b',
  }));
  backgroundContext.commands = commands;
  vm.runInContext('dispatchCommand(commands[0])', backgroundContext);
  vm.runInContext('dispatchCommand(commands[1])', backgroundContext);
  await waitFor(
    () => Object.keys(evalResolvers).length === 2,
    'both page evaluations to start');

  const evalMessages = windowMessages.filter(
    (message) => message.direction === 'daedalus-eval');
  const firstRelay = evalMessages[0] && evalMessages[0].relayId;
  for (const listener of backgroundListeners) {
    listener({
      type: 'result', id: '_eval', relayId: firstRelay,
      result: 'wrong-tab', error: null, world: '',
    }, { tab: { id: 8 } }, () => {});
  }
  await delay();

  for (const owner of completionOrder) {
    evalResolvers[owner]();
    await waitFor(
      () => postedResults.some((item) => item.result === owner),
      'page result for ' + owner);
  }

  windowObject.postMessage({
    direction: 'daedalus-eval-result',
    id: '_eval',
    relayId: 'not-pending',
    r: 'unrecognised',
  });
  await delay();

  return {
    relayIds: evalMessages.map((message) => message.relayId || null),
    results: postedResults.map((item) => ({
      result: item.result,
      deliveryId: item._did || null,
    })),
  };
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


_CDP_HANDLE_LIFECYCLE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const backgroundPath = process.argv[1];
const released = [];
const postedResults = [];
const finalAwaitPromise = [];
const timers = [];
let pendingResolve;

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function eventTarget() {
  return { addListener() {} };
}

const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': 'lifecycle-token',
        'daedalus-server': 'test-bridge',
      }),
      set: async () => {},
      remove: async () => {},
    },
    onChanged: eventTarget(),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    query(_query, callback) {
      const tabs = [{ id: 7, url: '', title: 'Page' }];
      if (callback) {
        callback(tabs);
        return undefined;
      }
      return Promise.resolve(tabs);
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
    attach: async () => {},
    detach: async () => {},
    sendCommand: async (_target, method, params) => {
      if (method === 'Runtime.releaseObject') {
        released.push(params.objectId);
        if (params.objectId === 'pending-original' && pendingResolve) {
          const resolve = pendingResolve;
          pendingResolve = null;
          setImmediate(() => resolve({ result: { objectId: 'pending-late' } }));
        }
        return {};
      }
      if (method === 'Runtime.evaluate') {
        if (params.expression.startsWith('typeof (function')) {
          return {
            result: { objectId: 'compile-result' },
            exceptionDetails: {
              text: 'compile failed',
              exception: {
                objectId: 'compile-exception',
                description: 'compile failed',
              },
            },
          };
        }
        finalAwaitPromise.push(params.awaitPromise);
        if (params.expression.includes('throw-case')) {
          return {
            result: { objectId: 'throw-result' },
            exceptionDetails: {
              text: 'throw failed',
              exception: {
                objectId: 'throw-exception',
                description: 'throw failed',
              },
            },
          };
        }
        if (params.expression.includes('reject-case')) {
          return {
            result: {
              objectId: 'reject-original',
              subtype: 'promise',
            },
          };
        }
        return { result: { value: 1 } };
      }
      if (method === 'Runtime.awaitPromise') {
        if (params.promiseObjectId === 'reject-original') {
          return {
            result: { objectId: 'reject-result' },
            exceptionDetails: {
              text: 'promise rejected',
              exception: {
                objectId: 'reject-exception',
                description: 'promise rejected',
              },
            },
          };
        }
        if (params.promiseObjectId === 'pending-original') {
          return new Promise((resolve) => { pendingResolve = resolve; });
        }
      }
      if (method === 'Runtime.callFunctionOn') {
        return { result: { value: 'settled' } };
      }
      return {};
    },
  },
  scripting: { executeScript: async () => [{ result: false }] },
  runtime: {
    onMessage: eventTarget(),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: { onAlarm: eventTarget(), create() {} },
};

const context = vm.createContext({
  chrome,
  fetch: async (target, init = {}) => {
    const url = String(target);
    if (url.endsWith('/result') && init.method === 'POST') {
      postedResults.push(JSON.parse(init.body));
      return response(200, { ok: true });
    }
    if (url.includes('/stream?')) return new Promise(() => {});
    return response(200, { ok: true });
  },
  crypto: { randomUUID: () => 'lifecycle-id' },
  AbortController,
  TextDecoder,
  URL,
  performance,
  btoa,
  setTimeout(callback, ms) {
    const timer = { callback, ms, active: true };
    timers.push(timer);
    return timers.length;
  },
  clearTimeout(id) {
    if (timers[id - 1]) timers[id - 1].active = false;
  },
  setInterval: () => 1,
  clearInterval() {},
  console: { log() {}, warn() {}, error() {} },
});

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function runEval(id, code) {
  context.command = { id, code, tabId: '7', _did: id };
  await vm.runInContext(
    '_evalViaCdp({...command, _execution: _executionContext(command)}, 7)',
    context);
}

(async () => {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context);
  await delay();
  vm.runInContext('_cdpSessions[7] = true', context);

  await runEval('compile', 'return compile-case');
  await runEval('throw', 'throw-case');
  await runEval('reject', 'reject-case');

  context.pendingRemote = {
    objectId: 'pending-original',
    subtype: 'promise',
  };
  const pending = vm.runInContext('_cdpSettle(7, pendingRemote)', context);
  await delay();
  const timer = timers.find((item) => item.active && item.ms === 10000);
  const pendingHasTimeout = Boolean(timer);
  if (timer) {
    timer.active = false;
    timer.callback();
    try { await pending; } catch (_) {}
    await delay();
    await delay();
  }

  process.stdout.write(JSON.stringify({
    released: [...new Set(released)].sort(),
    finalAwaitPromise,
    pendingHasTimeout,
    resultWorlds: postedResults.map((item) => item.world),
  }));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def _run_cdp_handle_lifecycle():
    node = shutil.which('node')
    assert node, 'node is required to execute the CDP lifecycle harness'
    result = subprocess.run(
        [node, '-e', _CDP_HANDLE_LIFECYCLE_HARNESS,
         str(EXTENSION_ROOT / 'background.js')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_eval_relay_overlap(order):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval relay'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(ROOT / 'extension' / 'background.js'),
         str(ROOT / 'extension' / 'content.js'),
         str(ROOT / 'extension' / 'page.js'), json.dumps(order)],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_eval_same_tab_preemption():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'preemption'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_gm_abort():
    node = shutil.which('node')
    assert node, 'node is required to execute the GM relay'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'gm-abort'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_a_gm_request_handle_can_actually_cancel_its_fetch(tmp):
    """`abort()` cancelled nothing: it was an empty function.

    The handle GM.xmlhttpRequest returns was `{ abort: function() {} }`, so a
    caller that stopped caring about a slow request had no way to say so. The
    fetch ran to completion or to its timeout in the service worker, holding
    the relay entry and the connection, and the page's callbacks fired for a
    response nobody was waiting for.

    All three scripts run here — page, content script and service worker —
    because the cancellation has to cross both hops to reach the
    AbortController, and a relay that drops it at either one looks identical
    from the page.
    """
    del tmp
    outcome = _run_gm_abort()
    assert outcome['inFlight'] == 1, outcome
    assert outcome['aborted'] is True, outcome
    # Exactly one terminal callback, and it is the abort one: a load or error
    # arriving afterwards must find nothing to call.
    assert outcome['onabort'] is True, outcome
    assert outcome['onload'] is False, outcome
    assert outcome['onerror'] is False, outcome
    assert outcome['ontimeout'] is False, outcome
    # Two abort() calls, one message: idempotent, and the second finds the
    # request already gone rather than telling the worker about a fetch it is
    # no longer running.
    assert outcome['abortMessages'] == 1, outcome
    # The worker keeps no controller for a request that is over.
    assert outcome['controllers'] == 0, outcome


def _run_eval_relay_marker(hostname):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'marker', hostname],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_eval_after_cdp_fails_mid_flight():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'midflight', '', 'midflight'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_eval_with_poisoned_page_globals(cdp_available):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'poisoned', '',
         '1' if cdp_available else '0'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def _run_main_world_injection_shapes():
    node = shutil.which('node')
    assert node, 'node is required to execute the extension eval path'
    result = subprocess.run(
        [node, '-e', _EVAL_RELAY_OVERLAP_HARNESS,
         str(EXTENSION_ROOT / 'background.js'),
         str(EXTENSION_ROOT / 'content.js'),
         str(EXTENSION_ROOT / 'page.js'), '[]', 'injection-shapes'],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_main_world_injection_result_shapes_are_explicit(tmp):
    """Every transport shape differs from a valid evaluated `null`."""
    del tmp
    actual = _run_main_world_injection_shapes()
    assert actual == {
        'reject': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: executeScript rejected',
            'world': 'page-main'},
        'empty': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: no result frame',
            'world': 'page-main'},
        'frame-error': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: frame exception',
            'world': 'page-main'},
        'missing-result': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: result frame has no result',
            'world': 'page-main'},
        'bare-null': {
            'hasResult': True, 'result': None,
            'error': 'MAIN-world eval failed: no result envelope',
            'world': 'page-main'},
        'genuine-null': {
            'hasResult': True, 'result': None, 'error': None,
            'world': 'page-main'},
        'eval-exception': {
            'hasResult': False, 'result': None,
            'error': 'operator exception', 'world': 'page-main'},
        'page-substitution': {
            'hasResult': True, 'result': 'PAGE-SUBSTITUTED', 'error': None,
            'world': 'page-main'},
    }, actual


def test_page_replaced_evaluators_use_injection_before_cdp(tmp):
    """A source-free probe keeps ordinary eval on the injection channel."""
    del tmp
    without_cdp = _run_eval_with_poisoned_page_globals(False)
    assert without_cdp == {
        'result': 'FORGED-EVAL:2 + 2',
        'world': 'page-main',
        'deliveryId': 'did-poisoned',
        'scriptingCalls': 2,
    }, without_cdp

    with_cdp = _run_eval_with_poisoned_page_globals(True)
    assert with_cdp == {
        'result': 'FORGED-EVAL:2 + 2',
        'world': 'page-main',
        'deliveryId': 'did-poisoned',
        'scriptingCalls': 2,
    }, with_cdp


def test_cdp_failure_after_dispatch_never_reruns_the_source(tmp):
    """Once the inspector has the source, no other evaluator may run it.

    Falling back after a dispatched evaluation would execute a command's side
    effects a second time, so a mid-flight inspector failure has to surface as
    an error rather than as a page-influenced answer.
    """
    del tmp
    actual = _run_eval_after_cdp_fails_mid_flight()
    assert actual['cdpSideEffects'] == 1, actual
    assert actual['scriptingCalls'] == 1, actual
    assert actual['result'] is None, actual
    # The error still names the channel that executed the command.
    assert actual['world'] == 'cdp', actual
    assert 'inspector detached mid-evaluation' in (actual['error'] or ''), actual


def test_cdp_eval_releases_every_remote_handle_in_held_sessions(tmp):
    """Compile, throw, reject, and pending paths release every CDP handle."""
    del tmp
    actual = _run_cdp_handle_lifecycle()
    assert actual == {
        'released': [
            'compile-exception',
            'compile-result',
            'pending-late',
            'pending-original',
            'reject-exception',
            'reject-original',
            'reject-result',
            'throw-exception',
            'throw-result',
        ],
        'finalAwaitPromise': [False, False, False],
        'pendingHasTimeout': True,
        'resultWorlds': ['cdp', 'cdp', 'cdp'],
    }, actual


def test_eval_relay_same_id_overlap_uses_bounded_invocation_ids(tmp):
    """Eval results retain delivery ids and unknown relay ids are ignored."""
    del tmp
    actual = {
        'a-first': _run_eval_relay_overlap(['owner-a', 'owner-b']),
        'b-first': _run_eval_relay_overlap(['owner-b', 'owner-a']),
    }
    assert actual == {
        'a-first': {
            'relayIds': ['relay-1', 'relay-2'],
            'results': [
                {'result': 'owner-a', 'deliveryId': 'did-a'},
                {'result': 'owner-b', 'deliveryId': 'did-b'},
            ],
        },
        'b-first': {
            'relayIds': ['relay-1', 'relay-2'],
            'results': [
                {'result': 'owner-b', 'deliveryId': 'did-b'},
                {'result': 'owner-a', 'deliveryId': 'did-a'},
            ],
        },
    }, actual


def test_same_tab_page_cannot_preempt_direct_eval_result(tmp):
    """A page-forged relay result cannot win a direct eval invocation."""
    del tmp
    actual = _run_eval_same_tab_preemption()
    assert actual == {
        'pageEvalMessages': 0,
        'results': [{'result': 'LEGIT', 'deliveryId': 'did-legit'}],
    }, actual


def test_page_eval_relay_world_is_namespaced_and_not_page_overridable(tmp):
    """Reserved hostnames and a forged marker stay in the page namespace."""
    del tmp
    hostnames = ('cdp', 'page-main', 'extension', 'page', 'relay.test')
    actual = {
        hostname: _run_eval_relay_marker(hostname)
        for hostname in hostnames
    }
    assert actual == {
        hostname: {
            'result': 'FORGED',
            'world': f'page:{hostname}',
            'deliveryId': 'did-marker',
        }
        for hostname in hostnames
    }, actual


_EXTENSION_RESULT_BOUNDARY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [backgroundPath, scenario] = process.argv.slice(1);
const changeListeners = [];
const detachListeners = [];
const sentMessages = [];
const timers = [];
const requests = [];
const resultPayloads = [];
const rules = [];
const createdTabs = [];
const uploadedData = [];
const windowTabs = [
  { id: 7, windowId: 3, active: true, url: 'about:blank#active' },
  { id: 8, windowId: 3, active: false, url: 'about:blank#target' },
];
const activations = [];
const messageListeners = [];
const cookieJar = [];
const removeCalls = [];
const storageStore = {
  'daedalus-token': 'initial-token',
  'daedalus-server': 'https://initial.example.com',
};
let captureResolver;
let tabQueryResolver;
let nextTimerId = 0;
let resultAttempts = 0;
let attachCalls = 0;
let detachCalls = 0;

// chrome.storage.local hands back a structured clone, so a reader that has not
// written yet cannot see another writer's in-flight mutation.
function copy(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function eventTarget(listeners = null) {
  return {
    addListener(listener) {
      if (listeners) listeners.push(listener);
    },
  };
}

function schedule(callback, delay) {
  const timer = {
    id: ++nextTimerId,
    callback,
    delay,
    cleared: false,
  };
  timers.push(timer);
  if (scenario === 'route' && (delay === 300 || delay === 600)) {
    setImmediate(() => {
      if (!timer.cleared) callback();
    });
  }
  return timer.id;
}

function clearScheduled(id) {
  const timer = timers.find((candidate) => candidate.id === id);
  if (timer) timer.cleared = true;
}

const chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const out = {};
        for (const key of keys) {
          if (key in storageStore) out[key] = copy(storageStore[key]);
        }
        return out;
      },
      set: async (entries) => {
        for (const key of Object.keys(entries)) {
          storageStore[key] = copy(entries[key]);
        }
      },
      remove: async (keys) => {
        for (const key of keys) delete storageStore[key];
      },
    },
    onChanged: eventTarget(changeListeners),
  },
  tabs: {
    onUpdated: eventTarget(),
    onCreated: eventTarget(),
    onRemoved: eventTarget(),
    create: async (details) => {
      createdTabs.push(details);
      return { id: 100 + createdTabs.length, windowId: 1, url: details.url };
    },
    query: async (query) => {
      if (scenario === 'screenshot-target') {
        return windowTabs
          .filter((tab) =>
            (query.active === undefined || tab.active === query.active)
            && (query.windowId === undefined || tab.windowId === query.windowId))
          .map((tab) => ({ ...tab }));
      }
      if (scenario === 'route' && Object.keys(query).length === 0) {
        return new Promise((resolve) => {
          tabQueryResolver = resolve;
        });
      }
      return [{ id: 7, url: 'https://page.example.com' }];
    },
    get: async (tabId) => {
      const known = windowTabs.find((tab) => tab.id === tabId);
      if (known) return { ...known, title: 'Page' };
      return {
        id: tabId,
        windowId: 3,
        url: 'https://page.example.com',
        title: 'Page',
      };
    },
    update: async (tabId, changes) => {
      if (changes && changes.active) {
        activations.push(tabId);
        for (const tab of windowTabs) tab.active = tab.id === tabId;
      }
      const updated = windowTabs.find((tab) => tab.id === tabId);
      return updated ? { ...updated } : { id: tabId, windowId: 3 };
    },
    sendMessage: async (_tabId, message) => {
      sentMessages.push(message);
    },
    captureVisibleTab: async () => {
      if (scenario === 'screenshot-target') {
        // A capture returns whatever is ACTIVE in the window, which is the
        // whole point: naming a tab does not select it.
        const active = windowTabs.find((tab) => tab.active);
        return 'data:image/png;base64,' + btoa('captured:' + (active && active.id));
      }
      if (scenario !== 'route') return 'data:image/png;base64,AA==';
      return new Promise((resolve) => {
        captureResolver = resolve;
      });
    },
  },
  scripting: {
    executeScript: async () => {
      throw new Error('scripting unavailable in residual relay test');
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(detachListeners),
    attach: async () => {
      attachCalls++;
      if (scenario !== 'net-capture') {
        throw new Error('debugger unavailable in residual relay test');
      }
      // Attempt 1 models a tab another client already owns; attempt 2 attaches
      // but fails to enable the domain.
      if (attachCalls === 1) throw new Error('Another debugger is already attached');
    },
    detach: async () => {
      detachCalls++;
    },
    sendCommand: async (_target, method) => {
      if (method === 'Network.enable' && attachCalls === 2) {
        throw new Error('Network.enable failed');
      }
      return {};
    },
  },
  cookies: {
    getAll: async () => cookieJar.map((cookie) => ({ ...cookie })),
    remove: async (details) => {
      removeCalls.push(details);
      // Chrome matches a partitioned cookie only when the partition is named,
      // and answers null when nothing matched -- which is the whole bug: the
      // caller counted a removal that never happened.
      const partition = JSON.stringify(details.partitionKey || null);
      const at = cookieJar.findIndex((cookie) =>
        cookie.name === details.name
        && JSON.stringify(cookie.partitionKey || null) === partition);
      if (at === -1) return null;
      const [gone] = cookieJar.splice(at, 1);
      return { name: gone.name };
    },
  },
  declarativeNetRequest: {
    getSessionRules: async () => rules.map((rule) => ({ ...rule })),
    updateSessionRules: async (change) => {
      for (const rule of change.addRules) {
        if (rules.some((existing) => existing.id === rule.id)) {
          throw new Error('Duplicate rule ID ' + rule.id);
        }
      }
      // Removal is honoured, not ignored: what the unblock scenario asserts
      // is which rules are STILL installed afterwards.
      for (const id of change.removeRuleIds || []) {
        const at = rules.findIndex((existing) => existing.id === id);
        if (at !== -1) rules.splice(at, 1);
      }
      rules.push(...change.addRules);
    },
  },
  runtime: {
    onMessage: eventTarget(messageListeners),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
};

// A body handed out one chunk at a time, so the harness can see how much of
// it the relay actually pulled before deciding. A response that reports its
// size only at the end cannot tell a bounded read apart from a full read
// followed by a size check.
const CHUNK_BYTES = 1024 * 1024;
let streamPlan = null;

function streamingResponse(chunkCount) {
  let handed = 0;
  streamPlan = { chunkCount, handed: 0, cancelled: false };
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    url: 'https://big.example.com/blob',
    headers: { forEach() {} },
    body: {
      getReader() {
        return {
          async read() {
            if (handed >= chunkCount) return { done: true, value: undefined };
            handed += 1;
            streamPlan.handed = handed;
            return { done: false, value: new Uint8Array(CHUNK_BYTES) };
          },
          async cancel() { streamPlan.cancelled = true; },
        };
      },
    },
  };
}

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.startsWith('https://big.example.com/')) {
    return streamingResponse(Number(new URL(url).searchParams.get('chunks')));
  }
  if (url.endsWith('/upload') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    requests.push({
      kind: 'upload', url, token: payload.token, id: payload.id,
    });
    uploadedData.push(payload.data);
    if (scenario === 'screenshot-reject') {
      return response(400, { error: 'invalid path component' });
    }
    return response(200, { path: 'capture.png', size: 4 });
  }
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    resultPayloads.push(payload);
    requests.push({
      kind: 'result', url, token: payload.token, id: payload.id,
      error: payload.error,
    });
    resultAttempts++;
    if (scenario === 'route' && resultAttempts === 1) {
      return response(503, { error: 'retry' });
    }
    return response(200, { ok: true });
  }
  if (url.includes('/stream?')) return response(503, { error: 'disabled' });
  return response(200, { ok: true });
}

let relaySequence = 0;

// One contextified worker. A second one models the service worker Chrome
// restarts after idle suspension: fresh script state, same browser-side stores.
function makeContext() {
  return vm.createContext({
    chrome,
    fetch: bridgeFetch,
    crypto: { randomUUID: () => 'relay-' + (++relaySequence) },
    AbortController,
    TextDecoder,
    URL,
    performance,
    btoa,
    setTimeout: schedule,
    clearTimeout: clearScheduled,
    setInterval: schedule,
    clearInterval: clearScheduled,
    console: { log() {}, warn() {}, error() {} },
  });
}

const context = makeContext();

function delay() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function waitFor(predicate, label) {
  for (let attempt = 0; attempt < 1000; attempt++) {
    if (predicate()) return;
    await delay();
  }
  throw new Error('timed out waiting for ' + label);
}

async function runCapacity() {
  context.prefill = Array.from({ length: 1000 }, (_unused, index) => ({
    id: 'existing-' + index,
    _did: 'did-existing-' + index,
  }));
  const relayIds = vm.runInContext(
    "prefill.map((command) => _registerEvalRelay("
      + "_executionContext(command), '7'))",
    context);
  context.nextCommand = {
    id: 'new-at-capacity',
    type: 'eval',
    code: '42',
    chromeTab: 7,
    _did: 'did-new-at-capacity',
  };
  await vm.runInContext('dispatchCommand(nextCommand)', context);
  context.firstRelay = relayIds[0];
  const first = vm.runInContext(
    "_takeEvalRelay(firstRelay, '7')", context);
  return {
    firstId: first && first.id,
    sentMessages: sentMessages.length,
    results: requests.filter((item) => item.kind === 'result'),
  };
}

async function runExpiry() {
  context.slowCommand = {
    id: 'slow-eval',
    _did: 'did-slow-eval',
  };
  const relayId = vm.runInContext(
    "_registerEvalRelay(_executionContext(slowCommand), '7')", context);
  const expiry = timers.find((timer) => timer.delay === 300000);
  if (!expiry) throw new Error('missing 300000 ms relay expiry');
  expiry.callback();
  expiry.callback();
  await delay();
  context.expiredRelay = relayId;
  return {
    stillPending: Boolean(vm.runInContext(
      "_takeEvalRelay(expiredRelay, '7')", context)),
    results: requests.filter((item) => item.kind === 'result'),
  };
}

async function runRouteSnapshot() {
  context.screenshotCommand = {
    id: 'route-snapshot',
    type: 'screenshot',
    _did: 'did-route-snapshot',
  };
  const execution = vm.runInContext(
    'dispatchCommand(screenshotCommand)', context);
  context.blockCommand = {
    id: 'block-route-snapshot',
    type: 'block-requests',
    pattern: '*://media.example.com/*',
    _did: 'did-block-route-snapshot',
  };
  const blockExecution = vm.runInContext(
    'dispatchCommand(blockCommand)', context);
  await waitFor(
    () => Boolean(captureResolver) && Boolean(tabQueryResolver),
    'side operations to start');
  for (const listener of changeListeners) {
    listener({
      'daedalus-token': { newValue: 'replacement-token' },
      'daedalus-server': {
        newValue: 'https://replacement.example.com',
      },
    }, 'local');
  }
  captureResolver('data:image/png;base64,AA==');
  await execution;
  tabQueryResolver([{ id: 7 }]);
  await blockExecution;
  return {
    requests,
    excludedRequestDomains: rules[0]
      ? rules[0].condition.excludedRequestDomains
      : null,
  };
}

async function runScreenshotTarget() {
  context.screenshotCommand = {
    id: 'targeted',
    type: 'screenshot',
    tabId: 8,
    _did: 'did-targeted',
  };
  await vm.runInContext('dispatchCommand(screenshotCommand)', context);
  return {
    captured: uploadedData.length
      ? Buffer.from(uploadedData[0], 'base64').toString() : null,
    activeAfter: (windowTabs.find((tab) => tab.active) || {}).id,
    activations,
    posted: resultPayloads.map((item) => ({
      tabUrl: item.result && item.result.tabUrl, error: item.error,
    })),
  };
}

async function runScreenshotReject() {
  context.screenshotCommand = {
    id: 'bad/id',
    type: 'screenshot',
    _did: 'did-bad-id',
  };
  await vm.runInContext('dispatchCommand(screenshotCommand)', context);
  return {
    uploads: requests.filter((item) => item.kind === 'upload').length,
    posted: resultPayloads.map((item) => ({
      result: item.result === undefined ? '<absent>' : item.result,
      error: item.error,
    })),
  };
}

async function runNetCapture() {
  const outcomes = [];
  for (const step of ['attach-fails', 'enable-fails', 'succeeds']) {
    context.captureCommand = {
      id: 'net-' + step,
      type: 'net-capture',
      tabId: 7,
      _did: 'did-net-' + step,
    };
    await vm.runInContext('dispatchCommand(captureCommand)', context);
    const posted = resultPayloads[resultPayloads.length - 1];
    outcomes.push({ step, result: posted.result, error: posted.error });
  }
  // Chrome detaches us (DevTools opened, target crashed): the capture is over
  // whether or not anything told the worker to stop it.
  for (const listener of detachListeners) listener({ tabId: 7 });
  context.captureCommand = {
    id: 'net-after-detach',
    type: 'net-capture',
    tabId: 7,
    _did: 'did-net-after-detach',
  };
  await vm.runInContext('dispatchCommand(captureCommand)', context);
  const posted = resultPayloads[resultPayloads.length - 1];
  outcomes.push({ step: 'after-detach', result: posted.result, error: posted.error });
  return { outcomes, attachCalls, detachCalls };
}

async function runHotfixRace() {
  context.storeCommands = ['fix-a', 'fix-b'].map((fixId) => ({
    id: 'store-' + fixId,
    type: 'store-hotfix',
    fixId,
    code: 'console.log("' + fixId + '")',
    _did: 'did-store-' + fixId,
  }));
  await vm.runInContext(
    'Promise.all([dispatchCommand(storeCommands[0]),'
    + ' dispatchCommand(storeCommands[1])])', context);
  const stored = storageStore['daedalus-hotfixes'] || { fixes: [] };
  return {
    posted: resultPayloads.map((item) => ({
      result: item.result, error: item.error,
    })),
    storedIds: stored.fixes.map((fix) => fix.id).sort(),
  };
}

async function runBlockRuleRestart() {
  context.blockCommand = {
    id: 'block-first',
    type: 'block-requests',
    pattern: '*://a.example.com/*',
    tabId: 7,
    _did: 'did-block-first',
  };
  await vm.runInContext('dispatchCommand(blockCommand)', context);

  // A restarted worker re-reads the shipped script with a zeroed counter while
  // the session rules it installed earlier are still present.
  const restarted = makeContext();
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), restarted);
  await vm.runInContext('loadConfig()', restarted);
  restarted.blockCommand = {
    id: 'block-after-restart',
    type: 'block-requests',
    pattern: '*://b.example.com/*',
    tabId: 7,
    _did: 'did-block-after-restart',
  };
  await vm.runInContext('dispatchCommand(blockCommand)', restarted);

  // Two adds in flight at once must not settle on one id either.
  restarted.concurrentCommands = ['c', 'd'].map((name) => ({
    id: 'block-' + name,
    type: 'block-requests',
    pattern: '*://' + name + '.example.com/*',
    tabId: 7,
    _did: 'did-block-' + name,
  }));
  await vm.runInContext(
    'Promise.all([dispatchCommand(concurrentCommands[0]),'
    + ' dispatchCommand(concurrentCommands[1])])', restarted);

  return {
    posted: resultPayloads.map((item) => ({
      ruleId: item.result && item.result.ruleId, error: item.error,
    })),
    installedIds: rules.map((rule) => rule.id),
  };
}

async function runUnblockZero() {
  // Three rules already installed, as an operator would have.
  rules.push({ id: 9001 }, { id: 9002 }, { id: 9003 });
  context.unblockCommand = {
    id: 'unblock-zero',
    type: 'unblock-requests',
    ruleId: 0,
    _did: 'did-unblock-zero',
  };
  await vm.runInContext('dispatchCommand(unblockCommand)', context);
  return {
    installedIds: rules.map((rule) => rule.id),
    posted: resultPayloads.map((item) => ({
      removed: item.result && item.result.removed, error: item.error,
    })),
  };
}

function settle() {
  // parseSSEChunk dispatches without awaiting, so let the real event loop
  // drain before looking at what the handler did.
  return new Promise((resolve) => setImmediate(resolve));
}

async function runDedupAcrossRestart() {
  const frame = 'event: command\ndata: ' + JSON.stringify({
    id: 'dedup-open', type: 'open-tab', url: 'about:blank',
    _did: 'did-dedup-1',
  }) + '\n\n';
  const deliver = 'parseSSEChunk(' + JSON.stringify(frame) + ')';

  vm.runInContext(deliver, context);
  for (let turn = 0; turn < 6; turn++) await settle();

  // A fresh worker instance over the SAME extension storage, which is what an
  // MV3 restart is.
  const restarted = makeContext();
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), restarted);
  await vm.runInContext('loadConfig()', restarted);
  vm.runInContext(deliver, restarted);
  for (let turn = 0; turn < 6; turn++) await settle();

  return {
    created: createdTabs.length,
    posted: resultPayloads.map((item) => item._did || null),
  };
}

async function runClearPartitioned() {
  cookieJar.push(
    { name: 'ordinary', domain: 'example.test', path: '/', secure: false },
    { name: 'chips', domain: 'example.test', path: '/', secure: false,
      partitionKey: { topLevelSite: 'http://example.test' } });
  context.clearCommand = {
    id: 'clear-partitioned',
    type: 'clear-cookies',
    url: 'http://example.test/',
    _did: 'did-clear-partitioned',
  };
  await vm.runInContext('dispatchCommand(clearCommand)', context);
  return {
    remaining: cookieJar.map((cookie) => cookie.name),
    posted: resultPayloads.map((item) => ({
      result: item.result, error: item.error,
    })),
    removeCalls: removeCalls.map((details) => ({
      name: details.name, partitionKey: details.partitionKey || null,
    })),
  };
}

function relayFetch(request) {
  return new Promise((resolve) => {
    const message = Object.assign({
      type: 'fetch',
      fetchId: 'bounded-' + (++relaySequence),
      method: 'GET',
      responseType: 'text',
    }, request);
    for (const listener of messageListeners) listener(message, {}, resolve);
  });
}

async function runFetchBound() {
  const steps = [];
  const cases = [
    // Exactly the 8 MiB default: a ceiling, not a threshold the last
    // permitted byte trips.
    { name: 'at the default', chunks: 8 },
    { name: 'over the default', chunks: 9 },
    // The opt-in raises the default for a caller that asks for more.
    { name: 'raised by opt-in', chunks: 12, maxResponseBytes: 16 * 1024 * 1024 },
    { name: 'binary under the default', chunks: 1, responseType: 'arraybuffer' },
  ];
  for (const item of cases) {
    const request = Object.assign({}, item);
    delete request.name;
    delete request.chunks;
    request.url = 'https://big.example.com/blob?chunks=' + item.chunks;
    const answer = await relayFetch(request);
    steps.push({
      name: item.name,
      error: answer.error || null,
      tooLarge: answer.tooLarge === true,
      dataLength: typeof answer.data === 'string' ? answer.data.length : null,
      chunksRead: streamPlan.handed,
      chunksOffered: streamPlan.chunkCount,
      cancelled: streamPlan.cancelled,
    });
  }
  // Showing the clamp by streaming would mean allocating past the ceiling,
  // so it is asked directly instead.
  const limits = {};
  for (const [label, asked] of [
      ['omitted', 'undefined'], ['zero', '0'], ['negative', '-1'],
      ['fractional', '1.5'], ['text', '"8000000"'],
      ['below the default', '1024'],
      ['above the ceiling', String(1024 * 1024 * 1024 * 1024)]]) {
    limits[label] = vm.runInContext('gmResponseLimit(' + asked + ')', context);
  }
  const timings = JSON.parse(vm.runInContext(
    'JSON.stringify(_fetchTimings.map((t) =>'
    + ' ({ bodySize: t.bodySize === undefined ? null : t.bodySize,'
    + ' error: t.error || null })))', context));
  return { steps, limits, timings };
}

async function run() {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context);
  await vm.runInContext('loadConfig()', context);
  if (scenario === 'capacity') return runCapacity();
  if (scenario === 'expiry') return runExpiry();
  if (scenario === 'route') return runRouteSnapshot();
  if (scenario === 'screenshot-reject') return runScreenshotReject();
  if (scenario === 'screenshot-target') return runScreenshotTarget();
  if (scenario === 'net-capture') return runNetCapture();
  if (scenario === 'hotfix-race') return runHotfixRace();
  if (scenario === 'block-rule-restart') return runBlockRuleRestart();
  if (scenario === 'unblock-zero') return runUnblockZero();
  if (scenario === 'clear-partitioned') return runClearPartitioned();
  if (scenario === 'dedup-restart') return runDedupAcrossRestart();
  if (scenario === 'fetch-bound') return runFetchBound();
  throw new Error('unknown scenario: ' + scenario);
}

run().then((result) => {
  process.stdout.write(JSON.stringify(result));
}).catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


def _run_extension_result_boundary(scenario):
    node = shutil.which('node')
    assert node, 'node is required to execute the extension result path'
    result = subprocess.run(
        [node, '-e', _EXTENSION_RESULT_BOUNDARY_HARNESS,
         str(EXTENSION_ROOT / 'background.js'), scenario],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_eval_relay_capacity_rejects_1001st_and_preserves_first(tmp):
    """The 1,001st relay fails while the first live relay remains valid."""
    del tmp
    actual = _run_extension_result_boundary('capacity')
    assert actual == {
        'firstId': 'existing-0',
        'sentMessages': 0,
        'results': [{
            'kind': 'result',
            'url': 'https://initial.example.com/result',
            'token': 'initial-token',
            'id': 'new-at-capacity',
            'error': 'Eval relay capacity exceeded',
        }],
    }, actual


def test_eval_relay_expiry_posts_one_timeout_at_300000_ms(tmp):
    """The exact relay TTL removes the entry and posts one terminal error."""
    del tmp
    actual = _run_extension_result_boundary('expiry')
    assert actual == {
        'stillPending': False,
        'results': [{
            'kind': 'result',
            'url': 'https://initial.example.com/result',
            'token': 'initial-token',
            'id': 'slow-eval',
            'error': 'Eval relay timed out after 300000 ms',
        }],
    }, actual


def test_result_route_snapshot_covers_retries_and_side_operations(tmp):
    """Config rotation cannot retarget result retries or side operations."""
    del tmp
    actual = _run_extension_result_boundary('route')
    assert actual == {
        'requests': [
            {
                'kind': 'upload',
                'url': 'https://initial.example.com/upload',
                'token': 'initial-token',
                'id': 'route-snapshot',
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'route-snapshot',
                'error': None,
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'route-snapshot',
                'error': None,
            },
            {
                'kind': 'result',
                'url': 'https://initial.example.com/result',
                'token': 'initial-token',
                'id': 'block-route-snapshot',
                'error': None,
            },
        ],
        'excludedRequestDomains': ['initial.example.com'],
    }, actual


def test_a_targeted_screenshot_captures_the_tab_it_names(tmp):
    """Naming a tab has to select it, because capture does not.

    captureVisibleTab captures whatever is active in the WINDOW it is given,
    so a screenshot aimed at an inactive tab returned the active sibling's
    pixels under the requested tab's url and title. Nothing in the answer said
    the image was of a different page.
    """
    del tmp
    actual = _run_extension_result_boundary('screenshot-target')
    assert actual['captured'] == 'captured:8', actual
    assert actual['posted'] == [
        {'tabUrl': 'about:blank#target', 'error': None}], actual
    # And the window is left as it was found.
    assert actual['activeAfter'] == 7, actual
    assert actual['activations'] == [8, 7], actual


def test_rejected_screenshot_upload_is_reported_as_an_error(tmp):
    """A 400 from /upload must not become a success envelope with no path."""
    del tmp
    actual = _run_extension_result_boundary('screenshot-reject')
    assert actual == {
        'uploads': 1,
        'posted': [{
            'result': None,
            'error': 'Screenshot upload failed: invalid path component',
        }],
    }, actual


def test_failed_net_capture_setup_leaves_no_capture_and_no_attachment(tmp):
    """Attach and enable failures roll back; a detach ends the capture."""
    del tmp
    actual = _run_extension_result_boundary('net-capture')
    assert actual == {
        'outcomes': [
            {
                'step': 'attach-fails',
                'result': None,
                'error': 'Another debugger is already attached',
            },
            {
                'step': 'enable-fails',
                'result': None,
                'error': 'Network.enable failed',
            },
            {
                'step': 'succeeds',
                'result': {'capturing': True, 'tabId': 7},
                'error': None,
            },
            {
                'step': 'after-detach',
                'result': {'capturing': True, 'tabId': 7},
                'error': None,
            },
        ],
        # One attach per call — a failed setup never answers `already: true`.
        'attachCalls': 4,
        # Only the enable failure had an attachment to give back.
        'detachCalls': 1,
    }, actual


def test_concurrent_hotfix_stores_both_survive(tmp):
    """Two stores dispatched together must both be in the record afterwards."""
    del tmp
    actual = _run_extension_result_boundary('hotfix-race')
    assert actual == {
        'posted': [
            {
                'result': {'stored': 'fix-a', 'total': 1, 'permanent': False},
                'error': None,
            },
            {
                'result': {'stored': 'fix-b', 'total': 2, 'permanent': False},
                'error': None,
            },
        ],
        'storedIds': ['fix-a', 'fix-b'],
    }, actual


def test_a_delivery_id_is_spent_once_across_worker_restarts(tmp):
    """At-most-once has to survive the worker, or it is at-most-once per boot.

    The ledger of spent delivery ids was module state, so an MV3 restart
    emptied it. The bridge redelivers a command whose socket write succeeded
    but whose unlink did not, which is exactly the case dedup exists for — and
    a worker that restarted in between executed it a second time.
    """
    del tmp
    actual = _run_extension_result_boundary('dedup-restart')
    assert actual['created'] == 1, actual
    assert actual['posted'].count('did-dedup-1') == 1, actual


def test_clearing_cookies_removes_the_partitioned_ones_too(tmp):
    """A cookie the browser refused to remove must not be counted as removed.

    `chrome.cookies.remove` matches a partitioned cookie only when the
    partition is named, and the call dropped `partitionKey` — so a CHIPS
    cookie stayed readable while the count said it had gone. The count was
    incremented per iteration rather than per removal, which is what let the
    two disagree in the first place.
    """
    del tmp
    actual = _run_extension_result_boundary('clear-partitioned')
    assert actual['remaining'] == [], actual
    assert len(actual['posted']) == 1, actual
    assert actual['posted'][0]['error'] is None, actual
    assert actual['posted'][0]['result']['removed'] == 2, actual
    assert actual['posted'][0]['result']['failed'] == [], actual
    partitioned = [call for call in actual['removeCalls']
                   if call['partitionKey']]
    assert len(partitioned) == 1, actual['removeCalls']


def test_rule_id_zero_is_refused_rather_than_removing_everything(tmp):
    """A specific id that is invalid must not widen into remove-all.

    `if (cmd.ruleId)` is false for 0, so `unblock-requests` with ruleId 0 fell
    through to the branch that removes every session rule and reported them as
    removed. The narrowest possible request destroyed the most.
    """
    del tmp
    actual = _run_extension_result_boundary('unblock-zero')
    assert actual['installedIds'] == [9001, 9002, 9003], actual
    assert len(actual['posted']) == 1, actual
    assert actual['posted'][0]['error'], actual
    assert actual['posted'][0]['removed'] is None, actual


def test_block_rule_ids_survive_a_worker_restart(tmp):
    """Session rules outlive the worker, so ids must not restart at the base."""
    del tmp
    actual = _run_extension_result_boundary('block-rule-restart')
    assert actual == {
        'posted': [
            {'ruleId': 9001, 'error': None},
            {'ruleId': 9002, 'error': None},
            {'ruleId': 9003, 'error': None},
            {'ruleId': 9004, 'error': None},
        ],
        'installedIds': [9001, 9002, 9003, 9004],
    }, actual


def test_the_gm_fetch_relay_bounds_the_response_while_it_reads(tmp):
    """An oversized response is abandoned at the limit, not measured after it.

    The shim is injected into every matching top-level page, so any visited
    site can invoke this relay. It had no ceiling at all: an 8 MiB response
    was materialized whole and then copied again into 11,184,812 base64
    characters. Reading through a counter is the part that matters — a size
    check after `arrayBuffer()` learns the size only once the worker is
    already holding every byte.
    """
    del tmp
    actual = _run_extension_result_boundary('fetch-bound')
    steps = {step['name']: step for step in actual['steps']}
    assert len(steps) == 4, actual

    mib = 1024 * 1024
    # Exactly the default is allowed: the limit is a ceiling, not a threshold
    # the last permitted byte trips.
    at_default = steps['at the default']
    assert at_default['error'] is None, at_default
    assert at_default['dataLength'] == 8 * mib, at_default
    assert at_default['cancelled'] is False, at_default

    over = steps['over the default']
    assert over['tooLarge'] is True, over
    assert '8388608' in (over['error'] or ''), over
    assert over['dataLength'] is None, over
    # The read stopped at the chunk that crossed the limit and cancelled the
    # body, rather than draining the response and rejecting it afterwards.
    assert over['chunksRead'] == 9, over
    assert over['cancelled'] is True, over

    raised = steps['raised by opt-in']
    assert raised['error'] is None, raised
    assert raised['dataLength'] == 12 * mib, raised

    # The binary path still base64s, because chrome.runtime.sendMessage is
    # JSON-serialized and an ArrayBuffer does not survive it.
    binary = steps['binary under the default']
    assert binary['error'] is None, binary
    assert binary['dataLength'] > mib, binary

    assert actual['limits'] == {
        # Anything that is not a usable positive number means "no preference".
        'omitted': 8 * mib,
        'zero': 8 * mib,
        'negative': 8 * mib,
        'text': 8 * mib,
        # A caller may ask for LESS, which is a safer request, not a weaker
        # one — including the floor of a fractional value.
        'fractional': 1,
        'below the default': 1024,
        # And may not ask its way past the ceiling.
        'above the ceiling': 64 * mib,
    }, actual['limits']

    # The diagnostic ring records the refusal as its own outcome and the raw
    # byte count for the rest; the text path used to record characters.
    assert [entry['error'] for entry in actual['timings']] == [
        None, 'too-large', None, None], actual['timings']
    assert [entry['bodySize'] for entry in actual['timings']] == [
        8 * mib, None, 12 * mib, mib], actual['timings']


def test_the_relay_ceiling_is_declared_once_and_bounds_the_default(tmp):
    """Both limits live in the worker, and the ceiling is the larger one."""
    del tmp
    source = (EXTENSION_ROOT / 'background.js').read_text(encoding='utf-8')
    found = dict(re.findall(
        r'const (GM_FETCH_MAX_RESPONSE|GM_FETCH_RESPONSE_CEILING) = '
        r'([0-9 *]+);', source))
    assert set(found) == {'GM_FETCH_MAX_RESPONSE',
                          'GM_FETCH_RESPONSE_CEILING'}, found
    values = {}
    for name, expression in found.items():
        product = 1
        for factor in expression.split('*'):
            product *= int(factor.strip())
        values[name] = product
    assert values['GM_FETCH_MAX_RESPONSE'] > 0, values
    assert (values['GM_FETCH_RESPONSE_CEILING']
            >= values['GM_FETCH_MAX_RESPONSE']), values


# ─── Typed-command routing guard: `tab` vs `tabId` ───
# `tab` routes to a server queue; `tabId` names a browser tab. A typed
# extension command routes to `tab: 'extension'`; sending a tab number as
# `tab` silently retargets it. Within the statically resolved shapes documented
# below, the scan is structural rather than value-shaped: Python senders are
# parsed with `ast` and JavaScript is read with a bracket-matching scanner, so
# the value expression and line wrapping do not weaken the check.


# ─── Relay contract: content.js → background.js ───
# Self-contained guard; other tests in this file may be rewritten without
# touching this block.


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
