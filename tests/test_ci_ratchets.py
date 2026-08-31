#!/usr/bin/env python3
"""The coverage ratchet: it raises the floor, and only ever upward.

CI rewrites the gate in .github/workflows/tests.yml from what a run actually
measured, so these tests pin what that rewrite may do — never lower the floor,
never touch a file whose recorded measurement has already drifted from its
gate, and never leave behind a number the pinning test would reject.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))


_PYTHON_STEP = '      - name: Python coverage gate\n'
_JAVASCRIPT_STEP = '      - name: JavaScript coverage gate\n'
_RATCHET_WORKFLOW = """jobs:
  coverage:
    steps:
      - name: Python coverage gate
        # Python measured: 73.3
        # Python floor: 72
        run: python -m coverage report --fail-under=72 --precision=1
      - name: JavaScript coverage gate
        # JavaScript measured: 34.6
        # JavaScript floor: 33.1
        run: |
          python scripts/ci/js_coverage.py "$NODE_V8_COVERAGE" \\
            --xml javascript-coverage.xml --fail-under=33.1
"""


def _ratchet():
    return _util.load(ROOT / 'scripts' / 'ci' / 'ratchet.py', 'ratchet_mod')


def test_promoted_modules_import_by_package(tmp):
    """Shared workflow modules must import without a sys.path test shim."""
    del tmp
    imported = subprocess.run(
        [sys.executable, '-c',
         'import scripts.ci.workflow_yaml; import scripts.ci.ratchet'],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert imported.returncode == 0, (imported.stdout, imported.stderr)


def _assert_duplicate_refused(language, anchor, name):
    ratchet = _ratchet()
    step = (_PYTHON_STEP if language == 'python' else _JAVASCRIPT_STEP)
    assert _RATCHET_WORKFLOW.count(step) == 1, step
    duplicate = _RATCHET_WORKFLOW.replace(
        step, f'{step}{anchor}\n', 1)
    try:
        ratchet.update(duplicate, 90.0, language)
    except SystemExit as why:
        assert f'exactly one {name}' in str(why), why
    else:
        raise AssertionError(
            f'duplicate {language} {name} was accepted')


def _assert_python_gate_raised(workflow):
    ratchet = _ratchet()
    try:
        raised = ratchet.update(workflow, 80.0, 'python')
    except SystemExit as why:
        raise AssertionError(str(why)) from why
    assert raised is not None
    assert '# Python measured: 80.0' in raised, raised
    assert ratchet.read_calibration(raised, 'python') == (80.0, 78.5)
    return raised


def _assert_python_decoy_ignored(workflow):
    raised = _assert_python_gate_raised(workflow)
    assert raised.count('# Python measured: 73.3') == 1, raised
    assert raised.count('# Python measured: 80.0') == 1, raised


def _forged_python_step_payload():
    return (
        '      - name: Document the Python gate\n'
        '        run: |\n'
        "          cat <<'YAML'\n"
        '          - name: Python coverage gate\n'
        '            # Python measured: 73.3\n'
        '            # Python floor: 72\n'
        '            run: python -m coverage report '
        '--fail-under=72 --precision=1\n'
        '          YAML\n')


def test_python_duplicate_measured_marker_is_refused(tmp):
    """A decoy measured marker must not divert the Python rewrite."""
    del tmp
    _assert_duplicate_refused(
        'python', '        # Python measured: 73.3', 'measured marker')


def test_python_duplicate_floor_marker_is_refused(tmp):
    """A decoy floor marker must not divert the Python rewrite."""
    del tmp
    _assert_duplicate_refused(
        'python', '        # Python floor: 72', 'floor marker')


def test_python_duplicate_gate_flag_is_refused(tmp):
    """A decoy gate flag must not divert the Python rewrite."""
    del tmp
    _assert_duplicate_refused(
        'python',
        '        run: python -m coverage report '
        '--fail-under=72 --precision=1',
        'gate flag')


def test_javascript_duplicate_measured_marker_is_refused(tmp):
    """A decoy measured marker must not divert the JavaScript rewrite."""
    del tmp
    _assert_duplicate_refused(
        'javascript', '        # JavaScript measured: 34.6',
        'measured marker')


def test_javascript_duplicate_floor_marker_is_refused(tmp):
    """A decoy floor marker must not divert the JavaScript rewrite."""
    del tmp
    _assert_duplicate_refused(
        'javascript', '        # JavaScript floor: 33.1', 'floor marker')


def test_javascript_duplicate_gate_flag_is_refused(tmp):
    """A decoy gate flag must not divert the JavaScript rewrite."""
    del tmp
    _assert_duplicate_refused(
        'javascript',
        '            --xml javascript-coverage.xml --fail-under=33.1',
        'gate flag')


def test_unknown_language_guard_names_the_language(tmp):
    """The defensive helper guard must not expose a bare KeyError."""
    del tmp
    ratchet = _ratchet()
    try:
        ratchet._patterns('ruby')
    except SystemExit as why:
        assert str(why) == 'unknown coverage language: ruby', why
    else:
        raise AssertionError('an unknown coverage language was accepted')


def test_explicit_scalar_cannot_hide_the_real_gate_anchor(tmp):
    """A less-indented YAML comment remains the real step anchor."""
    del tmp
    ratchet = _ratchet()
    marker = _PYTHON_STEP + '        # Python measured: 73.3\n'
    replacement = (
        '    # Python measured: 73.3\n'
        + _PYTHON_STEP
        + '        env:\n'
        '          RATCHET_NOTE: |2\n'
        '           # Python measured: 73.3\n'
        '            scalar content\n')
    workflow = _RATCHET_WORKFLOW.replace(marker, replacement, 1)

    try:
        raised = ratchet.update(workflow, 80.0, 'python')
    except SystemExit as why:
        raise AssertionError(str(why)) from why
    assert raised is not None
    assert '    # Python measured: 73.3\n' in raised, raised
    assert '           # Python measured: 80.0\n' in raised, raised
    assert ratchet.read_calibration(raised, 'python') == (80.0, 78.5)


def test_comment_indentation_cannot_hide_the_real_gate_anchor(tmp):
    del tmp
    ratchet = _ratchet()
    marker = _PYTHON_STEP + '        # Python measured: 73.3\n'
    replacement = _PYTHON_STEP + '      # Python measured: 73.3\n'
    workflow = _RATCHET_WORKFLOW.replace(marker, replacement, 1)
    try:
        raised = ratchet.update(workflow, 80.0, 'python')
    except SystemExit as why:
        raise AssertionError(str(why)) from why
    assert raised is not None
    assert '      # Python measured: 80.0\n' in raised, raised
    assert ratchet.read_calibration(raised, 'python') == (80.0, 78.5)


def test_anchor_decoy_in_another_job_is_invisible(tmp):
    del tmp
    workflow = _RATCHET_WORKFLOW.replace(
        '  coverage:\n',
        '  decoy:\n'
        '    # Python measured: 73.3\n'
        '    steps: []\n'
        '  coverage:\n', 1)
    _assert_python_decoy_ignored(workflow)


def test_anchor_decoy_in_another_step_is_invisible(tmp):
    del tmp
    decoy = (
        '      - name: Decoy\n'
        '        # Python measured: 73.3\n'
        '        run: "true"\n')
    workflow = _RATCHET_WORKFLOW.replace(
        _PYTHON_STEP, decoy + _PYTHON_STEP, 1)
    _assert_python_decoy_ignored(workflow)


def test_anchor_decoy_in_another_run_payload_is_invisible(tmp):
    del tmp
    decoy = (
        '      - name: Decoy\n'
        '        run:\t|\n'
        '          # Python measured: 73.3\n')
    workflow = _RATCHET_WORKFLOW.replace(
        _PYTHON_STEP, decoy + _PYTHON_STEP, 1)
    _assert_python_decoy_ignored(workflow)


def test_anchor_decoy_in_a_quoted_scalar_is_invisible(tmp):
    del tmp
    decoy = (
        '      - name: Decoy\n'
        '        env:\n'
        '          NOTE: "before\n'
        '          # Python measured: 73.3\n'
        '            after"\n'
        '        run: "true"\n')
    workflow = _RATCHET_WORKFLOW.replace(
        _PYTHON_STEP, decoy + _PYTHON_STEP, 1)
    _assert_python_decoy_ignored(workflow)


def test_anchor_decoy_in_an_environment_value_is_invisible(tmp):
    del tmp
    decoy = (
        '      - name: Decoy\n'
        '        env:\n'
        '          "RATCHET:DECOY": |\n'
        '            # Python measured: 73.3\n'
        '        run: "true"\n')
    workflow = _RATCHET_WORKFLOW.replace(
        _PYTHON_STEP, decoy + _PYTHON_STEP, 1)
    _assert_python_decoy_ignored(workflow)


def test_anchor_decoy_in_the_other_language_gate_is_invisible(tmp):
    del tmp
    workflow = _RATCHET_WORKFLOW.replace(
        _JAVASCRIPT_STEP,
        _JAVASCRIPT_STEP + '        # Python measured: 73.3\n', 1)
    _assert_python_decoy_ignored(workflow)


def test_anchor_decoy_between_gate_steps_is_invisible(tmp):
    del tmp
    workflow = _RATCHET_WORKFLOW.replace(
        _JAVASCRIPT_STEP,
        '      # Python measured: 73.3\n' + _JAVASCRIPT_STEP, 1)
    _assert_python_decoy_ignored(workflow)


def test_forged_step_payload_is_invisible_when_real_gate_exists(tmp):
    del tmp
    workflow = _RATCHET_WORKFLOW.replace(
        _PYTHON_STEP, _forged_python_step_payload() + _PYTHON_STEP, 1)
    _assert_python_decoy_ignored(workflow)


def test_forged_step_payload_cannot_replace_a_renamed_real_gate(tmp):
    del tmp
    ratchet = _ratchet()
    renamed_step = _PYTHON_STEP.replace(
        'coverage gate', 'coverage check')
    workflow = _RATCHET_WORKFLOW.replace(
        _PYTHON_STEP, _forged_python_step_payload() + renamed_step, 1)
    try:
        ratchet.update(workflow, 80.0, 'python')
    except SystemExit as why:
        assert "step named 'Python coverage gate'; found 0" in str(why), why
    else:
        raise AssertionError('a forged payload replaced the renamed gate')


def test_step_name_decoys_in_other_scalar_styles_are_invisible(tmp):
    del tmp
    decoys = (
        '      - name: Scalar decoys\n'
        '        env:\n'
        '          FOLDED: >-\n'
        '            - name: Python coverage gate\n'
        '          QUOTED: "before\n'
        '          - name: Python coverage gate\n'
        '            after"\n'
        '        run: "true"\n')
    workflow = _RATCHET_WORKFLOW.replace(
        _PYTHON_STEP, decoys + _PYTHON_STEP, 1)
    raised = _assert_python_gate_raised(workflow)
    assert raised.count('- name: Python coverage gate') == 3, raised


def test_semantic_gate_name_with_trailing_comment_is_accepted(tmp):
    del tmp
    workflow = _RATCHET_WORKFLOW.replace(
        _PYTHON_STEP,
        '      - name: Python coverage gate # managed\n', 1)
    _assert_python_gate_raised(workflow)


def test_quoted_gate_name_with_extra_separation_is_accepted(tmp):
    del tmp
    workflow = _RATCHET_WORKFLOW.replace(
        _PYTHON_STEP,
        '      - name :    "Python coverage gate"\n', 1)
    _assert_python_gate_raised(workflow)


def test_trailing_comment_indentation_does_not_change_ownership(tmp):
    del tmp
    failures = []
    for indent in (6, 8, 10):
        comment = ' ' * indent + '# Python measured: 73.3\n'
        workflow = _RATCHET_WORKFLOW.replace(
            _JAVASCRIPT_STEP, comment + _JAVASCRIPT_STEP, 1)
        try:
            _assert_python_decoy_ignored(workflow)
        except AssertionError as error:
            failures.append(f'{indent}: {error}')
    assert not failures, '\n'.join(failures)


def test_renamed_gate_step_is_refused_by_expected_name(tmp):
    del tmp
    ratchet = _ratchet()
    for language, step in (
            ('python', _PYTHON_STEP),
            ('javascript', _JAVASCRIPT_STEP)):
        expected = step.partition('name: ')[2].strip()
        renamed = _RATCHET_WORKFLOW.replace(
            step, step.replace('coverage gate', 'coverage check'), 1)
        try:
            ratchet.update(renamed, 90.0, language)
        except SystemExit as why:
            assert f"step named '{expected}'" in str(why), why
        else:
            raise AssertionError(f'a renamed {language} gate was accepted')


def test_the_ratchet_raises_the_floor_to_what_a_run_measured(tmp):
    """The recorded measurement is what goes stale, so it is rewritten too."""
    del tmp
    ratchet = _ratchet()
    raised = ratchet.update(_RATCHET_WORKFLOW, 80.0, 'python')
    assert raised is not None, 'a measurement 8 points up justified no raise'
    measured, floor = ratchet.read_calibration(raised, 'python')
    assert measured == 80.0, raised
    assert floor == 78.5, raised
    assert '--fail-under=78.5' in raised, raised


def test_the_ratchet_never_lowers_the_floor(tmp):
    """A ratchet only turns one way: a run that reached fewer subprocesses is
    not evidence that the code lost coverage."""
    del tmp
    ratchet = _ratchet()
    for measured in (73.3, 72.0, 60.0, 73.4):
        assert ratchet.update(
            _RATCHET_WORKFLOW, measured, 'python') is None, measured


def test_the_ratchet_leaves_a_raise_the_pinning_rule_accepts(tmp):
    """Whatever it writes must satisfy the test that pins these numbers, or
    the next run goes red on a file this script wrote."""
    del tmp
    ratchet = _ratchet()
    for measured in (80.0, 91.7, 100.0):
        raised = ratchet.update(_RATCHET_WORKFLOW, measured, 'python')
        assert raised is not None, measured
        recorded, floor = ratchet.read_calibration(raised, 'python')
        assert floor < recorded, (floor, recorded)
        assert recorded - floor <= 2.0, (recorded, floor)


def test_the_ratchet_refuses_a_gate_that_already_disagrees(tmp):
    """A flag that has drifted from its own comment is a hand fix, not
    something to bake in under a fresh number."""
    del tmp
    ratchet = _ratchet()
    disagreeing = _RATCHET_WORKFLOW.replace('--fail-under=72', '--fail-under=70')
    try:
        ratchet.update(disagreeing, 90.0, 'python')
    except SystemExit as why:
        assert 'fix that by hand' in str(why), why
    else:
        raise AssertionError('a disagreeing gate was rewritten anyway')


def test_the_ratchet_refuses_a_file_carrying_no_calibration(tmp):
    """Silently doing nothing would read as 'no raise justified'."""
    del tmp
    ratchet = _ratchet()
    try:
        ratchet.update(
            'run: python -m coverage report\n', 90.0, 'python')
    except SystemExit as why:
        assert "step named 'Python coverage gate'" in str(why), why
    else:
        raise AssertionError('a file with no calibration was accepted')


def test_present_gate_missing_measured_marker_records_no_calibration(tmp):
    del tmp
    ratchet = _ratchet()
    workflow = _RATCHET_WORKFLOW.replace(
        '        # Python measured: 73.3\n', '', 1)
    try:
        ratchet.update(workflow, 90.0, 'python')
    except SystemExit as why:
        assert str(why) == (
            'the python coverage gate records no calibration'), why
    else:
        raise AssertionError('a gate missing its measured marker was accepted')


def test_the_ratchet_rewrites_the_file_it_is_pointed_at(tmp):
    """The entry point writes only when it raises, and says which it did."""
    workflow = Path(tmp) / 'tests.yml'
    workflow.write_text(_RATCHET_WORKFLOW, encoding='utf-8')
    script = str(ROOT / 'scripts' / 'ci' / 'ratchet.py')

    quiet = subprocess.run(
        [sys.executable, script, '--language', 'python',
         '--measured', '73.0',
         '--workflow', str(workflow)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert quiet.returncode == 0, (quiet.stdout, quiet.stderr)
    assert 'justifies no raise' in quiet.stdout, quiet.stdout
    assert workflow.read_text(encoding='utf-8') == _RATCHET_WORKFLOW

    raised = subprocess.run(
        [sys.executable, script, '--language', 'python',
         '--measured', '80.0',
         '--workflow', str(workflow)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert raised.returncode == 0, (raised.stdout, raised.stderr)
    assert 'raised the Python coverage floor 72.0 -> 78.5' \
        in raised.stdout, raised.stdout
    assert '--fail-under=78.5' in workflow.read_text(encoding='utf-8')


def test_package_entry_point_reports_refused_workflow_yaml(tmp):
    """A reader refusal must become the ratchet's decode diagnostic."""
    workflow = Path(tmp) / 'refused.yml'
    workflow.write_text(
        'jobs:\n  coverage:\n    steps: not-a-sequence\n',
        encoding='utf-8')
    result = subprocess.run(
        [sys.executable, '-m', 'scripts.ci.ratchet',
         '--language', 'python', '--measured', '80.0',
         '--workflow', str(workflow)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert result.returncode != 0, result.stdout
    assert result.stdout == '', result.stdout
    assert result.stderr == (
        "cannot decode workflow YAML: job 'coverage' steps are not a "
        'sequence\n'), result.stderr


def test_the_ratchet_can_raise_the_real_workflow(tmp):
    """The regexes are pinned against the file they actually run on, not only
    against the fixture above."""
    del tmp
    ratchet = _ratchet()
    text = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    recorded, floor = ratchet.read_calibration(text, 'python')
    assert floor < recorded, (floor, recorded)
    raised = ratchet.update(text, recorded + 5.0, 'python')
    assert raised is not None, 'the real workflow refused a five-point raise'
    new_measured, new_floor = ratchet.read_calibration(raised, 'python')
    assert new_measured == recorded + 5.0, new_measured
    assert new_floor > floor, (new_floor, floor)


def test_each_coverage_ratchet_records_what_it_was_calibrated_to(tmp):
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
    ratchet = _ratchet()
    for language in ('python', 'javascript'):
        measured, floor = ratchet.read_calibration(workflow, language)
        assert floor < measured, (language, floor, measured)
        assert measured - floor <= 2.0, (
            f'the {language} ratchet allows a '
            f'{measured - floor:.1f} point regression')


def test_languages_ratchet_independently(tmp):
    """Raising either trio must leave the other one byte-for-byte alone."""
    del tmp
    ratchet = _ratchet()
    python_before = ratchet.read_calibration(_RATCHET_WORKFLOW, 'python')
    javascript_before = ratchet.read_calibration(
        _RATCHET_WORKFLOW, 'javascript')

    python_raise = ratchet.update(_RATCHET_WORKFLOW, 80.0, 'python')
    assert python_raise is not None
    javascript_text = _RATCHET_WORKFLOW.partition(
        '        # JavaScript measured:')[2]
    assert python_raise.partition(
        '        # JavaScript measured:')[2] == javascript_text
    assert ratchet.read_calibration(
        python_raise, 'javascript') == javascript_before
    assert ratchet.read_calibration(python_raise, 'python') == (80.0, 78.5)

    javascript_raise = ratchet.update(
        _RATCHET_WORKFLOW, 40.0, 'javascript')
    assert javascript_raise is not None
    python_text = _RATCHET_WORKFLOW.partition(
        '        # JavaScript measured:')[0]
    assert javascript_raise.partition(
        '        # JavaScript measured:')[0] == python_text
    assert ratchet.read_calibration(
        javascript_raise, 'python') == python_before
    assert ratchet.read_calibration(
        javascript_raise, 'javascript') == (40.0, 38.5)


def _git(repo, *args):
    """Run one git command inside a fixture repository."""
    return subprocess.run(('git', '-C', str(repo)) + args, check=True,
                          capture_output=True, text=True, timeout=60)


def _ratchet_subject_chain():
    """The subject-selection chain, exactly as the workflow ships it."""
    workflow = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(
        encoding='utf-8')
    lines = workflow.splitlines()
    begins = [
        index for index, line in enumerate(lines)
        if line.lstrip().startswith('if ')
        and 'git diff --quiet -- scripts/ci/size_baseline.py' in line]
    assert len(begins) == 1, begins
    start = begins[0]
    indent = lines[start][:-len(lines[start].lstrip())]
    end = start + next(
        offset for offset, line in enumerate(lines[start:])
        if line == f'{indent}fi')
    chain = '\n'.join(lines[start:end + 1])
    assert '.github/workflows/tests.yml' in chain, chain
    assert 'raise coverage ratchets and tighten module sizes' in chain, chain
    return chain


def _subject_selected(repo, chain):
    """The bare subject the chain announces, run in `repo`."""
    done = subprocess.run(
        [_util.workflow_bash(), '-e', '-o', 'pipefail', '-c', chain],
        cwd=repo, env=_util.child_coverage('scrub'),
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (done.stdout, done.stderr)
    line = done.stdout.strip()
    assert line.startswith('subject='), line
    return line[len('subject='):]


def test_ratchet_subject_names_a_file_that_moved(tmp):
    """A subject announces the file that moved, never the one that did not.

    `git diff --quiet -- <path>` exits 0 when the path is unchanged, so an
    unnegated condition reaches the arm for the file that did NOT move: a
    coverage-only raise announced a module-size tighten and the other way
    round, which titled ratchet commits for a file none of them touched.
    """
    chain = _ratchet_subject_chain()
    workflow_rel = '.github/workflows/tests.yml'
    baseline_rel = 'scripts/ci/size_baseline.py'
    cases = (
        ((workflow_rel,), 'raise the coverage ratchets'),
        ((baseline_rel,), 'tighten the module size baseline'),
        ((workflow_rel, baseline_rel),
         'raise coverage ratchets and tighten module sizes'),
    )
    repo = Path(tmp) / 'subjects'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'tests@example.invalid')
    _git(repo, 'config', 'user.name', 'Tests')
    for rel in (workflow_rel, baseline_rel):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'# {rel}\n', encoding='utf-8')
    _git(repo, 'add', '-f', '.')
    _git(repo, 'commit', '-qm', 'base')
    for changed, subject in cases:
        for rel in (workflow_rel, baseline_rel):
            path = repo / rel
            path.write_text(f'# {rel}\n', encoding='utf-8')
        for rel in changed:
            (repo / rel).write_text(
                f'# {rel}\n# moved\n', encoding='utf-8')
        assert _subject_selected(repo, chain) == subject, changed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciratchets_')


if __name__ == '__main__':
    raise SystemExit(main())
