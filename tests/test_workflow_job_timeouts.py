#!/usr/bin/env python3
"""Every runner job of every workflow declares an effective timeout.

A job added without `timeout-minutes` inherits GitHub's 360-minute
default, so an unbounded job is a four-hour ceiling nobody chose. This
gate classifies every job of both workflow extensions, refuses a
construct it cannot classify, and refuses a workflow that classifies no
job at all, rather than reporting success over nothing. A job with
neither `runs-on` nor a job-level `uses` is not a runner job and is not
asked for a bound; such a job is invalid, and the repository's pinned
actionlint is the gate that refuses it.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfjobs import load, workflow_files  # noqa: E402
from _yamlscalar import YAMLReadError  # noqa: E402

_REAL = ROOT / '.github' / 'workflows' / 'tests.yml'
_NUMBER = re.compile(
    r'\+?(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9_]+)?'
    r'\Z')


def test_every_real_runner_job_declares_a_timeout(tmp):
    """The gate's standing assertion over the shipped workflows."""
    del tmp
    violations = _timeout_violations()
    assert not violations, '\n'.join(violations)


def test_the_real_workflows_classify_every_job(tmp):
    """All nine shipped workflows decode, each naming its runner jobs."""
    del tmp
    for path in workflow_files():
        jobs = load(path).jobs
        assert jobs, path
        assert all(isinstance(job, dict) for job in jobs.values()), path


def test_the_gate_refuses_a_directory_with_no_workflow_files(tmp):
    """A scan over nothing is a refusal, never a pass."""
    empty = Path(tmp) / 'workflows'
    empty.mkdir()
    try:
        _timeout_violations(empty)
    except YAMLReadError as error:
        assert 'no workflow files' in str(error), str(error)
        return
    raise AssertionError('a directory with no workflow files was accepted')


def test_a_planted_unbounded_job_is_named_in_the_real_workflow(tmp):
    """Planting the defect in a copy of the real workflow names the job."""
    violations = _timeout_violations(_planted(tmp))
    assert len(violations) == 1, violations
    assert 'aggregate' in violations[0], violations
    assert 'tests.yml:' in violations[0], violations
    assert load(_REAL).jobs['aggregate']['timeout-minutes'] == '5'


def test_a_planted_unbounded_job_in_a_second_extension_is_named(tmp):
    """Discovery covers `.yaml`, not only the `.yml` the tree spells."""
    root = _planted(tmp)
    (root / 'probe.yaml').write_text(
        'jobs:\n'
        '  probe:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - run: echo probe\n', encoding='utf-8')
    named = [line for line in _timeout_violations(root)
             if 'probe.yaml' in line]
    assert len(named) == 1, named
    assert 'job \'probe\'' in named[0], named


BOUNDED_JOB = (
    '  probe:\n'
    '    runs-on: ubuntu-latest\n'
    '    timeout-minutes: 5\n'
    '    steps:\n'
    '      - run: echo hi\n')


def _fixture(tmp, name, source):
    """Write one fixture workflow into a fresh workflows directory."""
    root = Path(tmp) / name
    root.mkdir(parents=True, exist_ok=True)
    (root / 'probe.yml').write_text(source, encoding='utf-8')
    return root


def test_each_valid_spelling_that_bounds_a_job_passes(tmp):
    """The bound is found however valid YAML spells the job."""
    cases = {
        'jobs-first': 'jobs:\n' + BOUNDED_JOB,
        'quoted-id': 'jobs:\n' + BOUNDED_JOB.replace(
            '  probe:', '  "probe":'),
        'comment-header': 'jobs:\n' + BOUNDED_JOB.replace(
            '  probe:', '  probe: # manual probe'),
        'flow-mapping': (
            'jobs: {probe: {runs-on: ubuntu-latest, timeout-minutes: 5,'
            ' steps: [{run: echo hi}]}}\n'),
        'four-space': 'jobs:\n' + BOUNDED_JOB.replace('  ', '    '),
        'after-another-key': 'name: x\non: push\njobs:\n' + BOUNDED_JOB,
        'defaults-after': 'jobs:\n' + BOUNDED_JOB
                          + 'defaults:\n  run:\n    shell: bash\n',
        'snapshot-job': 'jobs:\n'
                        '  probe:\n'
                        '    snapshot:\n'
                        '      always: true\n'
                        '    steps:\n'
                        '      - run: echo hi\n',
        'needs-only-job': 'jobs:\n'
                          '  probe:\n'
                          '    needs: other\n'
                          '  other:\n'
                          '    runs-on: ubuntu-latest\n'
                          '    timeout-minutes: 5\n',
        'indentless-needs': 'jobs:\n'
                            '  probe:\n'
                            '    needs:\n'
                            '    - other\n'
                            '    runs-on: ubuntu-latest\n'
                            '    timeout-minutes: 5\n'
                            '  other:\n'
                            '    runs-on: ubuntu-latest\n'
                            '    timeout-minutes: 5\n',
        'fractional-bound': 'jobs:\n' + BOUNDED_JOB.replace(
            'timeout-minutes: 5', 'timeout-minutes: 0.5'),
        'float-bound': 'jobs:\n' + BOUNDED_JOB.replace(
            'timeout-minutes: 5', 'timeout-minutes: 4.5'),
        'plus-signed-bound': 'jobs:\n' + BOUNDED_JOB.replace(
            'timeout-minutes: 5', 'timeout-minutes: +5'),
    }
    for name, source in sorted(cases.items()):
        violations = _timeout_violations(_fixture(tmp, name, source))
        assert not violations, f'{name}: {violations}'


def test_each_unbounded_shape_is_named(tmp):
    """Every shape that fails to bound a job is a named violation."""
    cases = {
        'no-bound': 'jobs:\n'
                    '  probe:\n'
                    '    runs-on: ubuntu-latest\n'
                    '    steps:\n'
                    '      - run: echo probe\n',
        'quoted-id-unbounded': 'jobs:\n'
                               '  "probe":\n'
                               '    runs-on: ubuntu-latest\n',
        'comment-header-unbounded': 'jobs:\n'
                                    '  probe: # comment\n'
                                    '    runs-on: ubuntu-latest\n',
        'anchor-and-alias': 'jobs:\n'
                            '  probe: &probe\n'
                            '    runs-on: ubuntu-latest\n'
                            '  clone: *probe\n',
        'four-space-unbounded': 'jobs:\n'
                                '    probe:\n'
                                '        runs-on: ubuntu-latest\n',
        'flow-mapping-unbounded': 'jobs: {probe: {runs-on: ubuntu-latest}}\n',
        'step-level-only': 'jobs:\n'
                           '  probe:\n'
                           '    runs-on: ubuntu-latest\n'
                           '    steps:\n'
                           '      - run: echo probe\n'
                           '        timeout-minutes: 5\n',
        'expression-value': 'jobs:\n'
                            '  probe:\n'
                            '    runs-on: ubuntu-latest\n'
                            '    timeout-minutes: ${{ matrix.timeout }}\n',
        'indentless-needs-unbounded': 'jobs:\n'
                                      '  probe:\n'
                                      '    needs:\n'
                                      '    - other\n'
                                      '    runs-on: ubuntu-latest\n'
                                      '  other:\n'
                                      '    runs-on: ubuntu-latest\n'
                                      '    timeout-minutes: 5\n',
        'zero-value': 'jobs:\n'
                      '  probe:\n'
                      '    runs-on: ubuntu-latest\n'
                      '    timeout-minutes: 0\n',
        'negative-value': 'jobs:\n'
                          '  probe:\n'
                          '    runs-on: ubuntu-latest\n'
                          '    timeout-minutes: -5\n',
    }
    for name, source in sorted(cases.items()):
        violations = _timeout_violations(_fixture(tmp, name, source))
        assert len(violations) == 1, f'{name}: {violations}'
        assert 'probe' in violations[0], f'{name}: {violations}'


def test_a_local_caller_is_checked_through_its_target(tmp):
    """The bound is read from the called workflow, not the caller."""
    root = _planted(tmp)
    (root / 'caller.yml').write_text(
        'jobs:\n'
        '  call:\n'
        '    uses: ./.github/workflows/tests.yml\n', encoding='utf-8')
    violations = _timeout_violations(root)
    named = [line for line in violations if 'aggregate' in line]
    assert len(named) == 1, violations
    assert 'tests.yml:' in named[0], named


def test_a_quoted_local_caller_target_is_not_read_as_external(tmp):
    """The quoted target decodes to a local path, not an external one."""
    root = _planted(tmp)
    (root / 'caller.yml').write_text(
        'jobs:\n'
        '  call:\n'
        '    uses: "./.github/workflows/tests.yml"\n', encoding='utf-8')
    violations = _timeout_violations(root)
    assert not [line for line in violations if 'caller.yml' in line], (
        violations)
    assert not any('cannot be verified' in line for line in violations), (
        violations)


def test_a_defective_caller_still_hands_its_target_to_the_gate(tmp):
    """Recording the caller's own violation does not end the walk.

    The target is spelled with a name discovery does not pick up, so the
    through-caller walk is the only route by which it can be reached.
    """
    root = _planted(tmp)
    (root / 'caller.yml').write_text(
        'jobs:\n'
        '  call:\n'
        '    uses: ./.github/workflows/target.txt\n'
        '    timeout-minutes: 5\n', encoding='utf-8')
    (root / 'target.txt').write_text(
        'jobs:\n'
        '  inner:\n'
        '    runs-on: ubuntu-latest\n', encoding='utf-8')
    violations = _timeout_violations(root)
    assert any('caller.yml' in line for line in violations), violations
    named = [line for line in violations if 'target.txt' in line]
    assert len(named) == 1, violations
    assert "'inner'" in named[0], named


def test_a_caller_cannot_carry_its_own_bound(tmp):
    """Requiring a bound on a caller makes the workflow invalid."""
    root = _planted(tmp)
    (root / 'caller.yml').write_text(
        'jobs:\n'
        '  call:\n'
        '    uses: ./.github/workflows/tests.yml\n'
        '    timeout-minutes: 5\n', encoding='utf-8')
    violations = _timeout_violations(root)
    named = [line for line in violations if 'caller.yml' in line]
    assert len(named) == 1, violations
    assert 'timeout-minutes' in named[0], named


def test_an_external_caller_target_is_refused(tmp):
    """A target this repository does not ship cannot be verified."""
    root = _planted(tmp)
    (root / 'caller.yml').write_text(
        'jobs:\n'
        '  call:\n'
        '    uses: owner/repo/.github/workflows/x.yml@main\n',
        encoding='utf-8')
    violations = _timeout_violations(root)
    assert any('owner/repo' in line for line in violations), violations


def test_an_external_caller_in_an_inline_job_is_refused(tmp):
    """The issue's own inline spelling of the same external caller."""
    root = _planted(tmp)
    (root / 'flow.yml').write_text(
        'jobs: {call: {uses:'
        ' owner/repo/.github/workflows/x.yml@main}}\n', encoding='utf-8')
    violations = _timeout_violations(root)
    named = [line for line in violations if 'flow.yml' in line]
    assert len(named) == 1, violations
    assert 'owner/repo' in named[0], named


def test_a_non_utf8_workflow_is_refused_not_raised(tmp):
    """Bytes this repository does not ship are a refusal, not a crash."""
    root = _planted(tmp)
    (root / 'binary.yml').write_bytes(
        b'jobs:\n  probe:\n    runs-on: ubuntu\xff-latest\n')
    violations = _timeout_violations(root)
    named = [line for line in violations if 'binary.yml' in line]
    assert len(named) == 1, violations
    assert 'UTF-8' in named[0], named


def test_a_local_target_the_repository_does_not_ship_is_refused(tmp):
    root = _planted(tmp)
    (root / 'caller.yml').write_text(
        'jobs:\n'
        '  call:\n'
        '    uses: ./.github/workflows/absent.yml\n', encoding='utf-8')
    violations = _timeout_violations(root)
    assert any('absent.yml' in line for line in violations), violations


def test_a_cyclic_pair_of_local_callers_is_checked_once(tmp):
    """Recursion follows local callers without following it forever."""
    root = _planted(tmp)
    (root / 'caller.yml').write_text(
        'jobs:\n'
        '  call:\n'
        '    uses: ./.github/workflows/other.yml\n', encoding='utf-8')
    (root / 'other.yml').write_text(
        'jobs:\n'
        '  back:\n'
        '    uses: ./.github/workflows/caller.yml\n', encoding='utf-8')
    violations = _timeout_violations(root)
    assert not [line for line in violations if '.yml' in line
                and 'tests.yml' not in line], violations


def test_an_unclassifiable_workflow_is_refused_not_passed(tmp):
    """A construct the reader cannot classify fails the gate loudly."""
    root = _planted(tmp)
    (root / 'refusal.yml').write_text(
        'jobs:\n'
        '  probe: &probe\n'
        '    runs-on: ubuntu-latest\n'
        '    timeout-minutes: 5\n', encoding='utf-8')
    violations = _timeout_violations(root)
    named = [line for line in violations if 'refusal.yml' in line]
    assert len(named) == 1, violations
    assert 'unsupported plain scalar' in named[0], named


def _planted(tmp):
    """Copy the real workflow, minus the aggregate job's bound, into `tmp`."""
    root = Path(tmp) / '.github' / 'workflows'
    root.mkdir(parents=True, exist_ok=True)
    source = _REAL.read_text(encoding='utf-8')
    section = source[source.index('  aggregate:\n'):]
    bound = '\n    timeout-minutes: 5\n'
    assert section.count(bound) == 1, 'the planted target moved'
    (root / 'tests.yml').write_text(
        source[:source.index('  aggregate:\n')]
        + section.replace(bound, '\n', 1), encoding='utf-8')
    return root


def _timeout_violations(directory=None):
    """Return one message per unbounded job or unclassifiable workflow.

    A reusable-workflow caller is exempt from carrying a bound and is
    followed into a local target, so an unbounded job cannot hide behind
    one; an external target is refused, because it cannot be verified
    from here.
    """
    violations = []
    checked = set()
    for path in workflow_files(directory):
        _scan(path, violations, checked)
    return violations


def _scan(path, violations, checked):
    """Check one workflow, and through a local caller its target."""
    if path in checked:
        return
    checked.add(path)
    try:
        workflow = load(path)
    except YAMLReadError as error:
        violations.append(str(error))
        return
    for name, job in workflow.jobs.items():
        target = _local_target(workflow, name, job, violations)
        problem = _unbounded(workflow, name, job)
        if problem is not None:
            violations.append(problem)
        if target is not None:
            _scan(target, violations, checked)


def _unbounded(workflow, name, job):
    """Return the message for a job that fails to bound itself."""
    where = _where(workflow, name)
    runs_on = 'runs-on' in job
    uses = job.get('uses')
    bound = job.get('timeout-minutes')
    if uses is not None:
        if runs_on:
            return f'{where}: a job cannot be a caller and a runner'
        if bound is not None:
            return f'{where}: a caller cannot carry timeout-minutes'
        return None
    if not runs_on:
        return None
    if bound is None:
        return f'{where}: job {name!r} declares no timeout-minutes'
    return _positive_literal(bound, where)


def _local_target(workflow, name, job, violations):
    """Resolve a local caller's target, refusing one that cannot be."""
    uses = job.get('uses')
    if uses is None:
        return None
    where = _where(workflow, name)
    if not isinstance(uses, str) or not uses.startswith('./'):
        violations.append(
            f'{where}: caller target {uses!r} cannot be verified from '
            f'this repository')
        return None
    resolved = workflow.path.parent / Path(uses).name
    if not resolved.is_file():
        violations.append(
            f'{where}: local target {uses!r} is not a workflow this '
            f'repository ships')
        return None
    return resolved


def _positive_literal(bound, where):
    """Refuse a bound that is not a number greater than zero.

    The accepted spellings are the numeric literals the repository's
    pinned actionlint accepts for `timeout-minutes`, measured against it:
    a plain, fractional, plus-signed or underscore-grouped decimal with
    an optional exponent. What the gate cannot read as a number from the
    source text it refuses, naming the value.
    """
    if not isinstance(bound, str) or not _NUMBER.fullmatch(bound):
        return f'{where}: timeout-minutes {bound!r} does not bound the job'
    digits = re.sub(r'(?<=[0-9])_(?=[0-9])', '', bound.lstrip('+'))
    if float(digits) <= 0:
        return f'{where}: timeout-minutes {digits} is not greater than zero'
    return None


def _where(workflow, name):
    """The file, line and literal header text one job sits at."""
    line, text = workflow.starts[name]
    return f'{workflow.path.name}:{line}: {text!r}'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
