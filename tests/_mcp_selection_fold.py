"""A call's callee read as a VALUE: what it produces, and what it names.

The store side's own property is asked of the callee's value, so the value
has to be read rather than the spelling that carries it, and both halves
live here for the same reason — the two questions are asked of one node and
must not disagree about it. What a value NAMES is the mention property; what
it PRODUCES is the fold.

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

The fold reads what an expression PRODUCES, and it reads it three ways.

- A WRAPPER produces the value it wraps. `X.__call__` is how Python spells
  "X is callable", `getattr(X, '__call__')` is the second spelling of the
  same projection, and a LAMBDA called with no arguments produces its own
  body. Calling any of them calls what it wraps, so a value read through
  one IS the value it wraps — which is why the rule is about the value and
  not a list of attribute names: `op.__call__` is the operation and
  `(0, op)[0].__call__` is the `0`.
- A subscript of a literal SEQUENCE is a POSITION and one of a literal
  MAPPING is a KEY, and either is decidable however it is spelled: written
  out, negated, computed, or reached through a star. The steps are what
  make that claim safe rather than optimistic. A POSITION is a VALUE and
  not a spelling, so a constant, a negation, an arithmetic one, a walrus
  and `bool` of a decided one are all settled by asking Python's own
  operator for the result; asking it rather than re-reading the operator
  leaves no one operator to forget. A CONTAINER is expanded rather than
  trusted, so a star over a literal TUPLE or LIST reads as that sequence at
  whatever depth the stars nest, and a star over anything else — a name, a
  set whose order is unspecified, a comprehension whose length is a runtime
  value — puts positions out of reach. The selected ELEMENT is folded
  again, so a container whose element is itself a selection reads through
  it and each step terminates.
- A value the runtime provably CANNOT reach through is CLEAN, and that is a
  decision like any other. `(None, True)` is what says so: an out-of-range
  position, a key the literal does not carry, a `set` no subscript reaches
  at all, a constant, the operation itself, and a chain that runs one step
  past it. The call raises on the EXPRESSION, so the mention the container
  carries is beside the question and a refusal there is a false one.

Anything the steps cannot decide — a free name, a slice, a comprehension's
length, a value reached through a call — is UNDETERMINED, which is not
silence: the caller reads the mention property over the whole expression
instead. `unresolvable_callee` is what tells the two apart.
"""
import ast
import operator

# The container literals a folded value can BE. A call on one raises before
# it reaches anything, so a callee the fold DECIDED to be one is clean
# however much of the operation its container carries.
CONTAINERS = (ast.List, ast.Tuple, ast.Set, ast.Dict,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

# The containers no subscript can reach at all, whatever key or position it
# names: a set is not a sequence and a generator is not either, so `s[0]`,
# `s['a']` and `s[i]` are one `TypeError` between them. A DICT comprehension
# is not here — it is a mapping, and a key reaches into it — nor is a LIST
# comprehension, whose length is a runtime value and whose position is
# genuinely unknown.
UNINDEXED = (ast.Set, ast.SetComp, ast.GeneratorExp)


# The two names the operation map tracks. `DYNAMIC_ATTRIBUTES` are the
# operation's own spellings; `REGISTRY_NAMES` are the one tracked name
# that is NOT a mention, so it is named here beside the property that
# excludes it rather than beside the registry that reads it.
DYNAMIC_ATTRIBUTES = ('import_module', '__import__')
REGISTRY_NAMES = ('sys', 'registry')


# The arithmetic an index expression can be settled by, asked of Python's
# own operators rather than of a table this walk keeps beside itself: a
# value the runtime has already settled is settled here too, and a table
# is one more spelling to leave out. `/` is here too, and a float it
# leaves is not a position — which is the value being settled, not the
# spelling being declined.
_ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub,
               ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv,
               ast.Mod: operator.mod, ast.Pow: operator.pow,
               ast.Div: operator.truediv}
_NUMERIC = (int, float, complex)


def _index_value(node):
    """The value an index expression produces, and whether it is decided.

    A constant, a negation, an arithmetic one, a walrus and `bool` of a
    decided one are all values the runtime has already settled, so each is
    settled here. An operator whose operands are not numbers is a spelling
    this walk does not read, and one that raises — a division by zero — is
    a value the walk knows names nothing.
    """
    if isinstance(node, ast.Constant):
        return node.value, True
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.UAdd):
            return _index_value(node.operand)
        if isinstance(node.op, ast.USub):
            value, decided = _index_value(node.operand)
            if not decided:
                return None, False
            return (-value, True) if isinstance(value, _NUMERIC) \
                else (None, True)
        return None, False
    if isinstance(node, ast.BinOp):
        arithmetic = _ARITHMETIC.get(type(node.op))
        left, right = _index_value(node.left), _index_value(node.right)
        if arithmetic is None or not (left[1] and right[1]) \
                or not all(isinstance(side[0], _NUMERIC)
                           for side in (left, right)):
            return None, False
        try:
            return arithmetic(left[0], right[0]), True
        except (ArithmeticError, TypeError, ValueError):
            return None, True
    if isinstance(node, ast.NamedExpr):
        return _index_value(node.value)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == 'bool' and len(node.args) == 1 \
            and not node.keywords:
        value, decided = _index_value(node.args[0])
        return (int(bool(value)), True) if decided else (None, False)
    return None, False


def _is_nullary_callable(func):
    """Whether a lambda can be called with NO arguments, so such a call
    produces its own body. A REQUIRED positional or keyword-only parameter
    (no default) blocks it; a vararg, a kwarg and a defaulted parameter do
    not. The code-eval axis reads the same predicate for its own lambda
    route; it is written out here because that axis is frozen and this one
    is not."""
    if not isinstance(func, ast.Lambda):
        return False
    args = func.args
    positional = args.posonlyargs + args.args
    if len(positional) > len(args.defaults):
        return False
    return not any(default is None for default in args.kw_defaults)


def _is_getattr(node):
    """Whether this call is a `getattr` lookup of at least two arguments."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'getattr' and len(node.args) >= 2)


def _getattr_value(node):
    """The value a `getattr` call produces, or None when it is an ordinary
    lookup of an ordinary attribute.

    `getattr(V, '__call__')` is Python's second spelling of the `__call__`
    projection, and it projects V whatever V is, so the fold takes it. A
    different constant does not: `getattr(op, 'other')` raises
    `AttributeError` and names nothing. A key this walk cannot read is
    neither — it may be `__call__` and it may be anything else — so the
    fold declines it and the MENTION property reads it instead.
    """
    if not _is_getattr(node):
        return None
    key = node.args[1]
    if not isinstance(key, ast.Constant) or key.value != '__call__':
        return None
    return node.args[0]


def _wrapper(node):
    """The value an expression produces through one transparent wrapper.

    Three spellings produce a value rather than naming one: `X.__call__`,
    which is how Python says "X is callable", the same projection written as
    `getattr(X, ...)`, and a LAMBDA called with no arguments, which produces
    its own body. Calling any of them calls the value it wraps, so a value
    read through one IS the value it wraps — which is why the rule is about
    the value and not a list of attribute names: `op.__call__` is the
    operation and `(0, op)[0].__call__` is the `0`.
    """
    if isinstance(node, ast.Attribute) and node.attr == '__call__':
        return node.value
    projected = _getattr_value(node)
    if projected is not None:
        return projected
    if isinstance(node, ast.Call) and not node.args and not node.keywords \
            and _is_nullary_callable(node.func):
        return node.func.body
    return None


def _elements(base):
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
        inner = _elements(element.value)
        if inner is None:
            return None
        elements.extend(inner)
    return elements


def _keyed_value(node, base, bound):
    """The value a dict literal's key selects, or `(None, True)` for a key
    the literal does not carry.

    A `Dict` is a literal container read by its KEY rather than by a
    position, so the key is read by the same reader that reads a position:
    a settled key that names an entry the literal carries selects that
    value, and a settled one that does not is the `KeyError` the runtime
    raises before a call reaches anything. A key this walk cannot read, and
    a `**` unpack that puts keys out of reach, are UNDETERMINED.
    """
    if any(not isinstance(key, ast.Constant) for key in base.keys):
        return node, False
    key, is_read = _index_value(node.slice)
    if not is_read:
        return node, False
    for literal, value in zip(base.keys, base.values):
        if literal.value == key:
            return selected_value(value, bound)
    return None, True


def _is_the_operation(node, bound):
    """Whether this expression IS the operation, and so a FUNCTION — the one
    decided value that is neither a container nor subscriptable.

    A name the map binds to the operation is it, and the operation's own
    attribute spelling is it. A function has no `__getitem__`, so `op[0]`
    is the same `TypeError` a set's is, and a chain that runs one step past
    the operation raises rather than reading anything.
    """
    if isinstance(node, ast.Name):
        return bound.get(node.id) == 'by name'
    return isinstance(node, ast.Attribute) and is_dynamic_import(node, bound)


def selected_value(node, bound):
    """The value an expression produces, and whether the fold decided it.

    `(None, True)` is a decision too, and the one that keeps a false refusal
    off a position the runtime provably cannot reach: `lst[4]`, `lst[0.0]`,
    `d['absent']` and `op[0]` all raise before a call can reach anything, so
    the call raises on the EXPRESSION rather than on a value read out of it.

    A wrapper is stripped first, so a projection or a nullary lambda call
    produces the value it wraps — but only when that value can be called at
    all, since a container has no `__call__` and the projection of one
    raises on the expression itself. A subscript of a literal tuple or list
    by a constant position inside it selects exactly that element, and a
    subscript of a literal dict by a constant key it carries selects that
    value. A base that is itself a subscript is folded FIRST, and the
    selected element is folded again after it, so a container whose element
    is a selection reads through it and the depth is unbounded with every
    step terminating.

    A `Set` no subscript can reach is a decision like any other, and so is a
    `Constant` and the operation itself: whatever a subscript of one of
    those produces, it is not a callable. A comprehension and a name are
    positions this walk cannot read — as is a key it cannot: those are
    UNDETERMINED, never silence.
    """
    wrapper = _wrapper(node)
    if wrapper is not None:
        inner, decided = selected_value(wrapper, bound)
        return (None, True) if decided and isinstance(inner, CONTAINERS) \
            else (inner, decided)
    if not isinstance(node, ast.Subscript):
        return node, True
    base, decided = selected_value(node.value, bound)
    if not decided:
        return node, False
    if base is None or isinstance(base, UNINDEXED) \
            or isinstance(base, ast.Constant) \
            or _is_the_operation(base, bound):
        return None, True
    if isinstance(base, ast.Dict):
        return _keyed_value(node, base, bound)
    if not isinstance(base, (ast.Tuple, ast.List)):
        return node, False
    elements = _elements(base)
    if elements is None:
        return node, False
    position, is_read = _index_value(node.slice)
    if not is_read:
        return node, False
    if not isinstance(position, int) \
            or not -len(elements) <= position < len(elements):
        return None, True
    return selected_value(elements[position], bound)


def callee_value(call, bound):
    """A call's callee value: folded where the fold decides, else whole."""
    value, decided = selected_value(call.func, bound)
    return call.func if value is None or not decided else value


def unresolvable_callee(call, bound):
    """The callee a mention refusal reads, or None when there is none.

    Two decided values are none of the refusal's business, and both are
    decided for the same reason — the call raises before it reaches
    anything. A container is not callable, so `[op]('x')` calls a list; and
    a subscript the fold read to a position the runtime cannot reach names
    nothing at all, so `[op][4]('x')` raises `IndexError` on the expression
    itself. A container or a position the fold does not decide is the same
    class one level out, and is still a value the caller may refuse.
    """
    value, decided = selected_value(call.func, bound)
    if value is None or (decided and isinstance(value, CONTAINERS)):
        return None
    return value if decided else call.func


def is_dynamic_import(func, bound):
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
        return yields_the_operation(func.value, bound)
    return False


def yields_the_operation(value, bound, delivered=False):
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

    A WRAPPER is the other value-shaped question, and it is asked before
    any of these: an expression that PRODUCES a value is a mention when the
    value it produces is, so `op.__call__`, `getattr(op, '__call__')` and
    `(lambda: op)()` are read as the operation and `(0, op)[0].__call__` as
    the `0`.
    """
    wrapper = _wrapper(value)
    if wrapper is not None:
        return yields_the_operation(wrapper, bound, delivered)
    if isinstance(value, ast.Name):
        tracked = bound.get(value.id)
        return tracked is not None and tracked not in REGISTRY_NAMES
    if isinstance(value, ast.Attribute):
        return is_dynamic_import(value, bound)
    if isinstance(value, ast.Call):
        # A `getattr` whose KEY this walk cannot read may be reading
        # `__call__`, so the lookup produces the value it reads off and is
        # asked about that. A readable key is an ordinary attribute, and
        # `_getattr_value` has already read the one that is a projection.
        if _is_getattr(value) and not isinstance(value.args[1], ast.Constant):
            return yields_the_operation(value.args[0], bound, delivered)
        return False
    if isinstance(value, ast.Lambda) and not delivered:
        return False
    return any(yields_the_operation(child, bound, delivered)
               for child in ast.iter_child_nodes(value))
