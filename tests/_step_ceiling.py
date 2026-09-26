"""A step-count bound for a loop whose guard is there to stop it.

machinery_route follows a callee's base through the bindings table to
decide whether a call is the import machinery. Its `seen` guard is the
only thing that stops a base that feeds itself, and the loop is UNCAPPED
— so deleting the guard does not make it answer wrong, it makes it not
stop. A wall clock is the wrong bound for that: a clock is the flaky
class, and a pin that spends the suite's whole time budget to learn what
it already knows is the defect this bound exists to remove.

So the bound is a COUNT of line events in that one function. What it
counts has to happen somewhere no other tracer is watching, and
sys.settrace does not qualify: a tracer set inside a frame is not what
the frame that set it leaves behind, and restoring it in that same
frame does not undo that. Measured under this repository's own runner,
`python3 -m coverage run --parallel-mode`, the in-process version left
sys.gettrace() as NoneType where it had been CTracer — coverage off for
the rest of that process, silently, on the passing path.

So the count is taken in a CHILD PROCESS. The ceiling travels with the
call, so the child is itself bounded by count and raises the same
legible message; the process boundary bounds nothing and there is no
timeout anywhere. The child is disposable, so it installs the tracer
and does not restore it: the isolation is the process, not a restore.
"""
import ast
import subprocess
import sys
from pathlib import Path

from _launch_audit import bound_sites

# Far above any real chain: a module spelling `a = b; b = a` reaches the
# loop twice and leaves on the second. A count of steps and not a
# duration, so a loaded machine cannot fail a correct tree.
CYCLE_STEP_CEILING = 10_000

# machinery_route is a closure inside launch_refusals, so the frame is
# named rather than imported. A stale name here would leave the tracer
# attached to nothing, so the count below refuses to believe it until it
# has seen this frame called — a pin that cannot fail is the defect
# class this module exists to remove.
TRACED = 'machinery_route'

# The same problem one step earlier, and it is why the count cannot be a
# post-hoc assertion: if the name is stale the guard's loop is somebody
# else's, and asserting after the call means asserting after a hang. So
# every line seen BEFORE the tracer attaches counts against this, and a
# tracer that never attaches is a legible failure rather than a spin.
UNATTACHED_CEILING = 100_000

TESTS = str(Path(__file__).resolve().parent)

# The child. It imports the analyser and this module's counter, so the
# counter has one definition and the parent and the child cannot drift.
CHILD = """
import sys

sys.path.insert(0, sys.argv[1])
from _launch_audit import bound_sites
from _step_ceiling import counted_sites

print(repr(counted_sites(sys.argv[2], sys.argv[3], int(sys.argv[4]))))
"""


def counted_sites(source, label, ceiling=CYCLE_STEP_CEILING):
    """`bound_sites` for one source, raising rather than letting
    machinery_route run past `ceiling` line events.

    Call this in a process that has nothing else to do afterwards. It
    installs the tracer for the whole interpreter and does not put it
    back; see the module docstring for why it cannot.
    """
    steps = 0
    unattached = 0
    attached = 0

    def local(frame, event, arg):
        del arg
        nonlocal steps, unattached, attached
        if event == 'call':
            if frame.f_code.co_name == TRACED:
                attached += 1
            return local
        if event != 'line':
            return local
        if frame.f_code.co_name == TRACED:
            steps += 1
            if steps > ceiling:
                raise AssertionError(
                    f'{TRACED} ran past {ceiling} steps; its seen guard is '
                    'not stopping the base chain')
        elif not attached:
            unattached += 1
            if unattached > UNATTACHED_CEILING:
                raise AssertionError(
                    f'no frame named {TRACED!r} was traced in '
                    f'{unattached} lines; the tracer is not attached and '
                    'this control would pass on anything')
        return local

    sys.settrace(local)
    sites = bound_sites(source, label)
    if not attached:
        # Reached only when the analysis was short, so the ceiling above
        # could not speak for it. A stale TRACED name leaves the tracer
        # attached to nothing, the count meaningless, and the test GREEN
        # — which is the same silent hole this bound was built to close,
        # one level up.
        raise AssertionError(
            f'no frame named {TRACED!r} was called, so the tracer never '
            'attached and the step count means nothing')
    return sites


def within_step_ceiling(source, label, ceiling=CYCLE_STEP_CEILING):
    """`bound_sites` for one source, counted in a child process.

    The caller's own tracing is never touched, which is the whole
    reason the count is taken over there and not here.
    """
    try:
        run = subprocess.run(
            [sys.executable, '-c', CHILD, TESTS, source, label,
             str(ceiling)],
            capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as refusal:
        raise AssertionError(
            'the step-counted child refused the source, and its own '
            f'message is the answer\n{refusal.stderr.strip()}'
        ) from refusal
    return ast.literal_eval(run.stdout)
