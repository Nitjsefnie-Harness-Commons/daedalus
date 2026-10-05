#!/usr/bin/env python3
"""The one import-closure limit the real `daedalus_mcp` tree cannot show.

The refusal pass in `test_mcp_tools.py` walks the real tree, so it can only
witness a closure property the tree actually presents; each shape below is
one it does not present, and the walk's arms are enumerated in
`test_mcp_import_refusals.py`. This file carried three such shapes and now
carries one: the other two were removed because a mutant that kills its
case here ALSO reds a case there, so the shape was breadth rather than
cover. The mutants, so the measurement can be repeated:

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

The case below stayed on a measurement, not a preference: dropping the
lambda arm from `static_value` reds it and leaves
`test_mcp_import_refusals.py` 9/9 and `test_mcp_tools.py` 21/21. Nothing
else on this tree catches a call's callee being read as a VALUE.
"""
import sys
from pathlib import Path
from unittest import mock

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


def test_scan_set_walks_a_deep_subscript_chain_in_linear_cost(_tmp):
    """A depth-20 chain costs at most a small constant times a depth-10
    one; re-asking a subtree's verdict twice per level scores x2 here, in
    whichever arm the descent runs — subscript nesting, nested calls, or
    the one-tuple store's fall-through. Each depth's set is asserted, so
    the composition alone must be the answer at every depth, not only the
    last one's."""
    real_scan, real_element = (_mcp_code_eval._scan,
                               _mcp_code_eval._element_node)
    tally, counts, names = [0], {}, {}

    def surrogate(real):
        def counting(*args):
            tally[0] += 1
            return real(*args)
        return counting

    def subscript(depth):
        return ('\nimport importlib\n\n\ndef load():\n'
                '    return [[importlib.import_module]]'
                f'{"[0]" * (depth - 1)}("pkg.leaf")\n')

    def calls(depth):
        return ('\nimport importlib\n\n\ndef load():\n'
                f'    return {"f(" * depth}importlib.import_module'
                f'{")" * depth}("pkg.leaf")\n')

    def tuples(depth):
        return ('\n\n\ndef load():\n'
                f'    x = {"(" * depth}0{",)" * depth}\n')

    shapes = {'subscript': subscript, 'call': calls,
              'fall-through': tuples}
    with mock.patch.multiple(
            _mcp_code_eval, _scan=surrogate(real_scan),
            _element_node=surrogate(real_element)):
        for label, build in shapes.items():
            for depth in (10, 20):
                tally[0] = 0
                names[label, depth] = _composition_names(
                    _tmp, {'composition.py': build(depth)})
                counts[label, depth] = tally[0]
    for label in shapes:
        assert counts[label, 20] <= 3 * counts[label, 10], counts
        assert names[label, 10] == names[label, 20] == {'composition.py'}, \
            names


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
