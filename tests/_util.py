"""Shared helpers for the Daedalus suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

Stdlib only and OS-agnostic: these run on Linux, macOS and Windows in CI, and a
POSIX-only assumption here would surface as a test failure rather than as the
platform difference it actually is.
"""
import contextlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import typing
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


_BACKGROUND_OVERLAP_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const nodeCrypto = require('crypto');

const [backgroundPath, commandsText, orderText, resultBase, token,
  waitBetweenText] = process.argv.slice(1);
const commands = JSON.parse(commandsText);
const completionOrder = JSON.parse(orderText);
const waitBetween = waitBetweenText === '1';
const bridgeUrl = resultBase || 'test-bridge';
const pendingCookies = new Map();
const postedResults = [];
const nativeFetch = globalThis.fetch;

function response(status, data) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

async function bridgeFetch(target, init = {}) {
  const url = String(target);
  if (url.endsWith('/result') && init.method === 'POST') {
    const payload = JSON.parse(init.body);
    if (resultBase) {
      const result = await nativeFetch(target, init);
      postedResults.push(payload);
      return result;
    }
    postedResults.push(payload);
    return response(200, { ok: true });
  }
  if (url.includes('/stream?')) return response(503, { error: 'disabled' });
  return response(200, { ok: true });
}

function eventTarget() {
  return { addListener() {} };
}

const chrome = {
  storage: {
    local: {
      get: async () => ({
        'daedalus-token': token,
        'daedalus-server': bridgeUrl,
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
      if (callback) {
        callback([]);
        return undefined;
      }
      return Promise.resolve([]);
    },
  },
  cookies: {
    getAll(details) {
      return new Promise((resolve) => {
        pendingCookies.set(details.domain, () => resolve([{
          domain: details.domain,
          name: 'owner',
          value: details.domain,
        }]));
      });
    },
  },
  debugger: {
    onEvent: eventTarget(),
    onDetach: eventTarget(),
  },
  runtime: {
    onMessage: eventTarget(),
    onConnect: eventTarget(),
    getPlatformInfo() {},
    getManifest: () => ({ version: '0.18.0' }),
  },
  alarms: {
    onAlarm: eventTarget(),
    create() {},
  },
};

const context = vm.createContext({
  chrome,
  fetch: bridgeFetch,
  crypto: { randomUUID: nodeCrypto.randomUUID },
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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(predicate, label, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await delay(10);
  }
  throw new Error('timed out waiting for ' + label);
}

async function waitForResultConsume() {
  const query = resultBase + '/result?token=' + encodeURIComponent(token)
    + '&tab=extension';
  await waitFor(async () => {
    const result = await nativeFetch(query);
    const body = await result.json();
    return body.pending === true;
  }, 'the first result to be consumed');
}

(async () => {
  vm.runInContext(fs.readFileSync(backgroundPath, 'utf8'), context);
  await vm.runInContext('loadConfig()', context);
  context.commands = commands;
  const executions = commands.map((_command, index) =>
    vm.runInContext('dispatchCommand(commands[' + index + '])', context));
  await waitFor(
    () => pendingCookies.size === commands.length,
    'both cookie handlers to start');

  for (let index = 0; index < completionOrder.length; index++) {
    const owner = completionOrder[index];
    const complete = pendingCookies.get(owner);
    if (!complete) throw new Error('missing cookie completion for ' + owner);
    const postedBefore = postedResults.length;
    complete();
    await waitFor(
      () => postedResults.length === postedBefore + 1,
      'result POST for ' + owner);
    if (waitBetween && index + 1 < completionOrder.length) {
      await waitForResultConsume();
    }
  }
  await Promise.all(executions);
  process.stdout.write(JSON.stringify(postedResults.map((item) => ({
    id: item.id,
    owner: item.result[0].value,
    deliveryId: item._did || null,
  }))));
})().catch((error) => {
  process.stderr.write((error.stack || String(error)) + '\n');
  process.exitCode = 1;
});
"""


_OVERLAP_INNER_WAIT_S = 15


def overlap_child_timeout(order, wait_between):
    """How long to let the overlap harness run before killing it.

    Every wait inside the harness is bounded and names what it was waiting
    for; this bound names nothing but the command, and carries the whole
    harness source into the failure. It is a backstop, so it has to outlast
    the worst inner path — one wait for the handlers, one per result, and
    one per gap when the caller waits between them — or a slow machine gets
    the useless message instead of the useful one.
    """
    waits = 1 + len(order) + (len(order) - 1 if wait_between else 0)
    return _OVERLAP_INNER_WAIT_S * (waits + 1)


def run_background_overlap(background, commands, order, result_base='',
                           token='overlap-token', wait_between=False):
    """Run same-id cookie commands through the shipped background worker."""
    node = shutil.which('node')
    if not node:
        raise AssertionError('node is required to execute the extension worker')
    result = subprocess.run(
        [node, '-e', _BACKGROUND_OVERLAP_HARNESS, str(background),
         json.dumps(commands), json.dumps(order), result_base, token,
         '1' if wait_between else '0'],
        cwd=ROOT, capture_output=True, text=True,
        timeout=overlap_child_timeout(order, wait_between))
    if result.returncode != 0:
        raise AssertionError(
            (result.returncode, result.stdout, result.stderr))
    return json.loads(result.stdout)


def load(path, name=None):
    """Import a module by path without running its __main__ block."""
    path = str(path)
    name = name or ('mod_' + os.path.splitext(os.path.basename(path))[0]
                    .replace('-', '_'))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def log_safe_cases():
    """The contract every _log_safe copy in this tree must satisfy.

    The helper ships as three behavior-identical copies (server.py,
    scripts/gen_gitignore.py, mcp_server.py) that cannot import one another,
    so each copy's suite — test_bridge_http, test_repo_contract and
    test_mcp_server — runs this one table against it, and a meta-test in the
    MCP suite proves a deliberately divergent copy fails it: values that must
    pass through in full, values that must be backslash-escaped, and values
    whose rendering must hit the fixed fallback rather than raise or escape
    to the caller as a non-string.
    """
    class BrokenStr(Exception):
        def __str__(self):
            raise RuntimeError('broken __str__')

    class EvilStr(str):
        """str() returns this subclass unchanged, so .encode() dispatches to it."""
        # The invalid shape is the point: handing self back so the caller's
        # .encode() runs this subclass's code outside any guard.
        def __str__(self):  # pylint: disable=invalid-str-returned
            return self

        def encode(self, *args, **kwargs):
            raise RuntimeError('evil encode')

    class BadFormat:
        """What a hostile decode() hands back: interpolating it raises."""
        def __format__(self, _spec):
            raise RuntimeError('evil format')

    class HostileChain(str):
        """str() returns this unchanged; decode() returns a non-string."""
        def __str__(self):  # pylint: disable=invalid-str-returned
            return self

        def encode(self, *args, **kwargs):
            return self

        def decode(self, *args, **kwargs):
            return BadFormat()

    large = 'x' * 200000
    return (
        (b'\xff', repr(b'\xff')),
        (None, 'None'),
        (large, large),
        (10 ** 5000, '<unprintable value>'),  # past the 4300-digit str() limit
        ('\ud800', '\\ud800'),
        ('\udc80', '\\udc80'),
        ('\udcff', '\\udcff'),
        (BrokenStr('x'), '<unprintable value>'),
        (EvilStr('x'), '<unprintable value>'),
        (HostileChain('x'), '<unprintable value>'),
    )


BIND_ERROR_MARKERS = (
    'Address already in use',                   # POSIX
    'Only one usage of each socket address',    # Windows WSAEADDRINUSE
    'forbidden by its access permissions',      # Windows WSAEACCES
)


def is_bind_error(text):
    """Whether `text` carries the operating system's own bind refusal.

    What these tests contract is that a bind failure arrives AS ITSELF —
    not retried into a fresh port, not flattened into a timeout. Which
    sentence the operating system uses to say it is not part of that
    contract, and pinning the POSIX wording failed Windows for reporting
    its own error correctly.
    """
    return any(marker in text for marker in BIND_ERROR_MARKERS)


class Skipped(Exception):
    """Raised by a test that cannot hold on this platform or install."""


def skip(reason) -> typing.NoReturn:
    """End the running test as skipped, with a reason the log will show.

    Annotated NoReturn because it is: a caller that ends in `skip(...)` has
    no fall-through path, and without the annotation every such caller reads
    as one function that sometimes returns a value and sometimes None.
    """
    raise Skipped(reason)


def require_undecodable_names(directory):
    """Skip unless this filesystem will hold a name that is not valid UTF-8.

    Several tests exist because a Linux filesystem stores raw bytes, so a
    filename can arrive that no decoder can read, and the bridge has to
    survive it. The behaviour is real and the tests are worth keeping — but
    APFS rejects such a name with `Illegal byte sequence` and Windows refuses
    to decode it at all, so on those platforms the fixture cannot be built
    and the test was reporting the platform's refusal as a bridge failure.

    Probe once, here, rather than in each test: a caller that gets past this
    is on a filesystem where the scenario under test can actually occur.
    """
    probe = os.fsencode(str(directory)) + b'/\xffprobe'
    try:
        os.mkdir(probe)
    except (OSError, UnicodeError, ValueError) as why:
        skip('this filesystem will not hold a name that is not valid UTF-8 '
             f'({type(why).__name__})')
    else:
        os.rmdir(probe)


_drawn_ports = set()


def free_port():
    """A port nothing is listening on, for a test that wants a dead address.

    No fixture draws numbers any more: both the bridge child and the MCP
    listener bind port 0 and announce the port they actually got, so there is
    no window between choosing a number and binding it. What is left is the
    opposite need — the CLI connection-failure test, which wants an address
    it can be sure nothing answers on, and never binds it at all.

    Two tests still replace this helper with a squatted port. They are
    guarding the fixtures rather than calling it: if a fixture went back to
    drawing a number, it would bind the squatter's port and fail.

    A number this helper has handed out is never handed out again, so a port
    one test reserved cannot be reallocated to another started afterwards.
    The redraw is bounded: a process that has handed out a substantial
    fraction of the ephemeral range gets an explicit RuntimeError rather than
    a silent infinite retry.
    """
    for _attempt in range(100):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        if port not in _drawn_ports:
            _drawn_ports.add(port)
            return port
    raise RuntimeError(
        'free_port: 100 draws without a fresh port — the no-repeat set has '
        'consumed too much of the ephemeral range')


def drain_lines(proc, collected=None):
    """Start relaying the child's stdout lines into a list, and return it.

    Nothing else may read the pipe: the readiness line the fixture waits for
    travels on this stream, and an undrained pipe would eventually fill and
    block the child.
    """
    collected = [] if collected is None else collected

    def pump():
        for line in proc.stdout:
            collected.append(line)

    threading.Thread(target=pump, daemon=True,
                     name='bridge-stdout-drain').start()
    return collected


def await_listening_line(proc, drained, timeout=20):
    """Return the port the child actually bound, read from its Listening line.

    server.py prints the line only after ThreadingHTTPServer has bound, so
    the number it carries is the bound port itself, never a guess.

    The announcement is SEARCHED FOR across every line the child has printed
    so far, not assumed to be the first one. A bridge prints whatever its
    platform gives it cause to before it gets that far — a malloc-tuning
    diagnostic where mallopt is not a glibc symbol, an MCP bootstrap failure
    where that front end's dependencies are missing — and a reader that
    inspected only the first line would take one of those for the
    announcement, miss the port, and time out on a bridge that came up fine.

    The scan stays bounded in both directions: a child that exits is
    reported the moment it does, and one that stays up without announcing
    fails at `timeout`. Either way the failure carries the child's captured
    output, which is what says which line arrived instead.
    """
    deadline = time.time() + timeout
    seen = 0
    while True:
        pending = drained[seen:]
        seen += len(pending)
        for line in pending:
            match = re.search(r'\[Daedalus\] Listening on 127\.0\.0\.1:(\d+)',
                              line)
            if match:
                return int(match.group(1))
        if proc.poll() is not None:
            raise RuntimeError('bridge exited during startup:\n'
                               + ''.join(drained))
        if time.time() > deadline:
            raise RuntimeError(
                f'bridge did not announce its port in {timeout}s:\n'
                + ''.join(drained))
        time.sleep(0.05)


@contextlib.contextmanager
def bridge(tmp, env=None, output=None):
    """Run the real server.py against a throwaway docroot.

    Yields (base_url, docroot). The bridge is stdlib-only, so this is a real
    end-to-end exercise of the HTTP surface rather than a mock of it — which is
    the point: every bug worth catching here lives in request parsing, path
    handling or the queue, none of which a mock would reproduce.

    The child binds port 0 and announces the port it actually got on its
    stdout Listening line, which a drain thread relays (and keeps the pipe
    from ever filling) into `output` when the caller passes a list. There is
    no drawn number for a concurrent process to take: the window between
    choosing a port and binding it no longer exists.

    stdout and stderr are captured and, on a startup failure, raised with the
    output attached; a bridge that dies silently would otherwise show up as an
    unexplained connection error in whichever test ran first.
    """
    docroot = Path(tmp) / 'docroot'
    docroot.mkdir(parents=True, exist_ok=True)
    child_env = dict(os.environ)
    child_env.update({
        'DAEDALUS_DIR': str(docroot),
        'DAEDALUS_PORT': '0',
        # The child's MCP side-thread binds ephemeral too: no test child ever
        # competes for the fixed default port, even across concurrent runs.
        'DAEDALUS_MCP_PORT': '0',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1',
    })
    child_env.update(env or {})
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / 'server.py')],
        cwd=str(ROOT), env=child_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    drained = drain_lines(proc, output)
    try:
        base = f'http://127.0.0.1:{await_listening_line(proc, drained)}'
        deadline = time.time() + 20
        while True:
            if proc.poll() is not None:
                raise RuntimeError('bridge exited during startup:\n'
                                   + ''.join(drained))
            try:
                get(base + '/health')
                break
            except (urllib.error.URLError, OSError) as exc:
                if time.time() > deadline:
                    raise RuntimeError(
                        'bridge did not answer /health in 20s') from exc
                time.sleep(0.05)
        yield base, docroot
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def request(url, method='GET', body=None, headers=None, timeout=10):
    """One HTTP call, returning (status, body-bytes).

    An HTTP error status is a result here, not an exception: most of what these
    suites assert is precisely that the bridge REFUSES something, and a helper
    that raised on 400 would make those assertions awkward enough to skip.
    """
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        hdrs.setdefault('Content-Type', 'application/json')
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get(url, **kw):
    return request(url, 'GET', **kw)


def get_json(url, **kw):
    status, raw = get(url, **kw)
    return status, json.loads(raw or b'null')


def post_json(url, body, **kw):
    status, raw = request(url, 'POST', body=body, **kw)
    return status, json.loads(raw or b'null')


def _assertion_site(exc):
    """`file:line: source` of the assert that failed, for bare asserts."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return ''
    last = frames[-1]
    return f'{os.path.basename(last.filename)}:{last.lineno}: {last.line}'


def _report_safely():
    """Make sure a failure can always be reported.

    A failure detail carries whatever the test was comparing, and on Windows
    the runner's stdout is a legacy code page where `print` raises rather
    than degrading. One failing assertion whose message held an arrow
    therefore aborted the whole file with a UnicodeEncodeError, and every
    test after it never ran — the report was lost along with the tests.

    A runner that cannot say what went wrong is worse than the thing that
    went wrong, so the stream degrades instead: `errors='replace'`
    throughout, and UTF-8 where the output is a pipe or a file, which is
    what `run_tests.py` reads.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            if os.environ.get('PYTHONIOENCODING') or stream.isatty():
                reconfigure(errors='replace')
            else:
                reconfigure(encoding='utf-8', errors='replace')
        except (OSError, ValueError):
            continue


def runner(tests, tmp_prefix='daedalustests_'):
    """Shared main(): run every callable, print PASS/FAIL, return exit code.

    Each test takes one argument: an isolated temp dir, handed over fully
    resolved — macOS resolves /var to /private/var, and a test comparing a path
    it was given against a path the code produced would otherwise see two
    spellings of one directory and call them different.
    """
    _report_safely()
    failed, skipped = [], []
    with tempfile.TemporaryDirectory(prefix=tmp_prefix) as tmp:
        tmp = os.path.realpath(tmp)
        for t in tests:
            d = os.path.join(tmp, t.__name__)
            os.makedirs(d, exist_ok=True)
            try:
                t(d)
                print(f'  PASS  {t.__name__}')
            except Skipped as e:
                skipped.append(t.__name__)
                print(f'  SKIP  {t.__name__}: {e}')
            except AssertionError as e:
                failed.append(t.__name__)
                detail = str(e) or _assertion_site(e)
                print(f'  FAIL  {t.__name__}: {detail}')
            except Exception as e:  # noqa: BLE001
                failed.append(t.__name__)
                print(f'  ERROR {t.__name__}: {type(e).__name__}: {e}')
    passed = len(tests) - len(failed) - len(skipped)
    summary = f'\n{passed}/{len(tests)} passed'
    if skipped:
        summary += f', {len(skipped)} skipped'
    print(summary)
    _write_summary(len(tests), passed, len(skipped), len(failed))
    return 1 if failed else 0


def _write_summary(total, passed, skipped, failed):
    """Hand the counts to the aggregate runner, when one asked for them.

    The aggregate cannot see them otherwise: every suite streams straight to
    the runner's own stdout, and capturing that stream to parse a number back
    out of it would trade live output for the number. A run whose coverage
    was entirely skipped has to be distinguishable from a verified one, and
    an exit code alone cannot say that.
    """
    path = os.environ.get('DAEDALUS_TEST_SUMMARY')
    if not path:
        return
    try:
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({'total': total, 'passed': passed,
                       'skipped': skipped, 'failed': failed}, handle)
    except OSError:
        pass


def collect(namespace):
    return [v for k, v in sorted(namespace.items())
            if k.startswith('test_') and callable(v)]
