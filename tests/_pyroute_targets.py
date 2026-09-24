"""Target-pairing helpers for the Python routing guard.

A materializer and a context manager are two statement shapes that reach the
same place: a value that may carry a routed callable is bound to a
destructuring target. Both pair through the assignment binder, so the callable
a container carries is not collapsed when it arrives through either shape.
"""
import ast

from _pyroute_containers import iterated_key, ordered_container
from _pyroute_live import clear_expression_cache
from _pyroute_mapping import apply_assignment_bindings
from _pyroute_state import (bind_alias_target, bind_builtin_names, bound_names,
                            discard_state_dict, evaluated_value, literal_truth,
                            resolve_sender_name, sync_cells)
from _pyroute_positions import bind_deferred_target
from _pyroute_values import (DYNAMIC_KEY, DeferredAlternatives,
                             DeferredCallable, DeferredContainer,
                             DeferredInstance, _known_value, merge_yielded)


_UNPROVABLE = object()


def materialized_order(consumer, argument, states):
    """The container an order-preserving materializer yields from a known
    ordered operand, or None. list and tuple re-kind such a container without
    reordering or dropping an element, so the result keeps that container's
    indices. Every other eager consumer either reorders or deduplicates the
    operand, reduces it to the one item max and min select or to the number
    sum adds, or -- as dict does -- projects each item into a key; none of
    those is a positional pairing of the operand's own elements."""
    if consumer not in ('list', 'tuple'):
        return None
    return ordered_container(
        merge_yielded(_known_value(argument, state) for state in states),
        consumer)


def enter_result(context_expr, state, analyze):
    """The value a deferred context manager's __enter__ yields, or None. The
    method is walked for that value, and whatever the walk reports is kept: a
    send inside __enter__ runs when the with statement does, and nothing else
    in the guard reaches the code behind an instance attribute."""
    context = evaluated_value(context_expr, state)
    if not isinstance(context, DeferredInstance):
        return None
    method = context.attributes.get('__enter__')
    if not isinstance(method, DeferredCallable):
        return None
    _, returned = analyze(method, [state])
    return returned


def probe_comprehension(node, active, skipped, copy_state, check, violations):
    """Check a comprehension's body into the states, then record what the
    comprehension yields at output index 0 when that index is provable, and
    return the states the body produced. A comprehension does not reorder --
    a filter selects which elements appear, never their order -- so the
    element at output index 0 is the iterable's element 0 whenever a filter
    provably keeps it there, and the merge of every element belongs only to a
    consumer that walks the result. A filter that may drop it, and an operand
    whose branches do not agree on it, both leave the position unprovable:
    the merge stands there, so the position is reported rather than read as
    though it were one element."""
    results = [node.key, node.value] if isinstance(
        node, ast.DictComp) else [node.elt]
    for result in results:
        active = check(result, active)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp)):
        for state in [*skipped, *active]:
            state.evaluated.pop(iterated_key(node), None)
        comprehension_first(
            node, results, active, copy_state, check, violations)
    return active


def _constant_truth(condition):
    """The truth of a filter decided by its own literals, or None when it is
    not decidable. literal_truth covers the literal forms it models; a `not`
    over one of them is decided here. A true result also says the filter
    reads no name, call, attribute or subscript, because none of those is a
    literal form and each would leave the truth undecidable."""
    truth = literal_truth(condition)
    if truth is not None or not isinstance(condition, ast.UnaryOp):
        return truth
    if not isinstance(condition.op, ast.Not):
        return None
    operand = _constant_truth(condition.operand)
    return None if operand is None else not operand


def _filter_inert(condition):
    """Whether a filter provably keeps the element at that position. A
    filter decided by its own literals keeps the element when that truth is
    true -- `if True`, `if not False`, `if 1`. A bare name is a truthiness
    test, and the guard holds no falsy value at one: a target carries a
    deferred element, and a deferred value is a callable, a container, an
    instance or a class, all of them truthy. Every other filter compares,
    resolves or calls on the element, and whether it drops the element is
    not modelled here."""
    if isinstance(condition, ast.Name):
        return True
    return _constant_truth(condition) is True


def _filters_inert(node):
    """A filter that may drop the element that would land at output index 0
    puts a later one there, so the position is unprovable."""
    return all(_filter_inert(condition) for generator in node.generators
               for condition in generator.ifs)


def _first_element(value):
    """The producer's element at output index 0 when the operand proves it,
    or _UNPROVABLE. Alternatives bind only when every branch is a tuple or
    list of known length with no unknown-key slot, and they agree on that
    element: a union of the
    branches is not an agreement, so a disagreement -- and a branch that
    proves no element at all -- leaves the position unprovable."""
    if isinstance(value, DeferredContainer):
        if value.kind in ('tuple', 'list') and value.length \
                and DYNAMIC_KEY not in value.items:
            return value.items.get(0)
        return _UNPROVABLE
    if isinstance(value, DeferredAlternatives):
        elements = [_first_element(branch) for branch in value.values]
        if elements and all(item is elements[0] for item in elements):
            return elements[0]
    return _UNPROVABLE


def comprehension_first(node, results, states, copy_state, check, violations):
    """Record what a comprehension yields at output index 0 and what a loop
    over it yields, or change nothing and return False when the first is not
    the iterable's own element 0.

    A comprehension does not reorder: its element at output index i is the
    producer's element at index i, whatever order the producer's model
    carries. Each generator's own binding is the merge of every element,
    which is what the body must see, but the value that lands at output
    index 0 is the body applied to the first element of every iterable in
    turn -- so a probe binds each target to that element and reads the result
    there, with the copied state's expression cache cleared so the probe
    reads its own bindings. The binding holds only when the element at that
    index is determinable on both counts: the operand has to yield one, and
    no filter may drop it. Where either is not determinable the position
    keeps the merge the primary run already made, which is what a consumer
    that reads one position then sees. A consumer that walks the result
    visits every element, so it must still see that merge too: it arrives
    as the container's `iterated` on the narrowed path, and as the item
    value itself where the position was not narrowed, because the two are
    the same merge. The probe's own findings are a narrowing of that merge,
    so they are discarded."""
    value_node = node.value if isinstance(node, ast.DictComp) else node.elt
    if not _filters_inert(node):
        return False
    for state in states:
        merged = state.evaluated.get(id(value_node))
        mark = len(violations)
        probe = copy_state(state)
        outputs = [probe]
        for generator in node.generators:
            clear_expression_cache(generator.iter, outputs)
            outputs = check(generator.iter, outputs)
            if len(outputs) != 1:
                return False
            element = _first_element(
                evaluated_value(generator.iter, outputs[0]))
            if element is _UNPROVABLE:
                return False
            clear_expression_cache(generator.target, outputs)
            bind_deferred_target(generator.target, element, outputs[0])
        for result in results:
            clear_expression_cache(result, outputs)
            outputs = check(result, outputs)
        del violations[mark:]
        state.evaluated[id(value_node)] = merge_yielded(
            output.evaluated.get(id(value_node)) for output in outputs)
        state.evaluated[iterated_key(node)] = merged
    return True


def bind_with_target(item, state, analyze):
    """Clear a `with ... as` target, then pair what __enter__ yields to it the
    way the assignment binder pairs its right-hand side."""
    names = bound_names(item.optional_vars)
    resolved = resolve_sender_name(item.context_expr, state.aliases)
    for name in names:
        discard_state_dict(state, name)
        state.aliases.pop(name, None)
        state.generators.pop(name, None)
        state.callables.pop(name, None)
        state.bound.add(name)
        bind_builtin_names(state, {name})
        if resolved is not None:
            state.aliases[name] = resolved
    sync_cells(state, names)
    entered_value = enter_result(item.context_expr, state, analyze)
    if entered_value is not None:
        apply_assignment_bindings(
            [item.optional_vars], entered_value, state, bind_alias_target)
