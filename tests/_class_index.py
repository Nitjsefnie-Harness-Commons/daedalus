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
        self._record_aliases(tree)

    def _record_aliases(self, tree):
        """A name bound in a scope to one of that scope's classes.

        `Alias = Base` then `class Child(Alias)` is a base the module
        does define, reached through a name that is not a class
        statement. Python resolves the alias before the base list is
        built, so the alias is followed here; an alias whose target is
        itself dynamic stays unreadable, which is a residual.
        """
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if self.scopes.get(id(node.value)) is not \
                    self.scopes.get(id(node.targets[0])):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            scope = self.scopes.get(id(node.targets[0]))
            table = self.class_bindings.get(id(scope), {})
            for target in node.targets:
                if isinstance(target, ast.Name) and table.get(node.value.id):
                    table.setdefault(target.id, []).extend(
                        table[node.value.id])

    def _record(self, node):
        self.class_attributes[id(node)] = dict(self.namespace_bindings(node))
        owner = self.class_bindings.setdefault(
            id(self.enclosing_scope.get(id(node))), {})
        owner.setdefault(node.name, []).append(node)

    def namespace_bindings(self, node):
        """Every name node's own class namespace binds, and to what.

        One walk, one property. A class body is a block Python executes,
        so a name bound anywhere inside it is a class-namespace binding:
        under an `if`, a loop, a `with`, a `try`, nested arbitrarily deep
        inside those, and through a walrus, which is an expression rather
        than a statement and so is named by no statement list at all. What
        is NOT a class-namespace binding is anything whose own scope is a
        nested function or a nested class, because those bind their own
        names; that is the nearest-scope test, and it is Python's rule
        rather than a convenience.

        A name the body declares `global` is the exception Python itself
        makes: it is written to the module, not to the class namespace, so
        it is not recorded as a class attribute here. That is fail-closed
        in the safe direction and it is a residual the docstring names.
        """
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
            elif isinstance(member, ast.NamedExpr) \
                    and isinstance(member.target, ast.Name):
                assigned = [(member.target.id, member.value)]
            else:
                continue
            for name, held in assigned:
                if name in self.declared_globals(node):
                    continue
                attributes[name] = None if name in attributes else held
        return attributes

    def declared_globals(self, node):
        """The names node's own body binds to the module, not to itself.

        A `global` statement writes the module's name rather than the
        class namespace's, so `self.mod` on such an attribute raises at
        runtime instead of reading what the class body wrote. Recording
        it anyway would be fail-closed, but it would also contradict the
        property this walk states, so the name is left out and the
        refusal to guess is the safe direction.
        """
        declared = set()
        for statement in node.body:
            if isinstance(statement, ast.Global):
                declared.update(statement.names)
        return declared

    def class_body_binds(self, node):
        """Every value a class statement's namespace binds, for the fixpoint.

        A class-namespace binding also makes the class NAME a derived
        binding for the launch audit's fixpoint. It reads the same walk
        the attribute table reads, so the two cannot disagree about what
        a class body binds.
        """
        return list(self.namespace_bindings(node).values())

    def _named_classes(self, scope, name):
        """The classes `name` can denote where `scope`'s base list sits.

        Python resolves a name in the enclosing scope and then falls back
        to the module's globals, so the enclosing scope is consulted
        first and the module's only for a name it does not carry. The
        fallback is per NAME, not per table: a function that defines a
        class of its own does not thereby stop resolving every other
        name to the module's.

        Scoping the lookup to the enclosing scope first is what a
        function-local class of the same name needs — it must not be
        shadowed by the module's — and the fallback is what a module-level
        class needs when the class statement sits inside a function that
        never names it.
        """
        enclosing = self.enclosing_scope.get(id(scope))
        here = self.class_bindings.get(id(enclosing), {})
        if name in here or enclosing is None:
            return here.get(name, ())
        return self.class_bindings.get(id(None), {}).get(name, ())

    def readable_bases(self, scope):
        """The classes scope's readable base expressions could name.

        A base name is resolved in the scope the class statement sits in
        and then in the module's globals, so a class defined inside a
        function still reaches a module-level class it never names, while
        a class defined inside a function does not shadow one of the same
        name at module level. A name a scope binds more than once yields
        every class it could name, because guessing between them is the
        one reading this index never takes. A qualified `Outer.Base` is
        read the same way: `Outer` through that scope, `Base` through the
        class body `Outer` binds.

        A base that is still unreadable is skipped: a subscripted
        `Generic[T]` and a base built by a call. That is a property of the
        base *expression*, not of the module — both can name a class the
        module defines — and the launch audit names it a residual.
        """
        if scope is None:
            return []
        found = []
        seen = set()
        pending = []
        for base in scope.bases:
            if isinstance(base, ast.Name):
                pending.extend(self._named_classes(scope, base.id))
            elif isinstance(base, ast.Attribute) \
                    and isinstance(base.value, ast.Name):
                for holder in self._named_classes(scope, base.value.id):
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
