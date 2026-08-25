#!/usr/bin/env python3
"""Executable contracts for locating named workflow steps."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _yamlread import YAMLReadError, step_scalar  # noqa: E402


def _raises(source, detail):
    """Require unsupported step-name syntax to be refused."""
    try:
        step_scalar(source, 'sample', 'target', 'if')
    except YAMLReadError as error:
        assert detail in str(error), str(error)
        return
    raise AssertionError(f'{detail} step-name syntax was accepted')


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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
