#!/usr/bin/env python3
"""The gating-workflow predicate both waiters read, on its own.

`ci_wait.py` refuses with exit 4 on a run set with no gating workflow and
`watch_all.py` keeps its hold on one; both ask ci_gate, so the controls that
hold the predicate in place live beside the predicate rather than in either
caller. A caller can only be as right as the thing it asks, and both of them
would be wrong together if this were a copy.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

SOURCE = (_util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
          / 'ci_gate.py')


def _ci_gate():
    return _util.load(SOURCE, 'ci_gate_contract')


def _gate_run(name, conclusion='success'):
    """One workflow run, as both waiters read it."""
    return {'name': name, 'status': 'completed', 'conclusion': conclusion}


def test_a_gating_run_is_present(tmp):
    del tmp
    mod = _ci_gate()
    runs = [_gate_run('gate freshness'), _gate_run('tests')]
    assert mod.missing_required(runs) == []
    assert mod.missing_required([_gate_run('tests')]) == []


def test_no_gating_run_names_what_is_missing(tmp):
    del tmp
    mod = _ci_gate()
    runs = [_gate_run('gate freshness'), _gate_run('CodeQL - Code Quality')]
    assert mod.missing_required(runs) == ['tests']
    assert mod.missing_required([]) == ['tests']


def test_the_name_matches_exactly(tmp):
    """`Tests` and `test` are different workflows. A case difference or a
    decorated spelling treated as the gate would reinstate the false green
    this predicate exists to remove, under a new spelling."""
    del tmp
    mod = _ci_gate()
    for name in ('Tests', 'test', 'TESTS', 'tests ', 'unit tests'):
        assert mod.missing_required([_gate_run(name)]) == ['tests'], name


def test_a_conclusion_is_irrelevant_to_the_predicate(tmp):
    """Both callers judge conclusions against rules of their own, and this
    one must not pre-empt them: a red gating run is present, so it is their
    answer to report, not an absence to wait on."""
    del tmp
    mod = _ci_gate()
    for conclusion in ('success', 'failure', 'cancelled', 'skipped'):
        assert mod.missing_required([_gate_run('tests', conclusion)]) == [], (
            conclusion)


def test_a_superset_requirement_names_each_missing_workflow(tmp):
    del tmp
    mod = _ci_gate()
    wanted = frozenset({'tests', 'audit'})
    assert mod.missing_required([_gate_run('tests')], required=wanted) == ['audit']
    assert mod.missing_required(
        [_gate_run('tests'), _gate_run('audit')], required=wanted) == []
    assert mod.missing_required([], required=wanted) == ['audit', 'tests']


def test_an_empty_requirement_is_satisfied_by_every_run_set(tmp):
    """The argument is a no-op, not a rule that refuses everything - which
    is what makes it usable as the default `verdict()` carries."""
    del tmp
    mod = _ci_gate()
    assert mod.missing_required([]) == ['tests']
    assert mod.missing_required([], required=frozenset()) == []
    assert mod.missing_required([_gate_run('gate freshness')],
                                required=frozenset()) == []


def test_both_waiters_read_this_one_predicate(tmp):
    """The extraction, not a reimplementation: both callers hold the SAME
    module object, so renaming or retargeting the gate cannot leave one of
    them answering for the old name. Equality carries the constant (two
    separately built frozensets are the same value and not the same
    object); identity carries the predicate, which is what "one mechanism"
    means."""
    del tmp
    skill = _util.ROOT / '.claude' / 'skills' / 'changing-daedalus'
    mod = _ci_gate()
    wait = _util.load(skill / 'ci_wait.py', 'ci_wait_gate_owner')
    hold = _util.load(skill / 'watch_all.py', 'watch_all_gate_owner')
    assert wait.ci_gate is hold.ci_gate
    assert wait.ci_gate.missing_required is hold.ci_gate.missing_required
    assert wait.REQUIRED_WORKFLOWS == mod.REQUIRED_WORKFLOWS
    assert hold.ci_gate.REQUIRED_WORKFLOWS == mod.REQUIRED_WORKFLOWS
    assert wait.ci_gate.REQUIRED_WORKFLOWS == mod.REQUIRED_WORKFLOWS


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='cigate_')


if __name__ == '__main__':
    raise SystemExit(main())
