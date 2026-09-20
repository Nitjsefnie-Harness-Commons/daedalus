"""Shared storage updates for deferred aggregate values."""
from dataclasses import replace

from _pyroute_values import DeferredContainer, sync_cells


def _replace_value(value, identity, replacement, memo):
    cached = memo.get(id(value))
    if cached is not None:
        return cached
    if getattr(value, 'identity', None) is identity:
        updated = replacement
    elif hasattr(value, 'values'):
        values = tuple(_replace_value(
            item, identity, replacement, memo) for item in value.values)
        updated = value if all(left is right for left, right in zip(
            values, value.values)) else replace(value, values=values)
    elif hasattr(value, 'items') and hasattr(value, 'identity'):
        items = {key: _replace_value(item, identity, replacement, memo)
                 for key, item in value.items.items()}
        updated = value if all(items[key] is item
                               for key, item in value.items.items()) \
            else replace(value, items=items)
    elif hasattr(value, 'attributes') and hasattr(value, 'identity'):
        attributes = {
            key: _replace_value(item, identity, replacement, memo)
            for key, item in value.attributes.items()}
        updated = value if all(attributes[key] is item
                               for key, item in value.attributes.items()) \
            else replace(value, attributes=attributes)
    elif hasattr(value, 'yielded'):
        yielded = _replace_value(value.yielded, identity, replacement, memo)
        updated = value if yielded is value.yielded \
            else replace(value, yielded=yielded)
    else:
        updated = value
    memo[id(value)] = updated
    return updated


def replace_deferred_storage(state, owner, replacement):
    memo = {}

    def update(value):
        return _replace_value(value, owner.identity, replacement, memo)
    state.callables = {name: update(value)
                       for name, value in state.callables.items()}
    state.evaluated = {key: update(value)
                       for key, value in state.evaluated.items()}
    state.generators = {name: update(value)
                        for name, value in state.generators.items()}
    for key, binding in list(state.cells.values.items()):
        deferred = update(binding.deferred)
        generator = update(binding.generator)
        if deferred is not binding.deferred \
                or generator is not binding.generator:
            state.cells.values[key] = replace(
                binding, deferred=deferred, generator=generator)


def join_clean_occupancy(kept, other):
    """Join two states that differ only in which keys ordinary values occupy.

    A key missing on either path is missing after the join, so setdefault,
    get and pop bind their deferred default as the path lacking the key
    does. The kept state is left untouched: other lists may still hold it."""
    joined = kept
    for name, owner in kept.callables.items():
        if not isinstance(owner, DeferredContainer):
            continue
        partner = other.callables[name]
        items = {key: item for key, item in owner.items.items()
                 if item is not None or key in partner.items}
        if len(items) == len(owner.items):
            continue
        if joined is kept:
            joined = kept.copy()
        replace_deferred_storage(joined, owner, DeferredContainer(
            items, owner.length, owner.kind, owner.identity))
        sync_cells(joined, {name})
    return joined
