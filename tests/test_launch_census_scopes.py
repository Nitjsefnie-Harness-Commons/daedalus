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


# The declaration is in a THIRD def -- neither the one the rebinding is
# written in nor the one the read is written in -- so the rebinding really
# is module-wide and the read is no longer the network read it is spelled
# as. This is the row the EXEMPTION needs, and it is not either row above:
# `_shadowed_parameters` scopes its own pop to the def the rebinding is in,
# so a same-def shape cannot see a module-wide pop, and the `NESTED_DEF`
# row puts the declaration where it does not reach.
GLOBAL_ELSEWHERE = (
    IMPORT + '\n\ndef other():\n    global urlopen\n    urlopen = object()'
    '\n\n\n' + READ)


def test_a_global_in_a_third_def_pops_the_import_for_a_read_elsewhere(tmp):
    """The pop gate exempts a rebinding under `global` -- so it must pop.

    `_dotted_bindings` skips the pop for any node `_global_rebindings`
    reported, which is what lets a rebinding written under a `global` keep
    its own effect on the module table. Drop that exemption and the pop
    fires on every module-wide rebinding, the `urlopen` import survives
    whatever the second binding did to it, and the read below is
    discharged as a network read that no longer exists at runtime.

    `test_launch_census_scopes.py`'s two rows are a claim about
    `_global_names`' SCAN -- which declaration applies to which rebinding
    -- and neither can see this EXEMPTION. The only other `global` rebind
    in the tree is at module scope, where `global` means nothing.
    """
    del tmp
    assert _rows(GLOBAL_ELSEWHERE), (
        'the `global` exemption in the pop gate stopped exempting, so a '
        'module-wide rebinding under `global` left the import standing and '
        'a real network read was read as a live one')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchcensusscopes_')


if __name__ == '__main__':
    raise SystemExit(main())
