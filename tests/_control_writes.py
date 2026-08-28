"""Fail-closed path ownership checks for mutation controls."""
import ast
from pathlib import Path

_UNKNOWN_PATH = 0
_RELATIVE_PATH = 1
_CONTROL_OWNED_PATH = 2
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


def _real_copy_call(node):
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == '_real_module_copy')


def _record_binding(bindings, target, value):
    if isinstance(target, ast.Name):
        bindings.setdefault(target.id, []).append(value)
        return
    if isinstance(target, ast.Starred):
        _record_binding(bindings, target.value, value)
        return
    if not isinstance(target, (ast.List, ast.Tuple)):
        return
    if _real_copy_call(value):
        for name in _bound_names(target):
            bindings.setdefault(name, []).append(_CONTROL_OWNED_PATH)
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


def _literal_path_kind(value):
    if not isinstance(value, str):
        return _UNKNOWN_PATH
    path = Path(value)
    if path.is_absolute() or '..' in path.parts:
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


def _path_kind(node, owned_names):
    if isinstance(node, int):
        return node
    if isinstance(node, ast.Name):
        return (_CONTROL_OWNED_PATH if node.id in owned_names
                else _UNKNOWN_PATH)
    if isinstance(node, ast.Constant):
        return _literal_path_kind(node.value)
    if isinstance(node, ast.Subscript):
        return (_CONTROL_OWNED_PATH
                if _path_kind(node.value, owned_names)
                == _CONTROL_OWNED_PATH else _UNKNOWN_PATH)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _path_kind(node.left, owned_names)
        right = _path_kind(node.right, owned_names)
        return left if right == _RELATIVE_PATH else _UNKNOWN_PATH
    if isinstance(node, ast.IfExp):
        body = _path_kind(node.body, owned_names)
        other = _path_kind(node.orelse, owned_names)
        return body if body == other else _UNKNOWN_PATH
    if not isinstance(node, ast.Call):
        return _UNKNOWN_PATH
    if (isinstance(node.func, ast.Name)
            and node.func.id in {'Path', 'str'} and len(node.args) == 1):
        return _path_kind(node.args[0], owned_names)
    if _os_path_join(node) and node.args:
        first = _path_kind(node.args[0], owned_names)
        rest = [_path_kind(part, owned_names) for part in node.args[1:]]
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
            pending.extend(reversed(list(_nested_scope_expressions(node))))
            continue
        yield node
        pending.extend(reversed(list(ast.iter_child_nodes(node))))


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
    for node in _scope_nodes(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                _record_binding(bindings, target, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            _record_binding(bindings, node.target, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _record_binding(bindings, node.target, node.iter)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            _record_binding(
                bindings, node.optional_vars, node.context_expr)
    owned = set()
    changed = True
    while changed:
        changed = False
        for name, sources in bindings.items():
            if name in owned:
                continue
            if all(_path_kind(source, owned) == _CONTROL_OWNED_PATH
                   for source in sources):
                owned.add(name)
                changed = True
    return owned


def _write_mode(node):
    mode = node.args[1] if len(node.args) > 1 else None
    for keyword in node.keywords:
        if keyword.arg == 'mode':
            mode = keyword.value
    if isinstance(mode, ast.Constant) and mode.value in _WRITE_MODES:
        return mode.value
    return None


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
            elif (isinstance(node.func, ast.Name)
                  and node.func.id == 'open' and node.args):
                mode = _write_mode(node)
                if mode is not None:
                    kind = f'open mode {mode!r}'
                    target = node.args[0]
            if (target is not None
                    and _path_kind(target, owned_names)
                    != _CONTROL_OWNED_PATH):
                violations.append(
                    (node.lineno, f'{label}:{node.lineno}: {kind} target '
                     'path is not control-owned'))
    return [message for _, message in sorted(violations)]
