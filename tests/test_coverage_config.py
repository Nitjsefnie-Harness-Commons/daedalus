#!/usr/bin/env python3
"""Pin coverage discovery configured by the repository."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402


_ENV = _util.coverage_free_environment(os.environ)
_RCFILE = ROOT / 'pyproject.toml'
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
    rows = {}
    for line in report.splitlines():
        columns = line.rsplit(maxsplit=3)
        if len(columns) != 4:
            continue
        name, statements, missed, percent = columns
        if not (statements.isdecimal() and missed.isdecimal()
                and percent.endswith('%')):
            continue
        rows[name] = (int(statements), int(missed), float(percent[:-1]))
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


def test_untracked_node_modules_python_is_not_reported(tmp):
    tmp = Path(tmp)
    _write_executed_files(tmp)
    third_party = tmp / _NODE_MODULE_FILE
    third_party.parent.mkdir(parents=True)
    third_party.write_text(
        'def bundled_tool():\n    return 0\n', encoding='utf-8')

    report = _coverage_report(tmp)
    rows = _coverage_rows(report)

    assert str(_NODE_MODULE_FILE) not in rows, report


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
