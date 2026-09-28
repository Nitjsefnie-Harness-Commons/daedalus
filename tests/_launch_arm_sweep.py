#!/usr/bin/env python3
"""Sweep EVERY arm of both analysers and check what the records claim.

`CRASH_CONTROLLED` is checked both ways a 14-arm set can be: each name
really crashes, and the set partitions the CONTROLLED arms. Neither
covers the 136 arms it does NOT name, and "no other arm is crash-held"
needs every one of them, so the sweep is the only place it can be
checked.

It lives here, beside the modules it imports, because it is a driver
over the test tree and not a repository script: under `scripts/` the
type checker's default scope put it on the root path, where the four
test-tree imports it is built on did not resolve.

    python3 tests/_launch_arm_sweep.py
"""
import sys
import tempfile
from pathlib import Path

from _arm_sweep import arm_sweep
from _launch_arm_records import (
    CRASH_CONTROLLED, EVIDENCE, ID, SECONDARY_CONTROLLED, STATE)
from _launch_arms import CONTROLLED, LAUNCH_ARMS, REDUNDANT, STATES
from test_launch_arms import CEILING_ARMS

EVIDENCE_OF = {arm[ID]: arm[EVIDENCE] for arm in LAUNCH_ARMS}


def _by_value(found):
    return sorted(label for label in found['moved']
                  if label not in found['crash'])


def _counters(findings):
    """Print every counter, and return the checks that did not agree."""
    states = {state: sum(1 for arm in LAUNCH_ARMS if arm[STATE] == state)
              for state in STATES}
    refused = sorted(name for name, found in findings.items()
                     if 'refused' in found)
    silent = {name for name, found in findings.items()
              if found.get('timed_out')}
    # A child that did not answer has no verdict for its evidence to be
    # IN, so counting it uncontrolled would condemn the two ceiling arms.
    answered = [arm for arm in LAUNCH_ARMS
                if arm[ID] not in silent and arm[ID] not in refused]
    uncontrolled = sorted(
        arm[ID] for arm in answered
        if arm[STATE] == CONTROLLED
        and EVIDENCE_OF[arm[ID]] not in findings[arm[ID]]['moved']
        and EVIDENCE_OF[arm[ID]] not in findings[arm[ID]]['crash'])
    crash_held = {name for name, found in findings.items()
                  if EVIDENCE_OF[name] in found['crash']}
    by_value = sorted(name for name, found in findings.items()
                      if not found['crash'] and found['moved'])
    only_recorded = sorted(CRASH_CONTROLLED - crash_held)
    only_derived = sorted(crash_held - CRASH_CONTROLLED)
    both = {name: _by_value(findings[name]) for name in sorted(crash_held)
            if _by_value(findings[name])}
    unrecorded = sorted(name for name in both
                        if not set(SECONDARY_CONTROLLED.get(name, ())))

    print(f'arms: {len(LAUNCH_ARMS)} {states}')
    print(f'cut refused: {len(refused)} {refused}')
    print(f'child did not answer: {len(silent)} {sorted(silent)}')
    print(f'evidence does not control: {len(uncontrolled)} {uncontrolled}')
    print(f'HELD BY A RAISE: {len(crash_held)}')
    print(f'held by a value only: {len(by_value)}')
    print(f'CRASH_CONTROLLED size : {len(CRASH_CONTROLLED)}')
    print(f'derived       size    : {len(crash_held)}')
    print(f'recorded - derived (in the set, sweep says no): {only_recorded}')
    print(f'derived - recorded (crashes, not in the set)  : {only_derived}')
    print(f'crash-held arms that ALSO move a label by value: {both}')

    failed = []
    if refused:
        failed.append(f'{len(refused)} arms the sweep could not cut: '
                      f'{refused}')
    if uncontrolled:
        failed.append(f'{len(uncontrolled)} CONTROLLED arms whose own '
                      f'evidence does not control them: {uncontrolled}')
    if only_recorded:
        failed.append('recorded in CRASH_CONTROLLED but not crash-held by '
                      f'the sweep: {only_recorded}')
    if only_derived:
        failed.append('crash-held by the sweep and missing from '
                      f'CRASH_CONTROLLED: {only_derived}')
    if unrecorded:
        failed.append('crash-held arms that also move a label by value with '
                      f'no SECONDARY_CONTROLLED entry: {unrecorded}')
    if set(silent) != CEILING_ARMS:
        failed.append(f'arms whose child did not answer {silent}, against '
                      f'the {sorted(CEILING_ARMS)} the step ceiling is '
                      'bound to')
    if failed:
        # `removed` and `promoted` exist so a reader can see which
        # clause a verdict depended on, and an arm named in a failure is
        # exactly where that text is wanted.
        for name in sorted({*refused, *uncontrolled, *only_recorded,
                            *only_derived, *unrecorded, *silent}):
            found = findings.get(name, {})
            removed = found.get('removed') or '<the cut was refused>'
            print(f'  {name}: removed={removed!r} '
                  f'promoted={found.get("promoted") or "<nothing>"!r}')
    return failed


def main():
    """Sweep the whole table, and exit nonzero on any failed check."""
    with tempfile.TemporaryDirectory(prefix='launch-arm-sweep-') as name:
        findings = arm_sweep(Path(name), LAUNCH_ARMS)
    failed = _counters(findings)
    if failed:
        for line in failed:
            print(f'FAIL {line}')
        return 1
    print(f'ok - {len(LAUNCH_ARMS)} arms swept, every check agrees with the '
          f'records ({REDUNDANT} and DEAD arms included)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
