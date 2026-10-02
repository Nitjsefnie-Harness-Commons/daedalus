#!/usr/bin/env python3
"""The surviving test-tree analyser helpers' own semantics, on fixtures.

Three helpers stay after cut 6 with no suite of their own left: the
mutation-sweep launch scan, the coverage memo's materialised node list,
and the shared source reader. Every kept suite that imports one exercises
a rule ABOUT the guard, never a rule about the helper, so each of the
three properties below is pinned here once rather than in a suite already
at the tests ceiling.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _coverage_memo  # noqa: E402
import _util  # noqa: E402
from _coverage_source_fixtures import _normalized_source  # noqa: E402
from _sweep_launch_scan import sweep_launches  # noqa: E402

HERE = 'tests/synthetic.py'
# Spelled out rather than read off `_sweep_launch_scan.SWEEP_ENTRY`: a fixture
# built from the subject's own constant cannot see that constant change.
PROGRAM = ('suite.test_each_new_binding_and_match_arm_is_mutation_'
           'sensitive(tmp)')


def _scan_of(body, head='import subprocess\n'):
    return sweep_launches(ast.parse(head + body), HERE)


def test_a_bare_name_launcher_is_still_a_launcher(tmp):
    """A launcher imported under its own name is a launcher.

    `_run_spellings` records what the module's own imports bind, and
    `_callee` is the other half: it has to read a bare `Name` receiver as
    well as an `Attribute` one. Dropping the `getattr(function, 'id')`
    arm leaves every dotted spelling working and every `from subprocess
    import run` launch invisible, which is the whole population the
    control exists for.
    """
    del tmp
    assert _scan_of(f'run(["-c", "{PROGRAM}"], timeout=120)\n',
                    head='from subprocess import run\n') == (
        [(HERE, 2)], [(HERE, 2)])


def test_a_scope_is_fully_bound_before_any_call_in_it_is_judged(tmp):
    """The two passes are ordered, and one loop binding as it judges is not
    the analysis.

    `_sweep_scope_nodes` yields a scope's own statements in document order
    but expands their subtrees only afterwards. So a program bound INSIDE A
    BRANCH — where real code puts one, and what the analyser's own docstring
    names — is yielded after the launch standing beside it, and that ordering
    holds only because the branch precedes the launch; put the launch first
    and its statement is yielded first too. Either way one loop that binds
    and judges as it goes can reach a launch before the name it runs has been
    bound, and the deadline scan reports a bounded sweep launch as unbounded.
    The first pass is what makes walk order irrelevant; collapsing the two
    loops is a false green, not a simplification.
    """
    del tmp
    assert _scan_of(f'if True:\n    p = ["-c", "{PROGRAM}"]\n'
                    'subprocess.run(p, timeout=120)\n') == (
        [(HERE, 4)], [(HERE, 4)])


def test_a_name_is_read_as_the_binding_in_force_at_its_line(tmp):
    """The LAST binding at or before a launch, never the join of them all.

    `_spelled` filters a name's bindings to those at or before the line
    being judged. Without that filter a launch reads a program bound after
    it, and a sweep launch with no deadline in sight at its own line is
    reported as one with the deadline — the false red this filter exists to
    stop. The second launch in the fixture is the control that keeps the
    filter from being a plain "the first binding" rule.
    """
    del tmp
    assert _scan_of(f'program = "print(1)"\n'
                    'subprocess.run([program], timeout=5)\n'
                    f'program = ["-c", "{PROGRAM}"]\n'
                    'subprocess.run([program])\n') == (
        [(HERE, 5)], [])


def test_a_scope_node_binds_its_own_program(tmp):
    """Every member of `_SCOPE_NODES` is a scope, and hides from outside.

    A class body is a scope of its own, so a program written inside one is
    not in force in the module beside it. Drop `ast.ClassDef` from the tuple
    and its bindings join the enclosing scope, the launch standing next to
    the class reads them, and a `timeout=` on a launch running something
    else is refused as the sweep's own.
    """
    del tmp
    assert _scan_of('class A: pass\n'
                    f'class C: argv = ["-c", "{PROGRAM}"]\n'
                    'subprocess.run(argv, timeout=5)\n') == ([], [])


def test_the_analysed_tree_is_its_own_first_node(tmp):
    """`nodes(tree)` includes the root, so a module-level binding is read.

    The materialised list is `[tree] + below`, and every guard that walks
    through it judges module-level bindings on the strength of the root
    being there. A list built from `ast.walk(tree)[1:]` matches a fresh
    walk everywhere below the root and silently stops judging the whole
    module, which is a guard that goes quiet rather than one that goes red.
    """
    del tmp
    tree = ast.parse('import os\n\nSUBJECT = "a value"\n')
    assert _coverage_memo.nodes(tree) == list(ast.walk(tree))


def test_a_crlf_module_reads_back_normalised(tmp):
    """The reader's CRLF arm, over bytes on disk rather than in memory.

    A checkout can carry CRLF, and a guard that reads a copied test module
    through this reader counts lines to place a diagnostic. Reading the
    bytes and decoding without the replacement leaves the carriage return
    in the text, so every line number derived from it after the first is
    off by one against the offsets the file on disk reports. It has to be
    real bytes: `read_text` applies universal newlines, so an in-memory
    string or a text-mode read cannot tell this reader from a correct one.
    """
    target = Path(tmp) / 'crlf.py'
    target.write_bytes(b'import os\r\nSUBJECT = "a value"\r\n')
    assert b'\r\n' in target.read_bytes()
    assert _normalized_source(target) == 'import os\nSUBJECT = "a value"\n'


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
