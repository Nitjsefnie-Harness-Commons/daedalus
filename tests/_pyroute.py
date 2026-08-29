"""Follow Python payloads and report unproved sender aliases."""
import ast

from _pyroute_state import (BUILTIN_CONSUMERS as _BUILTIN_CONSUMERS,
                            COMPREHENSIONS as _COMPREHENSIONS,
                            OPAQUE_TAB_SPREAD as _OPAQUE_TAB_SPREAD,
                            UNPROVABLE_SENDER as _UNPROVABLE_SENDER,
                            DeferredCallable, DeferredGenerator, FlowState,
                            apply_alias_statement, apply_dict_statement,
                            argument_defaults,
                            bind_alias_target, bind_builtin_names, bound_names,
                            callable_state, class_functions, clear_names,
                            dedupe_states, deferred_generator,
                            definition_values,
                            dict_assignments as _dict_assignments,
                            function_allowed_opaque,
                            is_extension_constant, literal_iterable_nonempty,
                            literal_truth, payload_keys,
                            new_exits, rebound_names, record_exit,
                            resolve_sender_name, state_signature,
                            statement_cannot_raise)

_apply_alias_statement = apply_alias_statement
_apply_dict_statement = apply_dict_statement
_argument_defaults = argument_defaults
_bind_alias_target = bind_alias_target
_bind_builtin_names = bind_builtin_names
_bound_names = bound_names
_callable_state = callable_state
_class_functions = class_functions
_clear_names = clear_names
_copy_state_pair = FlowState.copy
_dedupe_state_pairs = dedupe_states
_deferred_generator = deferred_generator
_definition_values = definition_values
dict_assignments = _dict_assignments
_function_allowed_opaque = function_allowed_opaque
_is_extension_constant = is_extension_constant
_literal_truth = literal_truth
_new_exits = new_exits
_rebound_names = rebound_names
_record_exit = record_exit
_resolve_sender_name = resolve_sender_name
_state_pair_signature = state_signature
_statement_cannot_raise = statement_cannot_raise
_EAGER_ITERABLE_CALLS = frozenset({
    'dict', 'frozenset', 'list', 'max', 'min', 'set', 'sorted', 'sum', 'tuple',
})
_PARTIAL_ITERABLE_CALLS = frozenset({'all', 'any', 'next'})


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
                        flow_exits=None, scope_callables=None,
                        scope_executed=None, active_callables=frozenset(),
                        module_scope=False):
    violations = []
    owns_scope = scope_callables is None
    scope_callables = [] if scope_callables is None else scope_callables
    scope_executed = set() if scope_executed is None else scope_executed
    fallback = _copy_state_pair(pairs[0]) if pairs else None

    def copied(found):
        return [_copy_state_pair(pair) for pair in found]

    def analyze_callable(deferred, callers, executed=False):
        key = id(deferred)
        if key in active_callables:
            return
        if executed:
            scope_executed.add(key)
        entries = []
        for caller in callers:
            entry = _copy_state_pair(deferred.state)
            entry.builtin_globals = set(caller.builtin_globals)
            entry.callables.update(caller.callables)
            entry.bound.update(caller.bound)
            entries.append(entry)
        nested, _ = _py_flow_violations(
            deferred.scope.body, entries, rel,
            _function_allowed_opaque(deferred.scope),
            active_callables=active_callables | {key})
        violations.extend(nested)

    def expression_value(node, state):
        if isinstance(node, ast.GeneratorExp):
            return _deferred_generator(node)
        if isinstance(node, ast.Name) and node.id in state.generators:
            return state.generators[node.id]
        if isinstance(node, (ast.NamedExpr, ast.Starred)):
            child = node.value
            if id(child) in state.evaluated:
                return state.evaluated[id(child)]
        if isinstance(node, (ast.Tuple, ast.List)):
            values = [state.evaluated.get(id(item)) for item in node.elts]
            if any(value is not None
                   and not isinstance(value, DeferredGenerator)
                   for value in values):
                return _UNPROVABLE_SENDER
        return _resolve_sender_name(node, state.aliases)

    def remember(node, current_pairs):
        for state in current_pairs:
            state.evaluated[id(node)] = expression_value(node, state)
        return current_pairs

    def generator_for(expr, state):
        if isinstance(expr, ast.GeneratorExp):
            value = state.evaluated.get(id(expr))
            return value if isinstance(value, DeferredGenerator) else None
        if isinstance(expr, ast.Name):
            return state.generators.get(expr.id)
        value = state.evaluated.get(id(expr))
        return value if isinstance(value, DeferredGenerator) else None

    def generator_nonempty(generator):
        if generator.remaining is not None:
            return generator.remaining > 0
        expression = generator.expression
        states = [literal_iterable_nonempty(clause.iter)
                  for clause in expression.generators]
        if False in states:
            return False
        if all(state is True for state in states) and not any(
                clause.ifs for clause in expression.generators):
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
        expression = generator.expression
        entered = copied(current_pairs)
        active = copied(current_pairs)
        may_skip = False
        for index, clause in enumerate(expression.generators):
            if index:
                active = check_expression(clause.iter, active)
            cardinality = iterable_nonempty(clause.iter, active)
            active = consume_iterable(clause.iter, active, exhaust=True)
            if cardinality is False:
                skipped = entered if may_skip else []
                return _dedupe_state_pairs([*skipped, *active])
            for condition in clause.ifs:
                active = check_expression(condition, active)
            truths = [_literal_truth(condition) for condition in clause.ifs]
            if False in truths:
                skipped = entered if may_skip else []
                return _dedupe_state_pairs([*skipped, *active])
            may_skip |= None in truths or cardinality is not True
        results = [expression.key, expression.value] if isinstance(
            expression, ast.DictComp) else [expression.elt]
        for result in results:
            active = check_expression(result, active)
        skipped = entered if may_skip else []
        return _dedupe_state_pairs([*skipped, *active])

    def advance_generator(state, generator):
        if generator.remaining is None:
            return
        remaining = max(0, generator.remaining - 1)
        advanced = DeferredGenerator(generator.expression, remaining)
        for name, value in list(state.generators.items()):
            if value is not generator:
                continue
            if remaining:
                state.generators[name] = advanced
            else:
                del state.generators[name]
        for key, value in list(state.evaluated.items()):
            if value is generator:
                state.evaluated[key] = advanced

    def consume_iterable(expr, current_pairs, exhaust):
        outputs = []
        for state in current_pairs:
            generator = generator_for(expr, state)
            if generator is None:
                outputs.append(state)
                continue
            if generator.remaining == 0 and not generator.evaluate_zero:
                advance_generator(state, generator)
                outputs.append(state)
                continue
            consumed = consume_generator(generator, [state])
            if exhaust:
                for output in consumed:
                    output.generators = {
                        name: value
                        for name, value in output.generators.items()
                        if value is not generator}
            else:
                for output in consumed:
                    advance_generator(output, generator)
            outputs.extend(consumed)
        return _dedupe_state_pairs(outputs)

    def exhaust_generators(expressions, current_pairs):
        for state in current_pairs:
            state.generators = {
                name: value for name, value in state.generators.items()
                if value.expression not in expressions}
        return _dedupe_state_pairs(current_pairs)

    def check_expression(node, current_pairs):
        if node is None:
            return current_pairs
        if isinstance(node, ast.Lambda):
            defaults = _argument_defaults(node.args)
            for default in defaults:
                current_pairs = check_expression(default, current_pairs)
            if defaults:
                nested = [_callable_state(node, current_pairs)]
                check_expression(node.body, nested)
            return remember(node, current_pairs)
        if isinstance(node, ast.NamedExpr):
            current_pairs = check_expression(node.value, current_pairs)
            for state in current_pairs:
                bindings = ({}, {})
                _bind_alias_target(node.target, node.value, state, bindings)
                state.aliases.pop(node.target.id, None)
                state.generators.pop(node.target.id, None)
                state.callables.pop(node.target.id, None)
                state.bound.add(node.target.id)
                _bind_builtin_names(state, {node.target.id})
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
            truth = _literal_truth(node.test)
            if truth is True:
                return remember(node, check_expression(node.body, tested))
            if truth is False:
                return remember(node, check_expression(node.orelse, tested))
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
                deferred = state.callables.get(call_name)
                consumer = (call_name if call_name in _BUILTIN_CONSUMERS
                            and call_name not in state.builtin_globals
                            and call_name not in state.builtin_locals
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
                if deferred is not None:
                    analyze_callable(deferred, current, executed=True)
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
                truths = [_literal_truth(condition)
                          for condition in generator.ifs]
                if False in truths:
                    skipped = entered if may_skip else []
                    return remember(node, _dedupe_state_pairs(
                        [*skipped, *active]))
                may_skip |= None in truths or cardinality is not True
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
            parts, current_pairs, rel, allowed_opaque_names, exits,
            scope_callables, scope_executed, active_callables, module_scope)

    for statement in statements:  # pylint: disable=too-many-nested-blocks
        if pairs:
            fallback = _copy_state_pair(pairs[0])
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for value in _definition_values(statement):
                pairs = check_expression(value, pairs)
            defining = pairs or ([fallback] if fallback is not None else [])
            for state in defining:
                state.aliases.pop(statement.name, None)
                state.generators.pop(statement.name, None)
                state.callables.pop(statement.name, None)
                state.bound.add(statement.name)
                _bind_builtin_names(state, {statement.name})
                deferred = DeferredCallable(
                    statement, _callable_state(statement, [state]))
                scope_callables.append(deferred)
                if pairs:
                    state.callables[statement.name] = deferred
            continue
        if isinstance(statement, ast.ClassDef):
            for state in pairs:
                state.aliases.pop(statement.name, None)
                state.generators.pop(statement.name, None)
                state.callables.pop(statement.name, None)
                state.bound.add(statement.name)
                _bind_builtin_names(state, {statement.name})
            nested, _ = _py_flow_violations(
                list(_class_functions(statement)),
                [_copy_state_pair(pair) for pair in pairs], rel,
                allowed_opaque_names, scope_callables=scope_callables,
                scope_executed=scope_executed,
                active_callables=active_callables,
                module_scope=module_scope)
            violations.extend(nested)
            continue
        if isinstance(statement, ast.If):
            pairs = check_expression(statement.test, pairs)
            incoming = [_copy_state_pair(pair) for pair in pairs]
            truth = _literal_truth(statement.test)
            if truth is True:
                found, pairs = walk(statement.body, incoming)
                violations.extend(found)
                continue
            if truth is False:
                found, pairs = walk(statement.orelse, incoming)
                violations.extend(found)
                continue
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
            header_nonempty = iterable_nonempty(header, pairs)
            loop_generators = {
                generator_for(header, state).expression for state in pairs
                if generator_for(header, state) is not None}
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                pairs = consume_iterable(header, pairs, exhaust=False)
            incoming = [_copy_state_pair(pair) for pair in pairs]
            zero_pairs = incoming
            if (isinstance(statement, (ast.For, ast.AsyncFor))
                    and header_nonempty is True):
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
                        state.callables.pop(name, None)
                        state.bound.add(name)
                        _bind_builtin_names(state, {name})
                        if resolved is not None:
                            state.aliases[name] = resolved
            found, pairs = walk(statement.body, entered)
            violations.extend(found)
            continue
        if isinstance(statement, (ast.Try, ast.TryStar)):
            incoming = [_copy_state_pair(pair) for pair in pairs]
            body_pairs = [_copy_state_pair(pair) for pair in incoming]
            handler_entry = []
            exits = _new_exits()
            for body_statement in statement.body:
                before = [_copy_state_pair(pair) for pair in body_pairs]
                safe = module_scope and body_pairs and all(
                    _statement_cannot_raise(body_statement, state.bound)
                    for state in body_pairs)
                found, body_pairs = walk(
                    [body_statement], body_pairs, exits)
                violations.extend(found)
                if not safe:
                    handler_entry.extend([*before, *copied(body_pairs)])
                if not body_pairs:
                    break
            if statement.orelse:
                found, normal_pairs = walk(
                    statement.orelse, body_pairs, exits)
                violations.extend(found)
            else:
                normal_pairs = body_pairs
            handler_pairs = []
            handler_entry = _dedupe_state_pairs(handler_entry)
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
    if owns_scope:
        seen = set()
        for deferred in scope_callables:
            key = id(deferred)
            if key in seen:
                continue
            seen.add(key)
            callers = [state for state in pairs
                       if any(value is deferred
                              for value in state.callables.values())]
            if not callers and key in scope_executed:
                continue
            if not callers:
                callers = pairs or [fallback or deferred.state]
            analyze_callable(deferred, callers)
    return violations, pairs


def py_tab_routing_violations(path, rel):
    """`tab` set to a non-'extension' value on a typed command sent from `path`.

    Typed means routed through ext_cmd/_ext_cmd or sent to /command with a
    `type` key. Eval payloads carry `code` and legitimately route by tab.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    violations, _ = _py_flow_violations(
        tree.body, [FlowState({}, {}, {}, {}, set(), set(), {}, set())], rel,
        frozenset(), module_scope=True)
    return list(dict.fromkeys(violations))
