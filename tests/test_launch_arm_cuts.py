#!/usr/bin/env python3
"""The controls on the mechanism that deletes a clause, not on the table.

`tests/test_launch_arms.py` checks the TABLE: that every arm is
addressed where it says, is in one of three states, and is held by the
evidence it names. This checks the MECHANISM underneath it --
`tests/_arm_sweep.py`'s `cut_arm` -- because the table's every claim is
read through that cut, and a mechanism that quietly cuts the wrong
clause or reports the wrong text makes all of them untrue.

Both controls here run over the whole table rather than the sample the
table suite replays: `cut_arm` is pure, so re-running it 150 times
costs nothing against the child interpreters a sweep really spends on.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _arm_sweep import CUT_KIND, cut_arm  # noqa: E402
from _launch_arm_records import CUT, EVIDENCE, FILE, ID, LINE  # noqa: E402
from _launch_arms import LAUNCH_ARMS  # noqa: E402

TESTS = Path(__file__).resolve().parent


def test_a_spec_whose_op_does_not_match_its_node_is_refused(tmp):
    """The op names a kind of clause, and any other kind is refused.

    `_locate` answers the node that CARRIES a line, not the one the op
    names, so a spec keyed one line off is not a wrong answer from the
    sweep but a wrong MUTATION: a `drop_if` on a `for` cut the whole
    loop and reported a plausible `removed`, and a `drop_stmt` on a
    decorator cut the decorator. Both are the module's own refusal now,
    naming the kind it found.

    The three probes are lines of `tests/_argv_read.py`, which is held
    byte-identical to base, so the node at each is fixed: a `for`, a
    `def` and a bare decorator. Each is asserted to be a kind the op
    does not cut before the refusal is read, so this cannot go on
    passing once the lines mean something else.
    """
    del tmp
    source = (TESTS / '_argv_read.py').read_text(encoding='utf-8')
    stmts = {node.lineno: node for node in ast.walk(ast.parse(source))
             if isinstance(node, ast.stmt)}
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

    The mechanism's reason for existing is that a reader can see WHICH
    clause a verdict depended on, and the only consumer of the field
    asked whether it was non-empty -- so a constant satisfied every
    control. Four relations close it, and each is read off the source
    and the mutation rather than out of the mechanism that reported
    them: the cut changed the file, the text it reported is a real
    region of the source, that text carries the line this arm is
    addressed at, and where the cut only deletes, the file is shorter
    by exactly the length of the text reported. `promoted` gets the
    matching relation -- it is in the mutated file -- so the field
    saying what took the clause's place is watched too.
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


def test_every_op_the_table_uses_cuts_the_kind_it_names(tmp):
    """`CUT_KIND` has to cover the ops, and the rows have to obey it.

    Two ways this table could rot into refusing every arm: an op the
    table uses that `CUT_KIND` does not name, which the guard would
    wave through unchecked, and a kind that drifts out from under a
    shipped spec. The first sweep run would catch the second, but only
    once someone spent five minutes; this is the same fact over a
    second.
    """
    del tmp
    named = {arm[CUT].split(':')[0] for arm in LAUNCH_ARMS}
    unknown = sorted(named - set(CUT_KIND) - {'boolop'})
    assert not unknown, f'cut ops with no kind the guard checks: {unknown}'
    sources = {arm[FILE]: (TESTS / arm[FILE]).read_text(encoding='utf-8')
               for arm in LAUNCH_ARMS}
    for arm in LAUNCH_ARMS:
        spec, source = arm[CUT], sources[arm[FILE]]
        op = spec.split(':')[0]
        if op not in CUT_KIND:
            continue
        line = int(spec.split(':')[1])
        node = max((held for held in ast.walk(ast.parse(source))
                    if getattr(held, 'lineno', None) == line),
                   key=lambda held: held.end_lineno, default=None)
        assert isinstance(node, CUT_KIND[op]), (
            f'{arm[ID]}: {spec} names a {type(node).__name__}, which is not '
            f'the {CUT_KIND[op].__name__} {op} cuts')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
