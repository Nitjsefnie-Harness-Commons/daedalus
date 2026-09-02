"""The guards every MCP tool refusal must witness, read from the source.

Nothing in TOOL_REFUSALS says which guard a case exists for, so deleting one
used to assert nothing. This reads the raises the package spells and makes
each an obligation of the tools that reach it.
"""
import ast
import dis
import importlib.util
from pathlib import Path

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
        'not value',
        "raise RuntimeError('guarded by nothing')",
        "raise RuntimeError('in an else')",
        'value < 1',
        'value < 100 and value > 50',
        'value == 42',
    ],
    ('guard_shapes', 'parser'): [
        "raise RuntimeError('re-raised from a handler')"],
}

GUARD_SHAPE_WITNESSES = [
    # (helper argument, the condition it fired from). 0 satisfies both
    # operands of the first guard, so it pins the short-circuit order too.
    (0, 'not value'),
    (-1, 'value < 1'),
    (60, 'value < 100 and value > 50'),
    (5, "raise RuntimeError('guarded by nothing')"),
    # A comprehension cannot be re-read outside the frame that ran it.
    (42, None),
]

# Deciding which `or` operand fired runs its source a second time, so only a
# bare-name predicate is re-read: a method call is where a mutation of what it
# reads would hide. Anything else is reported undeterminable, not guessed at.
PURE_CALLS = frozenset({'isinstance', 'len', 'bool', 'abs'})
_UNREADABLE = (ast.Await, ast.Lambda, ast.NamedExpr, ast.Yield, ast.YieldFrom,
               ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _rereadable(node):
    for child in ast.walk(node):
        if isinstance(child, _UNREADABLE):
            return False
        if isinstance(child, ast.Call) and not (
                isinstance(child.func, ast.Name)
                and child.func.id in PURE_CALLS):
            return False
    return True


def _guard_operands(test):
    """The conditions of one `if` test that can fire its raise alone.

    Only `or` splits: each of its operands is independently sufficient, so
    each needs its own witness. No operand of an `and` is.
    """
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return list(test.values)
    return [test]


def _enclosing_function(node, parents):
    while node is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
        node = parents.get(node)
    return ''


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
        else:
            operands = [(_unguarded_text(node), None)]
        key = (str(path.resolve()), node.lineno)
        if key in sites:
            # Two raises on one line cannot be told apart by a traceback, so
            # neither can be witnessed; say so instead of letting one witness
            # answer for both.
            operands = [(f'two raises share line {node.lineno}', None)]
        sites[key] = (path.stem, _enclosing_function(node, parents), operands)
    return sites


def _unguarded_text(node):
    """Names a raise no `if` body holds: an `else`, a handler, a fall through.

    A bare re-raise unparses to `raise` wherever it stands, so it carries its
    line rather than colliding; anything else describes itself."""
    if node.exc is None:
        return f'bare raise at line {node.lineno}'
    return ast.unparse(node)


def tool_guards(paths):
    """Every raise the MCP package spells, keyed by (file, line).

    Reading the source rather than the pin table is what makes a deleted
    refusal case visible, and the scan covers the whole package so moving a
    guard out of `tools_*.py` cannot move it out of view. An `assert` states
    a type invariant rather than refusing an argument, and is left out.
    """
    sites = {}
    for path in paths:
        sites.update(_module_guard_sites(path))
    return sites


def reachable_guards(sites, codes):
    """The raise sites a tool's own reachable code objects can raise from.

    A method on the bridge object, or a function found through globals, is
    not this tool's obligation; it is accounted for off the tool surface.
    """
    reached = {}
    for code in codes:
        filename = str(Path(code.co_filename).resolve())
        for instruction in dis.get_instructions(code):
            positions = instruction.positions
            if instruction.opname != 'RAISE_VARARGS' or positions is None:
                continue
            site = sites.get((filename, positions.lineno))
            if site is not None:
                reached[(filename, positions.lineno)] = site
    return reached


def guard_keys(sites):
    """The (module, function, condition) triples a site mapping spells."""
    return [(module, function, text)
            for module, function, operands in sites.values()
            for text, _node in operands]


def witnessed_guard(sites, raised):
    """The (module, function, condition) a caught refusal fired from.

    The innermost traceback frame on a known raise names the site, so a guard
    reached through a helper is witnessed where it is written. Of several `or`
    operands the first one true of that frame fired, as the interpreter
    short-circuited. The condition is None where they cannot be re-read;
    guessing would credit an operand that never fired.
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
    (module, function, operands), frame = fired
    if len(operands) == 1:
        return (module, function, operands[0][0])
    for text, node in operands:
        if not _rereadable(node):
            return (module, function, None)
        try:
            decided = eval(  # pylint: disable=eval-used
                compile(ast.Expression(node), '<guard>', 'eval'),
                dict(frame.f_globals), dict(frame.f_locals))
        except Exception:
            return (module, function, None)
        if decided:
            return (module, function, text)
    return (module, function, None)


def guard_shape_probe(directory):
    """GUARD_SHAPES written, scanned and imported, for the floor's tests."""
    path = Path(directory) / 'guard_shapes.py'
    path.write_text(GUARD_SHAPES, encoding='utf-8')
    spec = importlib.util.spec_from_file_location('guard_shapes', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tool_guards([path]), module.outer()


UNWITNESSED_GUARDS = {
    # Per REGISTERED TOOL: guards the tool's own code reaches that no refusal
    # case of its own fires, each carrying the reason. Keyed by the same
    # (module, function, condition) triple the scan spells, so an entry cannot
    # exempt a same-named function in another module. A stale entry - one the
    # tool does not reach, or one its own case already witnesses - is refused.
    # navigate, reload, title and url each hand _send_eval a constant id and
    # code, so neither of its guards can fire from them.
    'navigate': {('tools_eval', '_send_eval', 'not cmd_id'),
                 ('tools_eval', '_send_eval', 'not code')},
    'reload': {('tools_eval', '_send_eval', 'not cmd_id'),
               ('tools_eval', '_send_eval', 'not code')},
    'title': {('tools_eval', '_send_eval', 'not cmd_id'),
              ('tools_eval', '_send_eval', 'not code')},
    'url': {('tools_eval', '_send_eval', 'not cmd_id'),
            ('tools_eval', '_send_eval', 'not code')},
    'ping': {
        # test_ping_raises_the_bridge_error pins it: the refusal driver cannot
        # set the poll body this branch reads
        ('tools_eval', 'ping', "res.get('error')"),
    },
    'segment_status': {
        # test_segment_status_refuses_an_absent_job pins it, and
        # test_segment_status_refuses_a_job_owned_by_another_token the other:
        # the refusal driver cannot set the HTTP status either branch reads
        ('tools_media', 'segment_status', 'found.status_code == 404'),
        ('tools_media', 'segment_status', 'found.status_code == 409'),
    },
}

GUARDS_OFF_THE_TOOL_SURFACE = {
    # Every raise in the package no registered tool's own code reaches, and the
    # suite that pins each. Declared exactly, and compared both ways, so moving
    # a tool's guard into a module off this surface adds an entry nobody wrote
    # and fails, instead of widening the blind spot in silence.
    #
    # test_mcp_server.py, test_start_in_thread_rejects_a_second_start
    ('server', 'start_in_thread', "_start_state['started']"),
    # test_mcp_server.py, test_mcp_lifespan_closes_loop_clients
    ('transport', 'close_current_loop_clients',
     'isinstance(outcome, BaseException)'),
    # test_mcp_transport_guards.py, test_empty_token_context_is_rejected
    ('transport', 'token', 'not t'),
    # test_mcp_transport_guards.py, test_poll_reports_timeout_...
    ('transport', 'poll_result',
     "raise TimeoutError(f'no result within {timeout}s')"),
    # test_mcp_server.py, test_a_nonpositive_mcp_timeout_admits_no_command,
    # and test_cli.py, test_a_negative_timeout_is_refused_...; both reached
    # through the bridge object rather than through any tool's own code
    ('transport', 'checked_timeout', 'not math.isfinite(timeout)'),
    ('transport', 'checked_timeout', 'timeout <= 0'),
    # test_mcp_transport_guards.py, test_extension_command_surfaces_...
    ('transport', 'ext_cmd', "res.get('error')"),
}


def unwitnessed_guard_gaps(tool, reached, witnessed):
    """Guards a tool reaches that neither its cases nor the allowlist cover.

    `reached` is what the tool's own code can raise from, `witnessed` what its
    OWN cases fired, and its UNWITNESSED_GUARDS entry what it pins nowhere. A
    guard witnessed through a sibling tool does not discharge this one, for
    the reason UNPINNED_PARAMETERS is per tool: deleting a tool's entries
    would otherwise hide behind whichever sibling shares the helper. A stale
    entry is refused, and so is one condition spelling two guards.
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
    """Package guards no registered tool's own code reaches.

    Compared against GUARDS_OFF_THE_TOOL_SURFACE both ways, so moving a guard
    off the tool surface adds a triple nobody declared and retiring one leaves
    a declaration nothing raises. Either fails.
    """
    return set(guard_keys(sites)) - {
        key for keys in reached.values() for key in keys}


def stranded_guards(reached, witnessed):
    """Every tool's unwitnessed or stale guard pins.

    `witnessed` is (tool, guard) pairs, and the filter back to the owning tool
    is what makes each tool's own entries load-bearing.
    """
    stranded = {}
    for tool, keys in reached.items():
        gaps = unwitnessed_guard_gaps(
            tool, keys, {key for owner, key in witnessed if owner == tool})
        if gaps:
            stranded[tool] = gaps
    return stranded
