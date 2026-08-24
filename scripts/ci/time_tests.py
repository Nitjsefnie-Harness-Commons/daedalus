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


def time_suite(python, suite, cwd):
    """Every passing test in one suite, with the seconds it took."""
    durations = {}
    started = time.monotonic()
    with subprocess.Popen(
            [python, '-u', str(suite)], cwd=str(cwd),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1) as child:
        assert child.stdout is not None
        for line in child.stdout:
            sys.stdout.write(line)
            now = time.monotonic()
            match = _RESULT.match(line.rstrip('\n'))
            if match:
                outcome, name = match.group(1), match.group(2).rstrip(':')
                if outcome == 'PASS':
                    durations[name] = now - started
                started = now
        child.wait()
    return durations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', required=True,
                        help='checkout to time')
    parser.add_argument('--python', required=True,
                        help='interpreter to run that checkout with')
    parser.add_argument('--out', required=True,
                        help='directory to write one JSON per suite into')
    args = parser.parse_args(argv)

    tree = Path(args.tree).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    suites = sorted((tree / 'tests').glob('test_*.py'))
    if not suites:
        print(f'no suites found under {tree}', file=sys.stderr)
        return 1
    for suite in suites:
        print(f'=== {suite.name} ===', flush=True)
        durations = time_suite(args.python, suite, tree)
        (out / f'{suite.stem}.json').write_text(
            json.dumps({'tests': durations}), encoding='utf-8')
        print(f'--- timed {len(durations)} passing tests in {suite.name}',
              flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
