"""Which key a Python client writes the routing field under.

Not a suite itself — run_tests.py only loads `test_*.py`.

This reads client source with the `ast` module and follows a payload dict
through the assignments, copies and updates that build it, so a call that
passes `tabId` where the bridge expects `tab` is found wherever the dict was
put together. A construction it cannot follow is reported as unprovable
rather than as clean.
"""
import ast


def _scope_nodes(scope):
    """Every node in `scope`, not descending into nested functions or lambdas:
    each function's locals are tracked as a separate scope."""
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _scope_nodes(child)


_OPAQUE_TAB_SPREAD = object()


def _merge_payload_keys(keys, spread, spread_node):
    """Apply one Python mapping spread to tracked payload keys.

    An unresolved spread makes the resulting `tab` value opaque. A later
    explicit `tab` write clears that uncertainty because dict construction is
    ordered and the later value wins.
    """
    if spread is None:
        keys[_OPAQUE_TAB_SPREAD] = (spread_node.lineno, spread_node)
        return
    if _OPAQUE_TAB_SPREAD in spread:
        keys[_OPAQUE_TAB_SPREAD] = (spread_node.lineno, spread_node)
    if 'tab' in spread:
        keys.pop(_OPAQUE_TAB_SPREAD, None)
    keys.update((key, value) for key, value in spread.items()
                if key is not _OPAQUE_TAB_SPREAD)


def payload_keys(expr, dicts):
    """Tracked string keys, or None for a wholly opaque expression.

    `**spread` of a tracked name merges, `dict(k=v, ...)` is the same mapping
    spelled as a call, and an unresolved spread records an opaque `tab` value
    instead of trusting the literal keys around it.
    """
    if isinstance(expr, ast.Name):
        return dicts.get(expr.id)
    if isinstance(expr, ast.Dict):
        keys = {}
        for k, v in zip(expr.keys, expr.values):
            if k is None:
                _merge_payload_keys(keys, payload_keys(v, dicts), v)
            elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                if k.value == 'tab':
                    keys.pop(_OPAQUE_TAB_SPREAD, None)
                keys[k.value] = (v.lineno, v)
        return keys
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
            and expr.func.id == 'dict'):
        if expr.args:
            return None                    # positional source is opaque
        keys = {}
        for kw in expr.keywords:
            if kw.arg is None:
                spread = payload_keys(kw.value, dicts)
                _merge_payload_keys(keys, spread, kw.value)
            else:
                if kw.arg == 'tab':
                    keys.pop(_OPAQUE_TAB_SPREAD, None)
                keys[kw.arg] = (kw.value.lineno, kw.value)
        return keys
    return None


def _update_keys(call, dicts):
    """The keys `d.update(...)` merges: the positional mapping when it
    resolves, plus keywords. None when any part is opaque."""
    keys = {}
    if call.args:
        merged = payload_keys(call.args[0], dicts)
        if merged is None:
            return None
        keys.update(merged)
    for kw in call.keywords:
        if kw.arg is None:
            return None                    # d.update(**opaque)
        if kw.arg == 'tab':
            keys.pop(_OPAQUE_TAB_SPREAD, None)
        keys[kw.arg] = (kw.value.lineno, kw.value)
    return keys


def _apply_dict_statement(node, dicts):
    """Apply one assignment or mapping mutation to a tracked Python state."""
    if isinstance(node, ast.AugAssign):          # d |= {...}
        if isinstance(node.op, ast.BitOr) and isinstance(node.target, ast.Name):
            merged = payload_keys(node.value, dicts)
            if merged is None:
                dicts.pop(node.target.id, None)
            else:
                _merge_payload_keys(
                    dicts.setdefault(node.target.id, {}), merged, node.value)
        return
    if isinstance(node, ast.Expr):               # d.update({...})
        call = node.value
        if (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == 'update'
                and isinstance(call.func.value, ast.Name)):
            merged = _update_keys(call, dicts)
            if merged is None:
                dicts.pop(call.func.value.id, None)
            else:
                _merge_payload_keys(
                    dicts.setdefault(call.func.value.id, {}), merged, call)
        return
    if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
        return
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            keys = payload_keys(node.value, dicts)
            if keys is None:
                dicts.pop(target.id, None)
            else:
                dicts[target.id] = keys
        elif (isinstance(target, ast.Subscript)
              and isinstance(target.value, ast.Name)
              and isinstance(target.slice, ast.Constant)
              and isinstance(target.slice.value, str)):
            if target.slice.value == 'tab':
                dicts.setdefault(target.value.id, {}).pop(
                    _OPAQUE_TAB_SPREAD, None)
            dicts.setdefault(target.value.id, {})[target.slice.value] = (
                node.value.lineno, node.value)


def dict_assignments(scope):
    """Map local names to their string keys: {name: {key: (lineno, value)}}.

    A literal `d = {...}` (annotated or not) or `d = dict(...)` resets the
    name; `d['k'] = v`, `d.update({...})` and `d |= {...}` add keys; last
    write wins, so `d = {'tab': 'extension'}` followed by `d['tab'] = tid`
    records `tid`. Only constant string keys are tracked. A wholly opaque
    rebinding or mutation (`d = f()`, `d.update(f())`, `d |= g()`) drops the
    name from tracking from that point rather than trusting keys it may have
    replaced; an unresolved `**` inside a dict retains an opaque-tab marker.
    """
    dicts = {}
    nodes = [n for n in _scope_nodes(scope)
             if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr))]
    for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset)):
        _apply_dict_statement(node, dicts)
    return dicts


def _is_extension_constant(node):
    return isinstance(node, ast.Constant) and node.value == 'extension'


def sender_aliases(scope):
    """Local names bound to `ext_cmd`/`_ext_cmd` by a plain same-scope
    assignment, e.g. `send = _ext_cmd` -- so a call through the alias is
    judged the same as a call through the name it was bound to.

    Only a bare `name = ext_cmd` (or `_ext_cmd`) counts. Anything less direct
    -- attribute access, a call, a conditional value -- leaves the name
    untracked, and a call through it still reads as an ordinary call rather
    than as a typed sender.
    """
    aliases = {}
    for node in _scope_nodes(scope):
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Name)
                and node.value.id in ('ext_cmd', '_ext_cmd')):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = node.value.id
    return aliases


def _py_call_violations(node, dicts, rel, allowed_opaque_names=frozenset(),
                        aliases=None):
    """Violations from one Call node, given the scope's tracked dicts."""
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else '')
    if aliases:
        name = aliases.get(name, name)
    if name in ('ext_cmd', '_ext_cmd'):
        found = []
        for kw in node.keywords:
            if kw.arg == 'tab' and not _is_extension_constant(kw.value):
                found.append(f'{rel}:{kw.value.lineno}: ext_cmd keyword `tab`')
            elif kw.arg is None:
                keys = payload_keys(kw.value, dicts)
                if keys is None or _OPAQUE_TAB_SPREAD in keys:
                    found.append(
                        f'{rel}:{kw.value.lineno}: opaque **'
                        f'{ast.unparse(kw.value)} passed to ext_cmd; `tab` '
                        'cannot be verified')
                elif 'tab' in keys:
                    lineno, value = keys['tab']
                    if not _is_extension_constant(value):
                        found.append(
                            f'{rel}:{lineno}: `tab` in '
                            f'**{ast.unparse(kw.value)} passed to ext_cmd')
        return found
    cmd_at = next((i for i, a in enumerate(node.args)
                   if isinstance(a, ast.Constant) and a.value == '/command'),
                  None)
    if cmd_at is None or cmd_at + 1 >= len(node.args):
        return []
    keys = payload_keys(node.args[cmd_at + 1], dicts)
    if keys and 'type' in keys and _OPAQUE_TAB_SPREAD in keys:
        lineno, spread = keys[_OPAQUE_TAB_SPREAD]
        allowed = (isinstance(spread, ast.Name)
                   and spread.id in allowed_opaque_names)
        if not allowed:
            return [f'{rel}:{lineno}: opaque spread may replace `tab` on a '
                    'typed /command payload']
    if keys and 'type' in keys and 'tab' in keys:
        lineno, value = keys['tab']
        if not _is_extension_constant(value):
            return [f'{rel}:{lineno}: `tab` on a typed /command payload']
    return []


def _copy_dict_state(dicts):
    return {name: keys.copy() for name, keys in dicts.items()}


def _dedupe_dict_states(states):
    """Collapse flow states that are equivalent for this routing contract."""
    found = {}
    for state in states:
        signature = []
        for name, keys in sorted(state.items()):
            if _OPAQUE_TAB_SPREAD in keys:
                tab = 'opaque'
            elif 'tab' not in keys:
                tab = 'absent'
            elif _is_extension_constant(keys['tab'][1]):
                tab = 'extension'
            else:
                tab = 'other'
            signature.append((name, 'type' in keys, tab))
        found.setdefault(tuple(signature), state)
    return list(found.values())


def _py_calls_in(node):
    nodes = [node, *_scope_nodes(node)]
    return [child for child in nodes if isinstance(child, ast.Call)]


def _py_flow_violations(statements, states, rel, allowed_opaque_names,
                        aliases=None):
    """Walk statements in order, retaining alternate `if` branch states."""
    violations = []

    def check_calls(node, current_states):
        for call in _py_calls_in(node):
            for state in current_states:
                violations.extend(_py_call_violations(
                    call, state, rel, allowed_opaque_names, aliases))

    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
            continue
        if isinstance(statement, ast.If):
            check_calls(statement.test, states)
            incoming = [_copy_dict_state(state) for state in states]
            body, body_states = _py_flow_violations(
                statement.body,
                [_copy_dict_state(state) for state in incoming], rel,
                allowed_opaque_names, aliases)
            violations.extend(body)
            if statement.orelse:
                other, other_states = _py_flow_violations(
                    statement.orelse,
                    [_copy_dict_state(state) for state in incoming], rel,
                    allowed_opaque_names, aliases)
                violations.extend(other)
            else:
                other_states = incoming
            states = _dedupe_dict_states([*body_states, *other_states])
            continue
        check_calls(statement, states)
        for state in states:
            _apply_dict_statement(statement, state)
        if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return violations, []
    return violations, states


def py_tab_routing_violations(path, rel):
    """`tab` set to a non-'extension' value on a typed command sent from `path`.

    Typed means: routed through ext_cmd/_ext_cmd (which themselves inject
    `tab: 'extension'`), or sent to /command carrying a `type` key. Eval
    payloads carry `code` instead of `type` and route BY tab legitimately —
    `_send_eval` sets `payload['tab'] = tab_id` and is correct — so they are
    exempt by structure, not by naming convention.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    violations = []
    scopes = [tree] + [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in scopes:
        statements = scope.body
        allowed_opaque = frozenset()
        if (isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
                and scope.name in ('ext_cmd', '_ext_cmd')
                and scope.args.kwarg is not None):
            allowed_opaque = frozenset({scope.args.kwarg.arg})
        found, _ = _py_flow_violations(
            statements, [{}], rel, allowed_opaque, sender_aliases(scope))
        violations.extend(found)
    return list(dict.fromkeys(violations))
