"""The payload model's own pass over one scope, with its key bindings.

`dict_assignments` reads a scope without the flow, so it has to carry the
name-to-literal table itself — a key position is resolved by the literal
its name was last bound to, and a reader that resolved payload keys
without that table would report the same key as the flow does and for a
different reason. It lives beside the store that maintains the table for
exactly that reason, and out of the flow module, which was a line from
its ceiling.

`_bind_literals` writes that table for both models, so the two read one
program the same way. It models the store forms `_STORES` names; a name a
form outside that set binds resolves to no key.
"""
import ast

from _pyroute_keys import _UNSAFE_LITERAL, _literal_value
from _pyroute_state import apply_dict_statement, scope_nodes

_STORES = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.Delete,
           ast.For, ast.AsyncFor)


def _bind_literals(node, literals):
    """Record the literal each name this store binds is bound to.

    A form left out resolves no key, so a payload whose key rides a name
    that form binds reaches the sender carrying a real `tab` — which is why
    the arms below cover every form `_STORES` names.
    """

    def record(name, literal):
        if literal is _UNSAFE_LITERAL:
            literals.pop(name, None)
        else:
            literals[name] = literal

    def value_of(expr):
        """The fold's answer, or the table's where the fold declines one."""
        if isinstance(expr, ast.NamedExpr):
            return value_of(expr.value)
        value = _literal_value(expr)
        if value is _UNSAFE_LITERAL and isinstance(expr, ast.Name):
            return literals.get(expr.id, _UNSAFE_LITERAL)
        return value

    def target_names(target):
        """The names a store target binds, in the order it binds them."""
        if isinstance(target, ast.Name):
            yield target.id
        elif isinstance(target, (ast.Tuple, ast.List)):
            for part in target.elts:
                yield from target_names(part)
        elif isinstance(target, ast.Starred):
            yield from target_names(target.value)

    def parts_of(target, value):
        """Pair an unpack target's parts with the elements each takes, or [].

        `[]` is a target and a value whose shapes do not line up, which is a
        program that raises before any name is bound.
        """
        stars = [index for index, part in enumerate(target.elts)
                 if isinstance(part, ast.Starred)]
        if not stars:
            return (list(zip(target.elts, value))
                    if len(target.elts) == len(value) else [])
        if len(stars) != 1 or len(value) < len(target.elts) - 1:
            return []
        star = stars[0]
        end = len(value) - (len(target.elts) - star - 1)
        return [*zip(target.elts[:star], value[:star]),
                (target.elts[star], value[star:end]),
                *zip(target.elts[star + 1:], value[end:])]

    def unbound_base(store):
        """Whether a store part's base is a name this table never bound.

        A subscript or an attribute part is evaluated where it stands, so a
        base nothing here has ever bound is what makes it raise before the
        parts after it are bound at all. A base the table HAS bound succeeds,
        and the walk carries on past it.
        """
        return (isinstance(store, (ast.Subscript, ast.Attribute))
                and isinstance(store.value, ast.Name)
                and store.value.id not in literals)

    def unpacked(target, value):
        """What each name an unpack target binds is bound to.

        Each part takes one element, in the order the parts stand, so a part
        that is not a name takes none: a `*` part becomes a list, which no key
        position names, and a subscript part is evaluated where it stands. Only
        such a part whose base was never bound ends the walk — it raises, and
        the parts after it are never bound. Everything before it keeps what it
        took.
        """
        if isinstance(target, ast.Name):
            yield (target.id, value)
            return
        if not isinstance(target, (ast.Tuple, ast.List)) \
                or not isinstance(value, (tuple, list)):
            return
        for part, element in parts_of(target, value):
            if isinstance(part, ast.Name):
                yield (part.id, element)
            elif isinstance(part, (ast.Tuple, ast.List)):
                yield from unpacked(part, element)
            elif isinstance(part, ast.Starred):
                if isinstance(part.value, ast.Name):
                    yield (part.value.id, _UNSAFE_LITERAL)
                elif unbound_base(part.value):
                    return
            elif unbound_base(part):
                return

    def bind_targets(targets, value):
        for target in targets:
            if isinstance(target, ast.Name):
                record(target.id, value)
                continue
            for name, literal in unpacked(target, value):
                record(name, literal)

    def bind_loop_target(loop):
        """Bind the names a loop target binds, and nothing else.

        The loop runs once per element, so a bare name carries the one
        literal they all agree on and a union of several that do not names no
        key. An unpacked target pairs BY POSITION against each element, which
        is the destructuring an assignment does: a name is recorded where
        every iteration agrees and forgotten otherwise.
        """
        target = loop.target
        value = value_of(loop.iter)
        if not isinstance(value, (tuple, list, set)) or not value:
            for name in target_names(target):
                record(name, _UNSAFE_LITERAL)
            return
        if isinstance(target, ast.Name):
            first = next(iter(value))
            record(target.id, first if all(i == first for i in value)
                   else _UNSAFE_LITERAL)
            return
        rounds = [dict(unpacked(target, item)) for item in value]
        for name in target_names(target):
            taken = [round_.get(name, _UNSAFE_LITERAL) for round_ in rounds]
            record(name, taken[0] if all(item == taken[0]
                                         for item in taken)
                   else _UNSAFE_LITERAL)

    def own_nodes(statement):
        """A statement's own nodes, stopping at a nested scope.

        A comprehension is walked, and reading one is safe because a
        comprehension introduces a scope of its own: a walrus in either
        clause binds in the scope the comprehension stands in, which is one
        this walk does write, and the iteration variable lives inside the
        comprehension, which is one it does not.
        """
        yield statement
        for child in ast.iter_child_nodes(statement):
            if not isinstance(child, (
                    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                    ast.Lambda)):
                yield from own_nodes(child)

    def bind_augmented(node):
        """`k += x` rebinds `k` to a value neither side folds to alone.

        The flow clears the name from the table before this runs, so the
        operand's contribution is not visible here either and the two models
        agree on forgetting it — which is a report `k += ""` is owed and does
        not yet get.
        """
        for name in target_names(node.target):
            record(name, _UNSAFE_LITERAL)

    def bind_walrus(nodes):
        for child in nodes:
            if (isinstance(child, ast.NamedExpr)
                    and isinstance(child.target, ast.Name)):
                record(child.target.id, _literal_value(child.value))

    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        bind_targets(node.targets if isinstance(node, ast.Assign)
                     else [node.target], value_of(node.value))
    elif isinstance(node, ast.AugAssign):
        bind_augmented(node)
    elif isinstance(node, ast.Delete):
        for target in node.targets:
            for name in target_names(target):
                literals.pop(name, None)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        # The target and the header only: the body and the `else` arm are
        # walked as the statements they are, in the order they run, so a
        # walrus in either binds when it runs and not before.
        bind_loop_target(node)
        bind_walrus(own_nodes(node.iter))
        return
    bind_walrus(own_nodes(node))


def dict_assignments(scope):
    """Map local names to string keys, retaining provable mutations."""
    dicts = {}
    literals = {}
    nodes = [node for node in scope_nodes(scope) if isinstance(node, _STORES)]
    for node in sorted(nodes, key=lambda item: (item.lineno, item.col_offset)):
        apply_dict_statement(node, dicts, literals)
        _bind_literals(node, literals)
    return dicts
