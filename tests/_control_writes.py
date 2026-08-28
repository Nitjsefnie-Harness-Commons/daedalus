"""Fail-closed path ownership checks for mutation controls."""
import ast
from pathlib import Path, PurePosixPath, PureWindowsPath

_UNKNOWN_PATH = 0
_RELATIVE_PATH = 1
_CONTROL_OWNED_PATH = 2
_UNRESOLVED_MODE = object()
_MAX_PROOF_DEPTH = 2
_READ_MODES = frozenset({'r', 'rb', 'rt'})
_WRITE_MODES = frozenset({'w', 'a', 'x', 'wb', 'ab', 'xb'})
_SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
           ast.ClassDef, ast.Lambda)


def _bound_names(target):
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Starred):
        yield from _bound_names(target.value)
    elif isinstance(target, (ast.List, ast.Tuple)):
        for part in target.elts:
            yield from _bound_names(part)


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
    for name in _bound_names(target):
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


def _nested_scope_expressions(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        yield from node.decorator_list
        yield from node.args.defaults
        yield from (value for value in node.args.kw_defaults
                    if value is not None)
    elif isinstance(node, ast.ClassDef):
        yield from node.decorator_list
        yield from node.bases
        yield from (keyword.value for keyword in node.keywords)


def _scope_nodes(scope):
    roots = [scope.body] if isinstance(scope, ast.Lambda) else scope.body
    pending = list(reversed(roots))
    while pending:
        node = pending.pop()
        if isinstance(node, _SCOPES):
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
    return names


def _module_functions(tree):
    """The module-level functions a call in this file can resolve to."""
    return {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


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
    function = context.get(call.func.id)
    if function is None or call.func.id not in context:
        return _UNKNOWN_PATH
    parameters = (function.args.posonlyargs + function.args.args
                  + function.args.kwonlyargs)
    if (function.args.vararg is not None or function.args.kwarg is not None
            or len(call.args) > len(parameters)):
        return _UNKNOWN_PATH
    seeded = {}
    for index, parameter in enumerate(parameters):
        value = _argument(call, parameter.arg, index)
        if value is None:
            return _UNKNOWN_PATH
        seeded[parameter.arg] = _path_kind(
            value, owned, trusted_names, mutated, context, depth)
    values = _returned_values(function)
    if not values:
        return _UNKNOWN_PATH
    inner_owned, inner_mutated = _owned_path_names(
        function, context, seeded, depth + 1)
    inner_trusted = {'Path', 'str', 'os'} - _scope_local_names(function)
    if all(_path_kind(value, inner_owned, inner_trusted, inner_mutated,
                      context, depth + 1) == _CONTROL_OWNED_PATH
           for value in values):
        return _CONTROL_OWNED_PATH
    return _UNKNOWN_PATH


def _mutated_container_names(scope):
    """Names whose elements a subscript can no longer prove.

    Ownership of a container is a claim about what it holds, and any
    mutation of it — an assignment into it or a call through one of its own
    methods — retires the claim, whatever the method is named.
    """
    mutated = set()
    for node in _scope_nodes(scope):
        mutated.update(_subscript_bases(node))
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)):
            mutated.add(node.func.value.id)
    return mutated


def _owned_path_names(scope, functions=None, seeded=None, depth=0):
    bindings = {}
    arguments = []
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        arguments = (scope.args.posonlyargs + scope.args.args
                     + scope.args.kwonlyargs)
    is_control = (isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and scope.name.startswith('test_'))
    if is_control and any(argument.arg == 'tmp' for argument in arguments):
        bindings['tmp'] = [_CONTROL_OWNED_PATH]
    for name, kind in (seeded or {}).items():
        bindings.setdefault(name, []).append(kind)
    local_names = _scope_local_names(scope)
    context = {name: function for name, function in (functions or {}).items()
               if name not in local_names}
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
    mutated = _mutated_container_names(scope)
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


def _argument(call, name, position):
    """A call's argument by keyword, else by position, else None."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def _write_mode(node, position):
    mode = _argument(node, 'mode', position)
    if mode is None:
        return None
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        if mode.value in _WRITE_MODES:
            return mode.value
        if mode.value in _READ_MODES:
            return None
    return _UNRESOLVED_MODE


def control_write_violations(source, repository_root):
    """Return every write whose target is not provably control-owned."""
    source = Path(source)
    tree = ast.parse(source.read_text(encoding='utf-8'))
    try:
        label = source.relative_to(repository_root).as_posix()
    except ValueError:
        label = source.name
    violations = []
    functions = _module_functions(tree)
    scopes = [tree]
    scopes.extend(node for node in ast.walk(tree)
                  if isinstance(node, _SCOPES[1:]))
    for scope in scopes:
        local_names = _scope_local_names(scope)
        owned_names, mutated_names = _owned_path_names(scope, functions)
        context = {name: function for name, function in functions.items()
                   if name not in local_names}
        for node in _scope_nodes(scope):
            if not isinstance(node, ast.Call):
                continue
            kind = None
            target = None
            if (isinstance(node.func, ast.Attribute)
                    and node.func.attr in {'write_bytes', 'write_text'}):
                kind = node.func.attr
                target = node.func.value
            elif (isinstance(node.func, ast.Attribute)
                  and node.func.attr == 'open'):
                mode = _write_mode(node, 0)
                if mode is _UNRESOLVED_MODE:
                    violations.append(
                        (node.lineno, f'{label}:{node.lineno}: '
                         'Path.open mode is unresolved'))
                elif mode is not None:
                    kind = f'Path.open mode {mode!r}'
                    target = node.func.value
            elif (isinstance(node.func, ast.Name)
                  and node.func.id == 'open'):
                if 'open' in local_names:
                    violations.append(
                        (node.lineno, f'{label}:{node.lineno}: '
                         'open callable is unresolved'))
                    continue
                mode = _write_mode(node, 1)
                if mode is _UNRESOLVED_MODE:
                    violations.append(
                        (node.lineno, f'{label}:{node.lineno}: '
                         'open mode is unresolved'))
                elif mode is not None:
                    kind = f'open mode {mode!r}'
                    target = _argument(node, 'file', 0)
                    if target is None:
                        violations.append(
                            (node.lineno, f'{label}:{node.lineno}: '
                             'open target is unresolved'))
                        continue
            if (target is not None
                    and _path_kind(
                        target, owned_names,
                        {'Path', 'str', 'os'} - local_names, mutated_names,
                        context)
                    != _CONTROL_OWNED_PATH):
                violations.append(
                    (node.lineno, f'{label}:{node.lineno}: {kind} target '
                     'path is not control-owned'))
    return [message for _, message in sorted(violations)]
