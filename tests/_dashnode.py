"""The dashboard suites' Node harness process boundary.

Not a suite itself — run_tests.py only loads `test_*.py`.

The dashboard behaviour suite runs shipped JavaScript modules in short Node
processes. This helper keeps process setup and captured failures consistent.
"""
import re
import shutil
import subprocess
from dataclasses import dataclass

from _repo import ROOT


_DASHBOARD_STEP_TIMEOUT_S = 5
_DASHBOARD_DRAIN_TIMEOUT_S = 0.2
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


@dataclass(frozen=True)
class DashboardNodeHarness:
    """One Node harness source and its validated process-bound metadata."""

    source: str
    bounded_steps: int
    module: bool = False

    def __post_init__(self):
        actual = len(_BOUNDED_AWAIT.findall(self.source))
        if actual != self.bounded_steps:
            raise ValueError(
                f'dashboard harness declares {self.bounded_steps} bounded '
                f'steps but performs {actual}')


def dashboard_child_timeout(bounded_steps,
                            step_timeout=_DASHBOARD_STEP_TIMEOUT_S):
    """How long to let a dashboard harness run before killing it.

    Every awaited step inside a harness is bounded and names what it was
    waiting for. This backstop preserves the child's pipes and last phase,
    but it still has to outlast the worst inner path — one full bound per
    declared step, plus one bound of process grace — so the more specific
    inner failure gets to report first.
    """
    return step_timeout * (bounded_steps + 1)


def _output_text(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value


def _latest_output(latest, earlier):
    if latest not in (None, b'', ''):
        return _output_text(latest)
    return _output_text(earlier)


def run_dashboard_node(harness, *arguments,
                       step_timeout=_DASHBOARD_STEP_TIMEOUT_S):
    """Run one dashboard JavaScript harness with captured output."""
    node = shutil.which('node')
    if not node:
        raise AssertionError('node is required to execute dashboard harnesses')
    options = ['--input-type=module'] if harness.module else []
    step_timeout_ms = round(step_timeout * 1000)
    timeout_source = (
        f'const _dashnodeStepTimeoutMs = {step_timeout_ms};\n')
    command = [
        node, *options, '--eval',
        _DASHBOARD_PRELUDE + timeout_source + harness.source,
        *map(str, arguments),
    ]
    process = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace')
    timeout = dashboard_child_timeout(harness.bounded_steps, step_timeout)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as failure:
        process.kill()
        drain_timed_out = False
        try:
            stdout, stderr = process.communicate(
                timeout=_DASHBOARD_DRAIN_TIMEOUT_S)
        except subprocess.TimeoutExpired as drain_failure:
            drain_timed_out = True
            stdout = _latest_output(drain_failure.stdout, failure.stdout)
            stderr = _latest_output(drain_failure.stderr, failure.stderr)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            try:
                process.wait(timeout=_DASHBOARD_DRAIN_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                pass
        phases = re.findall(r'^\[phase\] (.+)$', stderr, re.MULTILINE)
        last_phase = phases[-1] if phases else 'none recorded'
        raise AssertionError(
            f'dashboard harness outer backstop timed out after {timeout}s; '
            f'drain timed out: {"yes" if drain_timed_out else "no"}; '
            f'last phase: {last_phase}; stdout: {stdout!r}; '
            f'stderr: {stderr!r}'
        ) from failure
    if process.returncode != 0:
        raise AssertionError((process.returncode, stdout, stderr))
    return subprocess.CompletedProcess(
        command, process.returncode, stdout, stderr)
