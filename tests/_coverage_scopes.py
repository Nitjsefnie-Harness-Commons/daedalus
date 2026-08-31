"""Scope and repository-root facts the coverage guard is judged against.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/_coverage_guard.py: these answer "what does this name
mean here", which is a different question from "is this launch safe".
"""
import ast
from pathlib import PurePosixPath, PureWindowsPath


_ROOT_MODULES = frozenset({'_util', 'test_dashboard_behaviour'})


_REFLECTIVE_READS = frozenset({'getattr', 'hasattr'})


def _chain_base(node):
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node


def _reaches_namespace(node):
    """Whether an attribute chain passes a subscript or a dunder name.

    `owner.__dict__[...]`, `owner.__class__` and `owner.x[...]` reach
    the mapping a module's names live in, or an object the checker
    cannot see, so nothing read from the owner afterwards is proved.
    """
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Subscript) or node.attr.startswith('__'):
            return True
        node = node.value
    return False


def _store_targets(node):
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return [node.target]
    if isinstance(node, ast.Delete):
        return list(node.targets)
    return []


def _reads_attribute(call):
    """hasattr/getattr with a literal name read an attribute, no more."""
    return (isinstance(call.func, ast.Name)
            and call.func.id in _REFLECTIVE_READS and len(call.args) > 1
            and isinstance(call.args[1], ast.Constant)
            and isinstance(call.args[1].value, str))


def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _retired_by_store(node, retired):
    """Whether a store retires every owner; named ones go into `retired`."""
    for target in _store_targets(node):
        for part in ast.walk(target):
            if not isinstance(part, (ast.Attribute, ast.Subscript)):
                continue
            base = _chain_base(part)
            names_root = (isinstance(part, ast.Attribute)
                          and part.attr == 'ROOT')
            if isinstance(base, ast.Name):
                if names_root or _reaches_namespace(part):
                    retired.add(base.id)
            elif names_root:
                return True
    return False


def _retires_all_by_setattr(node):
    """setattr/delattr of ROOT on something the module cannot name."""
    if (not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name)
            or node.func.id not in {'setattr', 'delattr'}
            or len(node.args) < 2 or isinstance(node.args[0], ast.Name)):
        return False
    name = node.args[1]
    return not isinstance(name, ast.Constant) or name.value == 'ROOT'


def _escapes(node, parents):
    """Whether an owner name appears other than as an attribute's object."""
    parent = parents.get(node)
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return False
    return not (isinstance(parent, ast.Call) and parent.args
                and parent.args[0] is node and _reads_attribute(parent))


def root_owner_names(tree):
    """Names bound by importing a module whose ROOT is the checkout root.

    An attribute is only a root spelling if the object it reads from is
    one of those modules and the module has been reached no other way:
    not stored through (`x.ROOT = ...`, `x.__dict__[...] = ...`), not
    handed on bare (`setattr(x, ...)`, `vars(x)`, `alias = x`), and not
    read through its namespace (`x.__dict__.update(...)`). A store to a
    `ROOT` the module cannot name at all retires every owner.
    """
    owners = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            owners.update(alias.asname or alias.name for alias in node.names
                          if alias.name in _ROOT_MODULES)
    parents = _parents(tree)
    retired = set()
    for node in ast.walk(tree):
        if _retired_by_store(node, retired) or _retires_all_by_setattr(node):
            return set()
        if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                and _reaches_namespace(node)):
            base = _chain_base(node)
            if isinstance(base, ast.Name):
                retired.add(base.id)
        if (isinstance(node, ast.Name) and node.id in owners
                and isinstance(node.ctx, (ast.Load, ast.Del))
                and _escapes(node, parents)):
            retired.add(node.id)
    return owners - retired


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
    names = {node.id for node in ast.walk(tree)
             if isinstance(node, ast.Name)
             and isinstance(node.ctx, (ast.Store, ast.Del))}
    names.update(node.arg for node in ast.walk(tree)
                 if isinstance(node, ast.arg))
    names.update(node.name for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef,
                                      ast.AsyncFunctionDef, ast.ClassDef)))
    names.update(node.name for node in ast.walk(tree)
                 if isinstance(node, ast.ExceptHandler) and node.name)
    root_values = []
    for node in ast.walk(tree):
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


def _scope_shadows(tree):
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
    shadows = {tree: set()}

    def record(scope, node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                shadows[scope].add(child.name)
                shadows[child] = set()
                if not isinstance(child, ast.ClassDef):
                    shadows[child].update(_parameter_names(child.args))
                record(child, child)
                continue
            if isinstance(child, ast.Lambda):
                shadows[child] = set(_parameter_names(child.args))
                record(child, child)
                continue
            if (isinstance(child, ast.Name)
                    and isinstance(child.ctx, (ast.Store, ast.Del))):
                shadows[scope].add(child.id)
            elif isinstance(child, ast.ExceptHandler) and child.name:
                shadows[scope].add(child.name)
            record(scope, child)

    record(tree, tree)
    return shadows
