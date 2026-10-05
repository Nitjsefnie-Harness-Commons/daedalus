"""The refusal SITES the analysers spell, derived by what they MEAN.

`test_mcp_import_refusals.py` compares what this reads with the tables
that claim to cover it, so a derivation recognising a raiser or a
reachable module by its SPELLING goes green on a real arm: the walk
reaches a module through whatever the runtime reaches, and a refusal is
raised through whatever object the module hands away; neither is a name.

An EDGE is what the walk would FOLLOW: a static import, and a call the walk
resolves to the import-by-name operation and hands a LITERAL module name
(`__import__('_mcp_dead_code')` reaches the same module the `import` above
it reaches). A name the walk cannot read is a REFUSAL at that call rather
than an edge — the arm is already one of that module's own sites, and the
module it might have named is unprovable. The readers are the walk's own —
`_mcp_import_closure._dynamic_callees`, `_mcp_selection_fold.callee_value`,
`is_dynamic_import` and `_mcp_import_closure._folded_string` — so a
spelling the walk accepts is an edge and one it declines is not; only the
resolution differs, a name resolving against the module names handed in
rather than the filesystem, which is what lets the same reader run over a
control tree that is only a string.

A RAISER is the OBJECT a call reaches: `REFUSAL_RAISERS` names the ones the
analysers spell out, plus every name one is BOUND to (by a store of
another, resolved to a fixed point so a chain of stores is a chain) and
every `functools.partial` built from one. An ATTRIBUTE is matched on its
own name whatever its base. The MESSAGE is not read at all: an arm whose
detail is assembled, passed by keyword, forwarded through a local,
produced by a helper or raised as an unanticipated exception type is a site
here exactly as one that spells a literal.

This module RAISES nothing and calls no raiser, which is why it is absent
from the derived set rather than named out of it.
"""
import ast
from pathlib import Path

import _mcp_code_eval
import _mcp_import_closure
import _mcp_selection_fold

TESTS = Path(__file__).resolve().parent

# The names a refusal can be raised through, spelled here rather than read
# out of the analysers because the two modules disagree about which is which
# — one DEFINES the raiser and the other only calls it — and because what
# makes a site recognizable is the object a call reaches, not its message.
REFUSAL_RAISERS = frozenset({'_refuse', 'refuse', 'AssertionError'})

# The walk's entry, named by the FUNCTION it defines rather than by its
# path, so a rename carries the anchor with it. A derivation needs one seed;
# everything else about the module set follows from it.
WALK_ENTRY = 'composition_scan_set'


def arm_sites(source, name):
    """Every site in one module's SOURCE that can raise a refusal, as
    `(enclosing function, line)`.

    A `raise` is a site whatever it raises, and so is a call that reaches a
    refusal raiser. A call that IS a raise's exception is the same site and
    is not counted twice. A module-level site carries the module's own name
    for its scope."""
    tree = ast.parse(source)
    bound = _bound_raisers(tree)
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Raise, ast.Call)):
            continue
        if isinstance(node, ast.Call) and (
                isinstance(parents.get(node), ast.Raise)
                or not _reaches_raiser(node.func, bound)):
            continue
        found.append((_owning_scope(node, parents, name), node.lineno))
    return found


def analyser_modules():
    """The analyser modules the walk is built from, derived: the CONNECTED
    COMPONENT of the `_mcp_*` module graph the walk's entry belongs to,
    keeping the members that spell a refusal site at all. The helpers
    `_mcp_code_eval`, `_mcp_dead_code` and `_mcp_selection_fold` are in the
    component and are absent because they raise nothing, rather than by
    being named out; a module the walk REACHES joins the component and has
    to be declared.
    """
    sources = {path.stem: path.read_text(encoding='utf-8')
               for path in sorted(TESTS.glob('_mcp_*.py')) if path.is_file()}
    entries = {name for name, source in sources.items()
               if _defines_function(source, WALK_ENTRY)}
    assert len(entries) == 1, f'{WALK_ENTRY} is defined by {sorted(entries)}'
    reached = component(sources, entries.pop())
    return sorted(TESTS / f'{name}.py' for name in reached
                  if arm_sites(sources[name], f'{name}.py'))


def component(sources, entry):
    """The module NAMES one entry's closure reaches, as a set.

    The relation is followed in BOTH directions, because a module that
    imports this one and one this one imports are the same closure: the walk
    reaches either. A name the component does not hold is not an edge, so an
    import of something outside the set contributes nothing rather than a
    phantom member."""
    edges = {name: _edges(source, set(sources))
             for name, source in sources.items()}
    reached = {entry}
    frontier = {entry}
    while frontier:
        further = set()
        for name in frontier:
            further |= edges[name]
            further |= {other for other, into in edges.items()
                        if name in into}
        frontier = further - reached
        reached |= frontier
    return reached


def _edges(source, known):
    """The module names one source reaches: a static import, and a call the
    walk resolves to the operation and hands a literal name."""
    tree = ast.parse(source)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found |= _reachable(alias.name, known)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            found |= _reachable(node.module or '', known)
    return found | _dynamic_edges(source, tree, known)


def _dynamic_edges(source, tree, known):
    """The module names one parse reaches through the import-by-name
    operation, read with the WALK's own readers.

    Only the LITERAL name is an edge, and the walk's other two answers are
    not: a name it cannot read is a refusal at that call, and a constant it
    can see is not a name at all. Neither reaches a module, so neither adds
    one here — the first is already one of this module's own sites."""
    bound = _mcp_import_closure._dynamic_callees(tree)
    scopes = _mcp_code_eval.scopes_for(tree, source, '')
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _mcp_selection_fold.is_dynamic_import(
                _mcp_selection_fold.callee_value(
                    node, bound, scopes), bound, scopes):
            continue
        folded = _mcp_import_closure._folded_string(
            node.args[0] if node.args else None)
        if folded is not None and not folded.startswith('.'):
            found |= _reachable(folded, known)
    return found


def _reachable(name, known):
    """The module names one dotted import name reaches among `known`: the
    module itself and the package initialisers the runtime executes to
    reach it, which is what `_import_targets` collects for the walk."""
    parts = name.split('.')
    return {'.'.join(parts[:index + 1])
            for index in range(len(parts))
            if '.'.join(parts[:index + 1]) in known}


def _bound_raisers(tree):
    """The names this module binds a refusal raiser to, at a fixed point.

    A store of something already known to raise a refusal binds one — a
    plain assignment, an annotated one, or a walrus, which are the three
    forms a binding of a name takes here — and a store of anything else
    takes a name back out, so the LAST store of a name is what says what it
    holds. The point is fixed rather than reached in one pass, and each pass
    carries the last one's answer into the next, because a store may name an
    alias a LATER store makes."""
    partials = _partial_factories(tree)
    stores = sorted((node for node in ast.walk(tree)
                     if isinstance(node, (ast.Assign, ast.AnnAssign,
                                          ast.NamedExpr))),
                    key=lambda node: (node.lineno, node.col_offset))
    bound = set(REFUSAL_RAISERS)
    for _ in range(len(stores) + 1):
        settled = set(bound)
        for store in stores:
            targets = store.targets if isinstance(store, ast.Assign) \
                else [store.target]
            for target in targets:
                name = _key(target)
                if name is None:
                    continue
                if _holds_raiser(store.value, settled, partials):
                    settled.add(name)
                else:
                    settled.discard(name)
        if settled == bound:
            break
        bound = settled
    return bound


def _holds_raiser(value, bound, partials):
    """Whether a stored value RAISES a refusal: a raiser itself, an alias of
    one, or a `functools.partial` wrapping one."""
    if isinstance(value, ast.Call) and _key(value.func) in partials:
        return bool(value.args) and _reaches_raiser(value.args[0], bound)
    return _reaches_raiser(value, bound)


def _reaches_raiser(node, bound):
    """Whether an expression IS a refusal raiser, by the object it reaches.

    A walrus is the value it binds, which is what the walk's own fold says
    of it and what the runtime evaluates it to, so a call through one calls
    what the store put in the name."""
    if isinstance(node, ast.Name):
        return node.id in bound
    if isinstance(node, ast.Attribute):
        return node.attr in bound or _key(node) in bound
    if isinstance(node, ast.NamedExpr):
        return _reaches_raiser(node.value, bound)
    return False


def _partial_factories(tree):
    """The call keys that BUILD a `functools.partial` in this module, read
    off its own imports: `f.partial` for an `import functools [as f]`, and
    the bare name for a `from functools import partial [as p]`. Nothing
    else is one — a `partial` this module never imported is a function of
    its own, whatever it is called."""
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            keys.update(f'{alias.asname or alias.name}.partial'
                        for alias in node.names if alias.name == 'functools')
        elif isinstance(node, ast.ImportFrom) and node.module == 'functools':
            keys.update(alias.asname or alias.name for alias in node.names
                        if alias.name == 'partial')
    return keys


def _key(node):
    """`a` for a name, `a.b` for an attribute over one, None otherwise."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f'{node.value.id}.{node.attr}'
    return None


def _owning_scope(node, parents, name):
    """The innermost function or class a node sits in, or `name`."""
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            return parent.name
        parent = parents.get(parent)
    return name


def _defines_function(source, function):
    """Whether one module defines a top-level function of that name."""
    return any(isinstance(node, ast.FunctionDef) and node.name == function
               for node in ast.parse(source).body)
