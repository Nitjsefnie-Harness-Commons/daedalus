"""The GENERATED product of the callee grammar, and the runtime oracle.

The hand cases in `test_mcp_import_selection.py` are a sample of the
spellings someone thought of; this is the product of a grammar over its axes,
classified by RUNNING each form against a real `importlib`. A hand-typed
case proves the spelling it names and nothing else, and a marker that cannot
express a class cannot sweep it: a member of the grammar is the only way a
class the hand cases miss becomes covered here.

Both duty markers are read off the CONSTRUCTION — which element positions
the builder placed, and whether the container is positional at all — so
neither borrows the guard's own answer to the question it is checking, and
neither is a spelling proxy for a value property.
"""
import json
import subprocess
import sys

import _mcp_import_closure


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
# from the positive, from the negative, or computed, and the two spellings
# that are the same integer by Python's own arithmetic rather than by a
# second operator — a unary plus and a bool. Every other step needs a value
# the literal does not carry — a free name, a key, a slice, a position the
# container does not have.
_CONSTANT_STEPS = ('at the operation', 'elsewhere', 'negative', 'a binop',
                   'a unary plus', 'a bool')

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
        # The same position by two spellings that are arithmetic rather than
        # a second operator: `+1` is `1`, and `True` is `1`. Both name
        # position ONE outright, so every container also gets the spelling
        # that selects something other than the operation — a step pinned on
        # the operation's own position could not fail on a fold that stopped
        # reading it, because both halves would move together.
        ('a unary plus', '[+1]'),
        ('a bool', '[True]'),
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


def generated():
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


def sweep(root, forms):
    """Classify and scan every form both ways, recording what each said."""
    for form, reached in zip(forms, _oracle(forms, False)):
        form['oracle'] = reached
        form['inline'] = _verdict(root, form, False)
    for form, reached in zip(forms, _oracle(forms, True)):
        form['stored'] = reached
        form['store'] = _verdict(root, form, True)
    return forms


def duty(form):
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


def tally(forms, key, classes):
    return {klass: sum(1 for form in forms if form[key] == klass)
            for klass in classes}


CLASSES = ('reaches', 'does not reach', 'undetermined')


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
