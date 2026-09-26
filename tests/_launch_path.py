"""The LAUNCH PATH: which modules the census reads, and why each is on it.

Derived, not listed. The seeds are the shared gate's launcher modules and
the functions that reach a launcher inside them; the callers are the
transitive closure across modules, resolved through the name each module
IMPORTS a path function as; and the one callee step is the module a child
is HANDED to, which a caller-only closure cannot see.

`tests/_launch_census.py` holds the rules and asks this module where to
apply them. The split is by concern, not by size: this is "which source",
that is "what in it is a bound".
"""
import ast
import inspect
import subprocess
from typing import TypeGuard

LAUNCHER_MODULES = ('_noderun.py', '_stream_fake.py')
CHILD_ENDING_MODULES = ('_processtree.py',)

# The seed pair, named because it names the GATE and not the set of
# modules under audit: the callers are derived.
LAZY_MODULES = ('_noderun.py', '_stream_fake.py')

# The path closure's result, and the parameters a path function fills with a
# launched child, so a callee can resolve a receiver that is one of its own.
_KNOWN = {}
_CHILD_PARAMETERS = {}


# --- the scope, derived ----------------------------------------------------

_TREES = {}
_BODIES = {}
_IMPORTS = {}


_SUBPROCESS_MEMBERS = (
    'Popen', 'call', 'check_call', 'check_output',
    'getoutput', 'getstatusoutput', 'run',
)


def _launch_members():
    """The `subprocess` members that reach `Popen`, by name.

    A member that CALLS a launcher launches, so `check_call` is here
    because it calls `run` even though its own text never says `Popen`, and
    `CompletedProcess` is not because it returns one rather than placing it.

    The names are written down because the closure that establishes them
    cannot be computed here: a launcher reached through a computed name
    binds a value the shared coverage guard refuses to follow, and that
    guard is fail-closed about exactly this shape. The closure is computed
    instead in `test_launch_path.py` against the live module and compared
    with this tuple, so a member a future stdlib adds is a red test rather
    than a silent omission.
    """
    return frozenset(_SUBPROCESS_MEMBERS)


_LAUNCH_MEMBERS = _launch_members()


def _popen_class():
    """The `Popen` class, resolved at use from the live module.

    A function so a control can point the census at a `Popen` whose methods
    take the timeout one slot later, which is what shows the positions are
    read rather than captured. Off `subprocess.__dict__` rather than
    `subprocess.Popen` because a member read through the module's own
    namespace is not a binding the shared coverage guard has to refuse.
    """
    return subprocess.__dict__['Popen']


def _wait_slot(method):
    """Which POSITIONAL slot a child's wait method takes its timeout in.

    Read off the unbound method with `self` dropped, so the number is the
    one a caller writes: `wait(30)` is slot 0 and `communicate(None, 30)`
    is slot 1. Reading it off the bound class would put both one higher and
    match nothing.
    """
    names = list(inspect.signature(
        getattr(_popen_class(), method)).parameters)
    positional = [name for name in names if name != 'self']
    return positional.index('timeout') if 'timeout' in positional else None


def _child_wait_slots():
    """`method -> positional timeout slot`, read from the signatures.

    A function rather than a literal so a control can recompute it against
    a stubbed `Popen` and see the positions MOVE. A hand list holding the
    right numbers reads identically and is wrong the day the signature
    changes, which is the whole of `guards/2026-08-31-a-positional-count-is-
    never-proof-in-an-ast-checker`.
    """
    return {method: slot for method in ('wait', 'communicate')
            if (slot := _wait_slot(method)) is not None}


# The rule below reads `_child_wait_slots()` at use, not this constant, so
# the constant is a record of the positions rather than the thing that
# decides them.
_CHILD_WAIT_SLOTS = _child_wait_slots()


def _module_constants(tree):
    """`name -> value node` for a module's top-level bindings."""
    constants = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if node.value is None:
            continue
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target])
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value
    return constants


def _constant_computes(name, constants):
    """P1: `name` is defined by a computation that reads named constants.

    Not `round(30 * 10)`: that is the original #1117 margin wearing a
    multiplication, and it references nothing, so nothing records what the
    thirty was.
    """
    if name not in constants:
        return False
    value = constants[name]
    if isinstance(value, ast.Constant):
        return False
    return any(isinstance(inner, ast.Name) and inner.id in constants
               for inner in ast.walk(value))


def _is_def(
        node: ast.AST,
) -> TypeGuard[ast.FunctionDef | ast.AsyncFunctionDef]:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def _is_call(node: ast.AST) -> TypeGuard[ast.Call]:
    return isinstance(node, ast.Call)


def _const_str(node):
    """The string a constant node holds, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _callee_name(call):
    """The name a call reaches, bare or attribute, or None."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _dotted(node):
    """`self.child` as the spelling a binding table can key."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f'{_dotted(node.value)}.{node.attr}'
    return ''


def _function_bodies(tree):
    """`name -> body` for every function a module defines, at any depth."""
    return {node.name: node for node in ast.walk(tree) if _is_def(node)}


def _imported(tree):
    """`local name -> canonical name` for everything a module imported.

    An `as` re-export is the reason this is a mapping and not a set:
    `from _noderun import run_node_program as rn` binds `rn`, and a walk
    that only knows the canonical spelling cannot see the call.
    """
    canonical = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                canonical[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                canonical[alias.asname or alias.name] = alias.name
    return canonical


def _subprocess_receivers(tree):
    """The names in `tree` bound to the `subprocess` module, in every
    spelling.

    `import subprocess`, `import subprocess as sp`,
    `from subprocess import run as launch` and `sub = subprocess` are four
    ways to name one call, and a census reading three of them misses a
    bound placed behind the fourth.
    """
    modules = {'subprocess'}
    for name, bound in _imported(tree).items():
        if bound == 'subprocess':
            modules.add(name)
    for _ in range(3):
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in modules):
                modules.update(target.id for target in node.targets
                               if isinstance(target, ast.Name))
    return modules


def _from_import_launches(tree):
    """The names bound straight to a launcher by a `from subprocess` import."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != 'subprocess':
            continue
        for alias in node.names:
            if alias.name in _LAUNCH_MEMBERS:
                names.add(alias.asname or alias.name)
    return names


def _member_aliases(scope, receivers, direct):
    """`local name -> launcher member` for a member bound to a name.

    `pop = subprocess.Popen` and `_RUN = subprocess.run` are one step past
    the receiver binding, and the closure a bound implies runs to a fixpoint
    so a name bound to a name is read too.
    """
    aliases = {}
    for _ in range(4):
        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign) or node.value is None:
                continue
            value = node.value
            member = None
            if (isinstance(value, ast.Attribute)
                    and getattr(value.value, 'id', None) in receivers
                    and value.attr in _LAUNCH_MEMBERS):
                member = value.attr
            elif isinstance(value, ast.Name) and (
                    value.id in direct or value.id in aliases):
                member = value.id
            if member is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = member
    return aliases


def _is_launch(call, receivers, direct, aliases=()):
    """Whether `call` places a child, by any binding at all.

    Four receiver spellings, a from-import, and a MEMBER bound to a name —
    the last because `pop = subprocess.Popen` names the same call one step
    further from the import than a receiver the walk already reads.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        return (func.attr in _LAUNCH_MEMBERS
                and getattr(func.value, 'id', None) in receivers)
    return (isinstance(func, ast.Name)
            and (func.id in direct or func.id in aliases))


def _launch_bound_names(body, receivers, direct, aliases=()):
    """The names this body binds FROM a launch, which it may hand on.

    A local from a launch, an attribute of one, a chain through a local,
    and a name this module's own caller filled with one. Computed to a small
    fixpoint so a two-step hand-off is seen.
    """
    bound = set()
    for _ in range(3):
        for node in ast.walk(body):
            if not isinstance(node, ast.Assign) or node.value is None:
                continue
            value = node.value
            launched = any(_is_launch(call, receivers, direct, aliases)
                           for call in ast.walk(value) if _is_call(call))
            if isinstance(value, ast.Name) and (
                    value.id in bound or value.id in _CHILD_PARAMETERS):
                launched = True
            if not launched:
                continue
            for target in node.targets:
                if isinstance(target, (ast.Name, ast.Attribute)):
                    bound.add(_dotted(target))
    return bound


def _parse_all(tests_dir):
    """Parse the tree once and keep it for the whole closure."""
    global _TREES, _BODIES, _IMPORTS  # noqa: PLW0603
    _TREES, _BODIES, _IMPORTS = {}, {}, {}
    for path in sorted(tests_dir.rglob('*.py')):
        relative = path.relative_to(tests_dir).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        _TREES[relative] = tree
        _BODIES[relative] = _function_bodies(tree)
        _IMPORTS[relative] = _imported(tree)
    return _BODIES


def _reachable(imported, names, by_stem):
    """The path names a module can actually call, by import or by stem.

    `from _noderun import run_node_program` imports the function and
    `import _noderun` imports the MODULE, and the function is reachable in
    the second case only by reading the stem's own path functions. A module
    that happens to define its own `_run` is not the `_run` in `_boundary.py`,
    so a name reaches a module through an import or nothing.
    """
    # Both spellings: the canonical name, and the LOCAL name it was
    # imported as, because a call in this module is written with the local
    # one. `from _stream_fake import run_gate as rg` then `rg(...)` is one
    # edge, and matching only the canonical spelling misses it.
    reachable = {name for name, canonical in imported.items()
                 if canonical in names or name in names}
    reachable |= {name for name, canonical in imported.items()
                  if canonical in names}
    for stem in set(imported.values()) & set(by_stem):
        reachable |= by_stem[stem]
    return reachable


def _calls_any(body, names):
    """Whether this body calls any of `names`, by bare or attribute name."""
    if not names:
        return False
    return any(_callee_name(call) in names
               for call in ast.walk(body) if _is_call(call))


def tree(relative):
    """One module's parse, kept by the closure that produced the path."""
    return _TREES[relative]


def bodies():
    """`relative module -> {function name: body}` for every parsed module.

    The census reads it to resolve a CALLED function's signature, which is
    the only place a deadline handed positionally says what its slot means.
    """
    return _BODIES


def body_named(name):
    """A parsed function body by bare name, or None.

    Names are not unique across the tree, so a name several modules define
    resolves to the one on the launch path when exactly one module there has
    it; a name no launcher module has is not a path function and the
    deadline it might carry is not this audit's subject.
    """
    holders = [bodies for module, bodies in _BODIES.items()
               if name in bodies
               and (module in _KNOWN
                    or module.rsplit('/', 1)[-1] in LAZY_MODULES
                    or module.rsplit('/', 1)[-1] in CHILD_ENDING_MODULES)]
    if len(holders) != 1:
        return None
    return holders[0][name]


def parameters_for(relative):
    """The parameters this module's functions receive a CHILD through."""
    return {name for name, entries in _CHILD_PARAMETERS.get(
        relative, {}).items() if any(entry[5] for entry in entries)}


def _module_owner(name):
    """The module a path function name lives in, when it is unambiguous."""
    owners = [relative for relative, in_path in _KNOWN.items()
              if name in in_path]
    return owners[0] if len(owners) == 1 else None


def _handed_a_child(relative):
    """Whether a path function hands a launched child to this module.

    The marker is deliberately narrow: the callee must be reached with a
    name the CALL SITE bound from a launch. "Launches a child" is not the
    marker — half the tree does that — and neither is "is called with some
    argument", which is every call. `tests/_processtree.py` is reached
    exactly this way: `run_node_program` calls `cleanup_process_tree` with
    the process the launch returned.
    """
    names = set(_BODIES[relative])
    for other, in_path in _KNOWN.items():
        tree = _TREES[other]
        receivers = _subprocess_receivers(tree)
        direct = _from_import_launches(tree)
        for name in in_path:
            body = _BODIES[other][name]
            aliases = _member_aliases(body, receivers, direct)
            bound = _launch_bound_names(body, receivers, direct, aliases)
            if not bound:
                continue
            for call in ast.walk(body):
                if not _is_call(call) or _callee_name(call) not in names:
                    continue
                if bound & {argument.id for argument in call.args
                            if isinstance(argument, ast.Name)}:
                    return True
    return False


def _child_parameters():
    """`relative -> {parameter name -> the call sites that fill it}`.

    Two things are recorded because a callee needs both. A child handed to
    a function as a parameter is a receiver the callee can only resolve
    through its callers. A DEADLINE handed the same way is a bound the
    callee cannot judge: the number is the caller's, so the argument
    expression is kept with the caller's own scope and constants, and each
    entry says whether the argument was a child.
    """
    filled = {}
    for other, in_path in _KNOWN.items():
        tree = _TREES[other]
        receivers = _subprocess_receivers(tree)
        direct = _from_import_launches(tree)
        constants = _module_constants(tree)
        for name in in_path:
            body = _BODIES[other][name]
            aliases = _member_aliases(body, receivers, direct)
            bound = _launch_bound_names(body, receivers, direct, aliases)
            for call in ast.walk(body):
                if not _is_call(call):
                    continue
                owner = _module_owner(_callee_name(call) or '')
                if owner is None or owner == other:
                    continue
                signature = _BODIES[owner].get(_callee_name(call))
                if signature is None:
                    continue
                positional = list(signature.args.posonlyargs)
                positional += list(signature.args.args)
                names = [argument.arg for argument in positional]
                pairs = list(enumerate(call.args))
                pairs += [(names.index(keyword.arg), keyword.value)
                          for keyword in call.keywords
                          if keyword.arg in names]
                pairs.sort()
                for position, value in pairs:
                    # Every caller-supplied NAME is recorded, not only a
                    # child, because a DEADLINE arrives the same way: the
                    # number is the caller's, and a scan that judged it in
                    # the callee's scope would refuse the shipped cleanup's
                    # own bounded reap. The flag says which kind it was.
                    if position >= len(names) or not isinstance(
                            value, ast.Name):
                        continue
                    filled.setdefault(owner, {}).setdefault(
                        names[position], []).append(
                            (other, call.lineno, value, body, constants,
                             value.id in bound))
    return filled


def seed_functions(tests_dir, launcher_modules=LAZY_MODULES):
    """The functions in the launcher modules that reach a launcher.

    Derived, not named: a function that places a launch, or forwards into
    one. `assert_gate_clean` and `require_node` live in the same modules and
    are not on the path, and including them is what once pulled an
    unrelated harness into the audit.
    """
    computed = []
    for name in launcher_modules:
        tree = ast.parse((tests_dir / name).read_text(encoding='utf-8'))
        receivers = _subprocess_receivers(tree)
        direct = _from_import_launches(tree)
        entries = {}
        for body in _function_bodies(tree).values():
            aliases = _member_aliases(body, receivers, direct)
            calls = [call for call in ast.walk(body) if _is_call(call)]
            entries[body.name] = (
                any(_is_launch(call, receivers, direct, aliases)
                    for call in calls),
                {(_callee_name(call) or '') for call in calls})
        computed.append(entries)
    seeds = {name for entries in computed for name, (launches, _) in
             entries.items() if launches}
    growing = True
    while growing:
        growing = False
        for entries in computed:
            for name, (_, calls) in entries.items():
                if name in seeds or not calls & seeds:
                    continue
                seeds.add(name)
                growing = True
    return frozenset(seeds)


def path_functions(tests_dir, launcher_modules=LAZY_MODULES):
    """`relative module -> {its functions on the launch path}`.

    Seeds, the transitive closure of callers, and then the one CALLEE step
    that matters: the module the child is handed to. A closure over callers
    alone leaves `tests/_processtree.py` — the module this branch created to
    end the child — outside the audit of the audit, and a `process.wait(5)`
    there is a live bound an order of magnitude tighter than the detector.
    """
    global _KNOWN  # noqa: PLW0603
    seeds = seed_functions(tests_dir, launcher_modules)
    own = _parse_all(tests_dir)
    known, by_stem, names = {}, {}, set(seeds)
    growing = True
    while growing:
        growing = False
        for relative, bodies in own.items():
            reachable = _reachable(_IMPORTS[relative], names, by_stem)
            found = {name for name in bodies if name in seeds}
            inner = True
            while inner:
                inner = False
                for name, body in bodies.items():
                    if name in found or not _calls_any(
                            body, reachable | found):
                        continue
                    found.add(name)
                    inner = True
            if found != known.get(relative, set()):
                known[relative] = found
                by_stem[relative[:-len('.py')]] = set(found)
                names |= found
                growing = True
    _KNOWN = known
    # The CALLEE closure is general: ANY module a path function hands a
    # launched child to joins, because that is where the child's lifetime is
    # decided. Naming the known ones was the gap — a second receiving module
    # was invisible, and a live `process.wait(5)` in it read clean. The
    # named pair stays as a pinned assertion, not as the gate.
    for relative, functions in own.items():
        if relative in known:
            continue
        if _handed_a_child(relative):
            known[relative] = set(functions)
            by_stem[relative[:-len('.py')]] = set(functions)
            names |= set(functions)
    _CHILD_PARAMETERS.clear()
    _CHILD_PARAMETERS.update(_child_parameters())
    return {relative: found for relative, found in known.items() if found}


def callable_path_names(tests_dir, launcher_modules=LAZY_MODULES,
                        paths=None):
    """`relative module -> {path function names callable from it}`.

    Its own path functions plus every path function it imports, by the name
    it imports it under. Without the `as` half, a caller that re-exports
    the launcher under a short name is invisible to the argv rule.
    """
    if paths is None:
        paths = path_functions(tests_dir, launcher_modules)
    everywhere = set().union(*paths.values()) if paths else set()
    by_stem = {relative[:-len('.py')]: set(found)
               for relative, found in paths.items()}
    callable_names = {}
    for relative, imported in _IMPORTS.items():
        reachable = _reachable(imported, everywhere, by_stem)
        own = paths.get(relative, frozenset())
        if own or reachable:
            callable_names[relative] = set(own) | reachable
    return callable_names
