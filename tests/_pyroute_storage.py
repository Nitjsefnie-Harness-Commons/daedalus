"""Shared storage updates for deferred aggregate values."""
from dataclasses import replace


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
