"""Deferred values and expression helpers for Python routing flow."""
import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _pyroute_state import FlowState

EAGER_ITERABLE_CALLS = frozenset({
    'dict', 'frozenset', 'list', 'max', 'min', 'set', 'sorted', 'sum', 'tuple',
})
PARTIAL_ITERABLE_CALLS = frozenset({'all', 'any', 'next'})


@dataclass(frozen=True)
class CellBinding:
    alias: object = None
    generator: object = None
    deferred: object = None
    payload: object = None
    bound: bool = False


@dataclass
class CellState:
    origins: dict = field(default_factory=dict)
    values: dict = field(default_factory=dict)

    def copy(self):
        return CellState(dict(self.origins), dict(self.values))

    def update(self, other):
        self.values.update(other.values)

    def without(self, names):
        return CellState({name: key for name, key in self.origins.items()
                          if name not in names}, self.values)


@dataclass(frozen=True)
class DeferredGenerator:
    """A generator expression and its known remaining yield count."""

    expression: ast.GeneratorExp
    remaining: int | None
    evaluate_zero: bool = False
    yielded: object = None
    state: 'FlowState' = None
    captured: frozenset = frozenset()
    origin: int = 0
    closure: CellState = field(default=None, compare=False, repr=False)

    def __post_init__(self):
        if self.state is not None:
            _register_closure(self)


@dataclass(frozen=True)
class DeferredCallable:
    """A callable body and its definition-time lexical state."""

    scope: ast.AST
    state: 'FlowState'
    locals: frozenset
    captured: frozenset = frozenset()
    origin: int = 0
    closure: CellState = field(default=None, compare=False, repr=False)

    def __post_init__(self):
        _register_closure(self)


@dataclass(frozen=True)
class DeferredClass:
    """Methods belonging to one statically known class object."""

    methods: dict


@dataclass(frozen=True)
class DeferredAlternatives:
    """Deferred values selected by one unresolved expression."""

    values: tuple


@dataclass(frozen=True)
class DeferredContainer:
    """Statically known deferred values stored by key or index."""

    items: dict
    length: int | None = None
    kind: str = 'list'
    identity: object = field(default_factory=object, compare=False,
                             repr=False)


@dataclass(frozen=True)
class DeferredInstance:
    """Deferred attributes on one locally constructed instance."""

    attributes: dict
    identity: object = field(default_factory=object, compare=False,
                             repr=False)


DEFERRED_VALUES = (DeferredGenerator, DeferredCallable, DeferredClass,
                   DeferredAlternatives, DeferredContainer,
                   DeferredInstance)


def is_deferred_value(value):
    return isinstance(value, DEFERRED_VALUES)


def new_deferred_callable(node, state, definition, local_names, captured):
    return DeferredCallable(
        node, definition, frozenset(local_names), frozenset(captured),
        state.namespace, state.cells)


def _binding_from_state(state, name):
    payload = state.dicts.get(name)
    return CellBinding(
        state.aliases.get(name), state.generators.get(name),
        state.callables.get(name), payload.copy() if payload is not None
        else None, name in state.bound)


def _register_closure(deferred):
    closure = deferred.closure or deferred.state.cells
    for name in deferred.captured:
        key = closure.origins.setdefault(name, (deferred.origin, name))
        binding = _binding_from_state(deferred.state, name)
        closure.values.setdefault(key, binding)
        deferred.state.cells.origins[name] = key
        deferred.state.cells.values.setdefault(key, binding)


def _apply_binding(state, name, key, binding):
    state.aliases.pop(name, None)
    state.generators.pop(name, None)
    state.callables.pop(name, None)
    state.dicts.pop(name, None)
    origin = state.dict_origins.pop(name, None)
    if origin is not None:
        state.dict_namespaces.setdefault(origin, {}).pop(name, None)
    if binding.alias is not None:
        state.aliases[name] = binding.alias
    if binding.generator is not None:
        state.generators[name] = binding.generator
    if binding.deferred is not None:
        state.callables[name] = binding.deferred
    if binding.payload is not None:
        payload = binding.payload.copy()
        state.dicts[name] = payload
        state.dict_origins[name] = key[0]
        state.dict_namespaces.setdefault(key[0], {})[name] = payload.copy()
    if binding.bound:
        state.bound.add(name)
    else:
        state.bound.discard(name)


def sync_cells(state, names):
    for name in names:
        key = state.cells.origins.get(name)
        if key is not None:
            state.cells.values[key] = _binding_from_state(state, name)


def load_callable_cells(deferred, caller, entry):
    for name in deferred.captured:
        key = deferred.state.cells.origins.get(name)
        if key is None:
            continue
        binding = caller.cells.values.get(
            key, deferred.state.cells.values.get(key))
        if binding is None:
            continue
        entry.cells.origins[name] = key
        entry.cells.values[key] = binding
        _apply_binding(entry, name, key, binding)


def merge_cell_states(states):
    if len(states) == 1:
        return states[0].cells
    origins = {}
    names = {name for state in states for name in state.cells.origins}
    for name in names:
        keys = [state.cells.origins.get(name) for state in states]
        if keys[0] is not None and all(key == keys[0] for key in keys):
            origins[name] = keys[0]
    values = {}
    for key in set(origins.values()):
        bindings = [state.cells.values.get(key) for state in states]
        if all(_binding_signature(item) == _binding_signature(bindings[0])
               for item in bindings):
            values[key] = bindings[0]
    return CellState(origins, values)


def _binding_signature(binding):
    if binding is None:
        return None
    generator = binding.generator
    generator_key = (generator.expression.lineno,
                     generator.expression.col_offset,
                     generator.remaining, generator.evaluate_zero) \
        if isinstance(generator, DeferredGenerator) else None
    payload = (tuple(sorted(
        (key, value[0], id(value[1]))
        for key, value in binding.payload.items()))
        if binding.payload is not None else None)
    return (binding.alias, generator_key,
            id(binding.deferred) if binding.deferred is not None else None,
            payload, binding.bound)


def cell_state_signature(cells):
    return (tuple(sorted(cells.origins.items())),
            tuple(sorted((key, _binding_signature(binding))
                         for key, binding in cells.values.items())))


def advance_generator(state, generator):
    if generator.remaining is None:
        return
    remaining = max(0, generator.remaining - 1)
    advanced = DeferredGenerator(
        generator.expression, remaining, generator.evaluate_zero,
        generator.yielded, generator.state, generator.captured,
        generator.origin, generator.closure)
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


def generator_for(expr, state):
    if isinstance(expr, ast.Name):
        return state.generators.get(expr.id)
    value = state.evaluated.get(id(expr))
    return value if isinstance(value, DeferredGenerator) else None


def generator_nonempty(generator, literal_nonempty):
    if generator.remaining is not None:
        return generator.remaining > 0
    expression = generator.expression
    states = [literal_nonempty(clause.iter)
              for clause in expression.generators]
    if False in states:
        return False
    if all(state is True for state in states) and not any(
            clause.ifs for clause in expression.generators):
        return True
    return None


def iterable_nonempty(expr, states, literal_nonempty):
    values = []
    for state in states:
        generator = generator_for(expr, state)
        values.append(generator_nonempty(generator, literal_nonempty)
                      if generator else literal_nonempty(expr))
    return (values[0] if values
            and all(value is values[0] for value in values) else None)


def exhaust_generators(expressions, states, dedupe):
    for state in states:
        state.generators = {
            name: value for name, value in state.generators.items()
            if value.expression not in expressions}
    return dedupe(states)


def merge_deferred_values(values):
    merged = []
    for value in values:
        nested = (value.values if isinstance(value, DeferredAlternatives)
                  else (value,))
        for item in nested:
            if is_deferred_value(item) and all(
                    item is not found for found in merged):
                merged.append(item)
    if not merged:
        return None
    return merged[0] if len(merged) == 1 else DeferredAlternatives(
        tuple(merged))


def callable_candidates(value):
    if isinstance(value, DeferredCallable):
        return (value,)
    if isinstance(value, DeferredAlternatives):
        return tuple(candidate for item in value.values
                     for candidate in callable_candidates(item))
    return ()


def reachable_callables(value):
    if isinstance(value, DeferredCallable):
        return (value,)
    if isinstance(value, DeferredAlternatives):
        values = value.values
    if isinstance(value, DeferredContainer):
        values = value.items.values()
    if isinstance(value, DeferredInstance):
        values = value.attributes.values()
    if isinstance(value, DeferredClass):
        values = value.methods.values()
    if isinstance(value, DeferredGenerator):
        values = (value.yielded,)
    if not isinstance(value, (DeferredAlternatives, DeferredContainer,
                              DeferredInstance, DeferredClass,
                              DeferredGenerator)):
        return ()
    return tuple(candidate for item in values
                 for candidate in reachable_callables(item))


def contains_callable(value, target):
    return any(candidate is target for candidate in reachable_callables(value))


def exposed_callables(states):
    exposed = {}
    for state in states:
        for value in state.callables.values():
            for deferred in reachable_callables(value):
                exposed.setdefault(
                    id(deferred), (deferred, []))[1].append(state)
    return exposed.values()


def _known_value(node, state):
    value = state.evaluated.get(id(node))
    if value is None and isinstance(node, ast.Name):
        value = state.callables.get(node.id)
    return value if is_deferred_value(value) else None


def deferred_expression_value(node, state, lambda_factory):
    if node is None:
        return None
    if isinstance(node, ast.Lambda):
        return lambda_factory(node, state)
    value = _known_value(node, state)
    if value is not None:
        return value
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [deferred_expression_value(item, state, lambda_factory)
                  for item in node.elts]
        if any(is_deferred_value(value) for value in values):
            kind = type(node).__name__.lower()
            return DeferredContainer(
                dict(enumerate(values)), len(values), kind)
    if isinstance(node, ast.Dict):
        items = {}
        for key, item in zip(node.keys, node.values):
            known_key = deferred_expression_value(key, state, lambda_factory)
            value = deferred_expression_value(item, state, lambda_factory)
            if known_key is not None and is_deferred_value(value):
                items[known_key] = value
        if items:
            return DeferredContainer(items, len(node.values), 'dict')
    return None


def live_expression_value(node, state, lambda_factory, captured):
    evaluated = state.evaluated
    state.evaluated = {key: value for key, value in evaluated.items()
                       if key != id(node)}
    try:
        return deferred_expression_value(
            node, state, lambda item, current:
            lambda_factory(item, current, captured))
    finally:
        state.evaluated = evaluated


def new_deferred_generator(node, state, captured, lambda_factory,
                           generator_factory):
    yielded = deferred_expression_value(node.elt, state, lambda_factory)
    generator = generator_factory(node, yielded)
    return DeferredGenerator(
        node, generator.remaining, generator.evaluate_zero, yielded, state,
        frozenset(captured), state.namespace, state.cells)


def generator_context(generator, caller, chain, copy_state, overlay):
    if generator.state is None or generator.captured <= chain:
        return copy_state(caller), frozenset(), frozenset(), False
    blocked = chain - generator.captured
    keep = blocked | (generator.captured - chain)
    entry = overlay(copy_state(generator.state), caller, keep, blocked)
    load_callable_cells(generator, caller, entry)
    return entry, keep, blocked, True


def bind_deferred_target(target, value, state):
    names = {node.id for node in ast.walk(target)
             if isinstance(node, ast.Name)}
    for name in names:
        state.aliases.pop(name, None)
        state.generators.pop(name, None)
        state.callables.pop(name, None)
        state.bound.add(name)
    if isinstance(target, ast.Name):
        if isinstance(value, DeferredGenerator):
            state.generators[target.id] = value
        elif is_deferred_value(value):
            state.callables[target.id] = value
    elif isinstance(target, (ast.Tuple, ast.List)) \
            and isinstance(value, DeferredContainer):
        for index, nested in enumerate(target.elts):
            bind_deferred_target(nested, value.items.get(index), state)
    sync_cells(state, names)


def bind_deferred_states(target, value, states):
    for state in states:
        bind_deferred_target(target, value, state)


def append_deferred(values, value):
    if value is not None:
        values.append(value)


def consumer_results(consumer, arguments, states):
    if consumer != 'iter' or not arguments:
        return []
    value = merge_deferred_values(
        _known_value(arguments[0], state) for state in states)
    return [value] if value is not None else []


def materialize_deferred(consumer, value):
    if value is None:
        return None
    if isinstance(value, DeferredAlternatives):
        return merge_deferred_values(
            materialize_deferred(consumer, item) for item in value.values)
    if consumer in ('max', 'min'):
        return value
    if consumer == 'sum':
        return None
    if consumer == 'dict' and isinstance(value, DeferredContainer):
        if value.kind not in ('list', 'tuple') or value.length != 2:
            return None
        key = value.items.get(0)
        item = value.items.get(1)
        if not is_deferred_value(item):
            return None
        try:
            hash(key)
        except TypeError:
            return None
        return DeferredContainer({key: item}, 1, 'dict')
    kind = 'list' if consumer == 'sorted' else consumer
    return DeferredContainer({0: value}, 1, kind)


def iterable_deferred(value):
    if isinstance(value, DeferredAlternatives):
        return merge_deferred_values(
            iterable_deferred(item) for item in value.values)
    if isinstance(value, DeferredContainer):
        if value.kind == 'dict':
            return merge_deferred_values(value.items.keys())
        return merge_deferred_values(value.items.values())
    return None


def _selected_values(value, key, attribute=False):
    if isinstance(value, DeferredAlternatives):
        return [selected for item in value.values
                for selected in _selected_values(item, key, attribute)]
    if attribute and isinstance(value, DeferredInstance):
        return [value.attributes.get(key)]
    if attribute and isinstance(value, DeferredClass):
        return [value.methods.get(key)]
    if not attribute and isinstance(value, DeferredContainer):
        return [value.items.get(key)]
    return []


def _display_value(node, state):
    items = {}
    index = 0
    for item in node.elts:
        value = _known_value(item.value if isinstance(item, ast.Starred)
                             else item, state)
        if isinstance(item, ast.Starred):
            if isinstance(value, DeferredContainer):
                for nested_index in range(value.length or 0):
                    nested = value.items.get(nested_index)
                    if nested is not None:
                        items[index + nested_index] = nested
                index += value.length or 0
            else:
                return merge_deferred_values(items.values())
        else:
            if value is not None:
                items[index] = value
            index += 1
    if items or isinstance(node, ast.List):
        return DeferredContainer(items, index, type(node).__name__.lower())
    return None


def _dict_value(node, state):
    items = {}
    for key, item in zip(node.keys, node.values):
        value = _known_value(item, state)
        if key is not None and isinstance(key, ast.Constant) \
                and value is not None:
            items[key.value] = value
    return DeferredContainer(
        items, len(node.values), 'dict') if items else None


def expression_value(node, state, generator_factory, sender_resolver,
                     unprovable_sender):
    known = _known_value(node, state)
    if known is not None:
        return known
    if isinstance(node, ast.GeneratorExp):
        return generator_factory(node)
    if isinstance(node, ast.Name) and node.id in state.generators:
        return state.generators[node.id]
    if isinstance(node, ast.Name) and node.id in state.callables:
        return state.callables[node.id]
    if isinstance(node, (ast.NamedExpr, ast.Starred)):
        value = state.evaluated.get(id(node.value))
        if value is not None:
            return value
    if isinstance(node, (ast.IfExp, ast.BoolOp)):
        value = merge_deferred_values(
            state.evaluated.get(id(child))
            for child in ast.iter_child_nodes(node))
        if value is not None:
            return value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        value = _display_value(node, state)
        if value is not None:
            return value
        values = [state.evaluated.get(id(item)) for item in node.elts]
        if any(item is not None and not isinstance(item, DeferredGenerator)
               for item in values):
            return unprovable_sender
    if isinstance(node, ast.Dict):
        return _dict_value(node, state) or sender_resolver(
            node, state.aliases)
    if isinstance(node, (ast.ListComp, ast.SetComp)):
        value = state.evaluated.get(id(node.elt))
        if is_deferred_value(value):
            return DeferredContainer(
                {0: value}, 1, type(node).__name__[:-4].lower())
    if isinstance(node, ast.Subscript):
        owner = _known_value(node.value, state)
        key = (node.slice.value
               if isinstance(node.slice, ast.Constant) else None)
        value = merge_deferred_values(_selected_values(owner, key))
        if value is not None:
            return value
    if isinstance(node, ast.Attribute):
        owner = _known_value(node.value, state)
        value = merge_deferred_values(
            _selected_values(owner, node.attr, attribute=True))
        if value is not None:
            return value
    if isinstance(node, ast.Call):
        owner = _known_value(node.func, state)
        if isinstance(owner, DeferredClass):
            return DeferredInstance(dict(owner.methods))
    return sender_resolver(node, state.aliases)


def expression_callables(node, state):
    values = [_known_value(child, state) for child in ast.walk(node)]
    return tuple(candidate for value in values if value is not None
                 for candidate in callable_candidates(value))


def follow_callable_call(candidates, arguments, states, call, analyze,
                         copy_states, dedupe_states):
    returned = []
    if candidates:
        invoked = []
        for candidate in candidates:
            branch, value = analyze(candidate, copy_states(states), call)
            invoked.extend(branch)
            if value is not None:
                returned.append(value)
        return dedupe_states(invoked), merge_deferred_values(returned)
    callbacks = {id(candidate): candidate
                 for argument in arguments for state in states
                 for candidate in expression_callables(argument, state)}
    for callback in callbacks.values():
        states, _ = analyze(callback, states)
    return states, None


def bind_call_arguments(deferred, call, caller, entry, sender_resolver):
    args = deferred.scope.args
    positional = [*args.posonlyargs, *args.args]
    if isinstance(call.func, ast.Attribute) and positional:
        positional = positional[1:]
    expressions = list(zip(positional, call.args))
    by_name = {parameter.arg: parameter
               for parameter in [*positional, *args.kwonlyargs]}
    expressions.extend((by_name[keyword.arg], keyword.value)
                       for keyword in call.keywords
                       if keyword.arg in by_name)
    literals = []
    for parameter, expression in expressions:
        name = parameter.arg
        value = _known_value(expression, caller)
        if value is not None:
            entry.callables[name] = value
        sender = sender_resolver(expression, caller.aliases)
        if sender is not None:
            entry.aliases[name] = sender
        if isinstance(expression, ast.Constant):
            literals.append((name, bool(expression.value)))
    if literals:
        assigned = {node.id for node in ast.walk(deferred.scope)
                    if isinstance(node, ast.Name) and isinstance(
                        node.ctx, (ast.Store, ast.Del))}
        literals = [item for item in literals if item[0] not in assigned]
    return tuple(literals)


def store_deferred_value(statement, state):
    # pylint: disable-next=import-outside-toplevel
    from _pyroute_storage import replace_deferred_storage
    if isinstance(statement, ast.Expr) \
            and isinstance(statement.value, ast.Call):
        call = statement.value
        owner_name = getattr(getattr(call.func, 'value', None), 'id', None)
        owner = state.callables.get(owner_name)
        if getattr(call.func, 'attr', None) == 'clear' \
                and isinstance(owner, DeferredContainer):
            replacement = DeferredContainer(
                {}, 0, owner.kind, owner.identity)
            replace_deferred_storage(state, owner, replacement)
            sync_cells(state, {owner_name})
        return
    if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Delete)):
        return
    value = (_known_value(statement.value, state)
             if not isinstance(statement, ast.Delete) else None)
    targets = (statement.targets if isinstance(statement, ast.Assign)
               else [statement.target] if not isinstance(
                   statement, ast.Delete) else statement.targets)
    for target in targets:
        owner_name = getattr(getattr(target, 'value', None), 'id', None)
        owner = state.callables.get(owner_name)
        if isinstance(target, ast.Attribute) \
                and isinstance(owner, DeferredInstance):
            attributes = dict(owner.attributes)
            if value is None:
                attributes.pop(target.attr, None)
            else:
                attributes[target.attr] = value
            replacement = DeferredInstance(attributes, owner.identity)
            replace_deferred_storage(state, owner, replacement)
            sync_cells(state, {owner_name})
        elif isinstance(target, ast.Subscript) \
                and isinstance(owner, DeferredContainer) \
                and isinstance(target.slice, ast.Constant):
            items = dict(owner.items)
            if value is None:
                items.pop(target.slice.value, None)
            else:
                items[target.slice.value] = value
            replacement = DeferredContainer(
                items, owner.length, owner.kind, owner.identity)
            replace_deferred_storage(state, owner, replacement)
            sync_cells(state, {owner_name})


def payload_key(dicts):
    return tuple(sorted(
        (name, frozenset((key, value[0], id(value[1]))
                         for key, value in keys.items()))
        for name, keys in dicts.items()))
