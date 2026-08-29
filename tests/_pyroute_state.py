"""Alias-flow state primitives for Python tab-routing analysis."""
import ast
import operator
from dataclasses import dataclass


OPAQUE_TAB_SPREAD = object()
UNPROVABLE_SENDER = '?ext_cmd'
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
BUILTIN_CONSUMERS = frozenset({
    'all', 'any', 'dict', 'frozenset', 'list', 'max', 'min', 'next', 'set',
    'sorted', 'sum', 'tuple',
})


@dataclass
class FlowState:
    """One reachable payload, alias, and deferred-expression state."""

    dicts: dict
    aliases: dict
    generators: dict
    evaluated: dict
    builtin_globals: set
    builtin_locals: set
    callables: dict
    bound: set

    def copy(self):
        return FlowState(
            {name: keys.copy() for name, keys in self.dicts.items()},
            dict(self.aliases), dict(self.generators), dict(self.evaluated),
            set(self.builtin_globals), set(self.builtin_locals),
            dict(self.callables), set(self.bound))


@dataclass(frozen=True)
class DeferredGenerator:
    """A generator expression and its known remaining yield count."""

    expression: ast.GeneratorExp
    remaining: int | None
    evaluate_zero: bool = False


@dataclass(frozen=True)
class DeferredCallable:
    """A callable body and its definition-time lexical state."""

    scope: ast.AST
    state: FlowState


def scope_nodes(scope):
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda)):
            continue
        yield child
        yield from scope_nodes(child)


def lexical_scope_nodes(scope):
    """Nodes compiled in `scope`, including nested-definition headers."""
    roots = [scope.body] if isinstance(scope, ast.Lambda) else scope.body

    def visit(node):
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            values = definition_values(node)
        elif isinstance(node, ast.Lambda):
            values = [*node.args.defaults,
                      *(value for value in node.args.kw_defaults
                        if value is not None)]
        elif isinstance(node, ast.ClassDef):
            values = [*node.decorator_list, *node.bases,
                      *(keyword.value for keyword in node.keywords)]
        else:
            values = ast.iter_child_nodes(node)
        for value in values:
            yield from visit(value)

    for root in roots:
        yield from visit(root)


def callable_builtin_scope(scope):
    """Return lexical shadows and explicit globals for one callable."""
    nodes = list(lexical_scope_nodes(scope))
    comprehension_targets = {
        target
        for parent in nodes if isinstance(parent, COMPREHENSIONS)
        for generator in parent.generators
        for target in ast.walk(generator.target)
        if isinstance(target, ast.Name)
    }
    bound = {node.id for node in nodes
             if isinstance(node, ast.Name)
             and node not in comprehension_targets
             and isinstance(node.ctx, (ast.Store, ast.Del))}
    bound |= {(alias.asname or alias.name).split('.')[0]
              for node in nodes
              if isinstance(node, (ast.Import, ast.ImportFrom))
              for alias in node.names}
    bound |= {node.name for node in nodes
              if isinstance(node, ast.ExceptHandler) and node.name}
    bound |= {node.name for node in nodes
              if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name}
    bound |= {node.rest for node in nodes
              if isinstance(node, ast.MatchMapping) and node.rest}
    bound |= {node.name for node in nodes
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef))}
    args = scope.args
    bound |= {arg.arg for arg in [*args.posonlyargs, *args.args,
                                  *args.kwonlyargs]}
    if args.vararg is not None:
        bound.add(args.vararg.arg)
    if args.kwarg is not None:
        bound.add(args.kwarg.arg)
    globals_ = {name for node in nodes if isinstance(node, ast.Global)
                for name in node.names}
    nonlocals = {name for node in nodes if isinstance(node, ast.Nonlocal)
                 for name in node.names}
    lexical = (bound - globals_ - nonlocals) | nonlocals
    return (lexical & BUILTIN_CONSUMERS,
            globals_ & BUILTIN_CONSUMERS)


def is_extension_constant(node):
    return isinstance(node, ast.Constant) and node.value == 'extension'


def _merge_payload_keys(keys, spread, spread_node):
    """Apply a spread; a later explicit tab clears opaque uncertainty."""
    if spread is None:
        keys[OPAQUE_TAB_SPREAD] = (spread_node.lineno, spread_node)
        return
    if OPAQUE_TAB_SPREAD in spread:
        keys[OPAQUE_TAB_SPREAD] = (spread_node.lineno, spread_node)
    if 'tab' in spread:
        keys.pop(OPAQUE_TAB_SPREAD, None)
    keys.update((key, value) for key, value in spread.items()
                if key is not OPAQUE_TAB_SPREAD)


def payload_keys(expr, dicts):
    """Return tracked string keys, or None for a wholly opaque expression."""
    if isinstance(expr, ast.Name):
        return dicts.get(expr.id)
    if isinstance(expr, ast.Dict):
        keys = {}
        for key, value in zip(expr.keys, expr.values):
            if key is None:
                _merge_payload_keys(keys, payload_keys(value, dicts), value)
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                if key.value == 'tab':
                    keys.pop(OPAQUE_TAB_SPREAD, None)
                keys[key.value] = (value.lineno, value)
        return keys
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
            and expr.func.id == 'dict'):
        if expr.args:
            return None
        keys = {}
        for keyword in expr.keywords:
            if keyword.arg is None:
                spread = payload_keys(keyword.value, dicts)
                _merge_payload_keys(keys, spread, keyword.value)
            else:
                if keyword.arg == 'tab':
                    keys.pop(OPAQUE_TAB_SPREAD, None)
                keys[keyword.arg] = (keyword.value.lineno, keyword.value)
        return keys
    return None


def _update_keys(call, dicts):
    keys = {}
    if call.args:
        merged = payload_keys(call.args[0], dicts)
        if merged is None:
            return None
        keys.update(merged)
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
        if keyword.arg == 'tab':
            keys.pop(OPAQUE_TAB_SPREAD, None)
        keys[keyword.arg] = (keyword.value.lineno, keyword.value)
    return keys


def apply_dict_statement(node, dicts):
    """Apply one assignment or mapping mutation to tracked payloads."""
    if isinstance(node, ast.AugAssign):
        if (isinstance(node.op, ast.BitOr)
                and isinstance(node.target, ast.Name)):
            merged = payload_keys(node.value, dicts)
            if merged is None:
                dicts.pop(node.target.id, None)
            else:
                _merge_payload_keys(
                    dicts.setdefault(node.target.id, {}), merged, node.value)
        return
    if isinstance(node, ast.Expr):
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
                    OPAQUE_TAB_SPREAD, None)
            dicts.setdefault(target.value.id, {})[target.slice.value] = (
                node.value.lineno, node.value)


def dict_assignments(scope):
    """Map local names to string keys, retaining provable mutations."""
    dicts = {}
    nodes = [node for node in scope_nodes(scope)
             if isinstance(node, (ast.Assign, ast.AnnAssign,
                                  ast.AugAssign, ast.Expr))]
    for node in sorted(nodes, key=lambda item: (item.lineno, item.col_offset)):
        apply_dict_statement(node, dicts)
    return dicts


def resolve_sender_name(expr, aliases):
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
                         'ext_cmd', '_ext_cmd', UNPROVABLE_SENDER))):
            return UNPROVABLE_SENDER
        if (isinstance(child, ast.Attribute)
                and child.attr in ('ext_cmd', '_ext_cmd')):
            return UNPROVABLE_SENDER
        if (isinstance(child, ast.Constant)
                and child.value in ('ext_cmd', '_ext_cmd')):
            return UNPROVABLE_SENDER
    return None


def bound_names(target):
    return {node.id for node in ast.walk(target)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))}


def rebound_names(node):
    """Names rebound by node, excluding scoped comprehension targets."""
    nodes = [node, *scope_nodes(node)]
    comprehension_targets = {
        target
        for parent in nodes if isinstance(parent, COMPREHENSIONS)
        for generator in parent.generators
        for target in ast.walk(generator.target)
        if isinstance(target, ast.Name)
    }
    names = {item.id for item in nodes
             if isinstance(item, ast.Name)
             and item not in comprehension_targets
             and isinstance(item.ctx, (ast.Store, ast.Del))}
    names |= {(alias.asname or alias.name).split('.')[0]
              for item in nodes
              if isinstance(item, (ast.Import, ast.ImportFrom))
              for alias in item.names}
    names |= {item.name for item in nodes
              if isinstance(item, ast.ExceptHandler) and item.name}
    names |= {item.name for item in nodes
              if isinstance(item, (ast.MatchAs, ast.MatchStar)) and item.name}
    names |= {item.rest for item in nodes
              if isinstance(item, ast.MatchMapping) and item.rest}
    names |= {item.name for item in ast.walk(node)
              if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)) and item is not node}
    return names


def evaluated_value(value, state):
    if id(value) in state.evaluated:
        return state.evaluated[id(value)]
    return resolve_sender_name(value, state.aliases)


_UNSAFE_LITERAL = object()
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg,
                    ast.Invert: operator.invert}


def _literal_value(expr):
    if isinstance(expr, ast.Constant):
        return expr.value
    if isinstance(expr, ast.UnaryOp) and type(expr.op) in _UNARY_OPERATORS:
        value = _literal_value(expr.operand)
        if (value is _UNSAFE_LITERAL
                or type(value) not in (int, float, complex)):
            return _UNSAFE_LITERAL
        try:
            return _UNARY_OPERATORS[type(expr.op)](value)
        except (ArithmeticError, TypeError, ValueError):
            return _UNSAFE_LITERAL
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        values = []
        for item in expr.elts:
            value = _literal_value(item.value if isinstance(item, ast.Starred)
                                   else item)
            if value is _UNSAFE_LITERAL:
                return value
            try:
                values.extend(value) if isinstance(item, ast.Starred) \
                    else values.append(value)
            except TypeError:
                return _UNSAFE_LITERAL
        try:
            return (tuple(values) if isinstance(expr, ast.Tuple) else
                    values if isinstance(expr, ast.List) else set(values))
        except (TypeError, ValueError):
            return _UNSAFE_LITERAL
    if isinstance(expr, ast.Dict):
        value = {}
        for key, item in zip(expr.keys, expr.values):
            item_value = _literal_value(item)
            key_value = _literal_value(key) if key is not None else None
            if item_value is _UNSAFE_LITERAL or key_value is _UNSAFE_LITERAL:
                return _UNSAFE_LITERAL
            try:
                if key is None:
                    if not isinstance(item_value, dict):
                        return _UNSAFE_LITERAL
                    value.update(item_value)
                else:
                    value[key_value] = item_value
            except (TypeError, ValueError):
                return _UNSAFE_LITERAL
        return value
    return _UNSAFE_LITERAL


def literal_iterable_cardinality(expr):
    """Return an exact literal-display length when it is provable."""
    if isinstance(expr, (ast.Tuple, ast.List)):
        counts = [literal_iterable_cardinality(item.value)
                  if isinstance(item, ast.Starred) else 1
                  for item in expr.elts]
        return None if any(count is None for count in counts) else sum(counts)
    if isinstance(expr, (ast.Set, ast.Dict)):
        value = _literal_value(expr)
        return None if value is _UNSAFE_LITERAL else len(value)
    return None


def literal_truth(expr):
    value = _literal_value(expr)
    return None if value is _UNSAFE_LITERAL else bool(value)


def deferred_generator(expr):
    remaining = 1
    for clause in expr.generators:
        count = literal_iterable_cardinality(clause.iter)
        if count == 0:
            return DeferredGenerator(expr, 0)
        truths = [literal_truth(condition) for condition in clause.ifs]
        if False in truths:
            return DeferredGenerator(expr, 0, True)
        if count is None or None in truths:
            return DeferredGenerator(expr, None)
        remaining *= count
    return DeferredGenerator(expr, remaining)


def bind_alias_target(target, value, state, bindings):
    if isinstance(target, ast.Name):
        resolved = evaluated_value(value, state)
        if isinstance(resolved, DeferredGenerator):
            bindings[1][target.id] = resolved
        elif resolved is not None:
            bindings[0][target.id] = resolved
        return
    if not isinstance(target, (ast.Tuple, ast.List)):
        return
    pairs = None
    if isinstance(value, (ast.Tuple, ast.List)):
        stars = [index for index, item in enumerate(target.elts)
                 if isinstance(item, ast.Starred)]
        if not stars and len(target.elts) == len(value.elts):
            pairs = zip(target.elts, value.elts)
        elif len(stars) == 1 and len(value.elts) >= len(target.elts) - 1:
            star = stars[0]
            suffix = len(target.elts) - star - 1
            pairs = [*zip(target.elts[:star], value.elts[:star]),
                     *(zip(target.elts[-suffix:], value.elts[-suffix:])
                       if suffix else ())]
    if pairs is not None:
        for nested_target, nested_value in pairs:
            bind_alias_target(nested_target, nested_value, state, bindings)
        return
    resolved = evaluated_value(value, state)
    if resolved is not None and not isinstance(resolved, DeferredGenerator):
        for name in bound_names(target):
            bindings[0][name] = UNPROVABLE_SENDER


def bind_builtin_names(state, names):
    global_names = (names & BUILTIN_CONSUMERS) - state.builtin_locals
    state.builtin_globals.update(global_names)


def delete_builtin_names(state, names):
    global_names = (names & BUILTIN_CONSUMERS) - state.builtin_locals
    state.builtin_globals.difference_update(global_names)


def apply_alias_statement(node, state):
    aliases = state.aliases
    if isinstance(node, ast.ImportFrom):
        for imported in node.names:
            local = imported.asname or imported.name
            bind_builtin_names(state, {local})
            aliases.pop(local, None)
            state.generators.pop(local, None)
            state.callables.pop(local, None)
            state.bound.add(local)
            if imported.name in ('ext_cmd', '_ext_cmd'):
                aliases[local] = imported.name
        return
    if isinstance(node, ast.Import):
        for imported in node.names:
            local = (imported.asname or imported.name).split('.')[0]
            bind_builtin_names(state, {local})
            aliases.pop(local, None)
            state.generators.pop(local, None)
            state.callables.pop(local, None)
            state.bound.add(local)
        return
    targets = (node.targets if isinstance(node, (ast.Assign, ast.Delete))
               else [node.target] if isinstance(
                   node, (ast.AnnAssign, ast.AugAssign)) else None)
    if targets is None:
        return
    bindings = ({}, {})
    if type(node) in (ast.Assign, ast.AnnAssign) and node.value is not None:
        for target in targets:
            bind_alias_target(target, node.value, state, bindings)
    names = set().union(*(bound_names(target) for target in targets))
    if isinstance(node, ast.Delete):
        delete_builtin_names(state, names)
        state.bound.difference_update(names)
    elif not isinstance(node, ast.AnnAssign) or node.value is not None:
        bind_builtin_names(state, names)
        state.bound.update(names)
    for name in names:
        aliases.pop(name, None)
        state.generators.pop(name, None)
        state.callables.pop(name, None)
    aliases.update(bindings[0])
    state.generators.update(bindings[1])


def value_signature(value):
    if isinstance(value, DeferredGenerator):
        expr = value.expression
        return ('generator', expr.lineno, expr.col_offset, value.remaining,
                value.evaluate_zero)
    return value


def state_signature(state):
    payloads = []
    for name, keys in sorted(state.dicts.items()):
        tab = ('opaque' if OPAQUE_TAB_SPREAD in keys else 'absent'
               if 'tab' not in keys else 'extension'
               if is_extension_constant(keys['tab'][1]) else 'other')
        payloads.append((name, 'type' in keys, tab))
    generators = tuple(sorted(
        (name, value.expression.lineno, value.expression.col_offset,
         value.remaining, value.evaluate_zero)
        for name, value in state.generators.items()))
    evaluated = tuple(sorted(
        (key, value_signature(value))
        for key, value in state.evaluated.items()))
    callables = tuple(sorted((name, id(value))
                             for name, value in state.callables.items()))
    return (tuple(payloads), tuple(sorted(state.aliases.items())),
            generators, evaluated, tuple(sorted(state.builtin_globals)),
            tuple(sorted(state.builtin_locals)), callables,
            tuple(sorted(state.bound)))


def dedupe_states(states):
    found = {}
    for state in states:
        found.setdefault(state_signature(state), state)
    return list(found.values())


def inherited_aliases(states):
    inherited = {}
    names = {name for state in states for name in state.aliases}
    for name in names:
        values = [state.aliases.get(name) for state in states]
        first = values[0]
        if first is not None and all(value == first for value in values):
            inherited[name] = first
        elif any(value is not None for value in values):
            inherited[name] = UNPROVABLE_SENDER
    return inherited


def inherited_generators(states):
    inherited = {}
    names = {name for state in states for name in state.generators}
    for name in names:
        values = [state.generators.get(name) for state in states]
        if values[0] is not None and all(value is values[0]
                                         for value in values):
            inherited[name] = values[0]
    return inherited


def inherited_callables(states):
    inherited = {}
    names = {name for state in states for name in state.callables}
    for name in names:
        values = [state.callables.get(name) for state in states]
        if values[0] is not None and all(value is values[0]
                                         for value in values):
            inherited[name] = values[0]
    return inherited


def merged_evaluated_value(default, states):
    values = [evaluated_value(default, state) for state in states]
    if all(value is values[0] or value == values[0] for value in values):
        return values[0]
    if any(value is not None and not isinstance(value, DeferredGenerator)
           for value in values):
        return UNPROVABLE_SENDER
    return None


def callable_state(scope, states):
    args = scope.args
    aliases = inherited_aliases(states)
    generators = inherited_generators(states)
    callables = inherited_callables(states)
    builtin_globals = set().union(
        *(state.builtin_globals for state in states))
    inherited_locals = set().union(
        *(state.builtin_locals for state in states))
    local_names, global_names = callable_builtin_scope(scope)
    builtin_locals = (inherited_locals | local_names) - global_names
    positional = [*args.posonlyargs, *args.args]
    parameters = [*positional, *args.kwonlyargs]
    if args.vararg is not None:
        parameters.append(args.vararg)
    if args.kwarg is not None:
        parameters.append(args.kwarg)
    bound = (set.intersection(*(state.bound for state in states))
             if states else set())
    bound.update(parameter.arg for parameter in parameters)
    for parameter in parameters:
        aliases.pop(parameter.arg, None)
        generators.pop(parameter.arg, None)
    defaults = zip(positional[-len(args.defaults):], args.defaults)
    if not args.defaults:
        defaults = ()
    for parameter, default in [*defaults, *zip(
            args.kwonlyargs, args.kw_defaults)]:
        if default is None:
            continue
        resolved = merged_evaluated_value(default, states)
        if isinstance(resolved, DeferredGenerator):
            generators[parameter.arg] = resolved
        elif resolved is not None:
            aliases[parameter.arg] = resolved
    return FlowState({}, aliases, generators, {}, builtin_globals,
                     builtin_locals, callables, bound)


def function_allowed_opaque(node):
    return (frozenset({node.args.kwarg.arg})
            if node.name in ('ext_cmd', '_ext_cmd')
            and node.args.kwarg is not None else frozenset())


def class_functions(node):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield child
        else:
            yield from class_functions(child)


def clear_names(states, names):
    for state in states:
        bind_builtin_names(state, names)
        state.bound.update(names)
        for name in names:
            state.dicts.pop(name, None)
            state.aliases.pop(name, None)
            state.generators.pop(name, None)
            state.callables.pop(name, None)


def new_exits():
    return {'break': [], 'continue': [], 'terminal': []}


def record_exit(exits, kind, states):
    if exits is not None:
        exits[kind].extend(state.copy() for state in states)


def argument_defaults(args):
    return [*args.defaults,
            *(default for default in args.kw_defaults if default is not None)]


def definition_values(node):
    """Expressions evaluated while a function object is defined."""
    args = node.args
    parameters = [*args.posonlyargs, *args.args]
    if args.vararg is not None:
        parameters.append(args.vararg)
    parameters.extend(args.kwonlyargs)
    if args.kwarg is not None:
        parameters.append(args.kwarg)
    annotations = [parameter.annotation for parameter in parameters
                   if parameter.annotation is not None]
    returns = [node.returns] if node.returns is not None else []
    return [*node.decorator_list, *argument_defaults(args),
            *annotations, *returns]


def statement_cannot_raise(node, bound):
    """Whether a simple module statement is statically exception-free."""
    if isinstance(node, ast.Pass):
        return True
    if isinstance(node, ast.Assign):
        targets = node.targets
        value = node.value
    elif isinstance(node, ast.Delete):
        names = set().union(*(bound_names(target) for target in node.targets))
        return all(isinstance(target, ast.Name) for target in node.targets) \
            and names <= bound
    else:
        return False
    safe_value = (isinstance(value, (ast.Constant, ast.Lambda))
                  or isinstance(value, ast.Name) and value.id in bound
                  or _literal_value(value) is not _UNSAFE_LITERAL)
    return safe_value and all(isinstance(target, ast.Name)
                              for target in targets)


def literal_iterable_nonempty(expr):
    """True/False for provable display cardinality, otherwise None."""
    count = literal_iterable_cardinality(expr)
    return None if count is None else count > 0
