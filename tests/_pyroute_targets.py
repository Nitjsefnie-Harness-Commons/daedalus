"""Target-pairing helpers for the Python routing guard.

A materializer and a context manager are two statement shapes that reach the
same place: a value that may carry a routed callable is bound to a
destructuring target. Both pair through the assignment binder, so the callable
a container carries is not collapsed when it arrives through either shape.
"""
from _pyroute_containers import ordered_container
from _pyroute_mapping import apply_assignment_bindings
from _pyroute_state import (bind_alias_target, bind_builtin_names, bound_names,
                            discard_state_dict, evaluated_value,
                            resolve_sender_name, sync_cells)
from _pyroute_values import (DeferredCallable, DeferredInstance, _known_value,
                             merge_yielded)


def materialized_order(consumer, argument, states):
    """The container an order-preserving materializer yields from a known
    ordered operand, or None. list and tuple re-kinder such a container
    without reordering or dropping an element, so the result keeps that
    container's indices; the other eager consumers reorder or deduplicate, and
    their result is not a positional pairing."""
    if consumer not in ('list', 'tuple'):
        return None
    return ordered_container(
        merge_yielded(_known_value(argument, state) for state in states),
        consumer)


def enter_result(context_expr, state, analyze, violations):
    """The value a deferred context manager's __enter__ yields, or None. The
    method is walked for that value only, so a send inside it is reported by
    the exposure sweep, not from here."""
    context = evaluated_value(context_expr, state)
    if not isinstance(context, DeferredInstance):
        return None
    method = context.attributes.get('__enter__')
    if not isinstance(method, DeferredCallable):
        return None
    mark = len(violations)
    _, returned = analyze(method, [state])
    del violations[mark:]
    return returned


def bind_with_target(item, state, analyze, violations):
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
    entered_value = enter_result(
        item.context_expr, state, analyze, violations)
    if entered_value is not None:
        apply_assignment_bindings(
            [item.optional_vars], entered_value, state, bind_alias_target)
