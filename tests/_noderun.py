"""Run a Node program from a closed, automatically cleaned file.

Not a suite itself — run_tests.py only loads `test_*.py`.

This launcher carries no scenario state and no configuration of its own, so
it lives in a neutral module both the boundary harness and the shared fetch
gate import. Keeping it here is what stops `tests/_boundary_env.py` (which
splices the gate) and `tests/_stream_fake.py` (the gate belongs to it) from
importing each other — a cycle pylint reads as R0401 and CI treats as fatal.
"""
import json
import subprocess
import tempfile
from pathlib import Path

import _util

# The one environment every Node child in this tree runs with: the parent's
# copy with the coverage collector's names stripped, so a child inheriting
# them cannot record paths that vanish with the temporary tree. It is bound
# once here and named at the launch below, which is the level that actually
# sets it — a threaded `env=` parameter would be a declaration the value
# never reaches, and the coverage guard reads `env=` as a static name.
_CHILD_ENV = _util.child_coverage('scrub')


def run_node_program(node, program, arguments, cwd, payload=None, timeout=30):
    """Run a Node program from a closed, automatically cleaned file.

    `cwd` is positional so a caller that forwards its own `cwd` (the shared
    gate's `run_gate`) neither names a `cwd=` keyword the coverage guard
    would read as an undeclared launch nor needs to restate the environment:
    both the guard's static check and the child's real environment are
    satisfied by the one `_CHILD_ENV` above.
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
            env=_CHILD_ENV, capture_output=True,
            text=True, encoding='utf-8', timeout=timeout)
