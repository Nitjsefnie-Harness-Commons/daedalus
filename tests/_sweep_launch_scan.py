"""The launches that run the mutation sweep, and any wall bound on them.

Not a suite itself — run_tests.py only loads `test_*.py`.
"""
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
    """The call's callee spelled `module.member`, or its bare name."""
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
            spellings |= {f'{alias.asname or "subprocess"}.{launcher}'
                          for alias in node.names
                          if alias.name == 'subprocess'
                          for launcher in _LAUNCHERS}
        elif (isinstance(node, ast.ImportFrom)
                and node.module == 'subprocess'):
            spellings |= (_LAUNCHERS if any(alias.name == '*'
                                            for alias in node.names)
                          else {alias.asname or alias.name
                                for alias in node.names
                                if alias.name in _LAUNCHERS})
    return spellings


def _spelled(node, bound, seen=(), before=0):
    """The text a literal-only expression spells, or None.

    A list is a program too, because `subprocess.run` takes its argv as
    one: its elements are read and joined, so a program assembled outside
    the call is seen the same as an inline one. A name carries every
    binding its scope gave it AT OR BEFORE `before`, the line of the
    launch being judged, so a program the scope only defines afterwards
    cannot be read into a call it did not run.
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
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = [_spelled(side, bound, seen, before)
                 for side in (node.left, node.right)]
        return None if None in parts else ''.join(parts)
    if isinstance(node, ast.Name) and id(node) not in seen:
        spelling = seen + (id(node),)
        parts = [_spelled(value, bound, spelling, before)
                 for line, value in bound.get(node.id, ()) if line <= before]
        return '\n'.join(text for text in parts if text is not None)
    return None


def _statement(node):
    """The expression a statement carries, or the node itself."""
    return node.value if isinstance(node, ast.Expr) else node


def _bind(node, bound):
    """Record what one statement binds in its scope, and on what line.

    Every bare-name target Python's own statement forms offer that names
    a readable value is read: `=`, `+=`, an annotated `=`, a walrus, a
    `for`/`in` target, and `argv.append(program)` / `argv.extend([...])`,
    which is the other way an argv is built. The forms that name a value
    the scan cannot read — `with`/`as` and `except`/`as` name a context
    manager and an exception, a comprehension target and a parameter
    default name a value of another scope — are left unbound and declared
    in the guard's docstring rather than bound to a node that spells to
    nothing. A target that is not a bare name — a tuple unpacking, a
    subscript, a class attribute — is likewise not read.
    """
    node = _statement(node)
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


def _own_nodes(scope):
    """Every node of one scope's own body, nested scopes left unopened.

    A nested scope is yielded as a node but not descended into, so its
    bindings never leak into the scope around it.
    """
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
    whole body is bound before any call in it is judged, so a program
    written inside a branch or a loop is read; and each binding is read
    only up to the call's own line, so a scope that reuses a name for an
    unrelated program afterwards reds nothing.
    """
    spellings = _run_spellings(tree)
    launches, timed = [], []
    pending = [(tree, {})]
    while pending:
        scope, bound = pending.pop()
        own = list(_own_nodes(scope))
        for node in own:
            if isinstance(node, _SCOPE_NODES):
                pending.append((node, {}))
            else:
                _bind(node, bound)
        for node in own:
            if (isinstance(node, ast.Call)
                    and _callee(node) in spellings
                    and _carries_sweep(node, bound)):
                launches.append((relative, node.lineno))
                timed += [(relative, node.lineno)
                          for keyword in node.keywords
                          if keyword.arg == 'timeout']
    return launches, timed
