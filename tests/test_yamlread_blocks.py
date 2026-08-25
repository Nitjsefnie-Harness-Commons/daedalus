#!/usr/bin/env python3
"""Executable contracts for block-scalar whitespace preservation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _yamlread import YAMLReadError, job_scalar  # noqa: E402


def test_deeper_whitespace_is_folded_content(tmp):
    """Whitespace past the content indent remains significant content."""
    del tmp
    body = '      a\n        \n      b\n'
    assert job_scalar(_source('>', body), 'sample', 'if') == 'a\n  \nb\n'
    assert job_scalar(_source('>-', body), 'sample', 'if') == 'a\n  \nb'
    assert job_scalar(_source('>+', body), 'sample', 'if') == 'a\n  \nb\n'
    assert job_scalar(_source('|', body), 'sample', 'if') == 'a\n  \nb\n'
    assert job_scalar(_source('|-', body), 'sample', 'if') == 'a\n  \nb'
    assert job_scalar(_source('|+', body), 'sample', 'if') == 'a\n  \nb\n'


def _source(header, body):
    """Wrap a block body in a named job scalar."""
    return 'jobs:\n  sample:\n    if: ' + header + '\n' + body


def _raises_incomplete(source):
    """Require implicit indentation errors to be refused."""
    try:
        job_scalar(source, 'sample', 'if')
    except YAMLReadError as error:
        assert 'block indentation is incomplete' in str(error), str(error)
        return
    raise AssertionError('invalid implicit block indentation was accepted')


def test_shallow_and_equal_whitespace_are_empty_lines(tmp):
    """Whitespace at or before the indent remains an empty physical line."""
    del tmp
    shallow = '      a\n    \n      b\n'
    equal = '      a\n      \n      b\n'
    empty = '      a\n\n      b\n'
    for body in (shallow, equal, empty):
        assert job_scalar(_source('>', body), 'sample', 'if') == 'a\nb\n'
        assert job_scalar(_source('>-', body), 'sample', 'if') == 'a\nb'
        assert job_scalar(_source('>+', body), 'sample', 'if') == 'a\nb\n'
        assert job_scalar(_source('|', body), 'sample', 'if') == 'a\n\nb\n'
        assert job_scalar(_source('|-', body), 'sample', 'if') == 'a\n\nb'
        assert job_scalar(_source('|+', body), 'sample', 'if') == 'a\n\nb\n'


def test_leading_and_trailing_whitespace_lines_are_preserved(tmp):
    """Leading blanks fold normally and trailing content keeps its spaces."""
    del tmp
    leading = '      \n      a\n'
    assert job_scalar(_source('>', leading), 'sample', 'if') == '\na\n'
    assert job_scalar(_source('>-', leading), 'sample', 'if') == '\na'
    assert job_scalar(_source('|', leading), 'sample', 'if') == '\na\n'
    assert job_scalar(_source('|-', leading), 'sample', 'if') == '\na'

    trailing = '      a\n        '
    assert job_scalar(_source('>', trailing), 'sample', 'if') == 'a\n  '
    assert job_scalar(_source('>-', trailing), 'sample', 'if') == 'a\n  '
    assert job_scalar(_source('>+', trailing), 'sample', 'if') == 'a\n  '
    assert job_scalar(_source('|', trailing), 'sample', 'if') == 'a\n  '
    assert job_scalar(_source('|-', trailing), 'sample', 'if') == 'a\n  '
    assert job_scalar(_source('|+', trailing), 'sample', 'if') == 'a\n  '


def test_implicit_all_whitespace_blocks_follow_chomping(tmp):
    """An implicit block with no visible lines has only its line breaks."""
    del tmp
    body = '    \n      \n'
    assert job_scalar(_source('>', body), 'sample', 'if') == ''
    assert job_scalar(_source('>-', body), 'sample', 'if') == ''
    assert job_scalar(_source('>+', body), 'sample', 'if') == '\n\n'
    assert job_scalar(_source('|', body), 'sample', 'if') == ''
    assert job_scalar(_source('|-', body), 'sample', 'if') == ''
    assert job_scalar(_source('|+', body), 'sample', 'if') == '\n\n'


def test_explicit_indent_controls_whitespace_content(tmp):
    """An explicit indent accepts deeper leading spaces as block content."""
    del tmp
    leading = '        \n      a\n'
    assert job_scalar(_source('>2-', leading), 'sample', 'if') == '  \na'
    assert job_scalar(_source('>2+', leading), 'sample', 'if') == '  \na\n'
    assert job_scalar(_source('|2-', leading), 'sample', 'if') == '  \na'
    assert job_scalar(_source('|2+', leading), 'sample', 'if') == '  \na\n'

    middle = '      a\n        \n      b\n'
    assert job_scalar(_source('>2-', middle), 'sample', 'if') == 'a\n  \nb'
    assert job_scalar(_source('|2-', middle), 'sample', 'if') == 'a\n  \nb'


def test_implicit_wider_leading_whitespace_is_refused(tmp):
    """A wider leading blank makes implicit indentation unsafe."""
    del tmp
    body = '        \n      a\n'
    for header in ('>', '>-', '>+', '|', '|-', '|+'):
        _raises_incomplete(_source(header, body))


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
