#!/usr/bin/env python3
"""Trigger-policy contracts for the workflows in .github/.

These tests read workflow trigger blocks through the bounded reader and keep
the paired-event policy contracts together as the set grows.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _workflows import (  # noqa: E402
    _event_option_keys, _workflow_path_filters, _workflow_triggers)


def _assert_no_workflow_gates_one_commit_twice(workflows):
    """Reject every push/pull_request pair without an event-level branch."""
    checked = []
    for path in sorted(workflows.iterdir()):
        if path.suffix not in ('.yml', '.yaml'):
            continue
        triggers = _workflow_triggers(
            path.read_text(encoding='utf-8'), path.name)
        if 'pull_request' not in triggers or 'push' not in triggers:
            continue
        checked.append(path.name)
        # The event's OWN keys, not any line in its block: a `branches:`
        # nested a level deeper filters something else, and reading it as
        # the push filter passes a trigger that carries none.
        assert 'branches' in _event_option_keys(
            triggers['push'], path.name), (
            f'{path.name} runs on every branch push AND on pull_request, so a '
            f'pull request from this repository gates its head SHA twice')
    assert checked, 'no workflow declares both triggers; has one been renamed?'


def test_double_gate_scan_reads_yaml_and_event_owned_options(tmp):
    """The double-gate helper must inspect both suffixes and own keys."""
    workflows = Path(tmp) / 'workflows'
    workflows.mkdir()
    control = ('name: control\n\non:\n'
               '  push:\n    branches: [main]\n'
               '  pull_request:\n')
    (workflows / 'control.yml').write_text(control, encoding='utf-8')
    branchless = ('name: branchless\n\non:\n'
                  '  push:\n  pull_request:\n')
    branchless_path = workflows / 'branchless.yaml'
    branchless_path.write_text(branchless, encoding='utf-8')
    try:
        _assert_no_workflow_gates_one_commit_twice(workflows)
    except AssertionError as failure:
        assert 'branchless.yaml' in str(failure), failure
    else:
        raise AssertionError('branchless .yaml workflow was accepted')

    branchless_path.unlink()
    nested = ('name: nested\n\non:\n'
              '  push:\n'
              '    paths:\n'
              '      - src/**\n'
              '    types:\n'
              '      branches: [main]\n'
              '  pull_request:\n')
    nested_path = workflows / 'nested.yaml'
    nested_path.write_text(nested, encoding='utf-8')
    try:
        _assert_no_workflow_gates_one_commit_twice(workflows)
    except AssertionError as failure:
        assert 'nested.yaml' in str(failure), failure
    else:
        raise AssertionError('nested branches key was treated as an option')


def test_no_workflow_gates_one_commit_twice(tmp):
    """A pull request's head SHA gets one run per workflow, not two.

    A branch pushed to this repository fires `push`, and opening a pull
    request from it fires `pull_request` against the same SHA — so seven
    workflows ran twice on every Dependabot pull request, `tests` included
    with its twelve matrix legs. Six open pull requests saturated the runner
    pool and pushes to main sat queued behind work already done.

    The fix is a `branches:` filter on `push`, which this pins. A branch with
    no pull request open then gets no run at all, which is the trade: it is
    not a tree anyone is reviewing.
    """
    del tmp
    _assert_no_workflow_gates_one_commit_twice(
        ROOT / '.github' / 'workflows')


def _assert_workflow_trigger_filters_match(workflows):
    """Assert symmetric path filters for every paired workflow in a tree."""
    checked = []
    for path in sorted(workflows.iterdir()):
        if path.suffix not in ('.yml', '.yaml'):
            continue
        triggers = _workflow_triggers(
            path.read_text(encoding='utf-8'), path.name)
        if 'pull_request' not in triggers or 'push' not in triggers:
            continue
        checked.append(path.name)
        filters = [_workflow_path_filters(triggers[event], path.name)
                   for event in ('push', 'pull_request')]
        assert filters[0] == filters[1], (
            f'{path.name} filters push and pull_request differently: '
            f'{filters[0]!r} != {filters[1]!r}')
    assert checked, 'no workflow declares both triggers; has one been renamed?'


def test_workflow_trigger_filters_match_between_push_and_pull_request(tmp):
    """Push and pull_request must make the same path-filtering choice.

    A filter on push alone lets a documentation-only commit skip the gates on
    main while the identical pull request runs them. This test owns only the
    symmetry property; the release-safety direction is pinned separately.
    """
    del tmp
    _assert_workflow_trigger_filters_match(ROOT / '.github' / 'workflows')


def test_workflow_reader_accepts_string_controls_and_a_leading_bom(tmp):
    """Positive scalar controls stay green through the policy helper."""
    workflows = Path(tmp) / 'workflows'
    workflows.mkdir()
    spelling = "[main, 'release', .gitignore, release-candidate, '**/*.md']"
    content = ('name: control\n\non:\n  push:\n    branches: [main]\n'
               f'    paths-ignore: {spelling}\n'
               f'  pull_request:\n    paths-ignore: {spelling}\n')
    (workflows / 'controls.yml').write_text(content, encoding='utf-8')
    bom = '\ufeff' + content.replace(spelling, '[docs\ufeff.md]')
    (workflows / 'bom.yml').write_text(bom, encoding='utf-8')
    _assert_workflow_trigger_filters_match(workflows)
    expected = {'paths-ignore': [
        'main', 'release', '.gitignore', 'release-candidate', '**/*.md']}
    triggers = _workflow_triggers(content, 'controls.yml')
    assert _workflow_path_filters(triggers['push'], 'controls.yml') == expected
    assert _workflow_path_filters(
        triggers['pull_request'], 'controls.yml') == expected
    triggers = _workflow_triggers(bom, 'bom.yml')
    assert _workflow_path_filters(triggers['push'], 'bom.yml') == {
        'paths-ignore': ['docs\ufeff.md']}


def test_workflow_trigger_gate_rejects_quote_collisions_and_accepts_comments(
        tmp):
    """The gate refuses unequal quote spellings and accepts equal comments."""
    cases = (
        ('flow-quote-collision',
         "    paths-ignore: [don't.md, isn't.md]\n",
         '    paths-ignore: ["don\'t.md, isn\'t.md"]\n', False),
        ('block-quote-collision',
         "    paths-ignore:\n      - don't.md  # docs\n",
         '    paths-ignore:\n      - "don\'t.md  # docs"\n', False),
        ('flow-trailing-comment',
         "    paths-ignore: ['**/*.md', 'LICENSE', '.gitignore']  # "
         "docs and metadata\n",
         "    paths-ignore: ['**/*.md', 'LICENSE', '.gitignore']  # "
         "docs and metadata\n",
         True),
    )
    for name, push_value, pull_value, accepted in cases:
        workflows = Path(tmp) / name
        workflows.mkdir()
        content = ('name: control\n\non:\n'
                   f'  push:\n{push_value}'
                   f'  pull_request:\n{pull_value}')
        (workflows / 'control.yml').write_text(content, encoding='utf-8')
        if accepted:
            _assert_workflow_trigger_filters_match(workflows)
            continue
        try:
            _assert_workflow_trigger_filters_match(workflows)
        except AssertionError as failure:
            assert 'control.yml' in str(failure), failure
        else:
            raise AssertionError(f'{name}: unequal filters were accepted')


def test_contribution_gates_have_unfiltered_push_triggers(tmp):
    """The three unfiltered contribution workflows run on every push to main.

    This test owns the release-safety direction: release.yml finds these runs
    by the shared commit SHA, so a Markdown-only push must not be filtered.
    """
    del tmp
    gate_names = ('tests.yml', 'audit.yml', 'codeql.yml')
    workflows = ROOT / '.github' / 'workflows'
    for name in gate_names:
        path = workflows / name
        assert path.is_file(), f'named contribution gate is missing: {name}'
        triggers = _workflow_triggers(
            path.read_text(encoding='utf-8'), name)
        assert 'push' in triggers, f'{name} has no push trigger'
        assert not _workflow_path_filters(triggers['push'], name), (
            f'{name} filters its push trigger: '
            f'{_workflow_path_filters(triggers["push"], name)}')
    for retired in ('lint.yml', 'types.yml', 'eslint.yml', 'actionlint.yml'):
        assert not (workflows / retired).exists(), (
            f'moved gate workflow still exists: {retired}')


def test_workflow_trigger_filters_accept_string_pairs_and_opposite_quotes(tmp):
    """Equal strings survive plain/quoted and opposite-quote spellings."""
    workflows = Path(tmp) / 'workflows'
    workflows.mkdir()
    cases = (
        ('exponent', '1e1_0', "'1e1_0'", ['1e1_0']),
        ('nan', '-.NaN', "'-.NaN'", ['-.NaN']),
        ('apostrophe', '"**/what\'s-new.md"',
         '"**/what\'s-new.md"', ["**/what's-new.md"]),
        ('double-quote', "'say \"hi\".md'", "'say \"hi\".md'",
         ['say "hi".md']),
    )
    for name, push_value, pull_value, expected in cases:
        content = ('name: control\n\non:\n'
                   f'  push:\n    paths-ignore: [{push_value}]\n'
                   f'  pull_request:\n    paths-ignore: [{pull_value}]\n')
        path = workflows / f'{name}.yml'
        path.write_text(content, encoding='utf-8')
        _assert_workflow_trigger_filters_match(workflows)
        triggers = _workflow_triggers(content, path.name)
        for event in ('push', 'pull_request'):
            assert _workflow_path_filters(
                triggers[event], path.name) == {'paths-ignore': expected}

    refusals = (
        ('surrounding-single', "['a''b']"),
        ('surrounding-double', r'["a\"b"]'),
        ('backslash', r'["a\\b"]'),
    )
    for name, value in refusals:
        refusal_dir = Path(tmp) / name
        refusal_dir.mkdir()
        content = ('name: refusal\n\non:\n'
                   f'  push:\n    paths-ignore: {value}\n'
                   f'  pull_request:\n    paths-ignore: {value}\n')
        (refusal_dir / 'control.yml').write_text(
            content, encoding='utf-8')
        try:
            _assert_workflow_trigger_filters_match(refusal_dir)
        except AssertionError as failure:
            assert 'control.yml' in str(failure), failure
        else:
            raise AssertionError(f'{name}: unsupported quote was accepted')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='workflowtriggers_')


if __name__ == '__main__':
    raise SystemExit(main())
