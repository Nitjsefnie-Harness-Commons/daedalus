"""What every measured child runs with, so two runs execute the same code.

The first two settings here are host state rather than tree state, and
neither is under this repository's control.

`PYTHONHASHSEED`
    CPython randomises string hashing per process when it is unset, so which
    order a module's globals are built in — and what that costs — differs
    between two runs of the same tree. The startup-only child is the whole
    of that term: it is a bare interpreter that did nothing, so whatever its
    cost varies with, it varies with.

`PYTHONDONTWRITEBYTECODE`
    A warm `__pycache__` makes a child cheaper by however much source it
    would have compiled, and which children find one depends on what ran
    before them. Measured on two runs of the same tree (CPython 3.13.14,
    valgrind 3.24.0, `--separate-threads=yes`): the first child's harness
    main thread compiled 21,930,238 instructions of source and the second
    compiled 6,268,419 — a 15,661,819 difference that is not the journey's
    work.

Neither is a number to subtract and neither is a tolerance to widen: both
make ONE side of the baseline subtraction differ from the other, and the
baseline is whichever child happened to run first. Fixing the seed and the
cache makes both sides pay the same compile, which is the property the whole
measurement rests on.

`DAEDALUS_CALLGRIND_BOUNDARY` is the third setting and it is not in
`MEASURED_ENV`, because its value is a path this repository does not know:
the job compiles `scripts/ci/callgrind_boundary.c` and exports where it put
the result, and a static dict merged over `os.environ` cannot carry a
per-environment path. So the child reads the name from the environment the
same way it reads the other two, and the parent is what has to decide the
variable is there at all — `boundary_refusal()` below.

It exists because the counted region is the whole child from `exec`, so
interpreter startup and the compile of its whole import closure are inside
it, on the harness child's own thread, which no journey excludes. That is
not a constant: measured with ASLR pinned on the import alone, adding
15,470 comment bytes to `tests/_journey_typed.py` and changing nothing else
moved it by 931,947 instructions, and a recorded count is
`median(kept_journey - kept_bridge_only)`, so the movement lands on the net
multiplied by the baseline's ratio to the residual it corrects. A client
request cannot be issued from pure Python — it is `asm volatile` inside a
GNU statement expression — so the boundary is a helper the job compiles and
the harness calls at the first statement of `main()`.

Refusing rather than defaulting is the whole point, and it is why the check
is HERE and not only in the child: an unset variable is the one state the
child cannot refuse, because a developer running one journey by hand is in
exactly the same state as a counted run that failed to build its helper.
The child is therefore inert when unset and the parent refuses to start a
counted run without it, which is the same rule `journey_threads` states —
anything the harness cannot establish is a refusal, never a silent
inclusion. Only callgrind is bounded: the request is callgrind's own, and
`perf` counts the same process from `exec` whether or not it is issued.

Its own module because the counters module is at its size ceiling, and
because these are facts about the CHILDREN rather than about the counting.
"""
import os

MEASURED_ENV = {'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1'}

BOUNDARY_ENV = 'DAEDALUS_CALLGRIND_BOUNDARY'


def boundary_refusal():
    """Why a counted run cannot start, or None when it can.

    A sentence rather than a bool, because the refusal is what a reader of
    a failed run gets and it has to name the variable that was missing.
    """
    if os.environ.get(BOUNDARY_ENV):
        return None
    return (f'{BOUNDARY_ENV} names no compiled boundary helper, so a '
            'counted child would keep its interpreter startup and the '
            'compile of its import closure in the recorded count')
