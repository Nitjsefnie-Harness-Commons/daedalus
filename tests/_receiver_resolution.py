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
reaches no child and discharges only on one: the number reaching no call
at all, every call it reaching having a receiver the tree binds to a
literal, or every one of them only building what a `raise` raises.

That is the whole of the rule, and each of the three is a proof rather
than a guess about what the census does not know.
`deadline_reaches_a_child` is where that reasoning lives, and
`literal_bindings` is what the second proof is read off; this is the map,
not the argument.
"""
import ast
import importlib
import inspect
import sys

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


def _names_a_target_binds(target):
    """Every NAME a target node binds, through a tuple or a star."""
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, (ast.Tuple, ast.List)):
        return tuple(name for child in target.elts
                     for name in _names_a_target_binds(child))
    if isinstance(target, ast.Starred):
        return _names_a_target_binds(target.value)
    if isinstance(target, (ast.MatchAs, ast.MatchStar)):
        # A `match` capture binds its name as TEXT, like `except ... as`.
        return (target.name,) if target.name else ()
    return ()


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
    """The names a node's enclosing function declares `global`."""
    declared = set()
    for outer in ast.walk(tree):
        if path._is_def(outer):
            for child in ast.walk(outer):
                if isinstance(child, ast.Global):
                    declared.update(child.names)
    return declared


def _rebindings(tree):
    """`(node, name)` for every binding a module can make besides an import.

    One pop per name, whatever form made it: a plain or tuple assignment,
    an augmented one, a walrus, a `del`, a `for` target, a comprehension
    target. Those forms differ in syntax and not in what they do to the
    name, and reading only the first of them left an import standing
    through five others — a DISCHARGE, which is this arm's own named
    failure direction. An `ast.Assign` is the one form with a right-hand
    side the reader resolves, and the caller is what uses that.
    """
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.NamedExpr)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        elif isinstance(node, (ast.For, ast.comprehension)):
            targets = [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.ExceptHandler):
            # `except E as urlopen` binds the name as TEXT, not a node.
            if node.name:
                found.append((node, node.name))
            continue
        elif isinstance(node, ast.withitem):
            targets = [node.optional_vars] if node.optional_vars else []
        elif isinstance(node, ast.match_case):
            for sub in ast.walk(node.pattern):
                if isinstance(sub, (ast.MatchAs, ast.MatchStar)):
                    found.extend((node, name)
                                 for name in _names_a_target_binds(sub))
            continue
        else:
            continue
        for target in targets:
            found.extend((node, name)
                         for name in _names_a_target_binds(target))
    return found


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

    The assignment pass runs to a fixpoint of THREE rounds, and that count
    is the whole of the termination argument: the loop is `for _ in
    range(3)`, so a cycle cannot run and a chain longer than the rounds
    stops where the budget runs out. Source order does the work in a
    single pass for a forward-declared chain; a chain written back to
    front resolves one indirection per round, so one longer than the
    budget stops short with its tail unresolved. That failure direction is
    a false red and never a false green — an unresolved callee is not a
    network read.
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
    for _ in range(3):
        for node, name in _rebindings(tree):
            if isinstance(node, ast.Assign):
                resolved = _resolve_dotted(node.value, bound)
                if resolved is not None:
                    # An ADD is safe module-wide: the binding it records
                    # is what the source says, and a function-local
                    # `_open = urllib.request.urlopen` is still the read
                    # it was written as for every caller in the module.
                    bound[name] = resolved
                    continue
            if node not in scoped or id(node) in globals_:
                # A POP is a shadow claim, and it needs the scope that
                # can reach the call: `urlopen = object()` inside one
                # helper shadows the import there, not in the module.
                bound.pop(name, None)
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


def literal_bindings(tree):
    """`dotted name` for every name written as a LITERAL in EVERY scope.

    The discharge's proof, and the reason it is one: a container literal
    is not a process, so a call whose receiver is one cannot end a child.
    The scan is the whole tree rather than the function because the
    binding the census needs is usually not in the function: a test
    double's recorder is bound in `__init__` and a fixture's list in the
    test body, while the deadline-carrying call sits in a third scope.

    A name is in the set only when EVERY writing of it is a literal, and
    that is a CONSERVATIVE JOIN rather than a last-write-wins. The order
    is not available to be right: `ast.walk` is breadth-first, so a nested
    write is applied after a shallower one whatever the source says, and
    a table-wide last-write-wins silences a class that holds a real child
    because a LATER class bound the same attribute to a list. Differing
    reachable states are unprovable, and unprovable here is a refusal.

    This is deliberately not the move `_dotted_bindings` makes. That one
    adds a binding module-wide and discards only outside a function or
    class body, so its two arms fail in OPPOSITE directions and neither
    can undo the other. Here a single non-literal writing is enough, and
    that is the opposite bias: over-refuse rather than discharge.
    """
    verdicts = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.value is not None:
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        literal = isinstance(value, _LITERALS)
        for target in targets:
            key = path._dotted_key(target)
            if key:
                verdicts[key] = verdicts.get(key, True) and literal
    return frozenset(key for key, ok in verdicts.items() if ok)


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


def _mentions(node, names):
    return any(isinstance(inner, ast.Name) and inner.id in names
               for inner in ast.walk(node))


def _deadline_sinks(function, derived):
    """Every call the deadline, or a name computed from it, reaches.

    The whole call, so a `*spread` in front of the argument is read like
    any other element: an index into `node.args` lands on the spread
    instead of the number behind it, and the deadline the spread stands
    for is then never examined.
    """
    return [call for call in ast.walk(function)
            if path._is_call(call) and _mentions(call, derived)]


def _raised_exceptions(function, bound):
    """`dotted callee` for every call that builds what a `raise` raises.

    Constructing the exception is not ending a child, and the deadline
    reaching nothing else is the same proof as the arithmetic case. The
    callee is resolved to a live object and asked what it IS, so a
    call that merely looks like an exception is not cleared — the mistake
    the two-name operation test made from the other direction.

    IMPORTED, specifically, and the limit is a false red rather than a
    false green: a local `class Refused(Exception)` does not fire this
    arm, because the resolver reads imports and a class the module
    defines is not one it can reach. A fixture that raises its own error
    with the deadline in hand is therefore refused. That HAPPENS today,
    and `test_a_raise_carrying_the_deadline_is_discharged_when_imported`
    is the control that holds it there.
    """
    raised = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        target = node.exc
        if not isinstance(target, ast.Call):
            continue
        dotted = _resolve_dotted(target.func, bound)
        value = _live_object(dotted) if dotted is not None else None
        if isinstance(value, type) and issubclass(value, BaseException):
            raised.add(path._dotted_key(target.func))
    return frozenset(raised)


def deadline_reaches_a_child(function, name, callees, receivers, direct,
                             aliases, literals, bound, shadowed):
    """Whether the deadline this signature takes can reach a child.

    Two ways, and both are the census's own question asked of a parameter
    instead of a call. The function PLACES a launch, so a caller has an
    undeclared way to bound the child it owns and the signature is the
    only place that shows. Or it HANDS the parameter, or a name computed
    from it, to a call the census cannot show is harmless.

    The second half is a POSITIVE proof in the discharge direction, and
    that is the whole of the difference. There are two of them, and
    `literal_bindings` carries the second: the deadline reaching no call
    at all, and a call whose receiver is in that set. Neither is a guess
    about what the census does not know.

    Everything else is refused, and the reason is that each earlier
    version substituted a guess for the proof. Discharging when the
    RECEIVER did not resolve silenced three shapes that reap a real child
    through an unresolved receiver. Reading the OPERATION instead, and
    discharging when its name was neither `wait` nor `communicate`,
    silenced the same class from the other side: those are two names off
    one stdlib class, and a rule built on them does not read the class, so
    `wait_procs`, a pool's `reap_all` and a spread in front of the
    argument all went unrefused. Measured against `origin/main`, both are
    the same mistake. An unresolved receiver and an unrecognised operation
    are each the census saying it does not know, and "does not know" is
    not a proof in either direction.

    The bound this leaves is the false red. A function that hands its
    deadline to a receiver the tree BUILDS rather than writes, a list from
    a call or an attribute filled from a parameter, is refused; so is a
    bare-name call to a function the walk did not resolve. That is the
    price of a discharge that is a proof rather than a guess, and it is
    in the direction that cannot end a child silently.

    A parameter the CALLER fills is a third thing and not this one: that
    number is the caller's, and `_parameter_bound_faults` reads it at the
    caller's line.
    """
    if any(path._is_launch(call, receivers, direct, aliases)
           for call in ast.walk(function) if path._is_call(call)):
        return True
    derived = {name}
    for _ in range(3):
        for node in ast.walk(function):
            if (isinstance(node, ast.Assign) and node.value is not None
                    and _mentions(node.value, derived)):
                derived.update(target.id for target in node.targets
                               if isinstance(target, ast.Name))
    sinks = _deadline_sinks(function, derived)
    if not sinks:
        return False
    raised = _raised_exceptions(function, bound)
    for call in sinks:
        func = call.func
        if path._dotted_key(func) in raised:
            continue
        if isinstance(func, ast.Name):
            return True
        if isinstance(func, ast.Attribute):
            receiver = path._dotted_key(func.value)
            if receiver not in literals:
                return True
            # A receiver the function itself SHADOWS is not the table's
            # verdict: the module saw one binding and the call may see
            # the caller's. Only a bare name can be shadowed that way —
            # `self.x` is an attribute of a parameter, and the join over
            # the class's own writings is what speaks for it.
            if isinstance(func.value, ast.Name) and func.value.id in shadowed:
                return True
            continue
        # No receiver to ask about, so no proof to offer: a refusal.
        return True
    return False
