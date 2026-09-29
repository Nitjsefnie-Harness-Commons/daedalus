"""How a control proves a path is storage it owns.

Not a suite itself — run_tests.py only loads `test_*.py`.

The other half of `tests/_control_writes.py`: that module decides WHAT
must be judged, and this one answers "is this expression a path this
control owns?" and "what does this scope contain?". Every write rule the
guard applies is here, so a change to what counts as owned is a change to
one file rather than to the assembly that consults it.
"""
import ast
from pathlib import PurePosixPath, PureWindowsPath

from _control_calls import argument, has_spread, pattern_names

_UNKNOWN_PATH = 0
_RELATIVE_PATH = 1
_CONTROL_OWNED_PATH = 2
_MAX_PROOF_DEPTH = 2
SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
          ast.ClassDef, ast.Lambda)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp,
                  ast.GeneratorExp)


def _names_in(target):
    """Every name an assignment target is reached through.

    Local rather than shared: the same generator is a module-level `def`
    in `tests/_coverage_scopes.py`, and a second copy here would be a
    re-implementation no residue row can name — a row may not name a file
    this branch adds.
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _names_in(target.value)
    if isinstance(target, (ast.List, ast.Tuple)):
        return [name for part in target.elts for name in _names_in(part)]
    return []


def _record_binding(bindings, target, value):
    if isinstance(target, ast.Name):
        bindings.setdefault(target.id, []).append(value)
        return
    if isinstance(target, ast.Starred):
        _record_binding(bindings, target.value, value)
        return
    if not isinstance(target, (ast.List, ast.Tuple)):
        return
    if isinstance(value, (ast.List, ast.Tuple)):
        plain = [part for part in target.elts
                 if not isinstance(part, ast.Starred)]
        if len(plain) == len(target.elts) == len(value.elts):
            for part, source in zip(target.elts, value.elts):
                _record_binding(bindings, part, source)
            return
    for name in _names_in(target):
        bindings.setdefault(name, []).append(value)


def _target_name_nodes(target):
    if isinstance(target, ast.Name):
        yield target
    elif isinstance(target, ast.Starred):
        yield from _target_name_nodes(target.value)
    elif isinstance(target, (ast.List, ast.Tuple)):
        for part in target.elts:
            yield from _target_name_nodes(part)


def _subscript_bases(node):
    """Names whose subscript or attribute this statement assigns into."""
    targets: list[ast.expr]
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    elif isinstance(node, ast.Delete):
        targets = list(node.targets)
    else:
        return
    while targets:
        target = targets.pop()
        if isinstance(target, (ast.List, ast.Tuple)):
            targets.extend(target.elts)
        elif isinstance(target, ast.Starred):
            targets.append(target.value)
        elif isinstance(target, (ast.Subscript, ast.Attribute)):
            base = target.value
            while isinstance(base, (ast.Subscript, ast.Attribute)):
                base = base.value
            if isinstance(base, ast.Name):
                yield base.id


def _literal_path_kind(value):
    if not isinstance(value, str):
        return _UNKNOWN_PATH
    paths = (PurePosixPath(value), PureWindowsPath(value))
    if any(path.anchor or '..' in path.parts for path in paths):
        return _UNKNOWN_PATH
    return _RELATIVE_PATH


def _os_path_join(node):
    function = node.func
    if not (isinstance(function, ast.Attribute)
            and function.attr == 'join'):
        return False
    owner = function.value
    return (isinstance(owner, ast.Attribute) and owner.attr == 'path'
            and isinstance(owner.value, ast.Name)
            and owner.value.id == 'os')


def _path_kind(node, owned_names, trusted_names, mutated_names=(),
               context=None, depth=0):
    if isinstance(node, int):
        return node
    if isinstance(node, ast.Name):
        return owned_names.get(node.id, _UNKNOWN_PATH)
    if isinstance(node, ast.Constant):
        return _literal_path_kind(node.value)
    if isinstance(node, ast.Subscript):
        if (isinstance(node.value, ast.Name)
                and node.value.id in mutated_names):
            return _UNKNOWN_PATH
        return (_CONTROL_OWNED_PATH
                if _path_kind(node.value, owned_names, trusted_names,
                              mutated_names, context, depth)
                == _CONTROL_OWNED_PATH else _UNKNOWN_PATH)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _path_kind(node.left, owned_names, trusted_names,
                          mutated_names, context, depth)
        right = _path_kind(node.right, owned_names, trusted_names,
                           mutated_names, context, depth)
        return left if right == _RELATIVE_PATH else _UNKNOWN_PATH
    if isinstance(node, ast.IfExp):
        body = _path_kind(node.body, owned_names, trusted_names,
                          mutated_names, context, depth)
        other = _path_kind(node.orelse, owned_names, trusted_names,
                           mutated_names, context, depth)
        return body if body == other else _UNKNOWN_PATH
    if not isinstance(node, ast.Call):
        return _UNKNOWN_PATH
    if (isinstance(node.func, ast.Name)
            and node.func.id in {'Path', 'str'}
            and node.func.id in trusted_names and len(node.args) == 1):
        return _path_kind(node.args[0], owned_names, trusted_names,
                          mutated_names, context, depth)
    if 'os' in trusted_names and _os_path_join(node) and node.args:
        first = _path_kind(node.args[0], owned_names, trusted_names,
                           mutated_names, context, depth)
        rest = [_path_kind(part, owned_names, trusted_names, mutated_names,
                           context, depth)
                for part in node.args[1:]]
        return (first if first != _UNKNOWN_PATH
                and all(part == _RELATIVE_PATH for part in rest)
                else _UNKNOWN_PATH)
    if context is None:
        return _UNKNOWN_PATH
    return _proved_call_kind(node, context, owned_names, trusted_names,
                             mutated_names, depth)


def _annotations(arguments):
    """Every annotation a signature evaluates when its def runs."""
    parameters = (arguments.posonlyargs + arguments.args
                  + arguments.kwonlyargs
                  + [part for part in (arguments.vararg, arguments.kwarg)
                     if part is not None])
    return [parameter.annotation for parameter in parameters
            if parameter.annotation is not None]


def _nested_scope_expressions(node):
    """What a nested scope's header evaluates in the enclosing scope."""
    if isinstance(node, ast.ClassDef):
        yield from node.decorator_list
        yield from node.bases
        yield from (keyword.value for keyword in node.keywords)
        return
    if not isinstance(node, ast.Lambda):
        yield from node.decorator_list
        yield from _annotations(node.args)
        if node.returns is not None:
            yield node.returns
    yield from node.args.defaults
    yield from (value for value in node.args.kw_defaults
                if value is not None)


def _scope_nodes(scope):
    roots = [scope.body] if isinstance(scope, ast.Lambda) else scope.body
    pending: list[ast.AST] = list(reversed(roots))
    while pending:
        node = pending.pop()
        if isinstance(node, SCOPES):
            yield node
            pending.extend(reversed(list(_nested_scope_expressions(node))))
            continue
        yield node
        pending.extend(reversed(list(ast.iter_child_nodes(node))))


def _scope_local_names(scope):
    """Names bound in this lexical scope, excluding nested bodies."""
    names = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        arguments = (scope.args.posonlyargs + scope.args.args
                     + scope.args.kwonlyargs)
        names.update(argument.arg for argument in arguments)
        if scope.args.vararg is not None:
            names.add(scope.args.vararg.arg)
        if scope.args.kwarg is not None:
            names.add(scope.args.kwarg.arg)
    for node in _scope_nodes(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split('.')[0]
                         for alias in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        else:
            names.update(pattern_names(node))
    return names


def _returned_values(scope):
    """Every expression this scope returns, tuples flattened.

    Empty where it returns nothing, or returns with no value: neither is
    provable, and the caller reads empty as unprovable.
    """
    values = []
    for node in _scope_nodes(scope):
        if not isinstance(node, ast.Return):
            continue
        if node.value is None:
            return []
        if isinstance(node.value, (ast.Tuple, ast.List)):
            values.extend(node.value.elts)
        else:
            values.append(node.value)
    return values


def _proved_call_kind(call, context, owned, trusted_names, mutated, depth):
    """The kind of a call's result, proved from the callee's own body.

    A helper is trusted for what it returns, never for what it is called:
    a copy helper edited to return a checkout path stops being proof the
    moment its body says so.
    """
    if depth >= _MAX_PROOF_DEPTH or not isinstance(call.func, ast.Name):
        return _UNKNOWN_PATH
    function, inner = context.resolve(call.func.id)
    if function is None:
        return _UNKNOWN_PATH
    seeded = _seeded_parameters(call, function, owned, trusted_names,
                                mutated, context, depth)
    values = _returned_values(function)
    if seeded is None or not values:
        return _UNKNOWN_PATH
    inner_owned, inner_mutated = _owned_path_names(
        function, inner, seeded, depth + 1)
    inner_trusted = {'Path', 'str', 'os'} - _scope_local_names(function)
    if all(_path_kind(value, inner_owned, inner_trusted, inner_mutated,
                      context, depth + 1) == _CONTROL_OWNED_PATH
           for value in values):
        return _CONTROL_OWNED_PATH
    return _UNKNOWN_PATH


def _seeded_parameters(call, function, owned, trusted, mutated, context,
                       depth):
    """Kinds for the callee's parameters from this call's arguments.

    A defaulted parameter the caller omits is seeded from its own default,
    the expression the callee would actually read.
    """
    arguments = function.args
    parameters = (arguments.posonlyargs + arguments.args
                  + arguments.kwonlyargs)
    if (has_spread(call) or arguments.vararg is not None
            or arguments.kwarg is not None
            or len(call.args) > len(parameters)):
        return None
    plain = arguments.posonlyargs + arguments.args
    defaults = {parameter.arg: default for parameter, default in zip(
        plain[len(plain) - len(arguments.defaults):], arguments.defaults)}
    defaults.update(
        {parameter.arg: default
         for parameter, default in zip(arguments.kwonlyargs,
                                       arguments.kw_defaults)
         if default is not None})
    seeded = {}
    for index, parameter in enumerate(parameters):
        value = argument(call, parameter.arg, index)
        if value is not None:
            seeded[parameter.arg] = _path_kind(
                value, owned, trusted, mutated, context, depth)
            continue
        if parameter.arg not in defaults:
            return None
        # A default is evaluated in its definition scope, which no call
        # site's environment can stand in for; only a literal proves.
        default = defaults[parameter.arg]
        seeded[parameter.arg] = (
            _literal_path_kind(default.value)
            if isinstance(default, ast.Constant) else _UNKNOWN_PATH)
    return seeded


def _closure_names(scope):
    """Names a nested scope reaches without binding them itself.

    A class body's bindings are invisible to the functions defined in
    it, comprehensions included, so they shield nothing below the class.
    """
    local = _scope_local_names(scope)
    in_class = isinstance(scope, ast.ClassDef)
    shield = set() if in_class else local
    nodes = list(_scope_nodes(scope))
    reads = {id(node.value) for node in nodes
             if isinstance(node, ast.Subscript)}
    names = set()
    for node in nodes:
        if (isinstance(node, ast.Name) and node.id not in local
                and id(node) not in reads):
            names.add(node.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, SCOPES[1:]):
            names |= _closure_names(node) - shield
        elif in_class and isinstance(node, COMPREHENSIONS):
            bound = {part.id for generator in node.generators
                     for part in _target_name_nodes(generator.target)}
            names.update(
                part.id for part in ast.walk(node)
                if isinstance(part, ast.Name)
                and isinstance(part.ctx, ast.Load)
                and part.id not in bound)
    return names


def _escaped_names(scope):
    """Names read anywhere but through their own subscript.

    Reaching a container through another name is reaching the same
    object, so a name that is ever handed on — bound, passed, listed,
    reached from a nested scope, or declared global or nonlocal — no
    longer proves what it holds.
    """
    nodes = list(_scope_nodes(scope))
    reads = {id(node.value) for node in nodes
             if isinstance(node, ast.Subscript)}
    escaped = {node.id for node in nodes
               if isinstance(node, ast.Name)
               and isinstance(node.ctx, ast.Load)
               and id(node) not in reads}
    for node in nodes:
        if isinstance(node, SCOPES[1:]):
            escaped |= _closure_names(node)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            escaped.update(node.names)
    return escaped


def _mutated_container_names(scope, bindings):
    """Names whose elements a subscript can no longer prove.

    Ownership of a container is a claim about what it holds, and any
    mutation of it — an assignment into it or a call through one of its own
    methods — retires the claim, whatever the method is named. A name bound
    to another name shares that object, so it never proves an element.
    """
    mutated = {name for name, sources in bindings.items()
               if any(isinstance(source, ast.Name) for source in sources)}
    for node in _scope_nodes(scope):
        mutated.update(_subscript_bases(node))
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)):
            mutated.add(node.func.value.id)
    return mutated | _escaped_names(scope)


def _owned_path_names(scope, functions=None, seeded=None, depth=0):
    bindings = {}
    for name, kind in (seeded or {}).items():
        bindings.setdefault(name, []).append(kind)
    local_names = _scope_local_names(scope)
    context = functions
    trusted_names = {'Path', 'str', 'os'} - local_names
    modelled = set()
    for node in _scope_nodes(scope):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            for target in node.targets:
                _record_binding(bindings, target, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            _record_binding(
                bindings, node.target, node.value)
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
            _record_binding(bindings, node.target, _UNKNOWN_PATH)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
            _record_binding(bindings, node.target, node.iter)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            targets = [node.optional_vars]
            _record_binding(
                bindings, node.optional_vars, node.context_expr)
        for target in targets:
            modelled.update(id(name) for name in _target_name_nodes(target))
        # An element written into an owned container can be a checkout
        # path, so the container stops being proof of anything.
        for name in _subscript_bases(node):
            bindings.setdefault(name, []).append(_UNKNOWN_PATH)
    # A binding form this pass does not model — a comprehension target, a
    # walrus, an except name — must clear ownership rather than inherit it.
    for node in _scope_nodes(scope):
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                and id(node) not in modelled):
            bindings.setdefault(node.id, []).append(_UNKNOWN_PATH)
        for name in pattern_names(node):
            bindings.setdefault(name, []).append(_UNKNOWN_PATH)
    mutated = _mutated_container_names(scope, bindings)
    # A name resolves to one kind only when every binding agrees on it, so
    # a relative literal stays relative and a contested name stays unknown.
    kinds = {}
    changed = True
    while changed:
        changed = False
        for name, sources in bindings.items():
            if name in kinds:
                continue
            resolved = {_path_kind(source, kinds, trusted_names, mutated,
                                   context, depth) for source in sources}
            if len(resolved) == 1 and _UNKNOWN_PATH not in resolved:
                kinds[name] = resolved.pop()
                changed = True
    return kinds, mutated
