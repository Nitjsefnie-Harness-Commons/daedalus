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


def _resolve_sender_name(expr, aliases):
    """The sender name ('ext_cmd' or '_ext_cmd') `expr` denotes -- directly,
    through an attribute access, or through one level of an alias already
    in `aliases` -- or None if it denotes neither."""
    if isinstance(expr, ast.Name):
        if expr.id in ('ext_cmd', '_ext_cmd'):
            return expr.id
        return aliases.get(expr.id)
    if isinstance(expr, ast.Attribute) and expr.attr in ('ext_cmd', '_ext_cmd'):
        return expr.attr
    return None


def _rebound_names(node):
    """Every local name `node` can rebind, in any binding form: a plain or
    annotated assignment, a `for`/`with`/`except` target, or an import."""
    nodes = [node, *_scope_nodes(node)]
    names = {n.id for n in nodes
             if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
    names |= {(a.asname or a.name).split('.')[0]
              for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom))
              for a in n.names}
    names |= {n.name for n in nodes
              if isinstance(n, ast.ExceptHandler) and n.name}
    return names


def _apply_alias_statement(node, aliases):
    """Track a same-scope alias of ext_cmd/_ext_cmd, in the same
    source-ordered pass the dict tracking below runs in, since an alias is
    exactly as order- and branch-sensitive as a payload dict is and can't
    be collected in a single whole-scope pass without going stale.

    Every name this statement can rebind is cleared first, in whatever
    form that rebinding takes, so a sender rebound through a `for` target
    or a `with ... as` (not just a plain assignment) stops reading as one
    too. Only a plain or annotated assignment can then re-establish an
    alias; resolving through `aliases` here, not just a literal name, is
    what makes `a = _ext_cmd` followed later by `b = a` recognize `b` too,
    one hop at a time, in source order.
    """
    for name in _rebound_names(node):
        aliases.pop(name, None)
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets = [node.target]
    else:
        return
    resolved = _resolve_sender_name(node.value, aliases)
    if resolved is not None:
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = resolved


def _py_call_violations(node, dicts, rel, allowed_opaque_names=frozenset(),
                        aliases=None):
    """Violations from one Call node, given the scope's tracked dicts.

    Only a bare-name callee ever consults `aliases`: `send()` can be judged
    against whatever `send` currently resolves to, but `sink.send()` names
    an attribute of `sink`, not the local `send`, and must not be
    reclassified just because some unrelated local happens to share that
    name.
    """
    func = node.func
    if isinstance(func, ast.Name):
        name = (aliases or {}).get(func.id, func.id)
    elif isinstance(func, ast.Attribute):
        name = func.attr
    else:
        name = ''
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


def _copy_state_pair(pair):
    """A (dict-state, alias-map) pair carries an alias map alongside its
    dict state through the same branch copies and merges, since a sender
    alias is exactly as flow-sensitive as a payload dict is."""
    state, aliases = pair
    return (_copy_dict_state(state), dict(aliases))


def _dedupe_state_pairs(pairs):
    """Collapse pairs whose (dict-state signature, alias map) is equivalent
    for this routing contract -- a call after the branch is still checked
    against every surviving pair, so a sender alias true on only one
    branch still gets caught, as long as two pairs whose alias maps
    actually differ are kept as two entries rather than folded into one
    by a signature that only looks at the dict-state half."""
    found = {}
    for state, aliases in pairs:
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
        found.setdefault(
            (tuple(signature), tuple(sorted(aliases.items()))),
            (state, aliases))
    return list(found.values())


def _py_calls_in(node):
    nodes = [node, *_scope_nodes(node)]
    return [child for child in nodes if isinstance(child, ast.Call)]


def _py_flow_violations(statements, pairs, rel, allowed_opaque_names):
    """Walk statements in order, retaining alternate `if` branch (dict-state,
    alias-map) pairs. Each pair advances together: a statement that isn't
    an `if` updates both halves of every pair in place, in the same source
    order, so an alias assignment and a dict assignment on the same line
    are both visible to whatever runs next."""
    violations = []

    def check_calls(node, current_pairs):
        for call in _py_calls_in(node):
            for state, aliases in current_pairs:
                violations.extend(_py_call_violations(
                    call, state, rel, allowed_opaque_names, aliases))

    for statement in statements:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
            # Not walked into, but its own name is still a binding: a def
            # or class that shadows an alias rebinds that name here just
            # as much as an assignment would, and skipping straight past
            # without clearing it left the alias reading as live under a
            # name that no longer denotes it.
            for _, aliases in pairs:
                aliases.pop(statement.name, None)
            continue
        if isinstance(statement, ast.If):
            check_calls(statement.test, pairs)
            incoming = [_copy_state_pair(pair) for pair in pairs]
            body, body_pairs = _py_flow_violations(
                statement.body,
                [_copy_state_pair(pair) for pair in incoming], rel,
                allowed_opaque_names)
            violations.extend(body)
            if statement.orelse:
                other, other_pairs = _py_flow_violations(
                    statement.orelse,
                    [_copy_state_pair(pair) for pair in incoming], rel,
                    allowed_opaque_names)
                violations.extend(other)
            else:
                other_pairs = incoming
            pairs = _dedupe_state_pairs([*body_pairs, *other_pairs])
            continue
        check_calls(statement, pairs)
        for state, aliases in pairs:
            _apply_dict_statement(statement, state)
            _apply_alias_statement(statement, aliases)
        if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
            return violations, []
    return violations, pairs


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
            statements, [({}, {})], rel, allowed_opaque)
        violations.extend(found)
    return list(dict.fromkeys(violations))
