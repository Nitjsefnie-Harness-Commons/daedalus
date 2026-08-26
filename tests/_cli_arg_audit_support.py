"""Non-executing audit resolvers, tables, and isolated dispatch controls.
Known-gap controls and documented outside families map in both directions."""
import argparse
import ast
import builtins
import contextlib
import io
import inspect
import os
import sys
from unittest import mock

# Keep do_tabs's indirect args.json read explicit beside the resolved reads.
KNOWN_INDIRECT_ARG_READS = (('tabs', 'json', 'do_tabs'),)
BRIDGE_ENV_NAMES = ('DAEDALUS_URL', 'DAEDALUS_TOKEN', 'TOKEN')
DISPATCH_PROBE_ERROR = 'real dispatch did not read undeclared_probe'
SHADOWING_DEFAULT_CASES = (
    'def inner(args=args):\n    return args.undeclared_probe\ninner()',
    'f = lambda args=args: args.undeclared_probe\nf()',)
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
     "builtins.vars(args).get('undeclared_probe')", False),)
NAMESPACE_ESCAPE_CASES = (
    ('other = args', 'other = args'),
    ('other = args\nthird = other\nthird.x', 'other = args'),
    ('other, = (args,)', '(args,)'),
    ("getattr(*(args, 'x'))", "(args, 'x')"),
    ('helper(args)', 'helper(args)'), ('helper([args])', '[args]'),
    ('helper(*[args])', '[args]'),
    ('getattr(args, some_variable)', 'getattr(args, some_variable)'),
    ('hasattr(args, some_variable)', 'hasattr(args, some_variable)'),
    ('helper(vars(args))', 'vars(args)'),
    ('args.__dict__', 'args.__dict__'),
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
     "getattr(args, 'json', False)", "getattr(args, 'json', False)"),)
BUILTIN_IDENTITY_GLOBAL_CASES = (
    ("getattr(args, 'json', False)", {'getattr': builtins.getattr}, ()),
    ("getattr(args, 'json', False)", {'getattr': len},
     ("namespace escape: getattr(args, 'json', False)",)),
    ("getattr(args, 'json', False)", {'__builtins__': {'getattr': len}},
     ("namespace escape: getattr(args, 'json', False)",)),
    ("_ = [value for getattr in (getattr(args, 'json', False),)]", None, ()),)
BUILTIN_IDENTITY_LOCAL_CASES = (
    ('local from-import',
     "from builtins import getattr\ngetattr(args, 'json', False)", {}, ()),
    ('local module import',
     "import builtins\nbuiltins.getattr(args, 'json', False)", {}, ()),
    ('local from-import alias',
     "from builtins import getattr as g\ng(args, 'json', False)", {}, ()),
    ('module alias', "G(args, 'json', False)",
     {'G': builtins.getattr}, ()),
    ('local import rebound after use',
     "from builtins import getattr as g\n"
     "g(args, 'json', False)\ng = len", {}, ()),
    ('local import rebound before use',
     "from builtins import getattr as g\ng = len\n"
     "g(args, 'json', False)", {},
     ("namespace escape: g(args, 'json', False)",)),
    ('local import conditionally rebound before use',
     "from builtins import getattr as g\nif True:\n    g = len\n"
     "g(args, 'json', False)", {},
     ("namespace escape: g(args, 'json', False)",)),
    ('module alias made local after use',
     "G(args, 'json', False)\nG = len", {'G': builtins.getattr},
     ("namespace escape: G(args, 'json', False)",)),
    ('global module alias',
     "global G\nG(args, 'json', False)", {'G': builtins.getattr}, ()),
    ('local import deleted after use',
     "from builtins import getattr as g\n"
     "g(args, 'json', False)\ndel g", {}, ()),
    ('local import deleted before use',
     "from builtins import getattr as g\ndel g\n"
     "g(args, 'json', False)", {},
     ("namespace escape: g(args, 'json', False)",)),
    ('local import conditionally deleted before use',
     "from builtins import getattr as g\nif True:\n    del g\n"
     "g(args, 'json', False)", {},
     ("namespace escape: g(args, 'json', False)",)),
    ('module alias made local by delete',
     "G(args, 'json', False)\ndel G", {'G': builtins.getattr},
     ("namespace escape: G(args, 'json', False)",)),
    ('global alias deleted after use',
     "global G\nG(args, 'json', False)\ndel G",
     {'G': builtins.getattr}, ()),
    ('global alias deleted before use',
     "global G\ndel G\nG(args, 'json', False)",
     {'G': builtins.getattr},
     ("namespace escape: G(args, 'json', False)",)),
    ('bare builtin made local by delete',
     "getattr(args, 'json', False)\ndel getattr", {},
     ("namespace escape: getattr(args, 'json', False)",)),
    ('unproven module alias', "G(args, 'json', False)", {'G': len},
     ("namespace escape: G(args, 'json', False)",)),)
# Shape, argv, empty result, seeded empty result, result, GUARANTEED.
ARGPARSE_STORAGE_CASES = (
    ('optional', (), (), 'seed', (), False),
    ('optional-remainder', ('--probe',), (), 'seed', [], False),
    ('required-option', ('--probe', 'x'), None, None, 'x', True),
    ('remainder', (), [], [], [], True),
    ('star', (), (), 'seed', (), False),
    ('plus', ('x',), None, None, ['x'], True),
    ('question', (), (), 'seed', (), False),
    ('positional', ('x',), None, None, 'x', True),)
# Name, required, shared destination, mixed defaults, empty/left/right,
# DECLARED, GUARANTEED.
ARGPARSE_MUTEX_STORAGE_CASES = (
    ('optional-distinct-suppress', False, False, False,
     {}, {'left': 'left'}, {'right': 'right'}, ('left', 'right'), ()),
    ('optional-distinct-mixed', False, False, True,
     {'right': 'right-default'},
     {'right': 'right-default', 'left': 'left'}, {'right': 'right'},
     ('left', 'right'), ('right',)),
    ('optional-shared-suppress', False, True, False,
     {}, {'probe': 'left'}, {'probe': 'right'}, ('probe',), ()),
    ('optional-shared-mixed', False, True, True,
     {'probe': 'right-default'}, {'probe': 'left'}, {'probe': 'right'},
     ('probe',), ('probe',)),
    ('required-distinct-suppress', True, False, False,
     None, {'left': 'left'}, {'right': 'right'}, ('left', 'right'), ()),
    ('required-distinct-mixed', True, False, True,
     None, {'right': 'right-default', 'left': 'left'},
     {'right': 'right'}, ('left', 'right'), ('right',)),
    ('required-shared-suppress', True, True, False,
     None, {'probe': 'left'}, {'probe': 'right'},
     ('probe',), ('probe',)),
    ('required-shared-mixed', True, True, True,
     None, {'probe': 'left'}, {'probe': 'right'},
     ('probe',), ('probe',)),)
# Name, placement, empty/left/right argv and namespaces, DECLARED, GUARANTEED.
ARGPARSE_MUTEX_PLACEMENT_CASES = (
    ('nested-argument-group', 'nested', (), ('--left',), ('--right',),
     None, {'probe': 'left'}, {'probe': 'right'},
     ('probe',), ('probe',)),
    ('top-level-with-subparser', 'top', ('tabs',),
     ('--left', 'tabs'), ('--right', 'tabs'), None,
     {'cmd': 'tabs', 'probe': 'left'},
     {'cmd': 'tabs', 'probe': 'right'},
     ('cmd', 'probe'), ('cmd', 'probe')),
    ('on-subparser', 'subparser', ('tabs',),
     ('tabs', '--left'), ('tabs', '--right'), None,
     {'cmd': 'tabs', 'probe': 'left'},
     {'cmd': 'tabs', 'probe': 'right'},
     ('cmd', 'probe'), ('cmd', 'probe')),)
REFLECTIVE_ESCAPE_CASES = (
    ("_ = locals()['args'].undeclared_probe", 'locals()'),
    ("_ = eval('args.undeclared_probe')", "eval('args.undeclared_probe')"),
    ('_ = vars()', 'vars()'), ('_ = globals()', 'globals()'),
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
    ('_ = currentframe()', 'currentframe()'),)
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
     ".f_locals['args'].undeclared_probe", "vars(sys)['_getframe']()"),)
COMPOSITE_SUBSCRIPT_FRAME_ROUTE_CASES = (
    'COMPOSITE_ROUTES[~0]',
    'COMPOSITE_ROUTES[not 0]',
    'COMPOSITE_ROUTES[--1]',
    'COMPOSITE_ROUTES[---1]',
    'COMPOSITE_ROUTES[+-1]',
    'COMPOSITE_ROUTES[~-1]',
    'COMPOSITE_ROUTES[-~0]',
    'COMPOSITE_ROUTES[not not 0]',
    'COMPOSITE_ROUTES[0:2][-1]',
    'COMPOSITE_ROUTES[::2][-1]',
    'COMPOSITE_ROUTES[::-1][-1]',
    'COMPOSITE_ROUTES[:][-1]',
    'COMPOSITE_ROUTES[0:3][1:][-1]',
    'COMPOSITE_ROUTES[-3:--1][-1]',
    'COMPOSITE_ROUTES[+True]',
    'COMPOSITE_ROUTES[-True]',
    'COMPOSITE_ROUTES[+False]',
    'COMPOSITE_ROUTES[not False]',
    'COMPOSITE_ROUTES[True]',
    'COMPOSITE_ROUTES[:+True][-1]',
    'COMPOSITE_ROUTES[-True:][-1]',
    'COMPOSITE_ROUTES[+False:][-1]',
    'COMPOSITE_ROUTES[:True][-1]',
    'COMPOSITE_ROUTES[:~0][-1]',
    'COMPOSITE_ROUTES[not True:][-1]',)
RESOLVER_ONLY_FRAME_ROUTE_CASES = (
    ('exact classmethod route (resolver only)',
     'class FrameRoutes:\n'
     '    active = classmethod(sys._getframe)',
     "_ = FrameRoutes.active().f_locals['args'].undeclared_probe",
     'FrameRoutes.active()'),
    ('unresolved currentframe spelling (resolver only)', '',
     "_ = currentframe().f_locals['args'].undeclared_probe",
     'currentframe()'),)
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
     ".f_locals['args'].undeclared_probe"),)
_FRAME_ROUTE_OBJECTS = (sys._getframe, inspect.currentframe)


@contextlib.contextmanager
def isolated_bridge_environment(values=None):
    with mock.patch.dict(os.environ, values or {}):
        for name in (() if values is not None else BRIDGE_ENV_NAMES):
            os.environ.pop(name, None)
        yield


def assert_real_dispatch_isolated(mutated_cli_tabs, assert_dispatch_crashes):
    environment = dict.fromkeys(BRIDGE_ENV_NAMES, 'test-live-bridge')
    body = ("PROBE_ENV.append((os.environ.get('DAEDALUS_URL'), "
            "os.environ.get('DAEDALUS_TOKEN')))")
    handler_module = mutated_cli_tabs(
        'neutralized_dispatch_cli', 'PROBE_ENV = []', body)
    calls = []

    def record_api(*args, **kwargs):
        return calls.append((args, kwargs)) or []
    handler_module.api = record_api
    try:
        with isolated_bridge_environment(environment):
            try:
                assert_dispatch_crashes(handler_module)
            except AssertionError as error:
                assert str(error) == DISPATCH_PROBE_ERROR, error
            else:
                assert False, DISPATCH_PROBE_ERROR
            assert {name: os.environ.get(name)
                    for name in BRIDGE_ENV_NAMES} == environment
        assert calls == [], calls
        assert handler_module.PROBE_ENV == [(None, None)]
    finally:
        sys.modules.pop(handler_module.__dict__['__name__'], None)


def add_storage_probe(parser, shape, dest='probe'):
    options = {'default': argparse.SUPPRESS}
    if shape in ('optional', 'optional-remainder', 'required-option'):
        options['required'] = shape == 'required-option'
        if shape == 'optional-remainder':
            options['nargs'] = argparse.REMAINDER
        return parser.add_argument(f'--{dest.replace("_", "-")}', **options)
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
        assert declared == {'probe'}, shape
        assert guaranteed == ({'probe'} if is_guaranteed else set()), shape
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


def _add_mutex_probe(owner, required, shared, mixed):
    group = owner.add_mutually_exclusive_group(required=required)
    left_dest = 'probe' if shared else 'left'
    right_dest = 'probe' if shared else 'right'
    group.add_argument(
        '--left', dest=left_dest, action='store_const', const='left',
        default=argparse.SUPPRESS)
    right_default = 'right-default' if mixed else argparse.SUPPRESS
    group.add_argument(
        '--right', dest=right_dest, action='store_const', const='right',
        default=right_default)


def _assert_parse_namespace(parser, argv, expected, shape):
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            namespace = vars(parser.parse_args(argv))
        except SystemExit as error:
            assert expected is None and error.code == 2, (shape, error)
            return
    assert namespace == expected, (shape, argv, namespace)


def _assert_mutex_claim(audit_handler, shape, declared, guaranteed):
    for dest in declared:
        expression = f'args.{dest}'
        expected = [] if dest in guaranteed else [expression]
        assert audit_handler(
            expression, declared, guaranteed) == expected, (shape, dest)


def assert_argparse_mutex_storage_contract(audit_handler):
    for case in ARGPARSE_MUTEX_STORAGE_CASES:
        (shape, required, shared, mixed, empty, left, right,
         expected_declared, expected_guaranteed) = case
        parser = argparse.ArgumentParser(add_help=False)
        _add_mutex_probe(parser, required, shared, mixed)
        declared, guaranteed = namespace_dests(parser)
        assert declared == set(expected_declared), shape
        assert guaranteed == set(expected_guaranteed), shape
        _assert_mutex_claim(
            audit_handler, shape, declared, guaranteed)
        for argv, expected in (((), empty), (('--left',), left),
                               (('--right',), right)):
            _assert_parse_namespace(parser, argv, expected, shape)

    for case in ARGPARSE_MUTEX_PLACEMENT_CASES:
        (shape, placement, empty_argv, left_argv, right_argv,
         empty, left, right, expected_declared, expected_guaranteed) = case
        parser = argparse.ArgumentParser(add_help=False)
        if placement == 'nested':
            owner = parser.add_argument_group('nested')
            _add_mutex_probe(owner, True, True, False)
            declared, guaranteed = namespace_dests(parser)
        else:
            subparsers = parser.add_subparsers(dest='cmd', required=True)
            tabs = subparsers.add_parser('tabs', add_help=False)
            _add_mutex_probe(
                parser if placement == 'top' else tabs,
                True, True, False)
            root_claim = namespace_dests(parser)
            sub_claim = namespace_dests(tabs)
            declared = root_claim[0] | sub_claim[0]
            guaranteed = root_claim[1] | sub_claim[1]
        assert declared == set(expected_declared), shape
        assert guaranteed == set(expected_guaranteed), shape
        _assert_mutex_claim(
            audit_handler, shape, declared, guaranteed)
        for argv, expected in (
                (empty_argv, empty), (left_argv, left),
                (right_argv, right)):
            _assert_parse_namespace(parser, argv, expected, shape)


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
