"""Side-effect-free constant resolution for the CLI argument audit.

The resolver follows supplied in-function imports and unshadowed handler
globals by object identity. It decides module attributes, module ``__dict__``,
exact-class attributes read directly from MRO dictionaries, signed-integer
list/tuple subscripts, constant-key dict subscripts and
``.get(key[, default])``, and constant-name ``getattr``. Single-argument
``vars`` is resolved on exact modules and classes; a class yields a mapping
proxy, whose downstream reads remain outside the exact-dict rules.
Exact ``staticmethod`` and ``classmethod`` wrappers are unwrapped; every other
descriptor stays raw, so its ``__get__`` is never invoked. The caller refuses
unresolved canonical ``_getframe`` and ``currentframe`` spellings.

The resolver never runs handler-defined code. Descriptors other than the two
exact wrappers, ``functools.partial``, exception traceback frames, other
container types, the iterator protocol, comprehension results, instance
attributes, ``operator.attrgetter`` and runtime-built names stay outside
frame-route resolution. Frame acquisition in another function is
categorically outside because the audit inspects only the handler's own body.
The module-level case tables enumerate the permitted reads, refused escapes,
decided frame routes, resolver-only controls and known outside families used
by the focused tests.
"""
import ast
import inspect
import sys

# ``do_tabs`` reads args.json through getattr(); keep that access documented
# even though the audit resolves constant indirect reads itself.
KNOWN_INDIRECT_ARG_READS = (
    ('tabs', 'json', 'do_tabs'),
)

SAFE_UNDECLARED_PROBES = (
    "getattr(args, 'undeclared_probe', None)",
    "hasattr(args, 'undeclared_probe')",
)

SHADOWING_DEFAULT_CASES = (
    'def inner(args=args):\n    return args.undeclared_probe\ninner()',
    'f = lambda args=args: args.undeclared_probe\nf()',
)

PERMITTED_NAMESPACE_READ_CASES = (
    ('args.json', 'args.undeclared_probe'),
    ("getattr(args, 'json')", "getattr(args, 'undeclared_probe')"),
    ("getattr(args, 'json', False)",
     "getattr(args, 'undeclared_probe', None)"),
    ("vars(args)['json']", "vars(args)['undeclared_probe']"),
    ("args.__dict__['json']", "args.__dict__['undeclared_probe']"),
    ("vars(args).get('json')",
     "vars(args).get('undeclared_probe')"),
    ("args.__dict__.get('json')",
     "args.__dict__.get('undeclared_probe')"),
    ("vars(args).get('json', False)",
     "vars(args).get('undeclared_probe', None)"),
    ("args.__dict__.get('json', False)",
     "args.__dict__.get('undeclared_probe', None)"),
    ("hasattr(args, 'json')", "hasattr(args, 'undeclared_probe')"),
)

NAMESPACE_ESCAPE_CASES = (
    ('other = args', 'other = args'),
    ('other = args\nthird = other\nthird.x', 'other = args'),
    ('other, = (args,)', '(args,)'),
    ("getattr(*(args, 'x'))", "(args, 'x')"),
    ('helper(args)', 'helper(args)'),
    ('helper([args])', '[args]'),
    ('helper(*[args])', '[args]'),
    ('getattr(args, some_variable)', 'getattr(args, some_variable)'),
    ('hasattr(args, some_variable)', 'hasattr(args, some_variable)'),
    ('helper(vars(args))', 'vars(args)'),
    ('args.__dict__', 'args.__dict__'),
)

REFLECTIVE_ESCAPE_CASES = (
    ("_ = locals()['args'].undeclared_probe", 'locals()'),
    ("_ = eval('args.undeclared_probe')", "eval('args.undeclared_probe')"),
    ('_ = vars()', 'vars()'),
    ('_ = globals()', 'globals()'),
    ("exec('args.undeclared_probe')", "exec('args.undeclared_probe')"),
    ("_ = sys._getframe().f_locals['args'].x", 'sys._getframe()'),
    ("_ = inspect.currentframe().f_locals['args'].x",
     'inspect.currentframe()'),
    ("from sys import _getframe\n"
     "_ = _getframe().f_locals['args'].x", '_getframe()'),
    ("from sys import _getframe as get_frame\n"
     "_ = get_frame().f_locals['args'].x", 'get_frame()'),
    ("from inspect import currentframe as cf\n"
     "_ = cf().f_locals['args'].x", 'cf()'),
    ("import sys as system\n"
     "_ = system._getframe().f_locals['args'].x", 'system._getframe()'),
    ("import inspect as insp\n"
     "_ = insp.currentframe().f_locals['args'].x",
     'insp.currentframe()'),
    ('_ = sys._getframe', 'sys._getframe'),
    ('_ = sys._getframe.__call__()', 'sys._getframe.__call__()'),
    ('helper(sys._getframe)', 'sys._getframe'),
    ('helper(inspect.currentframe)', 'inspect.currentframe'),
    ('_ = _getframe()', '_getframe()'),
    ('_ = currentframe()', 'currentframe()'),
)

DECIDED_FRAME_ROUTE_CASES = (
    ('from sys import _getframe as get_frame',
     "_ = get_frame().f_locals['args'].undeclared_probe", 'get_frame()'),
    ('', "_ = sys._getframe.__call__().f_locals['args'].undeclared_probe",
     'sys._getframe.__call__()'),
    ('from inspect import currentframe as cf',
     "_ = cf().f_locals['args'].undeclared_probe", 'cf()'),
    ('', "_ = getattr(sys, '_getframe')().f_locals['args']"
     ".undeclared_probe", "getattr(sys, '_getframe')()"),
    ('FRAME_ROUTES = [sys._getframe]',
     "_ = FRAME_ROUTES[0]().f_locals['args'].undeclared_probe",
     'FRAME_ROUTES[0]()'),
    ('TAB_FRAME_ROUTES = (sys._getframe,)',
     "_ = TAB_FRAME_ROUTES[0]().f_locals['args'].undeclared_probe",
     'TAB_FRAME_ROUTES[0]()'),
    ("FRAME_ROUTES = {'active': sys._getframe}",
     "_ = FRAME_ROUTES['active']().f_locals['args'].undeclared_probe",
     "FRAME_ROUTES['active']()"),
    ("FRAME_ROUTES = {'active': sys._getframe}",
     "_ = FRAME_ROUTES.get('active')()"
     ".f_locals['args'].undeclared_probe",
     "FRAME_ROUTES.get('active')()"),
    ("FRAME_ROUTES = {'active': sys._getframe}",
     "_ = FRAME_ROUTES.get('active', None)()"
     ".f_locals['args'].undeclared_probe",
     "FRAME_ROUTES.get('active', None)()"),
    ('FRAME_ROUTES = [sys._getframe]',
     "_ = FRAME_ROUTES[-1]().f_locals['args'].undeclared_probe",
     'FRAME_ROUTES[-1]()'),
    ('FRAME_ROUTES = (sys._getframe,)',
     "_ = FRAME_ROUTES[-1]().f_locals['args'].undeclared_probe",
     'FRAME_ROUTES[-1]()'),
    ('FRAME_ROUTES = (sys._getframe,)',
     "_ = FRAME_ROUTES[+0]().f_locals['args'].undeclared_probe",
     'FRAME_ROUTES[+0]()'),
    ("FRAME_ROUTES = {-1: sys._getframe}",
     "_ = FRAME_ROUTES[-1]().f_locals['args'].undeclared_probe",
     'FRAME_ROUTES[-1]()'),
    ('class FrameRoutes:\n    active = sys._getframe',
     "_ = FrameRoutes.active().f_locals['args'].undeclared_probe",
     'FrameRoutes.active()'),
    ('class FrameRoutes:\n    active = staticmethod(sys._getframe)',
     "_ = FrameRoutes.active().f_locals['args'].undeclared_probe",
     'FrameRoutes.active()'),
    ('', "_ = sys.__dict__['_getframe']()"
     ".f_locals['args'].undeclared_probe", "sys.__dict__['_getframe']()"),
    ('', "_ = sys.__dict__.get('_getframe')()"
     ".f_locals['args'].undeclared_probe",
     "sys.__dict__.get('_getframe')()"),
    ('', "_ = vars(sys)['_getframe']()"
     ".f_locals['args'].undeclared_probe", "vars(sys)['_getframe']()"),
)

RESOLVER_ONLY_FRAME_ROUTE_CASES = (
    ('exact classmethod route (resolver only)',
     'class FrameRoutes:\n'
     '    active = classmethod(sys._getframe)',
     "_ = FrameRoutes.active().f_locals['args'].undeclared_probe",
     'FrameRoutes.active()'),
    ('unresolved currentframe spelling (resolver only)', '',
     "_ = currentframe().f_locals['args'].undeclared_probe",
     'currentframe()'),
)

KNOWN_GAP_FRAME_ROUTE_CASES = (
    ('comprehension result',
     'FRAME_ROUTES = [sys._getframe]',
     "_ = [route for route in FRAME_ROUTES][0]()"
     ".f_locals['args'].undeclared_probe"),
    ('instance attribute',
     'class FrameRoutes:\n    active = sys._getframe',
     "_ = FrameRoutes().active().f_locals['args'].undeclared_probe"),
    ('custom descriptor class attribute',
     'class RouteDescriptor:\n'
     '    def __get__(self, obj, objtype=None):\n'
     '        return sys._getframe\n'
     'class FrameRoutes:\n'
     '    active = RouteDescriptor()',
     "_ = FrameRoutes.active().f_locals['args'].undeclared_probe"),
    ('property descriptor',
     'class FrameRoutes:\n'
     '    @property\n'
     '    def active(self):\n'
     '        return sys._getframe',
     "_ = FrameRoutes().active().f_locals['args'].undeclared_probe"),
    ('cached-property descriptor',
     'import functools\n'
     'class FrameRoutes:\n'
     '    @functools.cached_property\n'
     '    def active(self):\n'
     '        return sys._getframe',
     "_ = FrameRoutes().active().f_locals['args'].undeclared_probe"),
    ('staticmethod subclass descriptor',
     'class RouteStaticmethod(staticmethod):\n'
     '    pass\n'
     'class FrameRoutes:\n'
     '    active = RouteStaticmethod(sys._getframe)',
     "_ = FrameRoutes.active().f_locals['args'].undeclared_probe"),
    ('functools.partial',
     'import functools\n'
     'FRAME_ROUTE = functools.partial(sys._getframe)',
     "_ = FRAME_ROUTE().f_locals['args'].undeclared_probe"),
    ('exception traceback frame', '',
     "try:\n"
     "        raise RuntimeError('probe')\n"
     "    except RuntimeError as err:\n"
     "        _ = err.__traceback__.tb_frame.f_locals['args']"
     ".undeclared_probe"),
    ('frame acquisition in another function',
     'def caller_namespace():\n'
     "    return sys._getframe(1).f_locals['args']",
     '_ = caller_namespace().undeclared_probe'),
    ('other container type',
     'import collections\n'
     'FRAME_ROUTES = collections.deque((sys._getframe,))',
     "_ = FRAME_ROUTES[0]().f_locals['args'].undeclared_probe"),
    ('iterator protocol',
     'FRAME_ROUTES = (sys._getframe,)',
     "_ = next(iter(FRAME_ROUTES))()"
     ".f_locals['args'].undeclared_probe"),
    ('operator.attrgetter',
     'import operator',
     "_ = operator.attrgetter('_getframe')(sys)()"
     ".f_locals['args'].undeclared_probe"),
    ('runtime-built name', '',
     "_ = getattr(sys, '_get' + 'frame')()"
     ".f_locals['args'].undeclared_probe"),
)

_FRAME_ROUTE_OBJECTS = (sys._getframe, inspect.currentframe)


def constant_string(node):
    """Return the string of a constant node, or None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def is_frame_route(value):
    """Return True only for the canonical frame-route objects by identity."""
    return any(value is route for route in _FRAME_ROUTE_OBJECTS)

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
