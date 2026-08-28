"""Driving a real Chrome with the unpacked extension loaded.

Not a suite itself — run_tests.py only loads `test_*.py`.

Starting a browser, finding its DevTools endpoint, waiting for OUR service
worker among whatever else is running, serving the test pages and evaluating
in them: the machinery every real-browser test needs before it can assert
anything. A browser that will not start is a skip; a worker that loads broken
is a failure.
"""
import contextlib
import errno
import http.server
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _evalpages import (CDP_CALL_HARNESS,  # noqa: E402
                        CDP_RESPONSE_DEADLINE_MS, CDP_TIMEOUT_EXIT_CODE,
                        HOSTILE_EVAL_SCRIPT, PERFORMANCE_POISON_EVAL_SCRIPT,
                        PLAIN_EVAL_SCRIPT, STRICT_CSP_EVAL_SCRIPT)
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402


NODE_WEBSOCKET_PROBE = (
    "process.exit(typeof WebSocket === 'function' ? 0 : 1)")
NODE_PROBE_TIMEOUT = 10
WINDOWS_COMMAND_TOO_LONG = 206


class CDPTimeout(AssertionError):
    """The JavaScript CDP harness reached its response deadline."""


class BrowserEnvironmentSkipped(_util.Skipped):
    """The browser installation could not reach a usable fixture state."""


class _EvalPageServer(http.server.ThreadingHTTPServer):
    def __init__(self, address, handler):
        super().__init__(address, handler)
        self._request_lock = threading.Lock()
        self._request_paths = []

    def record_request(self, path):
        with self._request_lock:
            self._request_paths.append(path)

    def request_marker(self):
        with self._request_lock:
            return len(self._request_paths)

    def received_request_since(self, page_url, marker):
        path = urllib.parse.urlsplit(page_url).path
        with self._request_lock:
            return path in self._request_paths[marker:]


_FIXTURE_SERVERS = {}
_FIXTURE_SERVERS_LOCK = threading.Lock()


class _EvalPageHandler(http.server.BaseHTTPRequestHandler):
    """Serve the real-page evaluator fixtures over one loopback origin."""

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        self.server.record_request(path)
        pages = {
            '/hostile.html': (
                b'<title>loading</title><body><script src="/hostile.js"></script></body>',
                'text/html', None),
            '/hostile.js': (
                HOSTILE_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/strict.html': (
                b'<title>loading</title><body><script src="/strict.js"></script></body>',
                'text/html', "default-src 'self'; script-src 'self'"),
            '/strict.js': (
                STRICT_CSP_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/performance-poison.html': (
                b'<title>loading</title><body><script src="/performance-poison.js"></script></body>',
                'text/html', None),
            '/performance-poison.js': (
                PERFORMANCE_POISON_EVAL_SCRIPT.encode(), 'text/javascript', None),
            '/plain.html': (
                b'<title>loading</title><body><script src="/plain.js"></script></body>',
                'text/html', None),
            '/plain.js': (
                PLAIN_EVAL_SCRIPT.encode(), 'text/javascript', None),
        }
        fixture = pages.get(path)
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
def eval_page_server():
    server = _EvalPageServer(
        ('127.0.0.1', 0), _EvalPageHandler)
    origin = f'http://127.0.0.1:{server.server_address[1]}'
    with _FIXTURE_SERVERS_LOCK:
        _FIXTURE_SERVERS[origin] = server
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield origin
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        with _FIXTURE_SERVERS_LOCK:
            _FIXTURE_SERVERS.pop(origin, None)


def cdp_call(node, target, method, params):
    args = [node, '-e', CDP_CALL_HARNESS, target, method, json.dumps(params),
            str(CDP_RESPONSE_DEADLINE_MS)]
    try:
        result = subprocess.run(
            args, cwd=ROOT, capture_output=True, text=True,
            timeout=CDP_RESPONSE_DEADLINE_MS / 1000 + 5)
    except subprocess.TimeoutExpired as why:
        raise CDPTimeout(
            (None, why.stdout or '', why.stderr or '')) from why
    failure = (result.returncode, result.stdout, result.stderr)
    if result.returncode == CDP_TIMEOUT_EXIT_CODE:
        raise CDPTimeout(failure)
    assert result.returncode == 0, (
        result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout or '{}')


def _fixture_request_marker(page_url):
    parts = urllib.parse.urlsplit(page_url)
    origin = f'{parts.scheme}://{parts.netloc}'
    with _FIXTURE_SERVERS_LOCK:
        server = _FIXTURE_SERVERS.get(origin)
    return None if server is None else (server, server.request_marker())


def _fixture_request_arrived(page_url, marker):
    if marker is None:
        return False
    server, request_index = marker
    parts = urllib.parse.urlsplit(page_url)
    origin = f'{parts.scheme}://{parts.netloc}'
    with _FIXTURE_SERVERS_LOCK:
        if _FIXTURE_SERVERS.get(origin) is not server:
            return False
    return server.received_request_since(page_url, request_index)


def cdp_eval(node, target, expression):
    response = cdp_call(node, target, 'Runtime.evaluate', {
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


def _raise_start_failure(label, executable, why):
    # Most exec refusals describe the machine or binary. E2BIG instead
    # describes the argument vector this harness built, so hiding it as an
    # unavailable environment would excuse a repository-owned command defect.
    if (why.errno == errno.E2BIG
            or getattr(why, 'winerror', None) == WINDOWS_COMMAND_TOO_LONG):
        raise AssertionError(
            f'{label} command was too large to start: {executable}') from why
    raise BrowserEnvironmentSkipped(
        f'{label} could not be launched: {executable} — {why}') from why


def browser_requirements():
    node = shutil.which('node')
    browser = next((path for name in (
        'chromium', 'chromium-browser', 'google-chrome',
        'google-chrome-stable', 'chrome')
        if (path := shutil.which(name))), None)
    if not node or not browser:
        raise BrowserEnvironmentSkipped(
            'Chromium and Node are required for the real-page eval test')
    try:
        websocket = subprocess.run(
            [node, '-e', NODE_WEBSOCKET_PROBE], cwd=ROOT,
            capture_output=True, text=True, timeout=NODE_PROBE_TIMEOUT)
    except OSError as why:
        _raise_start_failure('Node WebSocket probe', node, why)
    except subprocess.TimeoutExpired as why:
        # The interpreter started, so its fixed program failing to terminate
        # is the harness's defect rather than a missing machine capability.
        raise AssertionError(
            f'Node WebSocket probe did not finish: {node}') from why
    if websocket.returncode != 0:
        raise BrowserEnvironmentSkipped(
            'this Node runtime has no WebSocket client for CDP')
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
    fails — see real_extension_page for where that boundary sits.
    """
    port_file = Path(profile) / 'DevToolsActivePort'
    # A cold runner's first browser start is slower than every later one: the
    # ubuntu legs timed out here on the first browser test of the run and
    # reached the same browser without trouble in the ones after it.
    deadline = time.time() + 30
    seen = 'it never wrote a DevTools port'
    while time.time() < deadline:
        if process.poll() is not None:
            raise BrowserEnvironmentSkipped(
                'Chromium exited before DevTools became available')
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
    raise BrowserEnvironmentSkipped(
        'this browser never exposed the fixture page and an '
        f'extension service worker over DevTools — {seen}')


def ready_worker(node, workers):
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
            if cdp_eval(node, target, _WORKER_READY_PROBE) is True:
                return target, True, None
            reached = True
            error = 'the worker answered without its declarations'
        except AssertionError as failure:
            error = f'evaluating in the worker failed: {failure}'
    return None, reached, error


def _cdp_channel_answers(node, target):
    try:
        cdp_call(node, target, 'Runtime.evaluate', {
            'expression': '1',
            'returnByValue': True,
        })
    except AssertionError:
        return False
    return True


def worker_state(node, target):
    """What one worker says about itself, for a failure that names which."""
    try:
        return cdp_eval(node, target, _WORKER_STATE_PROBE)
    except AssertionError as why:
        return f'could not be read back: {why}'


@contextlib.contextmanager
def real_extension_page(tmp, bridge_url, token, page_url,
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
    node, browser = browser_requirements()
    profile = Path(tmp) / 'chromium-profile'
    extension = (extension_root or EXTENSION_ROOT).resolve()
    # Ours last: a browser that carries an extension of its own lists that
    # one first, which is the order the CI legs see.
    loaded = ','.join(str(Path(item).resolve())
                      for item in (*extra_extensions, extension))
    browser_args = [
        browser,
        '--headless=new',
        '--no-sandbox',
        '--disable-gpu',
        '--disable-dev-shm-usage',
        '--no-first-run',
        '--no-default-browser-check',
        # Avoid a D-Bus secret-service probe whose reply timeout holds
        # every network transaction. This temporary profile is discarded.
        '--password-store=basic',
        '--remote-allow-origins=*',
        '--remote-debugging-port=0',
        '--disable-extensions-except=' + loaded,
        '--load-extension=' + loaded,
        '--user-data-dir=' + str(profile),
        'about:blank',
    ]
    try:
        process = subprocess.Popen(
            browser_args, cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as why:
        _raise_start_failure('Chromium', browser, why)
    configuration_started = False
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
        # Only this first navigation can still be environmental. Later calls
        # follow configuration and fail.
        request_marker = _fixture_request_marker(page_url)
        try:
            cdp_call(node, page_target, 'Page.navigate', {'url': page_url})
        except CDPTimeout as why:
            channel_answers = _cdp_channel_answers(node, page_target)
            request_arrived = _fixture_request_arrived(
                page_url, request_marker)
            if not channel_answers:
                raise BrowserEnvironmentSkipped(
                    'the browser CDP channel stopped answering during the '
                    'first fixture navigation: '
                    f'{_browser_version(browser)}') from why
            if not request_arrived:
                raise BrowserEnvironmentSkipped(
                    'the first fixture navigation never reached the fixture: '
                    f'{_browser_version(browser)}') from why
            # A channel that selectively loses the navigation reply but
            # answers this probe is indistinguishable from a slow fixture.
            # The realistic channel-loss case, where it stopped working, is
            # caught above; the observations here cannot settle the synthetic
            # selective-loss case without inventing another causal inference.
            raise AssertionError(
                'the fixture received the first navigation request but did '
                f'not satisfy it before the deadline: {page_url}') from why
        deadline = time.time() + 30
        last_error = 'no evaluation was attempted'
        answered = False
        worker_target = None
        while time.time() < deadline:
            worker_target, reached, error = ready_worker(node, workers)
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
                states = [worker_state(node, item['webSocketDebuggerUrl'])
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
            raise BrowserEnvironmentSkipped(
                'this browser never let the extension worker be reached '
                f'over the debugger: {_browser_version(browser)} — '
                f'{last_error}')

        configuration_started = True
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
                configured = cdp_eval(node, worker_target, configure)
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
            replacement, _reached, _error = ready_worker(node, workers)
            if replacement:
                worker_target = replacement
        assert configured is True, (
            f'extension worker did not load test configuration ({configured!r}'
            f'). Worker state: {worker_state(node, worker_target)}')
        cdp_call(node, page_target, 'Page.navigate', {'url': page_url})

        deadline = time.time() + 15
        while time.time() < deadline:
            if cdp_eval(
                    node, page_target,
                    'globalThis.__evalPageReady === true') is True:
                break
            time.sleep(0.25)  # one node process per attempt; see above
        else:
            raise AssertionError(
                'the fixture page never set __evalPageReady: ' + page_url)

        cdp_eval(node, worker_target, 'registerAllTabs()')
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
    except BrowserEnvironmentSkipped as why:
        if not configuration_started:
            raise
        # Once configuration starts, this fixture has established a usable
        # browser; an environment label from later code cannot cross the line.
        raise AssertionError(
            'browser environment skip escaped after extension configuration: '
            + str(why)) from why
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def real_ext_command(bridge_url, token, cmd_id, payload):
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


def real_eval(bridge_url, token, tab_id, cmd_id, code):
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


def hostile_eval_matrix(tmp):
    """Return five eval shapes from the fully poisoned real page."""
    token = 'cdpevaltok'
    matrix = {}
    with _util.bridge(
            tmp, env={'TOKEN': '', 'DAEDALUS_TOKEN': token,
                      'DAEDALUS_MCP_PORT': '0'}) as (bridge_url, _docroot):
        with eval_page_server() as pages:
            page_url = pages + '/hostile.html'
            with real_extension_page(
                    tmp, bridge_url, token, page_url) as (node, page, tab_id):
                poison = cdp_eval(
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
                    actual = real_eval(
                        bridge_url, token, tab_id, 'cdp-' + label, code)
                    matrix[label] = actual
    return matrix
