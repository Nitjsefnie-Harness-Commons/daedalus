#!/usr/bin/env python3
"""Check the syntax of every JavaScript example shipped by the repository."""
import json
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


def test_examples_keep_values_through_the_shipped_evaluator(tmp):
    """A body wrapper must not discard the examples' completion values."""
    del tmp
    node = shutil.which('node')
    assert node, 'node is required to execute the shipped evaluator'
    cases = [
        ('kill-hls.js', '', 'no hls instance found'),
        ('scrape-discord-messages.js', 'extract-only', []),
        ('scrape-discord-messages.js', 'scroll-back',
         {'error': 'no chat scroller found'}),
        ('scrape-discord-messages.js', 'unknown',
         {'error': 'unknown action unknown'}),
        ('open-tabs.js', '', 'opened 1 tabs'),
        ('react-fill-input.js', '', 'NO_INPUT'),
    ]
    program = r"""
const fs = require('fs');
const vm = require('vm');
const evaluator = require('path').resolve('extension/worker/evaluate.js');
(async () => {
  const values = [];
  for (const [file, action] of JSON.parse(fs.readFileSync(0, 'utf8'))) {
    const context = {
      performance, window: { __SCRAPE_ARGS: { action } },
      document: { querySelector: () => null, querySelectorAll: () => [] },
      GM: { openInTab() {} },
    };
    vm.runInNewContext(fs.readFileSync(evaluator, 'utf8'), context,
      { filename: evaluator });
    const result = await context._executeMainWorldEval(
      fs.readFileSync('examples/' + file, 'utf8'));
    if (result.e) throw new Error(file + ': ' + result.e);
    values.push(result.r === undefined ? '<undefined>' : result.r);
  }
  process.stdout.write(JSON.stringify(values));
})();
"""
    checked = run_node_argv(
        node, ['-e', program], ROOT,
        stdin_data=json.dumps([row[:2] for row in cases]))
    assert checked.returncode == 0, checked.stderr
    actual = json.loads(checked.stdout)
    assert actual == [row[2] for row in cases], actual


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
