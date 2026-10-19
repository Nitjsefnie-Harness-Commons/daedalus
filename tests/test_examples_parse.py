#!/usr/bin/env python3
"""Check the syntax of every JavaScript example shipped by the repository."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _noderun import run_node_argv  # noqa: E402

ROOT = _util.ROOT


def _tracked_examples():
    """Return the tracked JavaScript examples from Git, not a fixed list."""
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', 'examples/*.js'],
        capture_output=True, check=True, text=True)
    return [line for line in listed.stdout.splitlines() if line]


def test_tracked_examples_have_expected_node_syntax(tmp):
    """Module parsing rejects top-level returns the eval wrapper accepted."""
    del tmp
    node = shutil.which('node')
    if not node:
        _util.skip('node is required to syntax-check JavaScript examples')
    examples = _tracked_examples()
    assert examples, 'git ls-files found no tracked JavaScript examples'
    for filename in examples:
        checked = run_node_argv(
            node, ['--input-type=module', '--check'], ROOT,
            stdin_data=(ROOT / filename).read_text(encoding='utf-8'))
        assert checked.returncode == 0, (
            f'{filename}: module parse failed:\n{checked.stderr}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
