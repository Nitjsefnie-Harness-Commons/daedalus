"""Follow Python payloads and report unproved sender aliases."""
import ast


def _scope_nodes(scope):
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _scope_nodes(child)


_OPAQUE_TAB_SPREAD = object()
_UNPROVABLE_SENDER = '?ext_cmd'
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp,
                   ast.GeneratorExp)


def _merge_payload_keys(keys, spread, spread_node):
    """Apply a spread; a later explicit tab clears opaque uncertainty."""
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
    """Return tracked string keys, or None for a wholly opaque expression."""
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
    """Return keys merged by d.update, or None when any part is opaque."""
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
    """Map local names to string keys, retaining only provable mutations.

    Opaque rebinding drops a name; unresolved spreads retain an opaque marker.
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
    if isinstance(expr, ast.Name):
        if expr.id in ('ext_cmd', '_ext_cmd'):
            return expr.id
        return aliases.get(expr.id)
    if (isinstance(expr, ast.Attribute)
            and expr.attr in ('ext_cmd', '_ext_cmd')):
        return expr.attr
    for child in ast.walk(expr):
        if (isinstance(child, ast.Name)
                and (child.id in ('ext_cmd', '_ext_cmd')
                     or aliases.get(child.id) in (
                         'ext_cmd', '_ext_cmd', _UNPROVABLE_SENDER))):
            return _UNPROVABLE_SENDER
        if (isinstance(child, ast.Attribute)
                and child.attr in ('ext_cmd', '_ext_cmd')):
            return _UNPROVABLE_SENDER
        if (isinstance(child, ast.Constant)
                and child.value in ('ext_cmd', '_ext_cmd')):
            return _UNPROVABLE_SENDER
    return None


def _rebound_names(node):
    """Names rebound by `node`, excluding scoped comprehension targets."""
    nodes = [node, *_scope_nodes(node)]
    comprehension_targets = {
        target
        for parent in nodes if isinstance(parent, _COMPREHENSIONS)
        for generator in parent.generators
        for target in ast.walk(generator.target)
        if isinstance(target, ast.Name)
    }
    names = {n.id for n in nodes
             if isinstance(n, ast.Name)
             and n not in comprehension_targets
             and isinstance(n.ctx, (ast.Store, ast.Del))}
    names |= {(a.asname or a.name).split('.')[0]
              for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom))
              for a in n.names}
    names |= {n.name for n in nodes
              if isinstance(n, ast.ExceptHandler) and n.name}
    names |= {n.name for n in nodes
              if isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name}
    names |= {n.rest for n in nodes
              if isinstance(n, ast.MatchMapping) and n.rest}
    names |= {n.name for n in ast.walk(node)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef)) and n is not node}
    return names


def _apply_alias_statement(node, aliases):
    if isinstance(node, ast.ImportFrom):
        for imported in node.names:
            local = imported.asname or imported.name
            aliases.pop(local, None)
            if imported.name in ('ext_cmd', '_ext_cmd'):
                aliases[local] = imported.name
        return
    if isinstance(node, ast.Import):
        for imported in node.names:
            aliases.pop((imported.asname or imported.name).split('.')[0], None)
        return
    targets = (node.targets if isinstance(node, (ast.Assign, ast.Delete))
               else [node.target] if isinstance(
                   node, (ast.AnnAssign, ast.AugAssign)) else None)
    if targets is None:
        return
    bindings = {}
    if type(node) in (ast.Assign, ast.AnnAssign) and node.value is not None:
        for target in targets:
            _bind_alias_target(target, node.value, aliases, bindings)
    for name in set().union(*(_bound_names(target) for target in targets)):
        aliases.pop(name, None)
    aliases.update(bindings)


def _bind_alias_target(target, value, aliases, bindings):
    if isinstance(target, ast.Name):
        resolved = _resolve_sender_name(value, aliases)
        if resolved is not None:
            bindings[target.id] = resolved
        return
    if not isinstance(target, (ast.Tuple, ast.List)):
        return
    pairs = None
    if isinstance(value, (ast.Tuple, ast.List)):
        stars = [i for i, item in enumerate(target.elts)
                 if isinstance(item, ast.Starred)]
        if not stars and len(target.elts) == len(value.elts):
            pairs = zip(target.elts, value.elts)
        elif len(stars) == 1 and len(value.elts) >= len(target.elts) - 1:
            star, suffix = stars[0], len(target.elts) - stars[0] - 1
            pairs = [*zip(target.elts[:star], value.elts[:star]),
                     *(zip(target.elts[-suffix:], value.elts[-suffix:])
                       if suffix else ())]
    if pairs is not None:
        for nested_target, nested_value in pairs:
            _bind_alias_target(nested_target, nested_value, aliases, bindings)
        return
    if _resolve_sender_name(value, aliases) is not None:
        for name in _bound_names(target):
            bindings[name] = _UNPROVABLE_SENDER


def _py_call_violations(node, dicts, rel, allowed_opaque_names=frozenset(),
                        sender_name=None):
    func = node.func
    if sender_name == _UNPROVABLE_SENDER:
        callee = func.id if isinstance(func, ast.Name) else ast.unparse(func)
        found = []
        for kw in node.keywords:
            if kw.arg == 'tab' and not _is_extension_constant(kw.value):
                found.append(f'{rel}:{kw.value.lineno}: `tab` passed through '
                             f'`{callee}`, which may be ext_cmd')
            elif kw.arg is None:
                keys = payload_keys(kw.value, dicts)
                if (keys and 'tab' in keys
                        and not _is_extension_constant(keys['tab'][1])):
                    lineno, _ = keys['tab']
                    found.append(f'{rel}:{lineno}: `tab` passed through '
                                 f'`{callee}`, which may be ext_cmd')
        return found
    if sender_name in ('ext_cmd', '_ext_cmd'):
        found = []
        for kw in node.keywords:
            if kw.arg == 'tab' and not _is_extension_constant(kw.value):
                found.append(f'{rel}:{kw.value.lineno}: ext_cmd keyword `tab`')
            elif kw.arg is None:
                keys = payload_keys(kw.value, dicts)
                if keys is None or _OPAQUE_TAB_SPREAD in keys:
                    found.append(f'{rel}:{kw.value.lineno}: opaque **'
                                 f'{ast.unparse(kw.value)} passed to ext_cmd; '
                                 '`tab` cannot be verified')
                elif 'tab' in keys:
                    lineno, value = keys['tab']
                    if not _is_extension_constant(value):
                        found.append(f'{rel}:{lineno}: `tab` in **'
                                     f'{ast.unparse(kw.value)} passed to '
                                     'ext_cmd')
        return found
    cmd_at = next((i for i, a in enumerate(node.args)
                   if isinstance(a, ast.Constant) and a.value == '/command'),
                  None)
    if cmd_at is None or cmd_at + 1 >= len(node.args):
        return []
    keys = payload_keys(node.args[cmd_at + 1], dicts)
    if keys and 'type' in keys and _OPAQUE_TAB_SPREAD in keys:
        lineno, spread = keys[_OPAQUE_TAB_SPREAD]
        allowed = getattr(spread, 'id', None) in allowed_opaque_names
        if not allowed:
            return [f'{rel}:{lineno}: opaque spread may replace `tab` on a '
                    'typed /command payload']
    if keys and 'type' in keys and 'tab' in keys:
        lineno, value = keys['tab']
        if not _is_extension_constant(value):
            return [f'{rel}:{lineno}: `tab` on a typed /command payload']
    return []


def _copy_state_pair(pair):
    return ({n: v.copy() for n, v in pair[0].items()}, dict(pair[1]))


def _state_pair_signature(pair):
    state, aliases = pair
    signature = []
    for name, keys in sorted(state.items()):
        tab = ('opaque' if _OPAQUE_TAB_SPREAD in keys else 'absent'
               if 'tab' not in keys else 'extension'
               if _is_extension_constant(keys['tab'][1]) else 'other')
        signature.append((name, 'type' in keys, tab))
    return (tuple(signature), tuple(sorted(aliases.items())))


def _dedupe_state_pairs(pairs):
    found = {}
    for pair in pairs:
        found.setdefault(_state_pair_signature(pair), pair)
    return list(found.values())


def _inherited_aliases(pairs):
    inherited = {}
    names = {name for _, aliases in pairs for name in aliases}
    for name in names:
        values = [aliases.get(name) for _, aliases in pairs]
        first = values[0]
        if first is not None and all(value == first for value in values):
            inherited[name] = first
        elif any(value is not None for value in values):
            inherited[name] = _UNPROVABLE_SENDER
    return inherited


def _function_aliases(node, pairs):
    aliases = _inherited_aliases(pairs)
    positional = [*node.args.posonlyargs, *node.args.args]
    parameters = [*positional, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        parameters.append(node.args.vararg)
    if node.args.kwarg is not None:
        parameters.append(node.args.kwarg)
    for parameter in parameters:
        aliases.pop(parameter.arg, None)
    defaults = zip(positional[-len(node.args.defaults):], node.args.defaults)
    if not node.args.defaults:
        defaults = ()
    for parameter, default in [*defaults, *zip(
            node.args.kwonlyargs, node.args.kw_defaults)]:
        if default is None:
            continue
        resolved = _resolve_sender_name(default, _inherited_aliases(pairs))
        if resolved is not None:
            aliases[parameter.arg] = resolved
    return aliases


def _function_allowed_opaque(node):
    return (frozenset({node.args.kwarg.arg})
            if node.name in ('ext_cmd', '_ext_cmd')
            and node.args.kwarg is not None else frozenset())


def _class_functions(node):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield child
        else:
            yield from _class_functions(child)


def _bound_names(target):
    return {node.id for node in ast.walk(target)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))}


def _clear_names(pairs, names):
    for state, aliases in pairs:
        for name in names:
            state.pop(name, None)
            aliases.pop(name, None)


def _new_exits():
    return {'break': [], 'continue': [], 'terminal': []}


def _record_exit(exits, kind, pairs):
    if exits is not None:
        exits[kind].extend(_copy_state_pair(pair) for pair in pairs)


def _argument_defaults(args):
    return [*args.defaults,
            *(default for default in args.kw_defaults if default is not None)]


def _literal_iterable_nonempty(expr):
    if not isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        return None
    states = [_literal_iterable_nonempty(item.value)
              if isinstance(item, ast.Starred) else True for item in expr.elts]
    return True if True in states else None if None in states else False


def _py_flow_violations(statements, pairs, rel, allowed_opaque_names,
                        flow_exits=None):
    violations = []

    def copied(found):
        return [_copy_state_pair(pair) for pair in found]

    def check_expression(node, current_pairs):
        if node is None:
            return current_pairs
        if isinstance(node, ast.Lambda):
            for default in _argument_defaults(node.args):
                current_pairs = check_expression(default, current_pairs)
            return current_pairs
        if isinstance(node, ast.NamedExpr):
            current_pairs = check_expression(node.value, current_pairs)
            for _, aliases in current_pairs:
                bindings = {}
                _bind_alias_target(node.target, node.value, aliases, bindings)
                aliases.pop(node.target.id, None)
                aliases.update(bindings)
            return current_pairs
        if isinstance(node, ast.BoolOp):
            current_pairs = check_expression(node.values[0], current_pairs)
            for value in node.values[1:]:
                current_pairs = _dedupe_state_pairs([
                    *copied(current_pairs),
                    *check_expression(value, copied(current_pairs))])
            return current_pairs
        if isinstance(node, ast.IfExp):
            tested = check_expression(node.test, current_pairs)
            body = check_expression(node.body, copied(tested))
            other = check_expression(node.orelse, copied(tested))
            return _dedupe_state_pairs([*body, *other])
        if isinstance(node, ast.Compare):
            active = check_expression(node.left, current_pairs)
            finished = []
            for comparator in node.comparators:
                active = check_expression(comparator, active)
                finished.extend(copied(active))
            return _dedupe_state_pairs([*finished, *active])
        if isinstance(node, ast.Call):
            callees = check_expression(node.func, current_pairs)
            arguments = sorted([*node.args,
                                *(kw.value for kw in node.keywords)],
                               key=lambda n: (n.lineno, n.col_offset))
            outputs = []
            for state, aliases in callees:
                sender = aliases.get(node.func.target.id) if isinstance(
                    node.func, ast.NamedExpr) else _resolve_sender_name(
                        node.func, aliases)
                current = [(state, aliases)]
                for argument in arguments:
                    current = check_expression(argument, current)
                for current_state, _ in current:
                    found = _py_call_violations(
                        node, current_state, rel, allowed_opaque_names, sender)
                    violations.extend(found)
                outputs.extend(current)
            return _dedupe_state_pairs(outputs)
        if isinstance(node, _COMPREHENSIONS):
            entered = check_expression(node.generators[0].iter, current_pairs)
            active = copied(entered)
            may_skip = False
            for index, generator in enumerate(node.generators):
                if index:
                    active = check_expression(generator.iter, active)
                cardinality = _literal_iterable_nonempty(generator.iter)
                if cardinality is False:
                    skipped = entered if may_skip else []
                    return _dedupe_state_pairs([*skipped, *active])
                for condition in generator.ifs:
                    active = check_expression(condition, active)
                may_skip |= bool(generator.ifs) or cardinality is not True
            results = [node.key, node.value] if isinstance(
                node, ast.DictComp) else [node.elt]
            for result in results:
                active = check_expression(result, active)
            if isinstance(node, ast.GeneratorExp):
                return entered
            skipped = entered if may_skip else []
            return _dedupe_state_pairs([*skipped, *active])
        if isinstance(node, ast.Dict):
            children = [child for item in zip(node.keys, node.values)
                        for child in item if child is not None]
        else:
            children = ast.iter_child_nodes(node)
        for child in children:
            current_pairs = check_expression(child, current_pairs)
        return current_pairs

    def walk(parts, current_pairs, exits=flow_exits):
        return _py_flow_violations(
            parts, current_pairs, rel, allowed_opaque_names, exits)

    for statement in statements:  # pylint: disable=too-many-nested-blocks
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            values = [*statement.decorator_list,
                      *_argument_defaults(statement.args)]
            for value in values:
                pairs = check_expression(value, pairs)
            for _, aliases in pairs:
                aliases.pop(statement.name, None)
            nested, _ = _py_flow_violations(
                statement.body,
                [({}, _function_aliases(statement, pairs))], rel,
                _function_allowed_opaque(statement))
            violations.extend(nested)
            continue
        if isinstance(statement, ast.ClassDef):
            for _, aliases in pairs:
                aliases.pop(statement.name, None)
            nested, _ = _py_flow_violations(
                list(_class_functions(statement)),
                [_copy_state_pair(pair) for pair in pairs], rel,
                allowed_opaque_names)
            violations.extend(nested)
            continue
        if isinstance(statement, ast.If):
            pairs = check_expression(statement.test, pairs)
            incoming = [_copy_state_pair(pair) for pair in pairs]
            body, body_pairs = walk(
                statement.body,
                [_copy_state_pair(pair) for pair in incoming])
            violations.extend(body)
            if statement.orelse:
                other, other_pairs = walk(
                    statement.orelse,
                    [_copy_state_pair(pair) for pair in incoming])
                violations.extend(other)
            else:
                other_pairs = incoming
            pairs = _dedupe_state_pairs([*body_pairs, *other_pairs])
            continue
        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            header = (statement.iter if isinstance(
                statement, (ast.For, ast.AsyncFor)) else statement.test)
            pairs = check_expression(header, pairs)
            incoming = [_copy_state_pair(pair) for pair in pairs]
            zero_pairs = incoming
            if (isinstance(statement, (ast.For, ast.AsyncFor))
                    and _literal_iterable_nonempty(statement.iter) is True):
                zero_pairs = []
            target_names = (_bound_names(statement.target)
                            if isinstance(statement, (ast.For, ast.AsyncFor))
                            else set())
            iteration_pairs = incoming
            post_body = []
            break_pairs = []
            previous = frozenset()
            for _ in range(4):
                entry = [_copy_state_pair(pair) for pair in iteration_pairs]
                _clear_names(entry, target_names)
                exits = _new_exits()
                found, fallthrough = walk(statement.body, entry, exits)
                violations.extend(found)
                _record_exit(flow_exits, 'terminal', exits['terminal'])
                break_pairs.extend(exits['break'])
                next_pairs = _dedupe_state_pairs(
                    [*fallthrough, *exits['continue']])
                post_body = _dedupe_state_pairs(
                    [*post_body, *next_pairs])
                iteration_pairs = _dedupe_state_pairs(
                    [*incoming, *post_body])
                signatures = frozenset(
                    _state_pair_signature(pair) for pair in iteration_pairs)
                if signatures == previous:
                    break
                previous = signatures
            pairs = _dedupe_state_pairs(
                [*zero_pairs, *post_body, *break_pairs])
            if statement.orelse:
                found, pairs = walk(statement.orelse, pairs)
                violations.extend(found)
            continue
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            entered = [_copy_state_pair(pair) for pair in pairs]
            for item in statement.items:
                entered = check_expression(item.context_expr, entered)
                if item.optional_vars is None:
                    continue
                names = _bound_names(item.optional_vars)
                for state, aliases in entered:
                    resolved = _resolve_sender_name(
                        item.context_expr, aliases)
                    for name in names:
                        state.pop(name, None)
                        aliases.pop(name, None)
                        if resolved is not None:
                            aliases[name] = resolved
            found, pairs = walk(statement.body, entered)
            violations.extend(found)
            continue
        if isinstance(statement, (ast.Try, ast.TryStar)):
            incoming = [_copy_state_pair(pair) for pair in pairs]
            body_pairs = [_copy_state_pair(pair) for pair in incoming]
            intermediate = []
            exits = _new_exits()
            for body_statement in statement.body:
                found, body_pairs = walk(
                    [body_statement], body_pairs, exits)
                violations.extend(found)
                intermediate.extend(
                    _copy_state_pair(pair) for pair in body_pairs)
                if not body_pairs:
                    break
            if statement.orelse:
                found, normal_pairs = walk(
                    statement.orelse, body_pairs, exits)
                violations.extend(found)
            else:
                normal_pairs = body_pairs
            handler_pairs = []
            handler_entry = _dedupe_state_pairs(
                [*incoming, *intermediate])
            for handler in statement.handlers:
                entered = [_copy_state_pair(pair)
                           for pair in handler_entry]
                if handler.type is not None:
                    entered = check_expression(handler.type, entered)
                if handler.name:
                    _clear_names(entered, {handler.name})
                found, handled = walk(handler.body, entered, exits)
                violations.extend(found)
                handler_pairs.extend(handled)
            normal_pairs = _dedupe_state_pairs(
                [*normal_pairs, *handler_pairs])
            if statement.finalbody:
                found, normal_pairs = walk(
                    statement.finalbody, normal_pairs, flow_exits)
                violations.extend(found)
                for kind in ('break', 'continue', 'terminal'):
                    final_exits = _new_exits()
                    found, fallthrough = walk(
                        statement.finalbody, exits[kind], final_exits)
                    violations.extend(found)
                    _record_exit(flow_exits, kind, fallthrough)
                    for final_kind in ('break', 'continue', 'terminal'):
                        _record_exit(
                            flow_exits, final_kind,
                            final_exits[final_kind])
            else:
                for kind in ('break', 'continue', 'terminal'):
                    _record_exit(flow_exits, kind, exits[kind])
            pairs = normal_pairs
            continue
        if isinstance(statement, ast.Match):
            pairs = check_expression(statement.subject, pairs)
            incoming = [_copy_state_pair(pair) for pair in pairs]
            case_pairs = []
            for case in statement.cases:
                entered = [_copy_state_pair(pair) for pair in incoming]
                _clear_names(entered, _rebound_names(case.pattern))
                if case.guard is not None:
                    entered = check_expression(case.guard, entered)
                found, matched = walk(case.body, entered)
                violations.extend(found)
                case_pairs.extend(matched)
            pairs = _dedupe_state_pairs([*incoming, *case_pairs])
            continue
        pairs = check_expression(statement, pairs)
        for state, aliases in pairs:
            _apply_dict_statement(statement, state)
            _apply_alias_statement(statement, aliases)
        if isinstance(statement, (ast.Return, ast.Raise)):
            _record_exit(flow_exits, 'terminal', pairs)
            pairs = []
            continue
        if isinstance(statement, ast.Break):
            _record_exit(flow_exits, 'break', pairs)
            pairs = []
            continue
        if isinstance(statement, ast.Continue):
            _record_exit(flow_exits, 'continue', pairs)
            pairs = []
            continue
    return violations, pairs


def py_tab_routing_violations(path, rel):
    """`tab` set to a non-'extension' value on a typed command sent from `path`.

    Typed means routed through ext_cmd/_ext_cmd or sent to /command with a
    `type` key. Eval payloads carry `code` and legitimately route by tab.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    violations, _ = _py_flow_violations(
        tree.body, [({}, {})], rel, frozenset())
    return list(dict.fromkeys(violations))
