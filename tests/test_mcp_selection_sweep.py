#!/usr/bin/env python3
"""The GENERATED universe the callee grammar produces, and what it is held to.

`_mcp_selection_sweep` generates the product of the grammar over its axes
and classifies each form by running it against a real `importlib`. The hand
cases in `test_mcp_import_selection.py` are a sample of the spellings
someone thought of; the four at the bottom are what the product is held to,
and a class the builders cannot name has no row at all — which is why the
class marker below is itself checked.

The two classes the CONTAINERS grammar could not name are here with their
generated rows because they are one class's coverage: a dict DISPLAY
carrying one key twice, and a LAMBDA in a position a fold selects. Both are
members of the stated property, neither was a member of the shipped
grammar, and each was a live bypass through a sweep that reported nothing
unpaid. Their hand boundary pins sit beside their generated ones so that a
row which stops discriminating and a case which stops agreeing are read in
the same place.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _mcp_selection_sweep  # noqa: E402
import _util  # noqa: E402

PROPERTY_CLASSES = _mcp_selection_sweep.PROPERTY_CLASSES


def _write_tree(directory, files):
    for name, source in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')


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


def _swept(_tmp):
    """The whole generated product, run against the oracle and the guard
    once for this process: three cases ask the same question of the same
    deterministic forms, and scanning every one of them per case triples a
    cost no assertion is buying."""
    _write_tree(Path(_tmp), {'pkg/__init__.py': '',
                             'pkg/leaf.py': 'leaf = True\n'})
    forms = _mcp_selection_sweep.sweep(
        Path(_tmp), _mcp_selection_sweep.generated())
    _mcp_selection_sweep.report(forms)
    return forms


# A dict DISPLAY keeps the LAST of two equal keys, so a reader that stops at
# the first reads an entry the runtime has already replaced. Each row is
# (the earlier key, the later key, the lookup that names both), and the
# first two are separate SPELLINGS of one value.
_DUPLICATE_KEYS = (('0', '0', '0'), ('0', 'False', 'False'),
                   ('1', 'True', '1'), ("'a'", "'a'", "'a'"))


def _repeated(earlier, later, earlier_value, later_value):
    return '{%s: %s, %s: %s}' % (earlier, earlier_value, later, later_value)


def test_a_dict_display_reads_the_last_of_two_equal_keys(_tmp):
    """`{0: print, 0: op}[0]` is `{0: op}`, so the entry the runtime keeps is
    the one the fold has to read: a reader that stops at the FIRST equal key
    decides the call clean and leaves the module out of the closure. Both
    directions are here, and a CONTAINER in the replaced entry is the
    near-miss such a reader decides clean rather than undecided.
    """
    for earlier, later, lookup in _DUPLICATE_KEYS:
        kept = _repeated(earlier, later, 'print', 'importlib.import_module')
        lost = _repeated(earlier, later, 'importlib.import_module', 'print')
        assert _scan(_tmp, f'{kept}[{lookup}]') == 'resolved', earlier
        assert _scan(_tmp, f'{lost}[{lookup}]') == 'silent', earlier
    assert _scan(_tmp, '{0: [0], 0: importlib.import_module}[0]') \
        == 'resolved'
    assert _scan(_tmp, '{0: importlib.import_module, 0: [0]}[0]') == 'silent'
    # A projection reads the same value, and a key the display does not
    # carry is still the `KeyError` the runtime raises.
    assert _scan(_tmp, '{0: print, 0: importlib.import_module}[0].__call__') \
        == 'resolved'
    assert _scan(_tmp, '{0: print, 0: importlib.import_module}[1]') == 'silent'


def test_a_lambda_produces_its_return_wherever_it_was_reached(_tmp):
    """A lambda is a function, and CALLING it produces its return — however
    it was reached and however many arguments the call supplies. A fold
    that returned the lambda itself, or that gated the arity on an empty
    argument list, made every one of these reach nothing.

    Both sides of the boundary are here: a call the signature accepts
    produces the body, a call it does not is the `TypeError` before any
    value exists, and near-misses all landing on the clean side would hold
    the shape of the rule and not its edge.

    The KEYWORD arms are pinned the same way, because a name and a `**`
    unpack are two spellings of one binding and a keyword-only parameter
    with a default is a parameter that is not required: each has a row the
    runtime produces the body on and a row it raises on, so a rule that
    accepted every name, or required every keyword-only parameter, fails
    here rather than passing on the shape.
    """
    for callee in ('(lambda: importlib.import_module)()',
                   '[(lambda: importlib.import_module)][0]()',
                   '[[(lambda: importlib.import_module)]][0][0]()',
                   '{"a": (lambda: importlib.import_module)}["a"]()',
                   '(*[(lambda: importlib.import_module)],)[0]()',
                   '[[(lambda: importlib.import_module)][0]][0]()',
                   # one row per way a signature can be satisfied
                   '(lambda a: importlib.import_module)(1)',
                   '(lambda a=0: importlib.import_module)(1)',
                   '(lambda *a: importlib.import_module)(1)',
                   '(lambda **k: importlib.import_module)(a=1)',
                   '(lambda **k: importlib.import_module)(**{"x": 1})',
                   '(lambda a, *, b: importlib.import_module)(1, b=2)',
                   '(lambda *, b=0: importlib.import_module)()',
                   '[(lambda a: importlib.import_module)][0](1)'):
        assert _scan(_tmp, callee) == 'resolved', callee
    for callee in ('(lambda a: importlib.import_module)()',
                   '(lambda a, b: importlib.import_module)(1)',
                   '(lambda a: print)(1)',
                   '(lambda: importlib.import_module)(x=1)',
                   '(lambda: importlib.import_module)(**{"x": 1})',
                   '(lambda *, b: importlib.import_module)()',
                   '[(lambda a: importlib.import_module)][0]()',
                   # A function's value is a FUNCTION, so the projection of
                   # a lambda is not the lambda's return.
                   '[(lambda: importlib.import_module)][0].__call__'):
        assert _scan(_tmp, callee) == 'silent', callee


def test_every_class_of_the_property_has_a_discriminating_row(_tmp):
    """Each class the grammar cannot spell has generated rows, and its rows
    DISAGREE with each other.

    A row is not coverage: `[(lambda a: op)][0]()` and `[(lambda: op)][0](1)`
    both raise, both are silent, and together they hold a shape and not the
    boundary. The class marker is what a count is read against, so it names
    the class and not the spelling — a marker nothing observes is a
    comment, and a spelling in the marker is the bug this case exists for.
    """
    forms = _swept(_tmp)
    assert set(PROPERTY_CLASSES) == {
        name for form in forms for name in form['classes']}
    for name in PROPERTY_CLASSES:
        rows = [form for form in forms if name in form['classes']]
        assert rows, name
        assert {form['oracle'] for form in rows} >= {'reaches',
                                                     'does not reach'}, name


def _owed(form):
    """The verdicts a CORRECT guard may answer for this form.

    Stated here rather than read from `duty`, because the instrument's own
    allowance cannot be the witness that it has not been widened.

    A verdict is wrong when the value the walk settled contradicts it, so
    RESOLVED is right only where the walk settled the value to the operation
    and SILENT only where it settled it to something else. Where the walk
    cannot settle the value at all the honest answer is not a verdict but the
    walk's own property: a form that CARRIES the operation is REFUSED, and
    one that carries nothing to call is silent. A refusal is owed in one place
    more, where the value IS the operation, because resolving it and refusing
    it agree about the reach — and owed nowhere else, since refusing a
    settled form that reaches nothing is the false positive that costs a
    real closure entry.
    """
    if not form['pinned']:
        return {'refused'} if form['carries'] else {'silent'}
    if form['oracle'] == 'reaches':
        return {'resolved', 'refused'}
    return {'silent'}


def test_every_generated_form_pays_what_it_owes(_tmp):
    """The whole generated product, against the oracle, form by form.

    Every class is counted and required to be non-empty: a sweep whose
    oracle decided nothing has measured nothing and would pass on any guard
    at all. The pinned does-not-reach class is what a rule that read the
    whole CONTAINER instead of the value would fail, so its count is
    reported next to the others. The ALLOWANCE the zero is read against is
    the contract and not `duty` itself, form by form, and a control says
    `unpaid` still names a form the contract forbids.
    """
    forms = _swept(_tmp)
    assert all(_mcp_selection_sweep.tally(
        forms, 'oracle', _mcp_selection_sweep.CLASSES).values())
    unpaid = _mcp_selection_sweep.unpaid(forms)
    assert unpaid == f'0 unpaid of {len(forms)}:\n', unpaid
    for form in forms:
        allowance = set(_mcp_selection_sweep.duty(form))
        assert allowance == _owed(form), (
            f'{form["callee"]}: allowance {sorted(allowance)} is not the '
            f'contract {sorted(_owed(form))}')
    unpinned = next(form for form in forms if not form['pinned'])
    probe = dict(unpinned,
                 inline='silent' if unpinned['carries'] else 'refused')
    assert _mcp_selection_sweep.unpaid([probe]).startswith('1 unpaid of 1:'), \
        'unpaid did not name a form the contract forbids'


def test_the_refusals_the_sweep_buys_are_only_the_ones_it_owes(_tmp):
    """A refusal the rule does not owe, counted and pinned rather than
    tolerated.

    Every one of them is a position the fold DECLINES to read — a free
    name, and that is the only one the grammar now has — on a container
    that carries the operation, so the value it selects is unknown to this
    walk even where the oracle settles it. A negative, a computed, an
    out-of-range, a float, a slice and a key position are not in that set
    and are not here: the fold reads each of them, and one it reads either
    selects an element or names nothing at all.

    Both the count and the SET are asserted, so a fold that widened, a step
    that stopped being settled, or a marker that drifted shows here as a
    failure instead of as a number scrolling past.
    """
    forms = _swept(_tmp)
    bought = [form for form in forms
              if form['inline'] == 'refused'
              and form['oracle'] == 'does not reach']
    for form in bought:
        assert not form['pinned'] and form['carries'], form
    assert len(bought) == 52, len(bought)
    assert sorted({form['step'] for form in bought}) == ['a name']


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
    forms = _swept(_tmp)
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
