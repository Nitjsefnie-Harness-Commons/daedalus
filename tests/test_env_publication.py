#!/usr/bin/env python3
"""No test module publishes a credential into its own environment.

Seven modules wrote into the suite process's `os.environ` at import and never
restored it: the credential and the child's MCP port, for consumers that did
not exist. Nothing that runs after the import in the same process needs
them, and a raw child that inherits `os.environ` inherits a credential the
suite never meant to hand it. The suites take the credential as a Python
name, or as an explicit per-spawn `env=`, which is what the modules' own
constants are for.

Two halves, because either alone is a snapshot of today. The runtime half
drives `PUBLISHERS` as a table, one row per module, and watches each import
in a FRESH interpreter: this process has already imported the modules that
matter, so an in-process before/after would snapshot an installed helper and
read green whatever it did. The structural half reads every `tests/*.py` in
the worktree for a write into the process environment that the module body
can execute at import, so an EIGHTH site fails here rather than waiting for
a successor to sweep for it. Every site that scan admits is classified
below, and an unclassified one is a failure: a list of these seven paths
with no classification behind it would pass by construction on a site
nobody has met yet. `_sites()` states the exact grammar it recognises, and
the shapes it cannot see are named there rather than left for the next
reader to assume.
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent

# The three names the publication is about, and the whole of the family the
# structural half reads: a module-level write of any of them is a site.
NAMES = ('DAEDALUS_TOKEN', 'DAEDALUS_MCP_PORT', 'TOKEN')
CREDENTIAL_NAMES = ('TOKEN',)
CREDENTIAL_PREFIXES = ('DAEDALUS_',)

# `except*` parses to its own node on 3.11+; the scan reads either as the
# block it is, and a Python that has never heard of it is not this tree's.
_TRY = (ast.Try, getattr(ast, 'TryStar', ast.Try))

# The statements the scan does not descend into, because a module import
# does not run their bodies. A list of what is refused, so that a statement
# type nobody thought of is read rather than passed.
_NOT_AT_IMPORT = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# The modules this branch took the publication out of, each with the names it
# published. The runtime half imports every row; the structural half requires
# that none of them has a site left.
PUBLISHERS = (
    ('_bridge', ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('_segments', ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('test_cli', ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('test_cli_duplicate_admission',
     ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('test_cli_error_reporting', ('DAEDALUS_MCP_PORT',)),
    ('test_cli_waits', ('DAEDALUS_MCP_PORT', 'DAEDALUS_TOKEN', 'TOKEN')),
    ('test_path_safety', ('DAEDALUS_TOKEN', 'TOKEN')),
)

# The sites the scan admits that STAY, with the reason each write is the
# purpose rather than a publication: the names are the ones the row admits,
# and `None` admits whatever the file publishes. A site here publishing a
# name outside its row is unclassified, and fails like any other.
KEPT = {
    'test_http_transport': (
        ('DAEDALUS_DIR', 'DAEDALUS_PORT'),
        'the in-process load reads this root back, which is the write'),
    'test_mcp_refusal_drain': (
        ('DAEDALUS_TOKEN', 'TOKEN'),
        'the request guard reads the token at request time, which is the '
        'read direction and not this mechanism'),
    'test_mcp_server': (
        None,
        'the request guard reads this token at request time; the mapping is '
        'imported, so what it publishes is not readable from this file'),
    'test_result_routes': (
        ('DAEDALUS_DIR', 'DAEDALUS_PORT'),
        'the in-process load reads this root back, which is the write'),
    'test_segment_routes': (
        ('DAEDALUS_DIR', 'DAEDALUS_PORT', 'DAEDALUS_MAX_SEGMENT_INDEX',
         'DAEDALUS_MAX_SEGMENTS_PER_JOB', 'DAEDALUS_MAX_SEGMENT_JOB_SIZE'),
        'the in-process load reads this root and these quotas back, which '
        'is the write'),
}

SNAPSHOT = """
import importlib
import json
import os
import sys
sys.path.insert(0, sys.argv[1])
before = dict(os.environ)
importlib.import_module(sys.argv[2])
print(json.dumps({'before': before, 'after': dict(os.environ)},
                 sort_keys=True))
"""


def _import_in_a_fresh_process(module, **ambient):
    """What a fresh interpreter's environment holds after the import.

    Every inherited `DAEDALUS_*` and `TOKEN` is dropped first, so the child
    starts from a known environment and `ambient` alone decides which of the
    watched names are present. Both snapshots are taken by the child itself,
    in one interpreter: a name the platform normalises on the way into a
    child is then already normalised in both halves of the comparison.
    """
    env = {name: value for name, value in os.environ.items()
           if name not in NAMES and not name.startswith('DAEDALUS_')}
    env.update({name: value for name, value in ambient.items()
                if value is not None})
    child = subprocess.run(
        [sys.executable, '-c', SNAPSHOT, str(TESTS_DIR), module], env=env,
        capture_output=True, text=True, check=False)
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def _values(snapshot, names, other=None) -> dict:
    """The named entries, as a reader of the credential needs to see them.

    A name the snapshot does not carry is absent, not a value: absence is
    one of the outcomes these controls score, so it is skipped here and
    found by the caller's comparison. `other` is the second snapshot, and
    then each entry reads as the pair the two snapshots disagree about.
    """
    reads: dict = {
        name: (snapshot[name] if other is None
               else (snapshot[name], other[name]))
        for name in names
        if name in snapshot and _is_credential(name)}
    unread = [name for name in names
              if name in snapshot and name not in reads]
    if unread:
        reads['(names only)'] = unread
    return reads


def _is_credential(name):
    """Whether a published name is one this tree treats as a credential.

    A name the scan could not read arrives as None, and is not a credential
    — but it is also not a name to skip, which is why the admission test
    below needs both halves rather than this one.
    """
    return (isinstance(name, str)
            and (name in CREDENTIAL_NAMES
                 or name.startswith(CREDENTIAL_PREFIXES)))


def _assert_nothing_published(snap):
    """The whole environment is one value, its key set included.

    Comparing only the names this issue happens to name would pass a helper
    that published some fourth one, so the diff is over both snapshots in
    full. Values are reported for the credential family only: a failure
    message that printed the whole inherited environment would put this
    machine's secrets into every CI log that ever saw it go red.
    """
    before, after = snap['before'], snap['after']
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed = sorted(name for name in before.keys() & after.keys()
                     if before[name] != after[name])
    assert not (added or removed or changed), (
        f'the import published into the suite environment: '
        f'added={_values(after, added)} '
        f'changed={_values(before, changed, after)} removed={removed}')


def _tests_modules():
    """Every module under tests/, from the worktree the scan then reads.

    One source for the list and the contents: an enumeration taken from the
    git index would miss a file that is written but not yet added, and a
    local run would read green over what CI refuses. A stray untracked
    `tests/*.py` is therefore scanned too, which is the loud direction.
    """
    return sorted((_util.ROOT / 'tests').glob('*.py'))


def _subscript_key(node):
    """The string a subscript spells, or None when it spells nothing."""
    if (isinstance(node, ast.Constant)
            and isinstance(node.value, str)):
        return node.value
    return None


def _published_names(node, bindings, depth=2):
    """The names a written value publishes, or None when unreadable here.

    An unreadable value is None rather than an empty list on purpose: a site
    whose names cannot be read has to be classified, because a control that
    read "no names" for what it could not parse would pass it.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Dict):
        return [key.value for key in node.keys
                if isinstance(key, ast.Constant)
                and isinstance(key.value, str)]
    if depth and isinstance(node, ast.Name) and node.id in bindings:
        return _published_names(bindings[node.id], bindings, depth - 1)
    return None


def _imports(statements):
    """The names the module binds the `os` module and its `environ` to.

    `import os`, `import os as o` and `from os import environ as e` are
    three spellings of one receiver, and a scan that matched only the
    attribute `os.environ` would miss the other two outright.
    """
    modules, environs = set(), set()
    for node in statements:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'os':
                    modules.add(alias.asname or 'os')
        elif isinstance(node, ast.ImportFrom) and node.module == 'os':
            for alias in node.names:
                if alias.name == 'environ':
                    environs.add(alias.asname or 'environ')
    return modules, environs


def _is_environ(node, scope, depth=2):
    """Whether an expression names the process environment mapping.

    `scope` carries the module aliases, the `environ` names imported from
    `os`, and the module-level assignments, so a name bound to `os.environ`
    is followed to the mapping instead of being matched by its spelling.
    """
    modules, environs, bindings = scope
    if (isinstance(node, ast.Attribute) and node.attr == 'environ'
            and isinstance(node.value, ast.Name)
            and node.value.id in modules):
        return True
    if not isinstance(node, ast.Name):
        return False
    if node.id in environs:
        return True
    return bool(depth and node.id in bindings
                and _is_environ(bindings[node.id], scope, depth - 1))


def _is_main_guard(node):
    """Whether this `if` is the `if __name__ == '__main__':` guard.

    Exactly that spelling: `type(...) is ast.Eq` over the single comparison
    against `'__main__'`. A `!=` guard, an `in` guard or a guard with an
    `else` are not this statement's body — the first two run in every
    importer and the `else` runs in every importer — so a check that
    matched them would skip a write that happens.
    """
    if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
        return False
    test = node.test
    return (len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.left, ast.Name) and test.left.id == '__name__'
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == '__main__')


def _child_statements(node):
    """The statements directly inside a statement, in source order.

    Not every container for statements IS a statement: a `match` arm, an
    `except` handler and a `case` guard are not, and their bodies are. So
    the walk passes through a non-statement until it reaches a statement
    one, and leaves descending into a statement to the caller's recursion.
    """
    found = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            found.append(child)
        else:
            found.extend(_child_statements(child))
    return found


def _executed_statements(body):
    """Every statement the module body can run when it is imported.

    The recursion names what does NOT run at import and refuses those:
    a function, a lambda's scope and a class body, which run per call or
    per definition rather than at import, and the body of the `__main__`
    guard, which runs only when the file is the program. Every other
    statement is reached, whatever its type: a branch, a handler, a loop,
    a `with`, a `match` arm. The guard's `else` is reached like any other,
    because an importer runs it.
    """
    found = []
    for node in body:
        if isinstance(node, _NOT_AT_IMPORT):
            continue
        found.append(node)
        guard = _is_main_guard(node)
        for child in _child_statements(node):
            if guard and any(child is skipped for skipped in node.body):
                continue
            found.extend(_executed_statements([child]))
    return found


def _key_names(node, bindings):
    """The name a subscript, `setdefault`, `set` or `__setitem__` writes."""
    read = _published_names(node, bindings)
    if read is not None:
        return read
    return [None]


def _update_names(call, bindings):
    """The names an `update` publishes, positionally and by keyword.

    `update(**BRIDGE_ENV)` publishes exactly what `update(BRIDGE_ENV)`
    does, so the keywords are read as mappings too; a mapping that cannot
    be read contributes None rather than dropping the site.
    """
    names = []
    for written in list(call.args) + [keyword.value
                                      for keyword in call.keywords]:
        read = _published_names(written, bindings)
        names.extend([None] if read is None else read)
    return names


def _written_names(node, scope, bindings):
    """The names a statement writes into the environment, or `()`.

    An empty tuple is "not a site"; a list holding None is a site whose
    names this scan cannot read, which has to be classified.
    """
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and _is_environ(target.value, scope)):
                return _key_names(target.slice, bindings)
        return ()
    if isinstance(node, ast.AugAssign):
        if isinstance(node.target, ast.Subscript):
            if _is_environ(node.target.value, scope):
                return _key_names(node.target.slice, bindings)
            return ()
        if _is_environ(node.target, scope):
            return _key_names(node.value, bindings)
        return ()
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return ()
    call = node.value
    if not (isinstance(call.func, ast.Attribute)
            and _is_environ(call.func.value, scope)):
        return ()
    if call.func.attr in ('setdefault', 'set', '__setitem__'):
        return _key_names(call.args[0], bindings) if call.args else [None]
    if call.func.attr == 'update':
        return _update_names(call, bindings)
    return ()


def _bound_names(node):
    """The names a statement binds, with the value each is given."""
    if isinstance(node, ast.Assign):
        return [(target.id, node.value) for target in node.targets
                if isinstance(target, ast.Name)]
    if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
            and node.value is not None):
        return [(node.target.id, node.value)]
    return []


def _bindings(statements):
    """What each module-level name first holds, in source order.

    First-wins, because the order a module body actually runs in is not
    knowable from its text. A name bound twice is read here as its FIRST
    binding, which closes both directions at once: a name that ends up
    holding a plain dict gets no environment write invented for it, and a
    name that WAS the environment when the write happened keeps the write
    even if something rebinds the name afterwards. The cost is named in
    `_sites()`: a name bound inside a branch is decided by source order,
    not by which branch runs.

    Every binding form is taken — each name target of an assignment,
    chained targets included, and the target of an annotated assignment —
    so a new shape is a new case in `_bound_names` only if it is not a
    target at all.
    """
    bound = {}
    for node in statements:
        for name, value in _bound_names(node):
            bound.setdefault(name, value)
    return bound


def _sites(source):
    """Every write into the environment the module body can run at import.

    The grammar, which the module docstring and the failure message both
    claim. POSITION: any statement the module body reaches on import —
    its own, or one nested anywhere inside it, whatever statement type that
    is — except a function, a lambda's scope or a class body, and the body
    of an `if __name__ == '__main__':` guard. RECEIVER: `os.environ`, an
    `import os as ...` alias, a `from os import environ` name, or a
    module-level name whose FIRST binding is any of those. WRITE: a
    subscript assignment, an augmented assignment (the subscript's key, or
    the value's names when the whole mapping is the target), or
    `setdefault`, `set`, `__setitem__` or `update` — positionally or by
    `**keyword`.

    Not read, and named here so nobody assumes otherwise: a write inside a
    function, a lambda or a class body; a rebind of `os.environ` itself,
    which replaces the mapping rather than writing into it; a receiver
    reached by computation, such as `getattr(os, 'environ')` or
    `__import__('os').environ`; a mapping that merely CONTAINS the
    environment, as `CFG['env'][...]` does; a write the module delegates to
    a helper it calls; a published mapping built by a call rather than
    spelled as a literal; and a deletion (`del e[...]`, `pop`, `clear`),
    which removes a name rather than publishing one.

    A name this scan cannot read is reported as unreadable, never as
    absent, so it is classified rather than passed.
    """
    tree = ast.parse(source)
    statements = _executed_statements(tree.body)
    modules, environs = _imports(statements)
    bindings = _bindings(statements)
    scope = (modules, environs, bindings)
    found = []
    for node in statements:
        names = _written_names(node, scope, bindings)
        if names:
            found.append((node.lineno, names))
    return found


def test_every_publisher_leaves_a_poisoned_environment_alone(_tmp):
    """Each row's import keeps every occupied name at the value it had.

    An import that overwrote `DAEDALUS_TOKEN` with its own credential would
    still leave a suite presenting the right token through its `env=`, so
    the ambient value itself is the only thing that shows the write.
    """
    for module, published in PUBLISHERS:
        snap = _import_in_a_fresh_process(
            module, DAEDALUS_TOKEN='ambient-token', DAEDALUS_MCP_PORT='8086')
        occupied = {name: snap['before'].get(name) for name in NAMES}
        assert occupied == {
            'DAEDALUS_TOKEN': 'ambient-token', 'DAEDALUS_MCP_PORT': '8086',
            'TOKEN': None}, (module, published,
                             _values(snap['before'], NAMES))
        _assert_nothing_published(snap)


def test_every_publisher_installs_nothing_into_an_empty_environment(_tmp):
    """With every watched name absent, all three stay absent.

    Absence is a value here: a control that read only present names would
    score a module that installed `TOKEN` where there was none as clean.
    """
    for module, published in PUBLISHERS:
        snap = _import_in_a_fresh_process(module)
        for phase in ('before', 'after'):
            occupied = _values(snap[phase], NAMES)
            assert occupied == {}, (module, published, phase, occupied)


def test_no_unclassified_module_publishes_at_import(_tmp):
    """Every write the scan's grammar admits is a classified site.

    This is the half that outlives today's seven rows. A module the table
    has never met fails here with its file and line, so the sweep is this
    control's job and not a successor's. The grammar is `_sites()`'s, and it
    is two denylists rather than two lists: a statement is read unless it
    is a function, a class body or the `__main__` guard's, and a name is
    read as the environment when it resolves to the environment rather than
    when it is spelled a way this file happens to know. So a statement type
    or a binding form nobody has met is read, not passed.
    """
    unclassified, republished = [], []
    for path in _tests_modules():
        stem = path.stem
        for line, names in _sites(path.read_text(encoding='utf-8')):
            if all(isinstance(name, str) and not _is_credential(name)
                   for name in names):
                continue
            if stem in dict(PUBLISHERS):
                republished.append(f'{path.relative_to(_util.ROOT)}:{line}')
            elif stem in KEPT:
                admitted, reason = KEPT[stem]
                if admitted is None:
                    continue
                if set(names) - set(admitted):
                    unclassified.append(
                        f'{path.relative_to(_util.ROOT)}:{line} publishes '
                        f'{names}; {stem} is classified for {admitted} '
                        f'({reason})')
            else:
                unread = ' (a name the scan cannot read)' \
                    if None in names else ''
                unclassified.append(
                    f'{path.relative_to(_util.ROOT)}:{line} publishes '
                    f'{names}{unread}, and no row classifies it')
    assert not republished, (
        'a module this branch took the publication out of has one again: '
        f'{republished}')
    assert not unclassified, (
        'a module-level write into the process environment, as read by the '
        'grammar in _sites(), with no classification: '
        f'{unclassified}')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
