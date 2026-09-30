"""Whether a `timeout` PARAMETER's value can reach a child, and the proof.

`tests/_receiver_resolution.py` answers WHICH RECEIVER a call is a call on;
this is the one rule that asks the same question of a parameter rather than
of a call. It lives here because the file it moved out of is under its size
ceiling and this arm is the growth that would have pushed it over.
`literal_bindings` stayed behind with the readers it shares — a rule that
imported this module back would be a cycle — and this module reads it as
the proof set it is handed.

Two proofs discharge, and both are POSITIVE. The function PLACES a launch,
so a caller has an undeclared way to bound the child it owns. Or the
number reaches no child. The second is read at the receiver, where a call
on a container the tree binds to a literal is harmless because a container
cannot end a child — and, where the receiver is a PARAMETER, at the CALL
SITES instead, because a parameter's value comes from the caller and no
local writing can prove it. That is the arm the `test_real_browser_
harness.py` site at line 131 needs and `origin/main` refuses, and it is
the shape a test double's recorder is written in: the double is handed a
list, and every module that hands it one can be read.

The name the call-site reading looks up is the ARGUMENT's own `Name.id`.
The double's parameter and the value passed into it are two different
identifiers, and a caller's scope may well hold a local of the PARAMETER's
name that has nothing to do with what it passes -- reading the parameter's
name let such a local vouch for a real child.

The failure direction is unchanged and is the whole of the discipline: this
arm only ever turns a `return True` into a `return False`, and only on a
proof a reader can state out loud. A receiver that is not a parameter, a
parameter with no call site in this module, a call site written outside
every function, a `*Starred` at or before the parameter's own index, an
omitted argument, an argument the caller does not write as a container, a
`Name` with a non-literal writing in the caller, and a container the caller
built out of or filled from a launch are each a refusal, and each has a
control of its own in `tests/test_launch_deadline_reach.py`. So is the
case a positional parameter is filled BY KEYWORD: the slot reader answers
a position and the argument reader does not look in `call.keywords` for
one, so `build(recorded=recorded)` is refused whatever the call wrote. That
is fail-closed and costs a real shape -- a double called entirely by
keyword -- so it is named rather than left as an accident of two readers
that each answer a different half of the question.

A `*spread` in front of the parameter is its own decision, and it was
deleted once and restored. A `Starred` occupies one index and expands to a
runtime-many, so every index at or after it is unprovable. The check is
scoped to the parameter's own slot rather than to the whole call: a `**`
fills named parameters and moves no positional index, so
`build(recorded, **extra)` is still the `recorded` the call wrote, while
`build(*spread, [])` against a three-parameter signature is not the `[]`
at index one. Measuring that on ONE-parameter signatures is what made the
check look unreachable -- there the star lands ON the slot and the
container condition refuses it for a different reason, so the table read
"nothing went red" and the branch was removed on a sample.

What the arm is NOT is complete, and the limit is a boundary rather than a
gap in the rule: a call from a module the census does not read is not
among the sites consulted, so a value handed in from outside this tree is
a receiver the arm cannot discharge. A call site is also matched to its
owning function by NAME, so a same-named method of another class is a site
this reader cannot attribute -- and, like the cross-module case, it is
named in `tests/_launch_census.py`'s own "Not enforced" list beside the
control that would fail if the reading changed.
"""
import ast

from _binding_names import _every_use_proven
import _launch_path as path
from _receiver_resolution import (_LITERALS, _live_object, _resolve_dotted)


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


def _def_enclosing(tree, node):
    """The function a node sits in, or None for the module's own scope.

    Innermost by narrowest span, and `node` itself is excluded: a def's own
    span contains its `def` line, so an inclusive search answers every
    def with itself and the walk out to an enclosing function never starts.
    """
    innermost = None
    for outer in ast.walk(tree):
        if not path._is_def(outer) or outer is node:
            continue
        end = outer.end_lineno or outer.lineno
        if outer.lineno <= node.lineno <= end:
            if innermost is None or outer.lineno > innermost.lineno:
                innermost = outer
    return innermost


def _parameter_slot(node, name):
    """Where a call must put `name` to fill it: a position or `'kw'`.

    `None` for a name the signature does not carry, and for a `*args` or a
    `**kwargs` of that name: a star parameter is filled by a spread, which
    is the one call shape the reader refuses outright, so resolving a
    position for it would be resolving nothing.
    """
    args = node.args
    for index, arg in enumerate([*args.posonlyargs, *args.args]):
        if arg.arg == name:
            return index
    if any(arg.arg == name for arg in args.kwonlyargs):
        return 'kw'
    return None


def _owning_parameter(function, receiver, tree):
    """`(owner, slot, name)` for the parameter a sink receiver names.

    The judged function first and then each function lexically enclosing
    it, because a nested `def` reaches a name the module bound in the
    function that declares it. `None` when the receiver is not a bare
    parameter name at all — a dotted `self.handles` is a container of the
    instance's own, which the literal table already answers.

    The judged function's OWN parameters are a match this function can
    find and the arm cannot use: a receiver the judged function takes
    itself is in that function's `shadowed` set, so the gate in front of
    the sink refuses the row whether or not the arm answers. The walk is
    therefore worth making for the ENCLOSING functions, and the
    first-iteration match is what `_shadowed_parameters` already covers.
    """
    if not receiver or '.' in receiver:
        return None
    scope = function
    while scope is not None:
        slot = _parameter_slot(scope, receiver)
        if slot is not None:
            return scope, slot, receiver
        scope = _def_enclosing(tree, scope)
    return None


def _call_sites_named(tree, owner):
    """`(call, the scope it is written in)` for every call of `owner`.

    By NAME, bare or as an attribute, and every match is taken rather than
    the first: a module that calls `run` from two places proves the value
    only when both say the same thing, and a call this reader cannot see
    is named as a limit in `tests/_launch_census.py` rather than assumed
    away. A match inside `owner` itself counts too, so a recursive call
    must satisfy the proof as well.
    """
    found = []
    for node in ast.walk(tree):
        if not path._is_call(node):
            continue
        func = node.func
        if ((isinstance(func, ast.Name) and func.id == owner.name)
                or (isinstance(func, ast.Attribute)
                    and func.attr == owner.name)):
            found.append((node, _def_enclosing(tree, node)))
    return found


def _written_as_a_literal(scope, name):
    """Whether EVERY writing of `name` inside `scope` is a container.

    The same conservative join `literal_bindings` makes, and the same
    reason it is a join: a name bound to a list on one line and to a child
    on the next is a child, and a last-write-wins reading of the order the
    two appear in would discharge it. A name the scope's OWN signature
    binds vetoes the same way, because a caller's parameter is a name this
    scope did not write — which is the veto `literal_bindings` applies
    module-wide and the reason it cannot answer for a nested double. The
    veto does NOT reach across scopes: a parameter of the double being
    judged is not a writing in the caller, and treating it as one is what
    made `recorded` unprovable in the first place.
    """
    verdicts = {}
    owners = {id(child): node for node in ast.walk(scope)
              if isinstance(node, (ast.Assign, ast.AnnAssign))
              for child in ast.walk(node)}
    for node in ast.walk(scope):
        ctx = getattr(node, 'ctx', None)
        if isinstance(ctx, (ast.Store, ast.Del)) and not isinstance(
                node, ast.Subscript):
            key = path._dotted_key(node)
            if not key:
                continue
            owner = owners.get(id(node))
            if owner is None or owner.value is None:
                verdicts[key] = False
                continue
            targets = (owner.targets if isinstance(owner, ast.Assign)
                       else [owner.target])
            same = any(path._dotted_key(t) == key for t in targets)
            verdicts[key] = verdicts.get(key, True) and (
                same and isinstance(owner.value, _LITERALS))
        elif isinstance(node, ast.arg) and node.arg == name:
            verdicts.setdefault(node.arg, False)
    return verdicts.get(name, False)


def _is_a_container(argument, scope):
    """Whether the argument written at a call site is a proven container.

    Two shapes and no more. The node is a container literal itself, or it
    is a `Name` every one of whose writings in the CALLER's own enclosing
    function is one. There is no third, and a `Name` written at module
    scope is refused before this is asked: the enclosing function is what
    the proof is read from, and a call outside every function has none.

    The name read is the ARGUMENT's own `Name.id`, never the parameter's.
    They are two different identifiers, and the caller's scope may well
    carry a local of the PARAMETER's name that has nothing to do with the
    value being passed: `def build(kid)` called `build(child)` from a
    function holding `kid = []` reads as a proven container under the
    parameter's name and is a real child under the argument's. That was
    not a reading this module could have got right by accident either,
    because every other row in the suite spells the two the same way --
    which is exactly why
    `test_an_argument_is_read_by_its_own_name_not_the_parameter_s` exists.
    """
    if isinstance(argument, _LITERALS):
        return True
    if not isinstance(argument, ast.Name):
        return False
    return _written_as_a_literal(scope, argument.id)


def _holds_no_child(argument, call, scope, tree, receivers, direct):
    """Whether the value a call site passes is not a launched child.

    Two readings, and they share one derivation rather than each inventing
    its own. A call INSIDE the argument is a launcher: `[Popen([...])]` is
    a container literal that holds a child, and a container literal is the
    one thing this arm would otherwise wave through. And a `Name` the
    caller bound FROM a launch is a child, which is
    `tests/_launch_path.py::_launch_bound_names` over the caller's own
    body -- the derivation that module already records beside every
    caller-supplied name, reused rather than re-derived. The call itself is
    read too: `Popen(kids)` fills the parameter with a child whatever the
    argument node is.

    All three tests read the SAME alias set, and it is the caller's ALIASES
    that make them agree. `pop = subprocess.Popen` then
    `build([pop([...])])` is one launch under three spellings, and
    `tests/_launch_path.py::_is_launch` counts a member bound to a name as
    one of its four receiver spellings -- so an empty alias set here made
    the direct spelling refuse and both aliased ones discharge, which is
    the census disagreeing with itself about one call. The set is read over
    the whole TREE, not the caller's body, because a member alias bound at
    module scope is a module binding by nature: `_subprocess_receivers`
    and `_from_import_launches` are both tree-wide for the same reason, and
    a reader that read only the caller's body would catch the local
    spelling and miss the module one.
    """
    aliases = path._member_aliases(tree, receivers, direct)
    if any(path._is_launch(inner, receivers, direct, aliases)
           for inner in ast.walk(argument) if path._is_call(inner)):
        return False
    if path._is_launch(call, receivers, direct, aliases):
        return False
    if not isinstance(argument, ast.Name):
        return True
    return argument.id not in path._launch_bound_names(
        scope, receivers, direct, aliases)


def _parameter_never_holds_a_child(function, receiver, tree, receivers,
                                   direct):
    """Whether a sink receiver is a parameter no call site fills with one.

    The arm's whole proof, in the order the conditions bite. The receiver
    is a parameter of the judged function or of one enclosing it; the
    owning function is called at least once IN THIS MODULE, because zero
    call sites prove nothing; every such call sits inside a function, so
    there is an enclosing scope to read a `Name` from; every one fills the
    parameter rather than leaving it to a default; every argument written
    there is a proven container; and no argument is a launched child.

    Anything else is the refusal this file already made, so the arm only
    ever turns a `return True` into a `return False`.
    """
    owned = _owning_parameter(function, receiver, tree)
    if owned is None:
        return False
    owner, slot, name = owned
    sites = _call_sites_named(tree, owner)
    if not sites:
        return False
    for call, scope in sites:
        if scope is None:
            # A call written outside every function has no enclosing
            # function to read a `Name` argument's writings from, and the
            # module-wide table is not that reading: it carries clauses
            # this join does not, so reading the whole tree here is WIDER
            # than the table the arm sits behind rather than narrower.
            return False
        if isinstance(slot, int) and any(
                isinstance(inner, ast.Starred)
                for inner in call.args[:slot + 1]):
            # A `Starred` occupies ONE index and expands to a runtime-many,
            # so every index at or after it is unprovable: `build(*spread,
            # [])` puts a literal where `call.args[1]` says the argument
            # is, while the body receives whatever the spread held. Scoped
            # to the slot rather than to the whole call on purpose -- a
            # `**` fills NAMED parameters and moves no positional index, so
            # `build(recorded, **extra)` is still the `recorded` the call
            # wrote. `_binding_names._spread_args` answers the coarser
            # question (any spread at all) and this needs the sharper one.
            return False
        argument = _argument_written_at(call, slot, name)
        if argument is None:
            return False
        if not _is_a_container(argument, scope):
            return False
        if not _holds_no_child(argument, call, scope, tree, receivers,
                               direct):
            return False
    return True


def _argument_written_at(call, slot, name):
    """The argument a call writes into a parameter, or None.

    None is the omitted case and a refusal rather than a default's value:
    `drain(kids)` against `drain(kids, timeout=None)` reaches the body
    with the DEFAULT, which the caller wrote no container for.
    """
    if isinstance(slot, int):
        return call.args[slot] if len(call.args) > slot else None
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def deadline_reaches_a_child(function, name, callees, receivers, direct,
                             aliases, literals, bound, shadowed, tree):
    """Whether the deadline this signature takes can reach a child.

    Two ways, and both are the census's own question asked of a parameter
    instead of a call. The function PLACES a launch, so a caller has an
    undeclared way to bound the child it owns and the signature is the
    only place that shows. Or it HANDS the parameter, or a name computed
    from it, to a call the census cannot show is harmless.

    The second half is a POSITIVE proof in the discharge direction, and
    that is the whole of the difference. `literals` carries it: a
    call whose receiver is in that set is harmless, because a container
    cannot end a child. When the receiver is a parameter instead, the
    proof is read at the call sites — see
    `_parameter_never_holds_a_child`, which is the only new thing here
    and only ever turns a `return True` into a `return False`. Either
    proof may fire only once `_every_use_proven` has cleared the gate in
    front of it — a deadline reaching no call at all is NOT a proof, and
    the generator is what falsified it: a deadline that travelled
    through a `for` target, a walrus, an augmented or annotated
    assignment, a tuple unpack or a `match` capture was still in flight,
    and an incomplete `derived` simply never saw the call it reached.

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
    limit = len(list(ast.walk(function))) + 1
    for _ in range(limit):
        before = len(derived)
        for node in ast.walk(function):
            if (isinstance(node, ast.Assign) and node.value is not None
                    and _mentions(node.value, derived)):
                derived.update(target.id for target in node.targets
                               if isinstance(target, ast.Name))
        if len(derived) == before:
            break
    if not _every_use_proven(function, derived):
        return True
    if any(isinstance(node, ast.Assign) and node.value is not None
           and any(isinstance(t, ast.Subscript) for t in node.targets)
           and _mentions(node.value, derived)
           for node in ast.walk(function)):
        # A subscript store hands the number to a container the census
        # resolves no body for, so it can reach anything.
        return True
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
            if receiver not in literals and not (
                    _parameter_never_holds_a_child(
                        function, receiver, tree, receivers, direct)):
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
