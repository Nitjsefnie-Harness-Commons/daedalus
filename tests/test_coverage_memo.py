#!/usr/bin/env python3
"""One file's content is analysed once per run, not once per scan.

tests/test_coverage_environment.py scans the whole test tree once per
control, so a run analyses the same `(relative, source)` pair many times
over. These pin the memo that removes the repetition: a repeated scan
reuses the earlier analysis, a reused analysis still declares the keep
sites it appended, and different content under one path is a different
key.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _coverage_guard  # noqa: E402
import _util  # noqa: E402
from _coverage_guard import _coverage_environment_violations  # noqa: E402

# A launch that inherits its cwd and never chdirs is provably safe, so a
# probe module built from this contributes no violations of its own.
_SAFE = """# {note}
import subprocess
subprocess.run(['python3', 'child.py'])
"""
# A keep declaration the allowlist does not name is a violation the guard
# reports from the `keeps` list _analyze appends to, not from its return
# value — which is what a memo that stores only the return value drops.
_KEEP = """# {note}
import subprocess
import _util
subprocess.run(['python3', 'child.py'], cwd=tmp,
               env=_util.child_coverage('keep', cwd=tmp))
"""


def _tree(tmp, modules):
    """A root holding `modules` under tests/, outside the checkout."""
    root = Path(tmp) / 'repository'
    (root / 'tests').mkdir(parents=True, exist_ok=True)
    for name, source in modules.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    return root


def _recorded_analyses(root, scans):
    """Scan `root` `scans` times; return the analyses and the results."""
    calls = []
    real = _coverage_guard._analyze

    def recorder(relative, source, keeps):
        calls.append((relative, source))
        return real(relative, source, keeps)

    _coverage_guard._analyze = recorder
    try:
        results = [_coverage_environment_violations(root)
                   for _ in range(scans)]
    finally:
        _coverage_guard._analyze = real
    return calls, results


def test_a_repeated_scan_reuses_the_earlier_analysis(tmp):
    """Three scans over three files cost three analyses, not nine."""
    root = _tree(tmp, {
        f'probe_reuse_{index}.py': _SAFE.format(
            note=f'memo reuse probe {index}')
        for index in range(3)})
    calls, results = _recorded_analyses(root, 3)
    assert len(calls) == 3, calls
    assert results[0] == results[1] == results[2], results


def test_a_reused_analysis_still_declares_its_keep_sites(tmp):
    """A memo hit replays the keep entries the analysis appended."""
    root = _tree(tmp, {
        'probe_keep.py': _KEEP.format(note='memo keep probe')})
    _, results = _recorded_analyses(root, 2)
    expected = ('tests/probe_keep.py::<module> declares keep without an '
                'allowlist entry')
    assert expected in results[0], results[0]
    assert results[0] == results[1], results


def test_changed_content_under_one_path_is_analysed_again(tmp):
    """The memo is keyed on content, so an edited file cannot hit."""
    root = _tree(tmp, {
        'probe_edit.py': _SAFE.format(note='memo edit probe before')})
    before_calls, before = _recorded_analyses(root, 1)
    (root / 'tests' / 'probe_edit.py').write_text(
        _KEEP.format(note='memo edit probe after'), encoding='utf-8')
    after_calls, after = _recorded_analyses(root, 1)
    assert len(before_calls) == 1, before_calls
    assert len(after_calls) == 1, after_calls
    assert before != after, (before, after)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
