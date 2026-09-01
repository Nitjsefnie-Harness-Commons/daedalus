#!/usr/bin/env python3
"""Time each test in a checkout, without that checkout's help.

This is the instrument, and an instrument must not depend on the thing it
measures. A speed comparison runs two checkouts — this commit and the last
release — and if the durations came from a feature the runner had to provide,
only the side that already had that feature could be measured, which is the
one side whose speed was never in question.

So nothing is asked of the tree being timed. Each suite is run with `-u`, so
its output is unbuffered whatever it does internally, and the wall clock is
read as each result line arrives. `_util.runner` prints one `  PASS  <name>`
line per test as it finishes, so the gap between consecutive lines is the
duration of the test the later line names.

Only passing tests are recorded: the comparison intersects the two sides on
name, and a test that failed on one of them must be absent rather than
present with however long it took to give up.

The first test of a suite carries that suite's interpreter startup and
imports, because there is no earlier line to measure from. That cost lands on
both sides of the comparison, which is what matters.

Nested invocations of `main()` relay their marker lines through the suite's
stdout. Those markers are this instrument's exact output format, so their
spans can be recognized without guessing from result content.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# `  PASS  name`, as _util.runner prints it. FAIL and SKIP lines are read too:
# they end a test's interval even though that test is not recorded.
_RESULT = re.compile(r'^\s+(PASS|FAIL|SKIP|ERROR)\s+(\S+)')
_RELAY_START = re.compile(r'^=== (.+) ===$')
_RELAY_END = re.compile(r'^--- timed ([0-9]+) passing tests in (.+)$')


def selected(name, only, except_globs):
    """Whether one suite file is inside the selection the caller asked for.

    Globs are matched against the suite file NAME the way pathlib matches a
    pattern that carries no separator: the whole name, with `*` never crossing
    a directory boundary. `--except` is applied first, so `--only '*'` minus
    the globs another group already claims is exactly that group's complement.
    """
    if any(Path(name).match(glob) for glob in except_globs or ()):
        return False
    if not only:
        return True
    return any(Path(name).match(glob) for glob in only)


def time_suite(python, suite, cwd):
    """Every passing test in one suite, with the seconds it took."""
    durations = {}
    started = time.monotonic()
    lines = []
    with subprocess.Popen(
            [python, '-u', str(suite)], cwd=str(cwd),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1) as child:
        assert child.stdout is not None
        for line in child.stdout:
            sys.stdout.write(line)
            lines.append((line.rstrip('\n'), time.monotonic()))
        child.wait()

    stack = []
    suppressed = []
    for index, (line, _now) in enumerate(lines):
        start = _RELAY_START.fullmatch(line)
        if start:
            stack.append([start.group(1), index, 0])
            continue
        match = _RESULT.match(line)
        if match and stack:
            if match.group(1) == 'PASS':
                stack[-1][2] += 1
            continue
        end = _RELAY_END.fullmatch(line)
        if end and stack:
            count, name = end.groups()
            candidate = stack[-1]
            if name == candidate[0] and int(count) == candidate[2]:
                suppressed.append((candidate[1], index))
                stack.pop()

    def is_suppressed(index):
        return any(start <= index <= end for start, end in suppressed)

    for index, (line, now) in enumerate(lines):
        if is_suppressed(index):
            continue
        match = _RESULT.match(line)
        if match:
            outcome, name = match.group(1), match.group(2).rstrip(':')
            if outcome == 'PASS':
                durations[name] = now - started
            started = now
    return durations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', required=True,
                        help='checkout to time')
    parser.add_argument('--python', required=True,
                        help='interpreter to run that checkout with')
    parser.add_argument('--out', required=True,
                        help='directory to write one JSON per suite into')
    parser.add_argument(
        '--only', nargs='+', metavar='GLOB',
        help='time only the suites whose file name matches one of these '
             'globs; without it every suite runs')
    parser.add_argument(
        '--except', nargs='+', default=(), dest='except_globs',
        metavar='GLOB',
        help='drop the suites whose file name matches one of these globs, '
             'applied before --only')
    args = parser.parse_args(argv)

    tree = Path(args.tree).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    found = sorted((tree / 'tests').glob('test_*.py'))
    suites = [suite for suite in found
              if selected(suite.name, args.only, args.except_globs)]
    if not suites:
        print(f'no suites found under {tree}' if not found else
              f'no suite under {tree} matches the selection', file=sys.stderr)
        return 1
    timed = 0
    for suite in suites:
        print(f'=== {suite.name} ===', flush=True)
        durations = time_suite(args.python, suite, tree)
        timed += len(durations)
        (out / f'{suite.stem}.json').write_text(
            json.dumps({'tests': durations}), encoding='utf-8')
        print(f'--- timed {len(durations)} passing tests in {suite.name}',
              flush=True)
    # Every suite running and none of them yielding a single passing test is
    # not a fast tree, it is a measurement that did not happen — and it used
    # to be written out as an empty map and reported as success, which the
    # comparison then read as "nothing to compare" and also called success.
    # One suite contributing nothing is allowed: a suite can skip entirely on
    # a platform. All of them contributing nothing cannot be. That leniency is
    # this instrument's alone -- the speed comparison is the stricter consumer
    # and judges a suite that recorded nothing as lost.
    if timed == 0:
        print(f'timed no passing tests across {len(suites)} suites; '
              'refusing to report a measurement that did not happen',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
