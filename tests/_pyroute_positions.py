"""Positional reads of deferred sequence containers.

An int key is an exact position from the start and the DYNAMIC_KEY slot may
sit at any position from the container's exact prefix on. A read that cannot name one exact position, or that
meets an unknown length it would need, joins every item: it never answers
with a clean subset.
"""
import ast

from _pyroute_keys import _literal_key
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,
                             DeferredAlternatives, DeferredContainer,
                             DeferredGenerator, _known_value,
                             is_deferred_value, merge_yielded, sender_value,
                             sync_cells)

_SEQUENCE_METHODS = ('pop', 'copy', '__getitem__')


def at_position(container, index):
    """Values a container may hold at position or key index."""
    items = container.items
    if container.kind == 'dict':
        return [items.get(index), items.get(DYNAMIC_KEY)]
    if not isinstance(index, int) or (
            index < 0 and container.length is None):
        return list(items.values())
    values = [items.get(index)]
    if index < 0:
        index += container.length
        values.append(items.get(index))
    if index >= container.exact_prefix:
        values.append(items.get(DYNAMIC_KEY))
    return values


def from_position(container, start):
    """The join of every value a container may hold at position start or
    later."""
    return merge_yielded(
        item for key, item in container.items.items()
        if key is DYNAMIC_KEY or key >= start)


def alias_target_pairs(target, value):
    if isinstance(value, DeferredAlternatives):
        branches = [alias_target_pairs(target, item) for item in value.values]
        unknown = [UNPROVABLE_SENDER] if None in branches else []
        branches = [branch for branch in branches if branch is not None]
        if not branches:
            return None
        paired = []
        for index, (nested, _) in enumerate(branches[0]):
            values = [branch[index][1] for branch in branches]
            paired.append((nested, merge_yielded(values + unknown)))
        return paired
    if isinstance(value, DeferredContainer):
        if value.kind not in ('tuple', 'list', 'set'):
            return None
        if value.length is None:
            return _unknown_length_pairs(target, value)
        items = [merge_yielded(at_position(value, index))
                 for index in range(value.length)]
    elif isinstance(value, (ast.Tuple, ast.List)):
        items = value.elts
    else:
        return None
    stars = [index for index, item in enumerate(target.elts)
             if isinstance(item, ast.Starred)]
    if not stars:
        return list(zip(target.elts, items)) \
            if len(target.elts) == len(items) else None
    if len(stars) != 1 or len(items) < len(target.elts) - 1:
        return None
    star = stars[0]
    suffix = len(target.elts) - star - 1
    end = len(items) - suffix
    pairs = [*zip(target.elts[:star], items[:star])]
    if isinstance(value, DeferredContainer):
        rest = items[star:end]
        pairs.append((target.elts[star].value, DeferredContainer(
            dict(enumerate(rest)), len(rest), 'list')))
    pairs.extend(zip(target.elts[star + 1:], items[end:]))
    return pairs


def _unknown_length_pairs(target, value):
    """A target before the star reads its own position; the star and every
    target after it read the open tail from the star's position."""
    start = next((index for index, item in enumerate(target.elts)
                  if isinstance(item, ast.Starred)), len(target.elts))
    tail = from_position(value, start)
    rest = DeferredContainer(
        {} if tail is None else {DYNAMIC_KEY: tail}, None, 'list')
    pairs = []
    for index, item in enumerate(target.elts):
        if index < start:
            pairs.append((item, merge_yielded(at_position(value, index))))
        elif isinstance(item, ast.Starred):
            pairs.append((item.value, rest))
        else:
            pairs.append((item, tail))
    return pairs


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
        sender = sender_value(value)
        if sender is not None: state.aliases[target.id] = sender
    elif isinstance(target, (ast.Tuple, ast.List)):
        for nested, item in alias_target_pairs(target, value) or ():
            bind_deferred_target(nested, item, state)
    sync_cells(state, names)


def bind_deferred_states(target, value, states):
    for state in states:
        bind_deferred_target(target, value, state)


def materialize_deferred(consumer, value, node=None):
    """A consumer that reorders, deduplicates or projects its operand puts
    any item at any position of an unknown count."""
    if value is None: return None
    if isinstance(value, DeferredAlternatives):
        return merge_yielded(
            materialize_deferred(consumer, item, node)
            for item in value.values)
    if consumer in ('max', 'min'):
        return value
    if consumer == 'sum':
        return None
    if consumer == 'dict' and isinstance(value, DeferredContainer):
        if value.kind not in ('list', 'tuple') or value.length not in (
                2, None):
            return None
        exact = value.length == 2 and DYNAMIC_KEY not in value.items
        key = value.items.get(0) if exact else None
        item = merge_yielded(at_position(value, 1))
        if not (is_deferred_value(item) or sender_value(item) is not None):
            return None
        return DeferredContainer(
            {DYNAMIC_KEY if key is None else key: item}, 1, 'dict', node)
    kind = 'list' if consumer == 'sorted' else consumer
    return DeferredContainer({DYNAMIC_KEY: value}, None, kind)


def sequence_method_value(node, state):
    """What pop, copy or __getitem__ reads from a known sequence, or
    None."""
    if not isinstance(node.func, ast.Attribute) \
            or node.func.attr not in _SEQUENCE_METHODS:
        return None
    owner = _known_value(node.func.value, state)
    if not isinstance(owner, DeferredContainer) or owner.kind == 'dict':
        return None
    if node.func.attr == 'copy':
        return DeferredContainer(dict(owner.items), owner.length, owner.kind,
                                 node, owner.exact_prefix)
    index = _literal_key(node.args[0], state) if node.args else -1
    return merge_yielded(at_position(owner, index))
