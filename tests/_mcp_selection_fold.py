"""What a call's callee STATICALLY produces, and whether the fold decided it.

The store side's own property is asked of the callee's VALUE, so the value
has to be read rather than the spelling that carries it. A subscript of a
literal tuple or list names a position, and a position the runtime reaches
is decidable however it is spelled: written out, negated, or computed. The
steps are what make that claim safe rather than optimistic:

- a POSITION is decoded, not type-tested. A negative literal is a `UnaryOp`
  around its constant and a computed one a `BinOp`, so a fold that only
  accepts a plain `Constant` never sees either, and the sign test it does
  write can reject nothing. A float is the one constant left declined, and
  deliberately: a float index raises at runtime, so there is no value for
  the fold to read.
- a CONTAINER is expanded, not trusted. A `Starred` element unpacks its value
  into the positions around it, so a star over a literal sequence is exactly
  as readable as that sequence; a star over anything else puts positions out
  of reach and is undetermined.
- the selected ELEMENT is folded again, so a container whose element is
  itself a selection reads through it and each step terminates.

Anything the steps decline is UNDETERMINED, which is not silence: the caller
reads the mention property over the whole expression instead.
"""
import ast


def _position(index):
    """The constant integer position an index expression names, or None.

    `Add` and `Sub` are folded because a value the runtime has already
    settled is settled here too; the wider arithmetic is not, because a
    division can leave a float and a float is not a position.
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
    """A literal container's elements, a starred literal expanded in place.

    None when a starred element carries a value this walk cannot read, which
    is the one container shape that puts the other positions out of reach.
    """
    elements = []
    for element in base.elts:
        if not isinstance(element, ast.Starred):
            elements.append(element)
            continue
        if not isinstance(element.value, ast.List) or any(
                isinstance(inner, ast.Starred)
                for inner in element.value.elts):
            return None
        elements.extend(element.value.elts)
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
