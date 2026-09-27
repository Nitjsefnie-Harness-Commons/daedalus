#!/usr/bin/env python3
"""A lambda parameter is supplied by NAME as much as by position.

The walk resolves a call whose callee is a lambda by asking whether the call
supplies what the signature requires — and it asked by counting the call's
POSITIONAL arguments against the signature's required count. A parameter
supplied by keyword supplies it, so `(lambda x: op)(x=1)` reaches the
import-by-name operation at runtime and the walk declined it: the module the
call reached was left out of the closure with nothing refused.

The rule the fix establishes is the one Python uses: a call binds its
arguments to a signature, by position and by name together, and the call
raises wherever Python's own binding raises. What the call does not say is
what a `*args` unpacks to or what keys a `**` mapping carries — and a
question the walk cannot answer must resolve toward its undecided class, not
toward either verdict.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402

_OPERATION = 'importlib.import_module'


def _verdict(directory, source):
    """`resolved`, `refused` or `silent` for a whole composition source.

    A `pkg/leaf.py` is on disk, so a closure that resolved the operation
    contains it and one that declined does not, which is what tells a
    resolution apart from a silence. A refusal is a third answer: the scan
    raised, so there is no closure at all.
    """
    package = Path(directory) / 'pkg'
    package.mkdir(exist_ok=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    (package / 'leaf.py').write_text('leaf = True\n', encoding='utf-8')
    (Path(directory) / 'composition.py').write_text(source, encoding='utf-8')
    try:
        scanned = _mcp_import_closure.composition_scan_set(
            Path(directory) / 'composition.py', directory)
    except AssertionError:
        return 'refused'
    names = {path.relative_to(directory).as_posix() for path in scanned}
    return 'resolved' if 'pkg/leaf.py' in names else 'silent'


def _callee(signature, arguments):
    """A composition whose callee is a lambda, called with `arguments`."""
    head = f'lambda {signature}' if signature else 'lambda'
    return ('\nimport importlib\n\n\ndef load():\n'
            f'    return ({head}: {_OPERATION})({arguments})'
            '("pkg.leaf")\n')


# Every way a required parameter arrives, over the signatures a lambda can
# declare. Each row is (the signature, what the call passes), and the
# `expected` is what the RUNTIME does, read by running the source: the
# operation is called, or the call raises `TypeError` before it reaches one.
# No row's answer is taken from the walk.
REACHES = (
    ('x', '1'),
    ('x', 'x=1'),
    ('x, y', '1, 2'),
    ('x, y', 'x=1, y=2'),
    ('x, y', '1, y=2'),
    ('x, y', 'y=2, x=1'),
    ('x, y', "**{'x': 1, 'y': 2}"),
    ('x, /', '1'),
    ('x, /, y', '1, 2'),
    ('*, k', 'k=1'),
    ('*, k', "**{'k': 1}"),
    ('x=1', ''),
    ('x, y=1', '1'),
    ('x, y=1, *, k', '1, k=2'),
    ('*a', ''),
    ('*a', '1, 2'),
    ('**k', ''),
    ('**k', 'z=1'),
    ('', ''),
)

# What the call's own binding refuses, and Python refuses with it. A
# parameter supplied twice, a name the signature does not have, a name for a
# POSITIONAL-ONLY parameter, and a required one left out are all the
# `TypeError` on the spot, so all of them are CLEAN rather than resolved.
RAISES = (
    ('x', '1, x=2'),
    ('x', "x=1, **{'x': 2}"),
    ('x, y', '1, 2, x=3'),
    ('x', '1, y=2'),
    ('x, /', 'x=1'),
    ('x, /', "**{'x': 1}"),
    ('x, y', "**{'x': 1, 'z': 2}"),
    ('x', ''),
    ('x, y', '1'),
    ('x, y=1', ''),
    ('x, y=1', 'y=1'),
    ('*, k', ''),
    ('*, k, j', 'k=1'),
    ('', '1'),
)

# A `*args` unpacks to a length and a `**` mapping to a set of keys, and
# neither is a value this walk can read. The call is UNDETERMINED, which is
# the walk's own class: a container that carries the operation is refused
# rather than resolved, and the ideal — silence — is the cost side filed as
# #1213.
UNDECIDED = (
    ('x', '*d'),
    ('x', '**d'),
    ('x, y', '1, *d'),
    ('x, y', '**d, y=2'),
)


def test_a_parameter_supplied_by_name_supplies_it(_tmp):
    """Every way a required parameter can arrive reaches the operation, and
    the module it imports belongs in the closure exactly as the positional
    spelling's does.

    The property is not the KEYWORD but the supply: position and name are
    one supply, and a `**` mapping the fold reads carries the names it
    displays. A rule that counted positionals alone got the first row of this
    table and none of the other twenty.
    """
    for signature, arguments in REACHES:
        source = _callee(signature, arguments)
        assert _verdict(_tmp, source) == 'resolved', (signature, arguments)


def test_a_binding_python_refuses_is_clean_and_still_is(_tmp):
    """Two values for one parameter, a name the signature does not have, a
    name for a POSITIONAL-ONLY parameter, and a required one left out are
    each a `TypeError` on the call, so the call raises on the expression
    itself and the container's mention is beside the question — CLEAN.

    Accepting any of them is where a fix that reads names too eagerly goes
    wrong, and it is the same fail-open direction the does-not-reach class
    forbids: a form the runtime raises must never enter the closure.
    """
    for signature, arguments in RAISES:
        source = _callee(signature, arguments)
        assert _verdict(_tmp, source) == 'silent', (signature, arguments)
    assert _verdict(_tmp, _callee('x', '1, 2')) == 'silent'
    assert _verdict(_tmp, _callee('x, y', '1, 2, 3')) == 'silent'


def test_a_call_the_walk_cannot_account_for_is_undecided(_tmp):
    """A `*args` unpacks to a length and a `**` mapping to a set of keys,
    and neither is a value this walk reads — so the call is UNDETERMINED.

    That is the walk's own class, and it is a refusal: a container that
    carries the operation, with an index or a callee this walk cannot settle.
    Resolving it would resolve a form whose runtime value is a `TypeError` for
    one reading of `d` and the operation for another; declaring it a raise
    would be the same claim in the other direction.
    """
    for signature, arguments in UNDECIDED:
        source = _callee(signature, arguments)
        assert _verdict(_tmp, source) == 'refused', (signature, arguments)


# The routes a fold reads a lambda out of a value in, each written whole so
# the selection and the call are told apart by the reader.
_ROUTES = (
    '[({op})][0]',
    '({{"a": {op}}})["a"]',
    '(*[({op})],)[0]',
    '[[({op})]][0][0]',
)


def _routed(route, arguments):
    return ('\nimport importlib\n\n\ndef load():\n    return ('
            + route.format(op=f'lambda x: {_OPERATION}') + f')({arguments})'
            '("pkg.leaf")\n')


def test_the_binding_form_is_the_same_through_a_selection(_tmp):
    """The binding form is the CALLEE'S, not the spelling that reached it, so
    a fold that reads the lambda out of a value and then binds the call
    answers exactly as the bare spelling does.

    Both sides of the boundary, through every route a fold selects one in: a
    keyword supply resolves and a name the signature does not have is a
    raise. A rule that bound arguments only on the bare spelling would pass
    the hand cases in the other suite and fail every row here.
    """
    for route in _ROUTES:
        for arguments, expected in (('x=1', 'resolved'),
                                    ('1, y=2', 'silent')):
            assert _verdict(_tmp, _routed(route, arguments)) == expected, (
                route, arguments)


def test_a_projected_lambda_is_decided_by_its_signature(_tmp):
    """A projection reaches the lambda, so the call's own binding decides
    whether the projection RUNS it.

    A `**kwargs` lambda takes no arguments and returns the operation, so a
    projection of it is a call that imports; the same projection of a lambda
    with a required parameter is a `TypeError` on the projection itself.
    """
    for signature, arguments, expected in (('**k', '', 'resolved'),
                                           ('x', '', 'silent'),
                                           ('x', '1', 'resolved')):
        head = f'lambda {signature}' if signature else 'lambda'
        source = ('\nimport importlib\n\n\ndef load():\n'
                  f'    return ({head}: {_OPERATION}).__call__'
                  f'({arguments})("pkg.leaf")\n')
        assert _verdict(_tmp, source) == expected, (signature, arguments)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
