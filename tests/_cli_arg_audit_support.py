"""Non-executing resolvers, tables, and isolated CLI dispatch controls.

DECLARED contains non-help/version, non-SUPPRESS action destinations and parser
defaults. GUARANTEED adds non-suppressed defaults, required actions, positional
REMAINDER, and parser defaults. A required mutually exclusive group guarantees
a destination only when every member stores that same non-SUPPRESS destination.
Direct reads require GUARANTEED; defaulted or presence-guarded reads require
DECLARED.

Builtin names, module attributes, and aliases qualify only when static
resolution proves the exact builtin object at the call site. Enclosing
statement prefixes establish exact aliases, earlier name or attribute
rebindings and deletions invalidate them, and unresolved or shadowed
references fail closed. Captured local aliases also require exact identity at
every proven direct invocation.

The constant resolver follows exact modules, classes, static/class methods,
list/tuple/dict values, constant keys, integer indices, and slices without
running handler code. UAdd and USub sign integer operands. Invert complements
integer operands. Not converts any resolved literal to bool. All four recurse;
bool values count as integer indices and slice bounds use that definition.

Outside families are non-exact descriptors, partial callables, traceback
frames, other containers and iterators, comprehension results, instance
attributes, attribute getters, runtime-built names, mapping-proxy reads,
call-produced indices, and external frame acquisition. Each named
family has a known-gap control, and every known-gap control belongs to exactly
one named family.
"""
import argparse
import builtins
import contextlib
import io
import os
import sys
from unittest import mock

import _cli_arg_audit_resolver as resolver

namespace_dests = resolver.namespace_dests
constant_string = resolver.constant_string
resolve_frame_value = resolver.resolve_frame_value
is_frame_route = resolver.is_frame_route
reflective_builtin_call = resolver.reflective_builtin_call
permitted_namespace_read = resolver.permitted_namespace_read
is_outside_expression = resolver.is_outside_expression

# Keep do_tabs's indirect args.json read explicit beside the resolved reads.
KNOWN_INDIRECT_ARG_READS = (('tabs', 'json', 'do_tabs'),)
BRIDGE_ENV_NAMES = ('DAEDALUS_URL', 'DAEDALUS_TOKEN', 'TOKEN')
DISPATCH_PROBE_ERROR = 'real dispatch did not read undeclared_probe'
REAL_STORAGE_DISPATCH_CASES = (
    ('optional',
     "PROBE_READS.append(getattr(args, 'undeclared_probe', None)); return",
     (), (None,)),
    ('remainder',
     'PROBE_READS.append(args.undeclared_probe); return', (), ([],)),
    ('required-option',
     'PROBE_READS.append(args.undeclared_probe); return',
     ('--undeclared-probe', 'present'), ('present',)),)
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
BUILTIN_IDENTITY_CALL_SITE_CASES = (
    ('module attribute reassigned before use',
     "sys.modules[__name__].G = len\nG(args, 'json', False)",
     {'G': builtins.getattr, 'sys': sys, '__name__': __name__},
     ("namespace escape: G(args, 'json', False)",)),
    ('module dictionary reassigned before use',
     "sys.modules[__name__].__dict__['G'] = len\n"
     "G(args, 'json', False)",
     {'G': builtins.getattr, 'sys': sys, '__name__': __name__},
     ("namespace escape: G(args, 'json', False)",)),
    ('global import reassigned through module dictionary',
     "global G\nfrom builtins import getattr as G\n"
     "sys.modules[__name__].__dict__['G'] = len\n"
     "G(args, 'json', False)",
     {'sys': sys, '__name__': __name__},
     ("namespace escape: G(args, 'json', False)",)),
    ('branch rebinding before nested call',
     "from builtins import getattr as g\n"
     "if condition:\n    g = len\n    g(args, 'json', False)", {},
     ("namespace escape: g(args, 'json', False)",)),
    ('loop-body rebinding before nested call',
     "from builtins import getattr as g\n"
     "for value in values:\n    g = len\n    g(args, 'json', False)", {},
     ("namespace escape: g(args, 'json', False)",)),
    ('closure captures exact alias',
     "from builtins import getattr as g\n"
     "def inner():\n    return g(args, 'json', False)\ninner()", {}, ()),
    ('closure alias rebound before invocation',
     "from builtins import getattr as g\n"
     "def inner():\n    return g(args, 'json', False)\n"
     "g = len\ninner()", {},
     ("namespace escape: g(args, 'json', False)",)),
    ('closure invoked before alias rebound',
     "from builtins import getattr as g\n"
     "def inner():\n    return g(args, 'json', False)\n"
     "inner()\ng = len", {}, ()),
    ('conditional binding before later call',
     "if condition:\n    from builtins import getattr as g\n"
     "g(args, 'json', False)", {},
     ("namespace escape: g(args, 'json', False)",)),
    ('same-branch conditional binding',
     "if condition:\n    from builtins import getattr as g\n"
     "    g(args, 'json', False)", {}, ()),
    ('unrelated object attribute reassigned',
     "from builtins import getattr as g\nthing.g = len\n"
     "g(args, 'json', False)", {}, ()),
    ('module attribute preserves exact local alias',
     "from builtins import getattr as g\n"
     "sys.modules[__name__].g = len\ng(args, 'json', False)",
     {'sys': sys, '__name__': __name__}, ()),
    ('builtin module unrelated attribute reassigned',
     "import builtins\nbuiltins.len = helper\n"
     "builtins.getattr(args, 'json', False)", {}, ()),
    ('builtin module target attribute reassigned',
     "import builtins\nbuiltins.getattr = helper\n"
     "builtins.getattr(args, 'json', False)", {},
     ("namespace escape: builtins.getattr(args, 'json', False)",)),)
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
OUTSIDE_EXPRESSION_FRAME_ROUTE_CASES = (
    ('comparison index',
     'COMPARISON_ROUTES = (None, sys._getframe)',
     "_ = COMPARISON_ROUTES[0 < 1]()"
     ".f_locals['args'].undeclared_probe",
     'COMPARISON_ROUTES[0 < 1]()'),
    ('tuple-literal key',
     "TUPLE_ROUTES = {(0, 1): sys._getframe}",
     "_ = TUPLE_ROUTES[(0, 1)]()"
     ".f_locals['args'].undeclared_probe",
     'TUPLE_ROUTES[0, 1]()'),
    ('binary index',
     'FRAME_ROUTES = (None, None, sys._getframe)',
     "_ = FRAME_ROUTES[1 + 1]().f_locals['args'].undeclared_probe",
     'FRAME_ROUTES[1 + 1]()'),)
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
    ('call-produced index',
     'FRAME_ROUTES = (None, sys._getframe)',
     "_ = FRAME_ROUTES[len((None,))]()"
     ".f_locals['args'].undeclared_probe"),)
KNOWN_GAP_FAMILIES = (
    ('non-exact descriptors',
     ('custom descriptor class attribute', 'property descriptor',
      'cached-property descriptor', 'staticmethod subclass descriptor')),
    ('partial callables', ('functools.partial',)),
    ('traceback frames', ('exception traceback frame',)),
    ('other containers and iterators',
     ('other container type', 'iterator protocol')),
    ('comprehension results', ('comprehension result',)),
    ('instance attributes', ('instance attribute',)),
    ('attribute getters', ('operator.attrgetter',)),
    ('runtime-built names', ('runtime-built name',)),
    ('mapping-proxy reads',
     ('class vars mapping-proxy get',
      'class vars mapping-proxy subscript')),
    ('call-produced indices', ('call-produced index',)),
    ('external frame acquisition',
     ('frame acquisition in another function',
      'frame acquisition in nested helper')),)
DOCSTRING_RULE_PHRASES = (
    'UAdd and USub sign integer operands',
    'Invert complements integer operands',
    'Not converts any resolved literal to bool',
    ('A required mutually exclusive group guarantees a destination only '
     'when every member stores that same non-SUPPRESS destination'),
    ('Builtin names, module attributes, and aliases qualify only when static '
     'resolution proves the exact builtin object at the call site'),
    ('Captured local aliases also require exact identity at every proven '
     'direct invocation'),)


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
        declared, guaranteed = resolver.namespace_dests(parser)
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
        assert resolver.namespace_dests(seeded) == \
            ({'probe'}, {'probe'}), shape
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
        declared, guaranteed = resolver.namespace_dests(parser)
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
            declared, guaranteed = resolver.namespace_dests(parser)
        else:
            subparsers = parser.add_subparsers(dest='cmd', required=True)
            tabs = subparsers.add_parser('tabs', add_help=False)
            _add_mutex_probe(
                parser if placement == 'top' else tabs,
                True, True, False)
            root_claim = resolver.namespace_dests(parser)
            sub_claim = resolver.namespace_dests(tabs)
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
