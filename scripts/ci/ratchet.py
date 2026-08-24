#!/usr/bin/env python3
"""Raise the coverage floor to what a run actually measured.

The floor is a ratchet: it only ever rises. What went stale beside it was the
`measured:` comment — a hand-maintained claim that nothing ever compared
against reality, so the test pinning the two numbers together cannot tell a
current measurement from one taken fifteen commits ago. It recorded 73.3 while
the job measured 75.0, which is a two-point regression budget nobody chose.

  python3 scripts/ci/ratchet.py --measured 75.0   # rewrite if that justifies a raise

Writes nothing when the measurement justifies no raise, so the caller decides
what happened by asking git whether the file moved.
"""
import argparse
import re
import sys
from pathlib import Path

WORKFLOW = (Path(__file__).resolve().parents[2]
            / '.github' / 'workflows' / 'tests.yml')

# How far the floor sits below the measurement. It absorbs run-to-run variation
# — which suites reach a subprocess before it exits, and which optional
# dependency set the runner resolved — rather than being a regression budget.
# test_the_coverage_ratchet_records_what_it_was_calibrated_to refuses a gap
# over 2.0 points, so this stays under it with room for the rounding.
BUFFER = 1.5

# The largest gap the pinning test accepts. Named here so a raise this script
# writes can be checked against it before the file is touched.
MAX_GAP = 2.0

_MEASURED = re.compile(r'(#\s*measured:\s*)([0-9]+(?:\.[0-9]+)?)')
_FLOOR = re.compile(r'(#\s*floor:\s*)([0-9]+(?:\.[0-9]+)?)')
_FLAG = re.compile(r'(--fail-under=)([0-9]+(?:\.[0-9]+)?)')


def read_calibration(text):
    """(measured, floor) the workflow records, or SystemExit when it records none.

    The flag is read too and required to agree with the floor: rewriting a
    file whose gate already disagrees with its own comment would bake that
    disagreement in rather than reporting it.
    """
    measured, floor, flag = (_MEASURED.search(text), _FLOOR.search(text),
                             _FLAG.search(text))
    if not (measured and floor and flag):
        raise SystemExit('the coverage gate records no calibration')
    floor_value, flag_value = float(floor.group(2)), float(flag.group(2))
    if floor_value != flag_value:
        raise SystemExit(
            f'the gate runs --fail-under={flag_value} while the recorded floor '
            f'is {floor_value}; fix that by hand before ratcheting')
    return float(measured.group(2)), floor_value


def floor_for(measured):
    """The floor a measurement justifies."""
    return round(measured - BUFFER, 1)


def update(text, measured):
    """The rewritten workflow, or None when nothing is justified.

    A measurement below the recorded floor plus the buffer changes nothing:
    the floor is a high-water mark, and a run that reached fewer subprocesses
    than the best one is not evidence that the code lost coverage.
    """
    floor = read_calibration(text)[1]
    target = floor_for(measured)
    if target <= floor:
        return None
    if not target < measured:
        raise SystemExit(
            f'a floor of {target} is not below the {measured} measured')
    if measured - target > MAX_GAP:
        raise SystemExit(
            f'a floor of {target} leaves a {measured - target:.1f} point gap '
            f'below {measured}, over the {MAX_GAP} the pinning test allows')
    text = _MEASURED.sub(lambda m: f'{m.group(1)}{measured:.1f}', text, count=1)
    text = _FLOOR.sub(lambda m: f'{m.group(1)}{target:.1f}', text, count=1)
    return _FLAG.sub(lambda m: f'{m.group(1)}{target:.1f}', text, count=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--measured', type=float, required=True,
                    help='the total coverage percentage this run measured')
    ap.add_argument('--workflow', type=Path, default=WORKFLOW,
                    help='the workflow file carrying the calibration')
    args = ap.parse_args()

    text = args.workflow.read_text(encoding='utf-8')
    rewritten = update(text, args.measured)
    floor = read_calibration(text)[1]
    if rewritten is None:
        print(f'coverage {args.measured:.1f}% justifies no raise above the '
              f'{floor} floor')
        return 0
    args.workflow.write_text(rewritten, encoding='utf-8')
    print(f'raised the coverage floor {floor} -> {floor_for(args.measured):.1f} '
          f'(measured {args.measured:.1f}%)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
