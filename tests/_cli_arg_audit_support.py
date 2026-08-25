"""Side-effect-free constant resolution for the CLI argument audit."""
import ast
import sys

# Exact type checks keep resolution from invoking handler-defined descriptors.
# pylint: disable=unidiomatic-typecheck


def _constant_value(node, unresolved):
    if isinstance(node, ast.Constant):
        return node.value
    return unresolved


def _static_attribute(base, attribute, unresolved):
    if type(base) is type(sys) and attribute == '__dict__':
        return base.__dict__
    if type(base) in (type(sys), type):
        return base.__dict__.get(attribute, unresolved)
    return unresolved


def _static_subscript(base, key, unresolved):
    if type(base) is list and isinstance(key, int):
        try:
            return base[key]
        except IndexError:
            return unresolved
    if type(base) is dict:
        return base.get(key, unresolved)
    return unresolved


def _builtin_call(node, function, handler_globals, imports, unresolved,
                  scope_binds, constant_string):
    if (not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Name)
            or node.keywords
            or any(isinstance(arg, ast.Starred) for arg in node.args)):
        return unresolved
    name = node.func.id
    if (scope_binds(function, name) or name in handler_globals
            or not node.args):
        return unresolved
    base = resolve_frame_value(
        node.args[0], function, handler_globals, imports, unresolved,
        scope_binds, constant_string)
    if name == 'getattr' and len(node.args) in (2, 3):
        attribute = constant_string(node.args[1])
        if attribute is not None:
            return _static_attribute(base, attribute, unresolved)
    if name == 'vars' and len(node.args) == 1:
        if type(base) in (type(sys), type):
            return base.__dict__
    return unresolved


def resolve_frame_value(node, function, handler_globals, imports, unresolved,
                        scope_binds, constant_string):
    """Resolve only literal access through exact built-in containers."""
    if isinstance(node, ast.Name):
        if node.id in imports:
            return imports[node.id]
        if scope_binds(function, node.id):
            return unresolved
        return handler_globals.get(node.id, unresolved)
    if isinstance(node, ast.Attribute):
        base = resolve_frame_value(
            node.value, function, handler_globals, imports, unresolved,
            scope_binds, constant_string)
        return _static_attribute(base, node.attr, unresolved)
    if isinstance(node, ast.Subscript):
        key = _constant_value(node.slice, unresolved)
        if key is not unresolved:
            base = resolve_frame_value(
                node.value, function, handler_globals, imports, unresolved,
                scope_binds, constant_string)
            return _static_subscript(base, key, unresolved)
    if isinstance(node, ast.Call):
        return _builtin_call(
            node, function, handler_globals, imports, unresolved,
            scope_binds, constant_string)
    return unresolved
