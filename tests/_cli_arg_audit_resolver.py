"""Static argparse, builtin-identity, and constant-resolution helpers.

Expression resolution has a total two-way partition. The explicit
``DECIDED_EXPRESSION_TYPES`` registry names the syntax this resolver handles;
every other ``ast.expr`` type is OUTSIDE by definition. Comparisons and tuple
literals stay OUTSIDE because reproducing their Python semantics would widen
the trusted evaluator. An OUTSIDE key that could select a canonical frame
route therefore fails closed.
"""
import argparse
import ast
import builtins
import inspect
import sys
from pathlib import Path


_FRAME_ROUTE_OBJECTS = (sys._getframe, inspect.currentframe)
_OUTSIDE_EXPRESSION = object()

EXPRESSION_DECIDED = 'DECIDED'
EXPRESSION_OUTSIDE = 'OUTSIDE'
DECIDED_EXPRESSION_TYPES = frozenset({
    ast.Attribute,
    ast.Call,
    ast.Constant,
    ast.Name,
    ast.Slice,
    ast.Subscript,
    ast.UnaryOp,
})


def _expression_node_types():
    """Return the expression-type universe exposed by this interpreter."""
    return frozenset({
        value for value in vars(ast).values()
        if isinstance(value, type)
        and value is not ast.expr
        and issubclass(value, ast.expr)})


def expression_type_disposition(node_type):
    """Return DECIDED or OUTSIDE for one expression node type."""
    if (not isinstance(node_type, type)
            or node_type is ast.expr
            or not issubclass(node_type, ast.expr)):
        raise TypeError('expected a concrete ast.expr node type')
    if node_type in DECIDED_EXPRESSION_TYPES:
        return EXPRESSION_DECIDED
    return EXPRESSION_OUTSIDE


def is_outside_expression(value):
    """Return whether resolution refused an OUTSIDE expression."""
    return value is _OUTSIDE_EXPRESSION


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


def _current_module_expression(node):
    return (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == 'modules'
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == 'sys'
            and isinstance(node.slice, ast.Name)
            and node.slice.id == '__name__')


def _module_binding_write(node):
    if (not isinstance(node, (ast.Attribute, ast.Subscript))
            or not isinstance(node.ctx, (ast.Store, ast.Del))):
        return None
    if isinstance(node, ast.Attribute) \
            and _current_module_expression(node.value):
        return node.attr
    if (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == '__dict__'
            and _current_module_expression(node.value.value)):
        return constant_string(node.slice)
    return None


def _statement_binding_writes(statement):
    names = set()
    module_names = set()
    builtin_module_attributes = set()
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
        module_binding = _module_binding_write(node)
        if module_binding is not None:
            module_names.add(module_binding)
        if (isinstance(node, ast.Attribute)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and isinstance(node.value, ast.Name)):
            builtin_module_attributes.add((node.value.id, node.attr))
        if isinstance(node, ast.Name) \
                and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        stack.extend(ast.iter_child_nodes(node))
    return names, module_names, builtin_module_attributes


def _update_builtin_bindings(statement, bindings, handler_globals,
                             unresolved, function, scope_binds):
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
    names, module_names, builtin_module_attributes = \
        _statement_binding_writes(statement)
    for name in names:
        bindings[name] = unresolved
    for name in module_names:
        if name not in bindings or not scope_binds(function, name):
            bindings[name] = unresolved
    for name, attribute in builtin_module_attributes:
        value = bindings.get(name, handler_globals.get(name, unresolved))
        if value is builtins:
            bindings[(name, attribute)] = unresolved


def _statement_prefixes(node, function):
    """Return enclosing statement prefixes from outermost to innermost."""
    prefixes = []
    current = node
    while current is not function:
        parent = current._parent
        for _, children in ast.iter_fields(parent):
            if (isinstance(children, list)
                    and current in children
                    and all(isinstance(child, ast.stmt)
                            for child in children)):
                prefixes.append(children[:children.index(current)])
                break
        current = parent
    return reversed(prefixes)


def _builtin_bindings_at(node, function, handler_globals, unresolved,
                         scope_binds):
    """Return builtin imports and invalidations at one call site."""
    bindings = {}
    for prefix in _statement_prefixes(node, function):
        for statement in prefix:
            _update_builtin_bindings(
                statement, bindings, handler_globals, unresolved,
                function, scope_binds)
    return bindings


def _execution_callable(node, function):
    current = node
    callables = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    while current is not function:
        parent = current._parent
        if (isinstance(parent, callables)
                and _callable_body_contains(parent, current)):
            return parent
        current = parent
    return function


def _captured_callable(node, function):
    owner = _execution_callable(node, function)
    return None if owner is function else owner


def _direct_invocations(captured, function):
    if not isinstance(captured, ast.FunctionDef):
        return ()
    references = tuple(
        node for node in ast.walk(function)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == captured.name
        and node.lineno > captured.end_lineno
        and _execution_callable(node, function) is function)
    if (not references
            or any(not isinstance(node._parent, ast.Call)
                   or node._parent.func is not node
                   for node in references)):
        return ()
    rebound = any(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and node.id == captured.name
        and node.lineno > captured.end_lineno
        for node in ast.walk(function))
    if rebound:
        return ()
    return tuple(node._parent for node in references)


def _captured_identity_is_exact(node, reference_name, expected, function,
                                handler_globals, unresolved, scope_binds):
    if not scope_binds(function, reference_name):
        return True
    captured = _captured_callable(node, function)
    if captured is None:
        return True
    invocations = _direct_invocations(captured, function)
    if not invocations:
        return False
    return all(
        _builtin_bindings_at(
            call, function, handler_globals, unresolved, scope_binds
        ).get(reference_name, unresolved) is expected
        for call in invocations)


def is_builtin_reference(node, name, function, handler_globals,
                         scope_binds, comprehension_shadows):
    """Return whether ``node`` provably names one exact builtin."""
    expected = getattr(builtins, name)
    unresolved = object()
    bindings = _builtin_bindings_at(
        node, function, handler_globals, unresolved, scope_binds)
    if isinstance(node, ast.Name):
        reference_name = node.id
        exact_local = bindings.get(reference_name) is expected
        if _builtin_name_is_shadowed(
                node, reference_name, function, scope_binds,
                comprehension_shadows, exact_local):
            return False
        if not _captured_identity_is_exact(
                node, reference_name, expected, function,
                handler_globals, unresolved, scope_binds):
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
    if bindings.get((module_name, node.attr)) is unresolved:
        return False
    exact_local = bindings.get(module_name) is builtins
    if _builtin_name_is_shadowed(
            node, module_name, function, scope_binds,
            comprehension_shadows, exact_local):
        return False
    if not _captured_identity_is_exact(
            node, module_name, builtins, function,
            handler_globals, unresolved, scope_binds):
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
    if (isinstance(node, ast.expr)
            and expression_type_disposition(type(node))
            == EXPRESSION_OUTSIDE):
        return _OUTSIDE_EXPRESSION
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Slice):
        bounds = []
        for bound in (node.lower, node.upper, node.step):
            value = None if bound is None else _constant_value(
                bound, unresolved)
            if is_outside_expression(value):
                return value
            if (value is unresolved
                    or (value is not None
                        and not _is_integer_index(value))):
                return unresolved
            bounds.append(value)
        return slice(*bounds)
    if isinstance(node, ast.UnaryOp):
        value = _constant_value(node.operand, unresolved)
        if value is unresolved or is_outside_expression(value):
            return value
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


def _contains_frame_route(container):
    """Return whether one exact built-in container holds a frame route."""
    if type(container) in (list, tuple):
        values = container
    elif type(container) is dict:
        values = container.values()
    else:
        return False
    return any(is_frame_route(value) for value in values)


def _static_get(node, function, handler_globals, imports, unresolved,
                scope_binds, string_resolver):
    if (not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != 'get'
            or len(node.args) not in (1, 2)
            or node.keywords):
        return unresolved
    key = _constant_value(node.args[0], unresolved)
    base = resolve_frame_value(
        node.func.value, function, handler_globals, imports, unresolved,
        scope_binds, string_resolver)
    if is_outside_expression(key):
        if _contains_frame_route(base):
            return _OUTSIDE_EXPRESSION
        return unresolved
    if key is unresolved:
        return unresolved
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
        if is_outside_expression(key):
            base = resolve_frame_value(
                node.value, function, handler_globals, imports, unresolved,
                scope_binds, string_resolver)
            if _contains_frame_route(base):
                return _OUTSIDE_EXPRESSION
            return unresolved
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


def assert_total_expression_partition():
    """Exercise the dynamic DECIDED/OUTSIDE expression partition."""
    universe = _expression_node_types()
    classified = {
        node_type: expression_type_disposition(node_type)
        for node_type in universe}
    decided = {
        node_type for node_type, disposition in classified.items()
        if disposition == EXPRESSION_DECIDED}
    outside = {
        node_type for node_type, disposition in classified.items()
        if disposition == EXPRESSION_OUTSIDE}
    assert DECIDED_EXPRESSION_TYPES <= universe
    assert decided == set(DECIDED_EXPRESSION_TYPES)
    assert decided.isdisjoint(outside)
    assert decided | outside == set(universe)
    assert set(classified.values()) == {
        EXPRESSION_DECIDED, EXPRESSION_OUTSIDE}
    assert {ast.Compare, ast.Tuple} <= outside


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
