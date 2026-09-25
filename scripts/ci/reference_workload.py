#!/usr/bin/env python3
"""Report how long one fixed unit of work takes on this runner.

The timings data file counts each suite's runtime as a multiple of this
workload, so a runner twice as slow scales every weight together and the
packing is unchanged. The work is therefore FIXED — a fixed iteration count
of a fixed computation — and the elapsed seconds are what is measured. A
"run until N seconds" loop would measure itself: the seconds would be N
on every runner, the unit would carry no signal, and a weight in it would
say nothing about the suite.

No I/O, no sleeping, no randomness and nothing read from the repository:
the unit is a property of the interpreter and the CPU, which is exactly
what it has to be. A SplitMix64 step on a 64-bit LCG is enough arithmetic
to keep the multiplier busy, small enough to stay in the interpreter's
fast paths, and deterministic to the last bit, so two runs of the same
count do the same work and differ only in how long the CPU took.

ITERATIONS is 16,000,000, measured on 2026-09-25 on this repository's
CI-class machine (AMD EPYC, CPython 3.13.14) at 3.18, 3.19, 3.31 and
3.63 s over four separate interpreter runs -- the way the workflow
invokes it -- so the round number near 3 s is `4.9e6 iterations/s`.
The count is a round 10^6 multiple at that rate rather than a figure
chosen for a seconds target, because the seconds are the reading and
the work is the unit. Three seconds is long enough that the spread
between two readings on the same machine (0.45 s here, one co-tenant
outlier) is an eighth of the reading, and short enough that one cell
of a 90-minute budget pays it once.

  python3 scripts/ci/reference_workload.py           # human line
  python3 scripts/ci/reference_workload.py --json    # {"seconds": …}
"""
import argparse
import json
import sys
import time

ITERATIONS = 16_000_000
_MASK = 0xFFFFFFFFFFFFFFFF
_MULTIPLIER = 6364136223846793005
_INCREMENT = 1442695040888963407
_SEED = 0x0123456789ABCDEF


def work(iterations=ITERATIONS):
    """Run the fixed computation; return the final state as a checksum.

    The returned value is a checksum of the WORK, not of the time: a
    caller can confirm the iteration count happened, and cannot use it to
    shortcut the loop. The loop body must not be hoisted, folded or
    otherwise short-circuited; its instruction count is the constant the
    weights are counted in. The constants are bound to locals first: three
    global lookups per iteration is a fourth of the loop's cost, and the
    unit must be a fixed COMPUTATION rather than a fixed mix of
    computation and dictionary lookups.
    """
    multiplier, increment, mask = _MULTIPLIER, _INCREMENT, _MASK
    state = _SEED
    for _ in range(iterations):
        state = (state * multiplier + increment) & mask
        state ^= state >> 29
    return state


def measure(iterations=ITERATIONS):
    """The elapsed seconds of one run of the fixed work, and its checksum."""
    started = time.perf_counter()
    checksum = work(iterations)
    return time.perf_counter() - started, checksum


def _parser():
    parser = argparse.ArgumentParser(
        description='Report the elapsed seconds of one fixed unit of work.')
    parser.add_argument(
        '--json', action='store_true', dest='as_json',
        help='print {"seconds": …, "iterations": …, "checksum": …}')
    parser.add_argument(
        '--iterations', type=int, default=ITERATIONS,
        help='iteration count (the count IS the unit; the default is it)')
    return parser


def main(argv=None):
    """Print the reading; return 0, or 1 after a named refusal."""
    args = _parser().parse_args(argv)
    if args.iterations < 1:
        print('reference_workload: --iterations must be at least one',
              file=sys.stderr)
        return 1
    seconds, checksum = measure(args.iterations)
    if args.as_json:
        print(json.dumps({'seconds': seconds,
                          'iterations': args.iterations,
                          'checksum': f'{checksum:016x}'}))
    else:
        print(f'reference workload: {seconds:.3f} s over '
              f'{args.iterations} iterations (checksum '
              f'{checksum:016x})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
