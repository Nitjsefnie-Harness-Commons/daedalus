"""The dashboard suites' Node harness process boundary.

Not a suite itself — run_tests.py only loads `test_*.py`.

The dashboard behaviour suite runs shipped JavaScript modules in short Node
processes. This helper keeps process setup and captured failures consistent.
"""
import re
import shutil
import subprocess

from _repo import ROOT


_DASHBOARD_STEP_TIMEOUT_S = 5

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


def run_dashboard_node(source, *arguments, module=False, bounded_steps=1,
                       step_timeout=_DASHBOARD_STEP_TIMEOUT_S):
    """Run one dashboard JavaScript harness with captured output."""
    node = shutil.which('node')
    if not node:
        raise AssertionError('node is required to execute dashboard harnesses')
    options = ['--input-type=module'] if module else []
    command = [
        node, *options, '--eval', _DASHBOARD_PRELUDE + source,
        *map(str, arguments),
    ]
    process = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    timeout = dashboard_child_timeout(bounded_steps, step_timeout)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as failure:
        process.kill()
        stdout, stderr = process.communicate()
        phases = re.findall(r'^\[phase\] (.+)$', stderr, re.MULTILINE)
        last_phase = phases[-1] if phases else 'none recorded'
        raise AssertionError(
            f'dashboard harness outer backstop timed out after {timeout}s; '
            f'last phase: {last_phase}; stdout: {stdout!r}; '
            f'stderr: {stderr!r}'
        ) from failure
    if process.returncode != 0:
        raise AssertionError((process.returncode, stdout, stderr))
    return subprocess.CompletedProcess(
        command, process.returncode, stdout, stderr)
