#!/usr/bin/env python3
"""Direct boundary coverage for the patch coverage reporter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))
import diff_coverage  # noqa: E402


def test_decode_git_path_keeps_a_final_unmatched_backslash(tmp):
    """A quoted path ending in a backslash retains that byte."""
    del tmp
    assert diff_coverage._decode_git_path('"b/end\\') == 'end\\'


def test_decode_git_path_decodes_the_tab_escape(tmp):
    """Git's one-character tab escape becomes a tab in the source path."""
    del tmp
    assert diff_coverage._decode_git_path('"b/a\\tb"') == 'a\tb'


def test_measure_skips_added_lines_without_statement_records(tmp):
    """A measured file with no touched records produces no coverage row."""
    del tmp
    assert diff_coverage.measure({'x.py': {1: 1}}, {'x.py': {9}}) == (
        [], 0, 0)


def test_executable_lines_skips_a_nameless_class_before_a_usable_one(tmp):
    """A class without a filename cannot invalidate a later usable class."""
    coverage_xml = Path(tmp) / 'classes.xml'
    coverage_xml.write_text(
        '<coverage><classes>'
        '<class><lines><line number="1" hits="1"/></lines></class>'
        '<class filename="usable.py"><lines>'
        '<line number="1" hits="1"/>'
        '</lines></class>'
        '</classes></coverage>\n',
        encoding='utf-8')
    assert diff_coverage.executable_lines(coverage_xml) == {
        'usable.py': {1: 1}}


def test_executable_lines_refuses_coordinate_zero(tmp):
    """Cobertura coordinates must be positive source-line numbers."""
    coverage_xml = Path(tmp) / 'zero.xml'
    coverage_xml.write_text(
        '<coverage><class filename="zero.py"><lines>'
        '<line number="0" hits="0"/>'
        '</lines></class></coverage>\n',
        encoding='utf-8')
    try:
        diff_coverage.executable_lines(coverage_xml)
    except ValueError as error:
        assert str(error) == (
            "invalid line number for zero.py: '0' (must be positive)")
    else:
        raise AssertionError('coordinate zero was accepted')


def test_validate_statement_records_refuses_an_omitted_statement(tmp):
    """Every executable added statement must have an XML record."""
    source = Path(tmp) / 'statements.py'
    source.write_text('first = 1\nsecond = 2\n', encoding='utf-8')
    measured = {str(source): {1: 1}}
    added = {str(source): {1, 2}}
    try:
        diff_coverage.validate_statement_records(measured, added)
    except ValueError as error:
        assert str(error) == (
            f'missing executable statement records for {source}: 2')
    else:
        raise AssertionError('an omitted statement record was accepted')


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
