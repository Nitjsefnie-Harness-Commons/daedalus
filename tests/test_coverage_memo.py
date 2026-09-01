#!/usr/bin/env python3
"""One file's content is analysed once per run and walked once per analysis.

tests/test_coverage_environment.py scans the whole test tree once per
control, so a run analyses the same `(relative, source)` pair many times
over, and each analysis used to walk the module tree once per pass.
These pin both caches: a repeated scan reuses the earlier analysis, a
reused analysis still declares the keep sites it appended, different
content under one path is a different key, and one analysis walks the
module tree once.
"""
import ast
import gc
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _coverage_guard  # noqa: E402
import _coverage_memo  # noqa: E402
import _util  # noqa: E402

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
# Byte-identical on purpose: two paths carrying one content is what a
# memo keyed on content alone reports twice under the first path.
_TWIN = """import subprocess
subprocess.run(['python3', 'child.py'], cwd=tmp)
"""
# A launch whose env= is a name bound once at module level reaches the
# passes that read assignment targets, mutations and declaration-name
# appearances — three converted call sites a direct keep never reaches.
_BOUND = """# {note}
import subprocess
import _util
ENV = _util.child_coverage('keep', cwd=tmp)
subprocess.run(['python3', 'child.py'], cwd=tmp, env=ENV)
"""


def _tree(tmp, modules):
    """A root holding `modules` under tests/, outside the checkout."""
    root = Path(tmp) / 'repository'
    (root / 'tests').mkdir(parents=True, exist_ok=True)
    for name, source in modules.items():
        (root / 'tests' / name).write_text(source, encoding='utf-8')
    return root


def _recorded_analyses(root, scans, between=None):
    """Scan `root` `scans` times; return the analyses and the results.

    One recorder serves every scan, so a `between` edit is seen by the
    memo as new content under an unchanged analyser rather than as a
    second analyser, which is the only way the content half of the key
    is the thing being tested.
    """
    calls = []
    real = _coverage_guard._analyze

    def recorder(relative, source, keeps):
        calls.append((relative, source))
        return real(relative, source, keeps)

    _coverage_guard._analyze = recorder
    try:
        scan = _coverage_guard._coverage_environment_violations
        results = []
        for index in range(scans):
            if index and between is not None:
                between()
            results.append(scan(root))
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
    """The content is part of the key, so an edited file cannot hit."""
    root = _tree(tmp, {
        'probe_edit.py': _SAFE.format(note='memo edit probe before')})

    def edit():
        (root / 'tests' / 'probe_edit.py').write_text(
            _KEEP.format(note='memo edit probe after'), encoding='utf-8')

    calls, results = _recorded_analyses(root, 2, between=edit)
    assert len(calls) == 2, calls
    assert results[0] != results[1], results


def _module_tree_walks(source):
    """How many times one analysis hands ast.walk a whole module."""
    walked = []
    real = ast.walk

    def counter(node):
        walked.append(node)
        return real(node)

    ast.walk = counter
    try:
        _coverage_guard._analyze('tests/probe_walk.py', source, [])
    finally:
        ast.walk = real
    return len([node for node in walked if isinstance(node, ast.Module)])


def test_a_module_tree_is_walked_once_per_analysis(tmp):
    """Every pass reads one materialised node list, not its own walk."""
    del tmp
    walks = _module_tree_walks(
        _KEEP.format(note='memo walk count probe')
        + _BOUND.format(note='memo walk count bound probe'))
    assert walks == 1, walks


def test_the_materialised_nodes_match_a_fresh_walk(tmp):
    """Same nodes in the same breadth-first order ast.walk yields."""
    del tmp
    tree = ast.parse(_KEEP.format(note='memo walk order probe'))
    assert _coverage_memo.nodes(tree) == list(ast.walk(tree))


def test_a_different_analyser_is_not_served_the_earlier_answer(tmp):
    """The analyser is part of the key, so a swap is a miss."""
    root = _tree(tmp, {
        'probe_analyser.py': _SAFE.format(note='memo analyser probe')})
    _, before = _recorded_analyses(root, 1)
    real = _coverage_guard._analyze

    def stricter(relative, source, keeps):
        return real(relative, source, keeps) + [f'{relative}: stricter']

    _coverage_guard._analyze = stricter
    try:
        after = _coverage_guard._coverage_environment_violations(root)
    finally:
        _coverage_guard._analyze = real
    marker = 'tests/probe_analyser.py: stricter'
    assert marker not in before[0], before
    assert marker in after, after


def test_identical_content_at_two_paths_is_named_at_both(tmp):
    """The path is part of the key, so a twin is not the first file."""
    root = _tree(tmp, {'probe_twin_a.py': _TWIN,
                       'probe_twin_b.py': _TWIN})
    _, results = _recorded_analyses(root, 1)
    named = sorted(line.split(':')[0] for line in results[0]
                   if 'probe_twin' in line)
    assert named == ['tests/probe_twin_a.py',
                     'tests/probe_twin_b.py'], results[0]


def test_the_node_cache_does_not_outlive_the_trees_it_walked(tmp):
    """Nothing the walk stores keeps its own weak key alive."""
    root = _tree(tmp, {
        'probe_weak.py': _BOUND.format(note='memo weak key probe')})
    _recorded_analyses(root, 1)
    gc.collect()
    assert len(_coverage_memo._BELOW) == 0, list(_coverage_memo._BELOW)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
