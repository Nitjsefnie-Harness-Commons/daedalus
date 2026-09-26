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
a second assignment to a module's own constant.
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


def _module_execution(tree):
    """(imports, binds, definitions) for what module execution establishes.

    imports maps a name to {import line: set of source module stems};
    binds maps a name to the set of lines that bind it; definitions maps
    a name to the subset of those lines carrying a def, async def or
    class.
    """
    imports = {}
    binds = {}
    definitions = {}

    def bind(name, lineno, defining=False):
        binds.setdefault(name, set()).add(lineno)
        if defining:
            definitions.setdefault(name, set()).add(lineno)

    def imported(name, lineno, source):
        imports.setdefault(name, {}).setdefault(lineno, set()).add(source)

    def collect_walrus(node):
        stack = list(ast.iter_child_nodes(node))
        while stack:
            current = stack.pop()
            if isinstance(current, ast.NamedExpr):
                if isinstance(current.target, ast.Name):
                    bind(current.target.id, current.lineno)
                stack.extend(ast.iter_child_nodes(current))
            elif isinstance(current, DEFN):
                continue
            elif isinstance(current, ast.Lambda):
                continue
            elif isinstance(current, COMPREHENSION):
                continue
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
            bind(node.name, node.lineno, defining=True)
            return
        collect_walrus(node)
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
