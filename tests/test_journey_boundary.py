#!/usr/bin/env python3
"""Contracts for the COUNTED BOUNDARY: the one point a counted child's
counters are zeroed, the argv that keeps the bridge it spawns instrumented
from birth, and every way the boundary refuses rather than lets an
interpreter's own cost back into the count."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
from _journey_contract import (  # noqa: E402
    BOUNDARY_ENV,
    ROOT,
    _util,
    boundary_loader, boundary_probe, counted_run,
    environment, journeys,
)


def test_the_counted_boundary_is_established_or_the_run_is_refused(tmp):
    """A counted child's count carries its own interpreter startup and the
    compile of its import closure unless a client request zeroes the counters
    after them, so the boundary is established or the run refuses naming why.
    The refusals alone pass against a module that loads the helper and never
    calls it, so the record below is the CALL, and unset loads nothing."""
    module, counters = journeys(), _journey_contract.counters()
    establish, absent = module._establish_counted_boundary, str(
        Path(tmp) / 'absent.so')
    idle = boundary_loader(daedalus_cg_zero_stats=True)
    # Each refusal is keyed on the words the SUBJECT mints, never on a word
    # the exception happens to carry: the AttributeError names the very
    # symbol whose absence is the finding, and the stub's own OSError says
    # "would not issue", so an anchor drawn from either is satisfied by its
    # sibling limb. The minted prefix is what tells "the helper loaded but
    # carries no symbol" from "the call did not issue" -- the two
    # diagnostics a reader has to be able to tell apart. A row that refuses
    # nothing expects nothing, so it spells both empty.
    for boundary, loader, code, minted, carried in (
            (None, idle, 0, '', ''), ('/cg.so', idle, 0, '', ''),
            (absent, module.CDLL, 3, 'the helper does not load', absent),
            ('/cg.so', boundary_loader(), 3,
             'the helper carries no daedalus_cg_zero_stats', 'no attribute'),
            ('/cg.so', boundary_loader(daedalus_cg_zero_stats=True,
                                       fails=True), 3,
             'daedalus_cg_zero_stats did not issue', 'would not issue')):
        got, said, spawned = boundary_probe(boundary, loader, establish)
        assert (got, spawned) == (code, []), (boundary, got, spawned)
        if code:
            assert said.startswith(
                f'counted boundary unavailable: {minted}: '), (minted, said)
            assert carried in said, (carried, said)
        else:
            assert not said, said
    assert (idle.served, idle.zeroed) == (['/cg.so'], ['/cg.so']), (
        idle.served, idle.zeroed)
    argv = ['--journey', 'command-round-trip', '--root', tmp]
    got, said, spawned = boundary_probe(
        absent, module.CDLL, establish, lambda: module.main(argv))
    assert (got, spawned) == (3, []) and absent in said, (
        'main must refuse before it spawns anything', got, said)
    value, why, argv = counted_run(counters, 'mcp-exec', ROOT, tmp, None)
    assert value is None and not argv and 'DAEDALUS_CALLGRIND_BOUNDARY' in (
        why or ''), (value, why, argv)
    value, why, argv = counted_run(counters, 'mcp-exec', ROOT, tmp)
    assert argv and argv[0][0] == '/usr/bin/valgrind', (value, why, argv)


def test_the_boundary_is_the_first_statement_of_main(tmp):
    """The placement is load-bearing and is asserted in three places, so it
    is controlled here rather than only claimed.

    A counted child's import closure is what the boundary exists to remove
    from the number, so the boundary has to run after that closure and
    before anything is spawned. `main` reached from `body` with its docstring
    skipped, because a docstring is not a statement of the run.
    """
    del tmp
    module = journeys()
    tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == 'main')
    statements = [node for node in main.body
                  if not (isinstance(node, ast.Expr)
                          and isinstance(node.value, ast.Constant)
                          and isinstance(node.value.value, str))]
    first = statements[0]
    called = getattr(getattr(first, 'value', None), 'func', None)
    assert isinstance(first, ast.Expr) and isinstance(called, ast.Name) and (
        called.id == '_establish_counted_boundary'), ast.dump(first)


def test_a_boundary_variable_that_is_set_but_empty_still_refuses(tmp):
    """Set-but-empty is a state a variable has, and it names no helper.

    Both readers take truthiness -- the parent asks whether the variable
    holds a usable path and the child asks the same of its own value -- and
    both are right. Neither is exercised by an unset variable, which is the
    only spelling the table above drives, so a reader that tested mere
    presence would let a set-but-empty value through on BOTH sides at once:
    the parent declines to refuse and the child stays inert, which is
    exactly the silent re-inclusion of the import the fail-closed contract
    exists to forbid.
    """
    del tmp
    counters, module = _journey_contract.counters(), journeys()
    with environment(BOUNDARY_ENV, ''):
        assert counters.journey_child_env.boundary_refusal() is not None, (
            'a set-but-empty boundary variable names no compiled helper, so '
            'the parent must still refuse')
    loader = boundary_loader(daedalus_cg_zero_stats=True)
    establish = module._establish_counted_boundary
    got, said, spawned = boundary_probe('', loader, establish)
    assert (got, said, spawned) == (0, '', []), (got, said, spawned)
    assert loader.served == [], (
        'a set-but-empty boundary variable names no helper, so the child '
        f'must load nothing rather than load the empty path: {loader.served}')
    # The oracle is live: the same probe with a real path does reach the
    # loader, so the empty record above is the child declining and not a
    # probe that never got there.
    boundary_probe('/cg.so', loader, establish)
    assert loader.served == ['/cg.so'], loader.served


def test_the_valgrind_argv_keeps_the_spawned_bridge_instrumented(tmp):
    """`--trace-children=yes` present and no `--instr-atstart` toggle.

    The harness's own bootstrap leaves the measurement through a client
    request; the bridge it spawns cannot, so that bridge is counted from
    birth only while the argv leaves valgrind's defaults alone. A toggle
    naming either direction would be the one argv change that silently
    redefines what every recorded count measures, and the prose above says
    so without a control that could fail.
    """
    counters = _journey_contract.counters()
    _value, _why, argv = counted_run(counters, 'mcp-exec', ROOT, tmp)
    assert argv, 'the counter spawned nothing to pin'
    asked = argv[0]
    assert '--trace-children=yes' in asked, asked
    assert not [part for part in asked
                if part.startswith('--instr-atstart')], (
        'no toggle may be added or flipped: the bridge the harness spawns is '
        f'instrumented from birth only while the default stands: {asked}')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeyboundary_')


if __name__ == '__main__':
    raise SystemExit(main())
