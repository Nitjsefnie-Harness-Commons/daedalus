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
from _yamlread import (  # noqa: E402
    YAMLReadError, job_mapping, job_scalar, step_scalars,
)

_MARK = '\ufeff'
_PLAIN = 'jobs:\n sample:\n  if: x'


def _marked(source):
    """Prepend one stream-start mark to a workflow source."""
    return _MARK + source


def test_a_marked_workflow_reads_like_its_unmarked_self(tmp):
    del tmp
    assert job_scalar(_marked(_PLAIN), 'sample', 'if') == 'x'


def test_a_mark_is_gone_before_lines_are_read(tmp):
    """A marked block body reaches `_decode_block` with the mark removed."""
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
