"""Not a suite itself — run_tests.py only loads `test_*.py`."""
import ast

SWEEP_ENTRY = (
    'test_each_new_binding_and_match_arm_is_mutation_sensitive')
# The four launchers whose deadline is their own `timeout=` keyword: the
# five-name family of tests/_coverage_bindings.py's _LAUNCHERS less
# `Popen`, whose deadline lives on the `wait` rather than on the launch.
_LAUNCHERS = frozenset({'run', 'call', 'check_call', 'check_output'})
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                ast.ClassDef)
_APPENDERS = ('append', 'extend')


def _callee(call):
    function = call.func
    if (isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)):
        return f'{function.value.id}.{function.attr}'
    return getattr(function, 'id', None)


def _run_spellings(tree):
    """Every spelling of a bounded launcher this module's own imports give.

    A `from subprocess import *` binds all four at once, and the scan
    cannot read past it to a later import that rebinds one, so it takes
    the name rather than passing it over.
    """
    spellings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'subprocess':
                    spellings |= {f'{alias.asname or "subprocess"}.{one}'
                                  for one in _LAUNCHERS}
                elif alias.name.startswith('subprocess.'):
                    # `import subprocess.run` binds the name
                    # `subprocess`, so it reaches the same launchers; but
                    # `import subprocess.run as sr` binds `sr` to the
                    # FUNCTION, so `sr` is itself a launcher.
                    member = alias.name.split('.', 1)[1]
                    spellings |= ({alias.asname} if alias.asname
                                  and member in _LAUNCHERS
                                  else {f'subprocess.{one}'
                                        for one in _LAUNCHERS})
        elif (isinstance(node, ast.ImportFrom)
                and node.module == 'subprocess'):
            spellings |= (_LAUNCHERS if any(alias.name == '*'
                                            for alias in node.names)
                          else {alias.asname or alias.name
                                for alias in node.names
                                if alias.name in _LAUNCHERS})
    return spellings


def _spelled(node, bound, seen=(), before=0):
    """The text a literal-only expression spells.

    A list is a program too, because `subprocess.run` takes its argv as
    one: its elements are read and joined, so a program assembled outside
    the call is seen the same as an inline one. A name resolves to the
    binding in force AT `before`, the line of the launch being judged:
    the LAST one at or before it, not every binding that ever existed by
    then, so a name the scope reused for something else afterwards does
    not colour a launch that ran before the reuse.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [piece.value if isinstance(piece, ast.Constant) else '?'
                 for piece in node.values]
        return ''.join(str(part) for part in parts)
    if isinstance(node, (ast.List, ast.Tuple)):
        parts = [_spelled(item, bound, seen, before) for item in node.elts]
        return '\n'.join(part for part in parts if part is not None)
    if isinstance(node, ast.Dict):
        parts = [_spelled(item, bound, seen, before)
                 for item in (*node.keys, *node.values) if item is not None]
        return '\n'.join(part for part in parts if part is not None)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = [_spelled(side, bound, seen, before)
                 for side in (node.left, node.right)]
        return None if None in parts else ''.join(parts)
    if isinstance(node, ast.Name) and id(node) not in seen:
        spelling = seen + (id(node),)
        eligible = [entry for entry in bound.get(node.id, ())
                    if entry[0] <= before]
        if not eligible:
            return None
        in_force = sorted(eligible, key=lambda entry: entry[0])[-1]
        return _spelled(in_force[1], bound, spelling, before)
    return None


def _captures(statement):
    """Every name one match statement binds, at any depth.

    Most capture slots are nodes and the walk reaches them, including a
    `*rest` (a `MatchStar`) inside a sequence pattern. A mapping's
    `**rest` is the exception: `ast.MatchMapping.rest` is a plain `str`,
    so there is nothing to walk to and it is collected by hand.
    """
    names = []
    for node in ast.walk(statement):
        if isinstance(node, ast.MatchAs) and node.name is not None:
            names.append(node.name)
        elif isinstance(node, ast.MatchStar) and node.name is not None:
            names.append(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            names.append(node.rest)
    return names


def _sweep_bind(node, bound):
    """Record what one statement binds in its scope, and on what line.

    Every bare-name target Python's own statement forms offer that names
    a readable value is read: `=`, `+=`, an annotated `=`, a walrus, a
    `for`/`in` target, a match capture, and `argv.append(program)` /
    `argv.extend([...])`, which is the other way an argv is built. A
    match capture is bound to the match SUBJECT rather than to the value
    its own pattern matched, which over-approximates — every capture gets
    the whole subject — and fails toward finding a launch, not past one.

    What is left unbound is the list
    `tests/test_static_guard_regressions.py`'s
    `test_a_sweep_launch_carries_no_wall_clock_bound` owns, so that the
    two cannot drift: the forms naming a value this cannot read (`with`
    and `except` name a context manager and an exception, so they spell
    to nothing) and the targets that are not bare names.
    """
    line = getattr(node, 'lineno', None)
    if line is None:
        return
    targets = ()
    value = None
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
        targets, value = [node.target], node.value
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        targets, value = [node.target], node.iter
    elif isinstance(node, ast.Match):
        for name in _captures(node):
            bound.setdefault(name, []).append((line, node.subject))
    for target in targets:
        if isinstance(target, ast.Name) and value is not None:
            bound.setdefault(target.id, []).append((line, value))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _APPENDERS and node.args
            and isinstance(node.func.value, ast.Name)):
        name = node.func.value.id
        bound.setdefault(name, []).append((line, node.args[0]))


def _carries_sweep(call, bound):
    """Does any argument spell the sweep's entry test?

    The program reaches the child as an element of the argv list, so the
    walk covers the whole argument rather than its top node.
    """
    for argument in (*call.args,
                     *(keyword.value for keyword in call.keywords)):
        for node in ast.walk(argument):
            if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.Name)):
                continue
            text = _spelled(node, bound, before=call.lineno)
            if text and SWEEP_ENTRY in text:
                return True
    return False


def _sweep_scope_nodes(scope):
    """Every node of one scope's own body, nested scopes left unopened."""
    pending = [scope]
    while pending:
        for child in ast.iter_child_nodes(pending.pop()):
            yield child
            if not isinstance(child, _SCOPE_NODES):
                pending.append(child)


def sweep_launches(tree, relative):
    """The (relative, line) of each sweep launch, and of each `timeout` on one.

    A nested scope is a scope of its own, so a call and the program it
    runs are read together and never borrowed from a sibling. A scope's
    whole body is bound in a first pass, before any call in it is
    judged, so a program written inside a branch or a loop is read too.
    """
    spellings = _run_spellings(tree)
    launches, timed = [], []
    pending = [(tree, {})]
    while pending:
        scope, bound = pending.pop()
        own = list(_sweep_scope_nodes(scope))
        # TWO PASSES, AND THE ORDER IS LOAD-BEARING: every binding in this
        # scope is recorded before any call in it is judged, so a program
        # written inside a branch is read. Collapsing the two loops is a
        # false green, not a simplification.
        for node in own:
            if isinstance(node, _SCOPE_NODES):
                pending.append((node, {}))
            else:
                _sweep_bind(node, bound)
        for node in own:
            if (isinstance(node, ast.Call)
                    and _callee(node) in spellings
                    and _carries_sweep(node, bound)):
                launches.append((relative, node.lineno))
                timed += [(relative, node.lineno)
                          for keyword in node.keywords
                          if keyword.arg == 'timeout']
    return launches, timed
