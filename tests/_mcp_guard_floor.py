"""The guards every MCP tool refusal must witness, read from the source.

Nothing in TOOL_REFUSALS says which guard a case exists for, so deleting one
used to assert nothing. This reads the raises the tool surface and its
imports spell and makes each an obligation of the tools that reach it.

Two stated limits. A refusal written as `return {'error': ...}` — the
convention at daedalus_mcp/tools_css.py `unblock_requests` — never raises, so
it is off this floor entirely. Reachability is lexical: a guard reached only
through a bridge method or a globals lookup lands off the tool surface,
declared in GUARDS_OFF_THE_TOOL_SURFACE.
"""
import ast
import dis
import importlib.util
from pathlib import Path
import sys

GUARD_SHAPES = """
try:
    raise ValueError('at module level')
except ValueError:
    pass


def outer():
    def helper(value):
        if not value or value < 1:
            raise ValueError('either condition fires this')
        if value < 100 and value > 50:
            raise ValueError('neither condition fires this alone')
        if [c for c in str(value) if c == '9'] or value == 42:
            raise ValueError('an operand that cannot be re-read')
        if any(c == '6' for c in str(value)) or value == 17:
            raise ValueError('an aggregator operand re-reads')
        assert value < 1000
        if value:
            pass
        else:
            raise RuntimeError('in an else')
        raise RuntimeError('guarded by nothing')

    def parser(text):
        try:
            return int(text)
        except ValueError:
            raise RuntimeError('re-raised from a handler')

    return helper, parser
"""

GUARD_SHAPE_CONDITIONS = {
    ('guard_shapes', ''): ["raise ValueError('at module level')"],
    ('guard_shapes', 'helper'): [
        "[c for c in str(value) if c == '9']",
        "any((c == '6' for c in str(value)))",
        'not value',
        "raise RuntimeError('guarded by nothing')",
        "raise RuntimeError('in an else')",
        'value < 1',
        'value < 100 and value > 50',
        'value == 17',
        'value == 42',
    ],
    ('guard_shapes', 'parser'): [
        "raise RuntimeError('re-raised from a handler')"],
}

GUARD_SHAPE_WITNESSES = [
    # (helper argument, the condition it fired from); 0 pins the
    # short-circuit order, 42 credits the sibling an unreadable operand
    # proves false, 9 is an unreadable operand that fired itself.
    (0, 'not value'),
    (-1, 'value < 1'),
    (60, 'value < 100 and value > 50'),
    (5, "raise RuntimeError('guarded by nothing')"),
    (26, "any((c == '6' for c in str(value)))"),
    (17, 'value == 17'),
    (42, 'value == 42'),
    (9, None),
]

# Re-deciding which operand fired runs its source a second time, so only
# these re-read: the bare-name calls below, the methods in PURE_METHODS, a
# pure module attribute such as math.isfinite, and a comprehension only as
# an aggregator's direct argument, since anything it walks is walked again.
PURE_CALLS = frozenset({'isinstance', 'len', 'bool', 'abs', 'int', 'str',
                        'min', 'max', 'any', 'all'})
AGGREGATES = frozenset({'all', 'any', 'len', 'max', 'min'})
PURE_METHODS = frozenset({'strip', 'startswith'})
PURE_ATTR_CALLS = frozenset({'isfinite'})
_UNREADABLE = (ast.Await, ast.Lambda, ast.NamedExpr, ast.Yield, ast.YieldFrom,
               ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _rereadable(node):
    parents = {child: parent for parent in ast.walk(node)
               for child in ast.iter_child_nodes(parent)}
    for child in ast.walk(node):
        if isinstance(child, _UNREADABLE):
            if not _aggregated(child, parents):
                return False
        if isinstance(child, ast.Call) and not _callable_pure(child):
            return False
    return True


def _aggregated(comp, parents):
    parent = parents.get(comp)
    return (isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id in AGGREGATES
            and comp in parent.args)


def _callable_pure(call):
    if isinstance(call.func, ast.Name):
        return call.func.id in PURE_CALLS
    return (isinstance(call.func, ast.Attribute)
            and call.func.attr in PURE_METHODS | PURE_ATTR_CALLS
            and isinstance(call.func.value, ast.Name))


def _guard_operands(test):
    """One condition per `or` operand: each can fire the raise alone. An
    `and` operand cannot, so an `and` test stays one condition."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return list(test.values)
    return [test]


def _enclosing_function(node, parents):
    while node is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
        node = parents.get(node)
    return ''


def _contains_or(test):
    """True when `ast.Or` appears anywhere in the test expression."""
    return any(isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
               for node in ast.walk(test))


def _module_guard_sites(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    sites = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue
        guard = parents.get(node)
        if isinstance(guard, ast.If) and node in guard.body:
            operands = [(ast.unparse(operand), operand)
                        for operand in _guard_operands(guard.test)]
            or_guard = _contains_or(guard.test)
        else:
            operands = [(_unguarded_text(node), None)]
            or_guard = False
        key = (str(path.resolve()), node.lineno)
        if key in sites:
            # Two raises on one line cannot be told apart by a traceback, so
            # neither can be witnessed; say so instead of letting one witness
            # answer for both.
            operands = [(f'two raises share line {node.lineno}', None)]
        sites[key] = (path.stem, _enclosing_function(node, parents), operands,
                      or_guard)
    return sites


def _unguarded_text(node):
    """Names a raise no `if` body holds. A bare re-raise unparses to `raise`
    wherever it stands, so it carries its line rather than colliding."""
    if node.exc is None:
        return f'bare raise at line {node.lineno}'
    return ast.unparse(node)


def composition_scan_set(composition, root):
    """The repo-local modules the composition imports, plus the composition.

    sys.modules after the composition loads is what the import graph really
    reached, so a guard that moves into any module it pulls in stays scanned;
    a directory or a glob cannot make that promise, which is how the blind
    spot survived two widenings. The composition itself is added explicitly:
    the suite loads it by path and nothing registers it, so its raises would
    otherwise vanish.
    """
    root = Path(root).resolve()
    paths = {Path(composition.__file__).resolve()}
    for module in list(sys.modules.values()):
        file = getattr(module, '__file__', None)
        if file is None:
            continue
        path = Path(file).resolve()
        if root not in path.parents or 'tests' in path.parts:
            continue
        paths.add(path)
    return sorted(paths)


def tool_guards(paths):
    """Every raise the given modules spell, keyed by (file, line); reading
    the source is what makes a deleted refusal case visible. An `assert`
    states an invariant rather than refusing an argument, and is left out."""
    sites = {}
    for path in paths:
        sites.update(_module_guard_sites(path))
    return sites


def reachable_guards(sites, codes):
    """The raise sites a tool's own reachable code objects can raise from; a
    bridge method or a globals lookup is accounted for off the tool surface.
    A reached site refusing on an `or` test is refused by the floor: split it
    into one raise per condition, because one witness cannot answer for two
    conditions on one line."""
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
            if site[3]:
                raise AssertionError(
                    f'{site[0]}.{site[1]} refuses on an `or` test '
                    f'({site[2]}); split it into one raise per condition, '
                    'because one witness cannot answer for two conditions '
                    'on one line')
            reached[(filename, positions.lineno)] = site
    return reached


def guard_keys(sites):
    """The (module, function, condition) triples a site mapping spells."""
    return [(module, function, text)
            for module, function, operands, _or in sites.values()
            for text, _node in operands]


def witnessed_guard(sites, raised):
    """The (module, function, condition) a caught refusal fired from.

    The innermost scanned frame on the traceback names the raise; of several
    `or` operands the first true one fired. An operand the floor cannot
    re-read hides only itself — reaching a later operand proves the earlier
    ones false at the raise. None is returned when nothing can be decided.
    """
    fired = None
    trace = raised.__traceback__
    while trace is not None:
        site = sites.get(
            (str(Path(trace.tb_frame.f_code.co_filename).resolve()),
             trace.tb_lineno))
        if site is not None:
            fired = (site, trace.tb_frame)
        trace = trace.tb_next
    if fired is None:
        return None
    (module, function, operands, _or), frame = fired
    if len(operands) == 1:
        return (module, function, operands[0][0])
    for text, node in operands:
        if not _rereadable(node):
            continue
        try:
            # bool() inside the catch: a bare name's truthiness can itself
            # raise, and eval only loads it.
            decided = bool(eval(  # pylint: disable=eval-used
                compile(ast.Expression(node), '<guard>', 'eval'),
                dict(frame.f_globals), dict(frame.f_locals)))
        except Exception:
            continue
        if decided:
            return (module, function, text)
    return (module, function, None)


def unreadable_note(sites, fired):
    """The remedy for a refusal resolved to (module, function, None): naming
    the operands and the way out keeps the failure a work order — a bare
    `no guard its own code reaches` reads as the floor rejecting correct
    code, and code a guard rejects gets weakened to fit."""
    if fired is None or fired[2] is not None:
        return ''
    operands = sorted({text for module, function, texts in sites.values()
                       if (module, function) == fired[:2]
                       for text, _node in texts})
    return (f' — none of its operands {operands} could be re-read to decide '
            'which fired: keep guard operands re-readable, or extend '
            'PURE_CALLS, PURE_METHODS or PURE_ATTR_CALLS')


def guard_shape_probe(directory, source=GUARD_SHAPES, name='guard_shapes'):
    """A source string written, scanned and imported, for the floor's tests."""
    path = Path(directory) / f'{name}.py'
    path.write_text(source, encoding='utf-8')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tool_guards([path]), module


UNWITNESSED_GUARDS = {
    # Per REGISTERED TOOL, keyed by the same (module, function, condition)
    # triple the scan spells, so an entry cannot exempt a same-named function
    # in another module. A stale entry - unreachable, or already witnessed by
    # the tool's own case - is refused. navigate, reload, title and url each
    # hand _send_eval a constant id and code, so neither guard can fire from
    # them; ping's and segment_status's branches read a poll body / HTTP
    # status the refusal driver cannot set, and are pinned by the named tests.
    'navigate': {('tools_eval', '_send_eval', 'not cmd_id'),
                 ('tools_eval', '_send_eval', 'not code')},
    'reload': {('tools_eval', '_send_eval', 'not cmd_id'),
               ('tools_eval', '_send_eval', 'not code')},
    'title': {('tools_eval', '_send_eval', 'not cmd_id'),
              ('tools_eval', '_send_eval', 'not code')},
    'url': {('tools_eval', '_send_eval', 'not cmd_id'),
            ('tools_eval', '_send_eval', 'not code')},
    'ping': {
        # test_ping_raises_the_bridge_error pins it
        ('tools_eval', 'ping', "res.get('error')"),
    },
    'segment_status': {
        # test_segment_status_refuses_an_absent_job and
        # test_segment_status_refuses_a_job_owned_by_another_token pin these
        ('tools_media', 'segment_status', 'found.status_code == 404'),
        ('tools_media', 'segment_status', 'found.status_code == 409'),
    },
}

GUARDS_OFF_THE_TOOL_SURFACE = {
    # Every raise the scanned modules spell that no registered tool's own code
    # reaches, and the test that pins each — verified by planting the guard's
    # failure and watching that test go red, never by reading the test, which
    # is how one citation here came to name a test that never fires the raise.
    # An entry saying nothing pins it is a stated gap, not an excuse. The
    # comparison runs both ways, so an undeclared raise and a stale
    # declaration both fail.
    #
    # test_mcp_server.py: test_start_in_thread_rejects_a_second_start
    ('server', 'start_in_thread', "_start_state['started']"),
    # nothing pins this: no test makes a client aclose raise
    ('transport', 'close_current_loop_clients',
     'isinstance(outcome, BaseException)'),
    # test_mcp_transport_guards.py: test_empty_token_context_is_rejected
    ('transport', 'token', 'not t'),
    # test_mcp_transport_guards.py: test_poll_reports_timeout_...
    ('transport', 'poll_result',
     "raise TimeoutError(f'no result within {timeout}s')"),
    # test_mcp_server.py: test_a_nonpositive_mcp_timeout_admits_no_command,
    # alone: it covers 0, -1, nan and inf
    ('transport', 'checked_timeout', 'not math.isfinite(timeout)'),
    ('transport', 'checked_timeout', 'timeout <= 0'),
    # test_mcp_transport_guards.py: test_extension_command_surfaces_...
    ('transport', 'ext_cmd', "res.get('error')"),
    # test_mcp_server.py: test_mcp_and_bridge_config_use_one_env_parser, and
    # its sibling test_mcp_numeric_settings_fail_cleanly_at_startup
    ('env_config', 'env_int',
     "raise SystemExit(f'{name} must be {requirement}; got {raw!r}') "
     'from None'),
    ('env_config', 'env_int', 'value < minimum'),
    ('env_config', 'env_int', 'maximum is not None and value > maximum'),
    # nothing pins this: no test feeds a positive-float setting an
    # unparseable value
    ('env_config', 'env_positive_float',
     "raise SystemExit(f'{name} must be a finite positive number; "
     'got {raw!r}\') from None'),
    # test_stream_lifecycle.py:
    # test_numeric_environment_settings_fail_cleanly_at_startup
    ('env_config', 'env_positive_float', 'not math.isfinite(value)'),
    ('env_config', 'env_positive_float', 'value <= 0'),
    # test_cli.py: test_missing_token_is_an_error
    ('transport', 'required', 'not v'),
    # nothing pins these three: no CLI test drives --max at all; only
    # test_extension_policy pins the ceiling constant, not the refusal
    ('transport', 'capture_limit',
     "raise argparse.ArgumentTypeError(f'--max must be an integer from 1 "
     'to {NET_CAPTURE_MAX}; got {value!r}\') from None'),
    ('transport', 'capture_limit', 'limit < 1'),
    ('transport', 'capture_limit', 'limit > NET_CAPTURE_MAX'),
    # nothing pins this: no CLI test passes --timeout a non-integer
    ('transport', 'positive_timeout',
     "raise argparse.ArgumentTypeError(f'timeout must be a whole number of "
     "seconds; got {value!r}') from None"),
    # test_cli.py: test_a_negative_timeout_is_refused_before_the_command_...
    ('transport', 'positive_timeout', 'seconds < 0'),
}


def unwitnessed_guard_gaps(tool, reached, witnessed):
    """Guards a tool reaches that neither its cases nor the allowlist cover.

    A guard witnessed through a sibling does not discharge this tool:
    deleting a tool's entries would otherwise hide behind whichever sibling
    shares the helper. A stale entry is refused, and so is one condition
    spelling two guards.
    """
    reached = list(reached)
    spelled = set(reached)
    witnessed = set(witnessed)
    allowed = set(UNWITNESSED_GUARDS.get(tool, ()))
    return (
        [f'{key!r} spells two of the guards'
         for key in sorted(spelled) if reached.count(key) > 1]
        + [f'UNWITNESSED_GUARDS names {key!r}, which it does not reach'
           for key in sorted(allowed - spelled)]
        + [f'UNWITNESSED_GUARDS names {key!r}, which its own refusal case '
           'already witnesses' for key in sorted(allowed & witnessed)]
        + [f'no refusal case of its own witnesses {key!r}'
           for key in sorted(spelled - witnessed - allowed)])


def guards_off_the_tool_surface(sites, reached):
    """Guards no registered tool's own code reaches."""
    return set(guard_keys(sites)) - {
        key for keys in reached.values() for key in keys}


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
