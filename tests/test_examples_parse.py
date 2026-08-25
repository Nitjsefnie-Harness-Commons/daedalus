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
    """Return the tracked JavaScript examples, without a hand-maintained list."""
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', 'examples/*.js'],
        capture_output=True, check=True, text=True, timeout=30)
    return [line for line in listed.stdout.splitlines() if line]


def _write_async_wrapper(filename, tmp):
    """Wrap a classic example body in an async function for syntax checking."""
    source = (ROOT / filename).read_text(encoding='utf-8')
    lines = source.splitlines()
    if lines and lines[0].startswith('#!'):
        raise AssertionError(
            f'{filename}: cannot wrap an example with a shebang')
    if any(line.lstrip().startswith(('import ', 'export ')) for line in lines):
        raise AssertionError(
            f'{filename}: cannot wrap an example with module syntax')

    # The expected-failure example is a classic script body, so its top-level
    # await and return become valid inside this async function. A shebang or
    # import/export is rejected above instead of silently skipping the check.
    wrapper = Path(tmp) / f'{Path(filename).name}.wrapped.js'
    wrapper.write_text(
        'async function __example__() {\n' + source + '\n}\n',
        encoding='utf-8')
    return wrapper


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
            wrapper = _write_async_wrapper(filename, tmp)
            wrapped = subprocess.run(
                [node, '--check', str(wrapper)], cwd=ROOT, capture_output=True,
                text=True, timeout=30)
            assert wrapped.returncode == 0, (
                f'{filename}: wrapped node --check failed:\n'
                f'{wrapped.stderr}')
        else:
            assert checked.returncode == 0, (
                f'{filename}: node --check failed:\n{checked.stderr}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
