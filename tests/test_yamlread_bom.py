#!/usr/bin/env python3
"""The byte-order mark rule of the workflow readers, pinned alone.

A real parser removes one mark, and only from the stream's first character.
`_lines` does the same, so a reader built on it reads a marked workflow as
the unmarked one while every other mark stays data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _yamlread import (  # noqa: E402
    YAMLReadError, job_mapping, job_scalar, step_mapping_scalar,
    step_scalar, step_scalars, top_level_mapping,
)

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
from workflow_yaml import workflow_step_items  # noqa: E402

_MARK = '\ufeff'
_PLAIN = 'jobs:\n sample:\n  if: x'
_STEPS = (
    'jobs:\n sample:\n  steps:\n'
    '   - name: first\n     id: one\n     run: true\n'
    '   - name: second\n     id: two\n     run: false\n')


def _marked(source):
    """Prepend one stream-start mark to a workflow source."""
    return _MARK + source


def _production_items(source):
    items = workflow_step_items(source, 'sample')
    assert items is not None, source
    return items


def test_the_production_reader_preserves_marked_step_coordinates(tmp):
    del tmp
    plain = _production_items(_STEPS)
    marked = _production_items(_marked(_STEPS))
    assert [(item.name, item.identity) for item in marked] == [
        (item.name, item.identity) for item in plain]
    for plain_item, marked_item in zip(plain, marked):
        assert marked_item.start == plain_item.start + 1
        assert marked_item.end == plain_item.end + 1
        assert _marked(_STEPS)[marked_item.start:marked_item.end] == (
            _STEPS[plain_item.start:plain_item.end])


def test_a_production_document_of_only_a_mark_reads_empty(tmp):
    del tmp
    assert workflow_step_items(_MARK, 'sample') is None


def test_the_production_reader_keeps_second_leading_mark_as_data(tmp):
    del tmp
    try:
        workflow_step_items(_marked(_marked(_STEPS)), 'sample')
    except YAMLReadError as error:
        assert 'unsupported plain scalar' in str(error), str(error)
        return
    raise AssertionError('the second leading mark was silently stripped')


def test_the_production_reader_keeps_an_interior_quoted_mark_as_data(tmp):
    del tmp
    source = (
        'jobs:\n sample:\n  steps:\n'
        '   - name: "a\ufeffb"\n     id: mark\n')
    values = [(item.name, item.identity)
              for item in _production_items(source)]
    assert values == [
        ('a\ufeffb', 'mark')]


def test_a_marked_workflow_reads_like_its_unmarked_self(tmp):
    del tmp
    assert job_scalar(_marked(_PLAIN), 'sample', 'if') == 'x'


def test_a_mark_is_gone_before_lines_are_read(tmp):
    """A marked block body reads like its unmarked self."""
    del tmp
    source = _marked('jobs:\n sample:\n  if: >-\n   a\n   b\n')
    assert job_scalar(source, 'sample', 'if') == 'a b'


def test_a_mark_reaches_the_mapping_reader(tmp):
    del tmp
    source = _marked('jobs:\n sample:\n  env:\n   A: b\n')
    assert job_mapping(source, 'sample', 'env') == {'A': 'b'}


def test_a_mark_reaches_the_step_reader(tmp):
    del tmp
    source = _marked(
        'jobs:\n sample:\n  steps:\n   - name: a\n     if: x\n')
    assert step_scalars(source, 'sample', 'if') == ['x']


def test_a_mark_reaches_the_step_scalar_reader(tmp):
    del tmp
    source = _marked(
        'jobs:\n sample:\n  steps:\n   - name: a\n     if: x\n')
    assert step_scalar(source, 'sample', 'a', 'if') == 'x'


def test_a_mark_reaches_the_step_mapping_scalar_reader(tmp):
    del tmp
    source = _marked(
        'jobs:\n sample:\n  steps:\n'
        '   - name: a\n     with:\n      k: v\n')
    assert step_mapping_scalar(source, 'sample', 'a', 'with', 'k') == 'v'


def test_a_mark_reaches_the_top_level_mapping_reader(tmp):
    del tmp
    source = _marked('env:\n A: b\njobs:\n sample:\n  if: x\n')
    assert top_level_mapping(source, 'env') == {'A': 'b'}


def test_a_document_of_only_a_mark_reads_empty(tmp):
    del tmp
    assert job_scalar(_MARK, 'sample', 'if') is None


def test_a_second_leading_mark_is_data_not_a_marker(tmp):
    """The parser strips one mark, so the second leaves `jobs` marked."""
    del tmp
    assert job_scalar(_MARK + _marked(_PLAIN), 'sample', 'if') is None


def test_a_mark_inside_a_quoted_scalar_is_data(tmp):
    del tmp
    source = 'jobs:\n sample:\n  if: "a\ufeffb"\n'
    assert job_scalar(source, 'sample', 'if') == 'a\ufeffb'


def test_a_mark_inside_a_block_scalar_is_data(tmp):
    del tmp
    source = 'jobs:\n sample:\n  if: |\n   \ufefftext\n   more\n'
    assert job_scalar(source, 'sample', 'if') == '\ufefftext\nmore\n'


def test_a_mark_inside_a_plain_scalar_is_data(tmp):
    del tmp
    source = 'jobs:\n sample:\n  if: x\ufeff\n'
    assert job_scalar(source, 'sample', 'if') == 'x\ufeff'
    source = 'jobs:\n sample:\n  if: \ufeffx\n'
    assert job_scalar(source, 'sample', 'if') == '\ufeffx'


def test_a_mark_inside_a_folded_continuation_is_data(tmp):
    """A mark in a wrapped plain scalar's continuation folds in as data."""
    del tmp
    source = 'jobs:\n sample:\n  if: a\n   \ufeffb\n'
    assert job_scalar(source, 'sample', 'if') == 'a \ufeffb'


def test_a_mark_before_a_mapping_key_is_refused_not_removed(tmp):
    """A mid-document mark leaves the key outside the admitted charset."""
    del tmp
    source = 'jobs:\n sample:\n  \ufeffif: x\n'
    try:
        job_scalar(source, 'sample', 'if')
    except YAMLReadError as error:
        assert 'unsupported plain scalar' in str(error), str(error)
        return
    raise AssertionError('a mark before a mapping key must refuse')


def test_a_workflow_without_a_mark_keeps_its_first_character(tmp):
    del tmp
    assert job_scalar(_PLAIN, 'sample', 'if') == 'x'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
