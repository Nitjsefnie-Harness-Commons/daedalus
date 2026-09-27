"""The row record every generated class writes, and the class a SIGNATURE
decides.

`_mcp_selection_sweep` composes a grammar over containers and selection steps.
Its lambda class answers what a LAMBDA'S VALUE is, which its own position
decides; this one answers what a call of that lambda PRODUCES, which the
arguments decide — a question the container axis never asks, so a rule that
counted positionals and a rule that binds arguments both sat on the same side
of every generated row and the instrument agreed with either.

`_form` lives here because the two modules write the same record and the
sweep's is the one that was already a moving target for the size ceiling.
"""


# The class this module's rows belong to, named as a property rather than as
# a spelling: a parameter is supplied by name as much as by position, and the
# rows span the forms that supply one and the forms whose runtime value is a
# raise.
SIGNATURE_CLASS = 'a parameter supplied by name'

# The routes a lambda is reached through, which is every route a fold
# selects one in plus the bare one the guard meets directly. `{op}` is the
# callee: the operation itself for the rows that must resolve, and a filler
# for the rows whose value is a position the operation is not in.
_SIGNATURE_ROUTES = (
    '({op})',  # the bare one, the way the guard meets a callee directly
    '[({op})][0]',
    '({{"a": {op}}})["a"]',
    '(*[{op}],)[0]',
    '[[({op})]][0][0]',
)

# Every way a call meets a signature, as (the signature, what the call
# passes, which side of the boundary the runtime puts it on). The domain is
# the signatures a lambda can declare and the bindings a call can carry, not
# a sample of them, so a rule that gets one binding form right and another
# wrong fails a row rather than passing the class.
#
# `reaches` rows are the ones the issue names and the ones the issue did
# not: a parameter supplied by keyword, by a `**` display the fold reads, and
# a mixture. `raises` rows are what the same change must NOT capture — one
# parameter supplied twice, a name the signature has no parameter for, a
# name for a POSITIONAL-ONLY parameter, and a required one left out.
_SIGNATURE_CASES = (
    ('x', '1', 'reaches'),
    ('x', 'x=1', 'reaches'),
    ('x', "**{'x': 1}", 'reaches'),
    ('x', '1, x=2', 'raises'),
    ('x', '1, y=2', 'raises'),
    ('x', '1, 2', 'raises'),
    ('x', '', 'raises'),
    ('x, y', '1, 2', 'reaches'),
    ('x, y', '1, y=2', 'reaches'),
    ('x, y', 'x=1, y=2', 'reaches'),
    ('x, y', 'y=2, x=1', 'reaches'),
    ('x, y', "**{'x': 1, 'y': 2}", 'reaches'),
    ('x, y', '1', 'raises'),
    ('x, y', '1, 2, 3', 'raises'),
    ('x, y', '1, 2, x=3', 'raises'),
    ('x, y', "**{'x': 1, 'z': 2}", 'raises'),
    ('x, y', 'x=1, z=2', 'raises'),
    ('x, y=1', '1', 'reaches'),
    ('x, y=1', '1, 2', 'reaches'),
    ('x, y=1', 'y=1', 'raises'),
    ('x, y=1', '', 'raises'),
    ('x=1', '', 'reaches'),
    ('x=1', '1, 2', 'raises'),
    ('x, /', '1', 'reaches'),
    ('x, /', 'x=1', 'raises'),
    ('x, /, y', '1, 2', 'reaches'),
    ('x, /, y', 'y=2', 'raises'),
    ('*, k', 'k=1', 'reaches'),
    ('*, k', '', 'raises'),
    ('*, k', '1', 'raises'),
    ('*, k=1', '', 'reaches'),
    ('*, k=1', '1', 'raises'),
    ('x, y=1, *, k', '1, k=2', 'reaches'),
    ('x, y=1, *, k', 'k=2', 'raises'),
    ('*a', '1, 2', 'reaches'),
    ('*a', '', 'reaches'),
    ('*a', '1, z=2', 'raises'),
    ('*a, k', 'k=1', 'reaches'),
    ('*a, k', '', 'raises'),
    ('**k', 'z=1', 'reaches'),
    ('**k', '', 'reaches'),
    ('**k', '1', 'raises'),
    ('', '', 'reaches'),
    ('', '1', 'raises'),
)


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


def signatures(imports, operation):
    """A lambda of every signature, called every way a call binds arguments.

    The property is the BINDING and not the keyword: position and name are
    one supply, so a row is generated for each of them and for the two forms
    Python REFUSES, which are on the other side of the same boundary. Both
    halves are generated from one construction, so a rule that reads only
    the positional arguments and a rule that reads only the names are each
    caught by rows the other passes.

    The body is the operation on half the rows and a filler on the other:
    the filler's value is a position the operation is not in, which is the
    class's own `does not reach` side, and it is what stops a rule that
    resolves the callee as the operation from passing the whole table.
    """
    for route in _SIGNATURE_ROUTES:
        for callee_body in (operation, '0'):
            for signature, arguments, side in _SIGNATURE_CASES:
                head = f'lambda {signature}: {callee_body}'
                yield _form(
                    f'a {signature or "bare"} lambda', f'bound {side}',
                    route.format(op=head) + f'({arguments})', '0', False,
                    False, (SIGNATURE_CLASS,), imports=imports,
                    # The binding is this class's own module source, and a
                    # filler-body row names the operation nowhere, so a row
                    # under one operation spelling and the same row under the
                    # other are two rows and not one.
                    setup=imports)
