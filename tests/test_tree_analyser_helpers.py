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
from _sweep_launch_scan import SWEEP_ENTRY, sweep_launches  # noqa: E402

HERE = 'tests/synthetic.py'
PROGRAM = f'suite.{SWEEP_ENTRY}(tmp)'


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
    but expands their subtrees only afterwards, so a program bound inside
    a branch — where real code puts one, and what the analyser's own
    docstring names — is yielded AFTER the launch standing beside it. One
    loop that binds and judges as it goes therefore judges that launch
    before the name it runs has been bound, and the deadline scan reports
    a bounded sweep launch as unbounded. The first pass is what makes walk
    order irrelevant; collapsing the two loops is a false green, not a
    simplification.
    """
    del tmp
    assert _scan_of(f'if True:\n    p = ["-c", "{PROGRAM}"]\n'
                    'subprocess.run(p, timeout=120)\n') == (
        [(HERE, 4)], [(HERE, 4)])


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
    found = _coverage_memo.nodes(tree)
    assert found[0] is tree, found[:1]
    assert isinstance(found[0], ast.Module)
    assert found == list(ast.walk(tree))


def test_a_crlf_module_reads_back_with_normalised_line_offsets(tmp):
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
    text = _normalized_source(target)
    assert text == 'import os\nSUBJECT = "a value"\n', repr(text)
    assert text[:text.index('SUBJECT')].count('\n') == 1


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
