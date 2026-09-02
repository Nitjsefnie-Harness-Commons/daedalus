"""The raise site every MCP tool refusal must witness, read from the source.

Nothing in TOOL_REFUSALS says which guard a case exists for, so deleting one
used to assert nothing. This reads every raise the modules the composition
can import spell and makes each site an obligation of the tools that reach
it, keyed (dotted module, function, condition).

Its limits, so nobody reads more into it than it does. It witnesses raise
sites that exist in the source; a guard deleted together with its declaration
is invisible to it, and for a guard nothing else pins, that deletion is
invisible everywhere. Reach is lexical — closure cells, defaults,
`__wrapped__`, module globals — so a site reached any other way is refused by
the pin run, not missed. The `or` refusal reads the `if` test a raise sits
DIRECTLY under: a raise reached through `try`, `while` or `else` bodies, or
a test spelled `not (a and b)`, is one site like any other. A refusal
written as `return {'error': ...}` — the
convention at daedalus_mcp/tools_css.py `unblock_requests` — never raises and
is off this floor.
"""
import ast
import dis
import importlib.util
import inspect
from pathlib import Path
import types


GUARD_SHAPES = '''
try:
    raise ValueError('at module level')
except ValueError:
    pass


def guard(value):
    if value < 1:
        raise ValueError('guarded')
    if value:
        pass
    else:
        raise RuntimeError('in an else')
    assert value < 1000
    raise RuntimeError('guarded by nothing')


def handler(text):
    try:
        return int(text)
    except ValueError:
        raise RuntimeError('re-raised from a handler') from None
'''

# Every raise shape the scan can meet, and the one key each resolves to. The
# `assert` states an invariant rather than refusing an argument and spells no
# site — the set equality is what holds that open.
GUARD_SHAPE_SITES = {
    ('guard_shapes', '', "raise ValueError('at module level')"),
    ('guard_shapes', 'guard', 'value < 1'),
    ('guard_shapes', 'guard', "raise RuntimeError('in an else')"),
    ('guard_shapes', 'guard', "raise RuntimeError('guarded by nothing')"),
    ('guard_shapes', 'handler',
     "raise RuntimeError('re-raised from a handler') from None"),
}

SELECT_SHAPES = '''
def inner(value):
    if value is None:
        raise ValueError('none refused')


def middle(value):
    inner(value); raise RuntimeError('shares the call line')
'''


def _contains_or(test):
    """True when an `or` appears anywhere in the test expression."""
    return any(isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
               for node in ast.walk(test))


def _enclosing_function(node, parents):
    while node is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
        node = parents.get(node)
    return ''


def _unguarded_text(node):
    """Names a raise no `if` body holds. A bare re-raise unparses to `raise`
    wherever it stands, so it carries its line rather than colliding."""
    if node.exc is None:
        return f'bare raise at line {node.lineno}'
    return ast.unparse(node)


def _dotted(path, root):
    """The module's name relative to the repository root: two modules that
    share a stem are different modules, each keyed on its own."""
    return '.'.join(
        path.resolve().relative_to(Path(root).resolve()).with_suffix('')
        .parts)


def _module_guard_sites(path, root):
    """Every raise site one module spells, keyed by (file, line).

    Each value is ((module, function, condition), or_guard). Two shapes a
    site key cannot tell apart are refused here instead of collapsed: two
    raises on one physical line — a traceback names the line, not the
    statement — and two sites in one function spelling one condition, whose
    remedy is distinct messages.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    raises = [node for node in ast.walk(tree) if isinstance(node, ast.Raise)]
    by_line = {}
    for node in raises:
        if node.lineno in by_line:
            raise AssertionError(
                f'{_dotted(path, root)}:{node.lineno}: two raises share this '
                'line; one raise per line, because a traceback names the '
                'line and one witness cannot answer for both')
        by_line[node.lineno] = node
    module = _dotted(path, root)
    sites = {}
    spelled = set()
    for node in raises:
        guard = parents.get(node)
        if isinstance(guard, ast.If) and node in guard.body:
            condition = ast.unparse(guard.test)
            or_guard = _contains_or(guard.test)
        else:
            condition = _unguarded_text(node)
            or_guard = False
        key = (module, _enclosing_function(node, parents), condition)
        if key in spelled:
            raise AssertionError(
                f'{module}.{key[1]} spells {condition!r} at two raise '
                'sites; one witness cannot answer for both — give the sites '
                'distinct messages')
        spelled.add(key)
        sites[(str(path.resolve()), node.lineno)] = (key, or_guard)
    return sites


def tool_guards(paths, root):
    """Every raise site the given modules spell, keyed by (file, line).

    Reading the source is what makes a deleted refusal case visible. An
    `assert` states an invariant rather than refusing an argument, and is
    left out.
    """
    sites = {}
    for path in paths:
        sites.update(_module_guard_sites(Path(path), root))
    return sites


def composition_scan_set(composition, root):
    """The repo-local modules the composition's SOURCE FILE can import.

    A static walk, not a runtime snapshot: every `import` and `from` at any
    depth — function bodies, `try` blocks, dead branches — resolves to files
    under the repository root or is provably elsewhere (stdlib, site
    packages), and the walk iterates to a fixed point. A target the walk
    cannot determine statically is unprovable and fails loudly, naming the
    module and the import site: a walk that silently omitted what it cannot
    resolve would be the next blind spot, not a closure.
    """
    root = Path(root).resolve()
    composition = Path(composition).resolve()
    seen = {composition}
    pending = [composition]
    while pending:
        for target in _import_targets(pending.pop(), root):
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return sorted(path for path in seen
                  if 'tests' not in path.relative_to(root).parts)


def _dynamic_callees(tree):
    """The names one module's imports bind to the import-by-name operation.

    `import importlib [as x]` and `import builtins [as x]` bind their module
    aliases, `from importlib import import_module [as y]` and `from importlib
    import __import__ [as z]` bind their function names, and the builtin
    `__import__` is bound before anything runs. Classification then resolves
    a call's callee through this map instead of matching spellings.
    """
    bound = {'__import__': 'by name'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ('importlib', 'builtins'):
                    bound[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module == 'importlib' and not node.level:
                for alias in node.names:
                    if alias.name in ('import_module', '__import__'):
                        bound[alias.asname or alias.name] = 'by name'
    return bound


def _is_dynamic_import(func, bound):
    """A call to import_module or __import__, per the module's own bindings.

    Loading a module by PATH — `spec_from_file_location`, `SourceFileLoader`
    — is a different operation and stays outside this recognition.
    """
    if isinstance(func, ast.Name):
        return bound.get(func.id) == 'by name'
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        source = bound.get(func.value.id)
        if source == 'importlib':
            return func.attr in ('import_module', '__import__')
        if source == 'builtins':
            return func.attr == '__import__'
    return False


def _import_targets(path, root):
    """The repo-local files one module's source can import.

    Empty when nothing the module names lives under root — stdlib and
    site-package targets are provably not this repository's. A dynamic
    import whose argument is not a constant string is unprovable and raises,
    naming the module and the import site; a constant resolves like an
    import.
    """
    targets = set()
    tree = ast.parse(path.read_text(encoding='utf-8'))
    bound = _dynamic_callees(tree)
    package = path.resolve().relative_to(Path(root).resolve()).parent.parts
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets |= _resolve_name(alias.name, (), root)
        elif isinstance(node, ast.ImportFrom):
            base = package[:max(len(package) + 1 - node.level, 0)] \
                if node.level else ()
            if node.module:
                targets |= _resolve_name(node.module, base, root)
            for alias in node.names:
                if alias.name != '*':
                    name = f'{node.module}.{alias.name}' if node.module \
                        else alias.name
                    targets |= _resolve_name(name, base, root)
        elif isinstance(node, ast.Call) and _is_dynamic_import(
                node.func, bound):
            argument = node.args[0] if node.args else None
            if isinstance(argument, ast.Constant) \
                    and isinstance(argument.value, str) \
                    and not argument.value.startswith('.'):
                targets |= _resolve_name(argument.value, (), root)
            else:
                raise AssertionError(
                    f'{_dotted(path, root)}:{node.lineno}: '
                    'import_module/__import__ is called with a name this '
                    'scan cannot read statically; import it normally or '
                    'pass an absolute constant, because an import closure '
                    'that silently skips a module it cannot resolve is not '
                    'closed')
    return targets


def _resolve_name(name, base, root):
    """One import target's repo-local files: the package `__init__` files
    importing it executes, then the module file itself. Relative imports
    arrive resolved against the importing module's package in `base`. Empty
    when no prefix of the dotted name matches the repository, which is what
    makes stdlib and site packages provably irrelevant."""
    parts = (*base, *name.split('.'))
    found = set()
    probe = Path(root).resolve()
    for index, part in enumerate(parts):
        package = probe / part
        if package.is_dir():
            probe = package
            init = package / '__init__.py'
            if init.is_file():
                found.add(init.resolve())
            continue
        module = probe / f'{part}.py'
        if index == len(parts) - 1 and module.is_file():
            found.add(module.resolve())
        break
    return found


def nested_code_objects(code):
    """A code object and every code object nested in it."""
    pending = [code]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(
            value for value in current.co_consts
            if isinstance(value, types.CodeType))


def _repo_function(function, root):
    """True for a plain function defined in this repository, tests excluded."""
    path = Path(function.__code__.co_filename).resolve()
    try:
        relative = path.relative_to(Path(root).resolve())
    except ValueError:
        return False
    return 'tests' not in relative.parts


def tool_code_objects(tool, root):
    """Every code object a tool's reachable code can execute from this
    repository, tests excluded.

    The walk follows the tool's own and nested code, functions in closure
    cells, function-valued defaults, `__wrapped__` links, and module-level
    functions found under the names its code loads. Functions held inside
    default containers and callables found any other way are outside the
    property; a site reached only through one is refused by the pin run,
    never missed.
    """
    root = Path(root).resolve()
    pending = [tool]
    seen_functions = set()
    seen_codes = set()
    while pending:
        function = pending.pop()
        if id(function) in seen_functions:
            continue
        seen_functions.add(id(function))
        codes = list(nested_code_objects(function.__code__))
        for code in codes:
            if id(code) not in seen_codes:
                seen_codes.add(id(code))
                yield code
        if function.__closure__:
            for cell in function.__closure__:
                try:
                    value = cell.cell_contents
                except ValueError:
                    continue
                if inspect.isfunction(value):
                    pending.append(value)
        for value in function.__defaults__ or ():
            if inspect.isfunction(value):
                pending.append(value)
        for value in (function.__kwdefaults__ or {}).values():
            if inspect.isfunction(value):
                pending.append(value)
        wrapped = getattr(function, '__wrapped__', None)
        if inspect.isfunction(wrapped):
            pending.append(wrapped)
        for code in codes:
            pending.extend(
                found for found in (function.__globals__.get(name)
                                    for name in code.co_names)
                if inspect.isfunction(found) and _repo_function(found, root))


def reachable_guards(sites, codes):
    """The raise sites a tool's own code objects can raise from, as their
    keys. A reached site refusing on an `or` test is refused by the floor:
    split it into one raise per condition, because one witness cannot answer
    for two conditions on one line. The `or` test read is the one a raise
    sits DIRECTLY under — a raise reached through `try`, `while` or `else`
    bodies, or a test spelled `not (a and b)`, is one site like any other."""
    reached = {}
    for code in codes:
        filename = str(Path(code.co_filename).resolve())
        for instruction in dis.get_instructions(code):
            positions = instruction.positions
            if instruction.opname != 'RAISE_VARARGS' or positions is None:
                continue
            site = sites.get((filename, positions.lineno))
            if site is None:
                continue
            key, or_guard = site
            if or_guard:
                raise AssertionError(
                    f'{key[0]}.{key[1]} refuses on an `or` test ({key[2]}); '
                    'split it into one raise per condition, because one '
                    'witness cannot answer for two conditions on one line')
            reached[(filename, positions.lineno)] = key
    return reached


def guard_keys(sites):
    """The (module, function, condition) keys a site mapping spells."""
    return [key for key, _or in sites.values()]


def witnessed_guard(sites, raised):
    """The innermost scanned raise site on the traceback, as its (module,
    function, condition) key.

    Frames between the catch and the raise appear on the traceback at their
    call lines, so the walk keeps going: the first matching frame can be a
    call that merely shares its line with a raise site.
    """
    fired = None
    trace = raised.__traceback__
    while trace is not None:
        site = sites.get(
            (str(Path(trace.tb_frame.f_code.co_filename).resolve()),
             trace.tb_lineno))
        if site is not None:
            fired = site
        trace = trace.tb_next
    return None if fired is None else fired[0]


def unwitnessed_guard_gaps(tool, reached, witnessed):
    """Guards a tool reaches that neither its cases nor the allowlist cover.

    A guard witnessed through a sibling does not discharge this tool: deleting
    a tool's entries would otherwise hide behind whichever sibling shares the
    helper. A stale entry is refused.
    """
    spelled = list(reached)
    witnessed = set(witnessed)
    allowed = set(UNWITNESSED_GUARDS.get(tool, ()))
    return (
        [f'UNWITNESSED_GUARDS names {key!r}, which it does not reach'
         for key in sorted(allowed - set(spelled))]
        + [f'UNWITNESSED_GUARDS names {key!r}, which its own refusal case '
           'already witnesses' for key in sorted(allowed & witnessed)]
        + [f'no refusal case of its own witnesses {key!r}'
           for key in sorted(set(spelled) - witnessed - allowed)])


def guards_off_the_tool_surface(sites, reached):
    """Guard keys no registered tool's own code reaches."""
    return set(guard_keys(sites)) - {
        key for keys in reached.values() for key in keys.values()}


def missing_citations(table, root):
    """The cited tests a table names that are absent from the tree.

    A citation is data: its suite is parsed with `ast` and the named test
    function must be defined there. A suite path that names no file is
    absent the same way. Rows citing None are stated gaps, not citations,
    and are never reported.
    """
    missing = []
    for entry in table:
        cited = entry[3]
        if cited is None:
            continue
        suite, _, name = cited.partition('::')
        path = Path(root) / suite
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except OSError:
            missing.append(cited)
            continue
        defined = {node.name for node in ast.walk(tree)
                   if isinstance(node, (ast.FunctionDef,
                                        ast.AsyncFunctionDef))}
        if name not in defined:
            missing.append(cited)
    return missing


def stranded_guards(reached, witnessed):
    """Every tool's unwitnessed or stale guard pins; `witnessed` is (tool,
    guard) pairs, and the filter back to the owner makes each tool's own
    entries load-bearing."""
    stranded = {}
    for tool, keys in reached.items():
        gaps = unwitnessed_guard_gaps(
            tool, keys, {key for owner, key in witnessed if owner == tool})
        if gaps:
            stranded[tool] = gaps
    return stranded


def guard_shape_probe(directory, source=GUARD_SHAPES, name='guard_shapes'):
    """A source string written, scanned and imported, for the floor's tests."""
    path = Path(directory) / f'{name}.py'
    path.write_text(source, encoding='utf-8')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tool_guards([path], directory), module


UNWITNESSED_GUARDS = {
    # Per REGISTERED TOOL, keyed by the same (module, function, condition)
    # triple the scan spells, so an entry cannot exempt a same-named function
    # in another module. A stale entry - unreachable, or already witnessed by
    # the tool's own case - is refused. navigate, reload, title and url each
    # hand _send_eval a constant id and code, so neither guard can fire from
    # them; ping's and segment_status's branches read a poll body / HTTP
    # status the refusal driver cannot set, and are pinned by
    # test_ping_raises_the_bridge_error and the two named
    # test_segment_status_refuses_* tests.
    'navigate': {('daedalus_mcp.tools_eval', '_send_eval', 'not cmd_id'),
                 ('daedalus_mcp.tools_eval', '_send_eval', 'not code')},
    'reload': {('daedalus_mcp.tools_eval', '_send_eval', 'not cmd_id'),
               ('daedalus_mcp.tools_eval', '_send_eval', 'not code')},
    'title': {('daedalus_mcp.tools_eval', '_send_eval', 'not cmd_id'),
              ('daedalus_mcp.tools_eval', '_send_eval', 'not code')},
    'url': {('daedalus_mcp.tools_eval', '_send_eval', 'not cmd_id'),
            ('daedalus_mcp.tools_eval', '_send_eval', 'not code')},
    'ping': {
        ('daedalus_mcp.tools_eval', 'ping', "res.get('error')"),
    },
    'segment_status': {
        ('daedalus_mcp.tools_media', 'segment_status',
         'found.status_code == 404'),
        ('daedalus_mcp.tools_media', 'segment_status',
         'found.status_code == 409'),
    },
}

GUARDS_OFF_THE_TOOL_SURFACE = {
    # Every raise the scanned modules spell that no registered tool's own
    # code reaches, compared both ways: an undeclared raise and a stale
    # declaration both fail. The fourth field names the test that pins the
    # guard - verified by planting the guard's failure and watching that
    # test go red - or None for a stated gap, all of them issue 545.
    ('daedalus_mcp.server', 'start_in_thread', "_start_state['started']",
     'tests/test_mcp_server.py::test_start_in_thread_rejects_a_second_start'),
    # nothing pins this: no test makes a client aclose raise
    ('daedalus_mcp.transport', 'close_current_loop_clients',
     'isinstance(outcome, BaseException)', None),
    ('daedalus_mcp.transport', 'token', 'not t',
     'tests/test_mcp_transport_guards.py::test_empty_token_context_is_'
     'rejected'),
    ('daedalus_mcp.transport', 'poll_result',
     "raise TimeoutError(f'no result within {timeout}s')",
     'tests/test_mcp_transport_guards.py::'
     'test_poll_reports_timeout_when_no_attempt_is_admitted'),
    # covers 0, -1, nan and inf alone
    ('daedalus_mcp.transport', 'checked_timeout',
     'not math.isfinite(timeout) or timeout <= 0',
     'tests/test_mcp_server.py::'
     'test_a_nonpositive_mcp_timeout_admits_no_command'),
    ('daedalus_mcp.transport', 'ext_cmd', "res.get('error')",
     'tests/test_mcp_transport_guards.py::'
     'test_extension_command_surfaces_result_error'),
    ('daedalus_bridge.env_config', 'env_int',
     "raise SystemExit(f'{name} must be {requirement}; got {raw!r}') "
     'from None',
     'tests/test_mcp_server.py::'
     'test_mcp_and_bridge_config_use_one_env_parser'),
    ('daedalus_bridge.env_config', 'env_int',
     'value < minimum or (maximum is not None and value > maximum)',
     'tests/test_mcp_server.py::'
     'test_mcp_and_bridge_config_use_one_env_parser'),
    # nothing pins this: no test feeds a positive-float setting an
    # unparseable value
    ('daedalus_bridge.env_config', 'env_positive_float',
     "raise SystemExit(f'{name} must be a finite positive number; "
     'got {raw!r}\') from None', None),
    ('daedalus_bridge.env_config', 'env_positive_float',
     'not math.isfinite(value) or value <= 0',
     'tests/test_stream_lifecycle.py::'
     'test_numeric_environment_settings_fail_cleanly_at_startup'),
    ('daedalus_cli.transport', 'required', 'not v',
     'tests/test_cli.py::test_missing_token_is_an_error'),
    # nothing pins these: no CLI test drives --max at all; only
    # test_extension_policy pins the ceiling constant, not the refusal
    ('daedalus_cli.transport', 'capture_limit',
     "raise argparse.ArgumentTypeError(f'--max must be an integer from 1 "
     'to {NET_CAPTURE_MAX}; got {value!r}\') from None', None),
    ('daedalus_cli.transport', 'capture_limit',
     'limit < 1 or limit > NET_CAPTURE_MAX', None),
    # nothing pins this: no CLI test passes --timeout a non-integer
    ('daedalus_cli.transport', 'positive_timeout',
     "raise argparse.ArgumentTypeError(f'timeout must be a whole number of "
     "seconds; got {value!r}') from None", None),
    ('daedalus_cli.transport', 'positive_timeout', 'seconds < 0',
     'tests/test_cli.py::'
     'test_a_negative_timeout_is_refused_before_the_command_is_sent'),
}
