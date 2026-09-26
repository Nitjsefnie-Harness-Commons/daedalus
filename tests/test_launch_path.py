#!/usr/bin/env python3
"""Which source the census reads, and why each module is on the launch path.

The scope is a control of its own, and it lives beside the module that
derives it: `tests/_launch_path.py` answers "which source", and
`tests/_launch_census.py` answers "what in it is a bound". Every control
here drives `path_functions()` or the trees it reads, and none of them
plants a bound — the rules' own controls are in
`tests/test_harness_launch_bounds.py`.

Pinned in both directions throughout, because a scope that admits
everything and a scope that admits nothing are both green: the expected
modules are named, the ones that must be out are named, and a module that
reaches the gate only by reaching another launcher of its own is pinned as
OUT so the audit cannot swallow the mechanism issue #1121 lists.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
import _launch_path as path  # noqa: E402
from _launch_fixtures import (  # noqa: E402
    HANG_DETECTOR_PROGRAM as _DETECTOR, write_source_tree as _tree)
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent


def test_the_shipped_tree_carries_no_undeclared_wall_bound(tmp):
    """The real path is clean, which is the property this suite exists for."""
    del tmp
    # Liveness of the seed: a renamed or moved launcher leaves the path
    # empty, and an empty path reads as a clean tree. Asserted against the
    # FILES rather than the constant, because a constant cannot be empty
    # and so cannot fail.
    for module in census.LAUNCHER_MODULES:
        assert (TESTS / module).is_file(), (module, 'the seed set emptied')
    faults = census.census(TESTS)
    assert not faults, '\n'.join(
        f'{relative}:{line}  {route}  {detail}'
        for relative, line, route, detail in faults)


def test_the_module_that_ends_the_child_is_on_the_path(tmp):
    """B1: the census judges the module the child is handed to.

    `tests/_processtree.py` is reached as a CALLEE, and a closure over
    callers alone leaves it out — which is how a `process.wait(5)` there
    went unnoticed at a fifth of the detector's budget. The two assertions
    are the property and its boundary: the module is in, and a module the
    path does not reach is out.
    """
    del tmp
    paths = census.path_functions(TESTS)
    for module in census.CHILD_ENDING_MODULES:
        assert module in paths, (module, sorted(paths))
    assert '_util.py' not in paths, sorted(paths)


def test_the_scope_reaches_every_module_that_calls_the_gate(tmp):
    """A caller is on the path because it calls the gate, wherever it lives.

    Pinned in both directions: the two harness modules that reach the gate
    only through `run_inline_gate` are on it, and a module that reaches
    nothing is not. A module that touches the gate AND launches a child of
    its own — a suite run as a subprocess — is on the path for the first
    only, which is what keeps the audit from swallowing the mechanism
    #1121 lists.
    """
    del tmp
    paths = census.path_functions(TESTS)
    for expected in ('_relayharness.py', '_cdpharness.py', '_boundary.py',
                     '_worker_runtime.py', 'test_bridge_fake_oracle.py'):
        assert expected in sorted(paths), (expected, sorted(paths))
    runtime = paths.get('test_worker_runtime.py', set())
    assert 'test_sibling_mutation_failure_names_module_type_and_handlers' \
        not in runtime, sorted(runtime)
    assert 'test_node_harness_decodes_utf8_independent_of_locale' in runtime


def test_a_caller_in_a_subdirectory_is_on_the_path(tmp):
    """A `tests/` subdirectory is not outside the tree.

    A real tree, not a string: the derivation walks `rglob`, and the only
    way to pin that is to hand it a subdirectory and read what it found.
    `tests/` is flat today, so this is latent — which is exactly why it needs
    a control rather than a note.
    """
    root = _tree(tmp, {
        '_noderun.py': _DETECTOR.replace(
            'def launch(argv):', 'def run_node_program(argv):'),
        '_stream_fake.py': (
            'def run_gate(*a, **k):\n    return run_node_program(*a, **k)\n'),
        'pkg/child.py': ('from _noderun import run_node_program\n'
                         'def go():\n'
                         '    return run_node_program(1, 2, 3, 4)\n'),
        'flat.py': ('from _noderun import run_node_program\n'
                    'def go():\n    return run_node_program(1, 2, 3, 4)\n'),
    })
    paths = census.path_functions(root, ('_noderun.py', '_stream_fake.py'))
    assert 'pkg/child.py' in paths, sorted(paths)
    assert 'flat.py' in paths, sorted(paths)


def test_an_aliased_import_still_reaches_the_gate(tmp):
    """`import ... as` is a route to the gate, not a different gate.

    The re-exported short name is what a caller that tidies its imports
    writes, and a reachability rule keyed on the canonical spelling alone
    cannot see the call.
    """
    root = _tree(tmp, {
        '_noderun.py': _DETECTOR.replace(
            'def launch(argv):', 'def run_node_program(argv):'),
        '_stream_fake.py': (
            'from _noderun import run_node_program as rn\n'
            'def run_gate(argv):\n    return rn(argv)\n'),
        'caller.py': ('from _stream_fake import run_gate as rg\n'
                      'def go(argv):\n    return rg(argv)\n'),
    })
    paths = census.path_functions(root, ('_noderun.py', '_stream_fake.py'))
    assert 'caller.py' in paths, sorted(paths)


def _live_launcher_members():
    """The launcher members, by closure, from the live `subprocess`.

    The fixed point the census used to compute: seeded at `Popen`, and a
    member that CALLS one is one, so `check_call` is in and `CompletedProcess`
    is out. Read through the module's own namespace so this control does not
    itself bind a launcher the shared guard would have to refuse.
    """
    import inspect
    import re
    import subprocess

    members = {'Popen'}
    growing = True
    while growing:
        growing = False
        for name, member in subprocess.__dict__.items():
            if name.startswith('_') or name in members:
                continue
            if inspect.isclass(member) or not callable(member):
                continue
            try:
                source = inspect.getsource(member)
            except (OSError, TypeError):
                continue
            for reached in sorted(members):
                if re.search(rf'\b{re.escape(reached)}\s*\(', source):
                    members.add(name)
                    growing = True
                    break
    return frozenset(members)


def test_the_launcher_member_names_match_the_live_module(tmp):
    """The names the census carries are the ones the live module agrees to.

    The census holds these as a literal, because a launcher reached through a
    computed name is a binding the shared coverage guard refuses to follow.
    So the closure that used to establish the list is established HERE,
    against the live module, and the two are compared: a member a future
    stdlib adds fails a test that names the difference, rather than passing
    unnoticed as a hand list would.
    """
    del tmp
    live = _live_launcher_members()
    carried = census._LAUNCH_MEMBERS
    assert carried == live, (
        'the carried member set has drifted from the live module: '
        f'only carried {sorted(carried - live)}, '
        f'only live {sorted(live - carried)}')
    # The closure and not just the set: `check_call` reaches a child by
    # calling `run`, and its own text never says `Popen`.
    assert 'check_call' in live, sorted(live)
    assert 'CompletedProcess' not in live, sorted(live)


def test_the_wait_slots_follow_the_signature(tmp):
    """The timeout positions are read from `Popen`, not written down.

    A hand list holding today's right numbers reads identically and is
    wrong the day a parameter moves, and no control that asserts the VALUES
    can tell the two apart. This recomputes the set against a `Popen` whose
    methods take the timeout one position later, and reads the positions
    move.
    """
    del tmp
    assert census._CHILD_WAIT_SLOTS == {'wait': 0, 'communicate': 1}

    class Reordered:
        """A `Popen` whose methods take the timeout one slot later."""

        def wait(self, marker, timeout=None):
            """A wait whose first positional is not the timeout."""

        def communicate(self, marker, timeout=None):
            """A communicate whose first positional is not the timeout."""

    real = path._popen_class
    path._popen_class = lambda: Reordered
    try:
        moved = census._child_wait_slots()
    finally:
        path._popen_class = real
    assert moved['wait'] == 1, moved
    assert census._CHILD_WAIT_SLOTS == {'wait': 0, 'communicate': 1}, (
        'the set was captured rather than recomputed')


def _reads_the_module(node, receivers):
    """Whether `node` reads a member off the `subprocess` module."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return False
    if node.func.id != 'getattr' or not node.args:
        return False
    first = node.args[0]
    return isinstance(first, ast.Name) and first.id in receivers


def _assigns_onto_the_module(node, receivers):
    """Whether `node` assigns an attribute onto the module."""
    if not isinstance(node, ast.Assign):
        return False
    return any(isinstance(target, ast.Attribute)
               and isinstance(target.value, ast.Name)
               and target.value.id in receivers
               for target in node.targets)


def _unfollowable_binding(node, receivers):
    """Why `node` binds a launcher the guard cannot follow, or ''."""
    if _reads_the_module(node, receivers):
        return 'a getattr off the subprocess module'
    if _assigns_onto_the_module(node, receivers):
        return 'an assignment onto the subprocess module'
    return ''


def test_no_census_module_binds_a_launcher_off_the_subprocess_module(tmp):
    """Neither refused shape is back in a module the census reads.

    A `getattr` off the `subprocess` module and an assignment onto it are
    both silent: the census read a launcher the shared guard could not
    follow, and the guard named the census's own modules without saying
    why. Naming the two shapes here makes a reintroduction a failure that
    says which one and where.
    """
    del tmp
    offenders = []
    for module in ('_launch_path.py', '_launch_audit.py', '_launch_census.py'):
        tree = ast.parse((TESTS / module).read_text(encoding='utf-8'))
        receivers = census._subprocess_receivers(tree)
        for node in ast.walk(tree):
            shape = _unfollowable_binding(node, receivers)
            if shape:
                offenders.append(
                    f'{module}:{getattr(node, "lineno", 0)}: {shape}')
    assert not offenders, '\n'.join(offenders)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchpath_')


if __name__ == '__main__':
    raise SystemExit(main())
