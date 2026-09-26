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
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
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


# The sweep. The forms above are a sample; these are the PRODUCT of a
# grammar over its axes, checked against a runtime oracle, so a spelling
# nobody thought of is covered too.

_FILLER = '0'

# How the generated source NAMES the operation. The second is the alias a
# `from ... import ... as` binds, which the guard's own map follows. It is
# here because a marker keyed on the first spelling cannot see it: such a
# marker calls the operation absent from a container that carries it, and
# the sweep then demands the silence the guard correctly refuses to give.
_BINDINGS = (
    ('a module attribute', 'import importlib\n',
     'importlib.import_module'),
    ('an alias', 'from importlib import import_module as im\n', 'im'),
)

# Where the operation sits and how wide the container is. The three numbers
# are the operation's position and the container's width, so every
# container also has a position that is NOT the operation's — which is what
# the `elsewhere` index reads, and what makes a discriminating near-miss
# possible at every position rather than only at the first.
_POSITIONS = (
    ('first', 0, 2),
    ('last', 1, 2),
    ('middle', 1, 3),
    ('after two', 2, 3),
)

# A container's spelling, and the element positions of `e` that spelling
# places. The second half is the generator's own answer to "does this
# container carry the operation": a builder that takes only `e[0]` places it
# only when the operation IS at position zero, and which spelling the
# operation wears does not change that. The conditional and the disjunction
# take the operation only, because a branch is chosen by a runtime value
# rather than by a position.
_CONTAINERS = {
    'a list literal': lambda e, at, op: (
        '[' + ', '.join(e) + ']', range(len(e))),
    'a tuple literal': lambda e, at, op: (
        '(' + ', '.join(e) + ',)', range(len(e))),
    'a dict literal': lambda e, at, op: (
        '{' + ', '.join(chr(97 + n) + ': ' + item
                        for n, item in enumerate(e)) + '}', range(len(e))),
    'a set literal': lambda e, at, op: (
        '{' + ', '.join(e) + '}', range(len(e))),
    'a list comprehension': lambda e, at, op: (
        '[' + e[0] + ' for _ in [0]]', (0,)),
    'a generator expression': lambda e, at, op: (
        '(' + e[0] + ' for _ in [0])', (0,)),
    'a conditional': lambda e, at, op: (
        '(' + op + ' if c else print)', (at,)),
    'a disjunction': lambda e, at, op: (
        '(' + op + ' or print)', (at,)),
    'a starred unpack': lambda e, at, op: (
        '(*[' + e[0] + '],)', (0,)),
    'a nested literal': lambda e, at, op: (
        '[[' + ', '.join(e) + ']]', range(len(e))),
    # The element is a SELECTION, so the fold descends through it and the
    # next step names a position in what that selection produces. Nesting by
    # wrapping literals cannot reach this shape, and it is the only container
    # the fold's recursion over the selected element exists for.
    'a subscript element': lambda e, at, op: (
        '(' + '[' + ', '.join(e) + '][' + str(at) + ']' + ',)',
        range(len(e))),
}

_BRANCHING = ('a conditional', 'a disjunction')

# The nested literal is the DEPTH axis's own spelling rather than a
# container kind of its own: it holds exactly one element, so the first
# subscript descends past it and the selection happens one level in. It is
# generated at depth two and up for that reason — at depth one it can only
# select a container, which says nothing about the operation.
_NESTED = 'a nested literal'

# The steps that name a position the fold READS: a constant integer, spelled
# from the positive, from the negative, or computed. Every other step needs
# a value the literal does not carry — a free name, a key, a slice, a
# position the container does not have.
_CONSTANT_STEPS = ('at the operation', 'elsewhere', 'negative', 'a binop')

# The containers whose elements have positions a subscript names. A `Dict`
# and a `Set` are keyed rather than positioned, and the fold declines both,
# so they are NOT here: a marker that called a dict literal pinned asserted
# a property the walk does not hold. `pinned` is this set crossed with
# `_CONSTANT_STEPS` and nothing else, and a form it names is settled by its
# literal alone — the oracle's class and the walk's fold agree, and the
# contract is the class.
_POSITIONAL = ('a list literal', 'a tuple literal', _NESTED,
               'a starred unpack', 'a subscript element')


def _steps(kind, at, width):
    """Every selection spelling the index and depth axes contribute, with
    the depth it belongs to.

    A BARE step — no subscript at all — is generated only for the two
    branching containers, whose value the oracle's own binding pins to the
    operation. Everywhere else the container's own value is a container,
    and this axis is about reading the operation OUT of one, so a bare
    literal is a shape it does not generate.
    """
    spellings = (
        ('at the operation', f'[{at}]'),
        # The far end that is not the operation, so "present but never
        # selected" is covered at more than one position and the
        # discriminating near-miss is not a single spelling.
        ('elsewhere', f'[{0 if at else width - 1}]'),
        ('out of range', f'[{width + 4}]'),
        ('negative', '[-1]'),
        ('a name', '[i]'),
        ('a string key', "['a']"),
        ('a slice', '[0:1]'),
        ('a binop', '[0 + 0]'),
    )
    for depth in (1, 2, 3):
        if kind == _NESTED and depth == 1:
            continue
        # The nested literal's own element has to be descended through before
        # a constant step can name a position in the list inside it.
        descent = '[0]' * (depth - 1) if kind == _NESTED else ''
        for name, spelling in spellings:
            prefix = descent if name in _CONSTANT_STEPS else ''
            yield depth, name, prefix + spelling * depth
        if kind in _BRANCHING and depth == 1:
            yield depth, 'bare', ''


def _nested(source, depth):
    """The container wrapped in one more literal per extra level, so every
    level of `depth` is a real selection rather than the first followed by
    a subscript of whatever it selected."""
    for _ in range(depth - 1):
        source = '[' + source + ']'
    return source


def _generated():
    """Every generated form, deduplicated, as a dict of the facts the oracle
    and the guard are both asked about.

    Both markers are read off the CONSTRUCTION — which element positions the
    builder placed, and whether the container is positional at all — so
    neither borrows the guard's own answer to the question it is checking,
    and neither is a spelling proxy for a value property. The alias axis is
    what proves the second half: a marker that searched the generated text
    for one spelling of the operation would report the aliased forms
    mention-free, and the sweep would demand silence the guard refuses.
    """
    seen = {}
    for binding, imports, operation in _BINDINGS:
        for kind, build in _CONTAINERS.items():
            for position, at, width in _POSITIONS:
                elements = ([_FILLER] * at + [operation]
                            + [_FILLER] * (width - at - 1))
                for depth, step, spelling in _steps(kind, at, width):
                    source, placed = build(elements, at, operation)
                    container = _nested(source, depth)
                    callee = container + spelling
                    if callee in seen:
                        continue
                    seen[callee] = {
                        'kind': kind, 'binding': binding,
                        'position': position, 'step': step,
                        'depth': depth, 'callee': callee,
                        'imports': imports, 'container': container,
                        'mentions': at in placed,
                        'pinned': kind in _POSITIONAL
                        and step in _CONSTANT_STEPS}
    return list(seen.values())


# The oracle: a child process against a real `importlib`, one form per line
# and each in its own globals, so one form's failure cannot decide
# another's class. The class is read off the VALUE by identity — reached, a
# known non-operation, or a value the oracle cannot supply.
_ORACLE = '''
import importlib
import json
import sys

for line in sys.stdin:
    assignment, source = json.loads(line)
    env = {"c": True, "i": 0, "importlib": importlib,
           "im": importlib.import_module}
    try:
        if assignment:
            exec(assignment, env)
        value = eval(source, env)
    except Exception:
        answer = "undetermined"
    else:
        answer = ("reaches" if value is importlib.import_module
                  else "does not reach")
    print(answer)
    sys.stdout.flush()
'''


def _assignment(form):
    return 'd = ' + form['container']


def _oracle(forms, stored):
    """Classify every form by running it, once per process wave."""
    payload = ''.join(
        json.dumps([_assignment(form) if stored else '', form['callee']])
        + '\n' for form in forms)
    child = subprocess.run([sys.executable, '-c', _ORACLE], input=payload,
                           capture_output=True, text=True, check=True)
    answered = child.stdout.split('\n')[:-1]
    assert len(answered) == len(forms), (
        f'the oracle answered {len(answered)} of {len(forms)} forms')
    return answered


def _verdict(root, form, stored):
    """What the scan does with one form: refused, resolved to the target, or
    silent. The target is a repo module, so a resolution shows up in the
    scan set rather than having to be told apart from a silence."""
    body = f'    {_assignment(form)}\n' if stored else ''
    body += f'    return {form["callee"]}("pkg.leaf")\n'
    (root / 'composition.py').write_text(
        f'\n{form["imports"]}\n\ndef load(c, i):\n' + body, encoding='utf-8')
    try:
        scanned = _mcp_import_closure.composition_scan_set(
            root / 'composition.py', root)
    except AssertionError:
        return 'refused'
    names = {path.relative_to(root).as_posix() for path in scanned}
    return 'resolved' if 'pkg/leaf.py' in names else 'silent'


def _sweep(root, forms):
    """Classify and scan every form both ways, recording what each said."""
    for form, reached in zip(forms, _oracle(forms, False)):
        form['oracle'] = reached
        form['inline'] = _verdict(root, form, False)
    for form, reached in zip(forms, _oracle(forms, True)):
        form['stored'] = reached
        form['store'] = _verdict(root, form, True)
    return forms


def _duty(form):
    """What the guard owes this form, and the class that decides it.

    A PINNED form — a positional container read by constant positions it
    declares — has a value both the oracle and the walk can settle, so its
    debt is the oracle's class: a form that reaches the operation is
    resolved to the target or refused, and a form that does not reach it is
    silent, because a false positive costs a real closure entry.

    Every other form's reach depends on something outside the literal — a
    free name, a position the walk declines, a container it cannot read — so
    it is the walk's UNDETERMINED class, and its debt is the mention
    property instead: refused when the expression mentions the operation,
    silent when it does not.
    """
    if not form['pinned']:
        return ('refused',) if form['mentions'] else ('silent',)
    return {'reaches': ('refused', 'resolved'),
            'does not reach': ('silent',),
            'undetermined': ('refused',) if form['mentions'] else ('silent',)
            }[form['oracle']]


def _tally(forms, key, classes):
    return {klass: sum(1 for form in forms if form[key] == klass)
            for klass in classes}


_CLASSES = ('reaches', 'does not reach', 'undetermined')


def _report(forms):
    print(f'\n  sweep: {len(forms)} distinct forms, inline '
          + str(_tally(forms, 'oracle', _CLASSES))
          + ', stored ' + str(_tally(forms, 'stored', _CLASSES))
          + ', pinned ' + str(_tally(forms, 'pinned', (True, False))))


def _unpaid(forms):
    """The forms whose verdict the guard did not owe, named."""
    owed = [f'    {form["callee"]} -> {form["inline"]} '
            f'(oracle {form["oracle"]}, pinned {form["pinned"]})'
            for form in forms if form['inline'] not in _duty(form)]
    return f'{len(owed)} unpaid of {len(forms)}:\n' + '\n'.join(owed)


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
    forms = _sweep(Path(_tmp), _generated())
    _report(forms)
    assert all(_tally(forms, 'oracle', _CLASSES).values())
    assert _unpaid(forms) == f'0 unpaid of {len(forms)}:\n', _unpaid(forms)


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
    forms = _sweep(Path(_tmp), _generated())
    _report(forms)
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
    forms = _sweep(Path(_tmp), _generated())
    _report(forms)
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
