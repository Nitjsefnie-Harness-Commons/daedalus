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

# The routes a lambda is reached through: the bare one, the guard meets a
# callee directly, and one selection, where the fold has read the lambda out
# of a value. The selection AXIS is the lambda class's to sweep; this one is
# about the call the lambda is given, and two routes is what says the fold
# reads the same lambda either way.
_SIGNATURE_ROUTES = ('({op})', '[({op})][0]')


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
            # Whether the signature REQUIRES anything, which is what makes
            # a call that supplies no name at all a raise rather than a
            # legal call to a lambda that wants nothing.
            'requires': (len(positional) - tail > 0
                         or any('=' not in part for part in declared)),
            'kwarg': bool(kwarg)})


def _bindings(signature):
    """Every way a call binds arguments to `signature`, as (the arguments,
    which side of the boundary the runtime puts it on).

    Generated from the signature's own structure rather than written beside
    it, so a signature added to the product brings its bindings with it and
    a cell nobody thought of is crossed rather than missed. The side is a
    PREDICTION and the oracle is the measurement: a form predicted to reach
    and labelled `raises` is caught by the suite's own contract, which
    requires every `bound raises` row to come back silent.
    """
    only, names, kwonly = (signature['only'], signature['names'],
                           signature['kwonly'])
    required = signature['required']
    by_name, defaulted = names[:required], names[required:]
    leading = ['0'] * len(only)
    positional = ', '.join(leading + ['0'] * len(names))
    named = [f'{name}=0' for name in list(by_name) + list(defaulted)
             + list(kwonly)]
    yields = ', '.join(leading + named)
    if positional or not kwonly:
        # The positional form supplies every parameter it may, and a
        # keyword-only parameter may only be named — so a signature with one
        # reaches by POSITION for its positional part and by NAME for the
        # rest, and omitting the name makes it a raise.
        yield ', '.join(leading + ['0'] * len(names)
                        + [f'{name}=0' for name in kwonly]), 'reaches'
    if named:
        yield yields, 'reaches'
        carried = '{%s}' % ', '.join(
            f"'{name}': 0" for name in list(by_name) + list(defaulted)
            + list(kwonly))
        yield ', '.join(leading + [f'**{carried}']), 'reaches'
    # The forms Python REFUSES, and a fix that reads names too eagerly must
    # not capture them.
    if by_name:
        yield ', '.join(leading + ['0'] * len(names)
                        + [f'{by_name[0]}=0']), 'raises'
    if required:
        yield ', '.join(leading + ['0'] * (required - 1)), 'raises'
    if only:
        yield f'{only[0]}=0', 'raises'
    if named and not signature['kwarg']:
        yield yields + ', z=0', 'raises'
    if signature['requires']:
        # A display that carries NO name: the walk reads the keys a display
        # displays, and one that displays none supplies none. It is the cell
        # a rule that reads a `**` as supplying nothing cannot be told from
        # the rule that reads it correctly on every other display, because
        # every other display this class generates carries a parameter.
        yield '**{}', 'raises'
    # One positional too many, and a `*args` is what makes it one: it soaks
    # up whatever comes after the parameters it follows, so a signature that
    # has one cannot be made to raise this way.
    if not signature['vararg']:
        yield ', '.join(([positional] if positional else []) + [_FILLER]), \
            'raises'


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
        for index, (arguments, side) in enumerate(_bindings(signature)):
            # The filler body on ONE form per signature: it is the class's
            # `does not reach` side and it is the same side for every
            # binding, so one per signature carries it.
            bodies = (operation, _FILLER) if index == 0 else (operation,)
            for body in bodies:
                head = f'lambda {text}: {body}'
                for route in _SIGNATURE_ROUTES:
                    yield _form(
                        f'a {text or "bare"} lambda', f'bound {side}',
                        route.format(op=head) + f'({arguments})', _FILLER,
                        False, False, (SIGNATURE_CLASS,), imports=imports,
                        # The binding is this class's own module source, and
                        # a filler-body row names the operation nowhere, so a
                        # row under one operation spelling and the same row
                        # under the other are two rows and not one.
                        setup=imports)
