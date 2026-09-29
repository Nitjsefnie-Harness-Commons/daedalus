#!/usr/bin/env python3
"""WHICH `global` declaration applies to a rebinding, at each nesting depth.

`tests/_receiver_resolution.py`'s `_global_names` answers per rebinding
rather than per module, and the question it answers is which enclosing def
the declaration was written in. This holds the two rows that separate "the
`global` is in the def the rebinding is in" from "the `global` is in a def
the rebinding is nested inside" — the same rule read at two depths, and not
one row between them.

Named for what it holds rather than for when it was written.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _launch_census import _faults  # noqa: E402
import _util  # noqa: E402

IMPORT = 'from urllib.request import urlopen\n'
READ = 'def run_gate(url):\n    return urlopen(url, timeout=10)\n'


def _rows(source):
    """Census rows for a planted module with EVERY function in path."""
    tree = ast.parse(source)
    forced = frozenset(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return sorted((line, route) for _, line, route, _ in
                  _faults('planted.py', tree, forced))


# The declaration is in the def the rebinding is written in, so it applies
# and the rebinding really is module-wide: the import is popped and the read
# is no longer a network read. This is the row the next differs from by one
# level of nesting and nothing else.
SAME_DEF = (IMPORT + '\n\ndef run_gate(url):\n    global urlopen\n'
            '    urlopen = object()\n    return urlopen(url, timeout=10)\n')

# The declaration is in the ENCLOSING def and does not reach the nested one,
# so the rebinding is local to `inner`, the module import survives, and the
# read in `run_gate` is the `urllib.request.urlopen` it is spelled as — a
# network read, and a network read is not a wall bound. RED on the
# breadth-first variant: its first hit is the enclosing def, so it inherits
# a declaration that was never in scope there, pops the import, and refuses
# a real network read.
NESTED_DEF = (IMPORT + '\n\ndef outer():\n    global urlopen\n\n'
              '    def inner():\n        urlopen = object()\n'
              '    inner()\n\n\n' + READ)


def test_a_global_in_the_same_def_pops_the_import(tmp):
    """The declaration the rebinding is written under is module-wide."""
    del tmp
    assert _rows(SAME_DEF), 'the same-def global stopped popping the import'


def test_a_global_in_an_enclosing_def_does_not_reach_the_nested_one(tmp):
    """The row that is red on the breadth-first variant.

    `ast.walk` is breadth-first, so an outer def is visited before the
    inner one nested in it and the first hit is the OUTERMOST def. The
    declaration in `outer` was never in scope in `inner`, so the
    rebinding there is local, the import survives, and the read is the
    network read it is spelled as.

    Widest-span does not separate these rows either: an outer def
    encloses the inner, so it is always the wider, and the two searches
    were measured to agree on every nested shape. Narrowest span is what
    picks the def the rebinding is actually in.
    """
    del tmp
    assert not _rows(NESTED_DEF), (
        'the enclosing global reached the nested def, so the rebinding was '
        'read as module-wide and a real network read was refused')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchcensusscopes_')


if __name__ == '__main__':
    raise SystemExit(main())
