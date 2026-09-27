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

# The value-bearing fields of every form the walk opens, keyed by the
# form; `_carried_parts`' docstring is the boundary this table states.
# Three entries carry a reason the rule does not give them on its own: a
# comprehension reaches its conditions through the statement-level node
# its generators hold, and not through its own iterable, which
# `_bound_values` judges as the comprehension arm's own business; a dict
# comprehension opens its key as well as its value; and an interpolation
# is opened on requirement rather than on the value-preserving argument,
# because `f"{launcher}"` binds a string and the issue still asks for it
# to be judged.
_CARRIED_FIELDS = {
    ast.Await: ('value',),
    ast.BoolOp: ('values',),
    ast.DictComp: ('key', 'value', 'generators'),
    ast.FormattedValue: ('value', 'format_spec'),
    ast.GeneratorExp: ('elt', 'generators'),
    ast.IfExp: ('test', 'body', 'orelse'),
    ast.JoinedStr: ('values',),
    ast.Lambda: ('body',),
    ast.ListComp: ('elt', 'generators'),
    ast.NamedExpr: ('value',),
    ast.SetComp: ('elt', 'generators'),
    ast.Slice: ('lower', 'upper', 'step'),
    ast.Yield: ('value',),
    ast.YieldFrom: ('value',),
    # Statement-level, so a comprehension's conditions are the one place
    # the walk leaves the expression forms and enters a sibling of them.
    ast.comprehension: ('ifs',),
}

# PEP 750's template-string forms are a 3.14 addition, so a literal naming
# them raises at import on 3.11-3.13 and takes every suite with it. Read
# each the way tests/_helper_binds.py reads `ast.TypeAlias`: absent, the
# form is not registered, and that is correct rather than merely quiet --
# an older parser can neither produce the node nor parse the `t"..."`
# that makes one. Only the value-bearing fields are named, because
# `_field_parts` hands back whatever is not None and `str` and
# `conversion` are a str and an int, which the walk would refuse.
_TEMPLATE_FIELDS = {
    getattr(ast, 'TemplateStr', None): ('values',),
    getattr(ast, 'Interpolation', None): ('value', 'format_spec'),
}
_CARRIED_FIELDS.update({form: fields for form, fields
                        in _TEMPLATE_FIELDS.items() if form is not None})

# The atoms `_names_one_of` and `_is_launch_value` judge, and the forms
# that build a new value out of their operands, where a launcher is
# transformed rather than carried and opening one would only manufacture
# refusals. The two are leaves together and are told apart wherever the
# distinction decides something — a receiver position reads a launch
# method off what it carries, so only an atom is excluded there.
_ATOMS = (ast.Name, ast.Attribute, ast.Constant)
_TRANSFORMED = (ast.BinOp, ast.UnaryOp, ast.Compare)
_LEAVES = _ATOMS + _TRANSFORMED

# What a form in neither class yields, so every arm refuses it.
_UNRECOGNISED = object()

# The two positions a bound value can sit in, which read differently.
_BIND = 'bind'
_TARGET = 'target'


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
    """Every (statement line, value, position) the statement binds unreadably.

    `position` says what the statement does with the value. `bind` is a
    value it carries somewhere — a default, a decorator, a match subject
    — and `target` is a name it binds, which reads differently: a target
    may shadow a name that already spells a module rather than carry a
    launcher, and the Assign arm exempts exactly that shape.
    """
    if isinstance(node, ast.Assign):
        if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and (_names_one_of(node.value, facts.subprocess_modules)
                     or _is_launch_value(node.value, facts))):
            return []
        return [(node.lineno, node.value, _BIND)]
    if isinstance(node, ast.AnnAssign):
        if node.value is None:
            return []
        if (isinstance(node.target, ast.Name)
                and (_names_one_of(node.value, facts.subprocess_modules)
                     or _is_launch_value(node.value, facts))):
            return []
        return [(node.lineno, node.value, _BIND)]
    if isinstance(node, ast.AugAssign):
        return [(node.lineno, node.value, _BIND)]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [(node.lineno, node.iter, _BIND),
                (node.lineno, node.target, _TARGET)]
    if isinstance(node, ast.comprehension):
        return [(node.target.lineno, node.iter, _BIND),
                (node.target.lineno, node.target, _TARGET)]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [(node.lineno, part, position)
                for item in node.items if item.optional_vars is not None
                for part, position in ((item.context_expr, _BIND),
                                       (item.optional_vars,
                                        _TARGET))]
    if isinstance(node, ast.NamedExpr):
        return [(node.lineno, node.value, _BIND)]
    if isinstance(node, _HEADER_FORMS):
        return [(value.lineno, value, _BIND)
                for value in _header_values(node)]
    if isinstance(node, ast.Match) and any(
            _pattern_binds(case.pattern) for case in node.cases):
        return [(node.lineno, node.subject, _BIND)]
    return []


def _target_parts(target):
    """What a binding target carries, with the names it binds left out.

    A target is an assignment target, so its own `Name` — and every name
    inside a `Starred`, `Tuple` or `List` form of it — is a name being
    bound, and one that already spells a module shadows the name rather
    than carrying a launcher. That is the exemption the Assign arm
    makes for the same shape, and it is why the two positions are not
    the same test. What the target reaches *through* is carrying.
    A subscript's index is not exempt, so `d[subprocess]` binds a
    launcher and `d[key]` does not.
    """
    if isinstance(target, ast.Name):
        return []
    if isinstance(target, ast.Starred):
        return _target_parts(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return [part for item in target.elts
                for part in _target_parts(item)]
    return list(_carried_parts(target))


def _pattern_binds(pattern):
    return any(
        isinstance(part, (ast.MatchAs, ast.MatchStar)) and part.name
        or isinstance(part, ast.MatchMapping) and part.rest
        for part in ast.walk(pattern)
    )


def _carried_parts(value):
    """
    Carried elements and arguments, including a call-based callee.

    The walk is total over `ast.expr`, and it puts every form of it in one of
    three classes.

    It opens Await, BoolOp, Call, Dict, DictComp, FormattedValue, GeneratorExp,
    IfExp, JoinedStr, Lambda, List, ListComp, NamedExpr, Set, SetComp, Slice,
    Starred, Subscript, Tuple, Yield, YieldFrom, comprehension.

    It leaves Attribute, BinOp, Compare, Constant, Name, UnaryOp.

    A form in neither class is refused rather than read as clean, so a Python
    that adds one fails closed instead.

    A form is opened because a sub-value arrives as it was written, and left
    alone because the form builds a new value out of what it is handed, so a
    launcher inside one is transformed rather than carried.

    Two entries carry a reason the rule does not give them. A dict
    comprehension opens its key as well as its value.

    The four comprehension forms reach their conditions through the
    statement-level node their `generators` hold, and not through their own
    iterable, which `_bound_values` judges as the comprehension arm's own
    business.

    And FormattedValue is opened on requirement rather than on that argument,
    because `f"{launcher}"` binds a string and not the launcher, and the issue
    asks for the interpolation to be judged all the same.

    Its successors carry the same ground. TemplateStr and Interpolation are
    the 3.14 template-string forms, opened because the issue asks for an
    f-string's successor judged too, and registered only where the
    interpreter has them: an older parser can neither produce the node nor
    parse the `t"..."` that makes one, so there is nothing there for the
    walk to open.
    """
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
    elif type(value) in _CARRIED_FIELDS:
        for field in _CARRIED_FIELDS[type(value)]:
            for part in _field_parts(getattr(value, field)):
                yield from _carried_parts(part)
    elif isinstance(value, _LEAVES):
        yield value
    else:
        yield _UNRECOGNISED


def _field_parts(field):
    """The nodes one named field holds, a list of them or a single one."""
    if isinstance(field, list):
        yield from field
    elif field is not None:
        yield field


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
    """Every sub-value a call's callee carries, bar the callable itself.

    The descent consumes an attribute chain and every subscript on the
    way down, and each subscript it consumes is itself a sub-value: its
    index and bounds are handed to the walk as it goes, not left behind
    at the base. What the descent lands on is handed over only when it
    is not an atom, because an atom is the name or attribute the other
    arms already read and the receiver position reads a launch method
    off what it carries — handing one over would find a bare module name
    and refuse a direct launch.
    """
    callee = value.func
    while isinstance(callee, (ast.Attribute, ast.Subscript)):
        if isinstance(callee, ast.Subscript):
            yield from _carried_parts(callee.slice)
        callee = callee.value
    if not isinstance(callee, _ATOMS):
        yield from _carried_parts(callee)


def _call_argument_parts(value):
    """Every value a call's arguments carry, starred forms included."""
    arguments = [*value.args,
                 *(keyword.value for keyword in value.keywords)]
    for argument in arguments:
        yield from _carried_parts(argument)


def _carries_launcher(parts, facts):
    """A launcher, the module a receiver reads one from, or a form the
    walk does not read."""
    return any(part is _UNRECOGNISED
               or _names_one_of(part, facts.subprocess_modules)
               or _is_launch_value(part, facts)
               for part in parts)


def _carries_launch_value(parts, facts):
    """A launcher: an attribute that names one, or a name bound to one,
    or a form the walk does not read.

    A bare `subprocess` module name is not one, and that is the whole of
    the difference from `_carries_launcher`. The two positions are read
    differently because they mean different things here, not because
    anything downstream differs: the receiver position is where a launch
    method is read off what is carried — `{'sp': subprocess}['sp'].run`
    calls `.run` on the module — so a module there is a launcher, while an
    argument position carries no such read and a module there is a value
    the walk has no launcher to lose.
    """
    return any(part is _UNRECOGNISED
               or _is_launch_value(part, facts)
               for part in parts)


def _unfollowable_launcher_bindings(tree, facts):
    """Lines binding or calling a launcher the alias walk cannot follow."""
    lines = []
    for node in memo_nodes(tree):
        for line, value, position in _bound_values(node, facts):
            parts = (_target_parts(value) if position == _TARGET
                     else _carried_parts(value))
            if _carries_launcher(parts, facts):
                lines.append(line)
        if (isinstance(node, ast.Call)
                and not _has_cwd_control(node)
                and (_carries_launcher(_call_receiver_parts(node), facts)
                     or _carries_launch_value(_call_argument_parts(node),
                                              facts))):
            lines.append(node.lineno)
    # One line carries one verdict: a call that also sits in a binding
    # position is reached by both arms, and the reader needs it said once.
    return list(dict.fromkeys(lines))
