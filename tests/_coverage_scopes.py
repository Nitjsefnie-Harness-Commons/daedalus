"""Scope and repository-root facts the coverage guard is judged against.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/_coverage_guard.py: these answer "what does this name
mean here", which is a different question from "is this launch safe".
"""
import ast
from pathlib import PurePosixPath, PureWindowsPath

from _coverage_memo import nodes as memo_nodes


_ROOT_MODULES = frozenset({'_util', 'test_dashboard_behaviour'})


_REFLECTIVE_READS = frozenset({'getattr', 'hasattr'})


_COMPREHENSION_SCOPES = (
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _chain(node):
    parts = []
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        parts.append(node)
        node = node.value
    return parts, node


def _reaches_namespace(parts):
    return any(isinstance(part, ast.Subscript) or part.attr.startswith('__')
               for part in parts)


def _names_root(parts):
    return any(isinstance(part, ast.Attribute) and part.attr == 'ROOT'
               for part in parts)


def _reads_attribute(call):
    return (isinstance(call.func, ast.Name)
            and call.func.id in _REFLECTIVE_READS and len(call.args) > 1
            and isinstance(call.args[1], ast.Constant)
            and isinstance(call.args[1].value, str)
            and not call.args[1].value.startswith('__'))


def _parents(tree):
    parents = {}
    for node in memo_nodes(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _retires_all_by_setattr(node):
    if (not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name)
            or node.func.id not in {'setattr', 'delattr'}
            or len(node.args) < 2 or isinstance(node.args[0], ast.Name)):
        return False
    name = node.args[1]
    return not isinstance(name, ast.Constant) or name.value == 'ROOT'


def _escapes(node, parents):
    parent = parents.get(node)
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return False
    return not (isinstance(parent, ast.Call) and parent.args
                and parent.args[0] is node and _reads_attribute(parent))


def root_owner_names(tree):
    """Import-bound names of a root module reached only by attribute reads."""
    modules = {}
    for node in memo_nodes(tree):
        if isinstance(node, ast.Import):
            modules.update((alias.asname or alias.name, alias.name)
                           for alias in node.names
                           if alias.name in _ROOT_MODULES)
    parents = _parents(tree)
    retired = set()
    for node in memo_nodes(tree):
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            parts, base = _chain(node)
            stored = isinstance(node.ctx, (ast.Store, ast.Del))
            if not isinstance(base, ast.Name):
                if stored and _names_root(parts):
                    return set()
            elif _reaches_namespace(parts) or (stored and _names_root(parts)):
                retired.add(base.id)
        elif _retires_all_by_setattr(node):
            return set()
        elif (isinstance(node, ast.Name) and node.id in modules
                and isinstance(node.ctx, (ast.Load, ast.Del))
                and _escapes(node, parents)):
            retired.add(node.id)
    gone = {modules[name] for name in retired if name in modules}
    return {name for name, module in modules.items() if module not in gone}


def _is_root_spelling(node, shadowed_names=frozenset(), owners=frozenset()):
    """Whether an expression provably names the repository root."""
    if any(isinstance(part, ast.Name) and part.id in shadowed_names
           for part in ast.walk(node)):
        return False
    if isinstance(node, ast.Name) and node.id == 'ROOT':
        return True
    if (isinstance(node, ast.Attribute) and node.attr == 'ROOT'
            and isinstance(node.value, ast.Name)
            and node.value.id in owners):
        return True
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and _is_relative_literal(node.right.value)):
        return _is_root_spelling(node.left, shadowed_names, owners)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'str' and len(node.args) == 1
            and not node.keywords):
        return _is_root_spelling(node.args[0], shadowed_names, owners)
    return False


def _is_relative_literal(value):
    """Whether a literal path stays relative on POSIX and Windows."""
    if not isinstance(value, str):
        return False
    paths = (PurePosixPath(value), PureWindowsPath(value))
    return all(not path.anchor and '..' not in path.parts for path in paths)


def _is_repository_root_binding(value):
    """Whether a module assignment derives the checkout root."""
    if (isinstance(value, ast.Attribute) and value.attr == 'ROOT'
            and isinstance(value.value, ast.Name)
            and value.value.id == '_util'):
        return True
    if (not isinstance(value, ast.Subscript)
            or not isinstance(value.slice, ast.Constant)
            or value.slice.value != 1):
        return False
    parents = value.value
    if not isinstance(parents, ast.Attribute) or parents.attr != 'parents':
        return False
    resolved = parents.value
    if (not isinstance(resolved, ast.Call) or resolved.args
            or resolved.keywords
            or not isinstance(resolved.func, ast.Attribute)
            or resolved.func.attr != 'resolve'):
        return False
    constructor = resolved.func.value
    return (isinstance(constructor, ast.Call)
            and isinstance(constructor.func, ast.Name)
            and constructor.func.id == 'Path'
            and len(constructor.args) == 1 and not constructor.keywords
            and isinstance(constructor.args[0], ast.Name)
            and constructor.args[0].id == '__file__')


def _shadowed_names(tree):
    """Names whose source value is replaced somewhere in the module."""
    walked = memo_nodes(tree)
    names = {node.id for node in walked
             if isinstance(node, ast.Name)
             and isinstance(node.ctx, (ast.Store, ast.Del))}
    names.update(node.arg for node in walked
                 if isinstance(node, ast.arg))
    names.update(node.name for node in walked
                 if isinstance(node, (ast.FunctionDef,
                                      ast.AsyncFunctionDef, ast.ClassDef)))
    names.update(node.name for node in walked
                 if isinstance(node, ast.ExceptHandler) and node.name)
    names.update(node.name for node in walked
                 if isinstance(node, ast.MatchAs)
                 and node.name)
    names.update(node.name for node in walked
                 if isinstance(node, ast.MatchStar)
                 and node.name)
    names.update(node.rest for node in walked
                 if isinstance(node, ast.MatchMapping) and node.rest)
    root_values = []
    for node in walked:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == 'ROOT'
               for target in targets):
            root_values.append(value)
    if (root_values and not {'Path', '_util'} & names
            and all(_is_repository_root_binding(value)
                    for value in root_values)):
        names.discard('ROOT')
    return names


def _parameter_names(arguments):
    """The names a def's or a lambda's parameter list binds in its scope."""
    return ({argument.arg for argument in
             (arguments.posonlyargs + arguments.args + arguments.kwonlyargs)}
            | {extra.arg for extra in (arguments.vararg, arguments.kwarg)
               if extra is not None})


def _containing_binding_scope(scope, parents):
    """Nearest scope outside any nested comprehension scopes."""
    while isinstance(scope, _COMPREHENSION_SCOPES):
        scope = parents[scope]
    return scope


def _evaluation_scopes(tree):
    """Nodes with their evaluation or binding scope, plus scope parents."""
    scoped = []
    parents = {tree: None}

    def enclosing(scope):
        if isinstance(scope, ast.ClassDef):
            return parents[scope]
        return scope

    def visit_arguments(arguments, scope):
        variadic = [value for value in (arguments.vararg, arguments.kwarg)
                    if value is not None]
        named = (arguments.posonlyargs + arguments.args
                 + arguments.kwonlyargs)
        for argument in named + variadic:
            if argument.annotation is not None:
                visit(argument.annotation, scope)

    def visit(node, scope):
        scoped.append((node, scope))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parents[node] = enclosing(scope)
            for decorator in node.decorator_list:
                visit(decorator, scope)
            visit_arguments(node.args, scope)
            for value in (*node.args.defaults, *node.args.kw_defaults):
                if value is not None:
                    visit(value, scope)
            if node.returns is not None:
                visit(node.returns, scope)
            for value in getattr(node, 'type_params', ()):
                visit(value, scope)
            for statement in node.body:
                visit(statement, node)
            return
        if isinstance(node, ast.ClassDef):
            parents[node] = enclosing(scope)
            for decorator in node.decorator_list:
                visit(decorator, scope)
            for value in (*node.bases, *node.keywords,
                          *getattr(node, 'type_params', ())):
                visit(value, scope)
            for statement in node.body:
                visit(statement, node)
            return
        if isinstance(node, ast.Lambda):
            parents[node] = enclosing(scope)
            visit_arguments(node.args, scope)
            for value in (*node.args.defaults, *node.args.kw_defaults):
                if value is not None:
                    visit(value, scope)
            visit(node.body, node)
            return
        if isinstance(node, ast.NamedExpr):
            visit(node.value, scope)
            visit(node.target, _containing_binding_scope(scope, parents))
            return
        if isinstance(node, _COMPREHENSION_SCOPES):
            parents[node] = enclosing(scope)
            first, *remaining = node.generators
            visit(first.iter, scope)
            visit(first.target, node)
            for condition in first.ifs:
                visit(condition, node)
            for generator in remaining:
                visit(generator.iter, node)
                visit(generator.target, node)
                for condition in generator.ifs:
                    visit(condition, node)
            values = ([node.key, node.value] if isinstance(node, ast.DictComp)
                      else [node.elt])
            for value in values:
                visit(value, node)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, tree)
    return tuple(scoped), parents


def _scope_shadows(tree, layout=None):
    """Shadowed names per scope, attributed to the scope that binds them.

    A name bound inside one function cannot reach another's expressions, so
    a launch is judged against its own scope chain rather than against every
    binding in the module. A lambda is a scope too: its parameters bind its
    own body, and nothing in the scope that writes the lambda.

    Second consumer: `tests/_bash_resolver_scan.py` keys its own binding walk
    exactly as this function keys scopes, and its verdicts are wrong if this
    attribution drifts — a parameter this function stops attributing resolves
    from an outer binding instead of being out of scope.
    """
    scoped, parents = layout or _evaluation_scopes(tree)
    shadows = {scope: set() for scope in parents}
    for node, scope in scoped:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            shadows[scope].add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            shadows[node].update(_parameter_names(node.args))
        if (isinstance(node, ast.Name)
                and isinstance(node.ctx, (ast.Store, ast.Del))):
            shadows[scope].add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            shadows[scope].add(node.name)
        elif isinstance(node, ast.MatchAs) and node.name:
            shadows[scope].add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            shadows[scope].add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            shadows[scope].add(node.rest)
    return shadows


def _scope_bindings(scoped, shadows):
    """Shadowed names per scope, plus the names each scope's imports bind.

    Binding a name and rebinding one are different questions, and an
    import answers only the first: `from x import dict` binds a name over
    the builtin, but it is not the replacement of a value a root spelling
    is written against. A caller asking which names a scope binds reads
    this; one asking which values were replaced reads `_scope_shadows`.
    """
    bindings = {scope: set(names) for scope, names in shadows.items()}
    for node, scope in scoped:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # A star import binds names only its own module knows.
            bindings[scope].update(alias.asname or alias.name.split('.')[0]
                                   for alias in node.names
                                   if alias.name != '*')
    return bindings


def _visible_scope_shadows(scope, shadows, parents):
    """Names the given per-scope product holds where `scope` evaluates."""
    visible = set()
    while scope is not None:
        visible.update(shadows[scope])
        scope = parents[scope]
    return visible
