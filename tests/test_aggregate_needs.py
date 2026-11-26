#!/usr/bin/env python3
"""A workflow's aggregate job depends on every job it must cover.

Branch protection names one required check, the aggregate job. A job
added beside it and left out of its `needs:` leaves branch protection
covering less than CI runs while CI stays green, so this gate asserts the
aggregate's `needs:` against the jobs the file declares, in both
directions, and pins the declared exemptions to the workflow itself.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo import ROOT  # noqa: E402
from _wffixtures import _probe_workflow  # noqa: E402
from _wfjobs import load, workflow_files  # noqa: E402
from _yamlscalar import YAMLReadError  # noqa: E402

AGGREGATE = 'aggregate'
_REAL = ROOT / '.github' / 'workflows' / 'tests.yml'

# What the real workflow is left with once the aggregate and its needs
# are removed: every job is covered.
EXEMPT = set()

NEEDS_BLOCK = '    needs:\n      - probe\n'
RUNNER = '    runs-on: ubuntu-latest\n    timeout-minutes: 5\n'
PROBE = '  probe:\n' + RUNNER


def test_the_real_aggregate_covers_exactly_its_jobs(tmp):
    """The gate's standing assertion over the shipped workflows."""
    del tmp
    violations = _aggregate_violations()
    assert not violations, '\n'.join(violations)


def test_the_declared_exemptions_are_the_jobs_left_over(tmp):
    """The declared list is two-sided: it must match what is real."""
    del tmp
    assert not _exemption_drift(load(_REAL)), _exemption_drift(load(_REAL))


def test_the_control_reads_the_shape_off_a_literal_oracle(tmp):
    """The shape oracle is a literal set, read off a fixture by hand.

    A `descendants` recomputed with the helper it judges would agree with
    whatever that helper returned. `direct` pins the listed half of the
    rule, `mid` and `leaf` the needs-closure half, `inner` and `outer` the
    issue's downstream-only jobs, absent however far their needs reach.
    """
    source = ('jobs:\n'
              '  aggregate:\n    needs:\n'
              '      - direct\n      - suites\n'
              + RUNNER
              + '  direct:\n'
              + RUNNER
              + '  suites:\n    needs:\n      - mid\n'
              + RUNNER
              + '  mid:\n    needs:\n      - leaf\n'
              + RUNNER
              + '  leaf:\n'
              + RUNNER
              + '  inner:\n    needs: aggregate\n'
              + RUNNER
              + '  outer:\n    needs:\n      - inner\n'
              + RUNNER)
    root = _probe_workflow(tmp, 'shape-oracle', source)
    workflow = load(root / 'probe.yml')
    actual = _descendants(_needs_of(workflow.jobs))
    assert actual == {'direct', 'suites', 'mid', 'leaf'}, sorted(actual)


def test_a_job_that_only_reaches_the_aggregate_downstream_is_named(tmp):
    """The issue's repro: reaching the aggregate is not being covered."""
    source = ('jobs:\n'
              '  aggregate:\n' + NEEDS_BLOCK + RUNNER + PROBE
              + '  inner:\n    needs: aggregate\n'
              + RUNNER
              + '  outer:\n    needs:\n      - inner\n'
              + RUNNER)
    violations = _scan_fixture(tmp, 'issue-shape', source)
    assert len(violations) == 1, violations
    assert "'inner'" in violations[0], violations
    assert "'outer'" in violations[0], violations


def test_both_violation_classes_are_reported_for_one_aggregate(tmp):
    """One run names every gap, so fixing one cannot hide the other."""
    source = ('jobs:\n'
              '  aggregate:\n'
              '    needs:\n'
              '      - absent\n' + RUNNER + PROBE
              + '  stray:\n' + RUNNER)
    violations = _scan_fixture(tmp, 'dual-gap', source)
    assert len(violations) == 2, violations
    joined = '\n'.join(violations)
    assert "'absent'" in joined and 'do not exist' in joined, violations
    assert "'stray'" in joined and 'does not cover' in joined, violations


def test_the_comparison_is_step_name_agnostic(tmp):
    """Steps are never consulted, so their spellings cannot move the set.

    A duplicated `Check dependency results` step, with its fields in either
    order, is held beside a genuine coverage gap to show the two do not
    interact.
    """
    steps = ('    steps:\n'
             '      - name: Check dependency results\n'
             '        run: echo aggregate\n'
             '      - run: echo again\n'
             '        name: Check dependency results\n')
    complete = ('jobs:\n'
                '  aggregate:\n' + NEEDS_BLOCK + RUNNER
                + steps + PROBE)
    assert not _scan_fixture(tmp, 'steps-complete', complete)
    gap = complete.replace('    needs:\n      - probe\n', '    needs: []\n')
    violations = _scan_fixture(tmp, 'steps-gap', gap)
    assert len(violations) == 1, violations
    assert "'probe'" in violations[0], violations


def _scan_fixture(tmp, name, source):
    return _aggregate_violations(_probe_workflow(tmp, name, source))


def test_an_explicit_key_jobs_form_is_refused_not_passed(tmp):
    """The fail-open shape a real branch shipped: refusal, never silence."""
    source = ('? jobs\n'
              ':\n'
              '  aggregate:\n'
              + NEEDS_BLOCK + RUNNER + PROBE)
    violations = _scan_fixture(tmp, 'explicit-key', source)
    assert violations, 'an explicit key form was accepted silently'


def test_a_job_downstream_of_the_aggregate_is_named(tmp):
    """Waiting on the aggregate is not being covered by it."""
    source = ('jobs:\n'
              '  aggregate:\n' + NEEDS_BLOCK + RUNNER + PROBE
              + '  downstream:\n    needs: aggregate\n'
              + RUNNER)
    violations = _scan_fixture(tmp, 'descendant', source)
    assert len(violations) == 1, violations
    named = [line for line in violations if "'downstream'" in line]
    assert len(named) == 1, violations
    assert 'does not cover' in named[0], named


def _aggregate_violations(directory=None):
    """One message per job the aggregate does not cover, or unknown need."""
    violations = []
    for path in workflow_files(directory):
        try:
            workflow = load(path)
            if AGGREGATE not in workflow.jobs:
                continue
            violations.extend(_gaps(workflow))
        except YAMLReadError as error:
            violations.append(str(error))
    return violations


def _gaps(workflow):
    """Every gap in one aggregate job's coverage, not only the first."""
    needs = _needs_of(workflow.jobs)
    unknown = sorted(needs[AGGREGATE] - set(workflow.jobs))
    uncovered = sorted(
        set(workflow.jobs) - {AGGREGATE} - _descendants(needs) - EXEMPT)
    violations = []
    if unknown:
        violations.append(f'{_where(workflow)}: needs names jobs that do '
                          f'not exist: {unknown}')
    if uncovered:
        violations.append(f'{_where(workflow)}: jobs the aggregate does not '
                          f'cover: {uncovered}')
    return violations


def _exemption_drift(workflow):
    """What the declared exemptions are against one workflow's reality."""
    needs = _needs_of(workflow.jobs)
    leftover = set(workflow.jobs) - {AGGREGATE} - _descendants(needs)
    if leftover == EXEMPT:
        return []
    return [f'{_where(workflow)}: the declared exemptions '
            f'{sorted(EXEMPT)} are not the jobs left over '
            f'{sorted(leftover)}']


def _needs_of(jobs):
    """Every job's needs as a set, refusing a shape that is not names."""
    needs = {}
    for name, job in jobs.items():
        value = job.get('needs', [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(
                isinstance(entry, str) for entry in value):
            raise YAMLReadError(f'job {name!r} needs is not a list of names')
        needs[name] = set(value)
    return needs


def _descendants(needs):
    """Jobs the aggregate covers: those it lists and their needs-closure."""
    covered = set(needs[AGGREGATE])
    for name in needs[AGGREGATE]:
        covered |= _closure(needs, name)
    return covered


def _closure(needs, name):
    """Every job one job reaches through its transitive needs."""
    seen = set()
    pending = list(needs.get(name, ()))
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(needs.get(current, ()))
    return seen


def _where(workflow):
    """The file, line and literal header text of the aggregate job."""
    line, text = workflow.starts[AGGREGATE]
    return f'{workflow.path.name}:{line}: {text!r}'


def test_a_non_utf8_workflow_is_refused_not_raised(tmp):
    """Bytes the tree does not ship are a refusal, never a crash."""
    root = Path(tmp) / 'workflows'
    root.mkdir()
    (root / 'binary.yml').write_bytes(b'jobs:\n  probe:\n    runs-on: \xff\n')
    violations = _aggregate_violations(root)
    named = [line for line in violations if 'binary.yml' in line]
    assert len(named) == 1, violations
    assert 'UTF-8' in named[0], named
