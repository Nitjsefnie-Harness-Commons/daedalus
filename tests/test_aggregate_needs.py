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
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfjobs import load, workflow_files  # noqa: E402
from _yamlscalar import YAMLReadError  # noqa: E402

AGGREGATE = 'aggregate'
_REAL = ROOT / '.github' / 'workflows' / 'tests.yml'

# Declared once, and asserted two ways: this is exactly what the real
# workflow is left with once the aggregate, its needs and its descendants
# are removed, and the workflow's own comment still says why.
EXEMPT = {'diff-coverage'}
_DOCUMENTED = ('INFORMATIONAL', 'deliberately absent')


def test_the_real_aggregate_covers_exactly_its_jobs(tmp):
    """The gate's standing assertion over the shipped workflows."""
    del tmp
    violations = _aggregate_violations()
    assert not violations, '\n'.join(violations)


def test_the_declared_exemptions_are_the_jobs_left_over(tmp):
    """The declared list is two-sided: it must match what is real."""
    del tmp
    assert not _exemption_drift(load(_REAL)), _exemption_drift(load(_REAL))


def test_the_real_aggregate_names_ten_jobs_and_two_descendants(tmp):
    """Today's shape, pinned so a change is a decision and not a drift."""
    del tmp
    workflow = load(_REAL)
    needs = _needs_of(workflow.jobs)
    assert len(needs[AGGREGATE]) == 10, sorted(needs[AGGREGATE])
    assert _descendants(needs) == {'timed', 'speed'}, _descendants(needs)
    assert EXEMPT == {'diff-coverage'}, EXEMPT


def test_the_exemption_is_still_documented_in_the_workflow(tmp):
    """The workflow's own comment must still carry the exemption's why."""
    del tmp
    comment = _comment_block(_REAL.read_text(encoding='utf-8'))
    for phrase in _DOCUMENTED:
        assert phrase in comment, (phrase, comment)


def test_an_extra_job_outside_the_aggregate_is_named(tmp):
    """The issue's own repro: a second job left out of `needs`."""
    source = ('jobs:\n'
              '  aggregate:\n'
              '    needs:\n'
              '      - probe\n'
              '    runs-on: ubuntu-latest\n'
              '    timeout-minutes: 5\n'
              '  probe:\n'
              '    runs-on: ubuntu-latest\n'
              '    timeout-minutes: 5\n'
              '  late:\n'
              '    runs-on: ubuntu-latest\n'
              '    timeout-minutes: 5\n')
    violations = _scan_fixture(tmp, 'late-job', source)
    assert len(violations) == 1, violations
    assert "'late'" in violations[0], violations


def test_a_need_that_names_no_declared_job_is_refused(tmp):
    source = ('jobs:\n'
              '  aggregate:\n'
              '    needs:\n'
              '      - absent\n'
              '    runs-on: ubuntu-latest\n'
              '    timeout-minutes: 5\n'
              '  probe:\n'
              '    runs-on: ubuntu-latest\n'
              '    timeout-minutes: 5\n')
    violations = _scan_fixture(tmp, 'absent-need', source)
    assert len(violations) == 1, violations
    assert "'absent'" in violations[0], violations


def test_an_exemption_removed_from_the_workflow_is_a_drift(tmp):
    """Reality changed under the constant, so the equality says so."""
    root = _copy_real(tmp)
    source = _REAL.read_text(encoding='utf-8')
    start = source.index('  diff-coverage:')
    end = source.index('  timed:')
    (root / 'tests.yml').write_text(
        source[:start] + source[end:], encoding='utf-8')
    workflow = load(root / 'tests.yml')
    drift = _exemption_drift(workflow)
    assert len(drift) == 1, drift
    assert 'diff-coverage' in drift[0], drift


def test_an_undocumented_exemption_is_a_drift(tmp):
    """The comment is half of the exemption: gone, the gate says so."""
    root = _copy_real(tmp)
    source = _REAL.read_text(encoding='utf-8')
    (root / 'tests.yml').write_text(
        source.replace(
            '    # INFORMATIONAL, never a gate — which is why it is'
            ' deliberately absent\n', '    # not a gate\n'),
        encoding='utf-8')
    comment = _comment_block((root / 'tests.yml').read_text(encoding='utf-8'))
    for phrase in _DOCUMENTED:
        assert phrase not in comment, (phrase, comment)


NEEDS_BLOCK = '    needs:\n      - probe\n'
RUNNER = '    runs-on: ubuntu-latest\n    timeout-minutes: 5\n'
PROBE = '  probe:\n' + RUNNER


def _fixture(tmp, name, source):
    """Write one fixture workflow into a fresh workflows directory."""
    root = Path(tmp) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / 'probe.yml').write_text(source, encoding='utf-8')
    return root


def _scan_fixture(tmp, name, source):
    return _aggregate_violations(_fixture(tmp, name, source))


PASS_CASES = {
    'spaced-jobs-key': 'jobs :\n' + '  aggregate:\n' + NEEDS_BLOCK + RUNNER
                       + PROBE,
    'quoted-jobs-key': '"jobs":\n' + '  aggregate:\n' + NEEDS_BLOCK + RUNNER
                       + PROBE,
    'flow-needs-trailing-comma': (
        'jobs:\n  aggregate:\n    needs: [probe, ]\n' + RUNNER + PROBE),
    'flow-needs-quoted-ids': (
        'jobs:\n  aggregate:\n    needs: ["probe"]\n' + RUNNER + PROBE),
    'block-needs-quoted-entry': (
        'jobs:\n  aggregate:\n    needs:\n      - "probe"\n'
        + RUNNER + PROBE),
    'indentless-needs': (
        'jobs:\n  aggregate:\n    needs:\n    - probe\n' + RUNNER + PROBE),
    'scalar-needs': 'jobs:\n  aggregate:\n    needs: probe\n' + RUNNER
                    + PROBE,
    'unusual-field-keys': (
        'jobs:\n  aggregate:\n    needs  : [probe]  # the cells\n'
        + RUNNER + PROBE),
    'flow-env-mapping': (
        'jobs:\n  aggregate:\n' + NEEDS_BLOCK + RUNNER
        + '    env: {NEEDS_JSON: from-context}\n' + PROBE),
    'step-name-not-first': (
        'jobs:\n  aggregate:\n' + NEEDS_BLOCK + RUNNER
        + '    steps:\n'
          '      - run: echo aggregate\n'
          '        name: Check dependency results\n' + PROBE),
    'explicit-block-indent': (
        'jobs:\n  aggregate:\n' + NEEDS_BLOCK + RUNNER
        + '    steps:\n'
          '      - name: script\n'
          "        run: |2-\n"
          "            python3 - <<'PY'\n"
          '            print("aggregate")\n'
          '            PY\n' + PROBE),
    'deeper-literal-block': (
        'jobs:\n  aggregate:\n' + NEEDS_BLOCK + RUNNER
        + '    steps:\n'
          '      - name: script\n'
          '        run: |\n'
          "              python3 - <<'PY'\n"
          '              print("aggregate")\n'
          '              PY\n' + PROBE),
    'flow-mapped-probe': (
        'jobs:\n  aggregate:\n' + NEEDS_BLOCK + RUNNER
        + '  probe: {runs-on: ubuntu-latest, timeout-minutes: 5}\n'),
}


def test_every_valid_spelling_of_a_complete_aggregate_passes(tmp):
    """A valid workflow the reader can classify is never a false failure."""
    for name, source in sorted(PASS_CASES.items()):
        violations = _scan_fixture(tmp, name, source)
        assert not violations, f'{name}: {violations}'


def test_an_explicit_key_jobs_form_is_refused_not_passed(tmp):
    """The fail-open shape a real branch shipped: refusal, never silence."""
    source = ('? jobs\n'
              ':\n'
              '  aggregate:\n'
              + NEEDS_BLOCK + RUNNER + PROBE)
    violations = _scan_fixture(tmp, 'explicit-key', source)
    assert violations, 'an explicit key form was accepted silently'


def test_a_descendant_needs_no_entry_of_its_own(tmp):
    """A job downstream of the aggregate is covered through it."""
    source = ('jobs:\n'
              '  aggregate:\n'
              '    needs:\n'
              '      - probe\n'
              '    runs-on: ubuntu-latest\n'
              '    timeout-minutes: 5\n'
              '  probe:\n'
              '    runs-on: ubuntu-latest\n'
              '    timeout-minutes: 5\n'
              '  timed:\n'
              '    needs: aggregate\n'
              '    runs-on: ubuntu-latest\n'
              '    timeout-minutes: 5\n')
    violations = _scan_fixture(tmp, 'descendant', source)
    assert not violations, violations


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
    """Check one aggregate job's needs against the jobs around it."""
    needs = _needs_of(workflow.jobs)
    aggregate_needs = needs[AGGREGATE]
    unknown = sorted(aggregate_needs - set(workflow.jobs))
    if unknown:
        return [f'{_where(workflow)}: needs names jobs that do not exist: '
                f'{unknown}']
    descendants = _descendants(needs)
    uncovered = sorted(
        set(workflow.jobs) - aggregate_needs - {AGGREGATE} - descendants
        - EXEMPT)
    if uncovered:
        return [f'{_where(workflow)}: jobs the aggregate does not cover: '
                f'{uncovered}']
    return []


def _exemption_drift(workflow):
    """What the declared exemptions are against one workflow's reality."""
    needs = _needs_of(workflow.jobs)
    leftover = set(workflow.jobs) - needs[AGGREGATE] - {AGGREGATE} \
        - _descendants(needs)
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
    """Every job whose transitive needs closure reaches the aggregate."""
    return {name for name in needs if AGGREGATE in _closure(needs, name)}


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


def _comment_block(source):
    """The comment lines that head the exempt job's own section."""
    lines = source.splitlines()
    start = next(index for index, line in enumerate(lines)
                 if line.rstrip() == '  diff-coverage:')
    end = next((index for index in range(start + 1, len(lines))
                if lines[index].strip()
                and not lines[index].lstrip(' ').startswith('#')
                and len(lines[index]) - len(lines[index].lstrip(' ')) == 2),
               len(lines))
    return '\n'.join(lines[start:end])


def _copy_real(tmp):
    """The real workflow, copied so a mutation cannot touch the tree."""
    root = Path(tmp) / '.github' / 'workflows'
    root.mkdir(parents=True, exist_ok=True)
    (root / 'tests.yml').write_text(
        _REAL.read_text(encoding='utf-8'), encoding='utf-8')
    return root


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
