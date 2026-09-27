#!/usr/bin/env python3
"""Which launcher a fixed-unit-of-work Node child is started with.

A Node child whose real cost is a fixed unit of work is bounded by the
shared hang detector in `tests/_noderun.py`, not by a number typed at its
call site: a wall-clock literal measures the runner's busyness and nothing
else, and that is what failed correct children on a loaded CI runner. So
every such launch goes through the shared primitive, and loses its
`timeout=`.

This is a separate suite from `tests/test_harness_launch_bounds.py` because
the two subjects are different and the size ceiling would not hold both:
that suite holds how a number may reach a child THROUGH the census's own
routes, and this one holds which launcher a call site picks at all. It
lives here for the same reason the repository relocates rather than
shrinks when a file passes its ceiling.

The population is DERIVED, never listed. A control that named the fifteen
sites it was written against would go green the moment a sixteenth
appeared, which is the one thing a sweep has to prevent. The only named set
is the boundary below, and every member of it is separately required to
still bound its own child, so an exemption cannot quietly become "neither
routed nor bounded", which is a hole rather than a boundary.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent


# A Node child whose cost is a fixed unit of work goes through the shared
# detector. The modules below are the boundary: their child is NOT a fixed
# unit of work, or its expiry is already classified, so the detector is the
# wrong bound for them and they keep a bound of their own. Each entry
# carries its reason, because a bare module name is a list rather than a
# justification, and a boundary nobody can explain is a boundary that
# grows.
CLASSIFYING_MODULES = {
    '_dashnode.py': 'scales its own bound per retry attempt',
    '_gm_harness.py': 'a real-browser storage boundary (task 3)',
    '_overlap.py': 'its expiry is already classified by the harness',
    '_realbrowser.py': 'a real-browser probe with a composed bound (task 3)',
    '_realbrowser_workers.py': 'a CDP call whose bound IS the response '
                               'deadline it asserts (task 3)',
    'test_real_browser_classification.py': 'a real-browser probe (task 3)',
    'test_real_browser_environment.py': 'a real-browser probe (task 3)',
    'test_real_browser_harness.py': 'a real-browser probe (task 3)',
}


def _argv_starts_a_node(argv, assigned, depth=0):
    """Whether a launch's executable is the one `which('node')` found.

    The executable is resolved through the module's own assignments rather
    than matched on a spelling, so a launch whose argv is bound to a local
    is still seen, and a call that merely happens to be called `run` on
    something that is not the subprocess module places no child at all.
    """
    if isinstance(argv, (ast.List, ast.Tuple)) and argv.elts:
        return _argv_starts_a_node(argv.elts[0], assigned, depth)
    if depth < 6 and isinstance(argv, ast.Name):
        return any(_argv_starts_a_node(value, assigned, depth + 1)
                   for value in reversed(assigned.get(argv.id, [])))
    if isinstance(argv, ast.Call):
        # `shutil.which('node')` is an attribute call and a bare
        # `which('node')` is a name call; both spellings are in the tree.
        if 'which' in (getattr(argv.func, 'id', None),
                       getattr(argv.func, 'attr', None)):
            return any(
                isinstance(arg, ast.Constant) and arg.value == 'node'
                for arg in argv.args)
        return _argv_starts_a_node(
            argv.args[0], assigned, depth) if argv.args else False
    if isinstance(argv, ast.Name):
        return argv.id == 'node'
    return False


def _node_launches(tree):
    """Every `(line, deadline)` at which this module starts a Node child.

    Whether a call places a child is the repository's own predicate rather
    than a second one written here, because a name-matching filter also
    matches `_ModuleJudgement(...).run`, and every such false positive is a
    hole a real launcher could hide behind.
    """
    assigned = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)
    receivers = census._subprocess_receivers(tree)
    direct = census._from_import_launches(tree)
    aliases = census._member_aliases(tree, receivers, direct)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not census._is_launch(node, receivers, direct, aliases):
            continue
        argv = node.args[0] if node.args else next(
            (k.value for k in node.keywords if k.arg in ('args', 'argv')),
            None)
        if argv is None or not _argv_starts_a_node(argv, assigned):
            continue
        deadline = next(
            (ast.unparse(k.value) for k in node.keywords
             if k.arg == 'timeout'),
            None)
        found.append((node.lineno, deadline))
    return sorted(found)


def _bounds_its_own_child(tree, line, launch_deadline):
    """Whether a classifying module bounds this child somewhere.

    Not only at the launch: `_dashnode.py` and `_overlap.py` launch with
    `Popen` and bound the `communicate` that waits for the child, which is
    the same property arriving one call later. Asserting the launch keyword
    alone was a false red on both. Reading a child's wait a second way here
    would duplicate the repository's own reader, so this asks the narrow
    question instead: whether a `timeout=` keyword appears anywhere in the
    function the launch sits in.
    """
    if launch_deadline is not None:
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.lineno <= line <= (node.end_lineno or node.lineno):
            continue
        return any(
            isinstance(inner, ast.Call)
            and any(kw.arg == 'timeout' for kw in inner.keywords)
            for inner in ast.walk(node))
    return False


def test_every_fixed_work_node_child_goes_through_the_shared_detector(tmp):
    """The rule, read off the tree rather than off a list of sites."""
    del tmp
    unrouted, unbounded = [], []
    for path in sorted(TESTS.glob('*.py')):
        if path.name in (Path(__file__).name, '_noderun.py'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        launches = _node_launches(tree)
        if not launches:
            continue
        if path.name in CLASSIFYING_MODULES:
            unbounded += [f'{path.name}:{line}' for line, deadline in launches
                          if not _bounds_its_own_child(tree, line, deadline)]
            continue
        unrouted += [f'{path.name}:{line} (timeout={deadline})'
                     for line, deadline in launches]
    assert not unrouted, (
        'a Node child whose cost is a fixed unit of work is launched '
        'outside the shared hang detector:\n' + '\n'.join(unrouted))
    assert not unbounded, (
        'a classifying module left its child with no bound at all:\n'
        + '\n'.join(unbounded))


def test_the_sweep_walk_can_still_see_a_node_launch(tmp):
    """Liveness of the population: a walk that sees nothing proves nothing.

    The domain is derived by resolving an executable through assignments, so
    a walk that lost that resolution would report a clean tree. This is the
    marker that must agree with the sweep: both shapes a real module is
    written in are planted, and the sweep's own reader has to find both.
    """
    del tmp
    planted = (
        'import shutil\n'
        'import subprocess\n'
        'def launch():\n'
        "    node = shutil.which('node')\n"
        "    return subprocess.run([node, 'child.js'], timeout=30)\n")
    assert _node_launches(ast.parse(planted)) == [(5, '30')]
    bound_to_a_local = (
        'import shutil\n'
        'import subprocess\n'
        'def launch():\n'
        "    node = shutil.which('node')\n"
        "    argv = [node, '-e', 'source']\n"
        '    return subprocess.run(argv, timeout=60)\n')
    assert _node_launches(ast.parse(bound_to_a_local)) == [(6, '60')]
    not_a_launch = (
        'class Judgement:\n'
        '    def run(self):\n'
        '        return self\n'
        'def launch(node):\n'
        "    return Judgement().run([node, 'child.js'], timeout=30)\n")
    assert not _node_launches(ast.parse(not_a_launch))
    python_child = (
        'import sys\n'
        'import subprocess\n'
        'def launch():\n'
        '    return subprocess.run([sys.executable, "suite.py"],\n'
        '                          timeout=30)\n')
    assert not _node_launches(ast.parse(python_child))


def test_the_boundary_direction_can_still_tell_bounded_from_unbounded(tmp):
    """Liveness of the second direction, in both of its spellings.

    A classifying module is exempt from the routing rule, so the only thing
    stopping the exemption from becoming "no bound anywhere" is this check.
    Two spellings are planted because the tree uses both: the bound on the
    launch, and the bound on the wait that follows a `Popen`.
    """
    del tmp
    on_the_launch = (
        'import shutil\n'
        'import subprocess\n'
        'def launch():\n'
        "    node = shutil.which('node')\n"
        "    return subprocess.run([node, 'child.js'], timeout=30)\n")
    assert _bounds_its_own_child(ast.parse(on_the_launch), 5, '30')
    on_the_wait = (
        'import shutil\n'
        'import subprocess\n'
        'def launch():\n'
        "    node = shutil.which('node')\n"
        "    child = subprocess.Popen([node, 'child.js'])\n"
        '    return child.communicate(timeout=30)\n')
    assert _bounds_its_own_child(ast.parse(on_the_wait), 5, None)
    with_nothing = (
        'import shutil\n'
        'import subprocess\n'
        'def launch():\n'
        "    node = shutil.which('node')\n"
        "    return subprocess.run([node, 'child.js'])\n")
    assert not _bounds_its_own_child(ast.parse(with_nothing), 5, None)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='nodelaunchrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
