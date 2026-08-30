"""CDP and service-worker machinery for the real-browser fixture."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from _evalpages import CDP_CALL_HARNESS, CDP_RESPONSE_DEADLINE_MS
from _evalpages import CDP_TIMEOUT_EXIT_CODE
from _realbrowser_errors import CDPEvaluationError, CDPTimeout
from _repo import ROOT


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


def _retire_browser(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


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


def _listed_workers(profile, declared):
    port = _devtools_port(profile)
    if not port:
        return []
    try:
        return _worker_targets(_devtools_targets(port), declared)
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
        f'never answered in the same launch — {_browser_version(browser)} '
        'demonstrably runs an unpacked MV3 worker, so the absence of ours '
        "is this repository's, not the machine's")


def _contention_observation(browser, extension, worker_script):
    return (
        f'{extension} declares {worker_script}, and that worker answered in '
        'the diagnosis launch; '
        f'{_browser_version(browser)} loaded this source, so its absence '
        'from the fixture launch was browser-launch contention')


def _worker_absence_verdict(node, browser, extension, worker_script, tmp):
    """Return the owner of a fixture launch that produced no worker."""
    control = _control_extension(tmp)
    profile = Path(tmp) / 'control-profile'
    loaded = ','.join(
        str(Path(item).resolve()) for item in (extension, control))
    process = subprocess.Popen(
        _browser_args(browser, loaded, profile), cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    control_answered = False
    try:
        deadline = time.time() + WORKER_ABSENCE_DEADLINE
        while time.time() < deadline:
            ours, _reached, _error = ready_worker(
                node, _listed_workers(profile, worker_script))
            if ours:
                return True, _contention_observation(
                    browser, extension, worker_script)
            if _control_worker_answered(
                    node, _listed_workers(profile, CONTROL_WORKER_SCRIPT)):
                control_answered = True
            if process.poll() is not None:
                if control_answered:
                    return False, (
                        'the diagnosis browser exited after the control '
                        'answered but before ours answered')
                return False, (
                    'the diagnosis browser exited before any control '
                    'worker was listed')
            time.sleep(0.5)
    finally:
        _retire_browser(process)
    if control_answered:
        raise AssertionError(
            _absence_guilt(browser, extension, worker_script))
    return False, 'the control extension produced no answering worker either'
