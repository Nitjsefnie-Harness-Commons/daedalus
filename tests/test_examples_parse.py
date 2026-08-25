#!/usr/bin/env python3
"""Check the syntax of every JavaScript example shipped by the repository."""
import re
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
    """Return the tracked JavaScript examples, without a hand-maintained list."""
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', 'examples/*.js'],
        capture_output=True, check=True, text=True, timeout=30)
    return [line for line in listed.stdout.splitlines() if line]


# A literal 'import '/'export ' prefix misses spellings like `export{};`,
# so the guard matches the keyword as a complete token instead: a word end
# that `$` and `_` cannot fake, since \b alone treats them inconsistently.
_MODULE_SYNTAX = re.compile(r'^\s*(?:import|export)\b(?![$\w])', re.MULTILINE)

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


def _has_module_syntax(source):
    """Whether a source declares module imports or exports, by token shape."""
    return _MODULE_SYNTAX.search(source) is not None


def _async_body_result(node, target, tmp):
    """Compile the file at `target` as an async function body, never run it."""
    helper = Path(tmp) / 'parse_async_body.js'
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
    if _has_module_syntax(source):
        raise AssertionError(
            f'{filename}: cannot check an example with module syntax as a '
            'body')

    # The expected-failure example is a classic script body, so its top-level
    # await and return become valid inside an async function. A shebang or
    # import/export is rejected above instead of silently skipping the check.
    checked = _async_body_result(node, ROOT / filename, tmp)
    assert checked.returncode == 0, (
        f'{filename}: async-body parse failed:\n{checked.stderr}')


def test_tracked_examples_have_expected_node_syntax(tmp):
    """Every example parses unless its explicit exception says why it cannot."""
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


def test_module_syntax_guard_matches_token_shape(tmp):
    """The guard fires on any import/export token, not one literal spelling."""
    for source in ('export{};', 'export {a};', 'export default x;',
                   "import{x}from'y';", "import 'y';",
                   "import*as ns from'y';"):
        assert _has_module_syntax(source), source
    for source in ('importantThing = 1;', 'exports.foo = 1;',
                   'exported = 2;', 'importer();'):
        assert not _has_module_syntax(source), source


def test_async_body_check_catches_delimiter_crossing(tmp):
    """The body parse rejects source whose braces would balance a wrapper."""
    node = shutil.which('node')
    if not node:
        _util.skip('node is required to syntax-check JavaScript examples')
    source = (ROOT / 'examples/scrape-discord-messages.js').read_text(
        encoding='utf-8')
    unmodified = Path(tmp) / 'unmodified.js'
    unmodified.write_text(source, encoding='utf-8')
    assert _async_body_result(node, unmodified, tmp).returncode == 0
    mutated = Path(tmp) / 'mutated.js'
    mutated.write_text(
        source + '\n}\nasync function maskedSyntaxError() {\n',
        encoding='utf-8')
    # Textual wrapping let these delimiters consume the wrapper's own braces
    # and pass; an independently compiled body must reject them.
    assert _async_body_result(node, mutated, tmp).returncode != 0


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
