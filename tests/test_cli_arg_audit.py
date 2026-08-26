#!/usr/bin/env python3
"""Static proof that CLI handlers read only parser-declared attributes.

``test_cli_handlers_read_only_declared_args`` scans every dispatched handler;
the focused controls make each classifier rule and resolver boundary
executable.

DECLARED contains each non-help/version action whose destination is not
``argparse.SUPPRESS``, plus every ``set_defaults`` key. GUARANTEED PRESENT
contains a destination when an action has a non-suppressed default, is required
on every successful parse, or is a positional ``REMAINDER``; parser defaults
are guaranteed too. Same-destination actions combine those properties.

Direct attributes, exact ``vars(args)``/``args.__dict__`` subscripts, and bare
``getattr`` without a default require GUARANTEED. ``hasattr``, defaulted
``getattr``, and constant-key mapping ``.get`` require DECLARED. These calls
receive builtin semantics only by identity: no enclosing callable or
comprehension may bind the bare name at that evaluation point. A module global
or the function's effective builtins must resolve to the exact builtin.
Attribute calls require the exact ``builtins`` module. Callable headers use
outer scope; every other live use of the parameter escapes. Bare reflective
names are refused even when shadowed; exact ``builtins`` forms are refused too.

Frame routes resolve through supplied imports, unshadowed globals, exact
module/class attributes, module ``__dict__``, exact list/tuple subscripts and
slices, constant-key dict reads/``.get``, and bare constant-name ``getattr``.
One-argument ``vars`` resolves exact modules/classes; class mapping-proxy reads
stay outside. Exact static/class methods unwrap; other descriptors stay raw.
Canonical unresolved ``_getframe``/``currentframe`` spellings are refused.

An integer index is an ``isinstance(value, int)`` value, including ``bool``.
Unary ``+``/``-`` executes through arbitrary stacks, so ``+True`` is integer
``1``. Slice bounds use the same definition and exact sliced sequences remain
eligible for another subscript.

Resolution never runs handler code. Outside are non-exact descriptors,
``partial``, traceback frames, other containers/iterators, comprehension
results, instance attributes, ``attrgetter``, runtime names, mapping-proxy
reads, binary/call indices, and external frame acquisition. Each named family
has a known-gap control, and every known-gap control belongs to a named family.
"""
import argparse
import ast
import builtins  # pylint: disable=unused-import
import inspect
import sys
import textwrap
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _cli_arg_audit_support as audit_support  # noqa: E402

sys.path.insert(0, str(_util.ROOT))

_REAL_STORAGE_DISPATCH_CASES = (
    ('optional',
     "PROBE_READS.append(getattr(args, 'undeclared_probe', None)); return",
     (), (None,)),
    ('remainder',
     'PROBE_READS.append(args.undeclared_probe); return', (), ([],)),
    ('required-option',
     'PROBE_READS.append(args.undeclared_probe); return',
     ('--undeclared-probe', 'present'), ('present',)),
)


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


def _comprehension_shadows(comprehension, name, child=None,
                           before_target=False):
    """Return whether a target has bound ``name`` at one child."""
    generators = comprehension.generators
    if child in generators:
        end = generators.index(child) + (not before_target)
        generators = generators[:end]
    return any(
        name in _binding_names(generator.target)
        for generator in generators)


_FRAME_ROUTE_ATTRS = {'sys': '_getframe', 'inspect': 'currentframe'}
_FRAME_ROUTE_MODULES = {'sys': sys, 'inspect': inspect}
_UNRESOLVED = object()


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
    return audit_support.resolve_frame_value(
        node, function, handler_globals, imports, _UNRESOLVED,
        _scope_binds, audit_support.constant_string)


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
    if audit_support.is_frame_route(_frame_value(
            node, function, handler_globals, imports)):
        return True
    if isinstance(node, ast.Call):
        if audit_support.is_frame_route(_frame_value(
                node.func, function, handler_globals, imports)):
            return True
        if (isinstance(node.func, ast.Attribute)
                and audit_support.is_frame_route(_frame_value(
                    node.func.value, function, handler_globals, imports))):
            return True
        return _unknown_frame_route(
            node.func, function, handler_globals, imports)
    if not isinstance(node, (ast.Name, ast.Attribute)):
        return False
    if audit_support.is_frame_route(_frame_value(
            node, function, handler_globals, imports)):
        return True
    if (isinstance(node, ast.Attribute)
            and audit_support.is_frame_route(_frame_value(
                node.value, function, handler_globals, imports))):
        return True
    return _unknown_frame_route(node, function, handler_globals, imports)


def _reflective_call(node, function, handler_globals, imports,
                     inspect_frame_routes=True):
    if audit_support.reflective_builtin_call(
            node, function, handler_globals, _scope_binds,
            _comprehension_shadows):
        return True
    return inspect_frame_routes and _frame_route_access(
        node, function, handler_globals, imports)


def _handler_arg_violations(function, args_name, declared, guaranteed,
                            handler_globals=None):
    """Return (reads, violations) for one handler's namespace use."""
    for node in ast.walk(function):
        for child in ast.iter_child_nodes(node):
            child._parent = node
    if handler_globals is None:
        handler_globals = globals()
    frame_imports = _frame_imports(function)
    reads = {}
    read_requirements = {}
    violations = []

    def check(node, inspect_frame_routes=True):
        # One node evaluated where the parameter name is live.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            visit_header(node, inspect_frame_routes)
            if not _scope_binds(node, args_name):
                body = (node.body if isinstance(node.body, list)
                        else [node.body])
                for statement in body:
                    check(statement, False)
            return
        if (isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                              ast.GeneratorExp))
                and _comprehension_shadows(node, args_name)):
            check(node.generators[0].iter, inspect_frame_routes)
            return
        if _reflective_call(
                node, function, handler_globals, frame_imports,
                inspect_frame_routes):
            violations.append(f'namespace escape: {ast.unparse(node)}')
            return
        if isinstance(node, ast.Name) and node.id == args_name:
            permitted = audit_support.permitted_namespace_read(
                node, function, handler_globals, _scope_binds,
                _comprehension_shadows)
            if permitted is None:
                violations.append(
                    f'namespace escape: {ast.unparse(node._parent)}')
            else:
                attribute, construct, needs_presence = permitted
                rendered = ast.unparse(construct)
                reads.setdefault(attribute, set()).add(rendered)
                read_requirements[(attribute, rendered)] = needs_presence
        visit(node, inspect_frame_routes)

    def visit(node, inspect_frame_routes=True):
        for child in ast.iter_child_nodes(node):
            check(child, inspect_frame_routes)

    def visit_header(nested, inspect_frame_routes):
        # Decorators, defaults, annotations and the return annotation are
        # evaluated where the callable is defined, not where its body runs.
        for decorator in getattr(nested, 'decorator_list', []):
            check(decorator, inspect_frame_routes)
        arguments = nested.args
        for default in arguments.defaults:
            check(default, inspect_frame_routes)
        for kw_default in arguments.kw_defaults:
            if kw_default is not None:
                check(kw_default, inspect_frame_routes)
        parameters = (arguments.posonlyargs + arguments.args
                      + arguments.kwonlyargs
                      + [arguments.vararg, arguments.kwarg])
        for parameter in parameters:
            if parameter is not None and parameter.annotation is not None:
                check(parameter.annotation, inspect_frame_routes)
        returns = getattr(nested, 'returns', None)
        if returns is not None:
            check(returns, inspect_frame_routes)

    visit(function)
    for (attribute, construct), needs_presence in sorted(
            read_requirements.items()):
        allowed = guaranteed if needs_presence else declared
        if attribute not in allowed:
            violations.append(construct)
    return reads, violations


def _audit_fake_handler(body, dests=('cmd', 'json'), present=None, scope=None):
    present = dests if present is None else present
    function = ast.parse(
        'def fake(args):\n' + textwrap.indent(body, '    ')).body[0]
    _, violations = _handler_arg_violations(
        function, 'args', set(dests), set(present), scope)
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


def _audit_real_tabs_handler(handler_module, parser=None):
    """Audit an in-memory module's actual dispatched ``do_tabs`` handler."""
    if parser is None:
        from daedalus_cli.parser import build_parser

        parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction))
    declared, guaranteed = audit_support.namespace_dests(parser)
    sub_declared, sub_guaranteed = audit_support.namespace_dests(
        subparsers.choices['tabs'])
    declared |= sub_declared
    guaranteed |= sub_guaranteed
    handler = handler_module.do_tabs
    tree = ast.parse(handler_module.__source__)
    function = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == 'do_tabs')
    args_name = (function.args.posonlyargs + function.args.args)[0].arg
    _, violations = _handler_arg_violations(
        function, args_name, declared, guaranteed, handler.__globals__)
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
    except AttributeError as error:
        assert str(error) == \
            "'Namespace' object has no attribute 'undeclared_probe'", error
    else:
        assert False, 'real dispatch did not read undeclared_probe'
    finally:
        cli.DISPATCH['tabs'] = original_handler
        sys.argv = original_argv


def test_cli_audit_excludes_dest_suppress_action(tmp):
    from daedalus_cli.parser import build_parser
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction))
    declared, guaranteed = audit_support.namespace_dests(parser)
    sub_declared, sub_guaranteed = audit_support.namespace_dests(
        subparsers.choices['tabs'])
    violations = _audit_fake_handler(
        'args.version', declared | sub_declared,
        guaranteed | sub_guaranteed)
    assert violations == ['args.version'], violations


def test_cli_audit_excludes_default_suppress_action(tmp):
    audit_support.assert_argparse_storage_contract(_audit_fake_handler)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--present', dest='shared', default=False)
    parser.add_argument(
        '--suppressed', dest='shared', default=argparse.SUPPRESS)
    assert audit_support.namespace_dests(parser)[1] == {'shared'}


def test_cli_audit_accepts_guarded_suppress_in_real_dispatch(tmp):
    from daedalus_cli import cli
    for index, (shape, body, argv, expected) in enumerate(
            _REAL_STORAGE_DISPATCH_CASES):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest='cmd', required=True)
        tabs = sub.add_parser('tabs')
        tabs.add_argument('--json', action='store_true')
        audit_support.add_storage_probe(tabs, shape, 'undeclared_probe')
        handler_module = _mutated_cli_tabs(
            f'storage_dispatch_cli_{index}', 'PROBE_READS = []', body)
        original_handler = cli.DISPATCH['tabs']
        original_build_parser = cli.build_parser
        original_argv = sys.argv

        def build_storage_parser(parser=parser):
            return parser

        try:
            assert _audit_real_tabs_handler(handler_module, parser) == []
            cli.DISPATCH['tabs'] = handler_module.__dict__['do_tabs']
            cli.build_parser = build_storage_parser
            sys.argv = ['daedalus', 'tabs', *argv]
            cli.main()
            assert handler_module.__dict__['PROBE_READS'] == list(expected)
        finally:
            cli.DISPATCH['tabs'] = original_handler
            cli.build_parser = original_build_parser
            sys.argv = original_argv
            sys.modules.pop(handler_module.__dict__['__name__'], None)


def test_cli_audit_includes_parser_set_defaults(tmp):
    parser = argparse.ArgumentParser(add_help=False)
    parser.set_defaults(from_defaults=False)
    assert vars(parser.parse_args([])) == {'from_defaults': False}
    declared, guaranteed = audit_support.namespace_dests(parser)
    violations = _audit_fake_handler(
        'args.from_defaults', declared, guaranteed)
    assert violations == [], violations


def test_cli_audit_checks_permitted_reads_by_attribute(tmp):
    for declared, undeclared, _ in \
            audit_support.PERMITTED_NAMESPACE_READ_CASES:
        assert _audit_fake_handler(declared) == [], declared
        assert _audit_fake_handler(undeclared) == [undeclared], undeclared


def test_cli_audit_reports_namespace_escapes(tmp):
    for body, construct in audit_support.NAMESPACE_ESCAPE_CASES:
        assert _audit_fake_handler(body) == [
            f'namespace escape: {construct}'], (body, construct)


def test_cli_audit_requires_builtin_identity(tmp):
    for body, scope, expected in audit_support.BUILTIN_IDENTITY_GLOBAL_CASES:
        assert _audit_fake_handler(body, scope=scope) == list(expected), body


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
    for body in audit_support.SHADOWING_DEFAULT_CASES:
        assert _audit_fake_handler(body) == [
            'namespace escape: args=args'], body


def test_cli_audit_sees_shadowing_decorators_and_annotations(tmp):
    decorated = '@consume(args)\ndef inner(args):\n    pass'
    assert _audit_fake_handler(decorated) == [
        'namespace escape: consume(args)']
    annotated = 'def inner(args: args.undeclared_probe):\n    pass'
    assert _audit_fake_handler(annotated) == ['args.undeclared_probe']


def test_cli_audit_refuses_reflective_namespace_access(tmp):
    for body, construct in audit_support.REFLECTIVE_ESCAPE_CASES:
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


def test_cli_audit_resolver_resolves_dict_get_default(tmp):
    unresolved = object()
    for expression, expected in (('+True', 1), ('-True', -1), ('+False', 0)):
        value = audit_support._constant_value(
            ast.parse(expression, mode='eval').body, unresolved)
        assert (type(value), value) == (int, expected), expression
    resolved = audit_support._constant_value(
        ast.parse('routes[:+True]', mode='eval').body.slice, unresolved)
    assert (type(resolved.stop), resolved.stop) == (int, 1)
    invalid = ast.parse("routes['not-an-index':]", mode='eval').body.slice
    assert audit_support._constant_value(invalid, unresolved) is unresolved
    function = ast.parse(
        "def do_tabs(args):\n"
        "    return ROUTES.get('active', DEFAULT_ROUTE)\n").body[0]
    call = function.body[0].value
    handler_globals = {
        'ROUTES': {'active': sys._getframe},
        'DEFAULT_ROUTE': inspect.currentframe,
    }
    assert _frame_value(
        call, function, handler_globals, {}) is sys._getframe
    handler_globals['ROUTES'] = {}
    assert _frame_value(
        call, function, handler_globals, {}) is inspect.currentframe
    literal_function = ast.parse(
        "def do_tabs(args):\n"
        "    return ROUTES.get('active', None)\n").body[0]
    literal_call = literal_function.body[0].value
    assert _frame_value(
        literal_call, literal_function, {'ROUTES': {}}, {}) is None


def test_cli_audit_resolver_only_resolves_exact_class_vars(tmp):
    class FrameRoutes:
        active = sys._getframe

    function = ast.parse(
        "def do_tabs(args):\n"
        "    return vars(FrameRoutes)\n").body[0]
    call = function.body[0].value
    value = _frame_value(
        call, function, {'FrameRoutes': FrameRoutes}, {})
    assert isinstance(value, type(FrameRoutes.__dict__))
    assert value['active'] is sys._getframe


def test_cli_audit_refuses_frame_routes_in_real_handler_module(tmp):
    """Real ``do_tabs`` mutations prove audit refusal and dispatch hazards."""
    for index, (module_prelude, body, construct) in enumerate(
            audit_support.DECIDED_FRAME_ROUTE_CASES):
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

    composite_routes = (
        'COMPOSITE_ROUTES = '
        '(sys._getframe, sys._getframe, sys._getframe)')
    for index, expression in enumerate(
            audit_support.COMPOSITE_SUBSCRIPT_FRAME_ROUTE_CASES):
        body = f"_ = {expression}().f_locals['args'].undeclared_probe"
        handler_module = _mutated_cli_tabs(
            f'composite_subscript_cli_{index}', composite_routes, body)
        try:
            violations = _audit_real_tabs_handler(handler_module)
            assert any(f'{expression}()' in item for item in violations), \
                (expression, violations)
            _assert_real_tabs_dispatch_crashes(handler_module)
        finally:
            sys.modules.pop(handler_module.__dict__['__name__'], None)

    for index, (case_name, module_prelude, body, construct) in enumerate(
            audit_support.RESOLVER_ONLY_FRAME_ROUTE_CASES):
        handler_module = _mutated_cli_tabs(
            f'resolver_only_cli_{index}', module_prelude, body)
        try:
            violations = _audit_real_tabs_handler(handler_module)
            assert any(construct in violation for violation in violations), \
                (case_name, violations)
        finally:
            sys.modules.pop(handler_module.__dict__['__name__'], None)

    # Documented gaps, not oversights: these routes stay unresolved.
    for index, (case_name, module_prelude, body) in enumerate(
            audit_support.KNOWN_GAP_FRAME_ROUTE_CASES):
        handler_module = _mutated_cli_tabs(
            f'known_gap_cli_{index}', module_prelude, body)
        try:
            violations = _audit_real_tabs_handler(handler_module)
            assert violations == [], (case_name, violations)
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
    global_declared, global_guaranteed = audit_support.namespace_dests(parser)
    dispatch_only = sorted(set(DISPATCH) - set(subparsers.choices))
    parser_only = sorted(set(subparsers.choices) - set(DISPATCH))
    assert not dispatch_only and not parser_only, (
        f'dispatch without parser: {dispatch_only}; '
        f'parser without dispatch: {parser_only}')
    violations = []
    handler_details = {}
    for name, handler in DISPATCH.items():
        subparser = subparsers.choices[name]
        declared, guaranteed = audit_support.namespace_dests(subparser)
        declared |= global_declared
        guaranteed |= global_guaranteed
        tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
        positional = function.args.posonlyargs + function.args.args
        args_name = positional[0].arg
        reads, handler_violations = _handler_arg_violations(
            function, args_name, declared, guaranteed, handler.__globals__)
        handler_details[name] = {'handler': handler.__qualname__,
                                 'declared': declared, 'reads': reads}
        violations.extend(
            (name, construct, handler.__qualname__)
            for construct in handler_violations)

    stale_reads = []
    for command, attribute, handler_name in \
            audit_support.KNOWN_INDIRECT_ARG_READS:
        detail = handler_details.get(command)
        if detail is None or detail['handler'] != handler_name:
            stale_reads.append(
                f'{command}: known indirect read refers to no handler')
            continue
        if attribute not in detail['reads']:
            stale_reads.append(
                f'{command}: {attribute} absent from handler source')
        if attribute not in detail['declared']:
            stale_reads.append(
                f'{command}: {attribute} not declared by parser')
    assert not stale_reads, '\n'.join(stale_reads)

    details = '\n'.join(
        f'{name}: {construct} read by {handler}'
        for name, construct, handler in violations)
    assert not violations, f'undeclared CLI argument reads:\n{details}'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
