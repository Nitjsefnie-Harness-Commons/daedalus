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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent
DETECTOR = """
import subprocess
import sys

from _processtree import cleanup_process_tree

SAMPLES = (1.0, 1.5, 2.0)
SLOWEST_S = max(SAMPLES)
MULTIPLE = 10
DEADLINE_S = round(SLOWEST_S * MULTIPLE)
CLEANUP_S = 5


class ChildDeadlineExceeded(Exception):
    pass


def launch(argv):
    process = subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=sys.platform != 'win32')
    try:
        returncode = process.wait(timeout=DEADLINE_S)
    except subprocess.TimeoutExpired:
        cleanup_process_tree(process, CLEANUP_S)
        raise ChildDeadlineExceeded(argv, DEADLINE_S) from None
    return returncode
"""


def _tree(root, files):
    """Write a tree of planted sources and return the directory."""
    root = Path(root)
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    return root


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
        '_noderun.py': DETECTOR.replace(
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
        '_noderun.py': DETECTOR.replace(
            'def launch(argv):', 'def run_node_program(argv):'),
        '_stream_fake.py': (
            'from _noderun import run_node_program as rn\n'
            'def run_gate(argv):\n    return rn(argv)\n'),
        'caller.py': ('from _stream_fake import run_gate as rg\n'
                      'def go(argv):\n    return rg(argv)\n'),
    })
    paths = census.path_functions(root, ('_noderun.py', '_stream_fake.py'))
    assert 'caller.py' in paths, sorted(paths)


def test_the_launcher_member_set_is_a_fixed_point_not_a_list(tmp):
    """The launcher set is derived from the stdlib's own source.

    Membership alone would not say so: a hand list containing the same names
    passes it. This measures the closure — a member that CALLS a launcher is
    a launcher — by seeding the walk at a set that does not yet contain
    `check_call` and reading what it reaches.
    """
    del tmp
    # The closure, not the result: `check_call` reaches a child by calling
    # `run`, and its own text never says `Popen`, so a hand list that
    # happened to hold the right names would have to be written deliberately.
    derived = census._launch_members()
    assert 'check_call' in derived, sorted(derived)
    assert 'check_output' in derived, sorted(derived)
    assert 'CompletedProcess' not in derived, sorted(derived)


def test_the_wait_slots_follow_the_signature(tmp):
    """The timeout positions are read from `Popen`, not written down.

    A hand list holding today's right numbers reads identically and is
    wrong the day a parameter moves, and no control that asserts the VALUES
    can tell the two apart. This recomputes the set against a `Popen` whose
    methods take the timeout one position later, and reads the positions
    move.
    """
    del tmp
    import subprocess as sp
    real = sp.Popen
    assert census._CHILD_WAIT_SLOTS == {'wait': 0, 'communicate': 1}

    class Reordered:
        """A `Popen` whose methods take the timeout one slot later."""

        def wait(self, marker, timeout=None):
            """A wait whose first positional is not the timeout."""

        def communicate(self, marker, timeout=None):
            """A communicate whose first positional is not the timeout."""

    sp.Popen = Reordered
    try:
        moved = census._child_wait_slots()
    finally:
        sp.Popen = real
    assert moved['wait'] == 1, moved
    assert census._CHILD_WAIT_SLOTS == {'wait': 0, 'communicate': 1}, (
        'the set was captured rather than recomputed')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchpath_')


if __name__ == '__main__':
    raise SystemExit(main())
