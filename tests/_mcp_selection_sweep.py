"""The GENERATED product of the callee grammar, and the runtime oracle.

The hand cases in `test_mcp_import_selection.py` are a sample of the
spellings someone thought of; this is the product of a grammar over its axes,
classified by RUNNING each form against a real `importlib`. A hand-typed
case proves the spelling it names and nothing else, and a marker that cannot
express a class cannot sweep it: a member of the grammar is the only way a
class the hand cases miss becomes covered here.

Every duty marker is read off the CONSTRUCTION — which element positions
the builder placed, whether the container is one a subscript reaches, and
whether a projected one can be called at all — so none borrows the guard's
own answer to the question it is checking, and none is a spelling proxy for
a value property. The universe is the PROPERTY and not the builders that
happen to exist: a class the grammar cannot name has no row, and a class its
builders spell one way has one row a fold can get wrong without it showing.
"""
import json
import subprocess
import sys

import _mcp_import_closure


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
    # A dict is a MAPPING, so its keys are the operation's own spellings
    # and the string-key step reads one of them. The bare names this used to
    # emit are not a dict at all, and a third of the product measured
    # nothing.
    'a dict literal': lambda e, at, op: (
        '{' + ', '.join(repr(chr(97 + n)) + ': ' + item
                        for n, item in enumerate(e)) + '}', range(len(e))),
    'a set literal': lambda e, at, op: (
        '{' + ', '.join(e) + '}', range(len(e))),
    'a list comprehension': lambda e, at, op: (
        '[' + e[0] + ' for _ in [0]]', (0,)),
    'a set comprehension': lambda e, at, op: (
        '{' + e[0] + ' for _ in [0]}', (0,)),
    'a dict comprehension': lambda e, at, op: (
        '{k: ' + e[0] + ' for k in [0]}', (0,)),
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
    # next step names a position in what that produces. Wrapping literals
    # cannot reach this shape, and it is the only container the fold's
    # recursion over the selected element exists for.
    'a subscript element': lambda e, at, op: (
        '([' + ', '.join(e) + '][' + str(at) + '])',
        range(len(e))),
}

_BRANCHING = ('a conditional', 'a disjunction')

# The nested literal is the DEPTH axis's own spelling rather than a
# container kind of its own: it holds exactly one element, so the first
# subscript descends past it and the selection happens one level in. It is
# generated at depth two and up for that reason — at depth one it can only
# select a container, which says nothing about the operation.
_NESTED = 'a nested literal'

# How the container is WRAPPED. `__call__` is how Python spells "this object
# is callable", so a projection is a route to the operation the walk reads as
# a VALUE — one the sibling code-eval axis closes and this product could not
# express at all.
_PROJECTIONS = ('', '.__call__')

# The steps of the index axis, each with whether the FOLD settles the value
# it names. A settled step is one the runtime settles too, so the form's
# debt is the ORACLE's class; an unsettled step needs a value the literal
# does not carry, so its debt is the mention property instead.
_STEPS = (
    # (name, spelling, settled, the position it names). The position is what
    # says whether a step SELECTS the element a one-element outer list holds
    # or raises short of it, which is the only thing the depth axis needs to
    # know about the element a builder produced. A name in the last field is
    # one of the `_steps` fields it takes its value from; a number is the
    # position outright, and None is a step that names none.
    ('at the operation', '[{at}]', True, 'at'),
    # The far end that is not the operation, so "present but never
    # selected" is covered at more than one position and the
    # discriminating near-miss is not a single spelling.
    ('elsewhere', '[{far}]', True, 'far'),
    ('out of range', '[{beyond}]', True, 'beyond'),
    ('negative', '[-1]', True, -1),
    # Both arithmetic spellings of the operation's own position. `+` and
    # `-` are generated as a pair so a fold that settled one and declined
    # the other leaves a row that reads the value beside a row that does
    # not, rather than one operator with nothing to fail it.
    ('a sum', '[{at} + 0]', True, 'at'),
    ('a difference', '[{at} - 0]', True, 'at'),
    # The same position by two spellings that are arithmetic rather than a
    # second operator: `+1` is `1`, and `True` is `1`. Both name position
    # ONE outright, so every container also gets the spelling that selects
    # something else — a step pinned on the operation's own position could
    # not fail on a fold that stopped reading it, because both halves would
    # move together.
    ('a unary plus', '[+1]', True, 1),
    ('a bool', '[True]', True, 1),
    # A key the container is not indexed by: a sequence raises TypeError
    # and a mapping raises KeyError, so it is settled too — it names
    # nothing. On a dict literal the operation's own key IS `'a'` when it
    # sits at position zero, which is the discriminating near-miss in the
    # mapping's own spelling.
    ('a string key', "['a']", True, None),
    # A free name and a slice need a value the literal does not carry, so
    # neither is a position this walk can settle.
    ('a name', '[i]', False, None),
    ('a slice', '[0:1]', False, None),
)

_SETTLED = frozenset(name for name, _, settled, _ in _STEPS if settled)

# The containers whose SUBSCRIPT outcome the fold decides whatever step
# names one: a sequence and a mapping are read by a position or by a key, and
# a set and a generator are a `TypeError` between them. A list or a dict
# comprehension is not here — its length and its keys are runtime values —
# and neither is a branch, whose value one runtime value chooses. `pinned`
# is the answer these two sets give, and a form it names is settled by its
# literal alone: the oracle's class and the walk's fold agree, and the
# contract is the class.
_INDEXED = ('a list literal', 'a tuple literal', 'a dict literal', _NESTED,
            'a starred unpack', 'a subscript element')
_UNINDEXED = ('a set literal', 'a set comprehension', 'a generator expression')
_DECIDED = _INDEXED + _UNINDEXED

# The one builder whose own value is not a container at all but the
# FUNCTION: it reads the operation out of a literal, so what it produces is
# the operation itself. A projection of a function reaches it and a
# subscript of one is a `TypeError`, which is the one place the two
# wrappers part company.
_FUNCTION = 'a subscript element'


def _filled(template, at, far, beyond):
    """One step's spelling with the three position fields filled in.

    Substituted rather than `str.format`-ed because `**fields` reads to the
    launch auditor as a call that could be hiding a timeout, and this is a
    list of step templates rather than a subprocess.
    """
    return (template.replace('{at}', str(at))
            .replace('{far}', str(far)).replace('{beyond}', str(beyond)))


def _steps(kind, at, width):
    """Every selection spelling the index, wrapper and depth axes
    contribute, with the depth it belongs to, whether the fold settles the
    value each names, and the position that step names.

    A BARE step — no subscript at all — is generated only for the two
    branching containers, whose value is the operation itself. Everywhere
    else the container's own value is a container, and this axis is about
    reading the operation OUT of one.
    """
    far, beyond = 0 if at else width - 1, width + 4
    fields = {'at': at, 'far': far, 'beyond': beyond}
    spellings = [(name, _filled(spelling, at, far, beyond), settled,
                  fields[declared] if isinstance(declared, str)
                  else declared)
                 for name, spelling, settled, declared in _STEPS]
    for depth in (1, 2, 3):
        if kind == _NESTED and depth == 1:
            continue
        # The nested literal's own element has to be descended through before
        # a settled step can name a position in the list inside it.
        descent = '[0]' * (depth - 1) if kind == _NESTED else ''
        for name, spelling, settled, position in spellings:
            prefix = descent if name in _SETTLED else ''
            yield depth, name, prefix + spelling * depth, settled, position
        if kind in _BRANCHING and depth == 1:
            yield depth, 'bare', '', False, None


def _nested(source, depth):
    """One more literal per extra level, so every level of `depth` is a
    real selection rather than the first followed by a subscript of what it
    selected."""
    for _ in range(depth - 1):
        source = '[' + source + ']'
    return source


def _pinned(kind, depth, settled, named):
    """What the CONSTRUCTION says the fold settles about this form.

    A function and a set are settled whatever the step names; a container
    the fold reads is settled by a settled step, and past the first level so
    is a chain whose element it reads; and the builders it cannot read settle
    at the outer list only when the step raises short of the element — a
    position the one-element list does not have, or a key a sequence is not
    indexed by.
    """
    if kind == _FUNCTION and depth == 1 or kind in _UNINDEXED and depth == 1:
        return True
    if kind in _DECIDED:
        return settled and (depth > 1 or kind in _INDEXED)
    return (settled and depth > 1
            and (named is None or named not in (0, -1)))


def _forms(kind, build, at, width, operation):
    """Every form one builder at one position contributes, over the depth,
    step and wrapper axes.

    A branching builder's own value is the operation, so a projection of it
    reaches; every other builder's is a container, and a container has no
    `__call__` to project. Past the first level every builder's value is the
    list `_nested` wrapped it in, so the depth axis makes it a one-element
    list whatever the builder was. `mentions` is what the STORE side is held
    to — it reads the container, not whether the store can complete — so it
    stays the construction's own answer; `carries` is the call side's, and
    the two part company on exactly the projected container and the projected
    function.
    """
    elements = [_FILLER] * at + [operation] + [_FILLER] * (width - at - 1)
    for depth, step, spelling, settled, named in _steps(kind, at, width):
        source, placed = build(elements, at, operation)
        pinned = _pinned(kind, depth, settled, named)
        # A branching builder's value IS the operation and a projection of it
        # reaches; every other builder's is a container at the first level,
        # which has no `__call__` to project, and the one-element list past
        # it. A builder that reads the operation out of a literal produces
        # the FUNCTION, which a projection does reach.
        projectable = kind in _BRANCHING and depth == 1
        for projection in _PROJECTIONS:
            container = _nested(source, depth) + projection
            yield {
                'kind': kind, 'step': step, 'depth': depth,
                'callee': container + spelling, 'container': container,
                'imports': None, 'mentions': at in placed, 'pinned': pinned,
                'carries': at in placed and not (
                    (projection and not projectable)
                    or (kind == _FUNCTION and depth == 1 and not projection))}


def generated():
    """Every generated form, deduplicated, as a dict of the facts the oracle
    and the guard are both asked about.

    Every marker is read off the CONSTRUCTION — which element positions the
    builder placed, whether the container is one a subscript reaches, and
    whether a projected one can be called at all — so none borrows the
    guard's own answer to the question it is checking, and none is a
    spelling proxy for a value property. The alias axis is what proves the
    last half: a marker that searched the generated text for one spelling of
    the operation would report the aliased forms mention-free, and the sweep
    would demand silence the guard refuses.
    """
    seen = {}
    for binding, imports, operation in _BINDINGS:
        for kind, build in _CONTAINERS.items():
            for position, at, width in _POSITIONS:
                for form in _forms(kind, build, at, width, operation):
                    if form['callee'] in seen:
                        continue
                    seen[form['callee']] = dict(
                        form, binding=binding, position=position,
                        imports=imports)
    return list(seen.values())


# The oracle: a child process against a real `importlib`, one form per line
# and each in its own globals, so one form's failure cannot decide
# another's class. The class is read off the VALUE — reached, a known
# non-operation, or a value the expression cannot produce at all, which is
# its own class because no call reaches through it. `reaches` counts a
# `__call__` PROJECTION as well as the operation itself: the projection is
# a different object that runs the same code, and an identity test called it
# a non-operation and so called a refusing guard's refusal unnecessary.
_ORACLE = '''
import importlib
import json
import sys


def runs_the_operation(value):
    return value is importlib.import_module or getattr(
        value, "__self__", None) is importlib.import_module


for line in sys.stdin:
    assignment, source = json.loads(line)
    env = {"c": True, "i": 0, "importlib": importlib,
           "im": importlib.import_module}
    try:
        if assignment:
            exec(assignment, env)
        value = eval(source, env)
    except Exception:
        answer = "raises"
    else:
        answer = "reaches" if runs_the_operation(value) else "does not reach"
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


# The swept product, per process: three of the suite's cases ask the same
# question of the same deterministic forms, and scanning every one of them
# once per case triples a cost no assertion is buying.
_SWEEPED = {}


def sweep(root, forms):
    """Classify and scan every form both ways, recording what each said."""
    key = tuple(form['callee'] for form in forms)
    if key not in _SWEEPED:
        for form, reached in zip(forms, _oracle(forms, False)):
            form['oracle'] = reached
            form['inline'] = _verdict(root, form, False)
        for form, reached in zip(forms, _oracle(forms, True)):
            form['stored'] = reached
            form['store'] = _verdict(root, form, True)
        _SWEEPED[key] = forms
    return _SWEEPED[key]


def duty(form):
    """What the guard owes this form, and the class that decides it.

    A PINNED form (`_pinned`) has a value both the oracle and the walk can
    settle, so its debt is the oracle's class: one that reaches the
    operation is resolved or refused, one that does not reach it is silent,
    and one that RAISES is silent too, because the call raises on the
    expression itself and a refusal there is a false one. A false positive
    costs a real closure entry, which is why the does-not-reach class is
    silence rather than a resolved target.

    Every other form's reach depends on something outside the literal, so it
    is the walk's UNDETERMINED class and its debt is the mention property:
    refused when the expression carries the operation, silent when it does
    not. `carries` and not `mentions` is the projected container, which
    mentions the operation in its text and produces nothing to call.
    """
    if not form['pinned']:
        return ('refused',) if form['carries'] else ('silent',)
    return {'reaches': ('refused', 'resolved'),
            'does not reach': ('silent',),
            'raises': ('silent',)}[form['oracle']]


def tally(forms, key, classes):
    return {klass: sum(1 for form in forms if form[key] == klass)
            for klass in classes}


CLASSES = ('reaches', 'does not reach', 'raises')


def report(forms):
    print(f'\n  sweep: {len(forms)} distinct forms, inline '
          + str(tally(forms, 'oracle', CLASSES))
          + ', stored ' + str(tally(forms, 'stored', CLASSES))
          + ', pinned ' + str(tally(forms, 'pinned', (True, False))))


def unpaid(forms):
    """The forms whose verdict the guard did not owe, named."""
    owed = [f'    {form["callee"]} -> {form["inline"]} '
            f'(oracle {form["oracle"]}, pinned {form["pinned"]})'
            for form in forms if form['inline'] not in duty(form)]
    return f'{len(owed)} unpaid of {len(forms)}:\n' + '\n'.join(owed)
