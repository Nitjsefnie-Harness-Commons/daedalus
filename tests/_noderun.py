"""Run a Node program from a closed, automatically cleaned file.

Not a suite itself — run_tests.py only loads `test_*.py`.

This launcher carries no scenario state and no configuration of its own, so
it lives in a neutral module both the boundary harness and the shared fetch
gate import. Keeping it here is what stops `tests/_boundary_env.py` (which
splices the gate) and `tests/_stream_fake.py` (which the gate belongs to) from
importing each other — a cycle pylint reads as R0401 and CI treats as fatal.
"""
import json
import subprocess
import tempfile
from pathlib import Path

import _util


def run_node_program(node, program, arguments, *, cwd,
                     env=_util.child_coverage('scrub'), payload=None,
                     timeout=30):
    """Run a Node program from a closed, automatically cleaned file.

    `env` is where a caller's coverage declaration lands; the terminal
    launch below owns the environment the child actually runs with. The
    coverage guard reads `env=` as a static declaration at every level,
    so no threaded parameter can carry it — the value that reaches the
    child is the `child_coverage('scrub')` call at the subprocess below.
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
