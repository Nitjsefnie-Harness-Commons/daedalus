"""Resolver tables and isolated CLI dispatch controls. DECLARED covers stored
action destinations and parser defaults; GUARANTEED adds required and
non-suppressed values. A required mutually exclusive group guarantees a
destination only when every member stores that same non-SUPPRESS destination.
Guarded or defaulted reads require DECLARED; direct reads require GUARANTEED.
Namespace stores are refused as namespace store escapes.
FRAME_NAMESPACE_PLANTS are ways the CLI can be made to read a frame's
namespace, planted into a real module and read one per row here. They are
plants, not the rule's inputs: the rule answers the operation once, from
the member set the resolver reads off types.FrameType. Three rows are the
sole catcher of one arm each — the callee that is itself a call, the
starred expansion, and the getattr whose name is an expression — so
dropping one of those three arms reds the row that names it. The
unreadable-subscript arm is pinned on both sides, by different
controls: four rows below are the sole catchers of its refusal, and the
real tree is the control for its exemption, since removing the
range-and-tuple exemption refuses the eleven correct slices the CLI
already has. CONDITIONS_PINNED rows C10 and C11 name the two."""
import argparse
import builtins
import contextlib
import io
import os
import sys
import types
from unittest import mock
import _cli_arg_audit_resolver as resolver

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
    ("builtins.getattr(args, 'json', False)",
     "builtins.getattr(args, 'undeclared_probe', None)"),
    ("builtins.hasattr(args, 'json')",
     "builtins.hasattr(args, 'undeclared_probe')"),
    ("builtins.vars(args).get('json')",
     "builtins.vars(args).get('undeclared_probe')"),)
NAMESPACE_ESCAPE_CASES = (
    ('other = args', 'other = args'),
    ('other = args\nthird = other\nthird.x', 'other = args'),
    ('other, = (args,)', '(args,)'),
    ("getattr(*(args, 'x'))", "getattr(*(args, 'x'))"),
    ('helper(args)', 'helper(args)'), ('helper([args])', '[args]'),
    ('helper(*[args])', 'helper(*[args])'),
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
    ("_ = locals()['args'].undeclared_probe", "locals()['args']"),
    ("_ = eval('args.undeclared_probe')", "eval('args.undeclared_probe')"),
    ('_ = vars()', 'vars()'), ('_ = globals()', 'globals()'),
    ("exec('args.undeclared_probe')", "exec('args.undeclared_probe')"),
    ("_ = builtins.locals()['args'].undeclared_probe",
     "builtins.locals()['args']"),
    ("_ = builtins.globals()['args'].undeclared_probe",
     "builtins.globals()['args']"),
    ("_ = builtins.eval('args.undeclared_probe')",
     "builtins.eval('args.undeclared_probe')"),
    ("builtins.exec('args.undeclared_probe')",
     "builtins.exec('args.undeclared_probe')"),
    ('_ = builtins.vars()', 'builtins.vars()'),
    ("locals = helper\n_ = locals()['args'].undeclared_probe",
     "locals()['args']"),
    ("globals = helper\n_ = globals()['args'].undeclared_probe",
     "globals()['args']"),
    ("eval = helper\n_ = eval('args.undeclared_probe')",
     "eval('args.undeclared_probe')"),
    ("exec = helper\nexec('args.undeclared_probe')",
     "exec('args.undeclared_probe')"),
    ('vars = helper\n_ = vars()', 'vars()'),
    ("_ = sys._getframe().f_locals['args'].x",
     "sys._getframe().f_locals['args']"),
    ("_ = inspect.currentframe().f_locals['args'].x",
     "inspect.currentframe().f_locals['args']"),
    ("from sys import _getframe\n"
     "_ = _getframe().f_locals['args'].x", "_getframe().f_locals['args']"),
    ("from sys import _getframe as get_frame\n"
     "_ = get_frame().f_locals['args'].x",
     "get_frame().f_locals['args']"),
    ("from inspect import currentframe as cf\n"
     "_ = cf().f_locals['args'].x", "cf().f_locals['args']"),
    ("import sys as system\n"
     "_ = system._getframe().f_locals['args'].x",
     "system._getframe().f_locals['args']"),
    ("import inspect as insp\n_ = insp.currentframe().f_locals['args'].x",
     "insp.currentframe().f_locals['args']"),
    ("_ = getattr(sys._getframe(), 'f_locals')['args'].x",
     "getattr(sys._getframe(), 'f_locals')['args']"),
    ('holder = helper()\n_ = holder[\'args\'].undeclared_probe',
     "holder['args']"),)
# Each row splices into the real daedalus_cli/commands_eval.py: the prelude
# after its first import, the replacement for the anchor. The last field is
# the receiver the refusal must name. These are plants, not the rule's inputs.
# Every refusal condition the rule carries, the control that fails when the
# condition is removed, and where that control lives. One row per condition,
# and every row was verified by removing that condition and watching that
# control go red. The test file is 675 of 700 and this file is 700 of 700, so
# the ledger lives here where a reader of the rule meets it.
CONDITIONS_PINNED = (
    ('C1', 'the member set is read off types.FrameType',
     'refuses_every_frame_member_the_interpreter_carries'),
    ('C2', 'a receiver resolved to a live frame is refused on that account',
     'refuses_a_resolved_frame_receiver'),
    ('C3', 'a frame read is refused at all', 'five controls, C1-C11 aside'),
    ('C4', 'the call arm: a callee that is itself a call',
     'the plant row "callee the audit cannot see is a call"'),
    ('C5', 'the call arm: a starred expansion',
     'the plant row "starred expansion hides the argument list"'),
    ('C6', 'the call arm: a proven getattr whose name is an expression',
     'the plant row "getattr whose name is an expression"'),
    ('C7', "resolve_origin sees a literal, which is the call arm's price",
     'accepts_a_real_call_naming_the_namespace_key'),
    ('C8', 'the walk starts at the module, not at a callable',
     'covers_a_second_package_module'),
    ('C9', 'a name a local scope binds is unproven',
     'refuses_a_frame_read_on_a_proven_receiver'),
    ('C10', 'the subscript arm REFUSES a key it cannot read',
     'four plant rows, "computed key, ..."'),
    ('C11', 'the subscript arm EXEMPTS a range or a tuple key',
     'refuses_frame_namespaces_in_the_real_package, 11 slices'),
)

FRAME_NAMESPACE_PLANTS = (
    ('attribute getter', 'import operator\n', 'def do_reload(args):\n',
     "def do_reload(args):\n    _ = operator.attrgetter('_getframe')(sys)()"
     ".f_locals['args'].undeclared_probe\n",
     "operator.attrgetter('_getframe')(sys)().f_locals['args']"),
    ('sibling helper', '', 'def do_reload(args):\n',
     'def _reached_namespace():\n'
     "    return sys._getframe(1).f_locals['args']\n\n\n"
     'def do_reload(args):\n'
     '    _ = _reached_namespace().undeclared_probe\n',
     "sys._getframe(1).f_locals['args']"),
    ('plain frame spelling', '', 'def do_reload(args):\n',
     "def do_reload(args):\n    _ = sys._getframe(1).f_locals['args']"
     '.undeclared_probe\n', "sys._getframe(1).f_locals['args']"),
    ('container index', '', 'def do_reload(args):\n',
     "def do_reload(args):\n    _ = {'a': sys}['a']._getframe().f_locals"
     "['args'].undeclared_probe\n",
     "{'a': sys}['a']._getframe().f_locals['args']"),
    ('member the old resolver never named', '', 'def do_reload(args):\n',
     "def do_reload(args):\n    _ = sys._getframe(1).f_globals['args']"
     '.undeclared_probe\n', "sys._getframe(1).f_globals['args']"),
    ('constant-string carrier', '', 'def do_reload(args):\n',
     "def do_reload(args):\n    _ = getattr(sys._getframe(), 'f_locals')"
     "['args'].undeclared_probe\n",
     "getattr(sys._getframe(), 'f_locals')['args']"),
    ('constant-string carrier, no args subscript', '',
     'def do_reload(args):\n',
     "def do_reload(args):\n    _ = getattr(sys._getframe(), 'f_locals')"
     ".get('undeclared_probe')\n", "getattr(sys._getframe(), 'f_locals')"),
    ('method of a class in the same module', '', 'def do_reload(args):\n',
     'class _Reach:\n'
     '    def namespace(self):\n'
     "        return sys._getframe(2).f_locals['args']\n\n\n"
     'def do_reload(args):\n'
     '    _ = _Reach().namespace().undeclared_probe\n',
     "sys._getframe(2).f_locals['args']"),
    ('module-level lambda',
     "_namespace = lambda: sys._getframe(1).f_locals['args']\n",
     'def do_reload(args):\n',
     'def do_reload(args):\n    _ = _namespace().undeclared_probe\n',
     "sys._getframe(1).f_locals['args']"),
    ('method of a class nested in the module',
     'class _Outer:\n    class _Inner:\n        def ns(self):\n'
     "            return sys._getframe(2).f_locals['args']\n",
     'def do_reload(args):\n',
     'def do_reload(args):\n'
     '    _ = _Outer._Inner().ns().undeclared_probe\n',
     "sys._getframe(2).f_locals['args']"),
    ('def under a module-level if',
     'if True:\n    def _helper():\n'
     "        return sys._getframe(1).f_locals['args']\n",
     'def do_reload(args):\n',
     'def do_reload(args):\n    return _helper().undeclared_probe\n',
     "sys._getframe(1).f_locals['args']"),
    ('callee the audit cannot prove', '', 'def do_reload(args):\n',
     'def do_reload(args):\n'
     '    getattr = object.__getattribute__\n'
     "    _ = getattr(sys._getframe(), 'f_locals').get('undeclared_probe')\n",
     "getattr(sys._getframe(), 'f_locals')"),
    ('callee the audit cannot see is a call', 'import operator\n',
     'def do_reload(args):\n',
     "def do_reload(args):\n"
     "    _ = operator.attrgetter('f_locals')(sys._getframe())"
     ".get('args').undeclared_probe\n",
     "operator.attrgetter('f_locals')(sys._getframe())"),
    ('starred expansion hides the argument list', '', 'def do_reload(args):\n',
     'def do_reload(args):\n'
     "    _ = getattr(*(sys._getframe(), 'f_locals')).get('args')"
     '.undeclared_probe\n',
     "getattr(*(sys._getframe(), 'f_locals'))"),
    ('getattr whose name is an expression', '', 'def do_reload(args):\n',
     "def do_reload(args):\n"
     "    _ = getattr(sys._getframe(), 'f_' + 'locals').get('args')"
     '.undeclared_probe\n',
     "getattr(sys._getframe(), 'f_' + 'locals')"),
    # The unreadable-subscript arm's four escapes: a member name the audit
    # cannot read, produced four ways, each a sole catcher for that arm.
    ('computed key, concatenation', "member = 'f_locals'\n",
     'def do_reload(args):\n',
     'def do_reload(args):\n    frame = sys._getframe()\n'
     "    _ = frame['f_' + 'locals'].get('args').undeclared_probe\n",
     "frame['f_' + 'locals']"),
    ('computed key, a bound name', "member = 'f_locals'\n",
     'def do_reload(args):\n',
     'def do_reload(args):\n    frame = sys._getframe()\n'
     "    _ = frame[member].get('args').undeclared_probe\n",
     'frame[member]'),
    ('computed key, a call', "member = 'f_locals'\n",
     'def do_reload(args):\n',
     'def do_reload(args):\n    frame = sys._getframe()\n'
     "    _ = frame[str(('f_locals',))].get('args').undeclared_probe\n",
     "frame[str(('f_locals',))]"),
    ('computed key, an f-string', "kind = 'locals'\n",
     'def do_reload(args):\n',
     'def do_reload(args):\n    frame = sys._getframe()\n'
     "    _ = frame[f'f_{kind}'].get('args').undeclared_probe\n",
     "frame[f'f_{kind}']"),
    ('class body', "class _Reach:\n    NS = sys._getframe()['f_locals']\n",
     'def do_reload(args):\n',
     'def do_reload(args):\n    _ = _Reach.NS.undeclared_probe\n',
     "sys._getframe()['f_locals']"),
    ('mapping key, no member selected', '', 'def do_reload(args):\n',
     'def do_reload(args):\n    holder = helper()\n'
     "    _ = holder['args'].undeclared_probe\n", "holder['args']"),
)


CLI_ANCHOR = 'def do_reload(args):\n'
_FRAME_DESCRIPTORS = (types.GetSetDescriptorType, types.MemberDescriptorType)
# Computed from the interpreter, never read out of the resolver: a hand-written
# set that dropped members has to fail the loop below, not pass it quietly.
FRAME_MEMBERS = tuple(sorted(
    name for name, member in vars(types.FrameType).items()
    if isinstance(member, _FRAME_DESCRIPTORS)))


def plant_in_reload(base, replacement, prelude=''):
    """Splice a replacement for the real do_reload into the real module."""
    source = base
    if prelude:
        assert source.count('import json\n') == 1, prelude
        source = source.replace('import json\n', 'import json\n' + prelude, 1)
    assert source.count(CLI_ANCHOR) == 1, replacement
    return source.replace(CLI_ANCHOR, replacement, 1)


def assert_every_frame_namespace_plant_refused(read_module, base):
    """Each plant, spliced into the real handler module, is refused once.

    The plants run against the real ``commands_eval.py`` through the real
    package walk, not against a synthetic tree, so a rule that only fires on a
    fixture's shape cannot pass this.
    """
    for name, prelude, _anchor, replacement, receiver in \
            FRAME_NAMESPACE_PLANTS:
        escapes = read_module(
            {'commands_eval': plant_in_reload(base, replacement, prelude)})
        assert len(escapes) == 1, (name, escapes)
        assert escapes[0].endswith(f': {receiver}'), (name, escapes)


def assert_domain_covers_a_second_module(read_module, package):
    """A frame read in a module other than the handler's is refused.

    The headline claim is the package, and every other plant splices into the
    handler's own module, so a walk narrowed to that one module satisfies all
    of them. This plants into a module that holds no handler, so the only thing
    it can be catching is the walk's module coverage.
    """
    source = (package / 'transport.py').read_text(encoding='utf-8')
    anchor = 'def token():\n'
    assert source.count(anchor) == 1
    body = ("def _read_namespace():\n"
            "    return sys._getframe()['f_locals']\n\n\n"
            'def token():\n')
    escapes = read_module(
        {'transport': source.replace(anchor, body, 1)})
    assert escapes == [
        "transport._read_namespace: sys._getframe()['f_locals']"], escapes


def assert_every_frame_member_refused(read_module, base):
    """Each member types.FrameType carries, planted, is refused once.

    The member list is this module's own reading of the interpreter, so
    replacing the resolver's derivation with a short literal drops the members
    it lost and fails here — which is the control the closed-domain claim
    needs and a hand run of the same loop is not.
    """
    for member in FRAME_MEMBERS:
        body = (f'def do_reload(args):\n    _ = sys._getframe(1).{member}'
                "['undeclared_probe']\n")
        escapes = read_module({'commands_eval': plant_in_reload(base, body)})
        assert escapes == [
            f'commands_eval.do_reload: sys._getframe(1).{member}'], (
                member, escapes)


def assert_namespace_key_call_accepted(read_module, base):
    """A call naming the namespace key is a path, not a read.

    The negative control on the tree the guard actually runs over: 131 calls
    in the CLI pass a constant string as a second argument and the call arm
    has to leave every one of them alone. Two receivers, because they are
    exempt by different rules and only the second exercises the arm's member
    test — a literal receiver is decided by the origin resolver, so a control
    with only that shape passes with the arm admitting the key, which is a
    control that stopped testing what it names.
    """
    shapes = {
        'literal receiver, namespace key':
            "def do_reload(args):\n    api('GET', 'args')\n",
        'unproven receiver, namespace key':
            "def do_reload(args):\n    cmd_id = 'x'\n"
            "    send(cmd_id, 'args')\n",
        'literal receiver, a frame member':
            "def do_reload(args):\n    api('GET', 'f_locals')\n",
        'unproven receiver, a frame member':
            "def do_reload(args):\n    send('t', 'f_code')\n"}
    for name, body in shapes.items():
        escapes = read_module({'commands_eval': plant_in_reload(base, body)})
        assert escapes == [], (name, escapes)


def assert_resolved_frame_receiver_refused(read_module, base, frame):
    """A receiver the audit resolves to a live frame is refused on that count.

    The only case in which a name the audit CAN see still has to be refused,
    and the one that keeps the frame-type test load-bearing.
    """
    body = ("def do_reload(args):\n"
            "    _ = HELD.f_locals.get('args').undeclared_probe\n")
    escapes = read_module(
        {'commands_eval': plant_in_reload(base, body)},
        extra_globals={'commands_eval': {'HELD': frame}})
    assert escapes == ['commands_eval.do_reload: HELD.f_locals'], escapes


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
