#!/usr/bin/env python3
"""The CLI namespace-escape audit's machinery and its focused cases.

The repository-wide check is ``test_cli_handlers_read_only_declared_args``
below; the rest is the visitor it drives and the small synthetic handlers
each rule is pinned with, so a change that weakens or over-tightens the
audit fails one of these cheap tests instead of hiding inside the
39-handler scan.

The audit is a whitelist, not a proof of the inverse. It sees reads made
through the namespace parameter's own name and refuses the routes a static
check cannot decide — ``locals()``, ``globals()``, ``eval``, ``exec`` and
a no-argument ``vars()``; a handler that reaches its namespace by any
other dynamic route is outside what a static check can see.
"""
import argparse
import ast
import inspect
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

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
    """Return True when a nested callable's own scope binds ``name``.

    Parameters count, and so does anything Python gives function scope:
    assignments, loop and ``with`` targets, ``except`` names, imports and
    the names of nested ``def`` and ``class`` statements. Bindings made in
    a deeper nested scope or as a comprehension target do not, and a
    ``nonlocal`` declaration means the name still reaches the handler.
    """
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


def _reflective_call(node):
    """Return True for a call that reaches the local frame by name.

    ``locals()``, ``globals()``, ``eval``, ``exec`` and a no-argument
    ``vars()`` read or execute the frame's own names, which no static rule
    can resolve; inside a handler they are namespace escapes outright.
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return False
    if node.func.id in ('locals', 'globals', 'eval', 'exec'):
        return True
    return node.func.id == 'vars' and not node.args


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
    """Resolve a use of the namespace parameter, or None for an escape.

    Only four direct reads are permitted: ``args.attr``,
    ``getattr(args, 'attr'[, default])``, ``vars(args)['attr']`` and
    ``args.__dict__['attr']``. Every other mention of the parameter is
    an escape: aliasing, containers, calls, returns, stores.
    """
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
    if not (parent.func.id == 'getattr' and len(parent.args) in (2, 3)
            and not any(isinstance(argument, ast.Starred)
                        for argument in parent.args)):
        return None
    attribute = _constant_string(parent.args[1])
    if attribute is None:
        return None
    return attribute, parent


def _handler_arg_violations(function, args_name, allowed):
    """Return (reads, violations) for one handler's namespace use.

    Permitted reads are checked against ``allowed``; every other mention
    of the parameter is a namespace escape, and reflective calls are
    refused by name. A nested callable whose own scope binds the name has
    only its body skipped: its decorators, defaults and annotations are
    evaluated in the enclosing scope — the handler's — and stay audited.
    A comprehension whose target binds the name shadows it for the whole
    comprehension except the outermost iterable, which is evaluated in the
    enclosing scope too.
    """
    for node in ast.walk(function):
        for child in ast.iter_child_nodes(node):
            child._parent = node
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
        if _reflective_call(node):
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


def test_cli_audit_reports_suppressed_destination(tmp):
    """A handler read of argparse's absent ``version`` is reported."""
    del tmp
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
    """Each permitted read passes when declared and is reported when not."""
    del tmp
    cases = [
        ('args.json', 'args.undeclared_probe'),
        ("getattr(args, 'json')", "getattr(args, 'undeclared_probe')"),
        ("getattr(args, 'json', False)",
         "getattr(args, 'undeclared_probe', None)"),
        ("vars(args)['json']", "vars(args)['undeclared_probe']"),
        ("args.__dict__['json']", "args.__dict__['undeclared_probe']"),
    ]
    for declared, undeclared in cases:
        assert _audit_fake_handler(declared) == [], declared
        assert _audit_fake_handler(undeclared) == [undeclared], undeclared


def test_cli_audit_reports_namespace_escapes(tmp):
    """Every other mention of the namespace parameter is an escape."""
    del tmp
    cases = [
        ('other = args', 'other = args'),
        ('other = args\nthird = other\nthird.x', 'other = args'),
        ('other, = (args,)', '(args,)'),
        ("getattr(*(args, 'x'))", "(args, 'x')"),
        ('helper(args)', 'helper(args)'),
        ('helper([args])', '[args]'),
        ('helper(*[args])', '[args]'),
        ('getattr(args, some_variable)', 'getattr(args, some_variable)'),
        ('helper(vars(args))', 'vars(args)'),
        ('args.__dict__', 'args.__dict__'),
    ]
    for body, construct in cases:
        assert _audit_fake_handler(body) == [
            f'namespace escape: {construct}'], (body, construct)


def test_cli_audit_respects_inner_scope_bindings(tmp):
    """An inner ``args`` parameter shadows; a closure over it is audited."""
    del tmp
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
    """A nested callable's defaults are evaluated in the handler's scope."""
    del tmp
    cases = [
        'def inner(args=args):\n    return args.undeclared_probe\ninner()',
        'f = lambda args=args: args.undeclared_probe\nf()',
    ]
    for body in cases:
        assert _audit_fake_handler(body) == [
            'namespace escape: args=args'], body


def test_cli_audit_sees_shadowing_decorators_and_annotations(tmp):
    """A shadowing def's decorators and annotations are audited too."""
    del tmp
    decorated = '@consume(args)\ndef inner(args):\n    pass'
    assert _audit_fake_handler(decorated) == [
        'namespace escape: consume(args)']
    annotated = 'def inner(args: args.undeclared_probe):\n    pass'
    assert _audit_fake_handler(annotated) == ['args.undeclared_probe']


def test_cli_audit_refuses_reflective_namespace_access(tmp):
    """locals(), globals(), eval, exec and a bare vars() are escapes."""
    del tmp
    cases = [
        ("_ = locals()['args'].undeclared_probe", 'locals()'),
        ("_ = eval('args.undeclared_probe')", "eval('args.undeclared_probe')"),
        ('_ = vars()', 'vars()'),
        ('_ = globals()', 'globals()'),
        ("exec('args.undeclared_probe')", "exec('args.undeclared_probe')"),
    ]
    for body, construct in cases:
        assert _audit_fake_handler(body) == [
            f'namespace escape: {construct}'], body


def test_cli_audit_respects_comprehension_shadowing(tmp):
    """A comprehension target that binds the name shadows it there."""
    del tmp
    assert _audit_fake_handler('_ = [args.json for args in values]') == []
    assert _audit_fake_handler('[args.json for value in values]') == []
    assert _audit_fake_handler("vars(args)['json']") == []


def test_cli_audit_sees_a_shadowing_comprehensions_iterable(tmp):
    """The outermost iterable runs in the handler and stays audited."""
    del tmp
    assert _audit_fake_handler(
        '_ = [args.value for args in args.undeclared_probe]') == [
        'args.undeclared_probe']


def test_cli_audit_respects_nested_local_bindings(tmp):
    """A nested function that assigns the name binds its own local."""
    del tmp
    shadowed = (
        'def inner():\n'
        "    args = type('T', (), {'json': True})()\n"
        '    return args.json')
    assert _audit_fake_handler(shadowed) == []


def test_cli_handlers_read_only_declared_args(tmp):
    """Dispatched handlers read only their parser's declared arguments.

    The audit sees reads made through the namespace parameter's own name
    and refuses the reflective escapes it cannot decide — ``locals()``,
    ``globals()``, ``eval``, ``exec`` and a no-argument ``vars()``; a
    handler that reaches its namespace by any other dynamic route is
    outside what a static check can see.
    """
    del tmp
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
            function, args_name, allowed)
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
    """Dispatch and the parser expose exactly the same subcommand names."""
    del tmp
    from daedalus_cli.cli import DISPATCH
    from daedalus_cli.parser import build_parser

    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction))
    assert set(DISPATCH) == set(subparsers.choices)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
