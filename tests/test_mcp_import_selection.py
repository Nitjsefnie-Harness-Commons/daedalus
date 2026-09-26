#!/usr/bin/env python3
"""The import-by-name operation reached through a CALL's callee VALUE.

A call's callee is a value, and the walk folds it before asking what it is.
Where the fold decides the value exactly — a subscript of a literal tuple
or list by a constant index inside it, however deeply nested — the value is
the operation and its argument is resolved exactly as the direct spelling
resolves it. Where the fold cannot, the walk asks the STORE side's own
property: does this expression's subtree mention the operation? A mention
is refused by spelling, site and remedy. A value that is not the operation
and does not mention it is left alone, so a container read that selects
something else is not a refusal.

The per-form cases below are a SAMPLE; the sweep at the bottom generates
its forms from a grammar and checks them against a runtime oracle, because
hand-typed spellings only ever prove the spellings that were thought of.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _mcp_selection_sweep  # noqa: E402
import _util  # noqa: E402


def _write_tree(directory, files):
    for name, source in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')


# Callee spellings that read the operation OUT of a value rather than
# naming it. None is a shape `_is_dynamic_import` answers, and that is the
# whole hole: the store side of the same operation asks a PROPERTY ("does
# this subtree mention the operation") and is right on every one of them,
# so two halves of one design disagreed about the same value.
# `(0, importlib.import_module)[0]` is deliberately NOT here — it is the
# near-miss that keeps the rule honest, and it has its own test.
OPERATION_SELECTIONS = (
    '[importlib.import_module][0]',
    '(importlib.import_module,)[0]',
    '(importlib.import_module if c else print)',
    '[importlib.import_module for _ in [0]][0]',
    '(0, importlib.import_module)[1]',
    '[[importlib.import_module]][0][0]',
    '[[[importlib.import_module]][0]][0][0]',
    '[importlib.import_module][i]',
    '[importlib.import_module][0:1]',
    '(*stars, importlib.import_module)[1]',
    "{'a': importlib.import_module}['a']",
    '[(0, importlib.import_module)][0][1]',
)

# The subset the fold cannot read exactly. Each can BE the operation at
# runtime and no static reading of it names which, so there is no target to
# resolve and the scan must refuse rather than guess one: the fold decides
# the VALUE, never the POSITION it declines to fold. A negative or a
# computed index is NOT here — the fold reads those positions, and what it
# selects is decided by what it selects, not by how the index is spelled.
UNREADABLE_SELECTIONS = (
    '(importlib.import_module if c else print)',
    '[importlib.import_module for _ in [0]][0]',
    '[importlib.import_module][i]',
    '[importlib.import_module][0:1]',
    '(*stars, importlib.import_module)[1]',
    "{'a': importlib.import_module}['a']",
)


def _spelling(callee):
    """The callee as a refusal names it: the AST's own unparse, which drops
    parentheses the source may have put around a conditional."""
    return ast.unparse(ast.parse(callee, mode='eval').body)


def _refuses_the_callee(_tmp, callee):
    """The scan refuses a call whose callee `callee` it cannot resolve.

    `_assert_refusal` in the closure suite checks the site and the reason;
    a refusal that fails to name the offending SPELLING leaves a maintainer
    reading the source to find which of several calls the closure is
    complaining about, and one that drops the remedy leaves them with a
    complaint instead, so both are checked here.
    """
    _write_tree(Path(_tmp), {'composition.py': (
        f'\nimport importlib\n\n\ndef load(c, i):\n'
        f"    return {callee}('os')\n")})
    try:
        _mcp_import_closure.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError as raised:
        message = str(raised)
        assert 'composition:6' in message, message
        assert _spelling(callee) in message, message
        assert 'reaches the import-by-name operation' in message, message
        assert message.endswith(_mcp_import_closure.CLOSURE_TAIL), message
    else:
        raise AssertionError(f'{callee} was silently skipped')


def test_a_callee_read_out_of_a_value_is_resolved_or_refused(_tmp):
    """A callee that IS the operation, spelled as a selection of a value, is
    either resolved to what the direct spelling resolves to or refused.

    The invariant, not a list of shapes: silently skipping any of them
    leaves the closure short of a module the code can import, which is the
    one failure the whole walk exists to prevent.
    """
    _write_tree(Path(_tmp), {'pkg/__init__.py': '',
                             'pkg/leaf.py': 'leaf = True\n'})
    for callee in OPERATION_SELECTIONS:
        _write_tree(Path(_tmp), {'composition.py': (
            f'\nimport importlib\n\n\ndef load(c, i):\n'
            f"    return {callee}('pkg.leaf')\n")})
        try:
            scanned = _mcp_import_closure.composition_scan_set(
                Path(_tmp) / 'composition.py', _tmp)
        except AssertionError as raised:
            assert 'composition:6' in str(raised), (callee, raised)
            assert _spelling(callee) in str(raised), (callee, raised)
        else:
            names = {path.relative_to(Path(_tmp)).as_posix()
                     for path in scanned}
            assert 'pkg/leaf.py' in names, (callee, names)


def _scan(_tmp, callee):
    """`resolved`, `refused`, or `silent` for one callee, with a resolvable
    `pkg/leaf.py` on disk so a resolved value is told apart from a silence."""
    _write_tree(Path(_tmp), {
        'pkg/__init__.py': '', 'pkg/leaf.py': 'leaf = True\n',
        'composition.py': ('\nimport importlib\n\n\ndef load(c, i):\n'
                           f'    return {callee}("pkg.leaf")\n')})
    try:
        scanned = _mcp_import_closure.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError:
        return 'refused'
    names = {path.relative_to(Path(_tmp)).as_posix() for path in scanned}
    return 'resolved' if 'pkg/leaf.py' in names else 'silent'


def test_a_callee_whose_value_is_a_lambda_is_not_refused(_tmp):
    """A lambda is a function: calling it returns whatever its body returns,
    so its VALUE reaches nothing, however the value is spelled.

    The three cases are one property at three depths — read directly, folded
    out of a container, and folded out of a nested one. An exemption keyed on
    the CALL's own spelling covers the first and refuses the other two, so the
    guard contradicts itself inside a single rule and refuses code that
    imports nothing.
    """
    for callee in ('(lambda: importlib.import_module)',
                   '[(lambda: 1), (lambda: importlib.import_module)][1]',
                   '[[(lambda: importlib.import_module)]][0][0]'):
        assert _scan(_tmp, callee) == 'silent', callee


def test_a_constant_negative_position_is_folded(_tmp):
    """`[-1]` is a position the fold reads, and the value it selects is
    resolved exactly as the positive spelling's is.

    The other half is the corpus's own rule: a position whose runtime value
    is clean is CLEAN, so the `0` the second case selects draws no refusal.
    """
    assert _scan(_tmp, '[importlib.import_module][-1]') == 'resolved'
    assert _scan(_tmp, '[0, importlib.import_module, 0][-1]') == 'silent'


def test_a_starred_literal_is_folded_by_the_elements_it_carries(_tmp):
    """`(*[...], 0)[1]` unpacks to a two-element tuple, so the position the
    index names is exactly readable and the `0` it selects imports nothing.

    A star over a value the literal does not carry is still undetermined, and
    still draws the refusal the fold declines to replace.
    """
    assert _scan(_tmp, '(*[importlib.import_module], 0)[1]') == 'silent'
    assert _scan(_tmp, '(*[importlib.import_module],)[0]') == 'resolved'
    _refuses_the_callee(_tmp, '(*stars, importlib.import_module)[1]')


def test_a_star_is_a_star_wherever_it_is_nested(_tmp):
    """A `Starred` element unpacks a LITERAL TUPLE exactly as it unpacks a
    list, and it does so at any depth: `(*(*[op],),)` is the one-element
    tuple the inner star produces.

    A star over a value whose positions this walk cannot know is still
    undetermined and still refused: a set's order is unspecified and a
    comprehension's length is a runtime value. Declining those is the
    declared limit, not the rule the two above stand on.
    """
    assert _scan(_tmp, '(*(importlib.import_module,),)[0]') == 'resolved'
    assert _scan(_tmp, '(*(*[importlib.import_module],),)[0]') == 'resolved'
    assert _scan(_tmp,
                 '(*(*[importlib.import_module, 0],),)[1]') == 'silent'
    _refuses_the_callee(_tmp, '(*{importlib.import_module},)[0]')
    _refuses_the_callee(
        _tmp, '(*(x for x in [0] if importlib.import_module), 0)[1]')


def test_a_container_element_that_is_itself_a_selection_is_folded(_tmp):
    """`([0, importlib.import_module][1],)[0]` is a one-element tuple whose
    ELEMENT is a selection, so folding the element and then the position is
    the recursion the fold already claims, read from the other direction."""
    assert _scan(_tmp, '([0, importlib.import_module][1],)[0]') == 'resolved'


def test_a_constant_index_folded_from_a_binop_is_read(_tmp):
    """`[0 + 0]` is a position the fold reads, by the same fold that already
    reads a constant string concatenation, so the index axis does not decline
    a spelling whose value the runtime has already settled.

    A float index is a different thing and stays declined: `lst[0.0]` raises
    `TypeError` at runtime, so there is no value to read either way.
    """
    assert _scan(_tmp, '[importlib.import_module][0 + 0]') == 'resolved'
    assert _scan(_tmp, '[0, importlib.import_module][0 + 0]') == 'silent'
    assert _scan(_tmp, '[importlib.import_module][0.0]') == 'refused'


def test_a_unary_plus_and_a_bool_are_both_constant_positions(_tmp):
    """`lst[+0]` is `lst[0]` and `lst[True]` is `lst[1]`, so the unary and
    the boolean are the same settled position by two more spellings.

    A float is the different thing and stays declined, for the reason the
    binop case already gives: `lst[0.0]` raises `TypeError` at runtime, so
    there is no position to read on either side of it.
    """
    assert _scan(_tmp, '[importlib.import_module][+0]') == 'resolved'
    assert _scan(_tmp, '[0, 0, importlib.import_module][+1]') == 'silent'
    assert _scan(_tmp, '[0, importlib.import_module][True]') == 'resolved'
    assert _scan(_tmp, '[0, 0, importlib.import_module][True]') == 'silent'
    assert _scan(_tmp, '[0, 0, importlib.import_module][False]') == 'silent'


def test_a_callee_the_fold_decides_to_be_a_container_is_not_refused(_tmp):
    """`[op]('pkg.leaf')` calls a LIST. The call raises `TypeError` before
    it reaches anything, so the callee is CLEAN however much the container
    it names mentions the operation.

    The walk already holds the value here — the fold returns the literal
    itself — so what it is refusing on is the CONTAINER's mention rather
    than the value's, which is the question it is not being asked. The
    generated sweep is blind to the class by construction, so this is the
    only control for it.

    A container the fold does NOT decide is the same class one level out,
    and stays refused: `[[op]][i]` may be a list, or a function.
    """
    for callee in ('[importlib.import_module]',
                   '(importlib.import_module,)',
                   '{importlib.import_module}',
                   "{'a': importlib.import_module}",
                   '[importlib.import_module for _ in [0]]',
                   '(importlib.import_module for _ in [0])'):
        assert _scan(_tmp, f'{callee}("pkg.leaf")') == 'silent', callee
    _refuses_the_callee(_tmp, '[[importlib.import_module]][i]')


def test_a_selection_the_fold_cannot_read_refuses(_tmp):
    """A selection that can BE the operation and cannot be read is refused
    by spelling, site and remedy, never resolved to a guess."""
    for callee in UNREADABLE_SELECTIONS:
        _refuses_the_callee(_tmp, callee)


def test_a_selection_of_a_known_other_value_is_not_refused(_tmp):
    """`(0, importlib.import_module)[0]('os')` calls `0`. It is NOT refused.

    The near-miss that pairs with the case above and keeps the rule from
    reading the whole CONTAINER instead of the value: a rule that refused
    every expression mentioning the operation would pass every bypass here
    and refuse this, and the module it would protect is one no call could
    have reached.

    The `('os')` is load-bearing rather than decorative — a subscript that
    is never CALLED is not a callee, so a version of this case without it
    exercised no rule at all and passed on a guard with the fold removed.
    """
    _write_tree(Path(_tmp), {'composition.py': '''
import importlib


def load():
    return (0, importlib.import_module)[0]('os')
'''})
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


def test_a_non_operation_attribute_call_is_left_alone(_tmp):
    """`importlib.util.find_spec` is a different operation on a different
    object, and stays accepted.

    The rule is about the operation's VALUE, not about a spelling that
    mentions importlib: widening it past the operation axis would refuse
    every attribute read off a tracked module.
    """
    _write_tree(Path(_tmp), {'composition.py': '''
import importlib.util


def load(name):
    return importlib.util.find_spec(name)
'''})
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


def test_a_folded_callee_resolves_exactly_as_the_direct_spelling(_tmp):
    """Where the fold decides the value, the two spellings agree exactly.

    Resolution is strictly better than refusal when the name argument reads
    as a constant, so a folded callee must reach the same target the direct
    spelling does — not a superset, and not silence.
    """
    _write_tree(Path(_tmp), {
        'composition.py': '''
import importlib


def load():
    return [[importlib.import_module]][0][0]('pkg.leaf')
''',
        'pkg/__init__.py': '',
        'pkg/leaf.py': 'leaf = True\n'})
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    names = {path.relative_to(Path(_tmp)).as_posix() for path in scanned}
    assert names == {'composition.py', 'pkg/__init__.py', 'pkg/leaf.py'}, names


# One container, two spellings: written out, and handed to a name first.
# The index is a name because a store hands the walk nothing to fold — the
# point being that the STORE side has always refused the stored spelling,
# so the inline one must not be the way past it.
STORED = '\nimport importlib\n\n\ndef load(i):\n    d = [0, importlib'
INLINE = '\nimport importlib\n\n\ndef load(i):\n    return [0, importlib'


def test_a_selection_agrees_with_a_store_of_the_same_container(_tmp):
    """The stored and inline spellings of one container are both closed.

    The store side refuses the stored one, at the store. What this pins is
    that the INLINE spelling of the same container is not the way past it:
    the two halves of one design agreeing is the property, and either half
    alone leaves the closure short.
    """
    for prefix, spelling in ((STORED, 'd[i]'),
                             (INLINE, '[0, importlib.import_module][i]')):
        _write_tree(Path(_tmp), {
            'composition.py': f'{prefix}.import_module]\n'
                              f"    return {spelling}('pkg.leaf')\n",
            'pkg/__init__.py': '',
            'pkg/leaf.py': 'leaf = True\n'})
        try:
            _mcp_import_closure.composition_scan_set(
                Path(_tmp) / 'composition.py', _tmp)
        except AssertionError as raised:
            assert 'composition:' in str(raised), raised
        else:
            raise AssertionError(f'{spelling} was silently skipped')


# The sweep: `_mcp_selection_sweep` generates the product of the grammar
# over its axes and classifies each form by running it. The three cases
# below are what the product is held to.
def test_every_generated_form_pays_what_it_owes(_tmp):
    """The whole generated product, against the oracle, form by form.

    Every class is counted and required to be non-empty: a sweep whose
    oracle decided nothing has measured nothing and would pass on any guard
    at all. The pinned does-not-reach class is what a rule that read the
    whole CONTAINER instead of the value would fail, so its count is
    reported next to the others.
    """
    _write_tree(Path(_tmp), {'pkg/__init__.py': '',
                             'pkg/leaf.py': 'leaf = True\n'})
    forms = _mcp_selection_sweep.sweep(
        Path(_tmp), _mcp_selection_sweep.generated())
    _mcp_selection_sweep.report(forms)
    assert all(_mcp_selection_sweep.tally(
        forms, 'oracle', _mcp_selection_sweep.CLASSES).values())
    unpaid = _mcp_selection_sweep.unpaid(forms)
    assert unpaid == f'0 unpaid of {len(forms)}:\n', unpaid


def test_the_refusals_the_sweep_buys_are_only_the_ones_it_owes(_tmp):
    """A refusal the rule does not owe, counted and pinned rather than
    tolerated.

    Every one of them is a position the fold DECLINES to read — a free name,
    a slice — on a container that mentions the operation, so the value it
    selects is unknown to this walk even where the oracle settles it. A
    negative, a computed and an out-of-range position are not in that set
    and are not here: the fold reads them, and what it reads settles the
    value rather than leaving it in doubt.

    Both the count and the SET are asserted, so a fold that widened or a
    marker that drifted shows here as a failure instead of as a number
    scrolling past.
    """
    _write_tree(Path(_tmp), {'pkg/__init__.py': '',
                             'pkg/leaf.py': 'leaf = True\n'})
    forms = _mcp_selection_sweep.sweep(
        Path(_tmp), _mcp_selection_sweep.generated())
    _mcp_selection_sweep.report(forms)
    bought = [form for form in forms
              if form['inline'] == 'refused'
              and form['oracle'] == 'does not reach']
    for form in bought:
        assert not form['pinned'] and form['mentions'], form
    assert len(bought) == 180, len(bought)
    assert sorted({form['step'] for form in bought}) == ['a name', 'a slice']


def test_a_stored_container_is_refused_exactly_when_it_mentions(_tmp):
    """The stored spelling of every generated container, against the store
    side's own property rather than the call side's.

    The store is the more conservative of the two by construction: it
    cannot know which element a reader will take, so it refuses a container
    that mentions the operation even where every index the reader could use
    selects something else. Pinning the store against ITSELF rather than
    against the inline verdict is what makes the agreement a real one — and
    the second assertion is the closure property that matters: no form that
    reaches the operation escapes BOTH spellings.
    """
    _write_tree(Path(_tmp), {'pkg/__init__.py': '',
                             'pkg/leaf.py': 'leaf = True\n'})
    forms = _mcp_selection_sweep.sweep(
        Path(_tmp), _mcp_selection_sweep.generated())
    _mcp_selection_sweep.report(forms)
    unclosed = [form['callee'] for form in forms
                if (form['store'] == 'refused') != form['mentions']]
    assert not unclosed, f'{len(unclosed)} stored forms: {unclosed[:5]}'
    escaping = [form['callee'] for form in forms
                if form['oracle'] == 'reaches'
                and form['store'] == 'silent'
                and form['inline'] == 'silent']
    assert not escaping, f'{len(escaping)} reaching forms escape: {escaping}'
    pairs = {(form['inline'], form['store']) for form in forms}
    print('  inline->stored verdict pairs: ' + ', '.join(
        f'{a}->{b}' for a, b in sorted(pairs)))


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
