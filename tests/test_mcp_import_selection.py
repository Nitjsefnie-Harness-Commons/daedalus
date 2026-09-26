#!/usr/bin/env python3
"""The import-by-name operation reached through a CALL's callee VALUE.

A call's callee is a value, and the walk folds it before asking what it is.
Where the fold decides the value exactly — a wrapper that produces the value
it wraps, a subscript of a literal sequence by a position inside it or of a
literal mapping by a key it carries, however deeply nested — the value is
the operation and its argument is resolved exactly as the direct spelling
resolves it. Where the fold cannot decide, the walk asks the STORE side's
own property: does this expression's subtree mention the operation? A
mention is refused by spelling, site and remedy. A value the runtime
provably cannot reach through is CLEAN, and a value that is not the
operation and does not mention it is left alone, so a container read that
selects something else is not a refusal.

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
# naming it. None is a shape `is_dynamic_import` answers, and that is the
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
    "{'a': importlib.import_module}[k]",
    '{**{"b": 0}, "a": importlib.import_module}["a"]',
    '(importlib.import_module or print).__call__',
    'getattr([0, importlib.import_module][i], \'__call__\')',
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
    the CALL's own spelling covers the first and refuses the other two.
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
    a spelling whose value the runtime has already settled."""
    assert _scan(_tmp, '[importlib.import_module][0 + 0]') == 'resolved'
    assert _scan(_tmp, '[0, importlib.import_module][0 + 0]') == 'silent'


def test_a_constant_index_folded_from_wider_arithmetic_is_read(_tmp):
    """`*`, `//`, `%` and `**` over two numeric constants are the same settled
    position `+` and `-` are, so the index axis folds them too — by asking
    Python's own operator for the value rather than by reading the spelling,
    which is what leaves no one operator to forget.

    A walrus binds a name and produces the same value, `bool(0)` is `0`, and
    `/` and a division by zero are the two that cannot: a float is not a
    position and a `ZeroDivisionError` raises before the container is read.
    """
    for index in ('0 * 1', '1 - 1', '1 % 1', '0 ** 1', '(j := 0)',
                  'bool(0)', '+0', '-(-0)'):
        assert _scan(_tmp, f'[importlib.import_module][{index}]') \
            == 'resolved', index
        assert _scan(_tmp, f'[0, importlib.import_module][{index}]') \
            == 'silent', index
    for index in ('2 - 1', '1 + 0', '1 * 1', '1 ** 1', '1 // 1'):
        assert _scan(_tmp, f'[importlib.import_module][{index}]') \
            == 'silent', index
        assert _scan(_tmp, f'[0, importlib.import_module][{index}]') \
            == 'resolved', index
    assert _scan(_tmp, '[importlib.import_module][0 / 1]') == 'silent'
    assert _scan(_tmp, '[importlib.import_module][1 // 0]') == 'silent'


def test_a_position_the_runtime_cannot_reach_is_clean(_tmp):
    """A position that provably raises names nothing, so the call raises
    before it reaches anything and the container's mention is beside the
    question.

    An out-of-range index, a `None`, a string and a float all raise on every
    container the fold reads, and a dict key the literal does not carry
    raises `KeyError`. Refusing them is a false refusal on code that imports
    nothing, and this is the corpus's own decision: a decided value that
    provably cannot call anything is CLEAN.
    """
    for index in ('4', '-4', 'None', "'x'", '1.5', '0 / 1', '1 // 1', 'True'):
        assert _scan(_tmp, f'[importlib.import_module][{index}]') \
            == 'silent', index
    assert _scan(_tmp, "{'a': importlib.import_module}['zzz']") == 'silent'
    assert _scan(_tmp, '{0: importlib.import_module}[1]') == 'silent'


# The wrappers a value passes through on its way to being called. Python
# spells "this object is callable" as `X.__call__`, and a lambda called
# with no arguments produces its own body; both produce the value they
# wrap, so a value read through one is the value it wraps.
PROJECTIONS = (
    'importlib.import_module.__call__',
    'importlib.import_module.__call__.__call__',
    "getattr(importlib.import_module, '__call__')",
    '[importlib.import_module][0].__call__',
    '[[importlib.import_module]][0][0].__call__',
    "{'a': importlib.import_module}['a'].__call__",
    'getattr([importlib.import_module][0], \'__call__\')',
)

# The near-miss the projection must not break: each projects a value the
# fold DECIDED, and none of those is the operation. `X.__call__` raises
# `AttributeError` for each, so a refusal here is a false one.
PROJECTION_CLEAN = (
    '(0, importlib.import_module)[0].__call__',
    "[0, importlib.import_module][0].__call__",
    "{'a': 0, 'b': importlib.import_module}['a'].__call__",
    '[print, importlib.import_module][0].__call__',
    'importlib.util.find_spec.__call__',
)


def test_a_callable_projection_of_the_operation_resolves_it(_tmp):
    """`op.__call__` IS the operation: `__call__` is how Python spells "this
    object is callable", so calling the projection calls the operation.

    Every form here is RESOLVED rather than refused, because the value the
    projection denotes is the operation and the argument is a constant name
    the direct spelling resolves. A guard that closed the route by REFUSING
    would be right about the reach and wrong about a module it can name —
    and the route is named in the issue's own second comment.
    """
    for callee in PROJECTIONS:
        assert _scan(_tmp, callee) == 'resolved', callee


def test_a_callable_projection_of_a_decided_value_is_clean(_tmp):
    """The projection is read as the VALUE it projects, so the near-miss
    pair survives it.

    Each of these projects a value the fold already decided and none is the
    operation, which is the same honesty property the un-projected pair
    stands on: a rule that read the projected CONTAINER instead of the
    projected value would refuse all of them, and each raises before it
    reaches anything.
    """
    for callee in PROJECTION_CLEAN:
        assert _scan(_tmp, callee) == 'silent', callee


def test_a_projection_delivered_to_a_name_refuses(_tmp):
    """A projection stored under a name is a DELIVERY: `d = op.__call__` and
    then `d('pkg.leaf')` really imports.

    The store side asks the same property the call side does, and the value
    it projects is the operation, so the store is refused. A store the
    property answered about the SPELLING would see an attribute named
    `__call__` and pass this.
    """
    _write_tree(Path(_tmp), {'composition.py': '''
import importlib


def load():
    d = importlib.import_module.__call__
    return d("pkg.leaf")
'''})
    try:
        _mcp_import_closure.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError as raised:
        assert 'composition:6' in str(raised), raised
    else:
        raise AssertionError('the projected delivery was silently skipped')


# A lambda this walk can call with no arguments produces its own body, so
# `(lambda: op)()` is a value the fold reads. A lambda with a REQUIRED
# parameter cannot be called that way and raises, which is the near-miss.
NULLARY_LAMBDAS = (
    '(lambda: importlib.import_module)()',
    '(lambda *a: importlib.import_module)()',
    '(lambda a=0: importlib.import_module)()',
    '(lambda **k: importlib.import_module)()',
)


def test_a_nullary_lambda_call_of_the_operation_resolves_it(_tmp):
    """`(lambda: op)()` produces the operation, and it is the CALLEE here, so
    it reaches the module rather than being carried as a value. The sibling
    code-eval axis calls the same shape a projection: another way a value is
    produced rather than named.
    """
    for callee in NULLARY_LAMBDAS:
        assert _scan(_tmp, callee) == 'resolved', callee


def test_a_lambda_that_cannot_be_called_with_no_arguments_is_clean(_tmp):
    """`(lambda a: op)()` raises `TypeError` before it produces anything, so
    there is no value to read and nothing to refuse.

    Its call-site lambda counterpart — the lambda itself, uncalled — is the
    call-result limit and stays silent too, so the two halves of the lambda
    rule are pinned from both directions.
    """
    assert _scan(_tmp, '(lambda a: importlib.import_module)()') == 'silent'
    assert _scan(_tmp, '(lambda a=0: print)()') == 'silent'


def test_a_getattr_whose_key_the_walk_cannot_read_is_its_object(_tmp):
    """`getattr(V, k)` with a key this walk cannot read MAY be reading
    `__call__`, so the walk reads the lookup as the value it reads off.

    It REFUSES rather than resolving, because the fold does not know the
    value: the key may be `__call__` and it may be anything else, so there
    is no one module to resolve to and the honest answer is the refusal the
    issue asks for. A readable key is an ordinary lookup instead — a
    different constant names an ordinary attribute, and an object that is
    not the operation is left alone however its key is spelled.
    """
    _refuses_the_callee(_tmp, 'getattr(importlib.import_module, k)')
    assert _scan(_tmp, "getattr(importlib.import_module, 'other')") == 'silent'
    assert _scan(_tmp, 'getattr(importlib.util, k)') == 'silent'
    assert _scan(_tmp, 'getattr(print, k)') == 'silent'


def test_a_keyed_selection_of_a_literal_dict_is_folded(_tmp):
    """`{'a': op}['a']` selects the operation by its KEY, and a dict is a
    literal container the fold reads like any other.

    This is the near-miss pair in its dict spelling and the half that keeps
    the rule honest: `{'a': 0, 'b': op}['a']` calls `0`, and a fold that
    read the whole dict would refuse it. Both halves are here because one
    without the other proves nothing — a fold that declined every dict
    fails the first and passes the second.
    """
    assert _scan(_tmp, "{'a': importlib.import_module}['a']") == 'resolved'
    assert _scan(_tmp, "{'a': 0, 'b': importlib.import_module}['a']") \
        == 'silent'
    assert _scan(_tmp, "{'b': 0, 'a': importlib.import_module}['a']") \
        == 'resolved'
    assert _scan(_tmp, "(lambda *a: {'a': importlib.import_module}['a'])") \
        == 'silent'


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

    What a refusal here refuses on is the CONTAINER's mention rather than the
    value's, which is the question it is not being asked. The generated
    sweep is blind to the class by construction — a subscript never reaches a
    comprehension as its base — so this is the only control for it, and the
    set and dict comprehensions are here because nothing else names them.

    A container the fold does NOT decide is the same class one level out,
    and stays refused: `[[op]][i]` may be a list, or a function.
    """
    for callee in ('[importlib.import_module]',
                   '(importlib.import_module,)',
                   '{importlib.import_module}',
                   "{'a': importlib.import_module}",
                   '[importlib.import_module for _ in [0]]',
                   '(importlib.import_module for _ in [0])',
                   '{importlib.import_module for _ in [0]}',
                   '{k: importlib.import_module for k in [0]}',
                   '{importlib.import_module for _ in [0]}.__call__',
                   '{k: importlib.import_module for k in [0]}.__call__'):
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

    Every one of them is a position the fold DECLINES to read — a free name
    or a slice, and those are the only two the grammar has — on a container
    that carries the operation, so the value it selects is unknown to this
    walk even where the oracle settles it. A negative, a computed, an
    out-of-range, a float and a key position are not in that set and are not
    here: the fold reads each of them, and one it reads either selects an
    element or names nothing at all.

    Both the count and the SET are asserted, so a fold that widened, a step
    that stopped being settled, or a marker that drifted shows here as a
    failure instead of as a number scrolling past.
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
        assert not form['pinned'] and form['carries'], form
    assert len(bought) == 196, len(bought)
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
