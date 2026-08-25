#!/usr/bin/env python3
"""Check the syntax of every JavaScript example shipped by the repository."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT


# This file must remain a CommonJS script for the browser wrapper, so Node's
# syntax checker rejects its top-level await.
EXPECTED_FAILURES = {
    'examples/scrape-discord-messages.js': (
        'await is only valid in async functions and the top level bodies '
        'of modules'),
}


def _tracked_examples():
    """Return the tracked JavaScript examples from Git, not a fixed list."""
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', 'examples/*.js'],
        capture_output=True, check=True, text=True, timeout=30)
    return [line for line in listed.stdout.splitlines() if line]


# Compiling with the AsyncFunction constructor checks the source as an
# independent function body. Textual wrapping was unsound: the source's own
# delimiters could balance against the wrapper's and hide a syntax error.
_ASYNC_BODY_HELPER = """\
const fs = require('fs');
const source = fs.readFileSync(process.argv[2], 'utf8');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
try {
  new AsyncFunction(source);
} catch (error) {
  process.stderr.write(String(error) + '\\n');
  process.exitCode = 1;
}
"""


def _async_body_result(node, target, tmp):
    """Compile the file at `target` as an async function body, never run it."""
    # The .cjs extension is load-bearing: a .js file's module system follows
    # the nearest ancestor package.json, and {"type":"module"} there would
    # make require() throw before any example is compiled.
    helper = Path(tmp) / 'parse_async_body.cjs'
    helper.write_text(_ASYNC_BODY_HELPER, encoding='utf-8')
    return subprocess.run(
        [node, str(helper), str(target)], cwd=ROOT, capture_output=True,
        text=True, timeout=30)


def _assert_parses_as_async_body(node, filename, tmp):
    """Fail unless a classic example parses as an independent async body."""
    source = (ROOT / filename).read_text(encoding='utf-8')
    if source.startswith('#!'):
        raise AssertionError(
            f'{filename}: cannot check an example with a shebang as a body')

    # The expected-failure example is a classic script body, so its top-level
    # await and return become valid inside an async function. A shebang is
    # rejected above; static module syntax is rejected by the compile itself.
    checked = _async_body_result(node, ROOT / filename, tmp)
    assert checked.returncode == 0, (
        f'{filename}: async-body parse failed:\n{checked.stderr}')


def test_tracked_examples_have_expected_node_syntax(tmp):
    """Every example parses unless an explicit exception says it cannot."""
    node = shutil.which('node')
    if not node:
        _util.skip('node is required to syntax-check JavaScript examples')

    examples = _tracked_examples()
    assert examples, 'git ls-files found no tracked JavaScript examples'
    stale = sorted(set(EXPECTED_FAILURES) - set(examples))
    assert not stale, (
        'expected-failure mapping names files that are not tracked: '
        f'{stale}')

    for filename in examples:
        checked = subprocess.run(
            [node, '--check', filename], cwd=ROOT, capture_output=True,
            text=True, timeout=30)
        reason = EXPECTED_FAILURES.get(filename)
        if reason is not None:
            assert checked.returncode != 0, (
                f'{filename}: expected node --check to fail for {reason!r}')
            assert reason in checked.stderr, (
                f'{filename}: node --check failed for an unexpected reason:\n'
                f'{checked.stderr}')
            _assert_parses_as_async_body(node, filename, tmp)
        else:
            assert checked.returncode == 0, (
                f'{filename}: node --check failed:\n{checked.stderr}')


def test_async_body_parse_is_the_module_syntax_oracle(tmp):
    """The body parse rejects export{}; but accepts import('x')."""
    node = shutil.which('node')
    if not node:
        _util.skip('node is required to syntax-check JavaScript examples')
    module = Path(tmp) / 'module.js'
    module.write_text('export{};\n', encoding='utf-8')
    # A static export is not valid in a function body; the compile rejects it.
    assert _async_body_result(node, module, tmp).returncode != 0
    dynamic = Path(tmp) / 'dynamic.js'
    dynamic.write_text("const loaded = import('x');\nreturn loaded;\n",
                       encoding='utf-8')
    # Dynamic import is legal in an async body; the deleted regex rejected it.
    assert _async_body_result(node, dynamic, tmp).returncode == 0


def test_async_body_check_catches_delimiter_crossing(tmp):
    """The body parse rejects source whose braces would balance a wrapper."""
    node = shutil.which('node')
    if not node:
        _util.skip('node is required to syntax-check JavaScript examples')
    body = 'const value = await Promise.resolve(1);\nreturn value;\n'
    control = Path(tmp) / 'control.js'
    control.write_text(body, encoding='utf-8')
    assert _async_body_result(node, control, tmp).returncode == 0
    attack = body + '}\nasync function maskedSyntaxError() {\n'

    # The attack has to be one the old textual wrapper ACCEPTED, or this test
    # would pass on any invalid source and prove nothing about delimiters.
    # Removing the crossing `}` leaves the wrapper's own function unclosed,
    # so this assertion fails and the test stops being a tautology.
    wrapped = Path(tmp) / 'wrapped.js'
    wrapped.write_text(
        'async function __example__() {\n' + attack + '\n}\n',
        encoding='utf-8')
    checked = subprocess.run(
        [node, '--check', str(wrapped)], cwd=ROOT, capture_output=True,
        text=True, timeout=30)
    assert checked.returncode == 0, checked.stderr

    mutated = Path(tmp) / 'mutated.js'
    mutated.write_text(attack, encoding='utf-8')
    # Textual wrapping let these delimiters consume the wrapper's own braces
    # and pass; an independently compiled body must reject them.
    assert _async_body_result(node, mutated, tmp).returncode != 0


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
