"""Every way a number can end a harness child, read from the tracked tree.

The operation this audits is a wall bound on a harness child, not a
`subprocess` call, so the set that matters is the routes a number can take
from a call site to a child's death — enumerated here as ROUTES rather than
sampled from the last round's findings. Each route is a place, not a
spelling: `tests/test_starvation_bounds.py` used to refuse the word
`timeout` at five shapes and admitted a `Popen.wait(30)`.

The launcher's own deadline is permitted, and the permission is what makes
this a rule rather than a prohibition. A bound is allowed only when BOTH
hold:

  P1  its value is a Name bound to a module constant whose own definition is
      not a bare numeric literal — so the number is not written at the call
      site, and the file records what it is instead of restating a digit;
  P2  the enclosing function (or module) catches the expiry and raises
      inside the handler — so expiry is a named, classified failure rather
      than a silent kill.

Taken together these are what issue #1117 asked for: the measurement and the
multiple have to be beside the constant, and the expiry has to name itself.
Neither condition is a promise in a comment, which is why a launcher
docstring is not what enforces them.

## Why this is not a second copy of `tests/_launch_audit.py`

`origin/main` grew `_launch_audit.py` over twelve commits and it is the
stronger of the two controls, so the question "why does this module exist"
has to be answered with a property and not with an effort. Measured against
the analyser at `deff8c67`, over the routes enumerated here:

Caught by both: a `timeout=` at a launch, an assignment alias, a
from-import (as `unplaced`), and a module-level constant feeding a launch.
Caught here and NOT SEEN there, which is the whole of the argument:

  - `Popen.wait(30)` positionally, `Popen.wait(timeout=30)`, and
    `Popen.communicate(None, 30)` — a method OF a launched object;
  - GNU `timeout(1)` in the launch argv, literal or through a name — a
    number inside an argv rather than a keyword on the call;
  - a `**` spread whose key the CALLER supplies, so the launch is handed a
    mapping and the key is named nowhere in the launcher;
  - `signal.alarm(n)` and `os.waitfor(pid, n)` — a process-level deadline
    that is not a `subprocess` call at all;
  - a keyword the `subprocess` does not take, and a `functools.partial`
    bound at import.

The property is the UNIT OF ANALYSIS, and it is not a matter of scope.
`_launch_audit`'s unit is the launch call, and its scope is a filter over
launch calls: the repo-layout gate keeps `head in ('git', 'ambiguous') or
kind == 'unplaced'`. Every miss above is outside that unit, so widening the
filter cannot reach them however wide it is made. A scope extension is the
right answer for the routes both catch and the wrong instrument for these.

So the two are complementary by construction rather than by preference:
`_launch_audit` owns every launch call in the tracked tree, this owns the
routes that are not launch calls, and on the one route they share they
agree (both refuse a bare `timeout=30`).

The overlap that DOES exist is `_launch_faults`' `timeout=` arm, which is
main's route expressed twice. It is single-sited and named here so the
consolidation is a deletion rather than a redesign: when the tree-wide
Node-launch form issue #1121 describes lands, that arm is replaced by a
call to `_launch_audit.bound_sites` and this module keeps the eleven
non-launch routes. That is a follow-up on #1121, not work this wave
blocked on — the shared module does not exist on this branch's base
(`ded3e4b4`), so a census that imported it could not be run or verified
here at all.

Not enforced, and named in `test_harness_launch_bounds.py` beside its
remedy: a module under `tests/` that places a child launch without reaching
the shared gate (the shape issue #1121 lists, deferred by the maintainer),
a deadline assembled without any argument to a child — a clock comparison
plus a kill — and a bound inside a class method reached through a
constructor, which this walk resolves no body for.
"""
import ast
import inspect
import re
import subprocess
from typing import TypeGuard


def _launch_members():
    """Every `subprocess` member that reaches `Popen`, read from its source.

    A fixed point over the module's own functions, seeded at `Popen`: a
    member that CALLS a launcher launches, so `check_call` is in the set
    because it calls `run` even though its own text never says `Popen`. A
    hand list of these is a list that goes stale the day the stdlib adds
    one, which is how `Popen.wait`'s positional timeout and five real
    `Popen` keywords came to be missing from the last version of this.
    """
    members = {'Popen'}
    growing = True
    while growing:
        growing = False
        for name in dir(subprocess):
            if name.startswith('_') or name in members:
                continue
            member = getattr(subprocess, name)
            if inspect.isclass(member) or not callable(member):
                continue
            try:
                source = inspect.getsource(member)
            except (OSError, TypeError):
                continue
            for reached in sorted(members):
                if re.search(rf'\b{re.escape(reached)}\s*\(', source):
                    members.add(name)
                    growing = True
                    break
    return frozenset(members)


_LAUNCH_MEMBERS = _launch_members()
# What a launch may legitimately be handed, from the same place. An
# allowlist, so a keyword a future interpreter adds is admitted and a
# misspelling of one is a fault rather than a silent pass.
_LAUNCH_KEYWORDS = frozenset(
    inspect.signature(subprocess.Popen.__init__).parameters) | frozenset(
    inspect.signature(subprocess.run).parameters) | _LAUNCH_MEMBERS
# The keywords that carry a deadline, and so are the only bound a call site
# can pass by name. Everything else a call site passes is its own business.
_BOUND_KEYS = frozenset({'timeout'})
# GNU `timeout(1)`'s own names, in argv position zero.
_ARGV_WRAPPERS = frozenset({'timeout', 'gtimeout'})


def _wait_slot(method):
    """Which POSITIONAL slot a child's wait method takes its timeout in.

    Read off the unbound method with `self` dropped, so the number is the
    one a caller writes: `wait(30)` is slot 0 and `communicate(None, 30)`
    is slot 1. Reading it off the bound class instead would put both one
    higher and match nothing.
    """
    names = list(inspect.signature(getattr(subprocess.Popen,
                                           method)).parameters)
    positional = [n for n in names if n != 'self']
    return positional.index('timeout') if 'timeout' in positional else None


_CHILD_WAIT_SLOTS = {
    method: slot for method in ('wait', 'communicate')
    if (slot := _wait_slot(method)) is not None
}
# A handler that catches one of these is handling the expiry. The named
# error a launcher raises itself is accepted by the same route, so a
# launcher that re-raises its own type is not forced to name the stdlib's.
_EXPIRY_NAMES = frozenset({'TimeoutExpired', 'TimeoutError', 'ChildDeadline'})
# One parse per module, kept while the path closure runs: it reads every
# module's bodies repeatedly and parsing is the expensive half.
_bodies = {}

# The shared gate's launcher modules. This is the one hand-named pair, and
# it names the OPERATION (the neutral launcher every harness module reaches
# and the gate that wraps it), not the set of modules under audit: the
# callers below are derived, recursively, from these. The liveness
# assertion in the suite reds if a rename empties the set, so the pair
# cannot go stale silently.
LAUNCHER_MODULES = ('_noderun.py', '_stream_fake.py')


def _is_def(
        node: ast.AST,
) -> TypeGuard[ast.FunctionDef | ast.AsyncFunctionDef]:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def _subprocess_receivers(tree):
    """The names in `tree` bound to the `subprocess` module itself.

    Three import spellings and one assignment, because a receiver reached
    four ways is a receiver the walk would otherwise miss: `import
    subprocess`, `import subprocess as sp`, `from subprocess import run as
    launch`, and `sub = subprocess`.
    """
    modules = {'subprocess'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'subprocess':
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
            if node.value.id in modules:
                modules.update(target.id for target in node.targets
                               if isinstance(target, ast.Name))
    return modules


def _from_import_launches(tree):
    """The names bound straight to a launcher by a `from subprocess` import."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'subprocess':
            names.update(alias.asname or alias.name for alias in node.names
                         if alias.name in _LAUNCH_MEMBERS)
    return names


def _is_launch(call, receivers, direct):
    """Whether `call` places a child, by any of the four bindings."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return (func.attr in _LAUNCH_MEMBERS
                and getattr(func.value, 'id', None) in receivers)
    return isinstance(func, ast.Name) and func.id in direct


def _launched_names(scope, receivers, direct):
    """The names `scope` binds to a LAUNCHED child, for the wait rule.

    Only a launch, not any call: a name bound to a session object or a
    socket has a `wait` too, and reading that as a child's would make the
    rule fire on a shape it does not describe.
    """
    bound = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(inner, ast.Call) and _is_launch(
                inner, receivers, direct) for inner in ast.walk(node.value)):
            continue
        bound.update(target.id for target in node.targets
                     if isinstance(target, ast.Name))
    return bound


def _module_constants(tree):
    """`name -> value node` for a module's top-level numeric bindings."""
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


def _constant_is_derived(name, constants):
    """P1: the constant exists and is not a bare literal.

    A bound spelled from a named constant whose value is an expression is
    derived, and the expression is where the measurement and the multiple are
    recorded. A constant that IS a literal is the number written twice, once
    at the definition and once by reference, and it carries no basis.
    """
    if name not in constants:
        return False
    return not isinstance(constants[name], ast.Constant)


def _handles_expiry(scope):
    """P2: this scope catches an expiry and raises inside the handler."""
    for node in ast.walk(scope):
        if not isinstance(node, ast.Try):
            continue
        caught = [ast.unparse(handler.type) if handler.type else ''
                  for handler in node.handlers]
        if not any(any(word in name for word in _EXPIRY_NAMES)
                   for name in caught):
            continue
        if any(isinstance(inner, ast.Raise)
               for handler in node.handlers for inner in ast.walk(handler)):
            return True
    return False


def _permitted(bound, scope, constants):
    """Both permission conditions, or the reason one of them is missing."""
    if not isinstance(bound, ast.Name):
        return 'the bound is written at the call site, not named'
    if not _constant_is_derived(bound.id, constants):
        return (f'the constant {bound.id} is a bare literal, so nothing '
                'records what the number is')
    if not _handles_expiry(scope):
        return 'nothing catches the expiry and raises a named failure'
    return None


def _timeout_slots(tree, in_path, receivers, direct):
    """Every `child.wait(...)` / `child.communicate(...)` carrying a bound.

    Both spellings: the positional slot, which is the one that carries a
    bound with no keyword anywhere in it, and the `timeout=` keyword, which
    is the ordinary one and was the only shape read before. Keyed by the
    receiver, so a `wait` on something that is not a launched child is not
    read as one.
    """
    slots = []
    for scope in _bodies_in_scope(tree, in_path):
        launched = _launched_names(scope, receivers, direct)
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in _CHILD_WAIT_SLOTS:
                continue
            if getattr(func.value, 'id', None) not in launched:
                continue
            index = _CHILD_WAIT_SLOTS[func.attr]
            if len(node.args) > index:
                slots.append((node, node.args[index]))
            for keyword in node.keywords:
                if keyword.arg == 'timeout':
                    slots.append((node, keyword.value))
    return slots


def _function_bodies(tree):
    """`(name, body)` for every function a module defines, at any depth."""
    return {node.name: node for node in ast.walk(tree) if _is_def(node)}


def _places_a_launch(node, receivers, direct):
    """Whether this function body hands an argv to a launcher itself."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call) and _is_launch(
                inner, receivers, direct):
            return True
    return False


def _calls_any(node, names):
    """Whether this function body calls any of `names`."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            func = inner.func
            called = (func.id if isinstance(func, ast.Name)
                      else func.attr if isinstance(func, ast.Attribute)
                      else None)
            if called in names:
                return True
    return False


def seed_functions(tests_dir, launcher_modules=LAUNCHER_MODULES):
    """The functions in the launcher modules that reach a launcher.

    Derived, not named: a function that places a launch, or forwards into
    one, is on the path. `assert_gate_clean` and `require_node` live in the
    same modules and are not on it, and including them is what once pulled
    an unrelated harness into the audit.
    """
    seeds = set()
    parsed = {}
    for name in launcher_modules:
        tree = ast.parse((tests_dir / name).read_text(encoding='utf-8'))
        parsed[name] = tree
        receivers = _subprocess_receivers(tree)
        direct = _from_import_launches(tree)
        for function, body in _function_bodies(tree).items():
            if _places_a_launch(body, receivers, direct):
                seeds.add(function)
    growing = True
    while growing:
        growing = False
        for tree in parsed.values():
            receivers = _subprocess_receivers(tree)
            direct = _from_import_launches(tree)
            for function, body in _function_bodies(tree).items():
                if function in seeds:
                    continue
                if (_places_a_launch(body, receivers, direct)
                        or _calls_any(body, seeds)):
                    seeds.add(function)
                    growing = True
    return frozenset(seeds)


def _imported_names(tree):
    """The names a module brought in, which its own bodies can call."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split('.')[0]
                         for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def path_functions(tests_dir, launcher_modules=LAUNCHER_MODULES):
    """`relative module -> {its functions on the launch path}`.

    Seeds, then the transitive closure of callers across modules. A path name
    reaches a module only through an IMPORT: a module that happens to define
    its own `_run` is not the `_run` in `_boundary.py`, and treating the two
    as one put half the test tree on this path.

    Everything else in these modules is out of the audit's subject, which is
    what keeps a test that launches another SUITE as a subprocess — a
    different mechanism, and the one issue #1121 lists — from being read as
    a harness child.
    """
    seeds = seed_functions(tests_dir, launcher_modules)
    global _bodies
    own, imported = {}, {}
    _bodies = {}
    for path in sorted(tests_dir.rglob('*.py')):
        relative = path.relative_to(tests_dir).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        _bodies[relative] = _function_bodies(tree)
        own[relative] = set(_bodies[relative])
        imported[relative] = _imported_names(tree)
    known = {}
    names = set(seeds)
    by_stem = {relative[:-len('.py')]: set(found)
               for relative, found in known.items()}
    growing = True
    while growing:
        growing = False
        for relative, functions in own.items():
            reachable = _reachable(imported[relative], names, by_stem)
            found = {name for name in functions if name in seeds}
            inner = True
            while inner:
                inner = False
                for name in functions:
                    if name in found:
                        continue
                    if _calls_any(_bodies[relative][name], reachable | found):
                        found.add(name)
                        inner = True
            if found != known.get(relative, set()):
                known[relative] = found
                by_stem[relative[:-len('.py')]] = set(found)
                names |= found
                growing = True
    return {relative: found for relative, found in known.items() if found}


def _reachable(imported, names, by_stem):
    """The path names a module can actually call, by import or by stem.

    `from _noderun import run_node_program` imports the function;
    `import _noderun` then `_noderun.run_node_program(...)` imports the
    MODULE, and the function is reachable only by reading the stem's own
    path functions. One of the two routes left out is a call site the
    rule cannot see, which is the shape a caller that already imports the
    module by name will write.
    """
    reachable = imported & names
    for stem in imported & set(by_stem):
        reachable |= by_stem[stem]
    return reachable


def callable_path_names(tests_dir, launcher_modules=LAUNCHER_MODULES):
    """`relative module -> {path function names callable from it}`.

    Its own path functions plus every path function it IMPORTS. The
    difference matters: `_boundary.py` does not define `run_node_program`, it
    imports it, so a rule that judged a call site by the callee being a
    LOCAL path function could not see the caller that supplies the bound —
    the route both reviewers proved live.
    """
    paths = path_functions(tests_dir, launcher_modules)
    everywhere = set().union(*paths.values()) if paths else set()
    by_stem = {relative[:-len('.py')]: set(found)
               for relative, found in paths.items()}
    callable_names = {}
    for path in sorted(tests_dir.rglob('*.py')):
        relative = path.relative_to(tests_dir).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        reachable = _reachable(_imported_names(tree), everywhere, by_stem)
        own = paths.get(relative, frozenset())
        if own or reachable:
            callable_names[relative] = set(own) | reachable
    return callable_names


def _bodies_in_scope(tree, in_path):
    """The scopes this audit reads: the module's statements and its path
    functions, each walked once.

    Module-level statements are always in the list, because that is where a
    module constant consumed by a launch lives — one of the routes that got
    past the previous version, which walked function bodies only. The
    definitions are NOT in the module's statement list, so adding the path
    functions cannot read the same body twice.
    """
    scopes = [node for node in tree.body
              if not _is_def(node) and not isinstance(node, ast.ClassDef)]
    scopes += [body for name, body in _function_bodies(tree).items()
               if name in in_path]
    return scopes


def _faults(relative, tree, in_path=frozenset(), callable_names=frozenset()):
    """Every bound on the launch path, located in this module's own file."""
    receivers = _subprocess_receivers(tree)
    direct = _from_import_launches(tree)
    callees = set(in_path) | set(callable_names)
    constants = _module_constants(tree)
    faults = []
    for scope in _bodies_in_scope(tree, in_path):
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            if _is_launch(node, receivers, direct):
                faults.extend(
                    _launch_faults(relative, node, scope, constants))
                if _is_argv_wrapper(node, receivers, direct, scope):
                    faults.append((
                        relative, node.lineno,
                        'timeout(1) in the launch argv',
                        'a deadline in argv is a margin spelled differently'))
            if _callee_name(node) in callees and _is_bound_keyword(
                    node, scope):
                faults.append((
                    relative, node.lineno, 'bound key at a call site',
                    _key_detail(node)))
            if _is_process_deadline(node):
                faults.extend(
                    _deadline_faults(relative, node, scope, constants))
    for node, bound in _timeout_slots(
            tree, in_path, receivers, direct):
        reason = _permitted(bound, _enclosing_scope(tree, node), constants)
        if reason:
            faults.append((relative, node.lineno,
                           'positional timeout on a launched child', reason))
    return faults


def _callee_name(call):
    """The name a call reaches, bare or attribute, or None."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _launch_faults(relative, call, scope, constants):
    """R1 and R2: what the launch itself is handed."""
    faults = []
    for keyword in call.keywords:
        if keyword.arg is None:
            continue
        if keyword.arg == 'timeout':
            reason = _permitted(keyword.value, scope, constants)
            if reason:
                faults.append((relative, call.lineno,
                               'timeout= at a launch', reason))
        elif keyword.arg not in _LAUNCH_KEYWORDS:
            faults.append((
                relative, keyword.value.lineno,
                f'{keyword.arg}= at a launch',
                'not a keyword this subprocess takes, so it is a bound '
                'spelled as a typo'))
    return faults


def _key_detail(call):
    """The bound key a call site passed, named rather than described."""
    return ', '.join(sorted(
        keyword.arg for keyword in call.keywords
        if keyword.arg in _BOUND_KEYS))


def _is_bound_keyword(call, scope):
    """R5: a deadline passed at a call site on the launch path.

    Read at the CALL SITE, not at the launch, which is the route both
    reviewers proved live: a caller supplies `timeout=30`, the launcher
    forwards it, and nothing in the launcher's own body mentions a bound.

    Both spellings of "supplies" are read. A `timeout=` keyword names the
    key outright. A `**spread` does not — the key is in the mapping the
    spread forwards — so the mapping is resolved and read, or the rule
    would pass the exact chain it exists to catch.
    """
    if any(keyword.arg in _BOUND_KEYS for keyword in call.keywords):
        return True
    for keyword in call.keywords:
        if keyword.arg is not None:
            continue
        mapping = _resolve(keyword.value, scope)
        if not isinstance(mapping, ast.Dict):
            continue
        if any(_const_str(key) in _BOUND_KEYS for key in mapping.keys):
            return True
    return False


def _const_str(node):
    """The string a constant node holds, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _resolve(value, scope):
    """A local or module binding for `value` when it is a bare Name."""
    if not isinstance(value, ast.Name):
        return value
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == value.id
                   for target in node.targets):
            continue
        return node.value
    return value


def _is_argv_wrapper(call, receivers, direct, scope):
    """R4: `['timeout', '30', node, …]` — a bound wearing an argv.

    Read through a local binding, because the argv is nearly always built
    into a variable first and then handed over by name.
    """
    if not _is_launch(call, receivers, direct) or not call.args:
        return False
    argv = _resolve(call.args[0], scope)
    while isinstance(argv, ast.BinOp) and isinstance(argv.op, ast.Add):
        argv = argv.left
    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
        return False
    head = argv.elts[0]
    name = head.value if isinstance(head, ast.Constant) else None
    return isinstance(name, str) and name in _ARGV_WRAPPERS


def _is_process_deadline(call):
    """R6/R7: a number that ends the process rather than a child."""
    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(
            func.value, ast.Name):
        return False
    return (func.value.id, func.attr) in (
        ('signal', 'alarm'), ('signal', 'setitimer'), ('os', 'waitfor'))


def _deadline_faults(relative, call, scope, constants):
    """R6/R7, with the same permission, so a detector is still allowed."""
    if call.func.attr == 'os.waitfor':
        bound = call.args[1] if len(call.args) > 1 else None
    elif call.func.attr == 'signal.setitimer':
        bound = call.args[0] if call.args else None
    else:
        bound = call.args[0] if call.args else None
    if bound is None:
        return []
    reason = _permitted(bound, scope, constants)
    if not reason:
        return []
    return [(relative, call.lineno,
             f'{call.func.value.id}.{call.func.attr} ends the process',
             reason)]


def _enclosing_scope(tree, node):
    """The function a node sits in, or the module when it sits in none."""
    best = tree
    for candidate in ast.walk(tree):
        if not _is_def(candidate):
            continue
        end = candidate.end_lineno or candidate.lineno
        if candidate.lineno <= node.lineno <= end:
            if best is tree or candidate.lineno > best.lineno:
                best = candidate
    return best


def census(tests_dir, launcher_modules=LAUNCHER_MODULES):
    """Every wall bound on the launch path, located in the file it is in."""
    faults = []
    callable_names = callable_path_names(tests_dir, launcher_modules)
    for relative, in_path in path_functions(
            tests_dir, launcher_modules).items():
        tree = ast.parse((tests_dir / relative).read_text(encoding='utf-8'))
        faults.extend(_faults(relative, tree, in_path,
                              callable_names.get(relative, frozenset())))
    return sorted(faults)
