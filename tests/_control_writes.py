"""Fail-closed path ownership checks for mutation controls."""
import ast
from pathlib import Path, PurePosixPath, PureWindowsPath

_UNKNOWN_PATH = 0
_RELATIVE_PATH = 1
_CONTROL_OWNED_PATH = 2
_UNRESOLVED_MODE = object()
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


def _real_copy_call(node, trusted):
    return (trusted and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == '_real_module_copy')


def _record_binding(bindings, target, value, trusted_copy=True):
    if isinstance(target, ast.Name):
        bindings.setdefault(target.id, []).append(value)
        return
    if isinstance(target, ast.Starred):
        _record_binding(bindings, target.value, value, trusted_copy)
        return
    if not isinstance(target, (ast.List, ast.Tuple)):
        return
    if _real_copy_call(value, trusted_copy):
        for name in _bound_names(target):
            bindings.setdefault(name, []).append(_CONTROL_OWNED_PATH)
        return
    if isinstance(value, (ast.List, ast.Tuple)):
        plain = [part for part in target.elts
                 if not isinstance(part, ast.Starred)]
        if len(plain) == len(target.elts) == len(value.elts):
            for part, source in zip(target.elts, value.elts):
                _record_binding(bindings, part, source, trusted_copy)
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


def _path_kind(node, owned_names, trusted_names):
    if isinstance(node, int):
        return node
    if isinstance(node, ast.Name):
        return (_CONTROL_OWNED_PATH if node.id in owned_names
                else _UNKNOWN_PATH)
    if isinstance(node, ast.Constant):
        return _literal_path_kind(node.value)
    if isinstance(node, ast.Subscript):
        return (_CONTROL_OWNED_PATH
                if _path_kind(node.value, owned_names, trusted_names)
                == _CONTROL_OWNED_PATH else _UNKNOWN_PATH)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _path_kind(node.left, owned_names, trusted_names)
        right = _path_kind(node.right, owned_names, trusted_names)
        return left if right == _RELATIVE_PATH else _UNKNOWN_PATH
    if isinstance(node, ast.IfExp):
        body = _path_kind(node.body, owned_names, trusted_names)
        other = _path_kind(node.orelse, owned_names, trusted_names)
        return body if body == other else _UNKNOWN_PATH
    if not isinstance(node, ast.Call):
        return _UNKNOWN_PATH
    if (isinstance(node.func, ast.Name)
            and node.func.id in {'Path', 'str'}
            and node.func.id in trusted_names and len(node.args) == 1):
        return _path_kind(node.args[0], owned_names, trusted_names)
    if 'os' in trusted_names and _os_path_join(node) and node.args:
        first = _path_kind(node.args[0], owned_names, trusted_names)
        rest = [_path_kind(part, owned_names, trusted_names)
                for part in node.args[1:]]
        return (first if first != _UNKNOWN_PATH
                and all(part == _RELATIVE_PATH for part in rest)
                else _UNKNOWN_PATH)
    return _UNKNOWN_PATH


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


def _owned_path_names(scope):
    bindings = {}
    arguments = []
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        arguments = (scope.args.posonlyargs + scope.args.args
                     + scope.args.kwonlyargs)
    is_control = (isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and scope.name.startswith('test_'))
    if is_control and any(argument.arg == 'tmp' for argument in arguments):
        bindings['tmp'] = [_CONTROL_OWNED_PATH]
    local_names = _scope_local_names(scope)
    trusted_copy = '_real_module_copy' not in local_names
    trusted_names = {'Path', 'str', 'os'} - local_names
    modelled = set()
    for node in _scope_nodes(scope):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            for target in node.targets:
                _record_binding(bindings, target, node.value, trusted_copy)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            _record_binding(
                bindings, node.target, node.value, trusted_copy)
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
            _record_binding(bindings, node.target, _UNKNOWN_PATH)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
            _record_binding(bindings, node.target, node.iter, trusted_copy)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            targets = [node.optional_vars]
            _record_binding(
                bindings, node.optional_vars, node.context_expr,
                trusted_copy)
        for target in targets:
            modelled.update(id(name) for name in _target_name_nodes(target))
    # A binding form this pass does not model — a comprehension target, a
    # walrus, an except name — must clear ownership rather than inherit it.
    for node in _scope_nodes(scope):
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                and id(node) not in modelled):
            bindings.setdefault(node.id, []).append(_UNKNOWN_PATH)
    owned = set()
    changed = True
    while changed:
        changed = False
        for name, sources in bindings.items():
            if name in owned:
                continue
            if all(_path_kind(source, owned, trusted_names)
                   == _CONTROL_OWNED_PATH
                   for source in sources):
                owned.add(name)
                changed = True
    return owned


def _write_mode(node, position):
    mode = node.args[position] if len(node.args) > position else None
    for keyword in node.keywords:
        if keyword.arg == 'mode':
            mode = keyword.value
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
    scopes = [tree]
    scopes.extend(node for node in ast.walk(tree)
                  if isinstance(node, _SCOPES[1:]))
    for scope in scopes:
        local_names = _scope_local_names(scope)
        owned_names = _owned_path_names(scope)
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
                  and node.func.id == 'open' and node.args):
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
                    target = node.args[0]
            if (target is not None
                    and _path_kind(
                        target, owned_names,
                        {'Path', 'str', 'os'} - local_names)
                    != _CONTROL_OWNED_PATH):
                violations.append(
                    (node.lineno, f'{label}:{node.lineno}: {kind} target '
                     'path is not control-owned'))
    return [message for _, message in sorted(violations)]
