#!/usr/bin/env python3
"""Executable contracts for the workflow block-scalar reader.

Every expected value is what PyYAML 6.0.3 decodes for the same document, so
these tables record the YAML specification rather than this reader.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))

from workflow_yaml import (  # noqa: E402
    YAMLReadError, workflow_step_items,
)
from yamlblock import (  # noqa: E402
    block_end, decode_block, parse_block_header,
)
from yamlscalar import text_indent  # noqa: E402


def _decode(header, body, parent_indent=0):
    pairs = [(line.rstrip('\n'), line.endswith('\n'))
             for line in body.splitlines(keepends=True)]
    return decode_block(
        pairs, parent_indent, parse_block_header(header, 'step name'),
        'step name')


def _table(cases):
    for header, body, decoded in cases:
        assert _decode(header, body) == decoded, (header, body)


def _refused(call, detail):
    try:
        call()
    except YAMLReadError as error:
        assert detail in str(error), str(error)
        return
    raise AssertionError(f'a shape {detail!r} must refuse was accepted')


def _step(field, body='', tail='     if: z\n'):
    return f'jobs:\n sample:\n  steps:\n   - {field}\n{body}{tail}'


def _fields(source):
    items = workflow_step_items(source, 'sample')
    assert items is not None, source
    return [(item.name, item.identity) for item in items]


def test_the_style_joins_the_lines_and_chomping_ends_them(tmp):
    del tmp
    _table([
        ('|', '  one\n  two\n', 'one\ntwo\n'),
        ('>', '  one\n  two\n', 'one two\n'),
        ('|', '  one\n\n\n', 'one\n'),
        ('|-', '  one\n\n\n', 'one'),
        ('|+', '  one\n\n\n', 'one\n\n\n'),
        ('>', '  one\n\n\n', 'one\n'),
        ('>-', '  one\n\n\n', 'one'),
        ('>+', '  one\n\n\n', 'one\n\n\n'),
        ('|', '  one\n  two', 'one\ntwo'),
        ('|+', '  one\n  two', 'one\ntwo'),
        ('>', '  one\n  two', 'one two'),
    ])


def test_the_content_indent_comes_from_the_header_or_the_first_line(tmp):
    del tmp
    _table([
        ('|2', '  one\n   two\n', 'one\n two\n'),
        ('|2-', '  one\n   two\n', 'one\n two'),
        ('|-2', '  one\n   two\n', 'one\n two'),
        ('>2', '  one\n  two\n', 'one two\n'),
        ('>-2', '  one\n  two\n', 'one two'),
        ('|4', '    one\n     two\n', 'one\n two\n'),
        ('|', '    one\n     two\n', 'one\n two\n'),
        ('|', '\n  one\n', '\none\n'),
        ('>', '\n\n  one\n', '\n\none\n'),
    ])


def test_blank_and_space_only_lines_decode_by_their_width(tmp):
    del tmp
    _table([
        ('|', '  one\n\n  two\n', 'one\n\ntwo\n'),
        ('|+', '\n  one\n\n', '\none\n\n'),
        ('|', '  one\n    \n  two\n', 'one\n  \ntwo\n'),
        ('|', '  one\n  \n  two\n', 'one\n\ntwo\n'),
        ('>', '  one\n    \n  two\n', 'one\n  \ntwo\n'),
        ('>', '  one\n  \n  two\n', 'one\ntwo\n'),
    ])


def test_an_empty_or_blank_only_body_keeps_only_what_chomping_keeps(tmp):
    del tmp
    _table([
        ('|', '', ''),
        ('|+', '', ''),
        ('>-', '', ''),
        ('|', '\n\n', ''),
        ('|-', '\n\n', ''),
        ('|+', '\n\n', '\n\n'),
        ('>+', '\n\n', '\n\n'),
    ])


def test_a_folded_block_keeps_the_breaks_it_may_not_fold(tmp):
    del tmp
    _table([
        ('>', '  one\n    deep\n  two\n', 'one\n  deep\ntwo\n'),
        ('>', '  one\n    d1\n    d2\n  two\n', 'one\n  d1\n  d2\ntwo\n'),
        ('>2', '   deep\n  one\n', ' deep\none\n'),
        ('>', '  one\n\n  two\n', 'one\ntwo\n'),
        ('>', '  one\n\n\n  two\n', 'one\n\ntwo\n'),
        ('>', '  one\n\n\n\n  two\n', 'one\n\n\ntwo\n'),
        ('>', '  one\n    deep\n\n  two\n', 'one\n  deep\n\ntwo\n'),
        ('>', '  one\n\n    deep\n  two\n', 'one\n\n  deep\ntwo\n'),
    ])


def test_a_header_outside_the_grammar_is_not_a_header(tmp):
    del tmp
    for header in ('|x', '||', '>-+', 'one', '|2 ', ''):
        assert parse_block_header(header, 'step name') is None, header
    _refused(lambda: parse_block_header('|0', 'step name'),
             'step name has an unsupported block header')
    _refused(lambda: parse_block_header('|2-3', 'step name'),
             'step name has two indentation indicators')


def test_a_body_short_of_the_content_indent_is_refused(tmp):
    del tmp
    _refused(lambda: _decode('|', '    \n  one\n'),
             'step name block indentation is incomplete')
    _refused(lambda: _decode('|', '  one\n one\n'),
             'step name block indentation is incomplete')
    assert text_indent('   one') == 3
    _refused(lambda: text_indent('\tone'),
             'tabs in YAML indentation are unsupported')


def test_a_block_ends_at_the_first_line_it_no_longer_covers(tmp):
    del tmp
    texts = ['key: |', '  one', '', '  two', 'next: 1']
    assert block_end(texts, 1, 5, 0, parse_block_header('|', 'x')) == 4
    assert block_end(texts, 1, 5, 0, parse_block_header('|3', 'x')) == 1
    assert block_end(texts[:4], 1, 4, 0,
                     parse_block_header('|', 'x')) == 4
    assert block_end(['key: |', '', ''], 1, 3, 0,
                     parse_block_header('|', 'x')) == 3
    assert block_end(['key: |', 'next: 1'], 1, 2, 0,
                     parse_block_header('|', 'x')) == 1
    assert block_end(['key: |', '', 'next: 1'], 1, 3, 0,
                     parse_block_header('|', 'x')) == 2


def test_a_step_name_and_id_may_be_written_as_a_block_scalar(tmp):
    del tmp
    assert _fields(_step('name: |', '      one\n      two\n')) == [
        ('one\ntwo\n', None)]
    assert _fields(_step('id: >-', '      one\n      two\n')) == [
        (None, 'one two')]


def test_the_dash_may_be_separated_by_any_separation_width(tmp):
    del tmp
    source = ('jobs:\n sample:\n  steps:\n'
              '   -  name: one\n      if: z\n'
              '   -   name: two\n       if: z\n')
    assert _fields(source) == [('one', None), ('two', None)]


def test_a_block_scalar_step_field_does_not_swallow_the_next_field(tmp):
    """A block on a dash line is indented from the mapping, not the dash."""
    del tmp
    assert _fields(_step('name: |2', '       one\n', '     id: gate\n')) == [
        ('one\n', 'gate')]
    assert _fields(_step('name: |', '\n', '     id: gate\n')) == [
        ('', 'gate')]


def test_a_kept_blank_body_before_another_field_keeps_its_breaks(tmp):
    del tmp
    assert _fields(_step('name: |+', '\n\n')) == [('\n\n', None)]


def test_a_step_line_short_of_its_own_indentation_is_refused(tmp):
    del tmp
    _refused(lambda: workflow_step_items(
        _step('name: one', '', '    bad: 1\n'), 'sample'),
        'step mapping has inconsistent indentation')
    _refused(lambda: workflow_step_items(
        'jobs:\n sample:\n  steps:\n       - name: one\n    bad: 1\n',
        'sample'),
        'step sequence has inconsistent indentation')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
