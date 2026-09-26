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
    '[importlib.import_module][-1]',
    '[importlib.import_module][0:1]',
    '(*[importlib.import_module],)[0]',
    "{'a': importlib.import_module}['a']",
    '[(0, importlib.import_module)][0][1]',
)

# The subset the fold cannot read exactly. Each can BE the operation at
# runtime and no static reading of it names which, so there is no target to
# resolve and the scan must refuse rather than guess one: the fold decides
# the VALUE, never the POSITION it declines to fold.
UNREADABLE_SELECTIONS = (
    '(importlib.import_module if c else print)',
    '[importlib.import_module for _ in [0]][0]',
    '[importlib.import_module][i]',
    '[importlib.import_module][-1]',
    '[importlib.import_module][0:1]',
    '(*[importlib.import_module],)[0]',
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
# grammar over five axes, checked against a runtime oracle, so a spelling
# nobody thought of is covered too.

_OPERATION = 'importlib.import_module'
_FILLER = '0'

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

# A container and how it holds elements. The first four are POSITIONAL:
# their elements have positions a subscript can name. The conditional and
# the disjunction take the operation only, because a branch is chosen by a
# runtime value rather than by a position — the position axis does not
# apply to them, and pairing it with them generates the same form repeatedly
# rather than a new one.
_CONTAINERS = {
    'a list literal': lambda e: '[' + ', '.join(e) + ']',
    'a tuple literal': lambda e: '(' + ', '.join(e) + ',)',
    'a dict literal': lambda e: '{' + ', '.join(
        chr(97 + n) + ': ' + item for n, item in enumerate(e)) + '}',
    'a set literal': lambda e: '{' + ', '.join(e) + '}',
    'a list comprehension': lambda e: '[' + e[0] + ' for _ in [0]]',
    'a generator expression': lambda e: '(' + e[0] + ' for _ in [0])',
    'a conditional': lambda e: '(' + _OPERATION + ' if c else print)',
    'a disjunction': lambda e: '(' + _OPERATION + ' or print)',
    'a starred unpack': lambda e: '(*[' + e[0] + '],)',
    'a nested literal': lambda e: '[[' + ', '.join(e) + ']]',
}

_BRANCHING = ('a conditional', 'a disjunction')

# The nested literal is the DEPTH axis's own spelling rather than a
# container kind of its own: it holds exactly one element, so the first
# subscript descends past it and the selection happens one level in. It is
# generated at depth two and up for that reason — at depth one it can only
# select a container, which says nothing about the operation.
_NESTED = 'a nested literal'

# The containers whose elements have positions a constant subscript names,
# and the steps that name one: a form built only from these is settled by
# its literal alone, so the oracle's class and the walk's fold agree about
# it and the contract is the class. Everything else needs a value the
# literal does not carry — a free name, a position the fold declines, a
# container with no positions at all.
_POSITIONAL = ('a list literal', 'a tuple literal', 'a dict literal',
               _NESTED)
_SETTLING = ('at the operation', 'elsewhere')


def _steps(kind, at, width, depth):
    """Every selection spelling the index and depth axes contribute.

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
    # The nested literal's own element has to be descended through before a
    # settling step can name a position in the list inside it.
    descent = '[0]' * (depth - 1) if kind == _NESTED else ''
    for name, spelling in spellings:
        yield name, (descent if name in _SETTLING else '') + spelling * depth
    if kind in _BRANCHING and depth == 1:
        yield 'bare', ''


def _nested(source, depth):
    """The container wrapped in one more literal per extra level, so every
    level of `depth` is a real selection rather than the first followed by
    a subscript of whatever it selected."""
    for _ in range(depth - 1):
        source = '[' + source + ']'
    return source


def _generated():
    """Every generated form, deduplicated, as a dict of the facts the oracle
    and the guard are both asked about."""
    seen = {}
    for kind, build in _CONTAINERS.items():
        for position, at, width in _POSITIONS:
            elements = ([_FILLER] * at + [_OPERATION]
                        + [_FILLER] * (width - at - 1))
            for depth in (1, 2, 3):
                if kind == _NESTED and depth == 1:
                    continue
                container = _nested(build(elements), depth)
                for step, spelling in _steps(kind, at, width, depth):
                    callee = container + spelling
                    if callee in seen:
                        continue
                    seen[callee] = {
                        'kind': kind, 'position': position, 'step': step,
                        'depth': depth, 'callee': callee,
                        'container': container,
                        # Read off the generated text rather than off the
                        # property the guard uses to decide, so the oracle
                        # side of the sweep does not borrow the guard's
                        # own answer to the question it is checking.
                        'mentions': _OPERATION in container,
                        # Whether the value is settled by the literal
                        # ALONE: a positional container read by constant
                        # positions it declares. Anything else needs
                        # something the literal does not carry.
                        'pinned': kind in _POSITIONAL
                        and step in _SETTLING}
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
    env = {"c": True, "i": 0, "importlib": importlib}
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
        '\nimport importlib\n\n\ndef load(c, i):\n' + body, encoding='utf-8')
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
    """A refusal the rule does not owe, counted rather than tolerated.

    The walk's fold declines a negative index, a slice and a computed one,
    so a container that mentions the operation and is read by one of those
    is refused whatever it selects. That is the rule working as written and
    it is deliberately more conservative than the value deserves; the
    oracle disagrees on exactly those forms, and this pins the number so a
    wider refusal is visible as a change rather than absorbed.
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
    print(f'  {len(bought)} conservative refusals of a pinned-free form, '
          f'all of them a declined position on a container that mentions '
          f'the operation: {sorted({form["step"] for form in bought})}')


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
