#!/usr/bin/env python3
"""The coverage ratchet: it raises the floor, and only ever upward.

CI rewrites the gate in .github/workflows/tests.yml from what a run actually
measured, so these tests pin what that rewrite may do — never lower the floor,
never touch a file whose recorded measurement has already drifted from its
gate, and never leave behind a number the pinning test would reject.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_RATCHET_WORKFLOW = """        # measured: 73.3
        # floor: 72
        run: python -m coverage report --fail-under=72 --precision=1
"""


def _ratchet():
    return _util.load(ROOT / 'scripts' / 'ci' / 'ratchet.py', 'ratchet_mod')


def test_the_ratchet_raises_the_floor_to_what_a_run_measured(tmp):
    """The recorded measurement is what goes stale, so it is rewritten too."""
    del tmp
    ratchet = _ratchet()
    raised = ratchet.update(_RATCHET_WORKFLOW, 80.0)
    assert raised is not None, 'a measurement 8 points up justified no raise'
    measured, floor = ratchet.read_calibration(raised)
    assert measured == 80.0, raised
    assert floor == 78.5, raised
    assert '--fail-under=78.5' in raised, raised


def test_the_ratchet_never_lowers_the_floor(tmp):
    """A ratchet only turns one way: a run that reached fewer subprocesses is
    not evidence that the code lost coverage."""
    del tmp
    ratchet = _ratchet()
    for measured in (73.3, 72.0, 60.0, 73.4):
        assert ratchet.update(_RATCHET_WORKFLOW, measured) is None, measured


def test_the_ratchet_leaves_a_raise_the_pinning_rule_accepts(tmp):
    """Whatever it writes must satisfy the test that pins these numbers, or
    the next run goes red on a file this script wrote."""
    del tmp
    ratchet = _ratchet()
    for measured in (80.0, 91.7, 100.0):
        raised = ratchet.update(_RATCHET_WORKFLOW, measured)
        assert raised is not None, measured
        recorded, floor = ratchet.read_calibration(raised)
        assert floor < recorded, (floor, recorded)
        assert recorded - floor <= 2.0, (recorded, floor)


def test_the_ratchet_refuses_a_gate_that_already_disagrees(tmp):
    """A flag that has drifted from its own comment is a hand fix, not
    something to bake in under a fresh number."""
    del tmp
    ratchet = _ratchet()
    disagreeing = _RATCHET_WORKFLOW.replace('--fail-under=72', '--fail-under=70')
    try:
        ratchet.update(disagreeing, 90.0)
    except SystemExit as why:
        assert 'fix that by hand' in str(why), why
    else:
        raise AssertionError('a disagreeing gate was rewritten anyway')


def test_the_ratchet_refuses_a_file_carrying_no_calibration(tmp):
    """Silently doing nothing would read as 'no raise justified'."""
    del tmp
    ratchet = _ratchet()
    try:
        ratchet.update('run: python -m coverage report\n', 90.0)
    except SystemExit as why:
        assert 'records no calibration' in str(why), why
    else:
        raise AssertionError('a file with no calibration was accepted')


def test_the_ratchet_rewrites_the_file_it_is_pointed_at(tmp):
    """The entry point writes only when it raises, and says which it did."""
    workflow = Path(tmp) / 'tests.yml'
    workflow.write_text(_RATCHET_WORKFLOW, encoding='utf-8')
    script = str(ROOT / 'scripts' / 'ci' / 'ratchet.py')

    quiet = subprocess.run(
        [sys.executable, script, '--measured', '73.0',
         '--workflow', str(workflow)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert quiet.returncode == 0, (quiet.stdout, quiet.stderr)
    assert 'justifies no raise' in quiet.stdout, quiet.stdout
    assert workflow.read_text(encoding='utf-8') == _RATCHET_WORKFLOW

    raised = subprocess.run(
        [sys.executable, script, '--measured', '80.0',
         '--workflow', str(workflow)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert raised.returncode == 0, (raised.stdout, raised.stderr)
    assert 'raised the coverage floor 72.0 -> 78.5' in raised.stdout, raised.stdout
    assert '--fail-under=78.5' in workflow.read_text(encoding='utf-8')


def test_the_ratchet_can_raise_the_real_workflow(tmp):
    """The regexes are pinned against the file they actually run on, not only
    against the fixture above."""
    del tmp
    ratchet = _ratchet()
    text = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    recorded, floor = ratchet.read_calibration(text)
    assert floor < recorded, (floor, recorded)
    raised = ratchet.update(text, recorded + 5.0)
    assert raised is not None, 'the real workflow refused a five-point raise'
    new_measured, new_floor = ratchet.read_calibration(raised)
    assert new_measured == recorded + 5.0, new_measured
    assert new_floor > floor, (new_floor, floor)


def test_the_coverage_ratchet_records_what_it_was_calibrated_to(tmp):
    """The floor, the flag and the measurement it was set against agree.

    The ratchet is raised by hand, so nothing keeps it near the number it was
    calibrated against except someone remembering — and it had drifted 3.2
    points below measured coverage, which is a regression budget rather than
    a ratchet. Pinning the recorded measurement next to the floor makes
    raising one without the other fail here instead of silently widening it.
    """
    del tmp
    workflow = (_util.ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    measured = re.search(r'#\s*measured:\s*([0-9.]+)', workflow)
    floor = re.search(r'#\s*floor:\s*([0-9.]+)', workflow)
    assert measured and floor, 'the coverage gate records no calibration'
    measured, floor = float(measured.group(1)), float(floor.group(1))
    flag = re.search(r'--fail-under=([0-9.]+)', workflow)
    assert flag, workflow
    assert float(flag.group(1)) == floor, (flag.group(1), floor)
    assert floor < measured, (floor, measured)
    assert measured - floor <= 2.0, (
        f'the ratchet allows a {measured - floor:.1f} point regression')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciratchets_')


if __name__ == '__main__':
    raise SystemExit(main())
