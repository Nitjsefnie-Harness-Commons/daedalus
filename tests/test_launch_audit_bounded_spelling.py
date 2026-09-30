#!/usr/bin/env python3
"""The filter that keeps the launch audit cheap, pinned to its own grammar.

`tests/_launch_keep.py::in_launch_population` skips a tracked file whose
source spells none of `BOUND_SPELLINGS`, which is sound only while the
analyser's own definition of a bounded call is exactly those two tokens:
a `timeout` keyword-argument name and a `**`-unpacked mapping. Both are
grammar, so a file spelling neither cannot carry one — but only until
the analyser grows a third.

So the filter is pinned from both sides: an AST read of the analyser
that goes red the moment a third bounding keyword is compared against a
call, and a behavioural pair that reads the real predicate on a file
carrying a bounded launch no `subprocess` spelling reaches, and on one
spelling no bounding token at all.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _launch_audit import bound_sites  # noqa: E402
from _launch_keep import (  # noqa: E402
    BOUND_SPELLINGS, in_launch_population)

# A bounded launch reached through the import machinery, which is the
# route `subprocess` never has to be spelled for. #1155 is the defect
# this shape reproduces: the old `'subprocess' in source` filter put
# this file out of the control's reach entirely.
REACHED_WITHOUT_SUBPROCESS = (
    'import importlib\n'
    '\n'
    '\n'
    'def probe():\n'
    "    proc = importlib.import_module('subprocess')\n"
    "    proc.run(['git', 'status'], timeout=30)\n")

# A file that spells no bounding token, so it cannot carry a bounded
# call whatever it does.
SPELLS_NO_BOUNDING = (
    'import json\n'
    '\n'
    '\n'
    'def probe():\n'
    '    return json.dumps({})\n')

# The unpacking limb on its own: `**` is spelled, `timeout` is not
# anywhere in the file, so a filter reading only `'timeout'` drops a
# file whose call the analyser still classifies as bounded.
UNPACKED_ONLY = (
    'import importlib\n'
    '\n'
    '\n'
    'def probe():\n'
    "    proc = importlib.import_module('subprocess')\n"
    "    limits = {'deadline': 30}\n"
    "    proc.run(['git', 'status'], **limits)\n")


def _compared_spellings(module):
    """The spellings the analyser compares a call's keywords against.

    Two shapes, both halves of the same test. The predicate reads
    `keyword.arg` directly, and the launch loop reads the mapping built
    from it, so `<constant> in keywords` names the same decision one
    step removed. `None` is the `**` limb spelled: the unpacking
    operator is a source token, not an AST value, so the source form is
    what the filter reads and the comparison carries.

    Scoped to `unplaced_bounded_call` by name, because the module
    compares `keyword.arg` for a decision that is not about bounding:
    `derives` asks for the `name=` of an `import_module` call. The pin
    asserts that the predicate still exists, so a rename breaks it
    loudly rather than vacuously.
    """
    predicate = next(
        (node for node in ast.walk(module)
         if isinstance(node, ast.FunctionDef)
         and node.name == 'unplaced_bounded_call'), None)
    assert predicate is not None, (
        'tests/_launch_audit.py holds no unplaced_bounded_call, so the '
        'predicates this pin reads are gone and the filter is unpinned')
    spelling_of = {None: '**'}
    compared = {
        spelling_of.get(node.comparators[0].value, node.comparators[0].value)
        for node in ast.walk(predicate)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Attribute)
        and node.left.attr == 'arg'
        and isinstance(node.comparators[0], ast.Constant)}
    compared |= {
        spelling_of.get(node.left.value, node.left.value)
        for node in ast.walk(module)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Constant)
        and node.comparators
        and isinstance(node.comparators[0], ast.Name)
        and node.comparators[0].id == 'keywords'}
    return compared


def test_every_spelling_the_analyser_bounds_on_is_one_the_filter_reads(tmp):
    """The filter's tokens ARE the analyser's, read out of its own AST.

    The filter's soundness is the claim that a file spelling none of
    `BOUND_SPELLINGS` carries no bounded call. That claim is only as
    long as the analyser's own definition, so the two are compared
    rather than asserted: add a third bounding keyword to
    `unplaced_bounded_call` and the pin goes red until the filter
    lists it, because a file spelling only the new token would
    otherwise leave the control's reach silently.
    """
    del tmp
    module = ast.parse(
        (Path(__file__).resolve().parent / '_launch_audit.py')
        .read_text(encoding='utf-8'))
    compared = _compared_spellings(module)
    assert compared, (
        'the analyser compares no spelling against a call\'s keywords, so '
        'this pin read nothing and the filter it guards is unpinned')
    unlisted = compared - set(BOUND_SPELLINGS)
    assert not unlisted, (
        f'the analyser bounds a call on {sorted(unlisted, key=str)}, which '
        f'BOUND_SPELLINGS does not list; a tracked file spelling only '
        f'that token would drop out of the launch control\'s reach without '
        f'a word: add it to {BOUND_SPELLINGS!r} in tests/_launch_keep.py')
    spare = set(BOUND_SPELLINGS) - compared
    assert not spare, (
        f'BOUND_SPELLINGS lists {sorted(spare)}, which the analyser never '
        f'compares a call against, so the filter keeps files it need not '
        f'read without buying any reach')


def test_a_bounded_launch_no_subprocess_spelling_reaches_is_still_found(tmp):
    """The #1155 shape: a bounded launch the old filter could not see.

    Every fixture both readings of the filter agreed on proved nothing
    about it. This one separates them: the launch is bounded and the
    old filter's token is absent, so the file is in the population
    only under a filter that reads what the control inspects.
    """
    del tmp
    assert 'import subprocess' not in REACHED_WITHOUT_SUBPROCESS, (
        'the fixture spells the token the #1155 filter looked for, so it '
        'cannot tell that filter from this one')
    assert in_launch_population('probe.py', REACHED_WITHOUT_SUBPROCESS), (
        'a tracked file carrying a bounded launch that reaches its launcher '
        'without spelling "import subprocess" fell out of the launch '
        "control's population; that is issue #1155 reopened")
    assert bound_sites(REACHED_WITHOUT_SUBPROCESS, 'probe.py'), (
        'the fixture carries a bounded launch the analyser reports no '
        'site for, so it pins nothing about the filter')


def test_a_file_spelling_no_bounding_token_is_outside_the_population(tmp):
    """The other half, and the only one that is sound rather than kind.

    The kept case above proves the filter does not skip a file with a
    site; this one proves what it does skip cannot have one, which is
    the whole reach argument. The two together are the filter; either
    alone is half of it.
    """
    del tmp
    assert not any(spelling in SPELLS_NO_BOUNDING
                   for spelling in BOUND_SPELLINGS), (
        'the fixture spells a bounding token, so it cannot tell a file the '
        'filter keeps from one it skips')
    assert bound_sites(SPELLS_NO_BOUNDING, 'probe.py') == [], (
        'the fixture reports a bounded site, so the filter may not skip it '
        'and the soundness argument does not hold')
    assert not in_launch_population('probe.py', SPELLS_NO_BOUNDING), (
        'a file spelling no bounding token is still read, which costs the '
        'audit a walk over the whole tree and buys no reach')


def test_the_unpacking_limb_alone_keeps_a_file_in_the_population(tmp):
    """The `**` half of `BOUND_SPELLINGS`, read on its own.

    A keyword-argument name and an unpacking operator are spelled
    differently, so the two limbs can be reasoned about separately and
    one can be dropped from the filter without the other noticing. This
    fixture spells `**` and never spells `timeout` anywhere, so a filter
    that kept only `'timeout'` would drop it — a file whose call the
    analyser classifies as bounded all the same.
    """
    del tmp
    assert 'timeout' not in UNPACKED_ONLY, (
        'the fixture spells "timeout", so it cannot tell a filter that '
        'reads the unpacking limb from one that does not')
    assert bound_sites(UNPACKED_ONLY, 'probe.py'), (
        'the analyser classifies no site in the fixture, so keeping it in '
        'the population buys nothing and pins nothing')
    assert in_launch_population('probe.py', UNPACKED_ONLY), (
        'a file whose only bound is a "**"-unpacked mapping fell out of '
        'the launch control\'s population: the unpacking limb is spelled '
        'as the operator, and no keyword-argument name stands in for it')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
