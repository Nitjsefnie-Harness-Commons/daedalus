"""Non-executing resolvers, tables, and isolated CLI dispatch controls.
DECLARED covers stored action destinations and parser defaults; GUARANTEED
adds required and non-suppressed values. A required mutually exclusive group
guarantees a destination only when every member stores that same non-SUPPRESS
destination.
Guarded or defaulted reads require DECLARED; direct reads require GUARANTEED.
Semantic claims are ``DECIDED`` consists only of the resolver's explicitly
enumerated expression node types | every other ``ast.expr`` node type is
``OUTSIDE`` by definition, so future AST node types enter the fail-closed side
automatically and no third bucket exists | comparisons and tuple-literal keys
stay ``OUTSIDE`` because reproducing their Python semantics would widen the
trusted evaluator | builtin aliases are trusted only with exact builtin
identity at the specific call site | uncertain, rebound, closure-dependent,
or conditional bindings fail closed | captured local aliases require exact
identity at every proven direct invocation. Semantic claims end.
The resolver follows exact modules, classes, methods, containers, indices, and
slices without running code. UAdd and USub sign integer operands. Invert
complements integer operands. Not converts any resolved literal to bool. All
four recurse; bool values count as integer indices and slice bounds.
Current named known-gap control families are non-exact descriptors, partial
callables, traceback frames, other containers and iterators, comprehension
results, instance attributes, attribute getters, runtime-built names,
mapping-proxy reads, call-produced indices, and external frame acquisition.
Each named family has a known-gap control, and every known-gap control belongs
to exactly one named family. The executable consistency check is
bidirectional: contract prose and control tables cover each other.
"""
import argparse
import ast
import builtins
import contextlib
import io
import inspect
import os
import sys
from unittest import mock
import _cli_arg_audit_resolver as resolver

_FRAME_READ = ".f_locals['args'].undeclared_probe"
_CLASS_FRAME_ROUTES = 'class FrameRoutes:\n    active = sys._getframe'
_DICT_FRAME_ROUTES = "FRAME_ROUTES = {'active': sys._getframe}"
_GETATTR_SOURCE = "getattr(args, 'json', False)"
_GETATTR_ESCAPE = ("namespace escape: getattr(args, 'json', False)",)
_G_ESC = ("namespace escape: G(args, 'json', False)",)
_LOCAL_ESCAPE = ("namespace escape: g(args, 'json', False)",)
_G_IMPORT = 'from builtins import getattr as g\n'
_G_CALL = "g(args, 'json', False)"
_G_CLOSURE = _G_IMPORT + 'def inner():\n    return g(args, \'json\', False)\n'
_MODULE_G_SCOPE = {'G': builtins.getattr, 'sys': sys, '__name__': __name__}
_BUILTINS_GETATTR_ESCAPE = (
    "namespace escape: builtins.getattr(args, 'json', False)",)


def _frame_case(prelude, construct):
    return prelude, f'_ = {construct}{_FRAME_READ}'


def _named_frame_case(name, prelude, construct, expected=None):
    case = name, *_frame_case(prelude, construct)
    return case if expected is None else (*case, expected)


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
    (f"getattr = helper\n{_GETATTR_SOURCE}", _GETATTR_SOURCE),
    ("hasattr = helper\nhasattr(args, 'json')", "hasattr(args, 'json')"),
    ("vars = helper\nvars(args).get('json')", "vars(args)"),
    (f"_ = (lambda getattr: {_GETATTR_SOURCE})(helper)", _GETATTR_SOURCE),
    ("def inner(getattr):\n    return getattr(args, 'json', False)\n"
     "inner(helper)", _GETATTR_SOURCE),
    ("def inner(getattr=getattr(args, 'json', False)):\n"
     "    return getattr(args, 'json', False)\ninner()", _GETATTR_SOURCE),
    (f"_ = [{_GETATTR_SOURCE} for getattr in helpers]", _GETATTR_SOURCE),
    (f"from operator import attrgetter as getattr\n{_GETATTR_SOURCE}",
     _GETATTR_SOURCE),)
BUILTIN_IDENTITY_GLOBAL_CASES = (
    ("getattr(args, 'json', False)", {'getattr': builtins.getattr}, ()),
    ("getattr(args, 'json', False)", {'getattr': len}, _GETATTR_ESCAPE),
    ("getattr(args, 'json', False)", {'__builtins__': {'getattr': len}},
     _GETATTR_ESCAPE),
    ("_ = [value for getattr in (getattr(args, 'json', False),)]", None, ()),)
BUILTIN_IDENTITY_CALL_SITE_CASES = (
    ('module attribute reassigned before use',
     "sys.modules[__name__].G = len\nG(args, 'json', False)",
     _MODULE_G_SCOPE, _G_ESC),
    ('module dictionary reassigned before use',
     "sys.modules[__name__].__dict__['G'] = len\nG(args, 'json', False)",
     _MODULE_G_SCOPE, _G_ESC),
    ('module setattr reassigned before use',
     "setattr(sys.modules[__name__], 'G', len)\nG(args, 'json', False)",
     _MODULE_G_SCOPE, _G_ESC),
    ('module setattr unrelated name',
     "setattr(sys.modules[__name__], 'OTHER', len)\n"
     "G(args, 'json', False)", _MODULE_G_SCOPE, ()),
    ('module setattr uncertain name',
     "setattr(sys.modules[__name__], target, len)\nG(args, 'json', False)",
     _MODULE_G_SCOPE, _G_ESC),
    ('global import reassigned through module dictionary',
     "global G\nfrom builtins import getattr as G\n"
     "sys.modules[__name__].__dict__['G'] = len\nG(args, 'json', False)",
     {'sys': sys, '__name__': __name__}, _G_ESC),
    ('branch rebinding before nested call',
     _G_IMPORT
     + "if condition:\n    g = len\n    g(args, 'json', False)", {},
     _LOCAL_ESCAPE),
    ('loop-body rebinding before nested call',
     _G_IMPORT
     + "for value in values:\n    g = len\n    g(args, 'json', False)", {},
     _LOCAL_ESCAPE),
    ('closure captures exact alias',
     _G_CLOSURE + 'inner()', {}, ()),
    ('closure alias rebound before invocation',
     _G_CLOSURE + 'g = len\ninner()', {}, _LOCAL_ESCAPE),
    ('closure invoked before alias rebound',
     _G_CLOSURE + 'inner()\ng = len', {}, ()),
    ('conditional binding before later call',
     "if condition:\n    from builtins import getattr as g\n"
     "g(args, 'json', False)", {}, _LOCAL_ESCAPE),
    ('same-branch conditional binding',
     "if condition:\n    from builtins import getattr as g\n"
     "    g(args, 'json', False)", {}, ()),
    ('unrelated object attribute reassigned',
     _G_IMPORT + 'thing.g = len\n' + _G_CALL, {}, ()),
    ('module attribute preserves exact local alias',
     _G_IMPORT
     + "sys.modules[__name__].g = len\ng(args, 'json', False)",
     {'sys': sys, '__name__': __name__}, ()),
    ('builtin module unrelated attribute reassigned',
     "import builtins\nbuiltins.len = helper\n"
     "builtins.getattr(args, 'json', False)", {}, ()),
    ('builtin module target attribute reassigned',
     "import builtins\nbuiltins.getattr = helper\n"
     "builtins.getattr(args, 'json', False)", {},
     _BUILTINS_GETATTR_ESCAPE),)
BUILTIN_IDENTITY_LOCAL_CASES = (
    ('local from-import',
     "from builtins import getattr\ngetattr(args, 'json', False)", {}, ()),
    ('local module import',
     "import builtins\nbuiltins.getattr(args, 'json', False)", {}, ()),
    ('local from-import alias',
     _G_IMPORT + _G_CALL, {}, ()),
    ('module alias', "G(args, 'json', False)",
     {'G': builtins.getattr}, ()),
    ('local import rebound after use',
     _G_IMPORT + _G_CALL + '\ng = len', {}, ()),
    ('local import rebound before use',
     _G_IMPORT + 'g = len\n' + _G_CALL, {}, _LOCAL_ESCAPE),
    ('local import conditionally rebound before use',
     _G_IMPORT + 'if True:\n    g = len\n' + _G_CALL, {}, _LOCAL_ESCAPE),
    ('module alias made local after use',
     "G(args, 'json', False)\nG = len", {'G': builtins.getattr},
     _G_ESC),
    ('global module alias',
     "global G\nG(args, 'json', False)", {'G': builtins.getattr}, ()),
    ('local import deleted after use',
     _G_IMPORT + _G_CALL + '\ndel g', {}, ()),
    ('local import deleted before use',
     _G_IMPORT + 'del g\n' + _G_CALL, {}, _LOCAL_ESCAPE),
    ('local import conditionally deleted before use',
     _G_IMPORT + 'if True:\n    del g\n' + _G_CALL, {}, _LOCAL_ESCAPE),
    ('module alias made local by delete',
     "G(args, 'json', False)\ndel G", {'G': builtins.getattr},
     _G_ESC),
    ('global alias deleted after use',
     "global G\nG(args, 'json', False)\ndel G",
     {'G': builtins.getattr}, ()),
    ('global alias deleted before use',
     "global G\ndel G\nG(args, 'json', False)",
     {'G': builtins.getattr}, _G_ESC),
    ('bare builtin made local by delete',
     "getattr(args, 'json', False)\ndel getattr", {}, _GETATTR_ESCAPE),
    ('unproven module alias', "G(args, 'json', False)", {'G': len}, _G_ESC),)
ARGPARSE_STORAGE_CASES = (
    ('optional', (), (), 'seed', (), False),
    ('optional-remainder', ('--probe',), (), 'seed', [], False),
    ('required-option', ('--probe', 'x'), None, None, 'x', True),
    ('remainder', (), [], [], [], True),
    ('star', (), (), 'seed', (), False),
    ('plus', ('x',), None, None, ['x'], True),
    ('question', (), (), 'seed', (), False),
    ('positional', ('x',), None, None, 'x', True),)
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
    ("import inspect as insp\n_ = insp.currentframe().f_locals['args'].x",
     'insp.currentframe()'),
    ('_ = sys._getframe', 'sys._getframe'),
    ('_ = sys._getframe.__call__()', 'sys._getframe.__call__()'),
    ('helper(sys._getframe)', 'sys._getframe'),
    ('helper(inspect.currentframe)', 'inspect.currentframe'),
    ('_ = _getframe()', '_getframe()'),
    ('_ = currentframe()', 'currentframe()'),)
_DECIDED_SOURCES = (
    _frame_case('from sys import _getframe as get_frame', 'get_frame()'),
    _frame_case('', 'sys._getframe.__call__()'),
    _frame_case('from inspect import currentframe as cf', 'cf()'),
    _frame_case('', "getattr(sys, '_getframe')()"),
    _frame_case('FRAME_ROUTES = [sys._getframe]', 'FRAME_ROUTES[0]()'),
    _frame_case('TAB_FRAME_ROUTES = (sys._getframe,)',
                'TAB_FRAME_ROUTES[0]()'),
    _frame_case(_DICT_FRAME_ROUTES, "FRAME_ROUTES['active']()"),
    _frame_case(_DICT_FRAME_ROUTES, "FRAME_ROUTES.get('active')()"),
    _frame_case(_DICT_FRAME_ROUTES, "FRAME_ROUTES.get('active', None)()"),
    _frame_case('FRAME_ROUTES = [sys._getframe]', 'FRAME_ROUTES[-1]()'),
    _frame_case('FRAME_ROUTES = (sys._getframe,)', 'FRAME_ROUTES[-1]()'),
    _frame_case('FRAME_ROUTES = (sys._getframe,)', 'FRAME_ROUTES[+0]()'),
    _frame_case('BOOL_ROUTES = {True: sys._getframe}',
                'BOOL_ROUTES[+True]()'),
    _frame_case('BOOL_ROUTES = {-1: sys._getframe}',
                'BOOL_ROUTES[-True]()'),
    _frame_case('BOOL_ROUTES = {False: sys._getframe}',
                'BOOL_ROUTES[+False]()'),
    _frame_case('FRAME_ROUTES = [None, sys._getframe]',
                'FRAME_ROUTES[:][-1]()'),
    _frame_case("FRAME_ROUTES = {-1: sys._getframe}", 'FRAME_ROUTES[-1]()'),
    _frame_case(_CLASS_FRAME_ROUTES, 'FrameRoutes.active()'),
    _frame_case('class FrameRoutes:\n    active = staticmethod(sys._getframe)',
                'FrameRoutes.active()'),
    _frame_case('', "sys.__dict__['_getframe']()"),
    _frame_case('', "sys.__dict__.get('_getframe')()"),
    _frame_case('', "vars(sys)['_getframe']()"),)
_DECIDED_EXPECTATIONS = (
    'get_frame()', 'sys._getframe.__call__()', 'cf()',
    "getattr(sys, '_getframe')()", 'FRAME_ROUTES[0]()',
    'TAB_FRAME_ROUTES[0]()', "FRAME_ROUTES['active']()",
    "FRAME_ROUTES.get('active')()", "FRAME_ROUTES.get('active', None)()",
    'FRAME_ROUTES[-1]()', 'FRAME_ROUTES[-1]()', 'FRAME_ROUTES[+0]()',
    'BOOL_ROUTES[+True]()', 'BOOL_ROUTES[-True]()', 'BOOL_ROUTES[+False]()',
    'FRAME_ROUTES[:][-1]()', 'FRAME_ROUTES[-1]()', 'FrameRoutes.active()',
    'FrameRoutes.active()', "sys.__dict__['_getframe']()",
    "sys.__dict__.get('_getframe')()", "vars(sys)['_getframe']()")
DECIDED_FRAME_ROUTE_CASES = tuple(
    (*source, expected) for source, expected in zip(
        _DECIDED_SOURCES, _DECIDED_EXPECTATIONS, strict=True))
COMPOSITE_SUBSCRIPT_FRAME_ROUTE_CASES = tuple(
    f'COMPOSITE_ROUTES{suffix}' for suffix in (
        '[~0]', '[not 0]', '[--1]', '[---1]', '[+-1]', '[~-1]', '[-~0]',
        '[not not 0]', '[0:2][-1]', '[::2][-1]', '[::-1][-1]', '[:][-1]',
        '[0:3][1:][-1]', '[-3:--1][-1]', '[+True]', '[-True]', '[+False]',
        '[not False]', '[True]', '[:+True][-1]', '[-True:][-1]',
        '[+False:][-1]', '[:True][-1]', '[:~0][-1]', '[not True:][-1]'))
OUTSIDE_EXPRESSION_FRAME_ROUTE_CASES = (
    _named_frame_case(
        'comparison index', 'COMPARISON_ROUTES = (None, sys._getframe)',
        'COMPARISON_ROUTES[0 < 1]()', 'COMPARISON_ROUTES[0 < 1]()'),
    ('tuple-literal key', "TUPLE_ROUTES = {(0, 1): sys._getframe}",
     f"_ = TUPLE_ROUTES[(0, 1)](){_FRAME_READ}",
     'TUPLE_ROUTES[0, 1]()'),
    _named_frame_case(
        'binary index', 'FRAME_ROUTES = (None, None, sys._getframe)',
        'FRAME_ROUTES[1 + 1]()', 'FRAME_ROUTES[1 + 1]()'),
    _named_frame_case(
        'nested comparison index',
        'ROUTES = {True: (sys._getframe,)}',
        'ROUTES[0 < 1][0]()', 'ROUTES[0 < 1][0]()'),
    _named_frame_case(
        'deep nested comparison index',
        'ROUTES = {True: [((sys._getframe,),)]}',
        'ROUTES[0 < 1][0][0][0]()', 'ROUTES[0 < 1][0][0][0]()'),)
RESOLVER_ONLY_FRAME_ROUTE_CASES = (
    _named_frame_case(
        'exact classmethod route (resolver only)',
        'class FrameRoutes:\n    active = classmethod(sys._getframe)',
        'FrameRoutes.active()', 'FrameRoutes.active()'),
    _named_frame_case(
        'unresolved currentframe spelling (resolver only)', '',
        'currentframe()', 'currentframe()'),)
KNOWN_GAP_FRAME_ROUTE_CASES = (
    _named_frame_case(
        'comprehension result', 'FRAME_ROUTES = [sys._getframe]',
        '[route for route in FRAME_ROUTES][0]()'),
    _named_frame_case('instance attribute', _CLASS_FRAME_ROUTES,
                      'FrameRoutes().active()'),
    _named_frame_case(
        'custom descriptor class attribute',
        'class RouteDescriptor:\n    def __get__(self, obj, objtype=None):\n'
        '        return sys._getframe\nclass FrameRoutes:\n'
        '    active = RouteDescriptor()',
        'FrameRoutes.active()'),
    _named_frame_case(
        'property descriptor',
        'class FrameRoutes:\n    @property\n    def active(self):\n'
        '        return sys._getframe',
        'FrameRoutes().active()'),
    _named_frame_case(
        'cached-property descriptor',
        'import functools\nclass FrameRoutes:\n'
        '    @functools.cached_property\n    def active(self):\n'
        '        return sys._getframe',
        'FrameRoutes().active()'),
    _named_frame_case(
        'staticmethod subclass descriptor',
        'class RouteStaticmethod(staticmethod):\n    pass\n'
        'class FrameRoutes:\n    active = RouteStaticmethod(sys._getframe)',
        'FrameRoutes.active()'),
    _named_frame_case(
        'functools.partial', 'import functools\n'
        'FRAME_ROUTE = functools.partial(sys._getframe)', 'FRAME_ROUTE()'),
    ('exception traceback frame', '',
     "try:\n        raise RuntimeError('probe')\n"
     "    except RuntimeError as err:\n"
     "        _ = err.__traceback__.tb_frame.f_locals['args']"
     ".undeclared_probe"),
    ('frame acquisition in another function',
     "def caller_namespace():\n    return sys._getframe(1).f_locals['args']",
     '_ = caller_namespace().undeclared_probe'),
    ('frame acquisition in nested helper', '',
     'def caller_namespace():\n'
     "        return sys._getframe(1).f_locals['args']\n"
     '    _ = caller_namespace().undeclared_probe'),
    _named_frame_case('class vars mapping-proxy get', _CLASS_FRAME_ROUTES,
                      "vars(FrameRoutes).get('active')()"),
    _named_frame_case(
        'class vars mapping-proxy subscript', _CLASS_FRAME_ROUTES,
        "vars(FrameRoutes)['active']()"),
    _named_frame_case('other container type',
                      'import collections\n'
                      'FRAME_ROUTES = collections.deque((sys._getframe,))',
                      'FRAME_ROUTES[0]()'),
    _named_frame_case(
        'iterator protocol', 'FRAME_ROUTES = (sys._getframe,)',
        'next(iter(FRAME_ROUTES))()'),
    _named_frame_case('operator.attrgetter', 'import operator',
                      "operator.attrgetter('_getframe')(sys)()"),
    _named_frame_case(
        'runtime-built name', '', "getattr(sys, '_get' + 'frame')()"),
    _named_frame_case('call-produced index',
                      'FRAME_ROUTES = (None, sys._getframe)',
                      'FRAME_ROUTES[len((None,))]()'),)
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
    'contract prose and control tables cover each other',)
SEMANTIC_CONTRACT_CLAIMS = (
    ("``DECIDED`` consists only of the resolver's explicitly enumerated "
     'expression node types'),
    ('every other ``ast.expr`` node type is ``OUTSIDE`` by definition, so '
     'future AST node types enter the fail-closed side automatically and no '
     'third bucket exists'),
    ('comparisons and tuple-literal keys stay ``OUTSIDE`` because '
     'reproducing their Python semantics would widen the trusted evaluator'),
    ('builtin aliases are trusted only with exact builtin identity at the '
     'specific call site'),
    ('uncertain, rebound, closure-dependent, or conditional bindings fail '
     'closed'),
    ('captured local aliases require exact identity at every proven direct '
     'invocation'),)


def assert_dict_get_default(frame_value):
    unresolved = object()
    for expression, expected in (('+True', 1), ('-True', -1),
                                 ('+False', 0)):
        value = resolver._constant_value(
            ast.parse(expression, mode='eval').body, unresolved)
        assert (type(value), value) == (int, expected), expression
    resolved = resolver._constant_value(
        ast.parse('routes[:+True]', mode='eval').body.slice, unresolved)
    assert (type(resolved.stop), resolved.stop) == (int, 1)
    invalid = ast.parse("routes['not-an-index':]", mode='eval').body.slice
    assert resolver._constant_value(invalid, unresolved) is unresolved
    function = ast.parse(
        "def do_tabs(args):\n"
        "    return ROUTES.get('active', DEFAULT_ROUTE)\n").body[0]
    call = function.body[0].value
    handler_globals = {'ROUTES': {'active': sys._getframe},
                       'DEFAULT_ROUTE': inspect.currentframe}
    assert frame_value(call, function, handler_globals, {}) is sys._getframe
    handler_globals['ROUTES'] = {}
    assert frame_value(call, function, handler_globals, {}) \
        is inspect.currentframe
    literal_function = ast.parse(
        "def do_tabs(args):\n"
        "    return ROUTES.get('active', None)\n").body[0]
    literal_call = literal_function.body[0].value
    assert frame_value(literal_call, literal_function,
                       {'ROUTES': {}}, {}) is None


def assert_every_unary_operator():
    unresolved = object()
    operator_cases = (
        ('+0', ast.UAdd, int, 0), ('-0', ast.USub, int, 0),
        ('~0', ast.Invert, int, -1), ('not 0', ast.Not, bool, True),)
    assert {operator for _, operator, _, _ in operator_cases} == \
        set(ast.unaryop.__subclasses__())
    combination_cases = (
        ('~-1', int, 0), ('-~0', int, 1),
        ('not not 0', bool, False), ('+True', int, 1),
        ('-True', int, -1), ('~False', int, -1),
        ('not False', bool, True), ('not 1.0', bool, False),
        ("not ''", bool, True), ('not None', bool, True),)
    for expression, operator, expected_type, expected in operator_cases:
        node = ast.parse(expression, mode='eval').body
        assert isinstance(node.op, operator), expression
        value = resolver._constant_value(node, unresolved)
        assert (type(value), value) == (expected_type, expected), expression
    for expression, expected_type, expected in combination_cases:
        node = ast.parse(expression, mode='eval').body
        value = resolver._constant_value(node, unresolved)
        assert (type(value), value) == (expected_type, expected), expression
    slice_cases = (
        ('routes[:~0]', slice(None, -1, None)),
        ('routes[-~0:]', slice(1, None, None)),
        ('routes[:(not 0)]', slice(None, True, None)),
        ('routes[(not not 0):]', slice(False, None, None)),)
    for expression, expected in slice_cases:
        node = ast.parse(expression, mode='eval').body.slice
        value = resolver._constant_value(node, unresolved)
        assert value == expected, expression


def assert_inner_scope_bindings(audit_handler):
    shadowed = ('def inner(args):\n    args.undeclared_probe\n'
                'shadow = lambda args: args.undeclared_probe')
    assert audit_handler(shadowed) == []
    closure = 'def inner():\n    args.undeclared_probe'
    assert audit_handler(closure) == ['args.undeclared_probe']


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
    api = mock.Mock(return_value=[])
    handler_module.api = api
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
        assert api.call_args_list == [], api.call_args_list
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


def assert_argparse_storage_contract(audit_handler):
    def stored(value):
        return {} if value == () else {'probe': value}

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
            assert vars(parser.parse_args([])) == stored(empty), shape
        assert vars(parser.parse_args(argv)) == stored(minimal), shape
        seeded = argparse.ArgumentParser(add_help=False)
        add_storage_probe(seeded, shape)
        seeded.set_defaults(probe='seed')
        assert resolver.namespace_dests(seeded) == \
            ({'probe'}, {'probe'}), shape
        if seeded_empty is not None:
            assert vars(seeded.parse_args([])) == stored(seeded_empty), shape
        seeded_minimal = minimal if argv else seeded_empty
        assert vars(seeded.parse_args(argv)) == stored(seeded_minimal), shape


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
