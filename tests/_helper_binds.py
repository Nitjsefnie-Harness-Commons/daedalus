"""What module execution binds in a tests module, and what it imports.

The one copy of the scope rules both helper-boundary controls use. A name
is bound by module execution, which is narrower than a direct child of
the module body: a walrus, a `for` target, a `with ... as`, an `except E
as`, a `match` capture and a bind in an `else` or `finally` body all
establish one, while a def, class, comprehension or lambda body is its
own namespace and establishes none. An import binds the LOCAL name it
brings in, so `import X as _Y` establishes `_Y` and `from X import name
as _Y` establishes `_Y`.

`scan` returns every bind, because a shadow is any rebind of an imported
name. `definitions` returns only the `def`, `async def` and `class`
binds, because a re-implementation is a second definition of a name, not
a second assignment to a module's own constant — and a PEP 695 `type X =
...` is a bind, not one of those three.

WHERE A NESTED SCOPE BINDS ANYWAY. A comprehension and a generator
expression are NOT scopes for an assignment expression: a walrus
anywhere inside one binds in the containing scope, which is the rule the
language states and the rule this walker used to skip them against. A
`def` and a `lambda` ARE scopes, so a walrus in their BODY binds
nothing — but their DEFAULTS, their annotations and a `def`'s decorators
are evaluated where they are written, so a walrus in those binds the
containing scope. A class BODY is a scope of its own, so
nothing in it binds the module, while a class HEADER is not: its
decorators, its bases and its keywords all evaluate where the class is
written.
"""
import ast

DEFN = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
COMPREHENSION = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _target_names(target):
    """An attribute or subscript target binds no module name, so
    `x.name = 1` and `d['k'] = 1` contribute nothing.
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for element in target.elts:
            names.extend(_target_names(element))
        return names
    return []


def _module_execution(tree, nodes=None):
    """(imports, binds, definitions) for what module execution establishes.

    imports maps a name to {import line: set of source module stems};
    binds maps a name to the set of lines that bind it; definitions maps
    a name to the subset of those lines carrying a def, async def or
    class. `nodes`, when given, collects the definition NODES themselves
    under the same names, for a caller that needs a definition's content.
    """
    imports = {}
    binds = {}
    definitions = {}

    def bind(name, lineno, defining=False, node=None):
        binds.setdefault(name, set()).add(lineno)
        if defining:
            definitions.setdefault(name, set()).add(lineno)
            if nodes is not None:
                nodes.setdefault(name, []).append(node)

    def imported(name, lineno, source):
        imports.setdefault(name, {}).setdefault(lineno, set()).add(source)

    def evaluated_here(node):
        """The parts of a def or lambda evaluated in the ENCLOSING scope.

        Defaults, annotations and decorators run where the def is
        written, and a class HEADER — its decorators, its bases and its
        keywords — runs there too, so a walrus in any of them binds the
        module. What does not is the BODY: a function's, a lambda's and
        a class's, each its own scope. Returning the first group and not
        the second is what makes a walrus in a default a module-scope
        bind and a walrus in a body not one.
        """
        if isinstance(node, ast.ClassDef):
            return list(node.decorator_list) + list(node.bases) + [
                keyword.value for keyword in node.keywords]
        arguments = node.args
        positional = (list(getattr(arguments, 'posonlyargs', []))
                      + arguments.args + arguments.kwonlyargs)
        parts = list(getattr(node, 'decorator_list', []))
        parts += [argument.annotation for argument in positional
                  if argument.annotation is not None]
        if arguments.vararg is not None and arguments.vararg.annotation:
            parts.append(arguments.vararg.annotation)
        if arguments.kwarg is not None and arguments.kwarg.annotation:
            parts.append(arguments.kwarg.annotation)
        parts += list(arguments.defaults)
        parts += [default for default in arguments.kw_defaults
                  if default is not None]
        if getattr(node, 'returns', None) is not None:
            parts.append(node.returns)
        return parts

    def collect_walrus(roots):
        # The roots themselves are seeded, not their children: a walrus
        # that IS a default is the node handed in, and a walk that only
        # looked below its roots would step over it.
        stack = list(roots)
        while stack:
            current = stack.pop()
            if isinstance(current, ast.NamedExpr):
                if isinstance(current.target, ast.Name):
                    bind(current.target.id, current.lineno)
                stack.extend(ast.iter_child_nodes(current))
            elif isinstance(current, ast.Lambda):
                # A lambda's body is its own scope; its defaults are
                # not. A comprehension is not a scope at all, so it is
                # descended into like any other expression.
                stack.extend(evaluated_here(current))
            elif isinstance(current, DEFN):
                stack.extend(evaluated_here(current))
            else:
                stack.extend(ast.iter_child_nodes(current))

    def record(node):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported(alias.asname or alias.name.split('.')[0],
                         node.lineno, alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != '*':
                    imported(alias.asname or alias.name, node.lineno,
                             node.module or '')
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _target_names(target):
                    bind(name, node.lineno)
        elif isinstance(node, ast.TypeAlias):
            # `type X = ...` binds X at module scope. It is a bind and
            # not one of the three DEFINING forms, so a `type X` beside
            # a `def X` is a rebind (the shadow control's) rather than a
            # second definition (this control's).
            bind(node.name.id, node.lineno)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            for name in _target_names(node.target):
                bind(name, node.lineno)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for name in _target_names(node.target):
                bind(name, node.lineno)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for name in _target_names(item.optional_vars):
                        bind(name, node.lineno)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bind(node.name, node.lineno)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                for sub in ast.walk(case.pattern):
                    if isinstance(sub, (ast.MatchAs, ast.MatchStar)):
                        if sub.name:
                            bind(sub.name, sub.lineno)
                    elif isinstance(sub, ast.MatchMapping) and sub.rest:
                        bind(sub.rest, sub.lineno)

    def statement(node):
        if isinstance(node, DEFN):
            # A def's defaults, annotations and decorators are evaluated
            # where it is written; its body is its own scope, so the
            # walk is handed the first group and not the node.
            collect_walrus(evaluated_here(node))
            bind(node.name, node.lineno, defining=True, node=node)
            return
        collect_walrus([node])
        record(node)
        for field in ('body', 'orelse', 'finalbody'):
            children = getattr(node, field, None)
            if (isinstance(children, list) and children and all(
                    isinstance(child, ast.stmt) for child in children)):
                block(children)
        for handler in getattr(node, 'handlers', None) or []:
            statement(handler)
        if isinstance(node, ast.Match):
            for case in node.cases:
                block(case.body)

    def block(body):
        for child in body:
            statement(child)

    block(tree.body)
    return imports, binds, definitions


def scan(tree):
    """(imports, binds) for every module-execution bind, of any form."""
    imports, binds, _ = _module_execution(tree)
    return imports, binds


def definitions(tree):
    """{name: {lineno}} for the def, async def and class binds alone."""
    _, _, definitions = _module_execution(tree)
    return definitions


def definition_nodes(tree):
    """{name: [node]} for the def, async def and class binds alone.

    The nodes, not just their lines, so a caller can compare two
    definitions by CONTENT: the branch boundary keys an allowance row on
    a declaration, and a declaration is identified by what it contains
    rather than by the line it sits on.

    The SCOPE is the one `_module_execution` decides, and it runs the
    walk that decides it rather than a second: `ast.walk` reaches a def
    nested in a function, a lambda or a class body, and those bind
    nothing at module scope, so counting one made the boundary refuse a
    legitimate row for a tabled file.
    """
    found = {}
    _module_execution(tree, found)
    return found
