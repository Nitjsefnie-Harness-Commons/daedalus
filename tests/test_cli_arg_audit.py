#!/usr/bin/env python3
"""The CLI namespace-escape audit's machinery and its focused cases.

The repository-wide check is ``test_cli_handlers_read_only_declared_args``
below; the rest is the visitor it drives and the small synthetic handlers
each rule is pinned with, so a change that weakens or over-tightens the
audit fails one of these cheap tests instead of hiding inside the
39-handler scan.

The audit refuses reads through the namespace parameter's own name outside
four permitted shapes: direct attributes, constant-name ``getattr``,
constant-name ``hasattr``, and constant mapping reads through ``vars(args)``
or ``args.__dict__``. It also refuses the named reflective calls ``locals()``,
``globals()``, ``eval``, ``exec`` and no-argument ``vars()``. Frame routes are
resolved through in-function imports, global names, module attributes,
exact-class attributes (including ``staticmethod`` and ``classmethod``),
constant list/tuple/dict subscripts, constant-key ``.get`` on an exact dict or
module ``__dict__``, and constant-name ``getattr``/``vars`` calls. Unresolved
``_getframe`` and ``currentframe`` spellings are refused outright. Every other
route is outside what this audit checks, including other container types, the
iterator protocol, comprehension results, instance attribute reads,
``operator.attrgetter``, and any name built at runtime.
"""
import argparse
import ast
import inspect
import sys
import textwrap
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _cli_arg_audit_support import resolve_frame_value  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

# `do_tabs` reads args.json through getattr(); keep that access documented
# even though the audit now resolves constant indirect reads itself.
KNOWN_INDIRECT_ARG_READS = (
    ('tabs', 'json', 'do_tabs'),
)


def _namespace_dests(actions):
    """Return destinations that argparse puts on a parsed namespace."""
    # Help and version actions print and exit instead of storing, so their
    # destinations never land on the namespace; an option declared with
    # default=SUPPRESS is absent only while omitted, so its dest stays.
    never_store = (argparse._HelpAction, argparse._VersionAction)
    return {action.dest for action in actions
            if action.dest != argparse.SUPPRESS
            and not isinstance(action, never_store)}


def _constant_string(node):
    """Return the string of a constant node, or None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _binding_names(target):
    """Return the names a binding target assigns."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names = set()
        for element in target.elts:
            names |= _binding_names(element)
        return names
    if isinstance(target, ast.Starred):
        return _binding_names(target.value)
    return set()


def _rebinds(function, name):
    """Return True when a nested function's own parameters bind ``name``."""
    arguments = function.args
    parameters = (arguments.posonlyargs + arguments.args
                  + arguments.kwonlyargs
                  + [arguments.vararg, arguments.kwarg])
    return name in {
        parameter.arg for parameter in parameters if parameter is not None}


def _scope_binds(function, name):
    """Return True when a nested callable's own scope binds ``name``."""
    if _rebinds(function, name):
        return True
    body = function.body
    stack = list(body) if isinstance(body, list) else [body]
    binds = False
    reaches_handler = False
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Nonlocal) and name in node.names:
            reaches_handler = True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            binds = binds or node.name == name
            continue                        # the body is another scope
        elif isinstance(node, ast.Lambda):
            continue                        # so is a lambda's
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                               ast.GeneratorExp)):
            # A comprehension's targets bind inside it; a walrus elsewhere
            # in one binds out here.
            if isinstance(node, ast.DictComp):
                stack.extend((node.key, node.value))
            else:
                stack.append(node.elt)
            for generator in node.generators:
                stack.append(generator.iter)
                stack.extend(generator.ifs)
            continue
        elif isinstance(node, ast.NamedExpr):
            binds = binds or node.target.id == name
        elif isinstance(node, ast.Assign):
            binds = binds or any(
                name in _binding_names(target) for target in node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For,
                               ast.AsyncFor)):
            binds = binds or name in _binding_names(node.target)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            binds = binds or any(
                item.optional_vars is not None
                and name in _binding_names(item.optional_vars)
                for item in node.items)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            binds = binds or any(
                (alias.asname or alias.name.split('.')[0]) == name
                for alias in node.names)
        elif isinstance(node, ast.ExceptHandler):
            binds = binds or node.name == name
        stack.extend(ast.iter_child_nodes(node))
    return binds and not reaches_handler


def _comprehension_shadows(comprehension, name):
    """Return True when one of a comprehension's targets binds ``name``."""
    return any(
        name in _binding_names(generator.target)
        for generator in comprehension.generators)


_FRAME_ROUTE_ATTRS = {'sys': '_getframe', 'inspect': 'currentframe'}
_FRAME_ROUTE_MODULES = {'sys': sys, 'inspect': inspect}
_FRAME_ROUTE_OBJECTS = (sys._getframe, inspect.currentframe)
_UNRESOLVED = object()


def _is_frame_route(value):
    """Return True only for the two frame-route objects by identity."""
    return any(value is route for route in _FRAME_ROUTE_OBJECTS)


def _frame_imports(function):
    """Return frame modules and routes imported inside ``function``."""
    bindings = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _FRAME_ROUTE_MODULES:
                    bindings[alias.asname or alias.name] = \
                        _FRAME_ROUTE_MODULES[alias.name]
        elif isinstance(node, ast.ImportFrom):
            route = _FRAME_ROUTE_ATTRS.get(node.module)
            if route is None:
                continue
            for alias in node.names:
                if alias.name == route:
                    bindings[alias.asname or alias.name] = getattr(
                        _FRAME_ROUTE_MODULES[node.module], route)
    return bindings


def _frame_value(node, function, handler_globals, imports):
    """Resolve constant access without executing handler source."""
    return resolve_frame_value(
        node, function, handler_globals, imports, _UNRESOLVED,
        _scope_binds, _constant_string)


def _unknown_frame_route(node, function, handler_globals, imports):
    """Refuse an unresolved canonical frame-route spelling."""
    if isinstance(node, ast.Name):
        return (node.id in _FRAME_ROUTE_ATTRS.values()
                and _frame_value(node, function, handler_globals, imports)
                is _UNRESOLVED)
    return (isinstance(node, ast.Attribute)
            and node.attr in _FRAME_ROUTE_ATTRS.values()
            and _frame_value(node.value, function, handler_globals, imports)
            is _UNRESOLVED)


def _frame_route_access(node, function, handler_globals, imports):
    """Return True when ``node`` names or accesses a frame route."""
    if _is_frame_route(_frame_value(
            node, function, handler_globals, imports)):
        return True
    if isinstance(node, ast.Call):
        if _is_frame_route(_frame_value(
                node.func, function, handler_globals, imports)):
            return True
        if (isinstance(node.func, ast.Attribute)
                and _is_frame_route(_frame_value(
                    node.func.value, function, handler_globals, imports))):
            return True
        return _unknown_frame_route(
            node.func, function, handler_globals, imports)
    if not isinstance(node, (ast.Name, ast.Attribute)):
        return False
    if _is_frame_route(_frame_value(
            node, function, handler_globals, imports)):
        return True
    if (isinstance(node, ast.Attribute)
            and _is_frame_route(_frame_value(
                node.value, function, handler_globals, imports))):
        return True
    return _unknown_frame_route(node, function, handler_globals, imports)


def _reflective_call(node, function, handler_globals, imports):
    """Return True for a reflective or frame-route access."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in ('locals', 'globals', 'eval', 'exec'):
            return True
        if node.func.id == 'vars' and not node.args:
            return True
    return _frame_route_access(node, function, handler_globals, imports)


def _constant_dict_subscript(node):
    """Return (attribute, node) for a constant ``node[...]`` read."""
    parent = node._parent
    if (isinstance(parent, ast.Subscript) and parent.value is node
            and isinstance(parent.ctx, ast.Load)):
        attribute = _constant_string(parent.slice)
        if attribute is not None:
            return attribute, parent
    return None


def _permitted_namespace_read(name):
    """Resolve a permitted namespace read, or None for an escape."""
    parent = name._parent
    if isinstance(parent, ast.Attribute) and parent.value is name:
        if not isinstance(parent.ctx, ast.Load):
            return None
        if parent.attr != '__dict__':
            return parent.attr, parent
        return _constant_dict_subscript(parent)
    if not (isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.args and parent.args[0] is name
            and not parent.keywords):
        return None
    if parent.func.id == 'vars' and len(parent.args) == 1:
        return _constant_dict_subscript(parent)
    arities = {'getattr': (2, 3), 'hasattr': (2,)}.get(parent.func.id)
    if not (arities and len(parent.args) in arities
            and not any(isinstance(argument, ast.Starred)
                        for argument in parent.args)):
        return None
    attribute = _constant_string(parent.args[1])
    if attribute is None:
        return None
    return attribute, parent


def _handler_arg_violations(function, args_name, allowed,
                            handler_globals=None):
    """Return (reads, violations) for one handler's namespace use."""
    for node in ast.walk(function):
        for child in ast.iter_child_nodes(node):
            child._parent = node
    if handler_globals is None:
        handler_globals = globals()
    frame_imports = _frame_imports(function)
    reads = {}
    violations = []

    def check(node):
        # One node evaluated where the parameter name is live.
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda))
                and _scope_binds(node, args_name)):
            visit_header(node)
            return
        if (isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                              ast.GeneratorExp))
                and _comprehension_shadows(node, args_name)):
            check(node.generators[0].iter)
            return
        if _reflective_call(
                node, function, handler_globals, frame_imports):
            violations.append(f'namespace escape: {ast.unparse(node)}')
            return
        if isinstance(node, ast.Name) and node.id == args_name:
            permitted = _permitted_namespace_read(node)
            if permitted is None:
                violations.append(
                    f'namespace escape: {ast.unparse(node._parent)}')
            else:
                attribute, construct = permitted
                reads.setdefault(attribute, set()).add(
                    ast.unparse(construct))
        visit(node)

    def visit(node):
        for child in ast.iter_child_nodes(node):
            check(child)

    def visit_header(nested):
        # Decorators, defaults, annotations and the return annotation are
        # evaluated where the callable is defined, not where its body runs.
        for decorator in getattr(nested, 'decorator_list', []):
            check(decorator)
        arguments = nested.args
        for default in arguments.defaults:
            check(default)
        for kw_default in arguments.kw_defaults:
            if kw_default is not None:
                check(kw_default)
        parameters = (arguments.posonlyargs + arguments.args
                      + arguments.kwonlyargs
                      + [arguments.vararg, arguments.kwarg])
        for parameter in parameters:
            if parameter is not None and parameter.annotation is not None:
                check(parameter.annotation)
        returns = getattr(nested, 'returns', None)
        if returns is not None:
            check(returns)

    visit(function)
    for attribute, constructs in sorted(reads.items()):
        if attribute not in allowed:
            violations.extend(sorted(constructs))
    return reads, violations


def _audit_fake_handler(body, allowed=('cmd', 'json')):
    """Audit ``body`` as a fake handler taking ``args``; return violations."""
    function = ast.parse(
        'def fake(args):\n' + textwrap.indent(body, '    ')).body[0]
    _, violations = _handler_arg_violations(function, 'args', set(allowed))
    return violations


def _mutated_cli_tabs(package_name, module_prelude, body):
    """Compile one real handler module in memory with a mutation."""
    source = (_util.ROOT / 'daedalus_cli' / 'commands_eval.py').read_text(
        encoding='utf-8')
    source = source.replace(
        'import sys\n', f'import sys\n{module_prelude}\n', 1)
    source = source.replace(
        'def do_tabs(args):\n', f'def do_tabs(args):\n    {body}\n', 1)
    filename = f'<mutated daedalus cli handler {package_name}>'
    module_name = f'{package_name}.commands_eval'
    module = types.ModuleType(module_name)
    module.__file__ = filename
    module.__package__ = 'daedalus_cli'
    module.__source__ = source
    sys.modules[module_name] = module
    # pylint: disable=exec-used
    exec(compile(source, filename, 'exec'), module.__dict__)
    return module


def _audit_real_tabs_handler(handler_module):
    """Audit an in-memory module's actual dispatched ``do_tabs`` handler."""
    from daedalus_cli.parser import build_parser

    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction))
    allowed = _namespace_dests(parser._actions) | _namespace_dests(
        subparsers.choices['tabs']._actions)
    handler = handler_module.do_tabs
    tree = ast.parse(handler_module.__source__)
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    args_name = (function.args.posonlyargs + function.args.args)[0].arg
    _, violations = _handler_arg_violations(
        function, args_name, allowed, handler.__globals__)
    return violations


def _assert_real_tabs_dispatch_crashes(handler_module):
    """The same mutation must fail real dispatch, not just the audit."""
    from daedalus_cli import cli

    original_handler = cli.DISPATCH['tabs']
    original_argv = sys.argv
    cli.DISPATCH['tabs'] = handler_module.do_tabs
    sys.argv = ['daedalus', 'tabs']
    try:
        cli.main()
    except (AttributeError, TypeError) as error:
        if isinstance(error, AttributeError):
            assert str(error) == \
                "'Namespace' object has no attribute 'undeclared_probe'", error
    else:
        assert False, 'real dispatch did not read undeclared_probe'
    finally:
        cli.DISPATCH['tabs'] = original_handler
        sys.argv = original_argv


def test_cli_audit_reports_suppressed_destination(tmp):
    from daedalus_cli.parser import build_parser

    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction))
    allowed = _namespace_dests(parser._actions) | _namespace_dests(
        subparsers.choices['tabs']._actions)
    violations = _audit_fake_handler('args.version', allowed)
    assert violations == ['args.version'], violations


def test_cli_audit_checks_permitted_reads_by_attribute(tmp):
    cases = [
        ('args.json', 'args.undeclared_probe'),
        ("getattr(args, 'json')", "getattr(args, 'undeclared_probe')"),
        ("getattr(args, 'json', False)",
         "getattr(args, 'undeclared_probe', None)"),
        ("vars(args)['json']", "vars(args)['undeclared_probe']"),
        ("args.__dict__['json']", "args.__dict__['undeclared_probe']"),
        ("hasattr(args, 'json')", "hasattr(args, 'undeclared_probe')"),
    ]
    for declared, undeclared in cases:
        assert _audit_fake_handler(declared) == [], declared
        assert _audit_fake_handler(undeclared) == [undeclared], undeclared


def test_cli_audit_reports_namespace_escapes(tmp):
    cases = [
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
    ]
    for body, construct in cases:
        assert _audit_fake_handler(body) == [
            f'namespace escape: {construct}'], (body, construct)


def test_cli_audit_respects_inner_scope_bindings(tmp):
    shadowed = (
        'def inner(args):\n'
        '    args.undeclared_probe\n'
        'shadow = lambda args: args.undeclared_probe')
    assert _audit_fake_handler(shadowed) == []
    closure = (
        'def inner():\n'
        '    args.undeclared_probe')
    assert _audit_fake_handler(closure) == ['args.undeclared_probe']


def test_cli_audit_sees_shadowing_callable_defaults(tmp):
    cases = [
        'def inner(args=args):\n    return args.undeclared_probe\ninner()',
        'f = lambda args=args: args.undeclared_probe\nf()',
    ]
    for body in cases:
        assert _audit_fake_handler(body) == [
            'namespace escape: args=args'], body


def test_cli_audit_sees_shadowing_decorators_and_annotations(tmp):
    decorated = '@consume(args)\ndef inner(args):\n    pass'
    assert _audit_fake_handler(decorated) == [
        'namespace escape: consume(args)']
    annotated = 'def inner(args: args.undeclared_probe):\n    pass'
    assert _audit_fake_handler(annotated) == ['args.undeclared_probe']


def test_cli_audit_refuses_reflective_namespace_access(tmp):
    cases = [
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
    ]
    for body, construct in cases:
        assert _audit_fake_handler(body) == [
            f'namespace escape: {construct}'], body

    calls = []

    class Descriptor:
        def __get__(self, obj, objtype=None):
            calls.append('descriptor __get__ invoked')
            return sys._getframe

    class FrameRoutes:
        active = Descriptor()

    function = ast.parse(
        "def do_tabs(args):\n    FrameRoutes.active()\n").body[0]
    attr_node = function.body[0].value.func
    value = _frame_value(
        attr_node, function, {'FrameRoutes': FrameRoutes}, {})
    assert value is FrameRoutes.__dict__['active']
    assert calls == []


def test_cli_audit_refuses_frame_routes_in_real_handler_module(tmp):
    """Real ``do_tabs`` mutations prove audit refusal and dispatch hazards."""
    cases = [
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
        ('class FrameRoutes:\n    active = sys._getframe',
         "_ = FrameRoutes.active().f_locals['args'].undeclared_probe",
         'FrameRoutes.active()'),
        ('class FrameRoutes:\n    active = staticmethod(sys._getframe)',
         "_ = FrameRoutes.active().f_locals['args'].undeclared_probe",
         'FrameRoutes.active()'),
        ('class FrameRoutes:\n    active = classmethod(sys._getframe)',
         "_ = FrameRoutes.active().f_locals['args'].undeclared_probe",
         'FrameRoutes.active()'),
        ('', "_ = sys.__dict__['_getframe']()"
         ".f_locals['args'].undeclared_probe", "sys.__dict__['_getframe']()"),
        ('', "_ = sys.__dict__.get('_getframe')()"
         ".f_locals['args'].undeclared_probe",
         "sys.__dict__.get('_getframe')()"),
        ('', "_ = vars(sys)['_getframe']()"
         ".f_locals['args'].undeclared_probe", "vars(sys)['_getframe']()"),
    ]
    for index, (module_prelude, body, construct) in enumerate(cases):
        package_name = f'mutated_cli_{index}'
        handler_module = _mutated_cli_tabs(
            package_name, module_prelude, body)
        try:
            violations = _audit_real_tabs_handler(handler_module)
            assert any(construct in violation for violation in violations), \
                (module_prelude, body, violations)
            _assert_real_tabs_dispatch_crashes(handler_module)
        finally:
            sys.modules.pop(handler_module.__dict__['__name__'], None)

    # Documented gaps, not oversights: neither route is resolved.
    gaps = [
        ('FRAME_ROUTES = [sys._getframe]',
         "_ = [route for route in FRAME_ROUTES][0]()"
         ".f_locals['args'].undeclared_probe"),
        ('class FrameRoutes:\n    active = sys._getframe',
         "_ = FrameRoutes().active().f_locals['args'].undeclared_probe"),
    ]
    for index, (module_prelude, body) in enumerate(gaps):
        handler_module = _mutated_cli_tabs(
            f'known_gap_cli_{index}', module_prelude, body)
        try:
            assert _audit_real_tabs_handler(handler_module) == []
            _assert_real_tabs_dispatch_crashes(handler_module)
        finally:
            sys.modules.pop(handler_module.__dict__['__name__'], None)


def test_cli_audit_respects_comprehension_shadowing(tmp):
    assert _audit_fake_handler('_ = [args.json for args in values]') == []
    assert _audit_fake_handler('[args.json for value in values]') == []
    assert _audit_fake_handler("vars(args)['json']") == []


def test_cli_audit_sees_a_shadowing_comprehensions_iterable(tmp):
    assert _audit_fake_handler(
        '_ = [args.value for args in args.undeclared_probe]') == [
        'args.undeclared_probe']


def test_cli_audit_respects_nested_local_bindings(tmp):
    shadowed = (
        'def inner():\n'
        "    args = type('T', (), {'json': True})()\n"
        '    return args.json')
    assert _audit_fake_handler(shadowed) == []


def test_cli_handlers_read_only_declared_args(tmp):
    from daedalus_cli.cli import DISPATCH
    from daedalus_cli.parser import build_parser

    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction))
    global_dests = _namespace_dests(parser._actions)
    dispatch_only = sorted(set(DISPATCH) - set(subparsers.choices))
    parser_only = sorted(set(subparsers.choices) - set(DISPATCH))
    assert not dispatch_only and not parser_only, (
        f'dispatch without parser: {dispatch_only}; '
        f'parser without dispatch: {parser_only}')
    violations = []
    handler_details = {}
    for name, handler in DISPATCH.items():
        subparser = subparsers.choices[name]
        allowed = global_dests | _namespace_dests(subparser._actions)
        tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
        positional = function.args.posonlyargs + function.args.args
        args_name = positional[0].arg
        reads, handler_violations = _handler_arg_violations(
            function, args_name, allowed, handler.__globals__)
        handler_details[name] = {'handler': handler.__qualname__,
                                 'allowed': allowed, 'reads': reads}
        violations.extend(
            (name, construct, handler.__qualname__)
            for construct in handler_violations)

    stale_reads = []
    for command, attribute, handler_name in KNOWN_INDIRECT_ARG_READS:
        detail = handler_details.get(command)
        if detail is None or detail['handler'] != handler_name:
            stale_reads.append(
                f'{command}: known indirect read refers to no handler')
            continue
        if attribute not in detail['reads']:
            stale_reads.append(
                f'{command}: {attribute} absent from handler source')
        if attribute not in detail['allowed']:
            stale_reads.append(
                f'{command}: {attribute} not declared by parser')
    assert not stale_reads, '\n'.join(stale_reads)

    details = '\n'.join(
        f'{name}: {construct} read by {handler}'
        for name, construct, handler in violations)
    assert not violations, f'undeclared CLI argument reads:\n{details}'


def test_cli_dispatch_matches_parser_subcommands(tmp):
    from daedalus_cli.cli import DISPATCH
    from daedalus_cli.parser import build_parser

    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction))
    assert set(DISPATCH) == set(subparsers.choices)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
