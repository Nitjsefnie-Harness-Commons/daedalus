"""Launcher bindings the coverage-environment alias walk cannot follow."""
import ast

from _coverage_memo import nodes as memo_nodes


_LAUNCHERS = frozenset(
    {'run', 'Popen', 'call', 'check_call', 'check_output'})

# A header binds names: a decorator binds the decorated name, and a
# signature binds its parameters. Each form carries one, both or neither.
_HEADER_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                 ast.Lambda)
_DECORATED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_SIGNED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _is_launch_value(value, facts):
    if isinstance(value, ast.Name):
        return value.id in facts.launch_callables
    return (isinstance(value, ast.Attribute)
            and value.attr in _LAUNCHERS
            and isinstance(value.value, ast.Name)
            and value.value.id in facts.subprocess_modules)


def _names_one_of(value, names):
    return isinstance(value, ast.Name) and value.id in names


def _header_values(node):
    """The values a definition header binds, decorators and defaults."""
    if isinstance(node, _DECORATED_FORMS):
        decorators = node.decorator_list
    else:
        decorators = []
    if isinstance(node, _SIGNED_FORMS):
        defaults = [*node.args.defaults,
                    *(value for value in node.args.kw_defaults
                      if value is not None)]
    else:
        defaults = []
    return [*decorators, *defaults]


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
    if isinstance(node, _HEADER_FORMS):
        return [(value.lineno, value) for value in _header_values(node)]
    if isinstance(node, ast.Match) and any(
            _pattern_binds(case.pattern) for case in node.cases):
        return [(node.lineno, node.subject)]
    return []


def _pattern_binds(pattern):
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
    callee = value.func
    while isinstance(callee, (ast.Attribute, ast.Subscript)):
        callee = callee.value
    if isinstance(callee, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
        yield from _carried_parts(callee)


def _call_argument_parts(value):
    """Every value a call's arguments carry, starred forms included."""
    arguments = [*value.args,
                 *(keyword.value for keyword in value.keywords)]
    for argument in arguments:
        yield from _carried_parts(argument)


def _carries_launcher(parts, facts):
    """A launcher, or the module a receiver reads one from."""
    return any(_names_one_of(part, facts.subprocess_modules)
               or _is_launch_value(part, facts)
               for part in parts)


def _carries_launch_value(parts, facts):
    """A launcher itself. A bare module name is not one: the alias walk
    follows that wherever it is bound, and only the receiver position
    reads a launcher out of the module it is handed."""
    return any(_is_launch_value(part, facts) for part in parts)


def _invokes_what_it_is_given(node, facts):
    """Whether this callee is one the guard already reads as a launcher.

    A launcher handed to a callee that invokes what it is given is
    unfollowable: that callee decides when, where and with what. The same
    launcher handed to any other callee is only mentioned there —
    compared, used as a spec, looked up in a registry — and none of those
    launches anything, so refusing it would refuse correct code. The test
    is the callee's own name against the one set that says what a
    launcher is called here, not a list of the callees that do not.
    """
    function = node.func
    if isinstance(function, ast.Attribute):
        name = function.attr
    elif isinstance(function, ast.Name):
        name = function.id
    else:
        return False
    return (name in _LAUNCHERS
            or _names_one_of(function, facts.launch_callables))


def _unfollowable_launcher_bindings(tree, facts):
    """Lines binding or calling a launcher the alias walk cannot follow."""
    lines = []
    for node in memo_nodes(tree):
        for line, value in _bound_values(node, facts):
            if _carries_launcher(_carried_parts(value), facts):
                lines.append(line)
        if (isinstance(node, ast.Call)
                and not _has_cwd_control(node)
                and (_carries_launcher(_call_receiver_parts(node), facts)
                     or (_invokes_what_it_is_given(node, facts)
                         and _carries_launch_value(
                             _call_argument_parts(node), facts)))):
            lines.append(node.lineno)
    # One line carries one verdict: a call that also sits in a binding
    # position is reached by both arms, and the reader needs it said once.
    return list(dict.fromkeys(lines))
