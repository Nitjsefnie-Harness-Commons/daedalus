"""Alias-flow state primitives for Python tab-routing analysis."""
import ast
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

    def copy(self):
        return FlowState(
            {name: keys.copy() for name, keys in self.dicts.items()},
            dict(self.aliases), dict(self.generators), dict(self.evaluated),
            set(self.builtin_globals), set(self.builtin_locals))


@dataclass(frozen=True)
class DeferredGenerator:
    """A generator expression and its known remaining yield count."""

    expression: ast.GeneratorExp
    remaining: int | None


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
            values = [*node.decorator_list, *node.args.defaults,
                      *(value for value in node.args.kw_defaults
                        if value is not None)]
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


def literal_iterable_cardinality(expr):
    """Return an exact literal-display length when it is provable."""
    if isinstance(expr, (ast.Tuple, ast.List)):
        total = 0
        for item in expr.elts:
            if isinstance(item, ast.Starred):
                count = literal_iterable_cardinality(item.value)
                if count is None:
                    return None
                total += count
            else:
                total += 1
        return total
    if isinstance(expr, ast.Dict):
        if not expr.keys:
            return 0
        counts = [literal_iterable_cardinality(value) if key is None else 1
                  for key, value in zip(expr.keys, expr.values)]
        if any(count is None for count in counts):
            return None
        total = sum(counts)
        return total if total <= 1 else None
    if isinstance(expr, ast.Set):
        if not expr.elts:
            return 0
        counts = [literal_iterable_cardinality(item.value)
                  if isinstance(item, ast.Starred) else 1
                  for item in expr.elts]
        if any(count is None for count in counts):
            return None
        total = sum(counts)
        return total if total <= 1 else None
    return None


def deferred_generator(expr):
    remaining = 1
    for clause in expr.generators:
        count = literal_iterable_cardinality(clause.iter)
        if count == 0:
            return DeferredGenerator(expr, 0)
        if count is None or clause.ifs:
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
            if imported.name in ('ext_cmd', '_ext_cmd'):
                aliases[local] = imported.name
        return
    if isinstance(node, ast.Import):
        for imported in node.names:
            local = (imported.asname or imported.name).split('.')[0]
            bind_builtin_names(state, {local})
            aliases.pop(local, None)
            state.generators.pop(local, None)
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
    elif not isinstance(node, ast.AnnAssign) or node.value is not None:
        bind_builtin_names(state, names)
    for name in names:
        aliases.pop(name, None)
        state.generators.pop(name, None)
    aliases.update(bindings[0])
    state.generators.update(bindings[1])


def value_signature(value):
    if isinstance(value, DeferredGenerator):
        expr = value.expression
        return ('generator', expr.lineno, expr.col_offset, value.remaining)
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
         value.remaining)
        for name, value in state.generators.items()))
    evaluated = tuple(sorted(
        (key, value_signature(value))
        for key, value in state.evaluated.items()))
    return (tuple(payloads), tuple(sorted(state.aliases.items())),
            generators, evaluated, tuple(sorted(state.builtin_globals)),
            tuple(sorted(state.builtin_locals)))


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
                     builtin_locals)


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
        for name in names:
            state.dicts.pop(name, None)
            state.aliases.pop(name, None)
            state.generators.pop(name, None)


def new_exits():
    return {'break': [], 'continue': [], 'terminal': []}


def record_exit(exits, kind, states):
    if exits is not None:
        exits[kind].extend(state.copy() for state in states)


def argument_defaults(args):
    return [*args.defaults,
            *(default for default in args.kw_defaults if default is not None)]


def literal_iterable_nonempty(expr):
    """True/False for provable display cardinality, otherwise None."""
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        states = [literal_iterable_nonempty(item.value)
                  if isinstance(item, ast.Starred) else True
                  for item in expr.elts]
    elif isinstance(expr, ast.Dict):
        states = [literal_iterable_nonempty(value) if key is None else True
                  for key, value in zip(expr.keys, expr.values)]
    else:
        return None
    return True if True in states else None if None in states else False
