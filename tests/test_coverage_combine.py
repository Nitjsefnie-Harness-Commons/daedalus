#!/usr/bin/env python3
"""coverage_combine.py: every parallel file survives, hash collisions included.

`coverage combine` skips a parallel data file whenever its filename-embedded
content hash was already seen -- and equal hashes do not imply equal data
(issue #266). coverage_combine.py replaces it in CI so nothing is dropped.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402

COMBINE_SCRIPT = ROOT / 'scripts' / 'ci' / 'coverage_combine.py'
# Exactly 10 word-characters: coverage's own filename hash slot
# (SUFFIX_PATTERN in coverage/sqldata.py) is fixed-width.
_SAME_HASH = 'abcdef1234'
# These subprocesses build and read fixture data files of their own; letting
# the ambient COVERAGE_PROCESS_START hook measure them would record incidental
# coverage under fixture paths this suite doesn't own.
_ENV = _util.child_coverage('scrub')


def _write_parallel_file(cwd, name_suffix, source_name, lines):
    """Write one real `coverage` parallel data file, deliberately hash-tagged.

    The tag coverage's own classifier reads comes straight from the filename
    (`hash_for_data_file` prefers it over hashing the content), so two files
    can be made to collide without finding a genuine hash collision.
    """
    cwd = Path(cwd)
    script = (
        f"from coverage import CoverageData\n"
        f"d = CoverageData(basename={str(cwd / '.coverage')!r}, "
        f"suffix={name_suffix!r})\n"
        f"d.add_lines({{{str(cwd / source_name)!r}: {list(lines)!r}}})\n"
        f"d.write()\n"
    )
    subprocess.run(
        [sys.executable, '-c', script], cwd=cwd, env=_ENV, check=True,
        capture_output=True, text=True)


def _write_remote_parallel_file(cwd, directory, name_suffix, source, lines):
    """Write data from a runner checkout into an artifact directory."""
    cwd = Path(cwd)
    directory = cwd / directory
    directory.mkdir(parents=True, exist_ok=True)
    script = (
        f"from coverage import CoverageData\n"
        f"d = CoverageData(basename={str(directory / '.coverage')!r}, "
        f"suffix={name_suffix!r})\n"
        f"d.add_lines({{{source!r}: {list(lines)!r}}})\n"
        f"d.write()\n"
    )
    subprocess.run(
        [sys.executable, '-c', script], cwd=cwd, env=_ENV, check=True,
        capture_output=True, text=True)


def test_a_hash_collision_between_parallel_files_is_not_dropped(tmp):
    (Path(tmp) / 'pyproject.toml').write_text(
        '[tool.coverage.run]\nparallel = true\nsource = ["."]\n',
        encoding='utf-8')
    (Path(tmp) / 'a.py').write_text(
        'def f():\n    return 1\n', encoding='utf-8')
    (Path(tmp) / 'b.py').write_text(
        'def g():\n    return 2\n', encoding='utf-8')
    _write_parallel_file(
        tmp, f'hostA.pid111.XaaaaaAx.H{_SAME_HASH}h', 'a.py', [1, 2])
    _write_parallel_file(
        tmp, f'hostB.pid222.XbbbbbBx.H{_SAME_HASH}h', 'b.py', [1, 2])

    result = subprocess.run(
        [sys.executable, str(COMBINE_SCRIPT)], cwd=tmp, env=_ENV,
        capture_output=True, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)

    report = subprocess.run(
        [sys.executable, '-m', 'coverage', 'report', '--ignore-errors'],
        cwd=tmp, env=_ENV, capture_output=True, text=True, check=True)
    assert 'a.py' in report.stdout, report.stdout
    assert 'b.py' in report.stdout, report.stdout

    # Both parallel files consumed like a real `combine` run, not left behind.
    assert not list(Path(tmp).glob('.coverage.*')), list(
        Path(tmp).glob('.coverage.*'))


def test_a_lone_parallel_file_combines_normally(tmp):
    (Path(tmp) / 'pyproject.toml').write_text(
        '[tool.coverage.run]\nparallel = true\nsource = ["."]\n',
        encoding='utf-8')
    (Path(tmp) / 'a.py').write_text(
        'def f():\n    return 1\n', encoding='utf-8')
    _write_parallel_file(
        tmp, f'hostA.pid111.XaaaaaAx.H{_SAME_HASH}h', 'a.py', [1, 2])

    result = subprocess.run(
        [sys.executable, str(COMBINE_SCRIPT)], cwd=tmp, env=_ENV,
        capture_output=True, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)

    report = subprocess.run(
        [sys.executable, '-m', 'coverage', 'report', '--ignore-errors'],
        cwd=tmp, env=_ENV, capture_output=True, text=True, check=True)
    assert 'a.py' in report.stdout, report.stdout


def test_no_parallel_files_is_a_hard_failure(tmp):
    (Path(tmp) / 'pyproject.toml').write_text(
        '[tool.coverage.run]\nparallel = true\nsource = ["."]\n',
        encoding='utf-8')

    result = subprocess.run(
        [sys.executable, str(COMBINE_SCRIPT)], cwd=tmp, env=_ENV,
        capture_output=True, text=True)

    assert result.returncode != 0, (result.stdout, result.stderr)


def test_parallel_files_from_each_os_artifact_are_unioned(tmp):
    """A line covered on either runner is covered in the combined report."""
    root = Path(tmp)
    (root / 'pyproject.toml').write_text(
        '[tool.coverage.run]\nparallel = true\nsource = ["."]\n'
        '[tool.coverage.paths]\nrepo = [".", "*/daedalus/daedalus"]\n',
        encoding='utf-8')
    (root / 'a.py').write_text('first = 1\nsecond = 2\n', encoding='utf-8')
    _write_remote_parallel_file(
        tmp, 'coverage-data-ubuntu-latest',
        'runner.pid111.XaaaaaAx.Habcdef1234h',
        '/synthetic/daedalus/daedalus/a.py', [1])
    _write_remote_parallel_file(
        tmp, 'coverage-data-windows-latest',
        'runner.pid222.XbbbbbBx.Habcdef1234h',
        r'D:\a\daedalus\daedalus\a.py', [2])

    result = subprocess.run(
        [sys.executable, str(COMBINE_SCRIPT),
         str(root / 'coverage-data-ubuntu-latest'),
         str(root / 'coverage-data-windows-latest')],
        cwd=tmp, env=_ENV, capture_output=True, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)

    report = subprocess.run(
        [sys.executable, '-m', 'coverage', 'report', '--ignore-errors'],
        cwd=tmp, env=_ENV, capture_output=True, text=True, check=True)
    assert re.search(r'^a\.py\s+2\s+0\s+100%', report.stdout,
                     re.MULTILINE), report.stdout
    assert not list((root / 'coverage-data-ubuntu-latest').glob(
        '.coverage.*'))
    assert not list((root / 'coverage-data-windows-latest').glob(
        '.coverage.*'))


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
