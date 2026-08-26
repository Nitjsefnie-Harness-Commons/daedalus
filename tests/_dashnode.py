"""The dashboard suites' Node harness process boundary.

Not a suite itself — run_tests.py only loads `test_*.py`.

The dashboard behaviour suite runs shipped JavaScript modules in short Node
processes. This helper keeps process setup and captured failures consistent.
"""
import shutil
import subprocess

from _repo import ROOT


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


def run_dashboard_node(source, *arguments, module=False, timeout=30):
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
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    if process.returncode != 0:
        raise AssertionError((process.returncode, stdout, stderr))
    return subprocess.CompletedProcess(
        command, process.returncode, stdout, stderr)
