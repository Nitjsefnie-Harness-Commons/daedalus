"""Side-effect-free static analysis for the CLI argument audit.

DECLARED contains each non-help/version action whose destination is not
``argparse.SUPPRESS``, plus every ``set_defaults`` key. GUARANTEED PRESENT
contains a destination when an action has a non-suppressed default, is required
on every successful parse, or is a positional ``REMAINDER``; every parser
default is also guaranteed. Same-destination actions combine those properties.
Direct attributes, exact-dict subscripts, and bare ``getattr`` need GUARANTEED;
``hasattr``, defaulted ``getattr``, and exact-dict ``.get`` need DECLARED.

``getattr``, ``hasattr``, and ``vars`` receive builtin semantics only by exact
identity. A bare name must be unbound by callable/comprehension scopes
and absent from module globals or bound there to that builtin. Attribute bases
must be the exact ``builtins`` module. Callable headers use outer scope. Bare
``locals``/``globals``/``eval``/``exec`` and no-argument ``vars`` are refused
by spelling even when shadowed (fail-closed); exact ``builtins`` forms are too.

The resolver follows supplied imports and unshadowed globals by identity. It
decides module/class attributes, module ``__dict__``, exact list/tuple
subscripts/slices and constant-key dict reads. Indices and bounds pass
``isinstance(value, int)``; ``bool`` counts, and unary ``+True`` is ``1``.
Bare constant-name ``getattr`` and one-argument ``vars`` resolve exact modules
and classes. Exact static/class methods unwrap; other descriptors stay raw.
Unresolved canonical ``_getframe``/``currentframe`` spellings are refused.

Resolution never runs handler code. Non-exact descriptors, ``partial``,
traceback frames, other containers, iterators, comprehension results, instance
attributes, ``attrgetter``, runtime names, class mapping-proxy reads, binary or
call-produced indices, and frame acquisition in another function stay outside.
The tables include a known-gap control for every named outside family.
"""
import argparse
import ast
import builtins
import inspect
import sys

# ``do_tabs`` reads args.json through getattr(); keep that access documented
# even though the audit resolves constant indirect reads itself.
KNOWN_INDIRECT_ARG_READS = (
    ('tabs', 'json', 'do_tabs'),
)

PERMITTED_NAMESPACE_READ_CASES = (
    ('args.json', 'args.undeclared_probe', True),
    ("getattr(args, 'json')", "getattr(args, 'undeclared_probe')", True),
    ("getattr(args, 'json', False)",
     "getattr(args, 'undeclared_probe', None)", False),
    ("vars(args)['json']", "vars(args)['undeclared_probe']", True),
    ("args.__dict__['json']", "args.__dict__['undeclared_probe']", True),
    ("vars(args).get('json')",
     "vars(args).get('undeclared_probe')", False),
    ("args.__dict__.get('json')",
     "args.__dict__.get('undeclared_probe')", False),
    ("vars(args).get('json', False)",
     "vars(args).get('undeclared_probe', None)", False),
    ("args.__dict__.get('json', False)",
     "args.__dict__.get('undeclared_probe', None)", False),
    ("hasattr(args, 'json')", "hasattr(args, 'undeclared_probe')", False),
    ("builtins.getattr(args, 'json', False)",
     "builtins.getattr(args, 'undeclared_probe', None)", False),
    ("builtins.hasattr(args, 'json')",
     "builtins.hasattr(args, 'undeclared_probe')", False),
    ("builtins.vars(args).get('json')",
     "builtins.vars(args).get('undeclared_probe')", False),
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
    ('def inner(args=args):\n    return args.undeclared_probe\ninner()',
     'args=args'),
    ('f = lambda args=args: args.undeclared_probe\nf()', 'args=args'),
    ("getattr = helper\ngetattr(args, 'json', False)",
     "getattr(args, 'json', False)"),
    ("hasattr = helper\nhasattr(args, 'json')", "hasattr(args, 'json')"),
    ("vars = helper\nvars(args).get('json')", "vars(args)"),
    ("_ = (lambda getattr: getattr(args, 'json', False))(helper)",
     "getattr(args, 'json', False)"),
    ("def inner(getattr):\n"
     "    return getattr(args, 'json', False)\n"
     "inner(helper)", "getattr(args, 'json', False)"),
    ("def inner(getattr=getattr(args, 'json', False)):\n"
     "    return getattr(args, 'json', False)\n"
     "inner()", "getattr(args, 'json', False)"),
    ("_ = [getattr(args, 'json', False) for getattr in helpers]",
     "getattr(args, 'json', False)"),
    ("from operator import attrgetter as getattr\n"
     "getattr(args, 'json', False)", "getattr(args, 'json', False)"),
)

BUILTIN_IDENTITY_GLOBAL_CASES = (
    ('getattr', ()),
    ('len', ("namespace escape: getattr(args, 'json', False)",)),
)

# Shape, argv, empty result, seeded empty result, result, GUARANTEED.
ARGPARSE_STORAGE_CASES = (
    ('optional', (), (), 'seed', (), False),
    ('optional-remainder', ('--probe',), (), 'seed', [], False),
    ('required-option', ('--probe', 'x'), None, None, 'x', True),
    ('remainder', (), [], [], [], True),
    ('star', (), (), 'seed', (), False),
    ('plus', ('x',), None, None, ['x'], True),
    ('question', (), (), 'seed', (), False),
    ('positional', ('x',), None, None, 'x', True),
)

REFLECTIVE_ESCAPE_CASES = (
    ("_ = locals()['args'].undeclared_probe", 'locals()'),
    ("_ = eval('args.undeclared_probe')", "eval('args.undeclared_probe')"),
    ('_ = vars()', 'vars()'),
    ('_ = globals()', 'globals()'),
    ("exec('args.undeclared_probe')", "exec('args.undeclared_probe')"),
    ("_ = builtins.locals()['args'].undeclared_probe", 'builtins.locals()'),
    ("_ = builtins.globals()['args'].undeclared_probe",
     'builtins.globals()'),
    ("_ = builtins.eval('args.undeclared_probe')",
     "builtins.eval('args.undeclared_probe')"),
    ("builtins.exec('args.undeclared_probe')",
     "builtins.exec('args.undeclared_probe')"),
    ('_ = builtins.vars()', 'builtins.vars()'),
    ("locals = helper\n_ = locals()['args'].undeclared_probe", 'locals()'),
    ("globals = helper\n_ = globals()['args'].undeclared_probe",
     'globals()'),
    ("eval = helper\n_ = eval('args.undeclared_probe')",
     "eval('args.undeclared_probe')"),
    ("exec = helper\nexec('args.undeclared_probe')",
     "exec('args.undeclared_probe')"),
    ('vars = helper\n_ = vars()', 'vars()'),
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
    ('BOOL_ROUTES = {True: sys._getframe}',
     "_ = BOOL_ROUTES[+True]().f_locals['args'].undeclared_probe",
     'BOOL_ROUTES[+True]()'),
    ('BOOL_ROUTES = {-1: sys._getframe}',
     "_ = BOOL_ROUTES[-True]().f_locals['args'].undeclared_probe",
     'BOOL_ROUTES[-True]()'),
    ('BOOL_ROUTES = {False: sys._getframe}',
     "_ = BOOL_ROUTES[+False]().f_locals['args'].undeclared_probe",
     'BOOL_ROUTES[+False]()'),
    ('FRAME_ROUTES = [None, sys._getframe]',
     "_ = FRAME_ROUTES[:][-1]().f_locals['args'].undeclared_probe",
     'FRAME_ROUTES[:][-1]()'),
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

COMPOSITE_SUBSCRIPT_FRAME_ROUTE_CASES = (
    'COMPOSITE_ROUTES[--1]',
    'COMPOSITE_ROUTES[---1]',
    'COMPOSITE_ROUTES[+-1]',
    'COMPOSITE_ROUTES[0:2][-1]',
    'COMPOSITE_ROUTES[::2][-1]',
    'COMPOSITE_ROUTES[::-1][-1]',
    'COMPOSITE_ROUTES[:][-1]',
    'COMPOSITE_ROUTES[0:3][1:][-1]',
    'COMPOSITE_ROUTES[-3:--1][-1]',
    'COMPOSITE_ROUTES[+True]',
    'COMPOSITE_ROUTES[-True]',
    'COMPOSITE_ROUTES[+False]',
    'COMPOSITE_ROUTES[True]',
    'COMPOSITE_ROUTES[:+True][-1]',
    'COMPOSITE_ROUTES[-True:][-1]',
    'COMPOSITE_ROUTES[+False:][-1]',
    'COMPOSITE_ROUTES[:True][-1]',
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
    ('frame acquisition in nested helper', '',
     'def caller_namespace():\n'
     "        return sys._getframe(1).f_locals['args']\n"
     '    _ = caller_namespace().undeclared_probe'),
    ('class vars mapping-proxy get',
     'class FrameRoutes:\n    active = sys._getframe',
     "_ = vars(FrameRoutes).get('active')()"
     ".f_locals['args'].undeclared_probe"),
    ('class vars mapping-proxy subscript',
     'class FrameRoutes:\n    active = sys._getframe',
     "_ = vars(FrameRoutes)['active']()"
     ".f_locals['args'].undeclared_probe"),
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
    ('constant binary index',
     'FRAME_ROUTES = (None, None, sys._getframe)',
     "_ = FRAME_ROUTES[1 + 1]().f_locals['args'].undeclared_probe"),
    ('call-produced index',
     'FRAME_ROUTES = (None, sys._getframe)',
     "_ = FRAME_ROUTES[len((None,))]()"
     ".f_locals['args'].undeclared_probe"),
)

_FRAME_ROUTE_OBJECTS = (sys._getframe, inspect.currentframe)


def add_storage_probe(parser, shape, dest='probe'):
    options = {'default': argparse.SUPPRESS}
    if shape in ('optional', 'optional-remainder', 'required-option'):
        option = f'--{dest.replace("_", "-")}'
        options['required'] = shape == 'required-option'
        if shape == 'optional-remainder':
            options['nargs'] = argparse.REMAINDER
        return parser.add_argument(option, **options)
    nargs = {
        'remainder': argparse.REMAINDER, 'star': '*', 'plus': '+',
        'question': '?', 'positional': None}[shape]
    if nargs is None:
        return parser.add_argument(dest, **options)
    return parser.add_argument(dest, nargs=nargs, **options)


def _stored(value):
    return {} if value == () else {'probe': value}


def assert_argparse_storage_contract(audit_handler):
    for case in ARGPARSE_STORAGE_CASES:
        shape, argv, empty, seeded_empty, minimal, is_guaranteed = case
        parser = argparse.ArgumentParser(add_help=False)
        add_storage_probe(parser, shape)
        declared, guaranteed = namespace_dests(parser)
        expected_guaranteed = {'probe'} if is_guaranteed else set()
        assert declared == {'probe'}, shape
        assert guaranteed == expected_guaranteed, shape
        expected_read = [] if is_guaranteed else ['args.probe']
        assert audit_handler(
            'args.probe', declared, guaranteed) == expected_read, shape
        if empty is not None:
            assert vars(parser.parse_args([])) == _stored(empty), shape
        assert vars(parser.parse_args(argv)) == _stored(minimal), shape
        seeded = argparse.ArgumentParser(add_help=False)
        add_storage_probe(seeded, shape)
        seeded.set_defaults(probe='seed')
        assert namespace_dests(seeded) == ({'probe'}, {'probe'}), shape
        if seeded_empty is not None:
            assert vars(seeded.parse_args([])) == _stored(seeded_empty), shape
        seeded_minimal = minimal if argv else seeded_empty
        assert vars(seeded.parse_args(argv)) == _stored(seeded_minimal), shape


def namespace_dests(parser):
    """Return the declared and guaranteed namespace destinations."""
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
    return declared, guaranteed | defaults


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
                              comprehension_shadows):
    current = node
    callables = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp,
                      ast.GeneratorExp)
    while current is not function:
        parent = current._parent
        if (isinstance(parent, comprehensions)
                and comprehension_shadows(parent, name)):
            return True
        if (isinstance(parent, callables)
                and _callable_body_contains(parent, current)
                and scope_binds(parent, name)):
            return True
        current = parent
    return False


def is_builtin_reference(node, name, function, handler_globals,
                         scope_binds, comprehension_shadows):
    """Return whether ``node`` provably names one exact builtin."""
    expected = getattr(builtins, name)
    if isinstance(node, ast.Name):
        if (node.id != name
                or _builtin_name_is_shadowed(
                    node, name, function, scope_binds,
                    comprehension_shadows)):
            return False
        return handler_globals.get(name, expected) is expected
    if (not isinstance(node, ast.Attribute)
            or node.attr != name
            or not isinstance(node.value, ast.Name)):
        return False
    module_name = node.value.id
    if _builtin_name_is_shadowed(
            node, module_name, function, scope_binds,
            comprehension_shadows):
        return False
    return handler_globals.get(module_name) is builtins


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

# Exact type checks and direct MRO dictionary lookup avoid handler-defined
# descriptors while preserving staticmethod and classmethod routes.
# pylint: disable=unidiomatic-typecheck


def _is_integer_index(value):
    """Return True for an ``int`` instance, including ``bool``."""
    return isinstance(value, int)


def _constant_value(node, unresolved):
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
    if (isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.UAdd, ast.USub))):
        value = _constant_value(node.operand, unresolved)
        if not _is_integer_index(value):
            return unresolved
        if isinstance(node.op, ast.USub):
            return -value
        return +value
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
