"""The dashboard suites' Node harness process boundary.

Not a suite itself — run_tests.py only loads `test_*.py`.

The dashboard behaviour suite runs shipped JavaScript modules in short Node
processes. This helper keeps process setup and captured failures consistent.

The JavaScript `bounded` helper races settlement but cannot cancel the losing
work or any handles that work owns. A caller that recovers from its timeout
must cancel those handles itself. The shipped asynchronous harnesses instead
pass timeout failures to `leave`, which flushes the error and exits the child.

Bound-count validation refuses unmodelled shapes before blanking comments. A
shallow scan keeps ordinary comments and plain template strings on the fast
path, but refuses template expressions and slash tokens. This is the correct
direction because `blank_js_comments` requires a consumer that meets an
unmodelled shape to report a violation rather than stay silent. Other string
content is preserved and must not match the whitespace-tolerant bound pattern.
"""
import ctypes
import re
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from _jsread import blank_js_comments
from _repo import ROOT


_DASHBOARD_STEP_TIMEOUT_S = 5
_DASHBOARD_PROCESS_GRACE_S = 5
# Healthy post-kill drains take single-digit to low-hundreds of milliseconds,
# while an inherited pipe writer held one beyond 3.6s. One second leaves room
# for scheduling jitter without letting a genuinely held pipe stall diagnosis.
_DASHBOARD_DRAIN_TIMEOUT_S = 1
_BOUNDED_AWAIT = re.compile(r'\bawait\s+bounded\s*\(')

_DASHBOARD_PRELUDE = r"""
const _dashnodeSetTimeout = globalThis.setTimeout;
const _dashnodeClearTimeout = globalThis.clearTimeout;

function phase(label) {
  process.stderr.write('[phase] ' + label + '\n');
}

function bounded(work, label, timeoutMs) {
  let timer;
  const guard = new Promise((_resolve, reject) => {
    timer = _dashnodeSetTimeout(
      () => reject(new Error('timed out waiting for ' + label)), timeoutMs);
  });
  return Promise.race([Promise.resolve(work), guard])
    .finally(() => _dashnodeClearTimeout(timer));
}

function leave(error) {
  const text = (error.stack || String(error)) + '\n';
  process.stderr.write(text, () => process.exit(1));
}
"""


def _ambiguous_bound_shape(source):
    """Return a shape needing more than comment blanking can provide."""
    index, end = 0, len(source)
    while index < end:
        char = source[index]
        pair = source[index:index + 2]
        if char in "'\"":
            quote = char
            index += 1
            while index < end and source[index] != quote:
                index += 2 if source[index] == '\\' else 1
            index += index < end
            continue
        if char == '`':
            index += 1
            while index < end and source[index] != '`':
                if source[index] == '\\':
                    index += 2
                elif source[index:index + 2] == '${':
                    return 'template expression'
                else:
                    index += 1
            index += index < end
            continue
        if pair == '//':
            terminators = [
                position for marker in ('\r', '\n', '\u2028', '\u2029')
                if (position := source.find(marker, index + 2)) >= 0]
            index = min(terminators, default=end)
            continue
        if pair == '/*':
            close = source.find('*/', index + 2)
            index = end if close < 0 else close + 2
            continue
        if char == '/':
            return 'slash token'
        index += 1
    return None


@dataclass(frozen=True)
class DashboardNodeHarness:
    """One Node harness source and its validated process-bound metadata."""

    source: str
    bounded_steps: int
    module: bool = False
    arguments: tuple[str | Path, ...] = ()

    def __post_init__(self):
        unsupported = _ambiguous_bound_shape(self.source)
        if unsupported:
            detail = ''
            if unsupported == 'slash token':
                detail = (
                    '; slash tokens include division and regex literals, '
                    'so hoist the expression out of the harness source or '
                    'restructure it')
            raise ValueError(
                'dashboard harness bound count cannot inspect '
                f'{unsupported} in dashboard harness source{detail}')
        source = blank_js_comments(self.source)
        actual = len(_BOUNDED_AWAIT.findall(source))
        if actual != self.bounded_steps:
            raise ValueError(
                f'dashboard harness declares {self.bounded_steps} bounded '
                f'steps but performs {actual}')


def dashboard_child_timeout(bounded_steps,
                            step_timeout=_DASHBOARD_STEP_TIMEOUT_S,
                            process_grace=_DASHBOARD_PROCESS_GRACE_S):
    """How long to let a dashboard harness run before killing it.

    Every awaited step inside a harness is bounded and names what it was
    waiting for. This backstop preserves the child's pipes and last phase,
    but it still has to outlast the worst inner path — one full bound per
    declared step, plus independent process grace. That lets the more specific
    inner failure report first.
    """
    return step_timeout * bounded_steps + process_grace


def _output_text(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value


def _latest_output(latest, earlier):
    if not latest:
        return earlier
    if not earlier:
        return latest
    if isinstance(latest, bytes) and isinstance(earlier, bytes):
        if latest.startswith(earlier):
            return latest
        if earlier.startswith(latest):
            return earlier
        return earlier + latest
    return _output_text(earlier) + _output_text(latest)


@dataclass(frozen=True)
class _OuterTimeoutAttempt:
    attempt: int
    pid: int
    argv: tuple[str, ...]
    timeout_s: float
    kill_issued: bool
    drain_outcome: str
    returncode: int | None
    stdout: str
    stderr: str
    last_phase: str
    drain_duration_s: float
    duration_s: float


class _DashboardOuterTimeout(Exception):
    def __init__(self, record, *, retryable=True):
        super().__init__()
        self.record = record
        self.retryable = retryable


def _cancel_windows_synchronous_io(thread):
    native_id = thread.native_id
    if native_id is None:
        raise RuntimeError('dashboard reader thread has no native id')
    win_dll = getattr(ctypes, 'WinDLL')
    get_last_error = getattr(ctypes, 'get_last_error')
    win_error = getattr(ctypes, 'WinError')
    kernel32 = win_dll('kernel32', use_last_error=True)
    open_thread = kernel32.OpenThread
    open_thread.argtypes = (
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_thread.restype = wintypes.HANDLE
    cancel_io = kernel32.CancelSynchronousIo
    cancel_io.argtypes = (wintypes.HANDLE,)
    cancel_io.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    handle = open_thread(0x0001, False, native_id)
    if not handle:
        raise win_error(get_last_error())
    try:
        if not cancel_io(handle):
            error = get_last_error()
            if error != 1168:
                raise win_error(error)
    finally:
        close_handle(handle)


def _close_process_pipes(process):
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()


def _finish_windows_pipe_readers(process, deadline):
    pairs = (
        (process.stdout, getattr(process, 'stdout_thread', None)),
        (process.stderr, getattr(process, 'stderr_thread', None)),
    )
    if any(stream is not None and thread is None
           for stream, thread in pairs):
        raise RuntimeError('dashboard process reader thread is missing')
    threads = tuple(
        thread for stream, thread in pairs
        if stream is not None and thread is not None)
    failures = []
    for thread in threads:
        if thread.is_alive():
            try:
                _cancel_windows_synchronous_io(thread)
            except Exception as failure:  # pylint: disable=W0718
                failures.append(failure)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    alive = tuple(thread for thread in threads if thread.is_alive())
    if alive:
        raise RuntimeError('dashboard process reader cleanup timed out')
    _close_process_pipes(process)
    if failures:
        raise RuntimeError(
            'dashboard process reader cancellation failed') from failures[0]


def _settled_windows_output(process, name):
    thread = getattr(process, f'{name}_thread', None)
    if thread is None or thread.is_alive():
        return ''
    chunks = getattr(process, f'_{name}_buff', None)
    if not chunks:
        return ''
    return chunks[0]


def _format_timeout_attempt(record):
    return (
        f'attempt {record.attempt}:\n'
        f'  pid: {record.pid}\n'
        f'  executable: {record.argv[0]!r}\n'
        f'  argv: {record.argv!r}\n'
        f'  outer timeout: {record.timeout_s}s\n'
        f'  kill issued: {"yes" if record.kill_issued else "no"}\n'
        f'  drain outcome: {record.drain_outcome}\n'
        f'  return code: {record.returncode!r}\n'
        f'  duration: {record.duration_s:.3f}s\n'
        '  dashboard harness outer backstop timed out after '
        f'{record.timeout_s}s; '
        f'drain timed out: '
        f'{"yes" if record.drain_outcome == "timed out" else "no"}; '
        f'drain took {record.drain_duration_s:.3f}s; '
        f'last phase: {record.last_phase}; '
        f'stdout: {record.stdout!r}; stderr: {record.stderr!r}')


def _run_dashboard_node_once(
        harness: DashboardNodeHarness, *, attempt: int
) -> subprocess.CompletedProcess[str]:
    node = shutil.which('node')
    if not node:
        raise AssertionError('node is required to execute dashboard harnesses')
    options = ['--input-type=module'] if harness.module else []
    timeout_scale = attempt
    step_timeout = _DASHBOARD_STEP_TIMEOUT_S * timeout_scale
    process_grace = _DASHBOARD_PROCESS_GRACE_S * timeout_scale
    step_timeout_ms = round(step_timeout * 1000)
    timeout_source = (
        f'const _dashnodeStepTimeoutMs = {step_timeout_ms};\n')
    command = [
        node, *options, '--eval',
        _DASHBOARD_PRELUDE + timeout_source + harness.source,
        *map(str, harness.arguments),
    ]
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace')
    timeout = dashboard_child_timeout(
        harness.bounded_steps, step_timeout, process_grace)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as failure:
        process.kill()
        cleanup_failure = None
        drain_started = time.monotonic()
        try:
            stdout, stderr = process.communicate(
                timeout=_DASHBOARD_DRAIN_TIMEOUT_S)
        except subprocess.TimeoutExpired as drain_failure:
            drain_outcome = 'timed out'
            stdout = _latest_output(drain_failure.stdout, failure.stdout)
            stderr = _latest_output(drain_failure.stderr, failure.stderr)
        except Exception as drain_failure:  # pylint: disable=W0718
            drain_outcome = (
                f'raised {type(drain_failure).__name__}: {drain_failure}')
            stdout = _output_text(failure.stdout)
            stderr = _output_text(failure.stderr)
            cleanup_failure = drain_failure
        else:
            drain_outcome = 'completed'
        if drain_outcome != 'completed':
            cleanup_deadline = (
                time.monotonic() + _DASHBOARD_DRAIN_TIMEOUT_S)
            try:
                if sys.platform == 'win32':
                    _finish_windows_pipe_readers(
                        process, cleanup_deadline)
                else:
                    _close_process_pipes(process)
            except Exception as settle_failure:  # pylint: disable=W0718
                if cleanup_failure is None:
                    cleanup_failure = settle_failure
            try:
                process.wait(timeout=max(
                    0.0, cleanup_deadline - time.monotonic()))
            except subprocess.TimeoutExpired as wait_failure:
                if cleanup_failure is None:
                    cleanup_failure = wait_failure
            if sys.platform == 'win32':
                stdout = _latest_output(
                    _settled_windows_output(process, 'stdout'), stdout)
                stderr = _latest_output(
                    _settled_windows_output(process, 'stderr'), stderr)
        stdout = _output_text(stdout)
        stderr = _output_text(stderr)
        phases = re.findall(r'^\[phase\] (.+)$', stderr, re.MULTILINE)
        last_phase = phases[-1] if phases else 'none recorded'
        drain_duration = time.monotonic() - drain_started
        record = _OuterTimeoutAttempt(
            attempt=attempt,
            pid=process.pid,
            argv=tuple(command),
            timeout_s=timeout,
            kill_issued=True,
            drain_outcome=drain_outcome,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            last_phase=last_phase,
            drain_duration_s=drain_duration,
            duration_s=time.monotonic() - started,
        )
        timeout_failure = _DashboardOuterTimeout(
            record, retryable=drain_outcome == 'completed')
        raise timeout_failure from (cleanup_failure or failure)
    if process.returncode != 0:
        raise AssertionError((process.returncode, stdout, stderr))
    return subprocess.CompletedProcess(
        command, process.returncode, stdout, stderr)


def run_dashboard_node(
        harness: DashboardNodeHarness
) -> subprocess.CompletedProcess[str]:
    """Run a dashboard harness, retrying one Windows outer timeout."""
    attempts = 2 if sys.platform == 'win32' else 1
    timeout_records = []
    for attempt in range(1, attempts + 1):
        try:
            result = _run_dashboard_node_once(harness, attempt=attempt)
        except _DashboardOuterTimeout as failure:
            timeout_records.append(failure.record)
            if failure.retryable and attempt < attempts:
                continue
            count = len(timeout_records)
            suffix = 'attempt' if count == 1 else 'attempts'
            records = '\n'.join(
                _format_timeout_attempt(record)
                for record in timeout_records)
            raise AssertionError(
                f'dashboard node outer timeout after {count} {suffix}\n'
                f'{records}') from failure
        if timeout_records:
            record = timeout_records[0]
            sys.stderr.write(
                'dashboard node recovered after outer timeout: '
                f'attempt 1, pid {record.pid}, '
                f'drain {record.drain_outcome}, '
                f'last phase {record.last_phase}\n')
        return result
    raise AssertionError(
        'dashboard node retry loop completed without a result')
