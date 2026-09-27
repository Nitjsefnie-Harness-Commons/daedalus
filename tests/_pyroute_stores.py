"""The store path's write side: one store target, whatever form it takes.

A name, an instance attribute, a subscript, and the helper that writes a
container back. Nothing here reads the flow; each takes the container and the
state it must change, so the store path above it stays a dispatcher.
"""
import ast
from collections.abc import Hashable
from typing import cast

from _pyroute_keys import _UNRESOLVED_KEY, _literal_key
from _pyroute_positions import position_key
from _pyroute_reads import _receiver_value
from _pyroute_storage import (container_copy, fold_dynamic,
                              replace_deferred_storage)
from _pyroute_values import (UNPROVABLE_SENDER, DeferredClass,
                             DeferredContainer, DeferredInstance,
                             is_deferred_value, sync_cells)

# Kinds whose instances take `x[k] = v`; any other kind refuses the store.
_ASSIGNS_BY_INDEX = ('list', 'dict')


def _rebuilt(state, owner, replacement, owner_name):
    replace_deferred_storage(state, owner, replacement)
    if owner_name:
        sync_cells(state, {owner_name})


def replace_container(state, owner_name, owner,
                      items, unknown_length=False):
    _rebuilt(state, owner, container_copy(owner, items, unknown_length),
             owner_name)


def replace_slots(state, owner_name, owner, slots):
    _rebuilt(state, owner, DeferredInstance(slots, owner.identity)
             if isinstance(owner, DeferredInstance)
             else DeferredClass(slots), owner_name)


def clear_owner(state, owner_name, owner):
    """Empty a tracked container, wherever the model reaches it from."""
    _rebuilt(state, owner, DeferredContainer(
        {}, 0, owner.kind, owner.identity), owner_name)


def root_name(node):
    """The name a receiver expression is rooted at, or None when it has none.

    Deferred storage is keyed by name, so this is the handle a store through
    any other spelling of the receiver still has."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def base_owner(base, state):
    """The name an expression writes through and the value it holds.

    A base spelled as a name is its own handle; any other writes through the
    name at its root, the only name the model's storage offers."""
    if isinstance(base, ast.Name):
        return base.id, state.callables.get(base.id)
    return root_name(base), _receiver_value(base, state)


def _seed_receiver(state, base, value):
    """Create the aggregate a receiver names and the model holds nothing for,
    so a store through it has a slot to write into. False when there is none.

    The aggregate is created against the receiver's own base, not against the
    name at its root: a root name bound to an instance names a different
    thing than the attribute or subscript spelled on it."""
    if not isinstance(base, (ast.Attribute, ast.Subscript)):
        return False
    owner = _receiver_value(base.value, state)
    name = root_name(base)
    if isinstance(base, ast.Attribute) \
            and isinstance(owner, (DeferredInstance, DeferredClass)):
        held = owner.attributes if isinstance(owner, DeferredInstance) \
            else owner.methods
        replace_slots(state, name, owner, {**held, base.attr: value})
        return True
    if isinstance(base, ast.Subscript) \
            and isinstance(owner, DeferredContainer):
        key = _literal_key(base.slice, state)
        if key is not _UNRESOLVED_KEY:
            replace_container(state, name, owner,
                              {**owner.items, cast(Hashable, key): value})
            return True
    return False


def _store_attribute(state, target, value, owner, owner_name):
    if isinstance(owner, DeferredInstance):
        slots = owner.attributes
    elif isinstance(owner, DeferredClass):
        slots = owner.methods
    else:
        return False
    if value is None:
        slots = {key: item for key, item in slots.items()
                 if key != target.attr}
    else:
        slots = {**slots, target.attr: value}
    replace_slots(state, owner_name, owner, slots)
    return True


def _subscript_store(state, target, value, owner, owner_name, removing,
                     unknown_call):
    literal = _literal_key(target.slice, state)
    dynamic = literal is _UNRESOLVED_KEY
    if owner is None:
        if value is None and (removing or not unknown_call):
            return
        owner = DeferredContainer({}, None, 'dict', target)
        state.callables[owner_name] = owner
    elif not isinstance(owner, DeferredContainer):
        return
    elif owner.kind not in _ASSIGNS_BY_INDEX:
        # No item assignment, so the store never happens in either
        # sign - the same answer a list store past the end gets.
        return
    items = dict(owner.items)
    mapping = owner.kind == 'dict'
    if not mapping and not dynamic:
        # A negative key counts from the end, so it names a position
        # rather than the key it is spelled as. The `not mapping` gate
        # is the whole dict exemption: a key on a mapping is a key.
        placed = position_key(owner, literal)
        if placed is None:
            # The runtime raises on a key past the start, so the
            # store never happens and the container stays as it is.
            if owner.length is not None:
                return
            # An unknown length names no position, so the value joins
            # the unknown-key slot every positional read consults.
            dynamic, literal = True, _UNRESOLVED_KEY
        else:
            literal = placed
    if dynamic and not removing:
        if value is not None or unknown_call:
            fold_dynamic(
                items,
                UNPROVABLE_SENDER if value is None else value)
        elif not mapping:
            return
    elif value is not None:
        items[literal] = value
    elif unknown_call:
        if items.get(literal) is None:
            items[literal] = UNPROVABLE_SENDER
    elif dynamic:
        if not mapping:
            return
    elif removing:
        items.pop(literal, None)
    else:
        items[literal] = None
    # A computed key may add or remove an entry: the count is unknown.
    replace_container(
        state, owner_name, owner, items,
        unknown_length=dynamic and mapping)


def store_deferred_target(
        target, value, state, removing=False, unknown_call=False):
    base = getattr(target, 'value', None)
    owner_name, owner = base_owner(base, state)
    if isinstance(target, ast.Attribute):
        if _store_attribute(state, target, value, owner, owner_name):
            return
        if owner is None and owner_name and is_deferred_value(value):
            # An attribute store on a base the model holds nothing for still
            # names a value, and the use site spells the same base and
            # attribute. Recording it against the base is what lets a later
            # `args.box.pop(0)` place the element it removes, rather than
            # dropping a value the model had and reading the call unproved.
            state.callables[owner_name] = DeferredInstance(
                {target.attr: value})
            sync_cells(state, {owner_name})
        else:
            _seed_receiver(state, base, value)
        return
    if not isinstance(target, ast.Subscript):
        return
    if isinstance(base, ast.Name) or owner is not None:
        _subscript_store(state, target, value, owner, owner_name, removing,
                         unknown_call)
    elif _seed_receiver(
            state, base, DeferredContainer({}, None, 'dict', target)):
        _subscript_store(state, target, value, _receiver_value(base, state),
                         root_name(base), removing, unknown_call)
