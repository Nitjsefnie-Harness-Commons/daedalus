"""What each class body binds, and what a receiver reads through it.

The launch audit resolves an attribute receiver by asking what the
enclosing class binds to that attribute's name. This module holds the
whole of that question: the scope every node sits in, the attributes
each class body binds, the classes each scope binds by name, and the
two walks that read a receiver through a class and then through its
bases.

It binds no configuration of its own. It is handed one parsed tree and
answers from it, and the one predicate it cannot answer — does this
bound value derive from `subprocess` — belongs to the caller, which
applies it at the one point the values are finally read.

One consumer: `tests/_launch_audit.py`. It answers with candidate
expressions and never with a verdict, so nothing here knows what
`subprocess` is and a change here moves which receiver the launch audit
resolves, not whether a launch is a launch.
"""
import ast

# The absent table entry, so a base that binds the name to nothing is not
# read as a class that binds it to None.
MISS = object()

SCOPE_NODES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


class ClassIndex:
    """One module's class bodies, bases and scopes.

    The class-body rule is Python's: a class body is a block that
    executes, so a name bound anywhere inside it is a class-namespace
    binding — inside an `if`, a loop, a `with`, a `try`, or nested
    arbitrarily deep inside those. What is NOT a class-namespace binding
    is anything whose own scope is a nested function or a nested class,
    because those bind their own names. That is the nearest-scope test
    in the walk below, and the boundary is Python's rather than a
    convenience: a class body's `if` writes into the class namespace, and
    a method body's assignment does not.
    """

    def __init__(self, tree):
        self.class_scopes = {}
        self.scopes = {}
        self.enclosing_scope = {}
        self.class_attributes = {}
        self.class_bindings = {}
        pending: list[tuple] = [(tree, None, None)]
        while pending:
            node, class_scope, scope = pending.pop()
            for child in ast.iter_child_nodes(node):
                if isinstance(child, SCOPE_NODES):
                    self.scopes[id(child)] = child
                    self.enclosing_scope[id(child)] = scope
                    if isinstance(child, ast.ClassDef):
                        self.class_scopes[id(child)] = child
                        pending.append((child, child, child))
                    else:
                        self.class_scopes[id(child)] = class_scope
                        pending.append((child, class_scope, child))
                else:
                    self.scopes[id(child)] = scope
                    self.class_scopes[id(child)] = class_scope
                    pending.append((child, class_scope, scope))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self._record(node)

    def _record(self, node):
        attributes = {}
        for member in ast.walk(node):
            if self.scopes.get(id(member)) is not node:
                continue
            if isinstance(member, ast.Assign):
                assigned = [(target.id, member.value)
                            for target in member.targets
                            if isinstance(target, ast.Name)]
            elif isinstance(member, ast.AnnAssign) \
                    and member.value is not None \
                    and isinstance(member.target, ast.Name):
                assigned = [(member.target.id, member.value)]
            else:
                continue
            for name, held in assigned:
                attributes[name] = None if name in attributes else held
        self.class_attributes[id(node)] = attributes
        owner = self.class_bindings.setdefault(
            id(self.enclosing_scope.get(id(node))), {})
        owner.setdefault(node.name, []).append(node)

    def class_body_binds(self, node):
        """The value a class statement's own body binds `node` to.

        A class body binding also makes the class NAME a derived binding
        for the module's fixpoint, exactly as it did when only a direct
        `Assign` was seen, so the direct statements are handed back and
        the caller appends them.
        """
        return [statement.value for statement in node.body
                if isinstance(statement, ast.Assign)]

    def _names_in(self, scope):
        return self.class_bindings.get(
            id(self.enclosing_scope.get(id(scope))), {})

    def readable_bases(self, scope):
        """The classes scope's readable base expressions could name.

        A base name is resolved through the scope the class statement
        sits in — the module, or the function or class body above it —
        rather than through a module-wide table, so a class defined
        inside a function does not shadow one of the same name at module
        level. A name that scope binds more than once yields every class
        it could name, because guessing between them is the one reading
        this index never takes. A qualified `Outer.Base` is read the same
        way: `Outer` through that scope, `Base` through the class body
        `Outer` binds.

        A base that is still unreadable is skipped: a subscripted
        `Generic[T]` and a base built by a call. That is a property of the
        base *expression*, not of the module — both can name a class the
        module defines — and the launch audit names it a residual.
        """
        if scope is None:
            return []
        owner = self._names_in(scope)
        found = []
        seen = set()
        pending = []
        for base in scope.bases:
            if isinstance(base, ast.Name):
                pending.extend(owner.get(base.id, ()))
            elif isinstance(base, ast.Attribute) \
                    and isinstance(base.value, ast.Name):
                for holder in owner.get(base.value.id, ()):
                    pending.extend(
                        self.class_bindings.get(id(holder), {}).get(
                            base.attr, ()))
        while pending:
            node = pending.pop(0)
            if id(node) in seen:
                continue
            seen.add(id(node))
            found.append(node)
            pending = list(self.readable_bases(node)) + pending
        return found

    def attribute_values(self, value):
        """The values this attribute can read, nearest reading first.

        The enclosing class's own table first, so an override shadows
        every base and a subclass binding the name to something that does
        not derive is not a launch. Then each readable base in
        inheritance order, because Python reads an attribute a base binds.

        Where two classes in that order bind the name differently, both
        are yielded and the caller derives if either does. That is a
        deliberate over-refusal, not a reachability claim: this index
        computes no C3 linearisation, so it cannot tell which of two
        branches the MRO would pick, and the cost is concrete — in a
        diamond whose nearer base binds a non-subprocess module the launch
        is refused even though at runtime it would raise AttributeError
        and could not hang. `diamond-bases-disagree-over-refuses` in
        tests/_launch_refusal_rows.py pins that spelling.
        """
        scope = self.class_scopes.get(id(value))
        own = self.class_attributes.get(id(scope), {})
        if value.attr in own:
            yield own[value.attr]
            return
        for base in self.readable_bases(scope):
            held = self.class_attributes.get(id(base), {}).get(
                value.attr, MISS)
            if held is not MISS:
                yield held
