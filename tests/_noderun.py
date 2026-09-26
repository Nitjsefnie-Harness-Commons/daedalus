"""Run a Node program from a closed, automatically cleaned file.

This launcher carries no scenario state and no configuration of its own, so
it lives in a neutral module both the boundary harness and the shared fetch
gate import — which is what stops `tests/_boundary_env.py` (which splices the
gate) and `tests/_stream_fake.py` (the gate belongs to it) from importing
each other, a cycle pylint reads as R0401 and CI treats as fatal.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import _util
from _processtree import cleanup_process_tree

# --- the hang detector -----------------------------------------------------
#
# This is a HANG DETECTOR, not a health margin, and the difference is the
# whole point of issue #1117. The margin that was here (a flat 30 s) failed
# correct children on a loaded `windows-latest` runner: the real cost of
# these children is a fixed unit of work, so the only thing a tight bound
# measures is how busy the runner is. Nothing correct reaches this number.
#
#   SLOWEST_CORRECT_CHILD_SAMPLES  the freeze control's observed times, the
#                                    slowest correct child on this path
#   SLOWEST_CORRECT_CHILD_S  10.70   max of those samples
#   HANG_DETECTOR_MULTIPLE   10      a runner ten times slower still passes
#   CHILD_DEADLINE_S         107     round(10.70 * 10)
#   CLEANUP_DEADLINE_S       5       round(107 * 0.05)
#
# A1's recording requirement and the guard's own rule are the same
# requirement, in the part a rule can see: the number is not written at the
# call site, and every figure it is composed of is itself composed rather
# than retyped. `tests/_launch_census.py` refuses a bound whose constant
# chain bottoms out in a literal, which is why the samples are a named
# table and the multiple is named beside the deadline it scales. The
# arithmetic is re-derivable by reading these five lines; the measurement
# they record is in the report.
SLOWEST_CORRECT_CHILD_SAMPLES = (10.57, 10.58, 10.63, 10.70)
SLOWEST_CORRECT_CHILD_S = max(SLOWEST_CORRECT_CHILD_SAMPLES)
HANG_DETECTOR_MULTIPLE = 10
CHILD_DEADLINE_S = round(SLOWEST_CORRECT_CHILD_S * HANG_DETECTOR_MULTIPLE)
# The cleanup is its own bound, shorter and for a different reason: it
# bounds the kill of a process that has already stopped answering, not the
# child, and it is a fraction of the detector rather than a number typed
# beside it.
CLEANUP_FRACTION = 0.05
CLEANUP_DEADLINE_S = round(CHILD_DEADLINE_S * CLEANUP_FRACTION)


class ChildDeadlineExceeded(Exception):
    """A harness child did not finish inside the hang detector's budget.

    Named rather than raised as a bare `TimeoutExpired` because the reader
    of a failed suite needs to tell "the child was killed at the detector"
    from "the child was killed by the suite ceiling" — the second is a job
    timeout that names a suite and not a test, and the first is this.

    Carries what the child produced before it was killed and what the
    cleanup did, because a child that stopped answering is exactly the case
    where its partial output is the only evidence there is.
    """

    def __init__(self, argv, deadline_s, stdout, stderr, cleanup):
        self.argv = argv
        self.deadline_s = deadline_s
        self.stdout = stdout
        self.stderr = stderr
        self.cleanup_diagnostic = cleanup
        super().__init__(
            f'a Node child did not finish within {deadline_s}s and was '
            f'killed; the suite ceiling is a weaker backstop because it '
            f'sends SIGTERM to the suite and leaves this child running.\n'
            f'  child: {argv[0]} {Path(str(argv[1])).name}\n'
            f'  deadline: {deadline_s}s\n'
            f'  cleanup: {cleanup}\n'
            f'  stdout: {stdout[:2000]!r}\n'
            f'  stderr: {stderr[:2000]!r}')


def run_node_program(node, program, arguments, cwd, payload=None):
    """Run a Node program from a closed, automatically cleaned file.

    `cwd` is positional so a caller that forwards its own `cwd` (the shared
    gate's `run_gate`) neither names a `cwd=` keyword the coverage guard would
    read as an undeclared launch nor has to restate the environment.

    The child is bounded by `CHILD_DEADLINE_S` — a hang detector whose basis
    is recorded above, not a health margin — and the bound spans the child's
    own execution and nothing else: the clock starts at the launch, and
    nothing before it (writing the program file) or after it (reading the
    result) is inside it. A future serialisation gate placed before the
    launch must stay outside it, or its queueing time would count against
    the child.

    The launch is the `Popen` shape rather than `subprocess.run` so that
    expiry can be a named, classified failure carrying the child's partial
    output AND a bounded, verified cleanup: `subprocess.run` kills the child
    it launched and nothing else, so a child that started a grandchild
    leaves it running, and under the suite ceiling a SIGTERM to the suite
    leaves the child itself running (reparented, alive). `start_new_session`
    is applied only where a process group exists to be killed; on Windows
    the tree is killed through `taskkill /T` instead, which is why the
    cleanup lives in `tests/_processtree.py` rather than here.

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
        argv = [node, str(program_path), *arguments]
        # stdout and stderr go to files rather than to pipes: a pipe buffer
        # the child fills and nobody drains would block the child itself and
        # make this detector fire on a child that was only talking.
        with tempfile.TemporaryDirectory(
                prefix='daedalus-node-out-') as output:
            stdout_path = Path(output) / 'stdout'
            stderr_path = Path(output) / 'stderr'
            with (stdout_path.open('wb') as stdout,
                  stderr_path.open('wb') as stderr):
                process = subprocess.Popen(
                    argv, cwd=cwd,
                    env=_util.child_coverage('scrub'),
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    start_new_session=sys.platform != 'win32')
                try:
                    returncode = process.wait(timeout=CHILD_DEADLINE_S)
                except subprocess.TimeoutExpired:
                    # Flush, then read, then kill — in that order. Flush
                    # because the handles are buffered writers, so a read
                    # before the close sees an empty file; read BEFORE the
                    # kill because on Windows a file another process still
                    # holds open is not reliably readable, and a read after
                    # a failed tree kill can raise PermissionError and
                    # replace the classified error with an unrelated one.
                    stdout.flush()
                    stderr.flush()
                    stdout, stderr = _read(stdout_path), _read(stderr_path)
                    cleanup = cleanup_process_tree(
                        process, CLEANUP_DEADLINE_S)
                    raise ChildDeadlineExceeded(
                        argv, CHILD_DEADLINE_S, stdout, stderr,
                        cleanup) from None
            return subprocess.CompletedProcess(
                argv, returncode, _read(stdout_path), _read(stderr_path))


def _read(path):
    """Read a child's stream, replacing bytes that are not valid UTF-8.

    `errors='replace'` turns them into U+FFFD rather than raising, because
    this is read on the failure path too and a decode error there would
    replace the classified error with an unrelated one. The old
    `subprocess.run(..., encoding='utf-8')` raised on them, so this IS a
    behaviour change: undecodable output is now reported lossy rather than
    fatal.
    """
    return path.read_text(encoding='utf-8', errors='replace')
