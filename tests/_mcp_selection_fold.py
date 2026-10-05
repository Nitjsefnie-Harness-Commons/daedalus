"""A call's callee read as a VALUE: what it produces, and what it names.

The store side's own property is asked of the callee's value, so the value
has to be read rather than the spelling that carries it, and both halves
live here for the same reason — the two questions are asked of one node and
must not disagree about it. What a value NAMES is the mention property; what
it PRODUCES is `static_value`, and every other reader in this module is
routed through that one function.


`static_value` is the same in kind. It answers ONE question — what value
does this expression produce — and it has three answers, and every reader
here reads one of them:

- an EXPRESSION this walk carries, or a value it computed from a literal.
  A WRAPPER produces the value it wraps, a LITERAL's selection produces the
  element or entry it names, and a form the runtime has already SETTLED
  produces the value Python computes for it. A conditional or boolean whose
  condition or decided operand picks the arm produces the arm's value. A
  lambda is a FUNCTION; a call's value is whatever that function returns.
- `UNREACHABLE`, a value the runtime provably cannot reach through: an
  out-of-range position, a key the display does not carry, a base no
  subscript reads, a lambda the call cannot fill, an operator that raises.
  The call raises on the EXPRESSION, so a mention the container carries is
  beside the question and a refusal there is a false one.
- UNDETERMINED, a value only a runtime value can settle — a free name, a
  comprehension's length, a value behind a call. That is not silence: the
  caller reads the mention property over the whole expression instead.
  `unresolvable_callee` is what tells the two apart.

A new form is a new ARM here and nowhere else. Nothing else in this module
decides a value by node shape, which is what keeps the readers agreeing:
last-wins is a property of a dict display rather than a position in a list
of cases, a lambda is a value rather than a spelling of one, and asking
Python's own operator settles an index no table of this walk's own would
have to be taught to recognise.
"""
import ast
import operator
from typing import TypeGuard

# The container literals a folded value can BE: a call on one raises
# before it reaches anything, however much of the operation it carries.
CONTAINERS = (ast.List, ast.Tuple, ast.Set, ast.Dict,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

# The decided base values no subscript can reach at all, whatever key or
# position it names — one `TypeError` between them. A DICT comprehension is
# a mapping and a LIST comprehension's length is a runtime value, so
# neither is here.
UNSUBSCRIPTED = (ast.Set, ast.SetComp, ast.GeneratorExp)

# A sentinel rather than `None` so a literal `None` — a value a dict key
# can be and an index cannot — is not read as unreachable.
UNREACHABLE = object()

# Not the same as UNREACHABLE: a free name and a slice are both unknown,
# and a reader that conflated them would refuse what it cannot decide.
UNREAD = object()

# The two names the operation map tracks. `REGISTRY_NAMES` are the one
# tracked name that is NOT a mention.
DYNAMIC_ATTRIBUTES = ('import_module', '__import__')
REGISTRY_NAMES = ('sys', 'registry')

# Python's own operators, not a table beside this walk. `/` is here too,
# and the float it leaves is not a position — the value being settled.
_ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub,
               ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv,
               ast.Mod: operator.mod, ast.Pow: operator.pow,
               ast.Div: operator.truediv}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

# What this walk computes WITH: bounded rather than typed, because the
# question is the COST of the computation. An operand outside the bound is
# UNDETERMINED, never a wait.
_SETTLED = 1 << 16


def _computable(value):
    """Whether this walk computes with a value rather than carrying it:
    the SIZE of the computation, not the type of the operand — a value
    with neither a magnitude nor a length is computable however strange it
    is, and the operator's own `TypeError` settles it.
    """
    if isinstance(value, (int, float)):
        return -_SETTLED <= value <= _SETTLED
    if isinstance(value, (str, bytes, tuple)):
        return len(value) <= _SETTLED
    return True


def _operator_settled(operation, *operands):
    """What an operator settles, `UNREAD` when this walk will not ask it,
    and `UNREACHABLE` when the runtime's own settlement is to RAISE — a
    raise is a settlement like any other, and it is `UNREACHABLE` rather
    than `UNREAD` because the walk knows it.
    """
    if any(not _computable(operand) for operand in operands):
        return UNREAD
    try:
        return operation(*operands)
    except (ArithmeticError, TypeError, ValueError):
        return UNREACHABLE


def _runtime_settled(node, bound, scopes):
    """The value a form the runtime has already SETTLED produces, `UNREAD`
    when this walk will not compute it, and `UNREACHABLE` when the runtime
    raises before the value exists. SETTLED is a property, not a list:
    constants, operators over settled values, walruses, agreeing
    conditionals and `bool` calls are values Python has computed already.
    A parameter no store has taken back carries its own default; a
    field-less f-string is the constant the compiler builds it from. A
    form nobody has met is `UNREAD` here.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp):
        unary = _UNARY.get(type(node.op))
        return (UNREAD if unary is None
                else _operator_settled(
                    unary, _runtime_settled(node.operand, bound, scopes)))
    if isinstance(node, ast.BinOp):
        arithmetic = _ARITHMETIC.get(type(node.op))
        return (UNREAD if arithmetic is None
                else _operator_settled(
                    arithmetic, _runtime_settled(node.left, bound, scopes),
                    _runtime_settled(node.right, bound, scopes)))
    if isinstance(node, ast.NamedExpr):
        return _runtime_settled(node.value, bound, scopes)
    if isinstance(node, ast.IfExp):
        # The branch is a runtime value, so a conditional settles only when
        # BOTH arms do and they agree: the value is then the same whichever
        # one the runtime picks.
        body = _runtime_settled(node.body, bound, scopes)
        other = _runtime_settled(node.orelse, bound, scopes)
        if not _computable(body) or not _computable(other) or body != other:
            return UNREAD
        return body
    if _is_the_bool_call(node, scopes):
        value = _runtime_settled(node.args[0], bound, scopes)
        return UNREAD if not _computable(value) else int(bool(value))
    if isinstance(node, ast.Name):
        default = scopes.parameter_default(node, node.id)
        if default is None:
            return UNREAD
        return _runtime_settled(default, bound, scopes)
    if isinstance(node, ast.JoinedStr):
        constants = [part.value for part in node.values
                     if isinstance(part, ast.Constant)
                     and isinstance(part.value, str)]
        if len(constants) == len(node.values):
            return ''.join(constants)
    return UNREAD


def _is_the_bool_call(node, scopes) -> bool:
    """Whether this call is a call of the `bool` BUILTIN over one argument:
    the question is what its callee denotes, and `bound` is not asked
    because it tracks the import-by-name operation and carries no builtin.
    """
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and len(node.args) == 1 and not node.keywords
            and scopes.denotes_builtin(node.func, 'bool'))


def _is_getattr(node) -> TypeGuard[ast.Call]:
    """Whether this call is a `getattr` lookup of at least two arguments."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'getattr' and len(node.args) >= 2)


def _projected(node: ast.expr):
    """The value a WRAPPER projects, or None when this is not one.
    `X.__call__` is how Python says "X is callable", and
    `getattr(X, '__call__')` is its second spelling. A `getattr` with a
    different constant names nothing, and a key this walk cannot read may
    be `__call__` or anything else, so the fold declines it to the MENTION
    property.
    """
    if isinstance(node, ast.Attribute) and node.attr == '__call__':
        return node.value
    if not _is_getattr(node):
        return None
    key = node.args[1]
    if not isinstance(key, ast.Constant) or key.value != '__call__':
        return None
    return node.args[0]


def _carried_keys(node, bound, scopes):
    """The names a `**` mapping supplies, or None when the walk cannot read
    them. A dict DISPLAY settles its own entries — the last of two equal
    keys, and the equality is Python's — so `**{'x': 1}` supplies exactly
    the keys it carries. Anything else is a runtime value this walk cannot
    read, and a call that carries one supplies an UNKNOWN set of names
    rather than none: `d` empty raises and `d` holding the key reaches.
    """
    if not isinstance(node, ast.Dict):
        return None
    keys = {}
    for key in node.keys:
        if key is None:
            return None
        value = _settled_position(key, bound, scopes)
        if not isinstance(value, str):
            return None
        # A dict keeps the LAST of two equal keys, so a display carrying one
        # name twice supplies that name ONCE — the two entries are one
        # binding, and reading them as two would make a `**` that fills a
        # parameter look like a call that supplies it twice.
        keys[value] = None
    return list(keys)


def _supplied(call, bound, scopes):
    """The names a call supplies BY NAME, or None when the call does not
    say. One carrier the walk cannot read leaves the whole call undecided
    rather than half-bound: a required parameter it may or may not fill is
    the question the call is being asked.
    """
    names = []
    for keyword in call.keywords:
        if keyword.arg is not None:
            names.append(keyword.arg)
            continue
        carried = _carried_keys(keyword.value, bound, scopes)
        if carried is None:
            return None
        names.extend(carried)
    return names


def _fills(func: ast.Lambda, call: ast.Call, bound, scopes):
    """What a call supplies to a lambda's signature: whether it supplies
    what the signature requires, whether it RAISES instead, or `UNREAD` when
    it does not say.

    A parameter is supplied by POSITION or by NAME, one supply, and the
    call raises wherever Python's own binding raises: a second value for
    one DECLARED parameter (by position, by name, or through a `**` the
    walk reads), a name the signature does not have and no `**kwargs` to
    catch it, or a name for a POSITIONAL-ONLY parameter. A name no
    parameter declares is caught by a `**kwargs` rather than refused, and
    carries the same second-supply check the declared arms do. A `*args`
    is `UNREAD`, the caller's own class.
    """
    args = func.args
    params = args.posonlyargs + args.args
    at = {argument.arg: index for index, argument in enumerate(params)}
    only = {argument.arg for argument in args.posonlyargs}
    # DECLARED and REQUIRED are not the same set: a default says a
    # parameter may go unfilled, not that naming it is an error.
    keyword_only = {argument.arg for argument in args.kwonlyargs}
    wanted = {argument.arg for argument, default
              in zip(args.kwonlyargs, args.kw_defaults) if default is None}
    names = _supplied(call, bound, scopes)
    if names is None or any(isinstance(value, ast.Starred)
                            for value in call.args):
        return UNREAD
    filled = set()
    by_keyword = set()
    by_catch_all = set()
    for position in range(len(call.args)):
        if position >= len(params):
            if args.vararg is None:
                return False
            break
        filled.add(position)
    for name in names:
        if name in only:
            return False
        index = at.get(name)
        if index is not None:
            if index in filled:
                return False
            filled.add(index)
        elif name in keyword_only:
            if name in by_keyword:
                return False
            by_keyword.add(name)
            wanted.discard(name)
        elif args.kwarg is None:
            return False
        elif name in by_catch_all:
            # A name no parameter declares is supplied ONCE however many
            # times, and a second supply is the same `TypeError` a second
            # positional is. The catch-all is an arm like any other here.
            return False
        else:
            by_catch_all.add(name)
    # A default is on the TAIL of the parameters, so the required ones are
    # the head. A `*args` is asked above, where the positionals it takes are
    # counted; it is not a licence to leave a REQUIRED parameter unfilled,
    # because the vararg soaks up what comes after it and not what came
    # before.
    required = set(range(len(params) - len(args.defaults)))
    if not filled >= required:
        return False
    return not wanted


def _expanded_elts(base):
    """A literal container's elements, every starred literal expanded in
    place. The expansion RECURSES because a star is a star wherever it
    sits: `(*(*[op],),)` unpacks the one-element tuple the inner star
    produces, and the position an index names is then the inner list's to
    read. None only when a star carries a value this walk cannot read,
    which is the one container shape that puts the other positions out of
    reach.
    """
    elements = []
    for element in base.elts:
        if not isinstance(element, ast.Starred):
            elements.append(element)
            continue
        if not isinstance(element.value, (ast.List, ast.Tuple)):
            return None
        inner = _expanded_elts(element.value)
        if inner is None:
            return None
        elements.extend(inner)
    return elements


def _settled_position(node, bound, scopes):
    """A POSITION or a KEY an index expression names, or `UNREAD` when this
    walk does not read one. A value is not a position until the container
    says so, so the reading is the caller's decision and not this one's.
    """
    value, decided = static_value(node, bound, scopes)
    if not decided or isinstance(value, ast.AST):
        return UNREAD
    return value


def _display_entries(base, bound, scopes):
    """The entries a dict display CARRIES, `None` when unreadable, and a
    `raises` answer for a display that never finishes building."""
    entries = []
    for key, value in zip(base.keys, base.values):
        if key is None:
            produced, decided = static_value(value, bound, scopes)
            if not decided or not isinstance(produced, ast.Dict):
                return None, False
            inner, raised = _display_entries(produced, bound, scopes)
            if inner is None or raised:
                return None, raised
            entries.extend(inner)
            continue
        if isinstance(key, (ast.List, ast.Set, ast.Dict,
                            ast.DictComp, ast.SetComp)):
            return None, True
        settled = _settled_position(key, bound, scopes)
        if settled is UNREACHABLE:
            return None, True
        if settled is UNREAD:
            return None, False
        entries.append((settled, value))
    return entries, False


def _keyed(node, base, bound, scopes):
    """The value a dict literal's key selects, or `UNREACHABLE` for a key
    the display does not carry. A DISPLAY keeps the LAST of two equal
    keys — the runtime's rule — and
    the equality is Python's, so `1`, `1.0` and `True` name one entry and
    `0` and `False` another. A `**` unpack whose display the walk reads
    merges its entries where the runtime merges them; one it cannot read
    puts the keys out of reach.
    """
    entries, raised = _display_entries(base, bound, scopes)
    if raised:
        return UNREACHABLE, True
    if entries is None:
        return node, False
    wanted = _settled_position(node.slice, bound, scopes)
    if wanted is UNREAD:
        return node, False
    selected = None
    for key, value in entries:
        if key == wanted:
            selected = value
    return ((UNREACHABLE, True) if selected is None
            else static_value(selected, bound, scopes))


def _is_the_operation(node, bound, scopes):
    """Whether this expression IS the operation, and so a FUNCTION — the
    one decided value that is neither a container nor subscriptable: a
    function has no `__getitem__`, so `op[0]` is the same `TypeError` a
    set's is.
    """
    if isinstance(node, ast.Name):
        return bound.get(node.id) == 'by name'
    return isinstance(node, ast.Attribute) and is_dynamic_import(
        node, bound, scopes)


def _called(func, call, bound, scopes):
    """The value a call of a LAMBDA produces: the body its signature
    accepts, `UNREACHABLE` when the call is a raise, and nothing decided
    when the call does not say what it supplies.

    A function's value is its RETURN, so a call of one IS that return —
    which is why a lambda reached by a fold reads the same as one written
    at the call site. A call this walk cannot account for is UNDETERMINED,
    and the value it carries is the BODY a refusal reads the mention
    property over.
    """
    filled = _fills(func, call, bound, scopes)
    if filled is UNREAD:
        return func.body, False
    if not filled:
        return UNREACHABLE, True
    return static_value(func.body, bound, scopes)


def _boolop_value(node, bound, scopes):
    """The value an `and`/`or` produces: the first operand whose truth
    short-circuits it, else the last operand's value. `wanted` is the truth
    that takes the branch — truthy for an `or`, falsy for an `and`.
    """
    wanted = isinstance(node.op, ast.Or)
    for value in node.values[:-1]:
        produced, decided = static_value(value, bound, scopes)
        if produced is UNREACHABLE:
            return UNREACHABLE, True
        truth = None
        if decided and not isinstance(produced, ast.AST):
            truth = bool(produced)
        elif decided and (_is_the_operation(produced, bound, scopes)
                          or isinstance(produced, ast.Lambda)):
            truth = True
        if truth is None:
            return node, False
        if truth == wanted:
            return produced, True
    return static_value(node.values[-1], bound, scopes)


def _decided_if_value(node, bound, scopes):
    """The value a conditional produces when its condition settles: the
    chosen arm's value. A raising condition raises the whole expression;
    one this walk cannot settle leaves the choice a runtime value.
    """
    test = _runtime_settled(node.test, bound, scopes)
    if test is UNREACHABLE:
        return UNREACHABLE, True
    if test is UNREAD or not _computable(test):
        return node, False
    return static_value(node.body if test else node.orelse, bound, scopes)


def static_value(node, bound, scopes):
    """The value an expression produces, and whether this walk decided it.

    Every arm below is a rule about WHAT AN EXPRESSION PRODUCES and never
    about which spelling arrived. Adding a form is adding it here.
    """
    # A WRAPPER produces the value it wraps: `X.__call__` is how Python
    # says "X is callable", `getattr(X, '__call__')` is its second
    # spelling, and calling the projection calls X — which is why the rule
    # is about the value and not a list of attribute names. A projection
    # of something that cannot be called raises on the expression itself.
    wrapped = _projected(node)
    if wrapped is not None:
        value, decided = static_value(wrapped, bound, scopes)
        return ((UNREACHABLE, True) if decided and not _is_callable(value)
                else (value, decided))
    # A form the runtime has already settled. Read as a position or a key
    # it is the value itself, and a settlement that RAISES is a value
    # nothing can be.
    settled = _runtime_settled(node, bound, scopes)
    if settled is not UNREAD:
        return ((UNREACHABLE, True) if settled is UNREACHABLE
                else (settled, True))
    if isinstance(node, ast.BoolOp):
        return _boolop_value(node, bound, scopes)
    if isinstance(node, ast.IfExp):
        return _decided_if_value(node, bound, scopes)
    if isinstance(node, ast.Call):
        # A call's value is what its callee RETURNS (the call-result
        # limit); a lambda's return is the exception, a rule about a VALUE:
        # the callee is folded, and a lambda the signature accepts produces
        # its body however the fold reached it.
        callee, decided = static_value(node.func, bound, scopes)
        if decided and isinstance(callee, ast.Lambda):
            return _called(callee, node, bound, scopes)
        return node, True
    if not isinstance(node, ast.Subscript):
        return node, True
    base, decided = static_value(node.value, bound, scopes)
    if not decided:
        return node, False
    if not isinstance(base, ast.AST) or isinstance(base, UNSUBSCRIPTED) \
            or _is_the_operation(base, bound, scopes):
        return UNREACHABLE, True
    if isinstance(node.slice, ast.Slice):
        # A slice produces a NEW container — or raises — and neither is
        # callable, so the bounds do not matter.
        return UNREACHABLE, True
    if isinstance(base, ast.Dict):
        return _keyed(node, base, bound, scopes)
    if not isinstance(base, (ast.Tuple, ast.List)):
        return node, False
    elements = _expanded_elts(base)
    if elements is None:
        return node, False
    position = _settled_position(node.slice, bound, scopes)
    if position is UNREAD:
        return node, False
    if not isinstance(position, int) \
            or not -len(elements) <= position < len(elements):
        return UNREACHABLE, True
    return static_value(elements[position], bound, scopes)


def _is_callable(value):
    """Whether a value can be CALLED at all. A value the walk computed from
    a literal is a CONSTANT and no constant is callable; a container is not
    callable either, and a value the runtime provably cannot reach through
    is not a thing at all. All three raise on the EXPRESSION, before a call
    reaches anything.
    """
    return isinstance(value, ast.AST) and not isinstance(value, CONTAINERS)


def callee_value(call, bound, scopes):
    """A call's callee value: folded where the fold decides, else whole."""
    value, decided = static_value(call.func, bound, scopes)
    return call.func if not _is_callable(value) or not decided else value


def unresolvable_callee(call, bound, scopes):
    """The callee a mention refusal reads, or None when there is none.

    Two decided values are none of the refusal's business — the call
    raises before it reaches anything: a container is not callable, and a
    value the runtime cannot reach through names nothing. A container or a
    position the fold does not decide is the same class one level out.
    """
    value, decided = static_value(call.func, bound, scopes)
    if not _is_callable(value):
        return None
    return value if decided else call.func


def is_dynamic_import(func, bound, scopes):
    """A call to import_module or __import__, per the module's own bindings.

    An attribute is the operation exactly when its own name is the
    operation's AND its base mentions the operation, read through the same
    property the store side uses. Loading a module by PATH
    (`spec_from_file_location`, `SourceFileLoader`) is a different
    operation.
    """
    if isinstance(func, ast.Name):
        return bound.get(func.id) == 'by name'
    if isinstance(func, ast.Attribute):
        if func.attr not in DYNAMIC_ATTRIBUTES:
            return False
        return yields_the_operation(func.value, bound, scopes)
    return False


def yields_the_operation(value, bound, scopes, delivered=False):
    """True when an expression's own subtree mentions the import-by-name
    operation, so a store of it can hand the operation to a name this map
    cannot follow.

    The property, not a list: a tracked name anywhere inside an expression
    is a mention, and every type nobody has thought of is read the same
    way, by its own children. A registry name is the one tracked name that
    is not a mention. Two early returns do NOT answer by their own
    children: an attribute is the operation exactly when its own name is
    the operation's AND its base mentions the operation, and a CALL is the
    property's single limit — a call evaluates to whatever its callee
    returns, so a name bound to a call's result is followed by neither
    this map nor these refusals.

    A LAMBDA turns on who is asking: read in place it is a function, not
    its body; DELIVERED to a name, calling the stored name delivers, so
    the body is read after all. And a value the fold READ is asked about
    in place of the spelling that carried it, so the two halves of this
    module cannot disagree.
    """
    wrapped = _projected(value)
    if wrapped is not None:
        return yields_the_operation(wrapped, bound, scopes, delivered)
    produced, _decided = static_value(value, bound, scopes)
    if isinstance(produced, ast.AST) and produced is not value:
        return yields_the_operation(produced, bound, scopes, delivered)
    if isinstance(value, ast.Name):
        tracked = bound.get(value.id)
        return tracked is not None and tracked not in REGISTRY_NAMES
    if isinstance(value, ast.Attribute):
        return is_dynamic_import(value, bound, scopes)
    if isinstance(value, ast.Call):
        # A `getattr` whose KEY this walk cannot read may be reading
        # `__call__`, so the lookup produces the value it reads off and is
        # asked about that. A readable key is an ordinary attribute, and
        # `_projection` has already read the one that is a projection.
        if _is_getattr(value) and not isinstance(value.args[1], ast.Constant):
            return yields_the_operation(value.args[0], bound, scopes,
                                        delivered)
        return False
    if isinstance(value, ast.Lambda) and not delivered:
        return False
    return any(yields_the_operation(child, bound, scopes, delivered)
               for child in ast.iter_child_nodes(value))
