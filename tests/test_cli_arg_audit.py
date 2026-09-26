"""Static proof that CLI handlers read only parser-declared attributes.
DECLARED covers stored action destinations and parser defaults; GUARANTEED adds
required and non-suppressed values. A required mutually exclusive group
guarantees a destination only when every member stores that same non-SUPPRESS
destination. Direct reads require GUARANTEED; guarded reads require DECLARED.
Namespace stores are refused as namespace store escapes.
A frame read is refused wherever it appears in the daedalus_cli package,
including in a helper a handler calls, and a member the resolver has never
heard of is refused like a known one because the member set is read off
types.FrameType rather than written out. Aliases follow prefixes; headers use
outer scope. Other parameters escape."""
import argparse
import ast
import importlib
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
    return any(name in _binding_names(item.target)
               for item in generators)


def _callable_header_nodes(nested):
    yield from getattr(nested, 'decorator_list', ())
    arguments = nested.args
    yield from arguments.defaults
    yield from filter(None, arguments.kw_defaults)
    parameters = (*arguments.posonlyargs, *arguments.args,
                  *arguments.kwonlyargs, arguments.vararg, arguments.kwarg)
    yield from (item.annotation for item in parameters
                if item is not None and item.annotation is not None)
    if (returns := getattr(nested, 'returns', None)) is not None:
        yield returns


def _origin(node, function, handler_globals):
    """What the audit can see a value to be, or ``resolver.UNPROVEN``."""
    return resolver.resolve_origin(
        node, function, handler_globals, resolver.UNPROVEN, _scope_binds)


def _frame_escapes(node, function, handler_globals, label, found):
    """Refuse a frame read whose receiver the audit cannot account for.

    The walk is over the whole callable and does not stop at a nested
    function's own header, so a read in a helper the callable calls is
    reported against the callable that reaches it. One read reports once:
    the walk stops descending as soon as a node is refused, so a line that
    both selects a member and subscripts it is not counted twice.
    """
    read = resolver.frame_read(node)
    if read is not None:
        receiver = resolver.reads_frame_namespace(
            node, _origin(read[1], function, handler_globals))
        if receiver is not None:
            found.append(f'{label}: {ast.unparse(receiver)}')
            return
    for child in ast.iter_child_nodes(node):
        _frame_escapes(child, function, handler_globals, label, found)


def frame_namespace_escapes(function, handler_globals, label):
    found = []
    _frame_escapes(function, function, handler_globals, label, found)
    return found


def _attach_parents(function):
    for node in ast.walk(function):
        for child in ast.iter_child_nodes(node):
            child._parent = node
    return function


def _handler_arg_violations(function, args_name, declared, guaranteed,
                            handler_globals=None):
    _attach_parents(function)
    if handler_globals is None:
        handler_globals = globals()
    reads, read_requirements, violations = {}, {}, []

    def check(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            for header in _callable_header_nodes(node):
                check(header)
            if not _scope_binds(node, args_name):
                body = (node.body if isinstance(node.body, list)
                        else [node.body])
                for statement in body:
                    check(statement)
            return
        if (isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                              ast.GeneratorExp))
                and _comprehension_shadows(node, args_name)):
            check(node.generators[0].iter)
            return
        if resolver.reflective_builtin_call(
                node, function, handler_globals, _scope_binds,
                _comprehension_shadows):
            violations.append(f'namespace escape: {ast.unparse(node)}')
            return
        read = resolver.frame_read(node)
        if read is not None and resolver.reads_frame_namespace(
                node, _origin(read[1], function, handler_globals)) is not None:
            violations.append(
                f'namespace escape: {ast.unparse(read[1])}')
            return
        if isinstance(node, ast.Name) and node.id == args_name:
            permitted = resolver.permitted_namespace_read(
                node, function, handler_globals, _scope_binds,
                _comprehension_shadows)
            if permitted is None:
                kind = ('namespace store escape' if isinstance(
                    getattr(node._parent, 'ctx', None), ast.Store)
                    else 'namespace escape')
                violations.append(f'{kind}: {ast.unparse(node._parent)}')
            else:
                attribute, construct, needs_presence = permitted
                rendered = ast.unparse(construct)
                reads.setdefault(attribute, set()).add(rendered)
                read_requirements[(attribute, rendered)] = needs_presence
        for child in ast.iter_child_nodes(node):
            check(child)
    for child in ast.iter_child_nodes(function):
        check(child)
    for (attribute, construct), needs_presence in sorted(
            read_requirements.items()):
        if attribute not in (guaranteed if needs_presence else declared):
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


CLI_PACKAGE = _util.ROOT / 'daedalus_cli'


def _package_callables(tree):
    """Yield every callable the package defines, outermost first.

    Outermost first because a callable's own walk descends into the ones it
    contains; a module-level function and the method of a module-level class
    are the two places the package defines one.
    """
    callables = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    for node in tree.body:
        if isinstance(node, callables):
            yield node
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield member


def package_frame_escapes(overrides=None):
    """Refuse a frame read anywhere in the CLI package, helper included.

    The domain is every module of the package rather than one handler's body,
    so a read inside a helper a handler calls is refused even when the
    handler names no frame itself. ``overrides`` substitutes a module's
    source, which is how the plant control runs the rule over real code it
    has altered; names the altered source adds stay unproven, and an
    unproven receiver is what the rule refuses.
    """
    escapes = []
    for path in sorted(CLI_PACKAGE.glob('*.py')):
        name = path.stem
        source = (overrides or {}).get(name) or path.read_text(
            encoding='utf-8')
        module_globals = vars(importlib.import_module(f'daedalus_cli.{name}'))
        for function in _package_callables(ast.parse(source)):
            escapes.extend(frame_namespace_escapes(
                function, module_globals, f'{name}.{function.name}'))
    return escapes


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
    for declared, undeclared in cases:
        assert _audit_fake_handler(declared) == [], declared
        assert _audit_fake_handler(undeclared) == [undeclared], undeclared


def test_cli_audit_store_semantics_are_fail_closed(tmp):
    probe = 'args.undeclared_probe'
    store = [f'namespace store escape: {probe}']
    inner = f'def inner():\n    global args\n    {probe}%s\ninner()'
    store_only = (f'{probe} = False', f'{probe}: bool = False',
                  f'{probe} += 1', f'{probe}, other = values',
                  inner % ' = False', inner % ': bool = False')
    for body, expected in tuple((body, store) for body in store_only) + (
            (f'if False:\n    {probe} = False\n{probe}', store + [probe]),
            (f'{probe} = {probe} or False', store + [probe]),
            (probe, [probe]), (f'{probe} = False\n{probe}', store + [probe])):
        assert (actual := _audit_fake_handler(body)) == expected, body
        assert not any(' read ' in message for message in actual), body


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


def test_cli_audit_resolver_only_resolves_exact_module_vars(tmp):
    resolver.assert_exact_class_vars()


def test_cli_audit_refuses_a_frame_read_on_a_proven_receiver(tmp):
    """A frame member read on a value the audit can see is left alone.

    The refusal is about an origin the audit cannot see, so a receiver it has
    resolved to a non-frame is not one. Without this, widening the member set
    would refuse correct code and a later round would narrow the rule back.
    """
    scope = {'ROUTES': {'f_locals': 1}, **globals()}
    assert _audit_fake_handler("ROUTES['f_locals']", scope=scope) == []
    scope = {'ROUTES': {'f_locals': 1}, **globals()}
    assert _audit_fake_handler('ROUTES.f_locals', scope=scope) == []


def test_cli_audit_refuses_frame_namespaces_in_the_real_package(tmp):
    """The real CLI package holds no frame read, in any module or helper."""
    assert package_frame_escapes() == []


def test_cli_audit_refuses_every_frame_namespace_plant(tmp):
    """Each plant, spliced into the real handler module, is refused once.

    The plants are run against the real ``commands_eval.py`` with the real
    package walk, not against a synthetic tree, so a rule that only fires on
    a fixture's shape cannot pass this.
    """
    base = (CLI_PACKAGE / 'commands_eval.py').read_text(encoding='utf-8')
    for name, prelude, anchor, replacement, receiver in \
            audit_support.FRAME_NAMESPACE_PLANTS:
        source = base
        if prelude:
            assert source.count('import json\n') == 1, name
            source = source.replace(
                'import json\n', 'import json\n' + prelude, 1)
        assert source.count(anchor) == 1, name
        escapes = package_frame_escapes(
            {'commands_eval': source.replace(anchor, replacement, 1)})
        assert len(escapes) == 1, (name, escapes)
        assert escapes[0].endswith(f': {receiver}'), (name, escapes)


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
        violations.extend((name, construct, handler.__qualname__)
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
    commands_by_module = {}
    for command, handler in DISPATCH.items():
        commands_by_module.setdefault(
            handler.__module__.rsplit('.', 1)[-1], []).append(command)
    for escape in package_frame_escapes():
        module = escape.split('.', 1)[0]
        violations.append((
            ', '.join(sorted(commands_by_module.get(module, []))) or module,
            escape, f'daedalus_cli/{module}.py'))
    details = '\n'.join(f'{name}: {construct} in {handler}'
                        for name, construct, handler in violations)
    assert not violations, f'CLI argument audit violations:\n{details}'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
