#!/usr/bin/env python3
"""Sweep EVERY arm of both analysers and check what the records claim.

`CRASH_CONTROLLED` says which CONTROLLED arms go red by RAISING rather
than to another value. `tests/test_launch_arms.py` checks each name in
it really does, and that the set is a partition of the CONTROLLED arms
-- but neither is a statement about the arms the set does NOT name, so
"and no other arm is crash-held" was a claim only re-derivable by
rewriting this sweep by hand. This is that sweep.

Run it with no arguments, from anywhere:

    python3 scripts/launch_arm_sweep.py

It is a gate and not a suite on purpose. Sweeping all 150 arms costs
about two minutes and it is the completeness half of a claim whose
forward half already runs on every leg, so putting it in `tests/` would
buy nothing a reader cannot have in one command. Every counter it
prints is derived rather than copied from a record, so the records
cannot drift from it silently: each check either passes because the two
agree, or exits nonzero naming the arms that disagree.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

from _arm_sweep import arm_sweep  # noqa: E402
from _launch_arm_records import (CRASH_CONTROLLED,  # noqa: E402
                                SECONDARY_CONTROLLED)
from _launch_arms import (CONTROLLED, LAUNCH_ARMS,  # noqa: E402
                          REDUNDANT, STATES, STEP_CEILING_CONTROL)

# The arm table's own column order, named so nothing here reads a bare 6.
ID, STATE, EVIDENCE = 0, 6, 7

# The step ceiling is a control for a mutant that does not answer WRONG,
# it does not stop, so the arms bound to it are exactly the arms whose
# child produces no verdict. An arm that DOES answer has a row to hold
# it, and reaching for the ceiling instead would be a control that can
# only fail on a hang. That is a claim about the sweep rather than about
# the table, so it is checked here and nowhere else.
CEILING_ARMS = {arm[ID] for arm in LAUNCH_ARMS
                if arm[EVIDENCE] == STEP_CEILING_CONTROL}
EVIDENCE_OF = {arm[ID]: arm[EVIDENCE] for arm in LAUNCH_ARMS}


def _by_value(found):
    return sorted(label for label in found['moved']
                  if label not in found['crash'])


def _report(findings):
    """Print every counter, and return the checks that did not agree."""
    states = {state: sum(1 for arm in LAUNCH_ARMS if arm[STATE] == state)
              for state in STATES}
    refused = sorted(name for name, found in findings.items()
                     if 'refused' in found)
    silent = {name for name, found in findings.items()
              if found.get('timed_out')}
    # An arm whose child did not answer has no verdict for its evidence to
    # be IN, so counting it as uncontrolled would condemn the two arms the
    # step ceiling exists for. They are checked by the ceiling check below.
    answered = [arm for arm in LAUNCH_ARMS
                if arm[ID] not in silent and arm[ID] not in refused]
    uncontrolled = sorted(arm[ID] for arm in answered
                          if arm[STATE] == CONTROLLED
                          and EVIDENCE_OF[arm[ID]] not in findings[arm[ID]]['moved']
                          and EVIDENCE_OF[arm[ID]]
                          not in findings[arm[ID]]['crash'])
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
    return failed


def main():
    """Sweep the whole table, and exit nonzero on any failed check."""
    with tempfile.TemporaryDirectory(prefix='launch-arm-sweep-') as name:
        findings = arm_sweep(Path(name), LAUNCH_ARMS)
    failed = _report(findings)
    if failed:
        for line in failed:
            print(f'FAIL {line}')
        return 1
    print(f'ok - {len(LAUNCH_ARMS)} arms swept, every check agrees with the '
          f'records ({REDUNDANT} and DEAD arms included)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
