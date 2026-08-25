#!/usr/bin/env python3
"""Executable contracts for locating named workflow steps."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _yamlread import (  # noqa: E402
    YAMLReadError, _comment, job_scalar, step_scalar, step_scalars,
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


def test_step_scalar_reads_sequence_item_mapping_field(tmp):
    """The field carried after the dash belongs to the step mapping."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - if: >-\n"
        "       first\n"
        "       && second\n"
        "     name: target\n")
    try:
        value = step_scalar(source, 'sample', 'target', 'if')
    except YAMLReadError as error:
        raise AssertionError(
            f'sequence-item scalar was not decoded: {error}') from error
    assert value == 'first && second', value


def test_equivalent_quoted_step_keys_are_duplicates(tmp):
    """Quoted and plain spellings cannot define one field twice."""
    del tmp
    source = (
        "jobs:\n sample:\n  steps:\n"
        "   - name: target\n"
        "     if: z\n"
        "     'if': false\n")
    try:
        step_scalar(source, 'sample', 'target', 'if')
    except YAMLReadError as error:
        assert 'duplicate mapping key: if' in str(error), str(error)
        return
    raise AssertionError('equivalent duplicate if keys were accepted')


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


def _coverage_workflow():
    """Read the privileged workflow whose steps must remain trusted."""
    path = _util.ROOT / '.github/workflows/coverage-comment.yml'
    return path.read_text(encoding='utf-8')


def _assert_no_privileged_checkout(workflow):
    """Refuse checkout by the decoded identity of every step action."""
    uses = step_scalars(workflow, 'comment', 'uses')
    assert uses is not None, 'privileged workflow steps were not decoded'
    for value in uses:
        identity = value.split('@', 1)[0].casefold()
        assert identity != 'actions/checkout', (
            f'privileged workflow decodes checkout action: {value}')


def _assert_checkout_mutation_refused(workflow):
    """Require one real-workflow checkout mutation to fail the contract."""
    try:
        _assert_no_privileged_checkout(workflow)
    except AssertionError as error:
        assert 'decodes checkout action' in str(error), str(error)
        return
    raise AssertionError('decoded checkout mutation was accepted')


def test_step_scalar_list_decodes_each_uses_spelling(tmp):
    """Plain, quoted, and escaped fields return decoded action values."""
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - uses: owner/one@abc\n'
        "      - 'uses': 'owner/two@def'\n"
        '      - "u\\x73es": "actions\\x2fcheckout@123"\n'
        '      - run: echo harmless\n')
    assert step_scalars(source, 'sample', 'uses') == [
        'owner/one@abc', 'owner/two@def', 'actions/checkout@123']


def test_step_scalar_list_decodes_folded_values(tmp):
    """A wrapped action pin remains visible to decoded step policy."""
    del tmp
    source = (
        'jobs:\n  sample:\n    steps:\n'
        '      - uses: >-\n'
        '          owner/action@0123456789abcdef\n')
    try:
        values = step_scalars(source, 'sample', 'uses')
    except YAMLReadError as error:
        raise AssertionError(
            f'folded step scalar was not decoded: {error}') from error
    assert values == ['owner/action@0123456789abcdef'], values


def test_privileged_workflow_has_no_decoded_checkout(tmp):
    """Every action in the trusted workflow is decoded before policy."""
    del tmp
    _assert_no_privileged_checkout(_coverage_workflow())


def test_escaped_checkout_value_mutation_is_refused(tmp):
    """The review's escaped checkout value cannot evade decoded policy."""
    del tmp
    workflow = _coverage_workflow()
    step = (
        '      - name: Check out pull-request code\n'
        '        uses: "actions\\x2fcheckout@'
        '3d3c42e5aac5ba805825da76410c181273ba90b1"\n'
        '        with:\n'
        '          ref: ${{ github.event.workflow_run.head_sha }}\n'
        '          persist-credentials: false\n\n')
    mutated = workflow.replace('    steps:\n', '    steps:\n' + step, 1)
    assert mutated != workflow, 'real privileged steps were not mutated'
    _assert_checkout_mutation_refused(mutated)


def test_quoted_and_escaped_uses_keys_are_refused(tmp):
    """Equivalent decoded uses keys cannot hide a checkout action."""
    del tmp
    workflow = _coverage_workflow()
    fields = ("        'uses': ", '        "u\\x73es": ')
    action = 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1'
    for field in fields:
        step = '      - name: Checkout spelling\n' + field + action + '\n'
        mutated = workflow.replace('    steps:\n', '    steps:\n' + step, 1)
        assert mutated != workflow, field
        _assert_checkout_mutation_refused(mutated)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
