"""Run a Node program from a closed, automatically cleaned file.

This launcher carries no scenario state and no configuration of its own, so
it lives in a neutral module both the boundary harness and the shared fetch
gate import — which is what stops `tests/_boundary_env.py` (which splices the
gate) and `tests/_stream_fake.py` (the gate belongs to it) from importing
each other, a cycle pylint reads as R0401 and CI treats as fatal.
"""
import json
import subprocess
import tempfile
from pathlib import Path

import _util


def run_node_program(node, program, arguments, cwd, payload=None, timeout=30):
    """Run a Node program from a closed, automatically cleaned file.

    `cwd` is positional so a caller that forwards its own `cwd` (the shared
    gate's `run_gate`) neither names a `cwd=` keyword the coverage guard would
    read as an undeclared launch nor has to restate the environment.

    The child runs with `child_coverage('scrub')` evaluated **here, at
    launch**, not snapshotted at import. A module-level snapshot cannot be
    correct for a value chosen per call: `test_js_coverage.py` sets
    `os.environ['NODE_V8_COVERAGE']` per test to point at its own dumps
    directory, and an import-time snapshot would send the child to the wrong
    directory.
    """
    with tempfile.TemporaryDirectory(prefix='daedalus-node-') as directory:
        program_path = Path(directory) / 'program.js'
        prologue = 'process.argv.splice(1, 1);'
        if payload is not None:
            prologue += f' process.argv.push({json.dumps(payload)});'
        prologue += '\n'
        program_path.write_text(
            prologue + program, encoding='utf-8')
        return subprocess.run(
            [node, str(program_path), *arguments], cwd=cwd,
            env=_util.child_coverage('scrub'), capture_output=True,
            text=True, encoding='utf-8', timeout=timeout)
