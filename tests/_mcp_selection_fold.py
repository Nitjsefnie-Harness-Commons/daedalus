"""A call's callee read as a VALUE: what it produces, and what it names.

The store side's own property is asked of the callee's value, so the value
has to be read rather than the spelling that carries it, and both halves
live here for the same reason — the two questions are asked of one node and
must not disagree about it. What a value NAMES is the mention property; what
it PRODUCES is `static_value`, and every other reader in this module is
routed through that one function.

The mention property is the property, not a list of the shapes that have
been met: a tracked name anywhere inside an expression is a mention, and
every type nobody has thought of is read the same way, by its own children.
Its early returns do NOT answer by their own children, and each is
accounted for — an attribute, which is the operation exactly when its own
name is the operation's AND its base mentions the operation; a call, which
evaluates to whatever its callee returns rather than to the callee; and a
`getattr` whose KEY this walk cannot read, which may be reading `__call__`
and so is asked about the object it reads off. A LAMBDA turns on WHO is
asking, not on the node: read in place it is a function, not its body, and
delivered to a name it is its body, so it is the `delivered` argument that
tells the two call sites apart.

`static_value` is the same in kind. It answers ONE question — what value
does this expression produce — and it has three answers, and every reader
here reads one of them:

- an EXPRESSION this walk carries, or a value it computed from a literal.
  A WRAPPER produces the value it wraps, a LITERAL's selection produces the
  element or entry it names, and a form the runtime has already SETTLED
  produces the value Python computes for it. A lambda is a FUNCTION; a
  call's value is whatever that function returns.
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

# The container literals a folded value can BE. A call on one raises before
# it reaches anything, so a callee the fold DECIDED to be one is clean
# however much of the operation its container carries.
CONTAINERS = (ast.List, ast.Tuple, ast.Set, ast.Dict,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

# The decided base values no subscript can reach at all, whatever key or
# position it names: a set is not a sequence and a generator is not either,
# so `s[0]`, `s['a']` and `s[i]` are one `TypeError` between them. A DICT
# comprehension is not here — it is a mapping, and a key reaches into it —
# nor is a LIST comprehension, whose length is a runtime value and whose
# position is genuinely unknown.
UNSUBSCRIPTED = (ast.Set, ast.SetComp, ast.GeneratorExp)

# The value a decided expression produces when the runtime provably cannot
# reach through it. A sentinel rather than `None` so a literal `None` — a
# value a dict key can be and an index cannot — is not read as this.
UNREACHABLE = object()

# A value this walk has NOT read, which is not the same as one it read as
# unreachable: a free name and a slice are both unknown, and a reader that
# conflated them with `UNREACHABLE` would refuse what it cannot decide.
UNREAD = object()

# The two names the operation map tracks. `DYNAMIC_ATTRIBUTES` are the
# operation's own spellings; `REGISTRY_NAMES` are the one tracked name
# that is NOT a mention, so it is named here beside the property that
# excludes it rather than beside the registry that reads it.
DYNAMIC_ATTRIBUTES = ('import_module', '__import__')
REGISTRY_NAMES = ('sys', 'registry')

# The arithmetic a settled value is asked of, from Python's own operators
# rather than from a table this walk keeps beside itself: a value the
# runtime has already settled is settled here too, and a table is one more
# spelling to leave out. `/` is here too, and a float it leaves is not a
# position — which is the value being settled, not the spelling declined.
_ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub,
               ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv,
               ast.Mod: operator.mod, ast.Pow: operator.pow,
               ast.Div: operator.truediv}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

# What this walk will compute WITH, and so ask an operator of. Bounded
# rather than typed, because the question is the COST of the computation
# and not the type of the operand: `operator.mul('a', 0)` settles `''` and
# `operator.pow(2, 10 ** 10)` settles nothing at all. An operand outside
# the bound leaves the expression UNDETERMINED, which is this walk's
# declared limit, and never a wait.
_SETTLED = 1 << 16


def _computable(value):
    """Whether this walk computes with a value rather than carrying it.

    The question is the SIZE of the computation and not the type of the
    operand, so a value with neither a magnitude nor a length — `None`,
    `True`, a nested `...` — is computable however strange it is, and the
    operator's own `TypeError` is what settles it.
    """
    if isinstance(value, (int, float)):
        return -_SETTLED <= value <= _SETTLED
    if isinstance(value, (str, bytes, tuple)):
        return len(value) <= _SETTLED
    return True


def _operator_settled(operation, *operands):
    """What an operator settles, `UNREAD` when this walk will not ask it,
    and `UNREACHABLE` when the runtime's own settlement is to RAISE.

    A raise is a settlement like any other — `lst[1 // 0]` is settled by
    the `ZeroDivisionError` before the container is read — and it is
    `UNREACHABLE` rather than `UNREAD` because the walk knows it.
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
    raises before the value exists.

    SETTLED is a property and not a list: a constant, a unary or binary
    operator over two settled values, a walrus, a conditional whose two
    arms agree, and `bool` of a settled one are each a value Python has
    computed already, and each is asked of Python's own operator. A form
    nobody has met is `UNREAD` here rather than a new arm of this walk's
    own, which is where a new settled form goes.
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
    return UNREAD


def _is_the_bool_call(node, scopes) -> bool:
    """Whether this call is a call of the `bool` BUILTIN over one argument.

    What settles the call is the VALUE it produces, so the question is what
    its callee denotes: a name the module leaves alone is the builtin, a
    `from builtins import bool [as x]` alias is the same builtin, and a name
    bound to something of its own is not it. `bound` is not asked, because
    it tracks the import-by-name operation and carries no builtin to find.
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
    `getattr(X, '__call__')` is its second spelling, so both project X
    whatever X is. A `getattr` with a different constant does not:
    `getattr(op, 'other')` raises `AttributeError` and names nothing. A
    key this walk cannot read is neither — it may be `__call__` and it may
    be anything else — so the fold declines it and the MENTION property
    reads it instead.
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
    them.

    A dict DISPLAY settles its own entries — the last of two equal keys, and
    the equality is Python's — so `**{'x': 1}` supplies exactly the keys it
    carries. Anything else is a runtime value this walk cannot read, and a
    call that carries one supplies an UNKNOWN set of names rather than none:
    `d` empty raises and `d` holding the key reaches, and the walk is not
    entitled to pick one.
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
    """The names a call supplies BY NAME, or None when the call does not say.

    A keyword says its own name, and a `**` mapping says the keys the
    display it reads carries. One of them the walk cannot read leaves the
    whole call undecided rather than half-bound, because a required
    parameter it may or may not fill is the question the call is being
    asked.
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

    A parameter is supplied by POSITION or by NAME, and the two are one
    supply, so the call's arguments are bound to the signature the way
    Python binds them rather than counted against it. The call raises
    wherever Python's own binding raises: a second value for one parameter,
    a name the signature does not have and no `**kwargs` to catch it, or a
    name for a POSITIONAL-ONLY parameter. What the call does not say is what
    a `*args` unpacks to, so that is `UNREAD`, and the caller's own class.
    """
    args = func.args
    params = args.posonlyargs + args.args
    at = {argument.arg: index for index, argument in enumerate(params)}
    only = {argument.arg for argument in args.posonlyargs}
    # Every parameter the signature DECLARES, and separately the ones it
    # REQUIRES: a default says a parameter may go unfilled, not that naming
    # it is an error, so the two sets are not the same and conflating them
    # reads a defaulted keyword-only parameter as a name the signature does
    # not have.
    keyword_only = {argument.arg for argument in args.kwonlyargs}
    wanted = {argument.arg for argument, default
              in zip(args.kwonlyargs, args.kw_defaults) if default is None}
    names = _supplied(call, bound, scopes)
    if names is None or any(isinstance(value, ast.Starred)
                            for value in call.args):
        return UNREAD
    filled = set()
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
            wanted.discard(name)
        elif args.kwarg is None:
            return False
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
    place.

    The expansion RECURSES because a star is a star wherever it sits:
    `(*(*[op],),)` unpacks the one-element tuple the inner star produces,
    and the position an index names is then the inner list's to read.
    Declining a nested star instead put a value the walk already holds out
    of reach. None only when a star carries a value this walk cannot read,
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


def _keyed(node, base, bound, scopes):
    """The value a dict literal's key selects, or `UNREACHABLE` for a key
    the display does not carry.

    A DISPLAY keeps the LAST of two equal keys, because that is what the
    runtime builds, so a reader that stops at the first reads an entry the
    display has already replaced — and reads the operation out of a dict
    that holds something else. The equality is Python's, so `1`, `1.0` and
    `True` name one entry and `0` and `False` another.

    A `**` unpack puts keys out of reach, and a key this walk does not read
    does the same. Either is UNDETERMINED rather than a guess.
    """
    entries = []
    for key, value in zip(base.keys, base.values):
        if key is None:
            return node, False
        if isinstance(key, (ast.List, ast.Set, ast.Dict,
                            ast.DictComp, ast.SetComp)):
            # A key must be HASHABLE and a mutable literal never is, so the
            # display raises `TypeError` before it is built at all — the
            # same decision an index the runtime cannot satisfy is.
            return UNREACHABLE, True
        settled = _settled_position(key, bound, scopes)
        if settled is UNREACHABLE:
            return UNREACHABLE, True
        if settled is UNREAD:
            return node, False
        entries.append((settled, value))
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
    """Whether this expression IS the operation, and so a FUNCTION — the one
    decided value that is neither a container nor subscriptable.

    A name the map binds to the operation is it, and the operation's own
    attribute spelling is it. A function has no `__getitem__`, so `op[0]`
    is the same `TypeError` a set's is, and a chain that runs one step past
    the operation raises rather than reading anything.
    """
    if isinstance(node, ast.Name):
        return bound.get(node.id) == 'by name'
    return isinstance(node, ast.Attribute) and is_dynamic_import(
        node, bound, scopes)


def _called(func, call, bound, scopes):
    """The value a call of a LAMBDA produces: the body its signature
    accepts, `UNREACHABLE` when the call is a raise, and nothing decided
    when the call does not say what it supplies.

    A function's value is its RETURN, so a call of one IS that return and
    a bare lambda is the function itself. That is the whole of the lambda
    rule, and it is why a lambda reached by a fold reads the same as one
    written at the call site: the fold produces the lambda, and the call
    of a produced lambda is the same call.

    A call this walk cannot account for is UNDETERMINED rather than either
    verdict, and the value it carries is the BODY it may produce: a refusal
    reads the mention property over that, which is the direction a question
    the walk cannot answer has to go.
    """
    filled = _fills(func, call, bound, scopes)
    if filled is UNREAD:
        return func.body, False
    if not filled:
        return UNREACHABLE, True
    return static_value(func.body, bound, scopes)


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
    if isinstance(node, ast.Call):
        # A call's value is what its callee RETURNS, and this walk follows
        # no call's result — the call-result limit. A lambda's return is
        # the exception, because that is a rule about a VALUE: the callee
        # is folded, and a lambda the signature accepts produces its body
        # however the fold reached it.
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
        # A slice produces a NEW container — or raises, on a mapping or a
        # function — and neither is callable, so the answer does not depend
        # on the bounds or on what the slice is taken of.
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

    Two decided values are none of the refusal's business, and both are
    decided for the same reason — the call raises before it reaches
    anything. A container is not callable, so `[op]('x')` calls a list; and
    a value the runtime provably cannot reach through names nothing at all,
    so `[op][4]('x')` raises `IndexError` on the expression itself. A
    container or a position the fold does not decide is the same class one
    level out, and is still a value the caller may refuse.
    """
    value, decided = static_value(call.func, bound, scopes)
    if not _is_callable(value):
        return None
    return value if decided else call.func


def is_dynamic_import(func, bound, scopes):
    """A call to import_module or __import__, per the module's own bindings.

    An attribute names the operation by its own name, so one whose attribute
    is not one of the operation's (`importlib.util`) is readable to a
    specific other object and is not the operation; one whose attribute IS
    the operation's is the operation exactly when its base mentions the
    operation, and that base is read through the same property the store
    side uses. Loading a module by PATH —
    `spec_from_file_location`, `SourceFileLoader` — is a different
    operation and stays outside this recognition.
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

    The property, not a list of the shapes that have been met: a tracked
    name anywhere inside an expression is a mention, and every type nobody
    has thought of is read the same way, by its own children. A registry
    name is the one tracked name that is not a mention: the map tracks it
    so a read of it can be refused. Two early
    returns do NOT answer by their own children, and each is accounted for.
    An attribute is one: it is the operation exactly when its own name is
    the operation's AND its base mentions the operation — a test that reads
    the whole base however it is spelled, so `importlib.util` is not the
    operation and `[importlib][0].import_module` is. The OTHER is the
    property's single limit, a call: a call evaluates to whatever its
    callee returns, not to the callee, so a name bound to a call's result
    is followed by neither this map nor these refusals.

    A LAMBDA is the call's statement about a value READ IN PLACE, and it
    turns on who is asking. Read in place it is a function, not its body,
    so `(lambda: op)` reaches nothing — the body is returned, not called.
    DELIVERED to a name it is the opposite: calling the stored name is what
    delivers, so the body is read after all. Keying this on the node rather
    than on the call site's spelling is what makes a lambda reached by a
    fold read the same as one written there.

    A value the fold READ is asked about in place of the spelling that
    carried it — a projection, a selection, a lambda's own return — so the
    two halves of this module are asked of the same value and cannot
    disagree about it.
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
