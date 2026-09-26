"""The launches that run the mutation sweep, and any wall bound on them.

Not a suite itself — run_tests.py only loads `test_*.py`. The scan is
structural rather than a list of sites, so a launch added later is
covered without a table to maintain.
"""
import ast

SWEEP_ENTRY = (
    'test_each_new_binding_and_match_arm_is_mutation_sensitive')
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                ast.ClassDef)


def _callee(call):
    """The call's callee spelled `module.member`, or its bare name."""
    function = call.func
    if (isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)):
        return f'{function.value.id}.{function.attr}'
    return getattr(function, 'id', None)


def _run_spellings(tree):
    """Every spelling of subprocess.run this module's own imports give."""
    spellings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            spellings |= {f'{alias.asname or "subprocess"}.run'
                          for alias in node.names
                          if alias.name == 'subprocess'}
        elif (isinstance(node, ast.ImportFrom)
                and node.module == 'subprocess'):
            spellings |= {alias.asname or 'run' for alias in node.names
                          if alias.name == 'run'}
    return spellings


def _spelled(node, bound, seen=()):
    """The text a literal-only expression spells, or None."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [piece.value if isinstance(piece, ast.Constant) else '?'
                 for piece in node.values]
        return ''.join(str(part) for part in parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = [_spelled(side, bound, seen)
                 for side in (node.left, node.right)]
        return None if None in parts else ''.join(parts)
    if isinstance(node, ast.Name) and id(node) not in seen:
        for value in bound.get(node.id, ()):
            text = _spelled(value, bound, seen + (id(node),))
            if text is not None:
                return text
    return None


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
            text = _spelled(node, bound)
            if text and SWEEP_ENTRY in text:
                return True
    return False


def sweep_launches(tree, relative):
    """The (relative, line) of each sweep launch, then of each timed one.

    A nested scope is a scope of its own, so a call and the program it
    runs are read together and never borrowed from a sibling. Every
    binding naming a program is tried, so a name bound twice in one
    scope is a candidate rather than a refusal.
    """
    spellings = _run_spellings(tree)
    launches, timed = [], []
    pending = [(tree, {})]
    while pending:
        scope, bound = pending.pop()
        for child in ast.iter_child_nodes(scope):
            if isinstance(child, _SCOPE_NODES):
                pending.append((child, {}))
                continue
            pending.append((child, bound))
            if (isinstance(child, ast.Assign) and len(child.targets) == 1
                    and isinstance(child.targets[0], ast.Name)):
                bound.setdefault(
                    child.targets[0].id, []).append(child.value)
            elif (isinstance(child, ast.Call)
                    and _callee(child) in spellings
                    and _carries_sweep(child, bound)):
                launches.append((relative, child.lineno))
                timed += [(relative, child.lineno)
                          for keyword in child.keywords
                          if keyword.arg == 'timeout']
    return launches, timed
