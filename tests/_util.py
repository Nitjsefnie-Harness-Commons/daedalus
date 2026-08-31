"""Shared helpers for the Daedalus suites.

Not a suite itself — run_tests.py only loads `test_*.py`.

Stdlib only and OS-agnostic: these run on Linux, macOS and Windows in CI, and a
POSIX-only assumption here would surface as a test failure rather than as the
platform difference it actually is.
"""
import contextlib
import http.client
import importlib.util
import json
import os
import re
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

from _teardown import settle

ROOT = Path(__file__).resolve().parents[1]
# run_tests.py starts the sorted suites in a process wave, so each process's
# first bridge start pays its cold import costs alongside the other firsts.
COLD_START_TIMEOUT = 60
WARM_START_TIMEOUT = 20
DRAIN_JOIN_TIMEOUT = 1
_bridge_started = False


def _is_wsl_launcher(candidate):
    """True for the WSL launchers a Windows PATH puts ahead of Git's Bash."""
    lowered = candidate.lower().replace('/', '\\')
    return (
        '\\windows\\system32\\bash.exe' in lowered
        or '\\windows\\syswow64\\bash.exe' in lowered
        or '\\microsoft\\windowsapps\\bash.exe' in lowered
    )


def bash_candidates(path, windows, exists=os.path.isfile,
                    program_files=None, program_files_x86=None,
                    local_app_data=None):
    """Bash executables to try, most preferred first.

    On Windows a bare program name resolves the system WSL launcher ahead of
    the Git-for-Windows Bash executable: System32 sits near the front of PATH
    and its `bash.exe` starts a Linux VM before it says anything. Candidates
    are therefore ordered Git-for-Windows first — the PATH directories that
    are not WSL launchers, then the standard install locations — and the WSL
    launchers come last, so a machine with only those still resolves rather
    than failing in the resolver.
    """
    separator = ';' if windows else ':'
    name = 'bash.exe' if windows else 'bash'
    found = []
    for directory in (path or '').split(separator):
        if not directory:
            continue
        candidate = directory.rstrip('/\\') + ('\\' if windows else '/') + name
        if exists(candidate):
            found.append(candidate)
    if not windows:
        return found
    on_path = [item for item in found if not _is_wsl_launcher(item)]
    launchers = [item for item in found if _is_wsl_launcher(item)]
    installs = []
    for base in (program_files, program_files_x86):
        for sub in ('Git\\bin', 'Git\\usr\\bin'):
            if base:
                installs.append(base.rstrip('/\\') + '\\' + sub + '\\' + name)
    if local_app_data:
        for sub in ('Programs\\Git\\bin', 'Programs\\Git\\usr\\bin'):
            installs.append(
                local_app_data.rstrip('/\\') + '\\' + sub + '\\' + name)
    return on_path + [item for item in installs if exists(item)] + launchers


def workflow_bash():
    """Resolve Git-for-Windows Bash through PATH, not a bare WSL launcher.

    On Windows, a bare program name can select the system WSL launcher ahead
    of the Git-for-Windows Bash executable on PATH; `bash_candidates` owns the
    ordering that prevents it, and the WSL launcher is still returned when it
    is the only Bash there is, so the suite that needed it fails with its own
    signal rather than a resolver error.

    Relative executable candidates are made absolute without resolving
    symlinks.
    """
    for candidate in bash_candidates(
            os.environ.get('PATH', ''), sys.platform.startswith('win'),
            program_files=os.environ.get('ProgramFiles'),
            program_files_x86=os.environ.get('ProgramFiles(x86)'),
            local_app_data=os.environ.get('LOCALAPPDATA')):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return (candidate if os.path.isabs(candidate)
                    else os.path.abspath(candidate))
    raise AssertionError('bash is required to execute the workflow shell')


def coverage_free_environment(environment):
    """Copy an environment without names consumed by coverage.py."""
    return {
        name: value for name, value in environment.items()
        if not name.startswith('COVERAGE_')
    }


def child_coverage(mode, environment=None, cwd=None):
    """Declare whether one subprocess keeps the coverage collector.

    Every launch into a working directory the coverage paths do not map back
    onto the repository makes this declaration at its `env=`: 'scrub' strips
    the collector so the child cannot record against paths that vanish with
    the temporary tree, and 'keep' retains it where the tree is mapped and
    the child's coverage is wanted. The guard in
    tests/test_coverage_environment.py reads the declaration syntactically.

    A 'keep' must also prove at runtime that its tree is mapped: pyproject's
    [tool.coverage.paths] maps repo = [".", "*/tree"] and nothing else, so
    the launch's cwd is required and must have a `tree` component. A renamed
    directory fails here rather than later in `coverage report`. It proves
    the retention too: an environment the caller already scrubbed keeps
    nothing, so a collector name this process carries must survive into the
    child's.
    """
    if environment is None:
        environment = os.environ
    if mode == 'scrub':
        # subprocess serializes env.items(), so that is the view to validate.
        scrubbed = dict(coverage_free_environment(environment).items())
        leaked = sorted(
            name for name in scrubbed if name.startswith('COVERAGE_'))
        if leaked:
            raise ValueError(
                "child_coverage('scrub') retained coverage names: "
                + ', '.join(leaked))
        return scrubbed
    if mode == 'keep':
        if cwd is None:
            raise ValueError(
                "child_coverage('keep') requires the launch's cwd")
        path = Path(cwd)
        if '..' in path.parts or 'tree' not in path.resolve().parts:
            raise ValueError(
                "child_coverage('keep') outside a mapped '*/tree' tree: "
                f'{cwd}')
        kept = dict(environment.items())
        dropped = sorted(
            name for name in os.environ
            if name.startswith('COVERAGE_') and name not in kept)
        if dropped:
            raise ValueError(
                "child_coverage('keep') dropped coverage names: "
                + ', '.join(dropped))
        return kept
    raise ValueError(
        f"child_coverage mode must be 'scrub' or 'keep': {mode!r}")


def startup_timeout():
    """The allowance the next bridge start in this process gets."""
    return WARM_START_TIMEOUT if _bridge_started else COLD_START_TIMEOUT


def load(path, name=None):
    """Import a module by path without running its __main__ block.

    The repository root is importable while the module executes.
    """
    path = str(path)
    name = name or ('mod_' + os.path.splitext(os.path.basename(path))[0]
                    .replace('-', '_'))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    root = str(ROOT)
    added_root = root not in sys.path
    if added_root:
        sys.path.insert(0, root)
    try:
        spec.loader.exec_module(mod)
    finally:
        if added_root:
            sys.path.remove(root)
    return mod


_PARENT_WATCH = load(
    ROOT / 'daedalus_bridge' / 'parent_watch.py', 'fixture_parent_watch')


def log_safe_cases():
    """The contract both log-safe implementations in this tree must satisfy.

    The shared log_safe.py module serves the bridge and MCP entry points. The
    standalone scripts/gen_gitignore.py keeps a behavior-identical copy because
    the repository root is not on its import path. Their suites run this one
    table against both implementations, and an MCP-suite meta-test proves a
    deliberately divergent standalone copy fails it: values that must pass
    through in full, values that must be backslash-escaped, and values whose
    rendering must hit the fixed fallback rather than raise or escape to the
    caller as a non-string.
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

    thread = threading.Thread(target=pump, daemon=True,
                              name='bridge-stdout-drain')
    setattr(proc, '_daedalus_drain_thread', thread)
    thread.start()
    return collected


def _startup_observations(proc, drained, waited):
    """Render one snapshot of the child's state and captured output."""
    lines = list(drained)
    code = proc.poll()
    drain_incomplete = False
    if code is not None:
        thread = getattr(proc, '_daedalus_drain_thread', None)
        if thread is not None:
            thread.join(timeout=DRAIN_JOIN_TIMEOUT)
            drain_incomplete = thread.is_alive()
            lines = list(drained)
    state = ('still running' if code is None
             else f'exited with code {code}')
    capture = f'{len(lines)} line(s) captured'
    if drain_incomplete:
        capture += '; drain timed out before EOF'
    return (f'waited {waited:.1f}s, child {state}, '
            f'{capture}:\n' + ''.join(lines))


LISTENING = re.compile(r'\[Daedalus\] Listening on 127\.0\.0\.1:(\d+)')


def listening_port(lines):
    """The port off the bridge's announcement, or None until it arrives."""
    for line in list(lines):
        match = LISTENING.search(line)
        if match:
            return int(match.group(1))
    return None


def listening_line(lines):
    """The announcement itself, or None until it arrives.

    Callers that want the line rather than the port share this match so one
    spelling of the announcement serves the whole harness.
    """
    for line in list(lines):
        if LISTENING.search(line):
            return line
    return None


def await_listening_line(proc, drained, timeout=WARM_START_TIMEOUT):
    """Return the port the child actually bound, read from its Listening line.

    server.py prints the line only after ThreadingHTTPServer has bound, so
    the number it carries is the bound port itself, never a guess.

    The announcement is SEARCHED FOR across every line the child has printed
    so far, not assumed to be the first one. A bridge prints whatever its
    platform gives it cause to — a malloc-tuning diagnostic where mallopt is
    not a glibc symbol before it gets that far, an MCP bootstrap failure from
    its own thread at whatever moment that front end's missing dependencies
    surface — and a reader that inspected only the first line would take one
    of those for the announcement, miss the port, and time out on a bridge
    that came up fine.

    The scan stays bounded in both directions: a child that exits is
    reported the moment it does, and one that stays up without announcing
    fails at `timeout`. Either way the failure carries the child's captured
    output, which is what says which line arrived instead.
    """
    started = time.time()
    deadline = started + timeout
    seen = 0
    while True:
        pending = drained[seen:]
        seen += len(pending)
        port = listening_port(pending)
        if port is not None:
            return port
        if proc.poll() is not None:
            raise RuntimeError(
                'bridge exited during startup: '
                + _startup_observations(
                    proc, drained, time.time() - started))
        if time.time() > deadline:
            raise RuntimeError(
                f'bridge did not announce its port in {timeout}s: '
                + _startup_observations(
                    proc, drained, time.time() - started))
        time.sleep(0.05)


@contextlib.contextmanager
def bridge(tmp, env=None, output=None, proc_out=None):
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

    `proc_out` receives the child itself. A caller waiting on something the
    child prints after readiness has to wait without a deadline, and the
    child dying is the only thing such a wait can give up on.
    """
    global _bridge_started

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
    proc, write_fd = _PARENT_WATCH.spawn(
        [sys.executable, str(ROOT / 'server.py')], env=child_env,
        cwd=str(ROOT), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    try:
        if proc_out is not None:
            proc_out.append(proc)
        drained = drain_lines(proc, output)
        timeout = startup_timeout()
        port = await_listening_line(proc, drained, timeout=timeout)
        base = f'http://127.0.0.1:{port}'
        started = time.time()
        deadline = started + timeout
        while True:
            if proc.poll() is not None:
                raise RuntimeError(
                    'bridge exited during startup: '
                    + _startup_observations(
                        proc, drained, time.time() - started))
            try:
                get(base + '/health')
                break
            except (urllib.error.URLError, OSError) as exc:
                if time.time() > deadline:
                    raise RuntimeError(
                        f'bridge did not answer /health in {timeout}s: '
                        + _startup_observations(
                            proc, drained,
                            time.time() - started)) from exc
                time.sleep(0.05)
        _bridge_started = True
        yield base, docroot
    finally:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        finally:
            os.close(write_fd)


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


def header_stream(base, target, headers):
    """Open a stream whose credential travels in headers, not the target."""
    port = int(base.rsplit(':', 1)[1])
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    conn.putrequest('GET', target)
    for name, value in headers:
        conn.putheader(name, value)
    conn.endheaders()
    return conn, conn.getresponse()


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


def runner(tests, tmp_prefix='daedalustests_', requires=None):
    """Shared main(): run every callable, print PASS/FAIL, return exit code.

    Each test takes one argument: an isolated temp dir, handed over fully
    resolved — macOS resolves /var to /private/var, and a test comparing a path
    it was given against a path the code produced would otherwise see two
    spellings of one directory and call them different.

    `requires` names an external dependency the WHOLE suite needs — a real
    browser, say. A suite that verified nothing is normally an aggregate
    failure, because a suite whose every test skipped is indistinguishable
    from a broken one; a suite that says what it needs is the one case where
    it is distinguishable, and the aggregate reports it as unrun rather than
    as verified. Only pass it where every test genuinely depends on the same
    absent thing.
    """
    _report_safely()
    failed, skipped = [], []
    # The acquisition stays outside the try: if it raises, `td` is unbound and
    # the finally would report an UnboundLocalError over the original OSError.
    td = tempfile.TemporaryDirectory(prefix=tmp_prefix)
    try:
        tmp = os.path.realpath(td.name)
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
    finally:
        if not settle(td.cleanup):
            print(f'  WARN  temporary tree left behind: {td.name}')
    passed = len(tests) - len(failed) - len(skipped)
    summary = f'\n{passed}/{len(tests)} passed'
    if skipped:
        summary += f', {len(skipped)} skipped'
    print(summary)
    _write_summary(len(tests), passed, len(skipped), len(failed), requires)
    return 1 if failed else 0


def _write_summary(total, passed, skipped, failed, requires=None):
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
                       'skipped': skipped, 'failed': failed,
                       'requires': requires}, handle)
    except OSError:
        # Not swallowed so much as reported elsewhere: run_tests.py reads this
        # file to aggregate counts and treats a suite that produced none as a
        # failure, so a write that fails turns up there rather than here.
        pass


def collect(namespace):
    return [v for k, v in sorted(namespace.items())
            if k.startswith('test_') and callable(v)]
