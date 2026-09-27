"""The row record every generated class writes, and the class a SIGNATURE
decides.

`_mcp_selection_sweep` composes a grammar over containers and selection steps.
Its lambda class answers what a LAMBDA'S VALUE is, which its own position
decides; this one answers what a call of that lambda PRODUCES, which the
arguments decide — a question the container axis never asks, so a rule that
counted positionals and a rule that binds arguments both sat on the same side
of every generated row and the instrument agreed with either.

`_form` lives here because the two modules write the same record and the
sweep's was the one that was already a moving target for the size ceiling.
"""
from itertools import product


# The class this module's rows belong to, named as a property rather than as
# a spelling: a parameter is supplied by name as much as by position, and the
# rows span the forms that supply one and the forms whose runtime value is a
# raise.
SIGNATURE_CLASS = 'a parameter supplied by name'

# The one filler the whole product uses for "a value that is not the
# operation". It lives beside `_form` because both modules write rows, and it
# is here rather than imported so there is one source for the literal.
_FILLER = '0'


def _form(kind, step, callee, container, mentions, carries, classes,
          pinned=True, imports='', setup=''):
    """One generated form, in the shape `duty` and the marker read.

    `pinned` is True for every form the repeated-key and lambda builders
    produce, and the construction says why: a dict display settles its own
    entries and a lambda's return is settled by the arguments the caller
    supplies. The builtin builder claims it only for a row whose binding the
    module RUNS and leaves the name alone, since a replaced name produces no
    value for either side to agree on. `setup` is what the oracle runs
    before the form, and is empty for every builder with no binding of its
    own.
    """
    return {'kind': kind, 'step': step, 'depth': 1, 'callee': callee,
            'container': container, 'mentions': mentions, 'pinned': pinned,
            'carries': carries, 'classes': classes, 'imports': imports,
            'setup': setup}


# --- the signature axis, as a product of its own sub-axes -----------------
#
# A lambda's parameter list has five independent parts and the walk has to
# answer for every combination of them, so the axis IS the product rather
# than a list of signatures someone wrote down. The first commit on this
# class listed thirteen signatures and asserted the list's length, and the
# two defects the review found on it were both in cells the list did not
# cross: a keyword-only parameter with a DEFAULT, and a `*args` beside a
# required positional. A count over a hand list witnesses that the list has
# not changed; a product witnesses that the domain has been crossed.
_POSONLY = ((), ('p',))
_POSITIONAL = ((), ('x',), ('x', 'y'))
_DEFAULTED = (0, 1, 2)          # how many of the positional TAIL default
_STAR = ('', '*, k', '*, k=1', '*, k, j', '*, k=1, j=2', '*a')
# The catch-all's own name is one no other slot uses, because a `**w`
# beside a `k` is a `SyntaxError` and the product would compose one.
_KWARG = ('', '**w')

# The routes a lambda is reached through: the bare one the guard meets a
# callee directly, and every route a fold selects one in. The route does not
# change the binding, and a round cut it to two and bought nothing a
# measurement could see, so every route is back.
_SIGNATURE_ROUTES = (
    '({op})',
    '[({op})][0]',
    '({{"a": {op}}})["a"]',
    '(*[{op}],)[0]',
    '[[({op})]][0][0]',
)

# The binding shapes this class CROSSES, as (the shape, which side of the
# boundary the runtime puts it on). This table is the generator's statement
# of its own coverage and the suite checks it in both directions: every shape
# here has rows, and every row's step is a shape here. A shape the walk must
# answer and this generator cannot emit would be invisible — a rule that gets
# it wrong would pass every row, and the ORACLE could not see it, because the
# oracle reads the rows and the missing one is not a row.
#
# The shapes are the ways a call's argument list relates to a signature, and
# the two halves are the walk's own: every parameter supplied, and every way
# Python refuses one. The first three are the supply — one supply by
# position, by name, and through a display — and the rest are the refusals.
BINDING_SHAPES = (
    ('every parameter by position', 'reaches'),
    ('every nameable parameter by name', 'reaches'),
    ('every nameable parameter through a display', 'reaches'),
    ('a required parameter left out', 'raises'),
    ('a display carrying no name', 'raises'),
    ('one positional too many', 'raises'),
    ('a positional parameter twice', 'raises'),
    ('a positional-only parameter by name', 'raises'),
    ('a keyword-only parameter twice', 'raises'),
    ('a keyword-only parameter twice, by name and display', 'raises'),
    ('a name no parameter declares', 'raises'),
)

# The shapes this class CANNOT cross, and why each is unreachable here. A
# shape that needs a value the oracle cannot supply is not a row, so it is
# named and held by a hand case instead — which is the honest way to be
# incomplete, and the one thing a generator cannot check about itself.
UNDECIDED_SHAPES = (
    ('a `*args` unpacking a runtime value',
     'the walk does not read the unpacked length, and a row would take its '
     'class from the walk rather than from a run'),
    ('a `**` mapping the walk cannot read', 'the same: its keys are a '
     'runtime value, and `d` empty raising and `d` holding the key reaching '
     'are one row and two verdicts'),
    ('a `**` display unpacking another display',
     'the keys are a runtime value by the same route'),
    ('a `**` display whose keys are not all names',
     'the walk cannot read them and Python refuses them outright'),
)


def shape_side(shape):
    """Which side of the boundary a declared shape is on."""
    return dict(BINDING_SHAPES)[shape]


def _signatures():
    """Every signature the product declares, as (the text, its structure).

    The structure is what the binding forms are generated FROM, so a form is
    never spelled for a signature it does not belong to.
    """
    seen = set()
    for only, positional, defaulted, star, kwarg in product(
            _POSONLY, _POSITIONAL, _DEFAULTED, _STAR, _KWARG):
        tail = min(defaulted, len(positional))
        named = [f'{name}=0' if index >= len(positional) - tail else name
                 for index, name in enumerate(positional)]
        # The `/` belongs after the positional-only parameters and before the
        # rest, whatever the rest turns out to be.
        parts = list(only) + (['/'] if only else []) + named
        if star:
            parts.append(star)
        if kwarg:
            parts.append(kwarg)
        text = ', '.join(parts)
        if text in seen:
            # A sub-axis with nothing to vary — a default count over an empty
            # positional list, say — composes the same signature twice, and
            # the domain is the signatures a lambda can DECLARE.
            continue
        declared = [part for part in star.split(', ')[1:] if part]
        seen.add(text)
        yield (text, {
            'only': only,
            'names': positional,
            'required': len(positional) - tail,
            'vararg': star == '*a',
            'kwonly': tuple(part.split('=')[0] for part in declared),
            'kwonly_required': tuple(part.split('=')[0] for part in declared
                                     if '=' not in part),
            # Whether the signature REQUIRES anything, which is what makes
            # a call that supplies no name at all a raise rather than a
            # legal call to a lambda that wants nothing. Counted over the
            # parameters as DECLARED rather than as a predicate over the
            # slots, and a positional-only parameter is in that count: it
            # cannot carry a default, so it is always one of them.
            'requires': (len(only) + len(positional) - tail
                         + sum(1 for part in declared
                               if '=' not in part)) > 0,
            'kwarg': bool(kwarg)})


def _bindings(signature):
    """Every binding shape this signature admits, as (the shape, the
    arguments), generated from the signature's own structure.

    The shape NAMES the row rather than the side, and that is the whole
    point: a side names which end of the boundary a row is on, so two
    refusals are one word and a rule that gets one of them wrong is
    indistinguishable from a rule that gets both right. A shape per refusal
    is what lets `BINDING_SHAPES` be checked instead of trusted, and
    `step_side` says which side a shape is on.

    A signature emits a shape only where it can produce it — a
    `*args` absorbs the positional that would be one too many, and a `**w`
    catches the name no parameter declares — and the suite checks that too:
    a signature with no boundary is NAMED rather than left to be a smaller
    number.
    """
    only, names, kwonly = (signature['only'], signature['names'],
                           signature['kwonly'])
    required = signature['required']
    by_name, defaulted = names[:required], names[required:]
    nameable = list(by_name) + list(defaulted) + list(kwonly)
    leading = ['0'] * len(only)
    positional = ', '.join(leading + ['0'] * len(names))
    if positional or not kwonly:
        # The positional form supplies every parameter it may, and a
        # keyword-only parameter may only be named — so a signature with one
        # reaches by POSITION for its positional part and by NAME for the
        # rest, and omitting the name makes it a raise.
        yield ('every parameter by position', ', '.join(
            leading + ['0'] * len(names) + [f'{name}=0' for name in kwonly]))
    if nameable:
        yield ('every nameable parameter by name',
               ', '.join(leading + [f'{name}=0' for name in nameable]))
        carried = '{%s}' % ', '.join(f"'{name}': 0" for name in nameable)
        yield ('every nameable parameter through a display',
               ', '.join(leading + [f'**{carried}']))
    if signature['requires']:
        # One required parameter left out: the call supplies every required
        # one but the LAST, which is the spelling of the raise. The order is
        # the parameter list's own — positional-only, then positional, then
        # keyword-only — so the parameter left out is the one the walk
        # reaches last, whichever kind it is.
        supplies = (leading + ['0'] * required
                    + [f'{name}=0' for name in signature['kwonly_required']])
        yield ('a required parameter left out', ', '.join(supplies[:-1]))
        # A display that carries NO name: the walk reads the keys a display
        # displays, and one that displays none supplies none.
        yield ('a display carrying no name', '**{}')
    if not signature['vararg']:
        # One positional too many, and a `*args` is what makes it one: it
        # soaks up whatever comes after the parameters it follows.
        yield ('one positional too many',
               ', '.join(([positional] if positional else []) + [_FILLER]))
    if by_name:
        yield ('a positional parameter twice', ', '.join(
            leading + ['0'] * len(names) + [f'{by_name[0]}=0']))
    if only:
        yield ('a positional-only parameter by name', f'{only[0]}=0')
    if kwonly:
        first = kwonly[0]
        yield ('a keyword-only parameter twice', f'{first}=0, {first}=0')
        # The same rule by the two spellings the walk reads separately: a
        # keyword says its own name and a display says its keys, and the
        # parameter is supplied twice either way.
        yield ('a keyword-only parameter twice, by name and display',
               f'{first}=0, **{{"{first}": 0}}')
    if nameable and not signature['kwarg']:
        yield ('a name no parameter declares', ', '.join(
            leading + [f'{name}=0' for name in nameable]) + ', z=0')


def step_side(shape):
    """Which side of the boundary a row's shape is on."""
    return shape_side(shape)


def signatures(imports, operation):
    """A lambda of every signature the product declares, called every way a
    call binds arguments, over both a body that reaches and a filler.

    The property is the BINDING and not the keyword: position and name are
    one supply, so a row is generated for each of them and for the forms
    Python REFUSES, which are on the other side of the same boundary. Both
    halves come from one construction, so a rule that reads only the
    positional arguments and a rule that reads only the names are each caught
    by rows the other passes.

    The body is the operation on half the rows and a filler on the other:
    the filler's value is a position the operation is not in, which is the
    class's own `does not reach` side, and it is what stops a rule that
    resolves the callee as the operation without binding the call at all.
    """
    for text, signature in _signatures():
        for index, (shape, arguments) in enumerate(_bindings(signature)):
            # The filler body on ONE form per signature: it is the class's
            # `does not reach` side and it is the same side for every
            # binding, so one per signature carries it.
            bodies = (operation, _FILLER) if index == 0 else (operation,)
            for body in bodies:
                head = f'lambda {text}: {body}'
                for route in _SIGNATURE_ROUTES:
                    yield _form(
                        f'a {text or "bare"} lambda', shape,
                        route.format(op=head) + f'({arguments})', _FILLER,
                        False, False, (SIGNATURE_CLASS,), imports=imports,
                        # The binding is this class's own module source, and
                        # a filler-body row names the operation nowhere, so a
                        # row under one operation spelling and the same row
                        # under the other are two rows and not one.
                        setup=imports)
