#!/usr/bin/env python3
"""The controls on the mechanism that deletes a clause, not on the table.

`tests/test_launch_arms.py` checks the TABLE. This checks the MECHANISM
it is read through -- `tests/_arm_sweep.py`'s `cut_arm` -- because a
mechanism that quietly cuts the wrong clause or reports the wrong text
makes every claim in the table untrue at once.

Both controls run over the whole table rather than the sample the table
suite replays: `cut_arm` is pure, so 150 re-runs cost nothing against
the child interpreters a sweep really spends on.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _arm_sweep import CUT_KIND, cut_arm  # noqa: E402
from _launch_arm_records import CUT, FILE, ID, LINE  # noqa: E402
from _launch_arms import LAUNCH_ARMS  # noqa: E402

TESTS = Path(__file__).resolve().parent


def test_a_spec_whose_op_does_not_match_its_node_is_refused(tmp):
    """The op names a kind of clause, and any other kind is refused.

    `_locate` answers the node that CARRIES a line, not the one the op
    names, so a spec keyed one line off is a wrong MUTATION, not a
    wrong answer: a `drop_if` on a `for` cut the whole loop and
    reported a plausible `removed`, and a `drop_stmt` on a decorator
    cut the decorator. Both are now the module's own refusal.

    The probes are lines of `tests/_argv_read.py`, held
    byte-identical to base, so the node at each is fixed: a `for`, a
    `def` and a bare decorator. Each is asserted to be a kind the op
    does not cut before the refusal is read.
    """
    del tmp
    source = (TESTS / '_argv_read.py').read_text(encoding='utf-8')
    stmts = {node.lineno: node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.stmt)}
    # An op `CUT_KIND` does not name is waved through unchecked, which is
    # the way the guard rots: the table grows one and nothing refuses it.
    uncovered = sorted({arm[CUT].split(':')[0] for arm in LAUNCH_ARMS}
                       - set(CUT_KIND) - {'boolop'})
    assert not uncovered, f'cut ops with no kind the guard checks: {uncovered}'
    for line, op in ((84, 'drop_if'), (125, 'drop_if'), (131, 'drop_stmt')):
        assert not isinstance(stmts.get(line), CUT_KIND[op]), (
            f'_argv_read.py:{line} is now a '
            f'{type(stmts.get(line)).__name__}, which {op} does cut, so '
            'this probe no longer reads an op and node that disagree')
        try:
            cut_arm(source, f'{op}:{line}')
            refused = ''
        except ValueError as error:
            refused = str(error)
        assert refused, (
            f'{op}:{line} was cut rather than refused, so a spec keyed off '
            'by one line still mutates whatever clause landed there')
        assert op in refused and 'found a' in refused, refused


def test_the_removed_text_is_the_text_the_cut_took(tmp):
    """`removed` is the text, which is the claim nothing else checked.

    The mechanism exists so a reader can see WHICH clause a verdict
    depended on, and the only consumer of the field asked whether it
    was non-empty -- so a constant satisfied every control. Four
    relations close it, each read off the source and the mutation
    rather than out of the mechanism: the cut changed the file, the
    text is a real region of the source, it carries the line this arm
    is addressed at, and where the cut only deletes the file is shorter
    by exactly its length. `promoted` gets the matching relation -- it
    is in the mutated file.
    """
    del tmp
    sources = {arm[FILE]: (TESTS / arm[FILE]).read_text(encoding='utf-8')
               for arm in LAUNCH_ARMS}
    for arm in LAUNCH_ARMS:
        name, line, spec = arm[ID], arm[LINE], arm[CUT]
        source, op = sources[arm[FILE]], spec.split(':')[0]
        mutated, removed, promoted = cut_arm(source, spec)
        where = f'{name} {spec}'
        assert mutated != source, f'{where}: the cut changed nothing'
        assert removed.strip() and removed in source, (
            f'{where}: the text reported is not a region of the source: '
            f'{removed[:60]!r}')
        assert source.splitlines(keepends=True)[line - 1] in removed, (
            f'{where}: the text reported does not carry the line this arm '
            f'is addressed at, so it cannot show which clause the verdict '
            f'depended on: {removed[:60]!r}')
        assert promoted in mutated, (
            f"{where}: what took the clause's place is not in the mutated "
            f'file: {promoted[:60]!r}')
        if op in ('drop_stmt', 'drop_span') or (op == 'drop_if'
                                                and not promoted):
            # The ops that only delete. `replace` re-renders a header, a
            # promoted `drop_if` re-supplies the chain below the head's
            # own body, and a `boolop` re-renders the whole enclosing
            # statement -- so none of the three leaves the file merely
            # shorter by the text it reported.
            assert len(source) - len(mutated) == len(removed), (
                f'{where}: the file is {len(source) - len(mutated)} '
                f'characters shorter and {len(removed)} were reported')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
