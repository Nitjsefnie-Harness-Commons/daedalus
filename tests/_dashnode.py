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
those shapes. A possibly desynchronised blanking must fail loudly. Plain
template strings and ordinary comments remain supported. Other string content
is preserved and must not match the whitespace-tolerant bound pattern.
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
_REGEX_PREFIX_WORDS = frozenset({
    'await', 'case', 'delete', 'do', 'else', 'in', 'instanceof', 'new',
    'of', 'return', 'throw', 'typeof', 'void', 'yield',
})
_CONTROL_PAREN_WORDS = frozenset({
    'catch', 'for', 'if', 'switch', 'while', 'with',
})

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


def _unsupported_bound_shape(source):
    """Return the first JavaScript shape comment blanking cannot model."""
    index, end = 0, len(source)
    expect_expression = True
    last_word = None
    control_parens = []
    while index < end:
        char = source[index]
        pair = source[index:index + 2]
        if char.isspace():
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            while index < end and source[index] != quote:
                index += 2 if source[index] == '\\' else 1
            index += index < end
            expect_expression = False
            last_word = None
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
            expect_expression = False
            last_word = None
            continue
        if pair == '//':
            newline = source.find('\n', index + 2)
            index = end if newline < 0 else newline
            continue
        if pair == '/*':
            close = source.find('*/', index + 2)
            index = end if close < 0 else close + 2
            continue
        if char == '/':
            if expect_expression:
                return 'regex literal'
            index += 2 if pair == '/=' else 1
            expect_expression = True
            last_word = None
            continue
        if char.isalpha() or char in '_$':
            stop = index + 1
            while (stop < end
                   and (source[stop].isalnum() or source[stop] in '_$')):
                stop += 1
            last_word = source[index:stop]
            expect_expression = last_word in _REGEX_PREFIX_WORDS
            index = stop
            continue
        if char.isdigit():
            index += 1
            while (index < end
                   and (source[index].isalnum() or source[index] in '._')):
                index += 1
            expect_expression = False
            last_word = None
            continue
        if char == '(':
            control_parens.append(last_word in _CONTROL_PAREN_WORDS)
            expect_expression = True
        elif char == ')':
            expect_expression = (
                control_parens.pop() if control_parens else False)
        elif char in ']}':
            expect_expression = False
        elif char == '.':
            expect_expression = False
        elif char in '+-' and pair == char * 2:
            index += 1
        else:
            expect_expression = True
        last_word = None
        index += 1
    return None


@dataclass(frozen=True)
class DashboardNodeHarness:
    """One Node harness source and its validated process-bound metadata."""

    source: str
    bounded_steps: int
    module: bool = False

    def __post_init__(self):
        unsupported = _unsupported_bound_shape(self.source)
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
