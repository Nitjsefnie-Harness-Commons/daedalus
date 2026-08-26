"""The dashboard suites' Node harness process boundary.

Not a suite itself — run_tests.py only loads `test_*.py`.

The dashboard behaviour suite runs shipped JavaScript modules in short Node
processes. This helper keeps process setup and captured failures consistent.

The JavaScript `bounded` helper races settlement but cannot cancel the losing
work or any handles that work owns. A caller that recovers from its timeout
must cancel those handles itself. The shipped asynchronous harnesses instead
pass timeout failures to `leave`, which flushes the error and exits the child.

Bound-count validation refuses template expressions and regex literals before
blanking comments because `blank_js_comments` intentionally does not model
those shapes. A shallow scan keeps ordinary comments and plain template
strings on the fast path; sources with an ambiguous slash ask Node's existing
parser whether it is a regex. A possibly desynchronised blanking must fail
loudly. Other string content is preserved and must not match the
whitespace-tolerant bound pattern.
"""
import re
import shutil
import subprocess
from dataclasses import dataclass

from _jsread import blank_js_comments
from _repo import ROOT


_DASHBOARD_STEP_TIMEOUT_S = 5
_DASHBOARD_DRAIN_TIMEOUT_S = 0.2
_BOUNDED_AWAIT = re.compile(r'\bawait\s+bounded\s*\(')

_REGEX_TOKEN_PROBE = r"""
const Module = module.constructor;
const natives = process.binding('natives');
const acornSource = natives['internal/deps/acorn/acorn/dist/acorn'];
if (!acornSource) throw new Error('Node did not expose its Acorn parser');
const acornModule = new Module('dashnode-acorn');
acornModule._compile(acornSource, 'dashnode-acorn.js');
const acorn = acornModule.exports;
let source = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { source += chunk; });
process.stdin.on('end', () => {
  const tree = acorn.parse(source, {
    ecmaVersion: 'latest', sourceType: process.argv[1]
  });
  function containsRegex(value) {
    if (!value || typeof value !== 'object') return false;
    if (value.regex) return true;
    if (Array.isArray(value)) return value.some(containsRegex);
    return Object.values(value).some(containsRegex);
  }
  const hasRegex = containsRegex(tree);
  process.stdout.write(hasRegex ? 'regex' : 'clear');
});
"""

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


def _source_has_regex(source, module):
    """Ask Node's existing parser whether source contains a regex."""
    node = shutil.which('node')
    if not node:
        raise ValueError(
            'node is required to inspect dashboard harness slash tokens')
    try:
        result = subprocess.run(
            [node, '--eval', _REGEX_TOKEN_PROBE,
             'module' if module else 'script'], input=source,
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=_DASHBOARD_STEP_TIMEOUT_S)
    except subprocess.TimeoutExpired as failure:
        raise ValueError(
            'dashboard harness slash inspection timed out after '
            f'{_DASHBOARD_STEP_TIMEOUT_S}s') from failure
    if result.returncode or result.stdout not in {'clear', 'regex'}:
        detail = result.stderr.strip() or repr(result.stdout)
        raise ValueError(
            f'dashboard harness slash inspection failed: {detail}')
    return result.stdout == 'regex'


def _unsupported_bound_shape(source, module):
    """Return the first source shape comment blanking cannot model."""
    shape = _ambiguous_bound_shape(source)
    if shape == 'slash token':
        return 'regex literal' if _source_has_regex(source, module) else None
    return shape


@dataclass(frozen=True)
class DashboardNodeHarness:
    """One Node harness source and its validated process-bound metadata."""

    source: str
    bounded_steps: int
    module: bool = False

    def __post_init__(self):
        unsupported = _unsupported_bound_shape(self.source, self.module)
        if unsupported:
            raise ValueError(
                'dashboard harness bound count cannot inspect '
                f'{unsupported}')
        source = blank_js_comments(self.source)
        actual = len(_BOUNDED_AWAIT.findall(source))
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
                # Preserve the recorded drain failure instead of replacing
                # it with another exception from this diagnostic helper.
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
