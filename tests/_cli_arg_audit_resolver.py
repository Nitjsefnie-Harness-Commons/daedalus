"""Static argparse, builtin-identity, and constant-resolution helpers."""
import argparse
import ast
import builtins
import inspect
import sys
from pathlib import Path


_FRAME_ROUTE_OBJECTS = (sys._getframe, inspect.currentframe)


def namespace_dests(parser):
    """Return destinations declared and guaranteed on successful parses."""
    never_store = (argparse._HelpAction, argparse._VersionAction)
    actions = [
        action for action in parser._actions
        if action.dest != argparse.SUPPRESS
        and not isinstance(action, never_store)]
    defaults = set(parser._defaults)
    declared = {action.dest for action in actions} | defaults
    guaranteed = {
        action.dest for action in actions
        if (action.default is not argparse.SUPPRESS
            or action.required
            or (not action.option_strings
                and action.nargs == argparse.REMAINDER))}
    required_group_dests = set()
    for group in parser._mutually_exclusive_groups:
        destinations = {action.dest for action in group._group_actions}
        if (group.required
                and len(destinations) == 1
                and argparse.SUPPRESS not in destinations
                and not any(isinstance(action, never_store)
                            for action in group._group_actions)):
            required_group_dests |= destinations
    return declared, guaranteed | required_group_dests | defaults


def constant_string(node):
    """Return the string of a constant node, or None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _constant_mapping_read(node):
    """Return (attribute, node, needs-presence) for an exact-dict read."""
    parent = node._parent
    if (isinstance(parent, ast.Subscript) and parent.value is node
            and isinstance(parent.ctx, ast.Load)):
        attribute = constant_string(parent.slice)
        if attribute is not None:
            return attribute, parent, True
    if (not isinstance(parent, ast.Attribute)
            or parent.value is not node
            or parent.attr != 'get'):
        return None
    call = parent._parent
    if (not isinstance(call, ast.Call)
            or call.func is not parent
            or len(call.args) not in (1, 2)
            or call.keywords
            or any(isinstance(argument, ast.Starred)
                   for argument in call.args)):
        return None
    attribute = constant_string(call.args[0])
    if attribute is not None:
        return attribute, call, False
    return None


def _callable_body_contains(scope, child):
    if isinstance(scope, ast.Lambda):
        return child is scope.body
    return child in scope.body


def _builtin_name_is_shadowed(node, name, function, scope_binds,
                              comprehension_shadows, ignore_root=False):
    current = node
    generator, before_target = None, False
    callables = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp,
                      ast.GeneratorExp)
    while current is not function:
        parent = current._parent
        if isinstance(parent, ast.comprehension):
            generator, before_target = parent, current is parent.iter
        if (isinstance(parent, comprehensions)
                and comprehension_shadows(
                    parent, name, generator, before_target)):
            return True
        if isinstance(parent, comprehensions):
            generator = None
        if (isinstance(parent, callables)
                and _callable_body_contains(parent, current)
                and not (ignore_root and parent is function)
                and scope_binds(parent, name)):
            return True
        current = parent
    return False


def _statement_bound_names(statement):
    names = set()
    stack = [statement]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.add(node.name)
            continue
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {
                alias.asname or alias.name.split('.')[0]
                for alias in node.names}
            continue
        if isinstance(node, ast.ExceptHandler) and node.name is not None:
            names.add(node.name)
        if isinstance(node, ast.Name) \
                and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _update_builtin_bindings(statement, bindings, unresolved):
    if isinstance(statement, ast.Import):
        for alias in statement.names:
            name = alias.asname or alias.name.split('.')[0]
            bindings[name] = (
                builtins if alias.name == 'builtins' else unresolved)
        return
    if isinstance(statement, ast.ImportFrom):
        for alias in statement.names:
            name = alias.asname or alias.name
            bindings[name] = (
                builtins.__dict__.get(alias.name, unresolved)
                if statement.module == 'builtins' else unresolved)
        return
    for name in _statement_bound_names(statement):
        bindings[name] = unresolved


def _builtin_bindings_before(node, function, unresolved):
    """Return straight-line builtin imports and invalidations before node."""
    bindings = {}
    for statement in function.body:
        if any(candidate is node for candidate in ast.walk(statement)):
            break
        _update_builtin_bindings(statement, bindings, unresolved)
    return bindings


def is_builtin_reference(node, name, function, handler_globals,
                         scope_binds, comprehension_shadows):
    """Return whether ``node`` provably names one exact builtin."""
    expected = getattr(builtins, name)
    unresolved = object()
    bindings = _builtin_bindings_before(node, function, unresolved)
    if isinstance(node, ast.Name):
        reference_name = node.id
        exact_local = bindings.get(reference_name) is expected
        if _builtin_name_is_shadowed(
                node, reference_name, function, scope_binds,
                comprehension_shadows, exact_local):
            return False
        value = resolve_frame_value(
            node, function, handler_globals, bindings, unresolved,
            scope_binds, constant_string)
        if value is not unresolved:
            return value is expected
        if reference_name != name:
            return False
        namespace = handler_globals.get('__builtins__', builtins)
        namespace = (namespace if isinstance(namespace, dict) else
                     namespace.__dict__
                     if type(namespace) is type(builtins) else {})
        return namespace.get(name) is expected
    if (not isinstance(node, ast.Attribute)
            or node.attr != name
            or not isinstance(node.value, ast.Name)):
        return False
    module_name = node.value.id
    exact_local = bindings.get(module_name) is builtins
    if _builtin_name_is_shadowed(
            node, module_name, function, scope_binds,
            comprehension_shadows, exact_local):
        return False
    value = resolve_frame_value(
        node, function, handler_globals, bindings, unresolved,
        scope_binds, constant_string)
    return value is expected


def reflective_builtin_call(node, function, handler_globals, scope_binds,
                            comprehension_shadows):
    """Return whether a call must be treated as reflective."""
    if not isinstance(node, ast.Call):
        return False
    names = ('locals', 'globals', 'eval', 'exec')
    if (isinstance(node.func, ast.Name)
            and (node.func.id in names
                 or (node.func.id == 'vars' and not node.args))):
        return True
    if not node.args:
        names += ('vars',)
    return any(
        is_builtin_reference(
            node.func, name, function, handler_globals,
            scope_binds, comprehension_shadows)
        for name in names)


def permitted_namespace_read(name, function, handler_globals, scope_binds,
                             comprehension_shadows):
    """Resolve a permitted namespace read, or None for an escape."""
    parent = name._parent
    if isinstance(parent, ast.Attribute) and parent.value is name:
        if not isinstance(parent.ctx, ast.Load):
            return None
        if parent.attr != '__dict__':
            return parent.attr, parent, True
        return _constant_mapping_read(parent)
    if not (isinstance(parent, ast.Call)
            and parent.args and parent.args[0] is name
            and not parent.keywords):
        return None
    if (len(parent.args) == 1
            and is_builtin_reference(
                parent.func, 'vars', function, handler_globals,
                scope_binds, comprehension_shadows)):
        return _constant_mapping_read(parent)
    builtin_name = next(
        (candidate for candidate in ('getattr', 'hasattr')
         if is_builtin_reference(
             parent.func, candidate, function, handler_globals,
             scope_binds, comprehension_shadows)), None)
    arities = {'getattr': (2, 3), 'hasattr': (2,)}.get(builtin_name)
    if (not arities or len(parent.args) not in arities
            or any(isinstance(argument, ast.Starred)
                   for argument in parent.args)):
        return None
    attribute = constant_string(parent.args[1])
    if attribute is None:
        return None
    needs_presence = builtin_name == 'getattr' and len(parent.args) == 2
    return attribute, parent, needs_presence


def is_frame_route(value):
    """Return True only for the canonical frame-route objects by identity."""
    return any(value is route for route in _FRAME_ROUTE_OBJECTS)


# Exact type/MRO reads avoid descriptors but preserve static/class methods.
# pylint: disable=unidiomatic-typecheck


def _is_integer_index(value):
    """Return True for an ``int`` instance, including ``bool``."""
    return isinstance(value, int)


def _constant_value(node, unresolved):
    """Resolve constants, slices, and all four Python unary operators.

    ``UAdd`` and ``USub`` sign an integer, ``Invert`` complements an integer,
    and ``Not`` converts any resolved literal to ``bool``. Operators recurse;
    unsupported operands and nodes remain unresolved.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Slice):
        bounds = []
        for bound in (node.lower, node.upper, node.step):
            value = None if bound is None else _constant_value(
                bound, unresolved)
            if (value is unresolved
                    or (value is not None
                        and not _is_integer_index(value))):
                return unresolved
            bounds.append(value)
        return slice(*bounds)
    if isinstance(node, ast.UnaryOp):
        value = _constant_value(node.operand, unresolved)
        if value is unresolved:
            return unresolved
        if isinstance(node.op, ast.Not):
            return not value
        if not _is_integer_index(value):
            return unresolved
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.Invert):
            return ~int(value)
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
    if (type(base) in (list, tuple)
            and (_is_integer_index(key) or type(key) is slice)):
        try:
            return base[key]
        except (IndexError, TypeError, ValueError):
            return unresolved
    if type(base) is dict:
        try:
            return base.get(key, unresolved)
        except TypeError:
            return unresolved
    return unresolved


def _static_get(node, function, handler_globals, imports, unresolved,
                scope_binds, string_resolver):
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
        scope_binds, string_resolver)
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
            scope_binds, string_resolver)
    return unresolved


def _builtin_call(node, function, handler_globals, imports, unresolved,
                  scope_binds, string_resolver):
    static_get = _static_get(
        node, function, handler_globals, imports, unresolved,
        scope_binds, string_resolver)
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
        scope_binds, string_resolver)
    if name == 'getattr' and len(node.args) in (2, 3):
        attribute = string_resolver(node.args[1])
        if attribute is not None:
            return _static_attribute(base, attribute, unresolved)
    if name == 'vars' and len(node.args) == 1:
        if type(base) in (type(sys), type):
            return base.__dict__
    return unresolved


def resolve_frame_value(node, function, handler_globals, imports, unresolved,
                        scope_binds, string_resolver):
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
            scope_binds, string_resolver)
        return _static_attribute(base, node.attr, unresolved)
    if isinstance(node, ast.Subscript):
        key = _constant_value(node.slice, unresolved)
        if key is not unresolved:
            base = resolve_frame_value(
                node.value, function, handler_globals, imports, unresolved,
                scope_binds, string_resolver)
            return _static_subscript(base, key, unresolved)
    if isinstance(node, ast.Call):
        return _builtin_call(
            node, function, handler_globals, imports, unresolved,
            scope_binds, string_resolver)
    return unresolved


def assert_dict_get_default(frame_value):
    """Exercise integer constants, slices, and exact-dict defaults."""
    unresolved = object()
    for expression, expected in (('+True', 1), ('-True', -1),
                                 ('+False', 0)):
        value = _constant_value(
            ast.parse(expression, mode='eval').body, unresolved)
        assert (type(value), value) == (int, expected), expression
    resolved = _constant_value(
        ast.parse('routes[:+True]', mode='eval').body.slice, unresolved)
    assert (type(resolved.stop), resolved.stop) == (int, 1)
    invalid = ast.parse("routes['not-an-index':]", mode='eval').body.slice
    assert _constant_value(invalid, unresolved) is unresolved
    function = ast.parse(
        "def do_tabs(args):\n"
        "    return ROUTES.get('active', DEFAULT_ROUTE)\n").body[0]
    call = function.body[0].value
    handler_globals = {
        'ROUTES': {'active': sys._getframe},
        'DEFAULT_ROUTE': inspect.currentframe,
    }
    assert frame_value(
        call, function, handler_globals, {}) is sys._getframe
    handler_globals['ROUTES'] = {}
    assert frame_value(
        call, function, handler_globals, {}) is inspect.currentframe
    literal_function = ast.parse(
        "def do_tabs(args):\n"
        "    return ROUTES.get('active', None)\n").body[0]
    literal_call = literal_function.body[0].value
    assert frame_value(
        literal_call, literal_function, {'ROUTES': {}}, {}) is None


def assert_logical_not_returns_bool():
    """Exercise ``Not`` over every representative literal category."""
    unresolved = object()
    cases = (('not 1.0', False), ("not ''", True), ('not None', True))
    for expression, expected in cases:
        node = ast.parse(expression, mode='eval').body
        value = _constant_value(node, unresolved)
        assert (type(value), value) == (bool, expected), expression


def assert_every_unary_operator():
    """Exercise the finite unary family and its recursive compositions."""
    unresolved = object()
    operator_cases = (
        ('+0', ast.UAdd, int, 0),
        ('-0', ast.USub, int, 0),
        ('~0', ast.Invert, int, -1),
        ('not 0', ast.Not, bool, True),)
    assert {operator for _, operator, _, _ in operator_cases} == \
        set(ast.unaryop.__subclasses__())
    combination_cases = (
        ('~-1', int, 0),
        ('-~0', int, 1),
        ('not not 0', bool, False),
        ('+True', int, 1),
        ('-True', int, -1),
        ('~False', int, -1),
        ('not False', bool, True),)
    for expression, operator, expected_type, expected in operator_cases:
        node = ast.parse(expression, mode='eval').body
        assert isinstance(node.op, operator), expression
        value = _constant_value(node, unresolved)
        assert (type(value), value) == (expected_type, expected), expression
    for expression, expected_type, expected in combination_cases:
        node = ast.parse(expression, mode='eval').body
        value = _constant_value(node, unresolved)
        assert (type(value), value) == (expected_type, expected), expression
    slice_cases = (
        ('routes[:~0]', slice(None, -1, None)),
        ('routes[-~0:]', slice(1, None, None)),
        ('routes[:(not 0)]', slice(None, True, None)),
        ('routes[(not not 0):]', slice(False, None, None)),)
    for expression, expected in slice_cases:
        node = ast.parse(expression, mode='eval').body.slice
        value = _constant_value(node, unresolved)
        assert value == expected, expression


def assert_exact_class_vars(frame_value):
    """Exercise exact class namespace reads without invoking descriptors."""
    class FrameRoutes:
        active = sys._getframe

    function = ast.parse(
        "def do_tabs(args):\n"
        "    return vars(FrameRoutes)\n").body[0]
    call = function.body[0].value
    value = frame_value(
        call, function, {'FrameRoutes': FrameRoutes}, {})
    assert isinstance(value, type(FrameRoutes.__dict__))
    assert value['active'] is sys._getframe


def assert_inner_scope_bindings(audit_handler):
    """Exercise shadowed parameters and live closure references."""
    shadowed = (
        'def inner(args):\n'
        '    args.undeclared_probe\n'
        'shadow = lambda args: args.undeclared_probe')
    assert audit_handler(shadowed) == []
    closure = 'def inner():\n    args.undeclared_probe'
    assert audit_handler(closure) == ['args.undeclared_probe']


def assert_no_dead_imports(path):
    """Require every top-level import in one module to be loaded."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    imported = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            imported |= {
                alias.asname or alias.name.split('.')[0]
                for alias in statement.names}
        elif isinstance(statement, ast.ImportFrom):
            imported |= {
                alias.asname or alias.name for alias in statement.names}
    loaded = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    unused = sorted(imported - loaded)
    assert unused == [], unused


def assert_docstrings_match(documents, rule_phrases, known_gap_families,
                            known_gap_cases):
    """Require both contract prose and controls to cover each other."""
    missing_claims = {}
    for module, document in documents.items():
        normalized = ' '.join(document.split())
        missing = [
            phrase for phrase in rule_phrases
            if phrase not in normalized]
        missing += [
            family for family, _ in known_gap_families
            if family not in normalized]
        if missing:
            missing_claims[module] = missing
    assert missing_claims == {}, missing_claims
    controls = {case[0] for case in known_gap_cases}
    mapped = [
        control for _, family_controls in known_gap_families
        for control in family_controls]
    duplicates = sorted({
        control for control in mapped if mapped.count(control) > 1})
    assert duplicates == [], duplicates
    assert set(mapped) == controls, {
        'unmapped': sorted(controls - set(mapped)),
        'unknown': sorted(set(mapped) - controls),
    }
