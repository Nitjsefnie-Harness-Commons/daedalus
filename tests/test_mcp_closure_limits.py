#!/usr/bin/env python3
"""The three import-closure limits the real `daedalus_mcp` tree cannot show.

The refusal pass in `test_mcp_tools.py` walks the real tree, so it can only
witness a closure property the tree actually presents. Each shape below is
one it does not present, driven on a synthetic composition instead, and the
arms of the walk are enumerated in `test_mcp_import_refusals.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402
from _mcp_import_fixtures import (  # noqa: E402
    _assert_scan_refusal, _write_tree)


# Two of the three cases below close shapes the real tree does not exercise,
# so the pass above cannot witness them: nothing in `daedalus_mcp` hands a
# program to a code-evaluating builtin, and no callee is read out of a
# nullary lambda's return. Each of those two states a REFUSAL and a SILENCE
# — a constant program reaching `eval`/`exec`/`compile` is refused while the
# same name bound to a tool is not, a nullary lambda callee resolves while a
# lambda with a required parameter refuses — which is what keeps a rule that
# over-reaches from passing. The third is a MEMBERSHIP rather than such a
# pair, and it is on the real tree: the barrier case pins that the call
# before a barrier contributes its module and the call behind it does not.
# The arms of the walk the real tree cannot present at all are enumerated in
# `test_mcp_import_refusals.py`.


def _composition_names(_tmp, tree):
    """The repo-local files this composition's scan set names, relative to
    the tree it was written into."""
    _write_tree(Path(_tmp), tree)
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    return {path.relative_to(Path(_tmp)).as_posix() for path in scanned}


def test_a_program_handed_to_a_code_evaluating_builtin_is_refused(_tmp):
    """A constant string reaching `eval`/`exec`/`compile` is a PROGRAM, and
    the walk either resolves it or refuses it — never silence.

    The other side is the same name bound to a tool, which is what
    `server.py` does with the `exec` its own tool module exports: a
    different function, so the scan set is READ and answered, not merely
    not an exception. Reading it is the whole point: a composition that
    raised some other arm's refusal would answer here too.
    """
    for name in ('eval', 'exec', 'compile'):
        program = f'\n\ndef load(x):\n    return {name}("importlib")(x)\n'
        _assert_scan_refusal(_tmp, program, 4, 'code-evaluating')
    assert _composition_names(_tmp, {
        'composition.py': '\neval_tools = {"exec": print}\n'
                          'exec = eval_tools["exec"]\n'
                          '\n\ndef load():\n    return exec("code")\n'
    }) == {'composition.py'}


def test_a_position_behind_a_barrier_is_out_of_the_scan_set(_tmp):
    """A statement the runtime cannot reach contributes no module, and the
    one before it still does.

    Both halves ride in one tree, so a rule that stopped marking the tail
    would add `pkg.leaf` and one that over-reached would drop `pkg.before`.
    Every barrier kind and that shared near miss are enumerated in
    `test_mcp_import_refusals.py`; this is the real tree's own suite, and
    `raise` is the kind the composition above reaches.
    """
    assert _composition_names(_tmp, {
        'composition.py': '\nimport importlib\n'
                          '\n\ndef load():\n'
                          '    importlib.import_module("pkg.before")\n'
                          '    raise RuntimeError("before the call")\n'
                          '    return importlib.import_module("pkg.leaf")\n',
        'pkg/__init__.py': '',
        'pkg/before.py': 'before = True\n',
        'pkg/leaf.py': 'leaf = True\n'}) == {
            'composition.py', 'pkg/__init__.py', 'pkg/before.py'}


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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
