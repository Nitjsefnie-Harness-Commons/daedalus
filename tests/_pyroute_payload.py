"""The payload model's own pass over one scope, with its key bindings.

`dict_assignments` reads a scope without the flow, so it has to carry the
name-to-literal table itself — a key position is resolved by the literal
its name was last bound to, and a reader that resolved payload keys
without that table would report the same key as the flow does and for a
different reason. It lives beside the store that maintains the table for
exactly that reason, and out of the flow module, which was a line from
its ceiling.

`_bind_literals` writes that table for both models, so the two read one
program the same way. A name a form outside `_STORES` binds resolves to no
key, and so does one a form it models without proving it.
"""
import ast
from collections.abc import Hashable

from _pyroute_invalidation import CONTAINER_MUTATORS
from _pyroute_keys import _UNSAFE_LITERAL, _literal_value
from _pyroute_state import apply_dict_statement, scope_nodes

_STORES = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.Delete,
           ast.For, ast.AsyncFor)


def _bind_literals(node, literals):
    """Record the literal each name this store binds is bound to.

    A form left out resolves no key, so a payload whose key rides a name that
    form binds reaches the sender carrying a real `tab`.
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

    def proven_store(part):
        """Whether a store part provably succeeds, so the walk may pass it.

        Proof is `name[index]`, and what each kind of base makes of it has a
        row: `unpack-subscript-int-base` and `unpack-subscript-empty-tuple`
        for a base that is neither a list nor a dict,
        `unpack-subscript-out-of-range` and `unpack-subscript-empty-list` for
        an index a list refuses, `unpack-subscript-float-index` for one that
        is not an integer, `unpack-subscript-bool-index` for one that is a
        bool, and `unpack-attribute-part` for anything but a subscript. A
        dict takes any hashable key, which `unpack-subscript-dict-base`
        reports on; a base absent here is unproven rather than unbound,
        because a name bound to anything else never reaches this table.

        An unproven part ends the walk, which costs a key the program may
        still spell: a fail-open, and what this guard already answers for a
        key position it cannot pin. Walking past one instead reports a key on
        a program that raises before the sender sees it.
        """
        if not isinstance(part, ast.Subscript):
            return False
        base, index = part.value, part.slice
        if not isinstance(base, ast.Name) \
                or not isinstance(index, ast.Constant):
            return False
        held = literals.get(base.id)
        if isinstance(held, dict):
            # A dict takes any hashable key, present or not.
            return isinstance(index.value, Hashable)
        if not isinstance(index.value, int) or isinstance(index.value, bool):
            return False
        return isinstance(held, list) and 0 <= index.value < len(held)

    def unpacked(target, value):
        """What each name an unpack target binds is bound to, or None.

        None is a target holding a store part the model cannot prove, and the
        whole destructuring binds nothing: the statement raises on that store
        before it binds any of its names, so a name a part before it took was
        never bound either. That is what `store-part-after-name` and
        `store-part-attr-after-name` hold clean.

        Each part takes one element, in the order the parts stand, so a part
        that is not a name takes none: a `*` part becomes a list, which no key
        position names (`unpack-star-binds-list`), and a store part is
        evaluated where it stands. Only `proven_store` carries the walk past
        one, and the two subscript rows that report,
        `unpack-subscript-base-bound` and `unpack-subscript-midway-bound`, are
        the ones whose store it carries.
        """
        if isinstance(target, ast.Name):
            return [(target.id, value)]
        if not isinstance(target, (ast.Tuple, ast.List)) \
                or not isinstance(value, (tuple, list)):
            return []
        bound = []
        for part, element in parts_of(target, value):
            if isinstance(part, ast.Name):
                bound.append((part.id, element))
            elif isinstance(part, (ast.Tuple, ast.List)):
                nested = unpacked(part, element)
                if nested is None:
                    return None
                bound.extend(nested)
            elif isinstance(part, ast.Starred):
                if isinstance(part.value, ast.Name):
                    bound.append((part.value.id, _UNSAFE_LITERAL))
                elif not proven_store(part.value):
                    return None
            elif not proven_store(part):
                return None
        return bound

    def drop_mutated_base(node):
        """Forget the name a statement mutates a subscript of.

        A folded list is a belief about a length, and `del a[0]`, `a.pop()`,
        `a.clear()` and a slice assignment each change it. The table cannot
        see the new length, so it forgets the name rather than vouch for an
        index against a list that is no longer there — which is what the six
        `mutate-` rows hold clean, and what `loop-target-length-unchanged`
        shows the other side of.
        """
        for target in (node.targets if isinstance(node, (ast.Assign,
                                                         ast.Delete))
                       else [node.target] if isinstance(node, (ast.AnnAssign,
                                                               ast.AugAssign))
                       else []):
            if isinstance(target, ast.Subscript) and isinstance(
                    target.value, ast.Name):
                literals.pop(target.value.id, None)
        if isinstance(node, ast.Expr):
            call = (node.value.value if isinstance(node.value, ast.NamedExpr)
                    else node.value)
            callee = call.func if isinstance(call, ast.Call) else None
            if (isinstance(callee, ast.Attribute) and callee.attr
                    in CONTAINER_MUTATORS
                    and isinstance(callee.value, ast.Name)):
                literals.pop(callee.value.id, None)

    def bind_targets(targets, value):
        for target in targets:
            if isinstance(target, ast.Name):
                record(target.id, value)
                continue
            bound = unpacked(target, value)
            for name, literal in bound or ():
                record(name, literal)

    def bind_loop_target(loop):
        """Bind the names a loop target binds, and nothing else.

        The loop runs once per element, so a bare name carries the one literal
        they all agree on (`loop-union-agrees`) and a union of several that
        do not names no key, which `loop-union` holds clean. An unpacked
        target pairs BY POSITION against each element, the destructuring an
        assignment does, so `loop-target-two-part` and `loop-target-star-part`
        report; `loop-target-arity` shows the other side, where the parts
        do not line up and the raise leaves nothing bound.
        """
        target = loop.target
        if isinstance(loop, ast.AsyncFor):
            # Nothing this table folds has an `__aiter__`, so no target of an
            # `async for` over a foldable iterable is provably bound and an
            # unprovable one must not be reported — `async-literal-iter`.
            for name in target_names(target):
                record(name, _UNSAFE_LITERAL)
            return
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
        rounds = [dict(unpacked(target, item) or {}) for item in value]
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
        this walk does write (`comp-list-ifs` and `comp-list-element`), and
        the iteration variable lives inside the comprehension, which is one it
        does not, which is why no row reports a comprehension's own target.
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
        agree on forgetting it: the `augmented` row of the two-writers table
        holds both at nothing. That costs a report `k += ""` is owed.
        """
        for name in target_names(node.target):
            record(name, _UNSAFE_LITERAL)

    def bind_walrus(nodes):
        for child in nodes:
            if (isinstance(child, ast.NamedExpr)
                    and isinstance(child.target, ast.Name)):
                record(child.target.id, _literal_value(child.value))

    drop_mutated_base(node)
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
