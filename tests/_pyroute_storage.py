"""Shared storage updates for deferred aggregate values."""
from dataclasses import replace

from _pyroute_values import (DYNAMIC_KEY, DeferredContainer, merge_yielded,
                             sync_cells)


def dict_length(items, counted=True):
    return len(items) if counted and DYNAMIC_KEY not in items else None


def stale_after_store(owner, written=(), unreadable=False):
    """The keys a store leaves holding a value it may have replaced.

    An unreadable store folded in a source whose keys the model could not
    read, and such a source may name any key the container already held, so
    every entry it carried in is a candidate from there on. A key the store
    wrote from a value the model DID read is exact, and a key an earlier
    unreadable store retired stays retired unless this one wrote it. The
    unknown-key slot is not one of these: it names no key, and the read
    arms consult it themselves.
    """
    if unreadable:
        return (owner.stale | (set(owner.items) - {DYNAMIC_KEY})) \
            - set(written)
    return owner.stale - set(written)


def container_copy(owner, items, unknown_length=False, stale=None):
    """A copy of owner holding items; a dict's length is recounted, and
    the keys an earlier unreadable store retired carry over unless this
    copy says which of them are current again."""
    length = owner.length
    if owner.kind == 'dict':
        length = dict_length(
            items, owner.length is not None and not unknown_length)
    return DeferredContainer(items, length, owner.kind, owner.identity,
                             owner.star_display,
                             owner.stale if stale is None else stale)


def fold_dynamic(target, value):
    target[DYNAMIC_KEY] = merge_yielded(
        (target.get(DYNAMIC_KEY), value))


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
    elif hasattr(value, 'methods') and hasattr(value, 'identity'):
        methods = {key: _replace_value(item, identity, replacement, memo)
                   for key, item in value.methods.items()}
        updated = value if all(methods[key] is item
                               for key, item in value.methods.items()) \
            else replace(value, methods=methods)
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
        length = owner.length if owner.length == partner.length else None
        star = owner.star_display or partner.star_display
        if len(items) == len(owner.items) and length == owner.length \
                and star == owner.star_display:
            continue
        if joined is kept:
            joined = kept.copy()
        replace_deferred_storage(joined, owner, DeferredContainer(
            items, length, owner.kind, owner.identity, star, owner.stale))
        sync_cells(joined, {name})
    return joined
