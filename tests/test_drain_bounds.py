#!/usr/bin/env python3
"""No stop-then-drain in this tree leaves its child undrained.

The property, the spelling surface and the shapes the guard does not
follow are the recognition claim on `tests/_drain_scan.py`, which is
where the analysis lives. These tests hold that analysis to the tree.
"""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _drain_scan as scan  # noqa: E402
import _util  # noqa: E402
from _drain_scan import _SITES, _UNBOUNDED_WITHOUT_STOP  # noqa: E402
from _repo import ROOT  # noqa: E402


def test_every_stop_then_drain_carries_a_bound(tmp):
    del tmp
    violations = scan._tree_violations(ROOT)
    assert not violations, '\n'.join(violations)


def test_the_scan_reads_exactly_the_tracked_python_tree(tmp):
    """A file Git does not list is one the scan never reads."""
    del tmp
    listed = subprocess.run(
        ['git', '-C', str(ROOT), 'ls-files', '-z', '*.py'],
        capture_output=True, check=True, timeout=60)
    tracked = {os.fsdecode(name) for name in listed.stdout.split(b'\0')
               if name}
    assert tracked, 'Git returned no tracked Python paths'
    scanned = {path.relative_to(ROOT).as_posix()
               for path in scan._python_sources(ROOT)}
    assert scanned == tracked, sorted(scanned ^ tracked)


def test_each_real_site_is_caught_when_its_bound_is_removed(tmp):
    for index, site in enumerate(_SITES):
        root = Path(tmp, f'site{index}')
        scan._scratch_git_tree(root)
        expected = scan._planted(root, site)
        violations = scan._tree_violations(root)
        assert any(entry.startswith(expected) for entry in violations), (
            site[0], expected, violations)


def test_a_scratch_tree_is_clean_before_a_bound_is_removed(tmp):
    """The plants scratch tree, with nothing planted in it."""
    root = Path(tmp, 'pristine')
    scan._scratch_git_tree(root)
    assert not scan._tree_violations(root), scan._tree_violations(root)


def test_a_bounded_drain_is_clean(tmp):
    del tmp
    assert scan._synthetic("""def reap(proc):
    proc.kill()
    proc.wait(timeout=10)
""") == []


def test_the_bounded_helper_is_clean(tmp):

    del tmp
    relative = 'tests/_drain.py'
    source = (ROOT / relative).read_text(encoding='utf-8')
    assert 'process.kill()\n    try:\n        out, err = process.' \
        'communicate(timeout=drain_timeout)' in source, source
    assert scan._analyze(relative, source) == []


def test_an_alias_is_followed_to_its_receiver(tmp):
    del tmp
    violations = scan._synthetic("""def reap(process):
    p = process
    p.kill()
    p.communicate()
""")
    expected = ('tests/synthetic.py:4: p.communicate() drains process with '
                'no timeout= bound, after the stop at line 3')
    assert violations == [expected]


def test_an_alias_chain_is_read_to_a_fixpoint(tmp):
    del tmp
    violations = scan._synthetic("""def reap(proc):
    a = proc
    b = a
    q = b
    q.kill()
    q.wait()
""")
    expected = ('tests/synthetic.py:6: q.wait() drains proc with no '
                'timeout= bound, after the stop at line 5')
    assert violations == [expected]


def test_a_drain_of_another_object_is_clean(tmp):
    del tmp
    assert scan._synthetic("""def reap(proc):
    proc.kill()
    other.wait()
""") == []


def test_a_drain_before_the_stop_is_clean(tmp):
    del tmp
    assert scan._synthetic("""def reap(proc):
    proc.communicate()
    proc.kill()
""") == []


def test_a_kwargs_spread_is_not_a_bound(tmp):
    del tmp
    violations = scan._synthetic("""def reap(proc, options):
    proc.kill()
    proc.communicate(**options)
""")
    expected = ('tests/synthetic.py:3: proc.communicate(**options) drains '
                'proc with no timeout= bound, after the stop at line 2')
    assert violations == [expected]


def test_a_computed_stop_is_refused(tmp):
    del tmp
    violations = scan._synthetic("""def reap(proc):
    getattr(proc, 'kill')()
    proc.wait(timeout=10)
""")
    expected = ("tests/synthetic.py:2: getattr(proc, 'kill')() names a "
                'member of proc, and cannot be shown to carry a timeout= '
                'bound')
    assert violations == [expected]


def test_a_computed_drain_is_refused(tmp):
    del tmp
    violations = scan._synthetic("""def reap(proc):
    proc.kill()
    proc['wait']()
""")
    expected = ("tests/synthetic.py:3: proc['wait']() names a member of "
                'proc, and cannot be shown to carry a timeout= bound')
    assert violations == [expected]


def test_a_computed_member_of_a_judged_object_is_refused(tmp):
    del tmp
    violations = scan._synthetic("""def reap(proc, key):
    proc.kill()
    proc[key]()
""")
    expected = ('tests/synthetic.py:3: proc[key]() names a member of proc, '
                'and cannot be shown to carry a timeout= bound')
    assert violations == [expected]


def test_a_computed_member_of_an_unread_object_is_clean(tmp):
    del tmp
    assert scan._synthetic("""def reap(proc, table, key):
    proc.kill()
    table[key]()
""") == []


def test_a_bound_reached_through_a_parameter_name_is_clean(tmp):
    del tmp
    assert scan._synthetic("""def reap(proc, limit):
    proc.kill()
    proc.communicate(timeout=limit)
""") == []


def test_a_name_bound_twice_stands_for_itself(tmp):
    del tmp
    assert scan._synthetic("""def reap(proc, other):
    proc = other
    proc.kill()
    proc.wait(timeout=10)
""") == []


def test_a_cyclic_alias_is_unreadable(tmp):
    del tmp
    assert scan._synthetic("""def reap(proc):
    a = b
    b = a
    proc.kill()
    a.wait()
""") == []


def test_a_stop_in_another_scope_is_not_a_stop_here(tmp):
    """The `tests/_speedharness.py` shape: joined by a call, not a scope."""
    del tmp
    assert scan._synthetic("""def stop(process):
    process.kill()


def drain(process):
    process.wait()
""") == []


def test_the_real_cross_scope_shape_is_the_speedharness(tmp):

    del tmp
    relative = 'tests/_speedharness.py'
    source = (ROOT / relative).read_text(encoding='utf-8')
    tree = ast.parse(source)
    lines = source.splitlines()
    stops = drains = None
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        if function.name == '_kill_process_tree':
            stops = function
        elif function.name == '_reap_process':
            drains = function
    assert stops is not None and drains is not None
    assert any('process.kill()' in lines[node.lineno - 1]
               for node in ast.walk(stops)
               if isinstance(node, ast.Call)), lines
    assert any('process.wait(timeout=_CLEANUP_TIMEOUT)'
               in lines[node.lineno - 1]
               for node in ast.walk(drains)
               if isinstance(node, ast.Call)), lines
    assert scan._analyze(relative, source) == []


def test_a_non_kill_unbounded_drain_is_not_flagged(tmp):

    for relative, drain in _UNBOUNDED_WITHOUT_STOP:
        source = (ROOT / relative).read_text(encoding='utf-8')
        assert source.count(drain) >= 1, relative
        assert scan._analyze(relative, source) == [], relative


def test_the_false_positive_control_still_catches_a_stop(tmp):
    """A control only means something if a stop beside it is still caught."""
    for relative, drain in _UNBOUNDED_WITHOUT_STOP:
        source = (ROOT / relative).read_text(encoding='utf-8')
        receiver = drain[:drain.index('.')]
        lead = source[:source.index(drain)].rsplit('\n', 1)[-1]
        planted = source.replace(
            drain, f'{receiver}.kill()\n{lead}{drain}', 1)
        assert scan._analyze(relative, planted), relative


def test_a_walrus_target_is_followed_to_its_receiver(tmp):

    del tmp
    violations = scan._synthetic("""def reap(proc):
    if (p := proc):
        p.terminate()
    proc.wait()
""")
    expected = ('tests/synthetic.py:4: proc.wait() drains proc with no '
                'timeout= bound, after the stop at line 3')
    assert violations == [expected], violations


def test_a_for_target_is_followed_to_its_receiver(tmp):

    del tmp
    violations = scan._synthetic("""def reap(proc):
    for p in (proc,):
        p.terminate()
    proc.wait()
""")
    expected = ('tests/synthetic.py:4: proc.wait() drains proc with no '
                'timeout= bound, after the stop at line 3')
    assert violations == [expected], violations


def test_a_with_target_is_followed_to_its_receiver(tmp):
    """`with managed as p` binds `p`, and the fixpoint reaches the receiver."""
    del tmp
    violations = scan._synthetic("""def reap(process):
    managed = process
    with managed as p:
        p.terminate()
    process.wait()
""")
    expected = ('tests/synthetic.py:5: process.wait() drains process with no '
                'timeout= bound, after the stop at line 4')
    assert violations == [expected], violations


_STOP_NAMED = re.compile(r'after the stop at line (\d+)$')


def test_each_aliased_binding_form_is_caught_in_a_real_module(tmp):
    """The three `=`-less forms, planted into a real converted site.

    The refusal must name the ALIASED stop. A drain that the same scope
    could have caught through an earlier stop on the receiver would pass
    this loop without the alias being read at all, which is a false green
    rather than teeth.
    """
    for index, (form, relative, bounded, aliased) in enumerate(
            scan._ALIASED):
        root = Path(tmp, f'aliased{index}')
        scan._scratch_git_tree(root)
        expected = scan._planted(root, (relative, bounded, aliased))
        mutated = (root / relative).read_text(encoding='utf-8')
        assert aliased in mutated, (form, relative)
        lines = mutated.splitlines()
        caught = [entry for entry in scan._tree_violations(root)
                  if entry.startswith(expected)]
        assert caught, (form, relative, expected,
                        scan._tree_violations(root))
        named = _STOP_NAMED.search(caught[0])
        assert named is not None, caught[0]
        stop = int(named.group(1))
        assert 'p.terminate()' in lines[stop - 1], (
            form, relative, stop, lines[stop - 1])


def test_a_loop_target_over_a_collection_is_not_an_alias(tmp):
    """`for p in procs` binds each handle, never the collection itself."""
    del tmp
    assert scan._synthetic("""def reap(proc, procs):
    for p in procs:
        p.terminate()
    proc.wait()
""") == []


def test_a_comprehension_runs_its_conditions_before_its_element(tmp):
    """`[p.wait() for p in procs if p.kill()]` kills, then drains."""
    del tmp
    violations = scan._synthetic("""def reap(procs):
    return [p.wait() for p in procs if p.kill()]
""")
    expected = ('tests/synthetic.py:2: p.wait() drains p with no timeout= '
                'bound, after the stop at line 2')
    assert violations == [expected], violations


def test_a_name_bound_twice_leaves_an_unbounded_drain_unread(tmp):
    """The declared twice-bound gap, in the unbounded case it matters for."""
    del tmp
    assert scan._synthetic("""def reap(proc, other):
    proc = other
    proc = other
    proc.kill()
    other.wait()
""") == []


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
