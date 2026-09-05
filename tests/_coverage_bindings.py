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


def _unfollowable_launcher_bindings(tree, facts):
    """Lines binding a launcher where the alias walk cannot see it."""
    lines = []
    for node in memo_nodes(tree):
        for line, value in _bound_values(node, facts):
            if any(_names_one_of(part, facts.subprocess_modules)
                   or _is_launch_value(part, facts)
                   for part in _carried_parts(value)):
                lines.append(line)
    return lines
