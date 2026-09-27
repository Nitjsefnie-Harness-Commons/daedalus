#!/usr/bin/env python3
"""The GENERATED universe the callee grammar produces, and what it is held to.

`_mcp_selection_sweep` generates the product of the grammar over its axes
and classifies each form by running it against a real `importlib`. The hand
cases in `test_mcp_import_selection.py` are a sample of the spellings
someone thought of; the five at the bottom are what the product is held to,
and a class the builders cannot name has no row at all — which is why the
class marker below is itself checked.

The four classes the CONTAINERS grammar could not name are here with their
generated rows because they are one class's coverage: a dict DISPLAY
carrying one key twice, a LAMBDA in a position a fold selects, a BUILTIN
behind a binding the module may or may not have made, and a PARAMETER the
call supplies by name rather than by position. All four are members of the
stated property, none was a member of the shipped grammar, and each was a
live bypass through a sweep that reported nothing unpaid. The first two
keep their hand boundary pins beside their generated ones, so that a row
which stops discriminating and a case which stops agreeing are read in the
same place. The last two are held in their own suites, which also carry the
shapes the oracle cannot run — a comprehension scope, a sibling scope on one
line, a store under a `global` declaration and a decorator's own scope for
the binding class, an unpacked `*args` and an unreadable `**` for the
signature class — and what this file adds for each is the twin that differs
only in the module's own source or only in the arguments.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _mcp_lambda_sweep  # noqa: E402
import _mcp_selection_sweep  # noqa: E402
import _util  # noqa: E402

PROPERTY_CLASSES = _mcp_selection_sweep.PROPERTY_CLASSES


def _write_tree(directory, files):
    for name, source in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')


def _callee_scan(_tmp, callee):
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
    once for this process: four cases ask the same question of the same
    deterministic forms, and scanning every one of them per case multiplies
    a cost no assertion is buying."""
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
        assert _callee_scan(_tmp, f'{kept}[{lookup}]') == 'resolved', earlier
        assert _callee_scan(_tmp, f'{lost}[{lookup}]') == 'silent', earlier
    assert _callee_scan(_tmp, '{0: [0], 0: importlib.import_module}[0]') \
        == 'resolved'
    assert _callee_scan(
        _tmp, '{0: importlib.import_module, 0: [0]}[0]') == 'silent'
    # A projection reads the same value, and a key the display does not
    # carry is still the `KeyError` the runtime raises.
    assert _callee_scan(
        _tmp, '{0: print, 0: importlib.import_module}[0].__call__') \
        == 'resolved'
    assert _callee_scan(
        _tmp, '{0: print, 0: importlib.import_module}[1]') == 'silent'


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
    here rather than passing on the shape. What these rows hold is the
    BEHAVIOUR these limbs produce, checked against a real `importlib` and
    unchanged by the keyword-binding defect filed as #1211 — not a claim
    that a limb cannot be driven to that defect's answer, which two probes
    can: `(lambda x: op)(**{'x': 1})` and `(lambda *, b: op)(**{'b': 1})`
    both reach and both scan clean. None of the pinned spellings is one of
    them, so none of them freezes a defect; that was a decision about which
    spellings to pin, not about which limbs are clean.
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
        assert _callee_scan(_tmp, callee) == 'resolved', callee
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
        assert _callee_scan(_tmp, callee) == 'silent', callee


def test_a_builtin_is_read_only_where_the_module_leaves_it(_tmp):
    """The class's sides, in the guard's OWN verdicts.

    `test_every_class_of_the_property_has_a_discriminating_row` checks that
    a class's rows disagree by ORACLE class; this checks the other half, and
    it checks it the only way that can fail: a row and its twin differ by
    lines of the module's own source and by nothing in the callee, so every
    pair has to come back with two different verdicts. A rule that read a
    replaced name as the builtin resolves a call that raises, and one that
    read an unreplaced alias as anything else declines a position the runtime
    settles; neither is visible in a count.

    There are four carriers and THREE of them are twins of the first — the
    name replaced, the name replaced from a nested scope under a `global`
    declaration, and the binding under a condition the module may not take —
    because a name the module has not bound is a different question from one
    it has bound to something else, and a builder that generated only the
    straight-line one could not tell a rule that reads when a statement ran
    from a rule that does not. The `global` twin is the one a rule cannot
    see coming: `symtable` reports that root symbol imported and not
    assigned, so the store is invisible to anything that reads it.

    The conditional rows split once more, and that is the point of them: a
    name the module bound to ITSELF is the builtin whether or not the
    statement ran, so those rows are still decided, while an ALIAS the module
    may never have bound is a name this walk cannot account for and is
    refused. A rule that asked only the spelling would resolve both. The
    `global` rows do NOT split that way: a store takes the name back
    whichever spelling brought it in, own name included.
    """
    forms = _swept(_tmp)
    rows = [form for form in forms
            if 'a builtin behind a binding' in form['classes']]
    assert len(rows) == 320, len(rows)
    by_step = {(form['callee'], form['step']): form['inline'] for form in rows}

    def _twins(suffix):
        return [(callee, step) for callee, step in by_step
                if step.endswith(suffix)]

    conditional = _twins(' under a condition')
    assert len(conditional) == 80, len(conditional)

    def _twin(callee, step):
        return by_step[(callee, step[:-len(' under a condition')])]

    alias = [(callee, step) for callee, step in conditional
             if step.startswith('bound under an alias ')]
    own = [(callee, step) for callee, step in conditional
           if step.startswith('bound under its own name ')]
    assert len(alias) == 40 and len(own) == 40, (len(alias), len(own))
    # An ALIAS the module may never have bound is a name this walk cannot
    # account for, and the row is the undecided class: refused, where its
    # straight-line twin is decided. That disagreement is the whole axis.
    for callee, step in alias:
        assert by_step[(callee, step)] == 'refused', (callee, step)
        assert _twin(callee, step) != 'refused', (callee, step)
    # A name the module bound to ITSELF is the builtin either way, so those
    # rows stay decided and agree with their twin — the case that says the
    # axis is about the NAME and not about the statement.
    for callee, step in own:
        assert by_step[(callee, step)] == _twin(callee, step), (callee, step)
    # A store under a `global` declaration takes the name back under BOTH
    # spellings, and every one of these rows is refused where its twin is
    # decided — so a rule that reads the symbol table without reading the
    # declarations resolves half of them.
    rebound = _twins(' and rebound under a global')
    assert len(rebound) == 80, len(rebound)
    for callee, step in rebound:
        assert by_step[(callee, step)] == 'refused', (callee, step)
        assert by_step[(callee, step[:-len(' and rebound under a global')])] \
            in ('resolved', 'silent'), (callee, step)
    # A name the module has NOT bound to the builtin itself is never resolved.
    undecided = [form for form in rows
                 if form['step'].endswith(' and replaced')
                 or form['step'].startswith('bound under an alias under a')
                 or form['step'].endswith(' and rebound under a global')]
    assert len(undecided) == 200, len(undecided)
    assert not [form['callee'] for form in undecided
                if form['inline'] == 'resolved']
    undecided_steps = {form['step'] for form in undecided}
    assert all(form['pinned'] for form in rows
               if form['step'] not in undecided_steps)
    assert {form['oracle'] for form in rows} == {
        'reaches', 'does not reach', 'raises'}


def test_a_parameter_supplied_by_name_is_supplied(_tmp):
    """The class's shapes, checked rather than trusted, and every one of them
    in the guard's OWN verdicts.

    The oracle is checked form by form; this case checks the thing the oracle
    cannot see, which is the GENERATOR'S COVERAGE. A shape the walk must
    answer and the builder never emits is a rule that may be wrong with
    nothing to catch it, so the builder states its shapes in
    `BINDING_SHAPES` and the first assertion is that the rows are exactly
    those — every declared shape crossed, and nothing crossed that is not
    declared. A shape the builder cannot emit is declared in
    `UNDECIDED_SHAPES` instead, with the reason, and the hand suite holds a
    case for each.

    Then the verdicts, one shape at a time. A call that binds its arguments
    produces the operation and resolves; a call whose own binding is a
    `TypeError` names nothing and is clean. The refusal shapes are separate
    rows rather than one word, which is what makes a rule that gets ONE of
    them wrong distinguishable from a rule that gets them all right.
    """
    forms = _swept(_tmp)
    rows = [form for form in forms
            if 'a parameter supplied by name' in form['classes']]
    crossed = {form['step'] for form in rows}
    declared = {shape for shape, _ in _mcp_lambda_sweep.BINDING_SHAPES}
    missing = sorted(declared - crossed)
    assert crossed == declared, (missing, sorted(crossed - declared))
    for shape, side in _mcp_lambda_sweep.BINDING_SHAPES:
        shaped = [form for form in rows if form['step'] == shape]
        assert shaped, shape
        for form in shaped:
            if side == 'raises':
                # The SIDE is a prediction and the ORACLE is the
                # measurement; a shape predicted to refuse and read by the
                # walk as a raise is the class failing at its own claim.
                assert form['oracle'] == 'raises', form
                assert form['inline'] == 'silent', form
            else:
                assert form['inline'] == (
                    'resolved' if form['oracle'] == 'reaches' else 'silent'), \
                    form
    assert len(rows) == 13800, len(rows)
    signatures = {form['kind'] for form in rows}
    assert len(signatures) == 144, len(signatures)
    refusals = {shape for shape, side
                in _mcp_lambda_sweep.BINDING_SHAPES if side == 'raises'}
    one_sided = []
    for kind in signatures:
        of = [form for form in rows if form['kind'] == kind]
        shapes = {form['step'] for form in of}
        resolving = any(form['inline'] == 'resolved' for form in of)
        if shapes & refusals:
            # A signature the product can make refuse carries BOTH sides: the
            # boundary is inside it, and a rule that reads only one binding
            # form has a row here that fails.
            assert resolving, kind
        else:
            # A signature whose only feature is a `*args` is not one of
            # these: it soaks up every positional, so no call can make its
            # binding raise, and it declares nothing that can be left out.
            # They are named rather than papered over, so a signature that
            # LOSES its other side shows here.
            one_sided.append(kind)
    assert sorted(one_sided) == ['a *a lambda'], sorted(one_sided)


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
    allowance cannot be the witness that it has not been widened. A
    restatement beside the instrument still moves with it, so what stops
    the two being widened together is the `_FORBIDDEN` table below, which
    is written out rather than derived from either.

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


# The verdict a CORRECT guard may NOT answer a pinned form of each oracle
# class, written out here instead of derived from `duty` or from `_owed`.
# Those two agree by construction, so a widening that moves them together
# leaves every generated form paid and the two restatements equal: the pair
# witnesses DISAGREEMENT, never content. An entry here is a statement no
# lockstep edit of the other two can satisfy without editing this as well.
_FORBIDDEN = (
    ('reaches', 'silent'),
    ('does not reach', 'resolved'),
    ('does not reach', 'refused'),
    ('raises', 'resolved'),
    ('raises', 'refused'),
)


def test_a_pinned_form_may_not_be_answered_what_the_contract_forbids(_tmp):
    """A form the oracle REACHES may not be answered `silent`, and one it
    does not reach or raises on may not be answered `resolved` or `refused`.

    The sharpest cell is the first: a guard that answers a reaching form
    `silent` omits the module the runtime imports, which is this file's
    whole defect class, and it does so with nothing refused anywhere. Every
    pinned form of a class is offered the verdict its class forbids and has
    to come back named, so a widening that answers one builder's forms
    differently from another's is named too.
    """
    forms = _swept(_tmp)
    for oracle, verdict in _FORBIDDEN:
        for form in [f for f in forms
                     if f['pinned'] and f['oracle'] == oracle]:
            unpaid = _mcp_selection_sweep.unpaid([dict(form, inline=verdict)])
            assert unpaid.startswith('1 unpaid of 1:'), (
                f'{form["callee"]}: a {oracle} form answered {verdict} is '
                f'paid: {unpaid}')


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
    walk even where the oracle settles it. A negative, a
    computed, an out-of-range, a float, a slice and a key position are not
    in that set and are not here: the fold reads each of them, and one it
    reads either selects an element or names nothing at all.

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
