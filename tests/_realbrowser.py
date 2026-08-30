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
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _deliveries import real_eval  # noqa: E402
from _evalpages import (CDP_CALL_HARNESS,  # noqa: E402
                        CDP_RESPONSE_DEADLINE_MS, CDP_TIMEOUT_EXIT_CODE)
from _realbrowser_errors import (CDPEvaluationError, CDPTimeout,  # noqa: E402
                                 FirstNavigationTimeout)
from _realbrowser_pages import (_fixture_request_arrived,  # noqa: E402
                                _fixture_request_marker, eval_page_server)
from _repo import EXTENSION_ROOT, ROOT  # noqa: E402


WEBSOCKET_PRESENT_TOKEN = 'websocket-present'
WEBSOCKET_ABSENT_TOKEN = 'websocket-absent'
NODE_WEBSOCKET_PROBE = (
    "process.stdout.write(typeof WebSocket === 'function' ? "
    f'{json.dumps(WEBSOCKET_PRESENT_TOKEN)} : '
    f'{json.dumps(WEBSOCKET_ABSENT_TOKEN)})')
NODE_PROBE_TIMEOUT = 10
WINDOWS_COMMAND_TOO_LONG = 206


class BrowserEnvironmentSkipped(_util.Skipped):
    """The browser installation could not reach a usable fixture state."""


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


def cdp_eval(node, target, expression):
    response = cdp_call(node, target, 'Runtime.evaluate', {
        'expression': expression,
        'awaitPromise': True,
        'returnByValue': True,
    })
    if response.get('exceptionDetails'):
        raise CDPEvaluationError(response)
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
    if (why.errno != errno.E2BIG
            and getattr(why, 'winerror', None)
            == WINDOWS_COMMAND_TOO_LONG):
        raise AssertionError(
            f'{label} command was too large to start: {executable}') from why
    if why.errno == errno.E2BIG:
        # E2BIG combines argv and environment size; a minimal Python spawn
        # observes whether the inherited environment alone crosses the limit.
        try:
            minimal = subprocess.run(
                [sys.executable, '-c', ''], cwd=ROOT,
                capture_output=True, text=True, timeout=NODE_PROBE_TIMEOUT)
        except OSError as minimal_failure:
            if minimal_failure.errno == errno.E2BIG:
                raise BrowserEnvironmentSkipped(
                    f'{label} could not be launched: {executable}; a minimal '
                    'spawn under the same inherited environment also failed '
                    'with E2BIG') from why
            raise AssertionError(
                f'{label} command was too large to start: {executable}; '
                'the cause is undetermined because a minimal spawn failed'
            ) from minimal_failure
        except subprocess.SubprocessError as minimal_failure:
            raise AssertionError(
                f'{label} command was too large to start: {executable}; '
                'the cause is undetermined because a minimal spawn failed'
            ) from minimal_failure
        if minimal.returncode != 0:
            raise AssertionError(
                f'{label} command was too large to start: {executable}; '
                'the cause is undetermined because a minimal spawn failed')
        raise AssertionError(
            f'{label} command was too large to start: {executable}') from why
    raise BrowserEnvironmentSkipped(
        f'{label} could not be launched: {executable} — {why}') from why


def _browser_args(browser, loaded, profile):
    """The launch for a headless session carrying `loaded` extensions.

    Shared by the fixture launch and the worker-absence diagnosis so the two
    cannot drift: whatever the fixture needed to start on a leg, the
    diagnosis needs to start under the same conditions.
    """
    return [
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
    # Exit status only says whether our program ran; distinct stdout tokens
    # carry the capability answer without conflating it with probe failure.
    if websocket.returncode != 0:
        raise AssertionError(
            f'Node WebSocket probe failed: {node}; '
            f'exit={websocket.returncode}, stdout={websocket.stdout!r}, '
            f'stderr={websocket.stderr!r}')
    if websocket.stdout == WEBSOCKET_ABSENT_TOKEN:
        raise BrowserEnvironmentSkipped(
            'this Node runtime has no WebSocket client for CDP')
    if websocket.stdout != WEBSOCKET_PRESENT_TOKEN:
        raise AssertionError(
            f'Node WebSocket probe returned an invalid answer: {node}; '
            f'stdout={websocket.stdout!r}')
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


def _worker_targets(targets, declared):
    """Every service worker whose script is declared by the extension.

    More than one extension can be loaded at once, and a CI runner's browser
    carries one of its own, so this is a list rather than the first match:
    which of them is this extension's is a question only the worker's own
    declarations answer, and DevTools happens to list the other one first.
    """
    return [item for item in targets
            if item.get('type') == 'service_worker'
            and item.get('url', '').endswith('/' + declared)]


def _wait_for_devtools(profile, process, declared):
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
                    workers = _worker_targets(targets, declared)
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
        except CDPEvaluationError as failure:
            # Exception details prove the worker evaluated our probe.
            reached = True
            error = f'evaluating in the worker failed: {failure}'
        except AssertionError as failure:
            error = f'evaluating in the worker failed: {failure}'
    return None, reached, error


def worker_state(node, target):
    """What one worker says about itself, for a failure that names which."""
    try:
        return cdp_eval(node, target, _WORKER_STATE_PROBE)
    except AssertionError as why:
        return f'could not be read back: {why}'


def declared_worker(extension):
    """Return the validated manifest worker path relative to the extension."""
    extension = Path(extension).resolve()
    manifest_path = extension / 'manifest.json'
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as why:
        raise AssertionError(
            f'extension manifest is unreadable: {manifest_path}: {why}'
        ) from why
    background = (manifest.get('background')
                  if isinstance(manifest, dict) else None)
    declared = (background.get('service_worker')
                if isinstance(background, dict) else None)
    if not isinstance(declared, str) or not declared:
        raise AssertionError(
            f'extension manifest declares no service worker: {manifest_path}')
    worker_path = (extension / declared).resolve()
    if not worker_path.is_relative_to(extension):
        raise AssertionError(
            'extension manifest declares a service worker outside the '
            f'extension: {worker_path}')
    if not worker_path.is_file():
        raise AssertionError(
            'extension manifest declares a missing service worker: '
            f'{worker_path}')
    return worker_path.relative_to(extension).as_posix()


CONTROL_WORKER_SCRIPT = 'control-worker.js'
CONTROL_WORKER_PROBE = 'globalThis.__controlWorkerLoaded === true'
WORKER_ABSENCE_DEADLINE = 30


def _control_extension(tmp):
    """Write a known-good MV3 extension whose worker answers its probe.

    Its script name is unique on purpose: the diagnosis launch carries our
    extension too, so two service workers are listed over DevTools at once
    and `_worker_targets` tells them apart by that name alone. It must never
    be our own script name, or the worker whose absence is being diagnosed
    could answer for the control.
    """
    root = Path(tmp) / 'control-extension'
    root.mkdir()
    (root / 'manifest.json').write_text(json.dumps({
        'manifest_version': 3,
        'name': 'daedalus-fixture-control',
        'version': '1.0',
        'background': {'service_worker': CONTROL_WORKER_SCRIPT},
    }), encoding='utf-8')
    (root / CONTROL_WORKER_SCRIPT).write_text(
        'globalThis.__controlWorkerLoaded = true;\n', encoding='utf-8')
    return root


def _devtools_port(profile):
    """The port Chromium wrote for this profile, or '' before it does."""
    try:
        lines = (Path(profile) / 'DevToolsActivePort').read_text(
            encoding='utf-8').splitlines()
    except OSError:
        return ''
    return lines[0] if lines else ''


def _control_worker_targets(profile):
    """The control extension's workers, or [] while none is listed."""
    port = _devtools_port(profile)
    if not port:
        return []
    try:
        return _worker_targets(_devtools_targets(port), CONTROL_WORKER_SCRIPT)
    except (OSError, ValueError):
        return []


def _control_worker_answered(node, items):
    """Whether a listed control worker ran its script to the flag.

    An unreadable answer is not an answer: a transport failure or an
    exception inside the probe leaves the control undemonstrated, and the
    next poll asks again instead of settling a verdict it did not observe.
    """
    for item in items:
        try:
            if cdp_eval(
                    node, item['webSocketDebuggerUrl'],
                    CONTROL_WORKER_PROBE) is True:
                return True
        except AssertionError:
            continue
    return False


def _absence_guilt(browser, extension, worker_script):
    """The failure raised when the control proves the browser can do this."""
    return (
        'the extension source kept its own service worker from loading: '
        f'{extension} declares {worker_script}, and a control extension '
        'loaded beside it in a second launch answered its probe while ours '
        f'never appeared — {_browser_version(browser)} '
        'demonstrably runs an unpacked MV3 worker, so the absence of ours '
        "is this repository's, not the machine's")


def _worker_absence_verdict(node, browser, extension, worker_script, tmp):
    """Decide whose fault it is that our extension produced no worker.

    A second launch carries the control extension beside ours, and the
    control's worker is the witness: a browser that lists it and sees it
    answer its probe has demonstrated the exact capability the fixture's
    skip would excuse, so ours is the failure and this raises naming our
    source. A browser that fails the control too returns what it observed,
    and the skip standing in the caller names the machine.
    """
    control = _control_extension(tmp)
    profile = Path(tmp) / 'control-profile'
    loaded = ','.join(
        str(Path(item).resolve()) for item in (extension, control))
    process = subprocess.Popen(
        _browser_args(browser, loaded, profile), cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + WORKER_ABSENCE_DEADLINE
        while time.time() < deadline:
            if _control_worker_answered(
                    node, _control_worker_targets(profile)):
                guilt = _absence_guilt(browser, extension, worker_script)
                raise AssertionError(guilt)
            if process.poll() is not None:
                return ('the diagnosis browser exited before any control '
                        'worker was listed')
            time.sleep(0.5)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    return 'the control extension produced no answering worker either'


@contextlib.contextmanager
def _environment_verdicts_closed():
    """Turn an environment skip after configuration into a hard failure."""
    try:
        yield
    except BrowserEnvironmentSkipped as why:
        raise AssertionError(
            'browser environment skip escaped after extension configuration: '
            + str(why)) from why


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
    MV3 service worker (decided by loading a control extension beside ours,
    never assumed from the absence), or is refused a profile is a property of
    the machine.

    From the configuration step on, the browser has demonstrably worked and
    everything asserted is the extension's own behaviour, so those stay hard
    failures. Waiting for __evalPageReady is on that side of the line because
    the readiness script is repository-owned. A timeout in the earlier first
    navigation remains undetermined and is represented by
    FirstNavigationTimeout. Skipping the environment costs no coverage of the
    extension source itself: this suite also runs background.js, content.js
    and page.js under Node, which does not need a browser and fails outright
    if that source is broken.
    """
    node, browser = browser_requirements()
    profile = Path(tmp) / 'chromium-profile'
    extension = (extension_root or EXTENSION_ROOT).resolve()
    worker_script = declared_worker(extension)
    # Ours last: a browser that carries an extension of its own lists that
    # one first, which is the order the CI legs see.
    loaded = ','.join(str(Path(item).resolve())
                      for item in (*extra_extensions, extension))
    browser_args = _browser_args(browser, loaded, profile)
    try:
        process = subprocess.Popen(
            browser_args, cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as why:
        _raise_start_failure('Chromium', browser, why)
    try:
        page, workers, devtools_port = _wait_for_devtools(
            profile, process, worker_script)
        page_target = page['webSocketDebuggerUrl']
        # Load the page before waiting on the worker. An MV3 worker goes
        # dormant on its own after about thirty idle seconds, and evaluating
        # in it over CDP does not wake it — on a slow machine the worker
        # that DevTools listed a moment ago can already be gone, and the
        # wait then polls a target nothing will ever answer. The content
        # script's keepalive port is an event the worker listens for, so
        # loading the page is what revives it, exactly as an ordinary
        # browsing session does.
        request_marker = _fixture_request_marker(page_url)
        try:
            cdp_call(node, page_target, 'Page.navigate', {'url': page_url})
        except CDPTimeout as why:
            request_arrived = _fixture_request_arrived(
                page_url, request_marker)
            # Arrival is an independent in-process observation, but it cannot
            # identify whether the browser, CDP transport, or repository kept
            # the navigation reply from arriving before the deadline.
            raise FirstNavigationTimeout(
                page_url, request_arrived) from why
        worker_target = _reached_worker(
            node, browser, workers, devtools_port, worker_script)
        with _environment_verdicts_closed():
            yield from _configured_fixture(
                node, bridge_url, token, worker_target, devtools_port,
                worker_script, page_target, page_url)
    except BrowserEnvironmentSkipped:
        # Reached only after the launch, so this is the one state a skip
        # cannot be trusted on: our extension produced no worker, which is
        # either the machine failing MV3 outright or our source failing to
        # load. The control extension tells those apart, and raises instead
        # of returning when the machine has proven itself.
        _worker_absence_verdict(
            node, browser, extension, worker_script, tmp)
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _reached_worker(node, browser, workers, devtools_port, worker_script):
    """Return the target after this extension's worker answers its probe."""
    deadline = time.time() + 30
    last_error = 'no evaluation was attempted'
    answered = False
    while time.time() < deadline:
        worker_target, reached, error = ready_worker(node, workers)
        answered = answered or reached
        if worker_target:
            return worker_target
        last_error = error
        try:
            workers = _worker_targets(
                _devtools_targets(devtools_port), worker_script)
        except (OSError, ValueError) as why:
            last_error = f'listing DevTools targets failed: {why}'
        time.sleep(0.5)
    if answered:
        states = [worker_state(node, item['webSocketDebuggerUrl'])
                  for item in workers]
        raise AssertionError(
            'the extension service worker never finished loading: '
            'DevTools exposed its target and the fixture page was '
            f'loaded to wake it. Last: {last_error}. Worker states: '
            f'{states}')
    raise BrowserEnvironmentSkipped(
        'this browser never let the extension worker be reached '
        f'over the debugger: {_browser_version(browser)} — {last_error}')


def _configured_fixture(node, bridge_url, token, worker_target,
                        devtools_port, worker_script, page_target, page_url):
    """Configure the reached worker, load the page, and yield the tab."""
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
        time.sleep(0.5)
        try:
            workers = _worker_targets(
                _devtools_targets(devtools_port), worker_script)
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
        time.sleep(0.25)
    else:
        raise AssertionError(
            'the fixture page never set __evalPageReady: ' + page_url)

    cdp_eval(node, worker_target, 'registerAllTabs()')
    deadline = time.time() + 15
    last_tabs = None
    while time.time() < deadline:
        query = urllib.parse.urlencode({'token': token})
        status, tabs = _util.get_json(bridge_url + '/tabs?' + query)
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
