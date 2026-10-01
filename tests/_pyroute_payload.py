"""The payload model's own pass over one scope, with its key bindings.

`dict_assignments` reads a scope without the flow, so it has to carry the
name-to-literal table itself — a key position is resolved by the literal
its name was last bound to, and a reader that resolved payload keys
without that table would report the same key as the flow does and for a
different reason. It lives beside the store that maintains the table for
exactly that reason, and out of the flow module, which was a line from
its ceiling.

`_bind_literals` writes that table for both models, so they cannot disagree
about the same program.
"""
import ast

from _pyroute_keys import _UNSAFE_LITERAL, _literal_value
from _pyroute_state import apply_dict_statement, scope_nodes

_STORES = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.Delete,
           ast.For, ast.AsyncFor)


def _bind_literals(node, literals):
    """Record the literal each name this store binds is bound to.

    Every binding form the language has, not the ones a first writer
    enumerated: a form left out resolves no key, so a payload whose key
    rides a name that form binds reaches the sender carrying a real `tab`.
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

    def unpacked(target, value):
        """What each name an unpack target binds is bound to.

        A `*` part takes the elements no other part takes, and the list it
        becomes names no key. Parts that do not line up with their value
        raise before any name is bound, so they bind nothing at all. A `*`
        part may itself be a subscript (`*a[0], b = x`), which binds no name.
        """
        if isinstance(target, ast.Starred):
            if isinstance(target.value, ast.Name):
                yield (target.value.id, _UNSAFE_LITERAL)
            return
        if isinstance(target, ast.Name):
            yield (target.id, value[0] if isinstance(value, (tuple, list))
                   and len(value) == 1 else value)
            return
        if not isinstance(target, (ast.Tuple, ast.List)) \
                or not isinstance(value, (tuple, list)):
            return
        stars = [index for index, part in enumerate(target.elts)
                 if isinstance(part, ast.Starred)]
        if not stars:
            parts = (list(zip(target.elts, value))
                     if len(target.elts) == len(value) else [])
        elif len(stars) != 1 or len(value) < len(target.elts) - 1:
            parts = []
        else:
            star = stars[0]
            end = len(value) - (len(target.elts) - star - 1)
            parts = [*zip(target.elts[:star], value[:star]),
                     (target.elts[star], value[star:end]),
                     *zip(target.elts[star + 1:], value[end:])]
        for part, element in parts:
            yield from unpacked(part, element)

    def bind_targets(targets, value):
        for target in targets:
            if isinstance(target, ast.Name):
                record(target.id, value)
                continue
            for name, literal in unpacked(target, value):
                record(name, literal)

    def bind_loop_target(loop):
        """A loop target takes every element of what it iterates in turn,
        so a name it binds carries the one literal they all agree on. A
        union of several that do not is a key no position can name, and an
        iterable the fold will not produce names nothing at all."""
        value = value_of(loop.iter)
        if isinstance(value, (tuple, list, set)) and value:
            first = next(iter(value))
            elements = (first,) if all(i == first for i in value) else ()
        else:
            elements = ()
        bound = dict(unpacked(loop.target, elements)) if elements else {}
        for name in target_names(loop.target):
            record(name, bound.get(name, _UNSAFE_LITERAL))

    def own_nodes(statement):
        """A statement's own nodes, stopping at a nested scope.

        A comprehension is walked: a walrus in either of its clauses binds
        in the scope the comprehension stands in, on every supported
        version, and the iteration variable it keeps to itself is a
        SyntaxError as a walrus target — so nothing inside one binds
        anywhere this walk does not already write.
        """
        yield statement
        for child in ast.iter_child_nodes(statement):
            if not isinstance(child, (
                    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                    ast.Lambda)):
                yield from own_nodes(child)

    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        bind_targets(node.targets if isinstance(node, ast.Assign)
                     else [node.target], value_of(node.value))
    elif isinstance(node, ast.Delete):
        for target in node.targets:
            for name in target_names(target):
                literals.pop(name, None)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        bind_loop_target(node)
    for child in own_nodes(node):
        if (isinstance(child, ast.NamedExpr)
                and isinstance(child.target, ast.Name)):
            record(child.target.id, _literal_value(child.value))


def dict_assignments(scope):
    """Map local names to string keys, retaining provable mutations."""
    dicts = {}
    literals = {}
    nodes = [node for node in scope_nodes(scope) if isinstance(node, _STORES)]
    for node in sorted(nodes, key=lambda item: (item.lineno, item.col_offset)):
        apply_dict_statement(node, dicts, literals)
        _bind_literals(node, literals)
    return dicts
