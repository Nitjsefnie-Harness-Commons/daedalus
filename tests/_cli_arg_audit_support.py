"""Side-effect-free constant resolution for the CLI argument audit.

Frame routes are resolved through in-function imports, global names, module
attributes, exact-class attributes (including ``staticmethod`` and
``classmethod``), constant list/tuple/dict subscripts, constant-key ``.get``
on an exact dict or module ``__dict__``, and constant-name ``getattr``/``vars``
calls. Unresolved ``_getframe`` and ``currentframe`` spellings are refused
outright. Every other route is outside this audit, including other container
types, the iterator protocol, comprehension results, instance attribute
reads, ``operator.attrgetter``, and names built at runtime.
"""
import ast
import sys

# Exact type checks and direct MRO dictionary lookup avoid handler-defined
# descriptors while preserving staticmethod and classmethod routes.
# pylint: disable=unidiomatic-typecheck


def _constant_value(node, unresolved):
    if isinstance(node, ast.Constant):
        return node.value
    if (isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.UAdd, ast.USub))
            and isinstance(node.operand, ast.Constant)
            and type(node.operand.value) is int):
        if isinstance(node.op, ast.USub):
            return -node.operand.value
        return node.operand.value
    return unresolved


def _static_attribute(base, attribute, unresolved):
    if type(base) is type(sys) and attribute == '__dict__':
        return base.__dict__
    if type(base) is type:
        for owner in base.__mro__:
            namespace = owner.__dict__
            if attribute not in namespace:
                continue
            value = namespace[attribute]
            if type(value) is staticmethod:
                return value.__func__
            if type(value) is classmethod:
                return value.__func__
            return value
        return unresolved
    if type(base) is type(sys):
        return base.__dict__.get(attribute, unresolved)
    return unresolved


def _static_subscript(base, key, unresolved):
    if type(base) is list and isinstance(key, int):
        try:
            return base[key]
        except IndexError:
            return unresolved
    if type(base) is tuple and isinstance(key, int):
        try:
            return base[key]
        except IndexError:
            return unresolved
    if type(base) is dict:
        return base.get(key, unresolved)
    return unresolved


def _static_get(node, function, handler_globals, imports, unresolved,
                scope_binds, constant_string):
    if (not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != 'get'
            or len(node.args) not in (1, 2)
            or node.keywords):
        return unresolved
    key = _constant_value(node.args[0], unresolved)
    if key is unresolved:
        return unresolved
    base = resolve_frame_value(
        node.func.value, function, handler_globals, imports, unresolved,
        scope_binds, constant_string)
    if type(base) is not dict:
        return unresolved
    if key in base:
        return base[key]
    if len(node.args) == 2:
        default = _constant_value(node.args[1], unresolved)
        if default is not unresolved:
            return default
        return resolve_frame_value(
            node.args[1], function, handler_globals, imports, unresolved,
            scope_binds, constant_string)
    return unresolved


def _builtin_call(node, function, handler_globals, imports, unresolved,
                  scope_binds, constant_string):
    static_get = _static_get(
        node, function, handler_globals, imports, unresolved,
        scope_binds, constant_string)
    if static_get is not unresolved:
        return static_get
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
