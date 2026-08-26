"""Static proof that CLI handlers read only parser-declared attributes.
DECLARED covers stored action destinations and parser defaults; GUARANTEED adds
required and non-suppressed values. A required mutually exclusive group
guarantees a destination only when every member stores that same non-SUPPRESS
destination. Direct reads require GUARANTEED; guarded reads require DECLARED.
Semantic claims are ``DECIDED`` consists only of the resolver's explicitly
enumerated expression node types | every other ``ast.expr`` node type is
``OUTSIDE`` by definition, so future AST node types enter the fail-closed side
automatically and no third bucket exists | comparisons and tuple-literal keys
stay ``OUTSIDE`` because reproducing their Python semantics would widen the
trusted evaluator | builtin aliases are trusted only with exact builtin
identity at the specific call site | uncertain, rebound, closure-dependent,
or conditional bindings fail closed | captured local aliases require exact
identity at every proven direct invocation. Semantic claims end.
Aliases follow prefixes; headers use outer scope. Other parameters escape;
unresolved frame spellings are refused. UAdd and USub sign integer operands.
Invert complements integer operands. Not converts any resolved literal to
bool. All four recurse; bool values are integer indices and slice bounds.
Current named known-gap control families are non-exact descriptors, partial
callables, traceback frames, other containers and iterators, comprehension
results, instance attributes, attribute getters, runtime-built names,
mapping-proxy reads, call-produced indices, and external frame acquisition.
Each named family and control maps exactly once. The bidirectional check
ensures contract prose and control tables cover each other."""
import argparse
import ast
import inspect
import sys
import textwrap
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _cli_arg_audit_support as audit_support  # noqa: E402
sys.path.insert(0, str(_util.ROOT))
resolver = audit_support.resolver


def _binding_names(target):
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*map(_binding_names, target.elts))
    if isinstance(target, ast.Starred):
        return _binding_names(target.value)
    return set()


def _scope_binds(function, name):
    arguments = function.args
    parameters = (*arguments.posonlyargs, *arguments.args,
                  *arguments.kwonlyargs, arguments.vararg, arguments.kwarg)
    if name in {item.arg for item in parameters if item is not None}:
        return True
    stack = (list(function.body) if isinstance(function.body, list)
             else [function.body])
    binds = False
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Nonlocal, ast.Global)) \
                and name in node.names:
            return False
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            binds = binds or node.name == name
            continue                        # the body is another scope
        elif isinstance(node, ast.Lambda):
            continue                        # so is a lambda's
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                               ast.GeneratorExp)):
            values = ((node.key, node.value) if isinstance(node, ast.DictComp)
                      else (node.elt,))
            stack.extend(values)
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
        elif isinstance(node, ast.Delete):
            binds = binds or any(
                name in _binding_names(target) for target in node.targets)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            binds = binds or any(
                name in _binding_names(item.optional_vars)
                for item in node.items)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            binds = binds or any(
                (alias.asname or alias.name.split('.')[0]) == name
                for alias in node.names)
        elif isinstance(node, ast.ExceptHandler):
            binds = binds or node.name == name
        stack.extend(ast.iter_child_nodes(node))
    return binds


def _comprehension_shadows(comprehension, name, child=None,
                           before_target=False):
    generators = comprehension.generators
    if child in generators:
        end = generators.index(child) + (not before_target)
        generators = generators[:end]
    return any(
        name in _binding_names(generator.target)
        for generator in generators)


def _callable_header_nodes(nested):
    yield from getattr(nested, 'decorator_list', ())
    arguments = nested.args
    yield from arguments.defaults
    yield from (default for default in arguments.kw_defaults
                if default is not None)
    parameters = (*arguments.posonlyargs, *arguments.args,
                  *arguments.kwonlyargs, arguments.vararg, arguments.kwarg)
    yield from (parameter.annotation for parameter in parameters
                if parameter is not None
                and parameter.annotation is not None)
    returns = getattr(nested, 'returns', None)
    if returns is not None:
        yield returns


_FRAME_ROUTE_ATTRS = {'sys': '_getframe', 'inspect': 'currentframe'}
_FRAME_ROUTE_MODULES = {'sys': sys, 'inspect': inspect}
_UNRESOLVED = object()


def _frame_imports(function):
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
    return resolver.resolve_frame_value(
        node, function, handler_globals, imports, _UNRESOLVED,
        _scope_binds, resolver.constant_string)


def _unknown_frame_route(node, function, handler_globals, imports):
    if isinstance(node, ast.Name):
        return (node.id in _FRAME_ROUTE_ATTRS.values()
                and _frame_value(node, function, handler_globals, imports)
                is _UNRESOLVED)
    return (isinstance(node, ast.Attribute)
            and node.attr in _FRAME_ROUTE_ATTRS.values()
            and _frame_value(node.value, function, handler_globals, imports)
            is _UNRESOLVED)


def _frame_route_access(node, function, handler_globals, imports):
    resolved = _frame_value(node, function, handler_globals, imports)
    if resolver.is_frame_route(resolved):
        return True
    if isinstance(node, ast.Call):
        function_value = _frame_value(
            node.func, function, handler_globals, imports)
        return (resolver.is_frame_route(function_value)
                or resolver.is_outside_expression(function_value)
                or (isinstance(node.func, ast.Attribute)
                    and resolver.is_frame_route(_frame_value(
                        node.func.value, function, handler_globals, imports)))
                or _unknown_frame_route(
                    node.func, function, handler_globals, imports))
    if not isinstance(node, (ast.Name, ast.Attribute)):
        return False
    return ((isinstance(node, ast.Attribute)
             and resolver.is_frame_route(_frame_value(
                 node.value, function, handler_globals, imports)))
            or _unknown_frame_route(
                node, function, handler_globals, imports))


def _reflective_call(node, function, handler_globals, imports,
                     inspect_frame_routes=True):
    return (resolver.reflective_builtin_call(
            node, function, handler_globals, _scope_binds,
            _comprehension_shadows)
            or inspect_frame_routes and _frame_route_access(
            node, function, handler_globals, imports))


def _handler_arg_violations(function, args_name, declared, guaranteed,
                            handler_globals=None):
    for node in ast.walk(function):
        for child in ast.iter_child_nodes(node):
            child._parent = node
    if handler_globals is None:
        handler_globals = globals()
    frame_imports = _frame_imports(function)
    reads, read_requirements, violations, assigned = {}, {}, [], set()

    def check(node, inspect_frame_routes=True):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            for header in _callable_header_nodes(node):
                check(header, inspect_frame_routes)
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
            permitted = resolver.permitted_namespace_read(
                node, function, handler_globals, _scope_binds,
                _comprehension_shadows)
            if permitted is None:
                violations.append(
                    f'namespace escape: {ast.unparse(node._parent)}')
            elif permitted[1] is None:
                assigned.add(permitted[0])
            else:
                attribute, construct, needs_presence = permitted
                rendered = ast.unparse(construct)
                reads.setdefault(attribute, set()).add(rendered)
                if attribute not in assigned:
                    read_requirements[(attribute, rendered)] = needs_presence
        for child in ast.iter_child_nodes(node):
            check(child, inspect_frame_routes)
    for child in ast.iter_child_nodes(function):
        check(child)
    for (attribute, construct), needs_presence in sorted(
            read_requirements.items()):
        allowed = guaranteed if needs_presence else declared
        if attribute not in allowed:
            violations.append(construct)
    return reads, violations


def _audit_fake_handler(body, dests=('cmd', 'json'), present=None, scope=None):
    present = dests if present is None else present
    if scope is None:
        scope = {**globals(), 'builtins': sys.modules['builtins']}
    function = ast.parse(
        'def fake(args):\n' + textwrap.indent(body, '    ')).body[0]
    _, violations = _handler_arg_violations(
        function, 'args', set(dests), set(present), scope)
    return violations


def _tabs_namespace_dests(parser):
    subparsers = next(action for action in parser._actions
                      if isinstance(action, argparse._SubParsersAction))
    declared, guaranteed = resolver.namespace_dests(parser)
    sub_declared, sub_guaranteed = resolver.namespace_dests(
        subparsers.choices['tabs'])
    return declared | sub_declared, guaranteed | sub_guaranteed


def _mutated_cli_tabs(package_name, module_prelude, body):
    source = (_util.ROOT / 'daedalus_cli' / 'commands_eval.py').read_text(
        encoding='utf-8')
    source = source.replace(
        'import sys\n', f'import sys\n{module_prelude}\n', 1)
    source = source.replace(
        'def do_tabs(args):\n', f'def do_tabs(args):\n    {body}\n', 1)
    filename = f'<mutated daedalus cli handler {package_name}>'
    module_name = f'{package_name}.commands_eval'
    module = types.ModuleType(module_name)
    module.__dict__.update(
        __file__=filename, __package__='daedalus_cli', __source__=source)
    sys.modules[module_name] = module
    # pylint: disable-next=exec-used  # This mutation executes altered source.
    exec(compile(source, filename, 'exec'), module.__dict__)
    return module


def _audit_real_tabs_handler(handler_module, parser=None):
    if parser is None:
        from daedalus_cli.parser import build_parser
        parser = build_parser()
    declared, guaranteed = _tabs_namespace_dests(parser)
    handler = handler_module.do_tabs
    tree = ast.parse(handler_module.__source__)
    function = next(node for node in tree.body if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == 'do_tabs')
    args_name = (function.args.posonlyargs + function.args.args)[0].arg
    _, violations = _handler_arg_violations(
        function, args_name, declared, guaranteed, handler.__globals__)
    return violations


def _assert_real_tabs_dispatch_crashes(handler_module):
    from daedalus_cli import cli
    original = cli.DISPATCH['tabs'], sys.argv, handler_module.api

    def refuse_api(*_args, **_kwargs):
        raise AssertionError(audit_support.DISPATCH_PROBE_ERROR)
    cli.DISPATCH['tabs'] = handler_module.do_tabs
    sys.argv = ['daedalus', 'tabs']
    handler_module.api = refuse_api
    try:
        with audit_support.isolated_bridge_environment():
            cli.main()
    except AttributeError as error:
        assert str(error) == \
            "'Namespace' object has no attribute 'undeclared_probe'", error
    else:
        assert False, audit_support.DISPATCH_PROBE_ERROR
    finally:
        cli.DISPATCH['tabs'], sys.argv, handler_module.api = original


def _assert_real_frame_case(package_name, prelude, body, label,
                            expected_construct=None, dispatch=True):
    """Audit a temporary handler and optionally prove its runtime read."""
    handler_module = _mutated_cli_tabs(package_name, prelude, body)
    try:
        violations = _audit_real_tabs_handler(handler_module)
        matched = (violations == [] if expected_construct is None else any(
            expected_construct in item for item in violations))
        assert matched, (label, violations)
        if dispatch:
            _assert_real_tabs_dispatch_crashes(handler_module)
    finally:
        sys.modules.pop(handler_module.__dict__['__name__'], None)


def _contract_drift(unsupported=(), undocumented=()):
    return {'unsupported': list(unsupported),
            'undocumented': list(undocumented)}


def test_cli_real_dispatch_helper_neutralizes_bridge(tmp):
    audit_support.assert_real_dispatch_isolated(
        _mutated_cli_tabs, _assert_real_tabs_dispatch_crashes)


def test_cli_audit_excludes_dest_suppress_action(tmp):
    from daedalus_cli.parser import build_parser
    parser = build_parser()
    declared, guaranteed = _tabs_namespace_dests(parser)
    violations = _audit_fake_handler('args.version', declared, guaranteed)
    assert violations == ['args.version'], violations


def test_cli_audit_excludes_default_suppress_action(tmp):
    audit_support.assert_argparse_storage_contract(_audit_fake_handler)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--present', dest='shared', default=False)
    parser.add_argument(
        '--suppressed', dest='shared', default=argparse.SUPPRESS)
    assert resolver.namespace_dests(parser)[1] == {'shared'}


def test_cli_audit_models_mutex_group_storage(tmp):
    audit_support.assert_argparse_mutex_storage_contract(_audit_fake_handler)


def test_cli_audit_accepts_guarded_suppress_in_real_dispatch(tmp):
    from daedalus_cli import cli
    for index, (shape, body, argv, expected) in enumerate(
            audit_support.REAL_STORAGE_DISPATCH_CASES):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest='cmd', required=True)
        tabs = sub.add_parser('tabs')
        tabs.add_argument('--json', action='store_true')
        audit_support.add_storage_probe(tabs, shape, 'undeclared_probe')
        handler_module = _mutated_cli_tabs(
            f'storage_dispatch_cli_{index}', 'PROBE_READS = []', body)
        original = cli.DISPATCH['tabs'], cli.build_parser, sys.argv

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
            cli.DISPATCH['tabs'], cli.build_parser, sys.argv = original
            sys.modules.pop(handler_module.__dict__['__name__'], None)


def test_cli_audit_includes_parser_set_defaults(tmp):
    parser = argparse.ArgumentParser(add_help=False)
    parser.set_defaults(from_defaults=False)
    assert vars(parser.parse_args([])) == {'from_defaults': False}
    declared, guaranteed = resolver.namespace_dests(parser)
    violations = _audit_fake_handler(
        'args.from_defaults', declared, guaranteed)
    assert violations == [], violations


def test_cli_audit_checks_permitted_reads_by_attribute(tmp):
    cases = audit_support.PERMITTED_NAMESPACE_READ_CASES
    for declared, undeclared, _ in cases:
        assert _audit_fake_handler(declared) == [], declared
        assert _audit_fake_handler(undeclared) == [undeclared], undeclared
    for body in ('args.undeclared_probe = False\nargs.undeclared_probe',
                 'args.undeclared_probe = False'):
        assert _audit_fake_handler(body) == [], body


def test_cli_audit_reports_namespace_escapes(tmp):
    for body, construct in audit_support.NAMESPACE_ESCAPE_CASES:
        expected = [f'namespace escape: {construct}']
        assert _audit_fake_handler(body) == expected, (body, construct)


def test_cli_audit_requires_builtin_identity(tmp):
    for body, scope, expected in audit_support.BUILTIN_IDENTITY_GLOBAL_CASES:
        assert _audit_fake_handler(body, scope=scope) == list(expected), body


def test_cli_audit_rechecks_builtin_identity_at_call_site(tmp):
    mismatches = {}
    for case in audit_support.BUILTIN_IDENTITY_CALL_SITE_CASES:
        name, body, scope, expected = case
        actual = _audit_fake_handler(body, scope=scope)
        if actual != list(expected):
            mismatches[name] = {'expected': list(expected), 'actual': actual}
    assert mismatches == {}, mismatches


def test_cli_audit_rechecks_builtin_identity_in_real_handler(tmp):
    body = (
        "setattr(sys.modules[__name__], 'G',\n"
        "        lambda namespace, *_: namespace.undeclared_probe)\n"
        "    G(args, 'json', False)")
    handler_module = _mutated_cli_tabs(
        'builtin_call_site_cli',
        'from builtins import getattr as G', body)
    try:
        assert _audit_real_tabs_handler(handler_module) == [
            "namespace escape: G(args, 'json', False)"]
        _assert_real_tabs_dispatch_crashes(handler_module)
    finally:
        sys.modules.pop(handler_module.__dict__['__name__'], None)


def test_cli_audit_resolves_exact_builtin_aliases(tmp):
    for case in audit_support.BUILTIN_IDENTITY_LOCAL_CASES:
        name, body, scope, expected = case
        assert _audit_fake_handler(
            body, scope=scope) == list(expected), name


def test_cli_audit_respects_inner_scope_bindings(tmp):
    audit_support.assert_inner_scope_bindings(_audit_fake_handler)


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
    audit_support.assert_dict_get_default(_frame_value)


def test_cli_audit_resolver_logical_not_returns_bool(tmp):
    audit_support.assert_every_unary_operator()


def test_cli_audit_resolver_decides_every_unary_operator(tmp):
    audit_support.assert_every_unary_operator()


def test_cli_audit_resolver_partitions_every_expression_type(tmp):
    resolver.assert_total_expression_partition()


def test_cli_audit_resolver_only_resolves_exact_class_vars(tmp):
    resolver.assert_exact_class_vars(_frame_value)


def test_cli_audit_refuses_frame_routes_in_real_handler_module(tmp):
    for index, (module_prelude, body, construct) in enumerate(
            audit_support.DECIDED_FRAME_ROUTE_CASES):
        _assert_real_frame_case(
            f'mutated_cli_{index}', module_prelude, body,
            (module_prelude, body), construct)
    composite_routes = 'COMPOSITE_ROUTES = (sys._getframe,) * 3'
    for index, expression in enumerate(
            audit_support.COMPOSITE_SUBSCRIPT_FRAME_ROUTE_CASES):
        body = f"_ = {expression}().f_locals['args'].undeclared_probe"
        _assert_real_frame_case(
            f'composite_subscript_cli_{index}', composite_routes, body,
            expression, f'{expression}()')
    for index, case in enumerate(
            audit_support.OUTSIDE_EXPRESSION_FRAME_ROUTE_CASES):
        case_name, module_prelude, body, construct = case
        _assert_real_frame_case(
            f'outside_expression_cli_{index}', module_prelude, body,
            case_name, construct)
    ordinary = {'ROUTES': {True: [((len,),)]}}
    assert _audit_fake_handler(
        '_ = ROUTES[0 < 1][0][0][0]()', scope=ordinary) == []
    for index, (case_name, module_prelude, body, construct) in enumerate(
            audit_support.RESOLVER_ONLY_FRAME_ROUTE_CASES):
        _assert_real_frame_case(
            f'resolver_only_cli_{index}', module_prelude, body,
            case_name, construct, dispatch=False)
    for index, (case_name, module_prelude, body) in enumerate(
            audit_support.KNOWN_GAP_FRAME_ROUTE_CASES):
        _assert_real_frame_case(
            f'known_gap_cli_{index}', module_prelude, body, case_name)


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


def test_cli_audit_module_has_no_dead_imports(tmp):
    tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))
    imported = {
        alias.asname or (alias.name.split('.')[0]
                         if isinstance(statement, ast.Import)
                         else alias.name)
        for statement in tree.body
        if isinstance(statement, (ast.Import, ast.ImportFrom))
        for alias in statement.names}
    loaded = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    assert imported <= loaded, sorted(imported - loaded)


def test_cli_audit_docstrings_match_control_tables(tmp):
    documents = {
        'test_cli_arg_audit': __doc__,
        '_cli_arg_audit_support': audit_support.__doc__,
        '_cli_arg_audit_resolver': resolver.__doc__}
    known_families = audit_support.KNOWN_GAP_FAMILIES
    known_cases = audit_support.KNOWN_GAP_FRAME_ROUTE_CASES
    resolver.assert_docstrings_match(
        documents, audit_support.DOCSTRING_RULE_PHRASES,
        audit_support.SEMANTIC_CONTRACT_CLAIMS, known_families, known_cases)
    unsupported_documents = dict(documents)
    unsupported_documents['_cli_arg_audit_support'] = \
        audit_support.__doc__.replace(
            ', and external frame acquisition.',
            ', comparison routes, and external frame acquisition.')
    contradictory_documents = dict(documents)
    contradictory_documents['_cli_arg_audit_resolver'] = (
        resolver.__doc__
        + ' Semantic claims are Comparisons and tuple-literal keys are '
        'DECIDED. Semantic claims end.')
    missing_documents = dict(documents)
    missing_documents.pop('_cli_arg_audit_resolver')
    undocumented_families = audit_support.KNOWN_GAP_FAMILIES + (
        ('comparison routes', ('comparison route control',)),)
    undocumented_cases = audit_support.KNOWN_GAP_FRAME_ROUTE_CASES + (
        ('comparison route control', '', ''),)
    drift_cases = (
        ('missing-module', missing_documents, known_families, known_cases,
         {'missing': ['_cli_arg_audit_resolver'], 'unknown': []}),
        ('contradictory-semantic-claim', contradictory_documents,
         known_families, known_cases,
         {'_cli_arg_audit_resolver': _contract_drift(unsupported=(
             'Comparisons and tuple-literal keys are DECIDED',))}),
        ('unsupported-prose', unsupported_documents, known_families,
         known_cases,
         {'_cli_arg_audit_support': _contract_drift(
             unsupported=('comparison routes',))}),
        ('undocumented-control', documents, undocumented_families,
         undocumented_cases, {
             module: _contract_drift(undocumented=('comparison routes',))
             for module in ('test_cli_arg_audit', '_cli_arg_audit_support',
                            '_cli_arg_audit_resolver')}),)
    for name, drift_documents, families, cases, expected in drift_cases:
        try:
            resolver.assert_docstrings_match(
                drift_documents, audit_support.DOCSTRING_RULE_PHRASES,
                audit_support.SEMANTIC_CONTRACT_CLAIMS,
                families, cases)
        except AssertionError as error:
            assert error.args == (expected,), {
                'case': name, 'expected': expected, 'actual': error.args}
        else:
            assert False, f'{name}: drift was accepted'


def test_cli_handlers_read_only_declared_args(tmp):
    from daedalus_cli.cli import DISPATCH
    from daedalus_cli.parser import build_parser
    parser = build_parser()
    subparsers = next(action for action in parser._actions
                      if isinstance(action, argparse._SubParsersAction))
    global_declared, global_guaranteed = resolver.namespace_dests(parser)
    assert set(DISPATCH) == set(subparsers.choices), (
        f'dispatch without parser: '
        f'{sorted(set(DISPATCH) - set(subparsers.choices))}; parser without '
        f'dispatch: {sorted(set(subparsers.choices) - set(DISPATCH))}')
    violations, handler_details = [], {}
    for name, handler in DISPATCH.items():
        subparser = subparsers.choices[name]
        declared, guaranteed = resolver.namespace_dests(subparser)
        declared |= global_declared
        guaranteed |= global_guaranteed
        tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
        function = next(node for node in ast.walk(tree) if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)))
        args_name = (function.args.posonlyargs + function.args.args)[0].arg
        reads, handler_violations = _handler_arg_violations(
            function, args_name, declared, guaranteed, handler.__globals__)
        handler_details[name] = {'handler': handler.__qualname__,
                                 'declared': declared, 'reads': reads}
        violations.extend(
            (name, construct, handler.__qualname__)
            for construct in handler_violations)
    for command, attribute, handler_name in \
            audit_support.KNOWN_INDIRECT_ARG_READS:
        detail = handler_details.get(command)
        assert detail is not None and detail['handler'] == handler_name, (
            f'{command}: known indirect read refers to no handler')
        assert attribute in detail['reads'], (
            f'{command}: {attribute} absent from handler source')
        assert attribute in detail['declared'], (
            f'{command}: {attribute} not declared by parser')
    details = '\n'.join(
        f'{name}: {construct} read by {handler}'
        for name, construct, handler in violations)
    assert not violations, f'undeclared CLI argument reads:\n{details}'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
