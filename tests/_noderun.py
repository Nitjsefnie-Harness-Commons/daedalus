"""Run a Node program from a closed, automatically cleaned file.

This launcher carries no scenario state and no configuration of its own, so
it lives in a neutral module both the boundary harness and the shared fetch
gate import — which is what stops `tests/_boundary_env.py` (which splices the
gate) and `tests/_stream_fake.py` (the gate belongs to it) from importing
each other, a cycle pylint reads as R0401 and CI treats as fatal.
"""
import contextlib
import json
import shutil
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
# The number is not written at the call site, and every figure it is
# composed of is itself composed rather than retyped: the samples are a
# named table and the multiple is named beside the deadline it scales.
# The arithmetic is re-derivable by reading these five lines; the
# measurement they record is in the report.
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


def _remove_tree(directory):
    """Delete a scratch tree, letting the caller record a failure."""
    shutil.rmtree(directory)


class _Scratch:
    """A scratch directory whose removal is best-effort AND REPORTED.

    `tempfile.TemporaryDirectory` deletes its tree in `__exit__`, and a file
    another process still holds is not deletable on Windows — so a plain
    context manager lets an `OSError` out of the `rmtree` replace whatever
    the caller raised. Here that is `ChildDeadlineExceeded`: the reader would
    get a bare errno naming no child, no deadline, no output and no cleanup
    report, which is the whole thing this class exists to prevent. The read
    door had the same shape and was closed by reordering; this is the other
    door, closed by not letting the removal raise at all.

    `tempfile.mkdtemp` rather than `TemporaryDirectory` so there is no
    failing `__exit__` left to be reached: the removal happens here, its
    outcome is appended to `outcomes`, and the caller's exception keeps
    travelling.
    """

    def __init__(self, prefix, outcomes):
        self._path = Path(tempfile.mkdtemp(prefix=prefix))
        self._name = self._path
        self._outcomes = outcomes

    def __enter__(self):
        return self._name

    def close(self):
        """Remove the tree, recording a failure rather than raising it.

        Idempotent, so the expiry handler can call it to have the outcome in
        the report it is about to build, and `__exit__` can call it again
        afterwards without a second removal.
        """
        if self._path is None:
            return
        try:
            _remove_tree(self._path)
        except OSError as failure:
            self._outcomes.append(
                f'; the scratch directory {self._path.name} was not fully '
                f'removed ({type(failure).__name__}: {failure}), which on '
                f'Windows is a surviving child still holding a file open')
        self._path = None

    def __exit__(self, *_exc):
        self.close()
        return False


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

    def __init__(self, argv, deadline_s, stdout, stderr, cleanup,
                 unlinked=''):
        self.argv = argv
        self.deadline_s = deadline_s
        self.stdout = stdout
        self.stderr = stderr
        self.cleanup_diagnostic = cleanup
        self.unlinked = unlinked
        super().__init__(
            f'a Node child did not finish within {deadline_s}s and was '
            f'killed; the suite ceiling is a weaker backstop, but it now '
            f'sends the request to the suite\'s whole process group and '
            f'escalates on that group, so this child does not survive '
            f'it.\n'
            f'  child: {_child_label(argv)}\n'
            f'  deadline: {deadline_s}s\n'
            f'  cleanup: {cleanup}{unlinked}\n'
            f'  stdout: {stdout[:2000]!r}\n'
            f'  stderr: {stderr[:2000]!r}')


# A bound on one element of the report's child line — a STRING this module
# prints, not on anything. A source handed to `node -e` is arbitrarily long,
# and that line is the one a reader reads first.
_LABEL_WIDTH = 60


def _child_label(argv):
    """The child as the expiry report names it, from either argv shape.

    A program launch hands `node <a written file> <arguments>`, and the
    basename of that second element is the useful half: it keeps a
    temporary path nobody can act on out of the report. An exact-argv
    launch hands `node -e <source>` or `node --check <path>`, where that
    element is a flag and the element after it is a SOURCE — so the
    basename is taken at the program file and nowhere else. Applied
    anywhere else it is the source sliced at its last `/`, naming a
    fragment that exists nowhere, and a `require('/a/b/c.js')` source
    loses its own name to `c.js'); …`.

    Every element is bounded as well: a source pasted whole makes the one
    line a reader scans first unreadable.
    """
    parts = []
    for index, part in enumerate(argv):
        text = str(part)
        if index == 1 and not text.startswith('-'):
            text = Path(text).name or text
        parts.append(text[:_LABEL_WIDTH])
    return ' '.join(parts)


def run_node_program(node, program, arguments, cwd, payload=None):
    """Run a Node program from a closed, automatically cleaned file.

    `cwd` is positional so a caller that forwards its own `cwd` (the shared
    gate's `run_gate`) neither names a `cwd=` keyword the coverage guard would
    read as an undeclared launch nor has to restate the environment.

    The prologue written ahead of the program is load-bearing rather than
    decoration: it splices the written file out of `process.argv`, so the
    child sees its own arguments in the positions the caller wrote them,
    and it pushes `payload` as an object literal LAST, which is where the
    harnesses read their plan from.

    The bound, the launch shape, the scratch trees and the classified
    expiry all belong to `_launch_child`, which is shared with
    `run_node_argv` — a second copy of a hang detector is a second copy a
    fix has to reach.
    """
    unlinked = []
    directory_scratch = _Scratch('daedalus-node-', unlinked)
    with directory_scratch as directory:
        program_path = Path(directory) / 'program.js'
        prologue = 'process.argv.splice(1, 1);'
        if payload is not None:
            prologue += f' process.argv.push({json.dumps(payload)});'
        prologue += '\n'
        program_path.write_text(
            prologue + program, encoding='utf-8')
        return _launch_child(
            [node, str(program_path), *arguments], cwd, unlinked,
            (directory_scratch,))


def run_node_argv(node, arguments, cwd, stdin_data=None, environment=None):
    """Run a Node child from an EXACT argv, with no program file of ours.

    `arguments` is the argv tail as a list of strings, so a caller can
    launch `['--check', path]`, `['-e', source, *args]` or `[script_path]`
    — none of which is `node <written-file> <args>`, and every one of which
    the program launch's prologue would rewrite.

    `cwd` is positional for the reason it is there above, and `stdin_data`
    is a keyword because it is the one input a caller supplies by name.
    `stdin_data=None` means stdin is DEVNULL exactly as it is for the
    program launch, so a child that reads stdin to end of file reaches it
    at once rather than waiting on a pipe nobody writes.

    `environment` is the environment to scrub, for the caller whose child
    needs a value this process does not carry: `test_js_coverage.py` points
    `NODE_V8_COVERAGE` at a dumps directory of its own per test, and a
    launcher that read `os.environ` would send the child to the wrong one.
    It is the second argument `child_coverage` already took, passed through
    rather than re-implemented, so `None` keeps the launch it always was.
    """
    return _launch_child(
        [node, *arguments], cwd, [], stdin_data=stdin_data,
        environment=environment)


def _launch_child(argv, cwd, unlinked, before_report=(), stdin_data=None,
                  environment=None):
    """Launch `argv`, bound by the detector, and read back what it produced.

    The one launch path, for both entry points. Two copies would mean a fix
    to the detector reached one of them.

    The child is bounded by `CHILD_DEADLINE_S` — a hang detector whose basis
    is recorded above, not a health margin — and the bound spans the child's
    own execution and nothing else: the clock starts at the launch, and
    nothing before it (writing a program file) or after it (reading the
    result) is inside it. A future serialisation gate placed before the
    launch must stay outside it, or its queueing time would count against
    the child.

    The launch is the `Popen` shape rather than `subprocess.run` so that
    expiry can be a named, classified failure carrying the child's partial
    output AND a bounded, verified cleanup: `subprocess.run` kills the child
    it launched and nothing else, so a child that started a grandchild
    leaves it running. The suite ceiling does not: since the per-suite
    bound it sends the request to the suite's whole process group, waits a
    bounded grace, and escalates on that group whatever happened in
    between, so this child is inside it and does not survive. On Windows
    the tree is killed through `taskkill /T` instead, and `taskkill /F` is
    a forced termination with no request and no grace -- so a child there
    is killed outright rather than asked, which is the platform
    difference, not a weaker ceiling. `start_new_session` is applied only
    where a process group exists to be killed, which is why the cleanup
    lives in `tests/_processtree.py` rather than here.

    Both scratch directories are `_Scratch`, not `TemporaryDirectory`, so a
    removal that fails is recorded in the report instead of replacing it —
    see `_Scratch` for the Windows shape that makes that matter.

    The child runs with `child_coverage('scrub')` evaluated **here, at
    launch**, not snapshotted at import, and over the caller's `environment`
    when it supplied one. A module-level snapshot cannot be correct for a
    value chosen per call: `test_js_coverage.py` builds an environment per
    test pointing `NODE_V8_COVERAGE` at its own dumps directory, and an
    import-time snapshot — or a launch that read `os.environ` where the
    caller passed a dict — would send the child to the wrong directory.

    `before_report` is the caller's own scratch trees, and `unlinked` the
    list their removal failures are appended to: both are closed after the
    cleanup and BEFORE the report is built, so a removal that fails is IN
    the report rather than replacing it.
    """
    output_scratch = _Scratch('daedalus-node-out-', unlinked)
    with output_scratch as output:
        stdout_path = Path(output) / 'stdout'
        stderr_path = Path(output) / 'stderr'
        # stdout and stderr go to files rather than to pipes: a pipe buffer
        # the child fills and nobody drains would block the child itself and
        # make this detector fire on a child that was only talking. stdin is
        # a file for the same reason, and because a pipe fed after the
        # launch blocks the launcher on a child that stopped reading.
        stdin_path = None
        if stdin_data is not None:
            stdin_path = Path(output) / 'stdin'
            stdin_path.write_text(stdin_data, encoding='utf-8')
        with contextlib.ExitStack() as opened:
            stdout = opened.enter_context(stdout_path.open('wb'))
            stderr = opened.enter_context(stderr_path.open('wb'))
            process = subprocess.Popen(
                argv, cwd=cwd,
                env=_util.child_coverage('scrub', environment),
                stdin=(subprocess.DEVNULL if stdin_path is None
                       else opened.enter_context(stdin_path.open('rb'))),
                stdout=stdout, stderr=stderr,
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
                stdout = _read_stream(stdout_path)
                stderr = _read_stream(stderr_path)
                cleanup = cleanup_process_tree(
                    process, CLEANUP_DEADLINE_S)
                for scratch in (output_scratch, *before_report):
                    scratch.close()
                raise ChildDeadlineExceeded(
                    argv, CHILD_DEADLINE_S, stdout, stderr, cleanup,
                    ''.join(unlinked)) from None
        return subprocess.CompletedProcess(
            argv, returncode, _read_stream(stdout_path),
            _read_stream(stderr_path))


def _read_stream(path):
    """Read a child's stream, replacing bytes that are not valid UTF-8.

    `errors='replace'` turns them into U+FFFD rather than raising, because
    this is read on the failure path too and a decode error there would
    replace the classified error with an unrelated one. The old
    `subprocess.run(..., encoding='utf-8')` raised on them, so this IS a
    behaviour change: undecodable output is now reported lossy rather than
    fatal.
    """
    return path.read_text(encoding='utf-8', errors='replace')
