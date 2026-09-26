"""A step-count bound for a loop whose guard is there to stop it.

machinery_route follows a callee's base through the bindings table to
decide whether a call is the import machinery. Its `seen` guard is the
only thing that stops a base that feeds itself, and the loop is UNCAPPED
— so deleting the guard does not make it answer wrong, it makes it not
stop. A wall clock is the wrong bound for that: a clock is the flaky
class, and a pin that spends the suite's whole time budget to learn what
it already knows is the defect this bound exists to remove.

So the bound is a COUNT of line events in that one function, installed
by the caller around the call and restored in a finally. With the guard
the loop leaves in a handful of steps; without it the count trips and
the failure names the guard. The ceiling lives here, in the control, so
no guard's behaviour depends on it.
"""
import sys

from _launch_audit import bound_sites

# Far above any real chain: a module spelling `a = b; b = a` reaches the
# loop twice and leaves on the second. A count of steps and not a
# duration, so a loaded machine cannot fail a correct tree.
CYCLE_STEP_CEILING = 10_000

TRACED = 'machinery_route'


def within_step_ceiling(source, label, ceiling=CYCLE_STEP_CEILING):
    """`bound_sites` for one source, refusing a machinery_route that runs
    past `ceiling` line events.

    machinery_route is a closure inside launch_refusals, so its frame is
    named rather than imported. sys.settrace fires one line event per
    iteration, which is what makes the bound a step count.
    """
    steps = 0

    def local(frame, event, arg):
        del frame, arg
        nonlocal steps
        if event == 'line':
            steps += 1
            if steps > ceiling:
                raise AssertionError(
                    f'{TRACED} ran past {ceiling} steps; its seen guard is '
                    'not stopping the base chain')
        return local

    def tracer(frame, event, arg):
        del arg
        if event == 'call' and frame.f_code.co_name == TRACED:
            return local
        return None

    previous = sys.settrace(tracer)
    try:
        return bound_sites(source, label)
    finally:
        sys.settrace(previous)
