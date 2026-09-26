"""The store path's write side: one store target, whatever form it takes.

A name, an instance attribute, a subscript, and the helper that writes a
container back. Nothing here reads the flow; each takes the container and the
state it must change, so the store path above it stays a dispatcher.
"""
import ast

from _pyroute_keys import _UNRESOLVED_KEY, _literal_key
from _pyroute_storage import (container_copy, fold_dynamic,
                              replace_deferred_storage)
from _pyroute_values import (UNPROVABLE_SENDER, DeferredContainer,
                             DeferredInstance, is_deferred_value, sync_cells)


def replace_container(state, owner_name, owner,
                      items, unknown_length=False):
    replacement = container_copy(owner, items, unknown_length)
    replace_deferred_storage(state, owner, replacement)
    sync_cells(state, {owner_name})


def store_deferred_target(
        target, value, state, removing=False, unknown_call=False):
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
    elif isinstance(target, ast.Attribute) and owner is None \
            and owner_name and is_deferred_value(value):
        # An attribute store on a base the model holds nothing for still
        # names a value, and the use site spells the same base and attribute.
        # Recording it against the base is what lets a later
        # `args.box.pop(0)` place the element it removes, rather than
        # dropping a value the model had and reading the call unproved.
        state.callables[owner_name] = DeferredInstance({target.attr: value})
        sync_cells(state, {owner_name})
    elif isinstance(target, ast.Subscript) \
            and isinstance(target.value, ast.Name):
        literal = _literal_key(target.slice, state)
        dynamic = literal is _UNRESOLVED_KEY
        if owner is None:
            if value is None and (removing or not unknown_call):
                return
            owner = DeferredContainer({}, None, 'dict', target)
            state.callables[owner_name] = owner
        elif not isinstance(owner, DeferredContainer):
            return
        items = dict(owner.items)
        mapping = owner.kind == 'dict'
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
