#!/usr/bin/env python3
"""Executable contracts for locating named workflow steps."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _yamlread import (  # noqa: E402
    YAMLReadError, _comment, job_scalar, step_scalar,
)


def _raises(source, detail):
    """Require unsupported step-name syntax to be refused."""
    try:
        step_scalar(source, 'sample', 'target', 'if')
    except YAMLReadError as error:
        assert detail in str(error), str(error)
        return
    raise AssertionError(f'{detail} step-name syntax was accepted')


def _job_raises(source, detail):
    """Require unsupported job-scalar syntax to be refused."""
    try:
        job_scalar(source, 'sample', 'if')
    except YAMLReadError as error:
        assert detail in str(error), str(error)
        return
    raise AssertionError(f'{detail} job syntax was accepted')


def test_step_name_can_follow_another_mapping_field(tmp):
    """A later name field still identifies its sequence item."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - x: y\n     name: target\n     if: z")
    assert step_scalar(source, 'sample', 'target', 'if') == 'z'


def test_step_name_inline_comment_is_refused(tmp):
    """A name comment cannot silently turn a present step into absence."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: target #\n     if: z")
    _raises(source, 'inline comment')


def test_step_name_anchor_is_refused(tmp):
    """A YAML anchor on a name is outside the admitted step subset."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: &a target\n     if: z")
    _raises(source, 'anchor')


def test_aliased_step_name_is_refused(tmp):
    """A YAML alias on a name is outside the admitted step subset."""
    del tmp
    source = (
        "target-name: &target-name target\n"
        "jobs:\n sample:\n  steps:\n"
        "   - name: *target-name\n     if: z")
    _raises(source, 'alias')


def test_step_name_tag_is_refused(tmp):
    """A YAML tag on a name is outside the admitted step subset."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: ! target\n     if: z")
    _raises(source, 'tag')


def test_quoted_step_name_key_is_recognized(tmp):
    """An exactly quoted name key still names the step mapping field."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - 'name': target\n     if: z")
    assert step_scalar(source, 'sample', 'target', 'if') == 'z'


def test_double_quoted_step_name_key_is_recognized(tmp):
    """A double-quoted name key still names the step mapping field."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - \"name\": target\n     if: z")
    assert step_scalar(source, 'sample', 'target', 'if') == 'z'


def test_step_name_without_mapping_separator_is_refused(tmp):
    """A colon without following whitespace is not a mapping field."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name:target\n     if: z")
    _raises(source, 'unsupported YAML mapping line')


def test_non_ascii_mapping_separator_is_refused(tmp):
    """A no-break space cannot separate a mapping key and value."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name:\u00a0target\n     if: z")
    _raises(source, 'unsupported YAML mapping line')


def test_non_ascii_scalar_whitespace_is_not_normalized(tmp):
    """No-break spaces in scalar values remain data, not trim markers."""
    del tmp
    job_source = 'jobs:\n sample:\n  if: \u00a0z\u00a0\n'
    assert job_scalar(job_source, 'sample', 'if') == '\u00a0z\u00a0'
    step_source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: \u00a0target\u00a0\n     if: z")
    assert step_scalar(step_source, 'sample', 'target', 'if') is None
    block_source = 'jobs:\n sample:\n  if: |\n   \u00a0\n   z\n'
    assert job_scalar(block_source, 'sample', 'if') == '\u00a0\nz\n'
    _job_raises(
        'jobs:\n sample:\n  if: >-\u00a0\n   z\n',
        'unsupported block scalar header')


def test_non_ascii_structural_whitespace_is_refused(tmp):
    """No-break spaces cannot hide malformed structure or comments."""
    del tmp
    assert not _comment(('\u00a0# comment', False))
    _raises(
        'jobs: \u00a0\n sample:\n  steps:\n'
        '   - name: target\n     if: z',
        'jobs is not a mapping')
    _raises(
        'jobs:\n sample:\n  steps:\n'
        '   - name: target\n     \u00a0\n     if: z',
        'unsupported YAML mapping line')
    _raises(
        'jobs:\n sample:\n  steps:\n'
        '   - name: target\n     \u00a0# comment\n     if: z',
        'unsupported YAML mapping line')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
