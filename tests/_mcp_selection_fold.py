"""A call's callee read as a VALUE: what it produces, and what it names.

The store side's own property is asked of the callee's value, so the value
has to be read rather than the spelling that carries it, and both halves
live here for the same reason — the two questions are asked of one node and
must not disagree about it. What a value NAMES is the mention property; what
it PRODUCES is the fold.

The mention property is the property, not a list of the shapes that have
been met: a tracked name anywhere inside an expression is a mention, and
every type nobody has thought of is read the same way, by its own children.
Its two early returns do NOT answer by their own children, and each is
accounted for — an attribute, which is the operation exactly when its own
name is the operation's AND its base mentions the operation, and a call,
which evaluates to whatever its callee returns rather than to the callee.
A LAMBDA turns on WHO is asking, not on the node: read in place it is a
function, not its body, and delivered to a name it is its body, so it is the
`delivered` argument that tells the two call sites apart.

The fold reads a subscript of a literal tuple or list as a POSITION, and a
position the runtime reaches is decidable however it is spelled: written
out, negated, or computed. The steps are what make that claim safe rather
than optimistic:

- a POSITION is decoded, not type-tested. A negative literal is a `UnaryOp`
  around its constant and a computed one a `BinOp`, so a fold that only
  accepts a plain `Constant` never sees either, and the sign test it does
  write can reject nothing. A constant that is not an INTEGER position is
  declined — a float, because `lst[0.0]` raises `TypeError` and so names
  nothing, a string, `None` — while a bool is READ, because a bool IS an
  int and `lst[True] is lst[1]`.
- a CONTAINER is expanded, not trusted. A `Starred` element unpacks its
  value into the positions around it, so a star over a literal TUPLE or LIST
  is exactly as readable as that sequence, at whatever depth the stars
  nest; a star over anything else — a name, a set whose order is
  unspecified, a comprehension whose length is a runtime value — puts
  positions out of reach and is undetermined.
- the selected ELEMENT is folded again, so a container whose element is
  itself a selection reads through it and each step terminates.

Anything the steps decline is UNDETERMINED, which is not silence: the caller
reads the mention property over the whole expression instead. A value the
runtime cannot CALL is the other thing that is not silence, and
`unresolvable_callee` is what tells the two apart.
"""
import ast

# The container literals a folded value can BE. A call on one raises before
# it reaches anything, so a callee the fold DECIDED to be one is clean
# however much of the operation its container carries.
CONTAINERS = (ast.List, ast.Tuple, ast.Set, ast.Dict,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


# The two names the operation map tracks. `DYNAMIC_ATTRIBUTES` are the
# operation's own spellings; `REGISTRY_NAMES` are the one tracked name
# that is NOT a mention, so it is named here beside the property that
# excludes it rather than beside the registry that reads it.
DYNAMIC_ATTRIBUTES = ('import_module', '__import__')
REGISTRY_NAMES = ('sys', 'registry')


def _position(index):
    """The constant integer position an index expression names, or None.

    `Add` and `Sub` are folded because a value the runtime has already
    settled is settled here too; the wider arithmetic is not, because `//`,
    `%` and `**` are not positions this walk computes, and `/` can leave a
    float, which is not a position at all.
    """
    if isinstance(index, ast.Constant) and isinstance(index.value, int):
        return index.value
    if isinstance(index, ast.UnaryOp):
        if isinstance(index.op, ast.UAdd):
            return _position(index.operand)
        if isinstance(index.op, ast.USub):
            folded = _position(index.operand)
            return None if folded is None else -folded
        return None
    if isinstance(index, ast.BinOp):
        left, right = _position(index.left), _position(index.right)
        if left is None or right is None:
            return None
        if isinstance(index.op, ast.Add):
            return left + right
        if isinstance(index.op, ast.Sub):
            return left - right
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


def selected_value(node):
    """The value an expression produces, and whether the fold decided it.

    A subscript of a literal tuple or list by a constant position inside it
    selects exactly that element, so the fold takes it. A base that is
    itself a subscript is folded FIRST, and the selected element is folded
    again after it, so a container whose element is a selection reads
    through it and the depth is unbounded with every step terminating.

    A `Dict` or `Set` is keyed rather than positioned, a slice is not an
    element, and a name or an out-of-range position is one this walk cannot
    read. All of them are UNDETERMINED, never silence.
    """
    if not isinstance(node, ast.Subscript):
        return node, True
    base, decided = selected_value(node.value)
    if not decided or not isinstance(base, (ast.Tuple, ast.List)):
        return node, False
    elements = _elements(base)
    position = _position(node.slice)
    if elements is None or position is None \
            or not -len(elements) <= position < len(elements):
        return node, False
    return selected_value(elements[position])


def callee_value(call):
    """A call's callee value: folded where the fold decides, else whole."""
    value, decided = selected_value(call.func)
    return value if decided else call.func


def unresolvable_callee(call):
    """The callee a mention refusal reads, or None when there is none.

    A container is not callable, so a value the fold DECIDED to be one makes
    the call raise `TypeError` before it reaches anything: `[op]('x')` calls
    a list, and whatever that list mentions is beside the question. A
    container the fold does not decide is the same class one level out, and
    is still a value the caller may refuse.
    """
    value, decided = selected_value(call.func)
    if decided and isinstance(value, CONTAINERS):
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
    so `(lambda: op)()` reaches nothing — the body is returned, not called.
    DELIVERED to a name it is the opposite: calling the stored name is what
    delivers, so the body is read after all. Keying this on the node rather
    than on the call site's spelling is what makes a lambda reached by a
    fold read the same as one written there.
    """
    if isinstance(value, ast.Name):
        tracked = bound.get(value.id)
        return tracked is not None and tracked not in REGISTRY_NAMES
    if isinstance(value, ast.Attribute):
        return is_dynamic_import(value, bound)
    if isinstance(value, ast.Call):
        return False
    if isinstance(value, ast.Lambda) and not delivered:
        return False
    return any(yields_the_operation(child, bound, delivered)
               for child in ast.iter_child_nodes(value))
