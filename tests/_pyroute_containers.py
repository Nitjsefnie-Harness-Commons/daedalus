"""Container reshaping for the Python routing guard.

A materializer that preserves a container's element order and count yields a
deferred container the destructuring binder can pair by index. This builds
that container from a known ordered operand, whatever expression ordered it.
"""
from dataclasses import dataclass

from _pyroute_values import (DeferredAlternatives, DeferredContainer,
                             merge_yielded)


@dataclass(frozen=True)
class SpreadContainer(DeferredContainer):
    """A comprehension's result, read two ways: the item at each output
    index is the body's value for the producer's element at that index, but
    a consumer that walks a list or set result visits every element, so
    `iterated` carries the body's value over all of them. A dict
    comprehension is consumed through the generator path, not this one, so
    its result carries no `iterated`."""

    iterated: object = None


def iterated_key(node):
    """The evaluated slot holding a comprehension's `iterated` value beside
    its item value. A node's id is positive, so the negation cannot collide
    with the slot the node's own value occupies."""
    return -id(node)


def ordered_container(value, kind, order=None):
    """The container a reshaping operation yields from a known ordered
    container operand, or None when the operand is not one.

    `order` maps a length to the operand positions the result reads, in
    result order; without it the result keeps the operand's own order. The
    result is renumbered from zero, and a key the operand does not carry
    stays absent, so a missing item keeps its difference from an ordinary
    one. Alternatives recurse, and one branch that is not an ordered
    container refuses the whole value rather than dropping a sender on that
    path."""
    if isinstance(value, DeferredAlternatives):
        branches = [ordered_container(item, kind, order)
                    for item in value.values]
        if any(branch is None for branch in branches):
            return None
        return merge_yielded(branches)
    if (not isinstance(value, DeferredContainer)
            or value.kind not in ('tuple', 'list')
            or value.length is None):
        return None
    positions = range(value.length) if order is None else order(value.length)
    items = {index: value.items[position]
             for index, position in enumerate(positions)
             if position in value.items}
    return DeferredContainer(items, len(positions), kind)
