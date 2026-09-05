#!/usr/bin/env python3
"""Executable contracts for scalar values inside workflow mappings."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlread import YAMLReadError, job_mapping  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
from workflow_yaml import workflow_step_items  # noqa: E402


def test_a_wrapped_mapping_value_must_start_as_plain(tmp):
    """Quoted, flow, block, and empty starts are not plain continuations."""
    del tmp
    spellings = (
        "      A: 'one'\n        two\n",
        '      A: [one]\n        two\n',
        '      A: |\n        two\n',
        '      A:\n        two\n',
    )
    for spelling in spellings:
        source = 'jobs:\n  sample:\n    env:\n' + spelling
        try:
            job_mapping(source, 'sample', 'env')
        except YAMLReadError as error:
            assert 'unsupported nested value' in str(error), str(error)
            continue
        raise AssertionError(f'job_mapping folded {spelling!r}')


def test_a_wrapped_mapping_value_must_pass_inline_admission(tmp):
    """The completed fold, not only its first line, must be admissible."""
    del tmp
    spellings = (
        '      A: one\n        b: c\n',
        '      A: one\n        two\tthree\n',
    )
    for spelling in spellings:
        source = 'jobs:\n  sample:\n    env:\n' + spelling
        try:
            job_mapping(source, 'sample', 'env')
        except YAMLReadError as error:
            assert 'unsupported plain scalar' in str(error), str(error)
            continue
        raise AssertionError(f'job_mapping admitted {spelling!r}')


def test_mapping_value_wrap_preserves_inline_admission(tmp):
    """Valid one-line and wrapped values share one decoded admission."""
    del tmp
    spellings = (
        ('one two', 'one\n        two', 'one two'),
        ('${{ x }}', '${{ x\n        }}', '${{ x }}'),
    )
    for inline, wrapped, expected in spellings:
        prefix = 'jobs:\n  sample:\n    env:\n      A: '
        assert job_mapping(prefix + inline + '\n', 'sample', 'env') == {
            'A': expected}
        assert job_mapping(prefix + wrapped + '\n', 'sample', 'env') == {
            'A': expected}


def test_mapping_value_wrap_cannot_admit_a_trailing_colon(tmp):
    """Wrapping cannot open a route to an invalid trailing value marker."""
    del tmp
    spellings = ('one two:', 'one\n        two:')
    for spelling in spellings:
        source = (
            'jobs:\n  sample:\n    env:\n      A: ' + spelling + '\n')
        try:
            job_mapping(source, 'sample', 'env')
        except YAMLReadError as error:
            assert 'unsupported plain scalar' in str(error), str(error)
            continue
        raise AssertionError(f'job_mapping admitted {spelling!r}')


def test_a_wrapped_mapping_value_stays_in_its_own_section(tmp):
    """One mapping value cannot absorb a sibling value's continuation."""
    del tmp
    source = (
        'jobs:\n  sample:\n    env:\n'
        '      A: one\n'
        '      B: three\n'
        '        four\n')
    assert job_mapping(source, 'sample', 'env') == {
        'A': 'one', 'B': 'three four'}


def test_a_comment_ends_a_wrapped_mapping_value(tmp):
    """Text after an inline comment cannot continue a mapping value."""
    del tmp
    source = (
        'jobs:\n  sample:\n    env:\n'
        '      A: one # comment\n'
        '        two\n')
    try:
        job_mapping(source, 'sample', 'env')
    except YAMLReadError as error:
        assert 'unsupported multiline scalar' in str(error), str(error)
        return
    raise AssertionError('job_mapping folded past an inline comment')


def test_explicit_mapping_value_keeps_its_scalar_content_opaque(tmp):
    """An explicit key's value cannot redefine an earlier anchor."""
    del tmp
    values = ('|\n     k: &a hidden\n', '>-\n     k: &a hidden\n',
              '"open\n     k: &a hidden"\n',
              "'open\n     k: &a hidden'\n")
    for value in values:
        for gap in ('', '\n   # comment\n'):
            source = ('jobs:\n sample:\n  env:\n'
                      '   FIRST: &a target\n'
                      f'   ? SECOND{gap}\n   : {value}'
                      '  steps:\n   - name: *a\n     if: z\n')
            parsed = yaml.load(source, Loader=yaml.BaseLoader)
            expected = parsed['jobs']['sample']['steps'][0]['name']
            items = workflow_step_items(source, 'sample')
            assert items is not None, source
            assert [item.name for item in items] == [expected] == ['target']


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
