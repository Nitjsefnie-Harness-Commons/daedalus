"""Launcher bindings the coverage-environment alias walk cannot follow."""
import ast

from _coverage_memo import nodes as memo_nodes


_LAUNCHERS = frozenset(
    {'run', 'Popen', 'call', 'check_call', 'check_output'})


def _is_launch_value(value, facts):
    """Whether an expression is a subprocess launcher or a known alias."""
    if isinstance(value, ast.Name):
        return value.id in facts.launch_callables
    return (isinstance(value, ast.Attribute)
            and value.attr in _LAUNCHERS
            and isinstance(value.value, ast.Name)
            and value.value.id in facts.subprocess_modules)


def _names_one_of(value, names):
    return isinstance(value, ast.Name) and value.id in names


def _bound_values(node, facts):
    """Every (statement line, value) the statement binds unreadably."""
    if isinstance(node, ast.Assign):
        if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and (_names_one_of(node.value, facts.subprocess_modules)
                     or _is_launch_value(node.value, facts))):
            return []
        return [(node.lineno, node.value)]
    if isinstance(node, ast.AnnAssign):
        if node.value is None:
            return []
        if (isinstance(node.target, ast.Name)
                and (_names_one_of(node.value, facts.subprocess_modules)
                     or _is_launch_value(node.value, facts))):
            return []
        return [(node.lineno, node.value)]
    if isinstance(node, ast.AugAssign):
        return [(node.lineno, node.value)]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [(node.lineno, node.iter)]
    if isinstance(node, ast.comprehension):
        return [(node.target.lineno, node.iter)]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [(node.lineno, item.context_expr) for item in node.items
                if item.optional_vars is not None]
    if isinstance(node, ast.NamedExpr):
        return [(node.lineno, node.value)]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        defaults = [*node.args.defaults,
                    *(value for value in node.args.kw_defaults
                      if value is not None)]
        return [(value.lineno, value) for value in defaults]
    if isinstance(node, ast.Match) and any(
            _pattern_binds(case.pattern) for case in node.cases):
        return [(node.lineno, node.subject)]
    return []


def _pattern_binds(pattern):
    """Whether a match pattern captures at least one name."""
    return any(
        isinstance(part, (ast.MatchAs, ast.MatchStar)) and part.name
        or isinstance(part, ast.MatchMapping) and part.rest
        for part in ast.walk(pattern)
    )


def _carried_parts(value):
    """Carried elements and arguments, including a call-based callee."""
    if isinstance(value, ast.Call):
        for part in [*value.args,
                     *(keyword.value for keyword in value.keywords)]:
            yield from _carried_parts(part)
        callee = value.func
        while isinstance(callee, (ast.Attribute, ast.Subscript)):
            callee = callee.value
        if isinstance(callee, ast.Call):
            yield from _carried_parts(callee)
    elif isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        for part in value.elts:
            yield from _carried_parts(part)
    elif isinstance(value, ast.Dict):
        for part in [*value.keys, *value.values]:
            if part is not None:
                yield from _carried_parts(part)
    elif isinstance(value, ast.Subscript):
        yield from _carried_parts(value.value)
        yield from _carried_parts(value.slice)
    elif isinstance(value, ast.Starred):
        yield from _carried_parts(value.value)
    else:
        yield value


def _has_cwd_control(value):
    """Whether a call spells a cwd keyword or a literal cwd spread."""
    for keyword in value.keywords:
        if keyword.arg == 'cwd':
            return True
        if keyword.arg is not None:
            continue
        spread = keyword.value
        if (isinstance(spread, ast.Dict) and any(
                isinstance(key, ast.Constant) and key.value == 'cwd'
                for key in spread.keys)):
            return True
        if (isinstance(spread, ast.Call)
                and isinstance(spread.func, ast.Name)
                and spread.func.id == 'dict'
                and any(item.arg == 'cwd' for item in spread.keywords)):
            return True
    return False


def _call_receiver_parts(value):
    """Carried elements in a dictionary-backed call receiver."""
    callee = value.func
    while isinstance(callee, (ast.Attribute, ast.Subscript)):
        callee = callee.value
    if isinstance(callee, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
        yield from _carried_parts(callee)


def _unfollowable_launcher_bindings(tree, facts):
    """Lines binding or calling a launcher the alias walk cannot follow."""
    lines = []
    for node in memo_nodes(tree):
        for line, value in _bound_values(node, facts):
            if any(_names_one_of(part, facts.subprocess_modules)
                   or _is_launch_value(part, facts)
                   for part in _carried_parts(value)):
                lines.append(line)
        if (isinstance(node, ast.Call)
                and not _has_cwd_control(node)
                and any(_names_one_of(part, facts.subprocess_modules)
                        or _is_launch_value(part, facts)
                        for part in _call_receiver_parts(node))):
            lines.append(node.lineno)
    return lines
