"""Scope and repository-root facts the coverage guard is judged against.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/_coverage_guard.py: these answer "what does this name
mean here", which is a different question from "is this launch safe".
"""
import ast
from functools import cached_property
from pathlib import PurePosixPath, PureWindowsPath

from _coverage_memo import nodes as memo_nodes


_ROOT_MODULES = frozenset({'_util', 'test_dashboard_behaviour'})
_ALL_NAMES = '*'
_CANONICAL_MEMBERS = {'Path': ('pathlib', 'Path')}
_PROOF_NAMES = frozenset({'Path', 'str', 'ROOT', _ALL_NAMES})


_REFLECTIVE_READS = frozenset({'getattr', 'hasattr'})


_COMPREHENSION_SCOPES = (
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


class _ScopeFacts:
    """Share derivations within one analysis; never cache a mutable tree.

    Previously each consumer recomputed these whole-module products.
    Keeping this object local to the analysis also releases parent maps
    that hold the root, without adding a process-wide invalidation rule.
    """

    def __init__(self, tree, layout=None):
        self.tree = tree
        self.layout = layout or _evaluation_scopes(tree)

    @cached_property
    def imports(self):
        return _import_rebound_names(self.tree)

    @cached_property
    def root_assignments(self):
        return {target: value for target, value in
                _root_assignments(self.tree).items()
                if target in self.root_scope_nodes}

    @cached_property
    def root_scope_nodes(self):
        annotations = {node.target for node, _ in self.layout[0]
                       if isinstance(node, ast.AnnAssign)
                       and node.value is None}
        return {node for node, scope in self.layout[0]
                if self.destinations[scope].get('ROOT', scope) is self.tree
                and node not in annotations}

    @cached_property
    def root_owners(self):
        return root_owner_names(self.tree, self.imports)

    @cached_property
    def bindings(self):
        return _binding_destinations(*self.layout)

    @property
    def destinations(self):
        return self.bindings[1]

    @property
    def binding_layout(self):
        return self.layout

    @property
    def binding_products(self):
        return self.bindings

    @cached_property
    def unprovable(self):
        return _unprovable_names(self.tree, self.layout, self)


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


def _import_bound_name(node, alias):
    if isinstance(node, ast.Import):
        return alias.asname or alias.name.split('.')[0]
    return alias.asname or alias.name


def _canonical_import(node, alias, bound):
    if isinstance(node, ast.Import):
        return False
    if bound == 'ROOT':
        return alias.name == 'ROOT' and alias.asname is None
    return (not node.level
            and (node.module, alias.name) == _CANONICAL_MEMBERS.get(bound))


def _import_rebound_names(tree):
    """Owner retirements and proof shadows, from one import enumeration."""
    rebound, proof_shadows = set(), {}
    root_imported = False
    for node in memo_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = _import_bound_name(node, alias)
                if not (isinstance(node, ast.Import)
                        and alias.name in _ROOT_MODULES):
                    rebound.add(bound)
                canonical = _canonical_import(node, alias, bound)
                if not canonical and bound in _PROOF_NAMES:
                    shadow = (f'{bound}()'
                              if bound in {'Path', 'str', 'ROOT'} else bound)
                    proof_shadows.setdefault(node, set()).add(shadow)
                if canonical and bound == 'ROOT':
                    root_imported = True
    return rebound, proof_shadows, root_imported


def _rebound_by_import(name, rebound):
    return name in rebound or _ALL_NAMES in rebound


def root_owner_names(tree, imports=None):
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
    # A mutation retires the module every alias shares; an import rebinding
    # retires only the name it rebinds.
    rebound, _, _ = imports if imports is not None else (
        _import_rebound_names(tree))
    return {name for name, module in modules.items()
            if module not in gone and not _rebound_by_import(name, rebound)}


def _is_root_spelling(node, shadowed_names=frozenset(), owners=frozenset()):
    """Whether an expression provably names the repository root."""
    if _ALL_NAMES in shadowed_names:
        return False
    if any(isinstance(part, ast.Name) and part.id in shadowed_names
           for part in ast.walk(node)):
        return False
    if isinstance(node, ast.Name) and node.id == 'ROOT':
        return 'ROOT()' not in shadowed_names
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
        return ('str()' not in shadowed_names
                and _is_root_spelling(node.args[0], shadowed_names, owners))
    return False


def _is_relative_literal(value):
    """Whether a literal path stays relative on POSIX and Windows."""
    if not isinstance(value, str):
        return False
    paths = (PurePosixPath(value), PureWindowsPath(value))
    return all(not path.anchor and '..' not in path.parts for path in paths)


def _is_repository_root_binding(value, owners=frozenset()):
    """Whether a module assignment derives the checkout root."""
    if (isinstance(value, ast.Attribute) and value.attr == 'ROOT'
            and isinstance(value.value, ast.Name)
            and value.value.id == '_util'):
        return '_util' in owners
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


def _root_assignments(tree):
    values = {}
    for node in memo_nodes(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        values.update((target, value) for target in targets
                      if isinstance(target, ast.Name) and target.id == 'ROOT')
    return values


def _other_root_bindings(facts, assignments):
    for node in facts.root_scope_nodes:
        if not _bound_names(node) & {'ROOT', _ALL_NAMES}:
            continue
        if node in assignments:
            continue
        if isinstance(node, ast.ImportFrom) and all(
                _import_bound_name(node, alias) not in {'ROOT', _ALL_NAMES}
                or _canonical_import(node, alias, 'ROOT')
                for alias in node.names):
            continue
        return True
    return False


def _unprovable_names(tree, layout=None, facts=None):
    facts = facts or _ScopeFacts(tree, layout)
    _, proof_shadows, root_imported = facts.imports
    scoped, parents = facts.layout
    imports = {scope: set() for scope in parents}
    for node, scope in scoped:
        imports[scope].update(proof_shadows.get(node, ()))
    destinations = facts.destinations
    names = _routed_bindings(imports, destinations)[tree]
    if not facts.root_assignments and not root_imported:
        names.add('ROOT()')
    return names, proof_shadows


def _shadowed_names(tree, facts=None):
    """Names whose source value is replaced somewhere in the module."""
    facts = facts or _ScopeFacts(tree)
    names = set().union(*(_bound_names(node) for node in memo_nodes(tree)
                          if not isinstance(node, (ast.Import,
                                                   ast.ImportFrom))))
    unprovable, _ = facts.unprovable
    names.update(unprovable)
    root_values = facts.root_assignments
    owners = facts.root_owners
    if (root_values
            and not _other_root_bindings(facts, root_values)
            and not {'Path', 'Path()', '_util'} & names
            and all(_is_repository_root_binding(value, owners)
                    for value in root_values.values())):
        names.discard('ROOT')
    return names


def _containing_binding_scope(scope, parents):
    """Nearest scope outside any nested comprehension scopes."""
    while isinstance(scope, _COMPREHENSION_SCOPES):
        scope = parents[scope]
    return scope


def _evaluation_scopes(tree, type_scopes=True):
    """Nodes with their evaluation or binding scope, plus scope parents."""
    scoped = []
    parents = {tree: None}

    def enclosing(scope):
        if isinstance(scope, ast.ClassDef):
            return parents[scope]
        return scope

    def visit_arguments(arguments, scope, binding_scope):
        variadic = [value for value in (arguments.vararg, arguments.kwarg)
                    if value is not None]
        named = (arguments.posonlyargs + arguments.args
                 + arguments.kwonlyargs)
        for argument in named + variadic:
            scoped.append((argument, binding_scope))
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
            visit_arguments(node.args, annotation, node)
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
            visit_arguments(node.args, scope, node)
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


def _scope_shadows(tree, layout=None, facts=None):
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
    facts = facts or _ScopeFacts(tree, layout)
    scoped, parents = facts.layout
    shadows = {scope: set() for scope in parents}
    unprovable, imports = facts.unprovable
    for node, scope in scoped:
        shadows[scope].update(imports.get(node, ()))
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            shadows[scope].update(_bound_names(node))
    # A star import is a SyntaxError inside a function, so module-wide
    # is its real scope.
    shadows[tree].update(unprovable)
    destinations = facts.destinations
    return _routed_bindings(shadows, destinations)


_FUNCTION_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_TYPE_PARAMETERS = tuple(getattr(ast, name) for name in (
    'TypeVar', 'ParamSpec', 'TypeVarTuple') if hasattr(ast, name))


def _bound_names(node):
    if (isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))):
        return {node.id}
    if isinstance(node, ast.arg):
        return {node.arg}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                         *_TYPE_PARAMETERS)):
        return {node.name}
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return {node.name} if node.name else set()
    if isinstance(node, ast.MatchMapping):
        return {node.rest} if node.rest else set()
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {_import_bound_name(node, alias) for alias in node.names}
    return set()


def _binding_destinations(scoped, parents):
    local = {scope: set() for scope in parents}
    global_names = {scope: set() for scope in parents}
    nonlocal_names = {scope: set() for scope in parents}
    for node, scope in scoped:
        local[scope].update(_bound_names(node))
        if isinstance(node, ast.Global):
            global_names[scope].update(node.names)
        elif isinstance(node, ast.Nonlocal):
            nonlocal_names[scope].update(node.names)
    module = next(scope for scope, parent in parents.items() if parent is None)
    destinations = {scope: dict.fromkeys(names, module)
                    for scope, names in global_names.items()}
    for scope in parents:
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
            destinations[scope][name] = module if target is None else target
    return local, destinations


def _routed_bindings(local, destinations):
    routed = {scope: set() for scope in local}
    for scope, names in local.items():
        for name in names:
            target = destinations[scope].get(name.removesuffix('()'), scope)
            routed[target].add(name)
    return routed


def _scope_bindings(scoped, parents, products=None):
    """All grammar-bound names, independent of the rebinding product.

    Declarations bind their destination even without an assignment:
    uncertainty cannot prove a builtin. Star imports bind every name.
    """
    local, destinations = (products if products is not None
                           else _binding_destinations(scoped, parents))
    local = {scope: names.copy() for scope, names in local.items()}
    for scope, declared in destinations.items():
        local[scope].update(declared)
    return _routed_bindings(local, destinations)


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
