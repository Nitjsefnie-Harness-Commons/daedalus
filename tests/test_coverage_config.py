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
_NODE_MODULE_FILE = 'node_modules/arbitrary_dependency/python/vendor_tool.py'


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


def test_unexecuted_python_in_nested_non_package_is_reported(tmp_path):
    _write_executed_files(tmp_path)
    unexecuted = tmp_path / 'pkgless' / 'deeper' / 'never_run.py'
    unexecuted.parent.mkdir(parents=True)
    unexecuted.write_text(
        'def never_run():\n    return 0\n', encoding='utf-8')

    report = _coverage_report(tmp_path)

    assert 'pkgless/deeper/never_run.py' in report, report


def test_untracked_node_modules_python_is_not_reported(tmp_path):
    _write_executed_files(tmp_path)
    third_party = tmp_path / _NODE_MODULE_FILE
    third_party.parent.mkdir(parents=True)
    third_party.write_text(
        'def bundled_tool():\n    return 0\n', encoding='utf-8')

    report = _coverage_report(tmp_path)

    assert _NODE_MODULE_FILE not in report, report


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
