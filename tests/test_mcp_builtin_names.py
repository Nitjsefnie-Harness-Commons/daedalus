#!/usr/bin/env python3
"""A name is the builtin it DENOTES, in every direction the walk can err.

`bool` is the builtin the fold computes an index from, and the walk used to
decide that call with a conjunction: the call is spelled `bool` AND the map
binds no `bool`. The second half can never be true, because `bool` is a
builtin and the map tracks the import-by-name operation rather than builtins.
So the conjunction was wrong twice over — a `from builtins import bool as b`
was not recognised at all, and a `bool` the module had bound to something of
its own was read as the builtin regardless.

The question is one question, asked of the module's own symbol table: does
this name resolve to the builtin here? A name no scope binds is it, a
`from builtins import bool [as b]` binds the builtin ITSELF, and a name bound
to something of its own is not it. These are the hand boundaries of that
class; `test_mcp_selection_sweep.py` holds its generated rows.

A REPLACED name is asserted to be REFUSED, and that is the walk's own
undecided class rather than the ideal answer: nothing settles a call of a
name this walk cannot follow, and the container carries the operation, so
the walk has no target and says so. The ideal is silence, because the
runtime raises on the index before it imports anything — that is the cost
side filed as #1213, and these cases hold what the walk does rather than
freeze the defect as the contract.

These are also the hand half of what the sweep cannot generate: a name bound
by a comprehension's own target, and a use in one of two scopes that share a
name and a line, have no row there because the oracle evaluates a callee in a
flat namespace. The sweep's class carries the two carriers it CAN run — a
name replaced, and a binding under a condition — and the pair between them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402

_OPERATION = 'importlib.import_module'


def _routes(at, index):
    """The containers a fold reads an element out of, with the operation at
    position `at` and `index` naming a position.

    A dict is a MAPPING, so its key is the position the call settles to and
    the display carries it under the int `bool` normalises to: `0` and
    `False` are one key to Python's own `==`, which is the route's own
    spelling of the same value a sequence is indexed by. A set and a
    generator are not here because no subscript reaches either.
    """
    elements = ['0', '0']
    elements[at] = _OPERATION
    listed = ', '.join(elements)
    mapped = f'{at}: {_OPERATION}, {1 - at}: 0'
    return (
        f'[{listed}][{index}]',
        f'({listed})[{index}]',
        f'{{{mapped}}}[{index}]',
        f'[[{listed}]][0][{index}]',
        f'(*[{listed}],)[{index}]',
    )


def _verdict(directory, source):
    """`resolved`, `refused` or `silent` for a whole composition source.

    A `pkg/leaf.py` is on disk, so a closure that resolved the operation
    contains it and one that declined does not, which is what tells a
    resolution apart from a silence. A refusal is a third answer and not a
    resolution: the scan raised, so there is no closure at all.
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


def _composition(bindings, callee, body=''):
    return (f'\nimport importlib\n{bindings}\n\ndef load():\n{body}'
            f'    return {callee}("pkg.leaf")\n')


# The two spellings a module can bind the builtin under, and the bindings
# that make each of them something else. The last row binds NOTHING: it is
# the control the old conjunction got right by coincidence, and it is here to
# say the property covers it rather than leaves it.
_ALIASED = 'from builtins import bool as b'
_OWN = 'from builtins import bool'
_ALIASES = ((_ALIASED, 'b'), (_OWN, 'bool'), ('', 'bool'))
_REBINDS = (
    ('b = print', 'b'),
    ('from builtins import bool\nbool = print', 'bool'),
    ('bool = print', 'bool'),
    (f'{_ALIASED}\nb = print', 'b'),
    (f'{_OWN}\nb = print', 'b'),
)


def test_a_builtin_the_module_leaves_alone_is_read_in_every_position(_tmp):
    """A `from builtins import bool` binds the builtin ITSELF, under its own
    name or another one, so every spelling of the call settles and every
    position it selects resolves.

    A rule that recognises only its own name fails the alias, and one that
    recognises any `b` fails the rebound rows below; the pair is the class.
    """
    for bindings, name in _ALIASES:
        for at, argument in ((0, '0'), (1, '2')):
            for route in _routes(at, f'{name}({argument})'):
                assert _verdict(_tmp, _composition(
                    f'{bindings}\n', route)) == 'resolved', (bindings, route)


def test_an_index_that_names_a_position_the_container_does_not_hold_is_clean(
        _tmp):
    """The near-miss: the same call naming the OTHER position selects a
    filler, so the call raises on a value the fold read.

    Every rule that decides the builtin has to decide this half too, and a
    rule that only ever answers `bool` — the conjunction this class replaced
    — produces it for the alias spellings by refusing a settled index.
    """
    for bindings, name in _ALIASES:
        for at, argument in ((0, '2'), (1, '0')):
            for route in _routes(at, f'{name}({argument})'):
                assert _verdict(_tmp, _composition(
                    f'{bindings}\n', route)) == 'silent', (bindings, route)


def test_a_builtin_the_module_replaces_is_not_read_at_all(_tmp):
    """A name bound to something of its own is NOT the builtin, however the
    call is spelled and wherever the binding is.

    This is the other side of the boundary, and the class exists because both
    sides hold at once. Every row raises `TypeError` at runtime — `print(0)`
    is `None`, and no container is indexed by `None` — so the right answer is
    the walk's own: the index is a value it cannot settle, and a container
    that carries the operation is refused. Reading either as the builtin
    would resolve a module nothing imports.
    """
    for bindings, name in _REBINDS:
        for at, argument in ((0, '0'), (1, '2')):
            for route in _routes(at, f'{name}({argument})'):
                assert _verdict(_tmp, _composition(
                    f'{bindings}\n', route)) == 'refused', (bindings, route)


def test_a_shadow_nearer_the_use_is_a_shadow_too(_tmp):
    """A binding inside the function hides the builtin at that use whatever
    shape it takes, and the same name brought in inside the function IS the
    builtin there because nothing nearer replaces it.

    A rule that reads only the module's own STORES fails every row of the
    first loop — hence an import of a different builtin, which the resolver
    reports as imported and not assigned, as the two stores are not the only
    shapes a name can be shadowed by. A rule that reads any `from builtins` in
    the module fails the second, which is the direction this class is named
    for.
    """
    for bindings, name in _REBINDS:
        for shadow in ('    {0} = print\n',
                       '    for {0} in [print]:\n        pass\n',
                       '    from builtins import len as {0}\n'):
            assert _verdict(_tmp, _composition(
                f'{bindings}\n', f'[{_OPERATION}, 0][{name}(0)]',
                shadow.format(name))) == 'refused', (bindings, shadow)
    for name in ('b', 'bool'):
        assert _verdict(_tmp, _composition(
            '', f'[{_OPERATION}, 0][{name}(0)]',
            f'    from builtins import bool as {name}\n')) == 'resolved'


def test_a_use_before_its_own_binding_is_not_the_binding_yet(_tmp):
    """A module runs top to bottom, so a use PRECEDING a later binding reads
    something else — and what it reads depends on the spelling.

    The builtin under its OWN name is a use before any binding at all, so it
    is the builtin and the module imports. An ALIAS before its own import is
    a `NameError`, so the walk has no builtin to read and declines.
    """
    assert _verdict(_tmp,
                    f'\nimport importlib\nEARLY = [{_OPERATION}, 0][bool(0)]'
                    '("pkg.leaf")\nbool = print\n') == 'resolved'
    assert _verdict(_tmp,
                    f'\nimport importlib\nEARLY = [{_OPERATION}, 0][b(0)]'
                    f'("pkg.leaf")\n{_ALIASED}\n') == 'refused'


def test_a_binding_the_module_may_not_have_run_is_not_a_builtin(_tmp):
    """A `from builtins` binds the builtin ONCE THE STATEMENT HAS RUN, and
    the resolver cannot say when that is — so the walk reads it off the
    source and declines wherever it cannot.

    Three ways the module gets there without the binding being there: the
    import is written after the use, it sits under a conditional the runtime
    does not take, and it sits under a conditional at the module itself. All
    three raise before the import is ever called, so resolving any of them
    puts a module in the closure that nothing reaches.
    """
    for source in (
            # ordered after the use, in the same scope
            f'\nimport importlib\n\n\ndef load():\n'
            f'    v = [{_OPERATION}, 0][b(0)]("pkg.leaf")\n'
            f'    from builtins import bool as b\n    return v\n',
            # under a conditional the runtime does not take, in a function
            f'\nimport importlib\nc = False\n\n\ndef load():\n'
            f'    if c:\n        from builtins import bool as b\n'
            f'    return [{_OPERATION}, 0][b(0)]("pkg.leaf")\n',
            # the same, at the module itself
            f'\nimport importlib\nc = False\nif c:\n'
            f'    from builtins import bool as b\n\n\ndef load():\n'
            f'    return [{_OPERATION}, 0][b(0)]("pkg.leaf")\n'):
        assert _verdict(_tmp, source) == 'refused', source


def test_a_comprehension_binds_its_own_name(_tmp):
    """A comprehension is a scope, and a name it binds is not the module's.

    A generator whose target IS the name binds it in its own body, so a
    `b(0)` there is a call of the target and not of the builtin — while a
    generator that binds something else leaves the module's name alone, and
    that row is what says the rule is about the name and not about being in
    a comprehension. The ITERABLE is the half Python evaluates in the
    enclosing scope, so the same name there IS the module's.
    """
    def _generator(target):
        return (f'\nimport importlib\nfrom builtins import bool as b\n\n\n'
                f'def load():\n    g = ({target} for {target} in [0] if '
                f'([{_OPERATION}, 0][b(0)]("pkg.leaf"), 1)[1])\n'
                '    return list(g)\n')
    assert _verdict(_tmp, _generator('x')) == 'resolved'
    assert _verdict(_tmp, _generator('b')) == 'refused'
    iterable = (f'\nimport importlib\nfrom builtins import bool as b\n\n\n'
                'def load():\n    return [x for x in '
                f'[{_OPERATION}, 0][b(0)]("pkg.leaf")]\n')
    assert _verdict(_tmp, iterable) == 'resolved'


def test_two_scopes_on_one_line_are_told_apart(_tmp):
    """Two sibling scopes can share a name AND a line, and a rule that
    matches on those two alone cannot say which one a use is inside.

    The first lambda is `lambda: 1` and the second takes a default, so a use
    in the second is inside a scope that binds the name and a use in the
    first is inside one that does not. Both are generated, both are walked,
    and the two answers differ.
    """
    index = f'[{_OPERATION}, 0][b(0)]'
    for tail, expected in ((f'lambda b=print: {index}', 'refused'),
                           (f'lambda: {index}', 'resolved')):
        source = ('\nimport importlib\nfrom builtins import bool as b\n\n\n'
                  'def load():\n'
                  f'    f = (lambda: 1), ({tail}("pkg.leaf"))\n'
                  '    return f[1]()\n')
        assert _verdict(_tmp, source) == expected, tail


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
