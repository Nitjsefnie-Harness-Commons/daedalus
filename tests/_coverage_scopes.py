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


def _evaluation_scopes(tree, type_scopes=False):
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

    def annotation_scope(node, scope):
        parameters = getattr(node, 'type_params', ())
        if not type_scopes or not parameters:
            return scope
        annotation = object()
        parents[annotation] = scope
        for parameter in parameters:
            visit(parameter, annotation)
        return annotation

    def visit(node, scope):
        scoped.append((node, scope))
        if type_scopes and isinstance(node, getattr(ast, 'TypeAlias', ())):
            parents[node] = annotation_scope(node, scope)
            visit(node.name, scope)
            visit(node.value, node)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotation = annotation_scope(node, scope)
            parents[node] = enclosing(annotation)
            for decorator in node.decorator_list:
                visit(decorator, scope)
            visit_arguments(node.args, annotation)
            for value in (*node.args.defaults, *node.args.kw_defaults):
                if value is not None:
                    visit(value, scope)
            if node.returns is not None:
                visit(node.returns, annotation)
            if not type_scopes:
                for value in getattr(node, 'type_params', ()):
                    visit(value, scope)
            for statement in node.body:
                visit(statement, node)
            return
        if isinstance(node, ast.ClassDef):
            annotation = annotation_scope(node, scope)
            parents[node] = enclosing(annotation)
            for decorator in node.decorator_list:
                visit(decorator, scope)
            for value in (*node.bases, *node.keywords):
                visit(value, annotation)
            if not type_scopes:
                for value in getattr(node, 'type_params', ()):
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


_FUNCTION_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_TYPE_PARAMETERS = tuple(getattr(ast, name) for name in (
    'TypeVar', 'ParamSpec', 'TypeVarTuple') if hasattr(ast, name))
_ALL_NAMES = '*'


def _bound_names(node):
    if (isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))):
        return {node.id}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                         *_TYPE_PARAMETERS)):
        return {node.name}
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return {node.name} if node.name else set()
    if isinstance(node, ast.MatchMapping):
        return {node.rest} if node.rest else set()
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split('.')[0]
                for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names}
    return set()


def _scope_bindings(scoped, parents):
    """All grammar-bound names, independent of the rebinding product.

    Declarations bind their destination even without an assignment:
    uncertainty cannot prove a builtin. Star imports bind every name.
    """
    local = {scope: set() for scope in parents}
    global_names = {scope: set() for scope in parents}
    nonlocal_names = {scope: set() for scope in parents}
    for node, scope in scoped:
        local[scope].update(_bound_names(node))
        if isinstance(node, _FUNCTION_SCOPES):
            local[node].update(_parameter_names(node.args))
        if isinstance(node, ast.Global):
            global_names[scope].update(node.names)
        elif isinstance(node, ast.Nonlocal):
            nonlocal_names[scope].update(node.names)
    module = next(scope for scope, parent in parents.items() if parent is None)
    bindings = {scope: names - global_names[scope] - nonlocal_names[scope]
                for scope, names in local.items()}
    for scope in parents:
        bindings[module].update(global_names[scope])
        for name in nonlocal_names[scope]:
            target = parents[scope]
            while target is not None:
                if (isinstance(target, _FUNCTION_SCOPES)
                        and name not in global_names[target]
                        and name not in nonlocal_names[target]
                        and (name in local[target]
                             or _ALL_NAMES in local[target])):
                    break
                target = parents[target]
            bindings[module if target is None else target].add(name)
    return bindings


def _name_is_unbound(name, scope, bindings, parents):
    if scope is None:
        return False
    skip_class = False
    while scope is not None:
        if scope not in bindings or scope not in parents:
            return False
        names = (set() if skip_class and isinstance(scope, ast.ClassDef)
                 else bindings[scope])
        if name in names or _ALL_NAMES in names:
            return False
        skip_class |= isinstance(scope, (*_FUNCTION_SCOPES, ast.ClassDef))
        scope = parents[scope]
    return True


def _visible_scope_shadows(scope, shadows, parents):
    visible = set()
    while scope is not None:
        visible.update(shadows[scope])
        scope = parents[scope]
    return visible
