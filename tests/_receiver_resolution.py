"""WHICH RECEIVER a call is a call on, which is a different question.

`tests/_launch_path.py` answers "which source" and `tests/_launch_census.py`
answers "what in it is a bound". This is the half between them: a callee
and a parameter both NAME a receiver, and a rule cannot say whether the
number in front of it reaches a child until it has said what the thing it
is in front of IS.

Neither is keyed on a name the source could spell another way. The
network arm resolves a callee to a live OBJECT and asks whether that
object is a member of a stdlib network module, so `urlopen`, an aliased
import of it, a module aliased at the import and a local bound from it
are ONE receiver. The reachability arm asks for a PROOF that the number
reaches no child and discharges only on one: every call it reaches having
a receiver the tree binds to a literal, every one of them only building
what a `raise` raises, or -- where the receiver is a PARAMETER, which
nothing local can prove -- every call site of the function that owns it
filling the parameter with a container that caller's own scope proves --
and only once every use of the deadline sits in a position the rule can
name.

That is the whole of the rule, and each of the three is a proof rather
than a guess about what the census does not know.
`literal_bindings` is what the first two are read off; this is the map,
not the argument. The decision point that combines them,
`deadline_reaches_a_child`, lives in `tests/_deadline_reach.py` -- it moved
there when the call-site arm was added, because this file was already at
its ceiling. `literal_bindings` stayed here with the binding readers it
shares, and the module that reasons over it imports this one rather than
the other way round.
"""
import ast
import importlib
import inspect
import sys

from _binding_names import (_names_a_target_binds, _receiver_escapes,
                            _rebindings, _spread_args)
import _launch_path as path

# The modules a NETWORK READ is a member of. This names MODULES and never
# callees: the members are read off the stdlib at use, so a member a
# future interpreter adds is a control that fires rather than a gap, and
# the list cannot be tuned to whatever sample the suite happens to hold.
# A name-keyed oracle is what four consecutive waves on
# `tests/_coverage_guard.py` measured, each green while nine of ten
# held-out launcher-carrying shapes went unrefused.
_NETWORK_MODULES = ('urllib.request', 'http.client', 'socket')
_NETWORK_READS = None
_NETWORK_OBJECTS = ()


def _resolve_dotted(node, bound):
    """The canonical path a Name or an Attribute spells, or None.

    The root of the spelling is the module's own binding and the rest is
    what the source wrote after it.
    """
    key = path._dotted_key(node)
    if not key:
        return None
    root, _, rest = key.partition('.')
    base = bound.get(root)
    if base is None:
        return None
    return f'{base}.{rest}' if rest else base


def _global_rebindings(tree):
    """`id` of every rebinding a `global` statement makes module-wide.

    A function that says `global urlopen` and then assigns to it is
    rebinding the MODULE name, so the pop belongs at module scope and the
    scope gate must not swallow it. This is the one case where a
    function-local rebinding reaches past its own body, and it is a
    declaration rather than an inference.
    """
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or node.value is None:
            continue
        names = _global_names(node, tree)
        if names.intersection(_names_a_target_binds(node.targets[0])):
            out.add(id(node))
    return out


def _global_names(node, tree):
    """The names the INNERMOST function enclosing `node` declares `global`.

    Scoped to the enclosing function, and that is the whole of what
    `node` is for: the parameter is what makes the answer per-rebinding
    rather than per-module. The module-wide version read as a decision
    and decided nothing about `node`, so one `global urlopen` in any
    function turned every rebinding of `urlopen` in the module into a
    module-scope rebinding.

    INNERMOST, and the search is by NARROWEST span for a reason worth
    stating: `ast.walk` is BREADTH-first, so an outer def is visited
    before the inner one nested in it, and its first hit is the
    OUTERMOST def — a `global urlopen` in `outer` was answering for a
    rebinding inside `def inner` nested in it, where the declaration does
    not apply and the assignment is a local that shadows the import.
    Widest span is NOT the fix and reads as one: an outer def encloses the
    inner, so it is always the wider, and measured over seven nested
    shapes the widest-span search agreed with the breadth-first first hit
    on every one. Narrowest span is what picks the def the node is
    actually in.
    """
    innermost, span = None, None
    for outer in ast.walk(tree):
        if not path._is_def(outer) or node not in ast.walk(outer):
            continue
        width = (outer.end_lineno or outer.lineno) - outer.lineno
        if span is None or width < span:
            innermost, span = outer, width
    if innermost is None:
        return set()
    declared = set()
    for child in ast.walk(innermost):
        if isinstance(child, ast.Global):
            declared.update(child.names)
    return declared


def _dotted_bindings(tree):
    """`local name -> canonical dotted path` for everything a module binds.

    What a NAME is bound to, which is the question `_imported` asks for a
    launch and this asks for anything: `import a.b`, `import a.b as x`,
    `from a.b import c`, `from a.b import c as x`, and a local bound from
    any of them. A callee is a RECEIVER before it is a spelling, and a
    rule that has to answer "what is this a call on" cannot answer it from
    the text of the call.

    It sits beside `_launch_path.py`'s `_imported` rather than inside it
    for two reasons, and the second is the one that closed the question.
    `_imported` answers a different question — it records the imported
    NAME and drops the module, where this head-splits `import a.b` and
    qualifies `from a.b import c` as `a.b.c` — and
    `tests/_launch_path.py` sits under its 700-line ceiling, so the reader
    lives here rather than pushing that file to the wall.

    A binding the reader cannot resolve POPS the name rather than leaving
    the import standing. `urlopen = lambda u: u` is not `urlopen` any
    more, and a binding that outlives the name it was read from discharges
    a call that is not a read at runtime — this arm's failure direction is
    a DISCHARGE, so the miss had to be closed rather than written down.

    A pop applies to every binding form the reader can see — a plain or
    tuple or starred assignment, an augmented one, a walrus, a `del`, a
    `for` target, a comprehension target — and to ALL of a form's targets
    rather than its first, because those forms differ in syntax and not in
    what they do to the name. A pop is also gated on the node sitting
    OUTSIDE every function and class body, so a rebind inside one function
    shadows the import there and not in the module.

    A parameter is NOT a pop here, and used to be claimed as one. It is
    carried per function by `_shadowed_parameters`, which is why a
    function's own `urlopen` parameter does not refuse a read in another
    function of the same file. The control beside this is
    `test_a_rebinding_in_any_form_stops_the_read_at_both_scopes`: one row per
    form, and every row is a read the reader now REFUSES.

    The pass runs until the table stops changing, bounded by the table's
    own size, so no round count is free and a cycle cannot run. That
    failure direction is a false red and never a false green.
    """
    scoped = _scoped_assignments(tree)
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split('.')[0]
                bound[alias.asname or head] = (
                    alias.name if alias.asname else head)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound[alias.asname or alias.name] = (
                    f'{node.module}.{alias.name}')
    globals_ = _global_rebindings(tree)
    binds = {}
    for _, name in _rebindings(tree):
        binds[name] = binds.get(name, 0) + 1
    limit = len(bound) + len(binds) + 1
    for _ in range(limit):
        before = len(bound)
        for node, name in _rebindings(tree):
            if isinstance(node, ast.Assign):
                resolved = _resolve_dotted(node.value, bound)
                same = bound.get(name) == resolved
                total = binds.get(name, 0) + (1 if name in bound else 0)
                if resolved is not None and (total == 1
                                             or (same and total == 2)):
                    # `total` is `binds.get(name, 0)`, the entries
                    # `_rebindings` collected, PLUS `1 if name in bound
                    # else 0`, which counts the import pass's entry —
                    # imports are not in `_rebindings` because the
                    # import pass keeps them in `bound`. So an import of
                    # the same name is a second binding, and this loop
                    # writes `bound[name] = resolved` below, so `total`
                    # can also count THIS GUARD'S OWN earlier entry: for
                    # a lone add with no import it reads 1 on round 1 and
                    # 2 on rounds 2 and 3, and `same` then compares the
                    # add with itself. The add is honoured when
                    # `total == 1`, or when `same and total == 2` — the
                    # other binding resolving to the same dotted path.
                    # Any other second binding falls through to the POP
                    # below, and the pop removes the name.
                    bound[name] = resolved
                    continue
            if node not in scoped or id(node) in globals_:
                # A POP is a shadow claim, and it needs the scope that
                # can reach the call: `urlopen = object()` inside one
                # helper shadows the import there, not in the module.
                bound.pop(name, None)
        if len(bound) == before:
            break
    return bound


def _function_nodes(tree):
    """Every function, lambda AND class body, as a set of objects.

    A class body binds no name in any scope the reader tracks: its names
    are attributes, reached through the instance, and a class-level
    `urlopen = []` must not pop a module import.
    """
    return {node for node in ast.walk(tree)
            if path._is_def(node) or isinstance(node, (ast.Lambda,
                                                       ast.ClassDef))}


def _scoped_assignments(tree):
    """The assignments inside a function body rather than the module."""
    inside = set()
    for node in _function_nodes(tree):
        inside.update(id(child) for child in ast.walk(node))
    return {node for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign,
                                 ast.NamedExpr, ast.Delete, ast.For,
                                 ast.comprehension, ast.ExceptHandler,
                                 ast.withitem))
            and id(node) in inside}


def _function_parameters(tree):
    """`function ->` its PARAMETERS, and nothing else.

    A parameter and a rebinding are different questions and the two arms
    ask different ones. A parameter is a shadow of the name whatever the
    caller passes, so it decides both. A rebinding says the name is no
    longer the import, which is the network arm's question alone: a
    function that assigns a LITERAL to it has both rebound the import and
    made a container, and the container is the discharge the reachability
    arm is allowed to take.
    """
    names = {}
    for node in ast.walk(tree):
        if not path._is_def(node):
            continue
        args = node.args
        found = {arg.arg for arg in list(args.posonlyargs) + list(args.args)
                 + list(args.kwonlyargs)}
        found.update(a.arg for a in (args.vararg, args.kwarg) if a)
        if found:
            names[node] = frozenset(found)
    return names


def _parameters_of(node):
    """Every name a function or lambda node's own signature binds."""
    args = node.args
    found = [arg.arg for arg in list(args.posonlyargs) + list(args.args)
             + list(args.kwonlyargs)]
    found.extend(arg.arg for arg in (args.vararg, args.kwarg) if arg)
    return found


def _shadowed_parameters(tree, bindings=None):
    """`function ->` what shadows a name inside it: its PARAMETERS, and
    every binding in its own body that is not a literal.

    The module table cannot carry either. A parameter named after a
    from-import is a shadow in the function that declares it and nowhere
    else, so popping it from the module table over-refused every read in
    the file that declared it: one `urlopen` parameter in an unrelated
    helper turned a real network read into a fault, measured on five
    shapes and one of five. A function-local rebinding is the same
    argument one form wider, and it is the reason the plain `Assign` was
    the only form caught at function scope before this.

    The gate is the function's OWN body, keyed on the function NODE. That
    is what keeps the pop from becoming a module-wide one: a rebinding in
    function A does not reach a sink in function B, and the control
    `test_a_rebinding_in_one_function_does_not_reach_another` is what says
    so. It is also the gate's limit, and the limit is a measured one: a
    rebinding in an ENCLOSING function read by a sink in a nested one is
    NOT caught, because catching it needs a scope chain rather than a
    body.

    This map answers the REBOUNDING question and `_function_parameters` answers
    the PARAMETER one, because they are different: `urlopen: object = []`
    has both rebound the import and made a container, and only the first
    of those is this arm's business.
    """
    shadows = {}
    every = _rebindings(tree)
    for node in ast.walk(tree):
        if not path._is_def(node):
            continue
        inside = {id(child) for child in ast.walk(node)}
        args = node.args
        names = {arg.arg for arg in list(args.posonlyargs) + list(args.args)
                 + list(args.kwonlyargs)}
        for sub in ast.walk(node):
            # A lambda's parameters are §4.2.1's FIRST bullet and are
            # `ast.arg`, not `Name`, so the loop below cannot see them.
            # They belong to the ENCLOSING function's shadow set: a
            # lambda binds nothing in the module, so the module table
            # correctly stays as it is and is the wrong table for this.
            if isinstance(sub, ast.Lambda):
                names.update(_parameters_of(sub))
        for bound, name in every:
            if id(bound) not in inside:
                continue
            value = getattr(bound, 'value', None)
            # The literal question is only about a form that BINDS that
            # value. An `AugAssign` or a walrus carries an operand, not a
            # binding, so `urlopen += 1` is a shadow and not a container.
            if isinstance(bound, ast.Assign) and (
                    value is not None
                    and _resolve_dotted(
                        value, bindings or {}) is not None):
                continue
            names.add(name)
        if names:
            shadows[node] = frozenset(names)
    return shadows


_LITERALS = (ast.List, ast.Dict, ast.Set, ast.Tuple, ast.Constant,
             ast.JoinedStr, ast.ListComp, ast.DictComp, ast.SetComp)


def record_binding(node, owners, verdicts):
    """One STEP of the conservative join, for one node, into `verdicts`.

    Both the `Store` arm and the binder arm set live here, so the join has
    ONE representation. A join that keeps its own copy of either half does
    not stay narrower than the one it copies -- it stays WEAKER, which is
    the direction that ends a child silently. A copy of this loop that
    dropped the binders read a caller's `kid = []` rebound by `case [kid]`
    as still a proven container, because a `match` capture, an
    `except ... as`, an import alias and a `def` or `class` name all carry
    a plain `str` rather than an `ast.Name` and the `Store` arm never sees
    them. The two callers below differ in the SCOPE they walk and in
    nothing else; `owners` is theirs, built the same way.

    The one distinction inside the join is the `setdefault` a PARAMETER
    makes against the assignment every other binder makes. The two fail in
    opposite directions -- a store folds in with AND, a parameter cannot
    overwrite what a store proved -- and that is deliberate, so it lives
    here where no caller can re-derive it wrongly.

    `owners` maps every child of an `Assign`/`AnnAssign` to the statement
    holding it, which is what lets the `Store` arm read the right-hand
    side: the node CARRYING the context is the node that is bound, so for
    `self.handles = []` the key is the `Attribute` and the `self` inside
    it is Load. A `Subscript` store (`d[k] = v`) MUTATES and rebinds no
    name, which is why it is excluded here rather than left to yield no
    key.
    """
    ctx = getattr(node, 'ctx', None)
    if isinstance(ctx, (ast.Store, ast.Del)) and not isinstance(
            node, ast.Subscript):
        key = path._dotted_key(node)
        if not key:
            return
        owner = owners.get(id(node))
        if owner is None or owner.value is None:
            verdicts[key] = False
            return
        targets = (owner.targets if isinstance(owner, ast.Assign)
                   else [owner.target])
        same = any(path._dotted_key(t) == key for t in targets)
        verdicts[key] = verdicts.get(key, True) and (
            same and isinstance(owner.value, _LITERALS))
        return
    if isinstance(node, ast.arg):
        verdicts.setdefault(node.arg, False)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)):
        verdicts[node.name] = False
    elif isinstance(node, ast.ExceptHandler) and node.name:
        verdicts[node.name] = False
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
        verdicts[node.name] = False
    elif isinstance(node, ast.MatchMapping) and node.rest:
        verdicts[node.rest] = False
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            verdicts[alias.asname or alias.name] = False
    elif isinstance(node, ast.Import):
        for alias in node.names:
            verdicts[alias.asname or alias.name.split('.')[0]] = False
    elif getattr(node, 'type_params', None):
        for param in type_params_of(node):
            verdicts[param.name] = False


def literal_bindings(tree):
    """`dotted name` for every name written as a LITERAL in EVERY scope.

    The discharge's proof, and the reason it is one: a container literal
    is not a process, so a call whose receiver is one cannot end a child.
    The scan is the whole tree rather than the function because the
    binding the census needs is usually not in the function: a test
    double's recorder is bound in `__init__` and a fixture's list in the
    test body, while the deadline-carrying call sits in a third scope.

    A name is in the set only when EVERY writing of it is a literal: a
    CONSERVATIVE JOIN, not a last-write-wins. The join cannot reach
    `tests/test_real_browser_harness.py:131` on its own, because the
    deadline reaches the `recorded` PARAMETER and a parameter's value is
    its caller's: the `recorded = []` that module writes at 174 is two
    lines from the only call, and no local writing proves a value the
    module did not write. `tests/_deadline_reach.py` discharges that site
    by consulting the call sites instead, which is where the argument
    lives; this table still refuses it, and it is the SECOND reading, not
    the only one. The order is not available to be right: `ast.walk` is
    breadth-first, so a nested write is applied after a shallower one
    whatever the source says, and a table-wide last-write-wins silences a
    class that holds a real child because a LATER class bound the same
    attribute to a list. Differing reachable states are unprovable, and
    unprovable here is a refusal.

    This is not the move `_dotted_bindings` makes: it adds a binding
    module-wide and discards only outside a function or class body, so
    its two arms fail in OPPOSITE directions and neither undoes the
    other. One non-literal writing is enough: over-refuse, not discharge.
    """
    verdicts = {}
    owners = {id(child): node for node in ast.walk(tree)
              if isinstance(node, (ast.Assign, ast.AnnAssign))
              for child in ast.walk(node)}
    for node in ast.walk(tree):
        record_binding(node, owners, verdicts)
    resolved, poison = _reflective(tree)
    for key, literal in resolved.items():
        verdicts[key] = verdicts.get(key, True) and literal
    for base in poison:
        for key in [k for k in verdicts if k.startswith(f'{base}.')]:
            verdicts[key] = False
    return frozenset(key for key, ok in verdicts.items() if ok)


def type_params_of(node):
    """A node's PEP 695 type parameters, read defensively; 3.12 and later.

    `getattr` rather than a bare attribute so a 3.11 tree is a skip
    rather than an AttributeError at import.
    """
    found = getattr(node, 'type_params', None) or ()
    return [param for param in found if getattr(param, 'name', None)]


def _reflective(tree):
    """`key -> is the value a literal`, and the receivers a write POISONS.

    A reflective write either resolves to a key or poisons every
    attribute key of its receiver, because a disclosed limit may only
    COST refusals: a form that discharges a live child `main` refuses is
    a false green, and that is the direction that blocks. A plain
    `helper(self)` call is not reflective and does not poison — only a
    writer is, and the clause says so rather than leaving it to be
    inferred from the absence of a case.
    """
    resolved, poison = {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == '__dict__':
            poison.append(path._dotted_key(node.value))
    for node in ast.walk(tree):
        if not path._is_call(node):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == 'vars' and node.args:
            poison.append(path._dotted_key(node.args[0]))
        elif ((isinstance(func, ast.Name) and func.id == 'setattr')
              or (isinstance(func, ast.Attribute)
                  and func.attr == 'setattr')):
            base = path._dotted_key(node.args[0]) if node.args else ''
            name = node.args[1] if len(node.args) > 1 else None
            # A spread or short arity cannot prove WHICH receiver, so all
            # of them are poisoned; the old arity guard skipped this branch
            # entirely, neither resolving a key nor poisoning.
            if len(node.args) != 3 or _spread_args(node):
                poison.extend([base, 'self'])
            elif (isinstance(name, ast.Constant)
                  and isinstance(name.value, str)):
                key = f'{base}.{name.value}' if base else name.value
                resolved[key] = isinstance(node.args[2], _LITERALS)
            else:
                poison.append(base)
        elif (isinstance(func, ast.Attribute)
              and func.attr == '__setattr__' and node.args):
            # In the BOUND `obj.__setattr__(...)` `args[0]` is the NAME,
            # not the receiver, so a short arity poisons `self` too.
            if len(node.args) != 3 or _spread_args(node):
                poison.extend([path._dotted_key(node.args[0]), 'self'])
            else:
                poison.append(path._dotted_key(node.args[0]))
    for node in ast.walk(tree):
        if path._is_def(node) and _receiver_escapes(node):
            poison.append('self')
    return resolved, [base for base in poison if base]


def _takes_a_timeout(value):
    try:
        return 'timeout' in inspect.signature(value).parameters
    except (TypeError, ValueError):
        return False


def _network_reads():
    """`id -> None` for every live object a network read is spelled with.

    The stdlib's own member tables, filtered to the members whose
    signature takes a `timeout` — which is what a read is bounded with,
    and is also what keeps a module's unrelated re-exports out. Keyed by
    IDENTITY, so the membership test is the object a call resolves to and
    not the name it was written under. The objects are held beside the
    ids, because an id set alone lets a collected object lend its address
    to the next one allocated.
    """
    global _NETWORK_READS, _NETWORK_OBJECTS  # noqa: PLW0603
    if _NETWORK_READS is None:
        found = [value for name in _NETWORK_MODULES
                 for value in vars(importlib.import_module(name)).values()
                 if callable(value) and _takes_a_timeout(value)]
        _NETWORK_OBJECTS = tuple(found)
        _NETWORK_READS = frozenset(map(id, found))
    return _NETWORK_READS


def _live_object(dotted):
    """The live object a canonical dotted path names, or None.

    Only a name the module's own source already binds is resolved, so
    asking what a call is a call on never reaches a third-party import to
    find out.
    """
    head, _, rest = dotted.partition('.')
    if head not in sys.stdlib_module_names:
        return None
    try:
        value = importlib.import_module(head)
    except ImportError:
        return None
    for part in filter(None, rest.split('.')):
        value = getattr(value, part, None)
        if value is None:
            return None
    return value


def is_network_read(func, bound, shadowed=frozenset()):
    """Whether a callee IS a network read, resolved rather than spelled.

    The receiver question `_is_launch` asks for a launch, asked here for
    the thing it proved was NOT one. Resolution is what makes this an arm
    rather than an allowance: `urlopen`, `from urllib.request import
    urlopen as fetch`, `import urllib.request as urlr` and a local bound
    from any of them are one object, and anything else that calls itself
    `urlopen` is not a member of it.
    """
    # The set is read FIRST because deriving it imports the three modules,
    # and a `getattr` on `urllib` for a name the package has not been
    # asked for yet is a miss the resolver would report as "not a read".
    reads = _network_reads()
    dotted = _resolve_dotted(func, bound)
    if dotted is None:
        return False
    written = path._dotted_key(func)
    if written and written.partition('.')[0] in shadowed:
        return False
    value = _live_object(dotted)
    return value is not None and id(value) in reads
