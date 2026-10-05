#!/usr/bin/env python3
"""The one import-closure limit the real `daedalus_mcp` tree cannot show.

The refusal pass in `test_mcp_tools.py` walks the real tree, so it can only
witness a closure property the tree actually presents. Each shape below is
one it does not present, driven on a synthetic composition instead, and the
arms of the walk are enumerated in `test_mcp_import_refusals.py`.

This file carried three such shapes and now carries one. Each of the other
two was removed because a mutant that kills its case here ALSO reds a case
in `test_mcp_import_refusals.py`, so the file was not the only place the
defect showed and the shape was breadth rather than cover. The mutants, so
the measurement can be repeated:

- the dead-code barrier: `dead_nodes` returning an empty set reds this
  file's barrier case and `test_mcp_import_refusals.py`'s
  `test_every_dead_code_barrier_kind_marks_the_tail_behind_it`, which drives
  all five barrier kinds and the fall-through near miss on one tree where
  this file drove one kind on a second tree saying the same thing.
- a constant program reaching a code-evaluating builtin: dropping the arm in
  `_import_targets`, forcing `is_builtin` to answer True, and making
  `may_be_code_eval` answer True for any callee each red this file's
  program case and a case in `test_mcp_import_refusals.py` — the arm itself
  as a table row, the `bool` index rows, and the near-miss rows beside them.
  Its other side, a name the module has bound to a tool, is what
  `NEAR_MISSES`' "a builtin that evaluates nothing" row and the real tree's
  own `server.py` answer.

The case below is the one that stayed, and it stayed on a measurement rather
than on a preference: dropping the lambda arm from `static_value` reds it
and leaves `test_mcp_import_refusals.py` 10/10 and `test_mcp_tools.py`
21/21. Nothing else on this tree catches a call's callee being read as a
VALUE.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_code_eval  # noqa: E402
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402
from _mcp_import_fixtures import _write_tree  # noqa: E402


def _composition_names(_tmp, tree):
    """The repo-local files this composition's scan set names, relative to
    the tree it was written into."""
    _write_tree(Path(_tmp), tree)
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    return {path.relative_to(Path(_tmp)).as_posix() for path in scanned}


def test_a_nullary_lambda_callee_of_the_operation_resolves_the_module(_tmp):
    """A call's callee is a VALUE, and `(lambda: op)()` produces the
    operation, so the module resolves exactly as the direct spelling does.

    The near miss is a lambda the SAME call cannot fill, which is the arm
    this row is about rather than its neighbour: a lambda with a required
    parameter raises `TypeError` before it produces anything, so it
    resolves nothing and refuses nothing. The zero-argument CALL after the
    lambda is what makes it that arm — it is the shape every positive here
    has, differing only in the signature. Without that call the row would be
    testing "a lambda read in place", which the walk answers by the
    call-result limit instead, and a rule that confused the two would pass.
    """
    for callee in ('(lambda: importlib.import_module)()',
                   '(lambda *a: importlib.import_module)()',
                   '(lambda a=0: importlib.import_module)()',
                   '(lambda **k: importlib.import_module)()'):
        assert 'pkg/leaf.py' in _composition_names(_tmp, {
            'composition.py': '\nimport importlib\n'
                              '\n\ndef load():\n'
                              f'    return {callee}("pkg.leaf")\n',
            'pkg/__init__.py': '',
            'pkg/leaf.py': 'leaf = True\n'}), callee
    assert _composition_names(_tmp, {
        'composition.py': '\nimport importlib\n'
                          '\n\ndef load():\n'
                          '    return (lambda a: importlib.import_module)()'
                          '("pkg.leaf")\n',
        'pkg/__init__.py': '',
        'pkg/leaf.py': 'leaf = True\n'}) == {'composition.py'}


def _scan_call_count(_tmp, depth):
    """The `_scan` invocations one depth-`depth` chain costs, counted by
    a pass-through surrogate, and the scan set that came with them."""
    real = _mcp_code_eval._scan
    calls = [0]

    def counting(*args):
        calls[0] += 1
        return real(*args)

    _mcp_code_eval._scan = counting
    try:
        names = _composition_names(_tmp, {
            'composition.py': '\nimport importlib\n\n\ndef load():\n'
                              '    return [[importlib.import_module]]'
                              f'{"[0]" * (depth - 1)}("pkg.leaf")\n',
            'pkg/leaf.py': 'leaf = True\n'})
    finally:
        _mcp_code_eval._scan = real
    return calls[0], names


def test_scan_set_walks_a_deep_subscript_chain_in_linear_cost(_tmp):
    """A depth-20 chain costs at most a small constant times a depth-10
    one; code re-asking a subtree's verdict twice per level scores x2 per
    level here. The result arm beside the ratio rejects a stopped scan.
    """
    shallow, _ = _scan_call_count(_tmp, 10)
    deep, names = _scan_call_count(_tmp, 20)
    assert deep <= 8 * shallow, (shallow, deep)
    # Past the operation the chain folds to UNREACHABLE, so the correct set
    # is the composition alone; the arm catches a stopped or refused walk.
    assert names == {'composition.py'}, names


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
