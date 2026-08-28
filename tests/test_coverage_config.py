#!/usr/bin/env python3
"""Pin coverage discovery configured by the repository."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_ENV = _util.child_coverage('scrub')
_RCFILE = ROOT / 'pyproject.toml'
# Independent roots stop a source entry for one fixture satisfying both.
_UNEXECUTED_FILES = (
    Path('orphaned_area') / 'never_reached.py',
    Path('miscellaneous_tree') / 'branch' / 'deeper'
    / 'also_never_reached.py',
)
_NODE_MODULE_FILE = (
    Path('node_modules') / 'arbitrary_dependency' / 'python'
    / 'vendor_tool.py')


def _coverage_report(cwd):
    commands = (
        [sys.executable, '-m', 'coverage', 'run',
         f'--rcfile={_RCFILE}', '--parallel-mode', 'driver.py'],
        [sys.executable, '-m', 'coverage', 'combine',
         f'--rcfile={_RCFILE}'],
        [sys.executable, '-m', 'coverage', 'report',
         f'--rcfile={_RCFILE}'],
    )
    for command in commands[:-1]:
        subprocess.run(
            command, cwd=cwd, env=_ENV, capture_output=True, text=True,
            check=True)
    return subprocess.run(
        commands[-1], cwd=cwd, env=_ENV, capture_output=True, text=True,
        check=True).stdout


def _write_executed_files(tmp):
    (tmp / 'driver.py').write_text(
        'import ran\nran.run()\n', encoding='utf-8')
    (tmp / 'ran.py').write_text(
        'def run():\n    return 1\n', encoding='utf-8')


def _coverage_rows(report):
    lines = report.splitlines()
    assert lines and lines[0].split() == [
        'Name', 'Stmts', 'Miss', 'Cover'], report
    rows = {}
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped or not stripped.strip('-'):
            continue
        columns = line.rsplit(maxsplit=3)
        assert len(columns) == 4, (
            f'unparsed coverage row: {line!r}\n{report}')
        name, statements, missed, percent = columns
        assert statements.isdecimal() and missed.isdecimal(), (
            f'unparsed coverage row: {line!r}\n{report}')
        assert percent.endswith('%'), (
            f'unparsed coverage row: {line!r}\n{report}')
        try:
            percentage = float(percent[:-1])
        except ValueError as error:
            raise AssertionError(
                f'unparsed coverage row: {line!r}\n{report}') from error
        rows[name] = (int(statements), int(missed), percentage)
    return rows


def test_unexecuted_python_in_nested_non_package_is_reported(tmp):
    tmp = Path(tmp)
    _write_executed_files(tmp)
    for relative_path in _UNEXECUTED_FILES:
        unexecuted = tmp / relative_path
        unexecuted.parent.mkdir(parents=True)
        unexecuted.write_text(
            'def never_run():\n    return 0\n', encoding='utf-8')

    report = _coverage_report(tmp)
    rows = _coverage_rows(report)

    for relative_path in _UNEXECUTED_FILES:
        assert rows.get(str(relative_path)) == (2, 2, 0.0), report


def test_node_modules_python_is_omitted_from_the_report(tmp):
    tmp = Path(tmp)
    _write_executed_files(tmp)
    third_party = tmp / _NODE_MODULE_FILE
    third_party.parent.mkdir(parents=True)
    third_party.write_text(
        'def bundled_tool():\n    return 0\n', encoding='utf-8')

    report = _coverage_report(tmp)
    rows = _coverage_rows(report)

    assert rows.get('driver.py') == (2, 0, 100.0), report
    assert rows.get('ran.py') == (2, 0, 100.0), report
    assert str(_NODE_MODULE_FILE) not in rows, report


def test_wrapped_forbidden_row_cannot_satisfy_negative_control(tmp):
    report = (
        'Name        Stmts   Miss  Cover\n'
        '-------------------------------\n'
        'driver.py       2      0   100%\n'
        f'{_NODE_MODULE_FILE.parent}{os.sep}\n'
        f'{_NODE_MODULE_FILE.name}       2      2     0%\n'
        'ran.py          2      0   100%\n'
        '-------------------------------\n'
        'TOTAL           6      2    67%\n')
    original_report = globals()['_coverage_report']
    globals()['_coverage_report'] = lambda _cwd: report
    try:
        try:
            test_node_modules_python_is_omitted_from_the_report(tmp)
        except AssertionError as error:
            assert 'unparsed coverage row' in str(error), error
            return
        raise AssertionError(
            'the negative control accepted a wrapped forbidden row')
    finally:
        globals()['_coverage_report'] = original_report


def test_whitespace_only_report_lines_are_ignored(tmp):
    del tmp
    report = (
        'Name        Stmts   Miss  Cover\n'
        '-------------------------------\n'
        'driver.py       2      0   100%\n'
        '   \n'
        'ran.py          2      0   100%\n'
        '-------------------------------\n'
        'TOTAL           4      0   100%\n')

    rows = _coverage_rows(report)

    assert rows.get('driver.py') == (2, 0, 100.0), rows
    assert rows.get('ran.py') == (2, 0, 100.0), rows


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
