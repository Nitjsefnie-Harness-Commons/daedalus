"""Follow Python payloads and report unproved sender aliases."""
import ast

from _pyroute_state import (BUILTIN_CONSUMERS as _BUILTIN_CONSUMERS,
                            COMPREHENSIONS as _COMPREHENSIONS,
                            OPAQUE_TAB_SPREAD as _OPAQUE_TAB_SPREAD,
                            UNPROVABLE_SENDER as _UNPROVABLE_SENDER,
                            FlowState, apply_alias_statement,
                            argument_defaults, bind_alias_target, bound_names,
                            callable_state, class_functions, clear_names,
                            dedupe_states, function_allowed_opaque,
                            is_extension_constant, literal_iterable_nonempty,
                            new_exits, rebound_names, record_exit,
                            resolve_sender_name, scope_nodes, state_signature)

_apply_alias_statement = apply_alias_statement
_argument_defaults = argument_defaults
_bind_alias_target = bind_alias_target
_bound_names = bound_names
_callable_state = callable_state
_class_functions = class_functions
_clear_names = clear_names
_copy_state_pair = FlowState.copy
_dedupe_state_pairs = dedupe_states
_function_allowed_opaque = function_allowed_opaque
_is_extension_constant = is_extension_constant
_new_exits = new_exits
_rebound_names = rebound_names
_record_exit = record_exit
_resolve_sender_name = resolve_sender_name
_scope_nodes = scope_nodes
_state_pair_signature = state_signature
_EAGER_ITERABLE_CALLS = frozenset({
    'dict', 'frozenset', 'list', 'max', 'min', 'set', 'sorted', 'sum', 'tuple',
})
_PARTIAL_ITERABLE_CALLS = frozenset({'all', 'any', 'next'})


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


def _py_flow_violations(statements, pairs, rel, allowed_opaque_names,
                        flow_exits=None):
    violations = []

    def copied(found):
        return [_copy_state_pair(pair) for pair in found]

    def expression_value(node, state):
        if isinstance(node, ast.GeneratorExp):
            return node
        if isinstance(node, ast.Name) and node.id in state.generators:
            return state.generators[node.id]
        if isinstance(node, (ast.NamedExpr, ast.Starred)):
            child = node.value
            if id(child) in state.evaluated:
                return state.evaluated[id(child)]
        if isinstance(node, (ast.Tuple, ast.List)):
            values = [state.evaluated.get(id(item)) for item in node.elts]
            if any(value is not None
                   and not isinstance(value, ast.GeneratorExp)
                   for value in values):
                return _UNPROVABLE_SENDER
        return _resolve_sender_name(node, state.aliases)

    def remember(node, current_pairs):
        for state in current_pairs:
            state.evaluated[id(node)] = expression_value(node, state)
        return current_pairs

    def generator_for(expr, state):
        if isinstance(expr, ast.GeneratorExp):
            return expr
        if isinstance(expr, ast.Name):
            return state.generators.get(expr.id)
        value = state.evaluated.get(id(expr))
        return value if isinstance(value, ast.GeneratorExp) else None

    def generator_nonempty(generator):
        states = [literal_iterable_nonempty(clause.iter)
                  for clause in generator.generators]
        if False in states:
            return False
        if all(state is True for state in states) and not any(
                clause.ifs for clause in generator.generators):
            return True
        return None

    def iterable_nonempty(expr, current_pairs):
        states = []
        for state in current_pairs:
            generator = generator_for(expr, state)
            states.append(generator_nonempty(generator) if generator
                          else literal_iterable_nonempty(expr))
        return states[0] if states and all(
            value is states[0] for value in states) else None

    def consume_generator(generator, current_pairs):
        entered = copied(current_pairs)
        active = copied(current_pairs)
        may_skip = False
        for index, clause in enumerate(generator.generators):
            if index:
                active = check_expression(clause.iter, active)
            cardinality = iterable_nonempty(clause.iter, active)
            active = consume_iterable(clause.iter, active, exhaust=True)
            if cardinality is False:
                skipped = entered if may_skip else []
                return _dedupe_state_pairs([*skipped, *active])
            for condition in clause.ifs:
                active = check_expression(condition, active)
            may_skip |= bool(clause.ifs) or cardinality is not True
        results = [generator.key, generator.value] if isinstance(
            generator, ast.DictComp) else [generator.elt]
        for result in results:
            active = check_expression(result, active)
        skipped = entered if may_skip else []
        return _dedupe_state_pairs([*skipped, *active])

    def consume_iterable(expr, current_pairs, exhaust):
        outputs = []
        for state in current_pairs:
            generator = generator_for(expr, state)
            if generator is None:
                outputs.append(state)
                continue
            consumed = consume_generator(generator, [state])
            if exhaust:
                for output in consumed:
                    output.generators = {
                        name: value
                        for name, value in output.generators.items()
                        if value is not generator}
            outputs.extend(consumed)
        return _dedupe_state_pairs(outputs)

    def exhaust_generators(generators, current_pairs):
        for state in current_pairs:
            state.generators = {
                name: value for name, value in state.generators.items()
                if value not in generators}
        return _dedupe_state_pairs(current_pairs)

    def check_expression(node, current_pairs):
        if node is None:
            return current_pairs
        if isinstance(node, ast.Lambda):
            defaults = _argument_defaults(node.args)
            for default in defaults:
                current_pairs = check_expression(default, current_pairs)
            if defaults:
                nested = [_callable_state(node.args, current_pairs)]
                check_expression(node.body, nested)
            return remember(node, current_pairs)
        if isinstance(node, ast.NamedExpr):
            current_pairs = check_expression(node.value, current_pairs)
            for state in current_pairs:
                bindings = ({}, {})
                _bind_alias_target(node.target, node.value, state, bindings)
                state.aliases.pop(node.target.id, None)
                state.generators.pop(node.target.id, None)
                state.builtin_shadows.update(
                    {node.target.id} & _BUILTIN_CONSUMERS)
                state.aliases.update(bindings[0])
                state.generators.update(bindings[1])
            return remember(node, current_pairs)
        if isinstance(node, ast.BoolOp):
            current_pairs = check_expression(node.values[0], current_pairs)
            for value in node.values[1:]:
                current_pairs = _dedupe_state_pairs([
                    *copied(current_pairs),
                    *check_expression(value, copied(current_pairs))])
            return remember(node, current_pairs)
        if isinstance(node, ast.IfExp):
            tested = check_expression(node.test, current_pairs)
            body = check_expression(node.body, copied(tested))
            other = check_expression(node.orelse, copied(tested))
            return remember(node, _dedupe_state_pairs([*body, *other]))
        if isinstance(node, ast.Compare):
            active = check_expression(node.left, current_pairs)
            finished = []
            for comparator in node.comparators:
                active = check_expression(comparator, active)
                finished.extend(copied(active))
            return remember(
                node, _dedupe_state_pairs([*finished, *active]))
        if isinstance(node, ast.Call):
            callees = check_expression(node.func, current_pairs)
            arguments = sorted([*node.args,
                                *(kw.value for kw in node.keywords)],
                               key=lambda n: (n.lineno, n.col_offset))
            outputs = []
            for state in callees:
                sender = state.aliases.get(node.func.target.id) if isinstance(
                    node.func, ast.NamedExpr) else _resolve_sender_name(
                        node.func, state.aliases)
                call_name = (node.func.id if isinstance(node.func, ast.Name)
                             else None)
                consumer = (call_name if call_name in _BUILTIN_CONSUMERS
                            and call_name not in state.builtin_shadows
                            else None)
                current = [state]
                for argument in arguments:
                    current = check_expression(argument, current)
                if consumer in _EAGER_ITERABLE_CALLS:
                    for argument in node.args:
                        current = consume_iterable(
                            argument, current, exhaust=True)
                elif consumer in _PARTIAL_ITERABLE_CALLS and node.args:
                    current = consume_iterable(
                        node.args[0], current, exhaust=False)
                for current_state in current:
                    found = _py_call_violations(
                        node, current_state.dicts, rel,
                        allowed_opaque_names, sender)
                    violations.extend(found)
                outputs.extend(current)
            return remember(node, _dedupe_state_pairs(outputs))
        if isinstance(node, ast.GeneratorExp):
            entered = check_expression(node.generators[0].iter, current_pairs)
            return remember(node, entered)
        if isinstance(node, _COMPREHENSIONS):
            entered = check_expression(node.generators[0].iter, current_pairs)
            active = copied(entered)
            may_skip = False
            for index, generator in enumerate(node.generators):
                if index:
                    active = check_expression(generator.iter, active)
                cardinality = iterable_nonempty(generator.iter, active)
                active = consume_iterable(
                    generator.iter, active, exhaust=True)
                if cardinality is False:
                    skipped = entered if may_skip else []
                    return remember(node, _dedupe_state_pairs(
                        [*skipped, *active]))
                for condition in generator.ifs:
                    active = check_expression(condition, active)
                may_skip |= bool(generator.ifs) or cardinality is not True
            results = [node.key, node.value] if isinstance(
                node, ast.DictComp) else [node.elt]
            for result in results:
                active = check_expression(result, active)
            skipped = entered if may_skip else []
            return remember(
                node, _dedupe_state_pairs([*skipped, *active]))
        if isinstance(node, ast.Starred):
            current_pairs = check_expression(node.value, current_pairs)
            current_pairs = consume_iterable(
                node.value, current_pairs, exhaust=True)
            return remember(node, current_pairs)
        if isinstance(node, ast.Dict):
            children = [child for item in zip(node.keys, node.values)
                        for child in item if child is not None]
        else:
            children = ast.iter_child_nodes(node)
        for child in children:
            current_pairs = check_expression(child, current_pairs)
        return remember(node, current_pairs)

    def walk(parts, current_pairs, exits=flow_exits):
        return _py_flow_violations(
            parts, current_pairs, rel, allowed_opaque_names, exits)

    for statement in statements:  # pylint: disable=too-many-nested-blocks
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            values = [*statement.decorator_list,
                      *_argument_defaults(statement.args)]
            for value in values:
                pairs = check_expression(value, pairs)
            for state in pairs:
                state.aliases.pop(statement.name, None)
                state.generators.pop(statement.name, None)
                state.builtin_shadows.update(
                    {statement.name} & _BUILTIN_CONSUMERS)
            nested, _ = _py_flow_violations(
                statement.body, [_callable_state(statement.args, pairs)], rel,
                _function_allowed_opaque(statement))
            violations.extend(nested)
            continue
        if isinstance(statement, ast.ClassDef):
            for state in pairs:
                state.aliases.pop(statement.name, None)
                state.generators.pop(statement.name, None)
                state.builtin_shadows.update(
                    {statement.name} & _BUILTIN_CONSUMERS)
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
            loop_generators = {
                generator_for(header, state) for state in pairs
                if generator_for(header, state) is not None}
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                pairs = consume_iterable(header, pairs, exhaust=False)
            incoming = [_copy_state_pair(pair) for pair in pairs]
            zero_pairs = incoming
            if (isinstance(statement, (ast.For, ast.AsyncFor))
                    and iterable_nonempty(statement.iter, pairs) is True):
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
            normal_pairs = _dedupe_state_pairs([*zero_pairs, *post_body])
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                normal_pairs = exhaust_generators(
                    loop_generators, normal_pairs)
            if statement.orelse:
                found, normal_pairs = walk(statement.orelse, normal_pairs)
                violations.extend(found)
            pairs = _dedupe_state_pairs([*normal_pairs, *break_pairs])
            continue
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            entered = [_copy_state_pair(pair) for pair in pairs]
            for item in statement.items:
                entered = check_expression(item.context_expr, entered)
                if item.optional_vars is None:
                    continue
                names = _bound_names(item.optional_vars)
                for state in entered:
                    resolved = _resolve_sender_name(
                        item.context_expr, state.aliases)
                    for name in names:
                        state.dicts.pop(name, None)
                        state.aliases.pop(name, None)
                        state.generators.pop(name, None)
                        state.builtin_shadows.update(
                            {name} & _BUILTIN_CONSUMERS)
                        if resolved is not None:
                            state.aliases[name] = resolved
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
        targets = (statement.targets if isinstance(statement, ast.Assign)
                   else [statement.target] if isinstance(
                       statement, ast.AnnAssign) else ())
        if any(isinstance(target, (ast.Tuple, ast.List))
               for target in targets):
            pairs = consume_iterable(statement.value, pairs, exhaust=True)
        for state in pairs:
            _apply_dict_statement(statement, state.dicts)
            _apply_alias_statement(statement, state)
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
        tree.body, [FlowState({}, {}, {}, {}, set())], rel, frozenset())
    return list(dict.fromkeys(violations))
