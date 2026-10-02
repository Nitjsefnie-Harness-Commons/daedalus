"""What every measured child runs with, so two runs execute the same code.

Both settings here are host state rather than tree state, and neither is
under this repository's control.

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

Its own module because the counters module is at its size ceiling, and
because these are facts about the CHILDREN rather than about the counting.
"""
MEASURED_ENV = {'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1'}
