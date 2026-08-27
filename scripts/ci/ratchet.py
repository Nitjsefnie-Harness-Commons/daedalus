#!/usr/bin/env python3
"""Raise the coverage floor to what a run actually measured.

The floor is a ratchet: it only ever rises. What went stale beside it was the
`measured:` comment — a hand-maintained claim that nothing ever compared
against reality, so the test pinning the two numbers together cannot tell a
current measurement from one taken fifteen commits ago. It recorded 73.3 while
the job measured 75.0, which is a two-point regression budget nobody chose.

  python3 scripts/ci/ratchet.py --language python --measured 75.0

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
# test_each_coverage_ratchet_records_what_it_was_calibrated_to refuses a gap
# over 2.0 points, so this stays under it with room for the rounding.
BUFFER = 1.5

# The largest gap the pinning test accepts. Named here so a raise this script
# writes can be checked against it before the file is touched.
MAX_GAP = 2.0

_NUMBER = r'([0-9]+(?:\.[0-9]+)?)'
_LANGUAGES = {
    'python': {
        'label': 'Python',
        'flag': (
            r'^([ \t]*run:[ \t]+python[ \t]+-m[ \t]+coverage[ \t]+'
            r'report[ \t]+--fail-under=)' + _NUMBER
            + r'([ \t]+--precision=1[ \t]*)$'),
    },
    'javascript': {
        'label': 'JavaScript',
        'flag': (
            r'^([ \t]*--xml[ \t]+javascript-coverage\.xml[ \t]+'
            r'--fail-under=)' + _NUMBER + r'([ \t]*)$'),
    },
}


def _patterns(language):
    try:
        settings = _LANGUAGES[language]
    except KeyError:
        raise SystemExit(f'unknown coverage language: {language}') from None
    label = settings['label']
    measured = (
        r'^([ \t]*#[ \t]+' + label + r'[ \t]+measured:[ \t]*)'
        + _NUMBER + r'([ \t]*)$')
    floor = (
        r'^([ \t]*#[ \t]+' + label + r'[ \t]+floor:[ \t]*)'
        + _NUMBER + r'([ \t]*)$')
    return tuple(
        re.compile(pattern, re.MULTILINE)
        for pattern in (measured, floor, settings['flag']))


def _unique_match(text, pattern, language, name):
    matches = list(pattern.finditer(text))
    if not matches:
        raise SystemExit(
            f'the {language} coverage gate records no calibration')
    if len(matches) != 1:
        raise SystemExit(
            f'the {language} coverage gate must record exactly one {name}; '
            f'found {len(matches)}')
    return matches[0]


def read_calibration(text, language):
    """(measured, floor) the workflow records, or SystemExit when it records none.

    The flag is read too and required to agree with the floor: rewriting a
    file whose gate already disagrees with its own comment would bake that
    disagreement in rather than reporting it.
    """
    measured_pattern, floor_pattern, flag_pattern = _patterns(language)
    measured = _unique_match(
        text, measured_pattern, language, 'measured marker')
    floor = _unique_match(text, floor_pattern, language, 'floor marker')
    flag = _unique_match(text, flag_pattern, language, 'gate flag')
    floor_value, flag_value = float(floor.group(2)), float(flag.group(2))
    if floor_value != flag_value:
        raise SystemExit(
            f'the gate runs --fail-under={flag_value} while the recorded floor '
            f'is {floor_value}; fix that by hand before ratcheting')
    return float(measured.group(2)), floor_value


def floor_for(measured):
    """The floor a measurement justifies."""
    return round(measured - BUFFER, 1)


def update(text, measured, language):
    """The rewritten workflow, or None when nothing is justified.

    A measurement below the recorded floor plus the buffer changes nothing:
    the floor is a high-water mark, and a run that reached fewer subprocesses
    than the best one is not evidence that the code lost coverage.
    """
    measured_pattern, floor_pattern, flag_pattern = _patterns(language)
    floor = read_calibration(text, language)[1]
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

    def replacement(match, value):
        return f'{match.group(1)}{value:.1f}{match.group(3)}'

    text = measured_pattern.sub(
        lambda match: replacement(match, measured), text, count=1)
    text = floor_pattern.sub(
        lambda match: replacement(match, target), text, count=1)
    return flag_pattern.sub(
        lambda match: replacement(match, target), text, count=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--language', choices=sorted(_LANGUAGES), required=True,
                    help='coverage calibration to raise')
    ap.add_argument('--measured', type=float, required=True,
                    help='the total coverage percentage this run measured')
    ap.add_argument('--workflow', type=Path, default=WORKFLOW,
                    help='the workflow file carrying the calibration')
    args = ap.parse_args()

    text = args.workflow.read_text(encoding='utf-8')
    rewritten = update(text, args.measured, args.language)
    floor = read_calibration(text, args.language)[1]
    label = _LANGUAGES[args.language]['label']
    if rewritten is None:
        print(f'{label} coverage {args.measured:.1f}% justifies no raise '
              f'above the {floor} floor')
        return 0
    args.workflow.write_text(rewritten, encoding='utf-8')
    print(f'raised the {label} coverage floor {floor} -> '
          f'{floor_for(args.measured):.1f} (measured {args.measured:.1f}%)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
