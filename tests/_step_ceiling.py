"""A step-count bound for a loop whose guard is there to stop it.

machinery_route's `seen` guard is the only thing that stops a base that
feeds itself, and that loop is UNCAPPED — so deleting the guard does not
make it answer wrong, it makes it not stop. A wall clock is the wrong
bound for that: a clock is the flaky class, and a pin that spends the
suite's time budget to learn what it already knows is the defect this
bound exists to remove. So the bound is a COUNT of line events.

The count covers the WHOLE analysis, not one function's frame, and that
is the load-bearing choice. A per-frame count is bound to a name: extract
the loop into a helper, delete the guard, and the counter watches an
empty frame while the helper spins — the exact harm this module exists
to remove, reached by a two-line refactor. Counting everything bounds
any non-terminating loop anywhere in the analyser and has no name in it
to go stale.

The count cannot be taken in-process. sys.settrace set inside a frame is
not what the frame that set it leaves behind, and restoring it there
does not undo that: under this repository's own runner the in-process
version left sys.gettrace() as NoneType where it had been CTracer —
coverage off for the rest of that process, silently, on the PASSING
path. So the count is taken in a CHILD PROCESS. The ceiling travels
with the call, so the child is itself bounded by count; the process
boundary bounds nothing and there is no timeout anywhere. The child is
disposable, so it installs the tracer and does not restore it — the
isolation is the process, not a restore.
"""
import ast
import subprocess
import sys
from pathlib import Path

from _launch_audit import bound_sites

# A healthy analysis of the pinned source is 4,738 line events, 14 of
# them inside machinery_route, and that figure is DETERMINISTIC: the
# count is steps, not a duration, so a loaded machine or a slow runner
# cannot change it and a correct tree cannot fail here. This ceiling is
# a SAMPLE margin over that measurement, not a derived bound — the loop
# it exists for has one, ~4 line events per distinct name in the source,
# but the whole analysis has no derivation and grows with every line
# added to the analyser. When the analyser grows, re-measure: if the
# healthy count approaches this, raise the ceiling with the measurement
# beside it, never without.
ANALYSIS_STEP_CEILING = 100_000

TESTS = str(Path(__file__).resolve().parent)

# The child imports the counter from here, so the two cannot drift. It
# reports the child's own pid, which is what lets a caller pin that the
# count really was taken over here and not in-process.
CHILD = """
import os
import sys

# insert(0, ...), not append: the child inherits PYTHONPATH, and putting
# the real tests directory first is what stops a decoy _launch_audit.py
# on that path from being imported instead of this repository's.
sys.path.insert(0, sys.argv[1])
from _step_ceiling import _counted_sites

print(repr((_counted_sites(sys.argv[2], sys.argv[3], int(sys.argv[4])),
            os.getpid())))
"""


def _counted_sites(source, label, ceiling=ANALYSIS_STEP_CEILING):
    """`bound_sites` for one source, raising if the whole analysis runs
    past `ceiling` line events.

    Private on purpose: importable in-process, this is one docstring away
    from the defect the module exists to prevent. Call this in a process
    with nothing else to do afterwards — it installs the tracer for the
    whole interpreter and does not put it back, for the reason in the
    module docstring.
    """
    steps = 0

    def local(frame, event, arg):
        del arg
        nonlocal steps
        if event == 'line':
            steps += 1
            if steps > ceiling:
                raise AssertionError(
                    f'the analysis ran past {ceiling} line events, last in '
                    f'{frame.f_code.co_name!r}: something in it does not '
                    'stop and no guard here is counting steps')
        return local

    sys.settrace(local)
    return bound_sites(source, label)


def within_step_ceiling(source, label, ceiling=ANALYSIS_STEP_CEILING):
    """`bound_sites` for one source, counted in a child process, as
    `(sites, child_pid)`.

    The pid is returned so a caller can PIN the boundary rather than
    assume it: it is the caller's own if this is ever readmitted
    in-process, and `sys.gettrace() is tracer` cannot tell, because an
    in-process version that restores cleanly passes that too.
    """
    try:
        run = subprocess.run(
            [sys.executable, '-c', CHILD, TESTS, source, label,
             str(ceiling)],
            capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as refusal:
        raise AssertionError(
            f'the step-counted child exited {refusal.returncode} without '
            f'answering; its own output is below\n{refusal.stderr.strip()}'
        ) from refusal
    return ast.literal_eval(run.stdout)
