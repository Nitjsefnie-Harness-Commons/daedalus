"""Scope and repository-root facts the coverage guard is judged against.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/_coverage_guard.py: these answer "what does this name
mean here", which is a different question from "is this launch safe".
"""
import ast
from pathlib import PurePosixPath, PureWindowsPath


def _is_root_spelling(node, shadowed_names=frozenset()):
    """Whether an expression provably names the repository root."""
    if any(isinstance(part, ast.Name) and part.id in shadowed_names
           for part in ast.walk(node)):
        return False
    if isinstance(node, ast.Name) and node.id == 'ROOT':
        return True
    if (isinstance(node, ast.Attribute) and node.attr == 'ROOT'
            and isinstance(node.value, ast.Name)
            and node.value.id in {'_util', 'behaviour'}):
        return True
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and _is_relative_literal(node.right.value)):
        return _is_root_spelling(node.left, shadowed_names)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'str' and len(node.args) == 1
            and not node.keywords):
        return _is_root_spelling(node.args[0], shadowed_names)
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


def _scope_shadows(tree):
    """Shadowed names per scope, attributed to the scope that binds them.

    A name bound inside one function cannot reach another's expressions, so
    a launch is judged against its own scope chain rather than against every
    binding in the module.
    """
    shadows = {tree: set()}

    def record(scope, node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                shadows[scope].add(child.name)
                shadows[child] = set()
                if not isinstance(child, ast.ClassDef):
                    arguments = (child.args.posonlyargs + child.args.args
                                 + child.args.kwonlyargs)
                    shadows[child].update(
                        argument.arg for argument in arguments)
                    shadows[child].update(
                        extra.arg for extra in
                        (child.args.vararg, child.args.kwarg)
                        if extra is not None)
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
