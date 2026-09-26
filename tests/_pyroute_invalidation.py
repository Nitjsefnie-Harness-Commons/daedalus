"""One invalidation rule for in-place mutation the model does not follow.

The model records a tracked container's positions, its keys and its length. A
mutation it follows updates them; a mutation it does not follow leaves them
standing, so a later read answers with a fact from before the mutation and the
guard reads clean on a shape it must report. So every in-place mutation the
model does not follow -- any method of a list, dict or set other than the ones
that only read, reached by any spelling, through any receiver -- drops the
recorded facts, and every later read of that container fails closed.

The rule is one rule rather than one per mutator, so what counts as a mutation
comes from the containers' own surfaces instead of from the spellings a filing
happened to use: a name in `list`, `dict` or `set` that is not a reader mutates
in place, and a method a future Python adds is a mutation until someone proves
otherwise.
"""
import ast

from _pyroute_reads import _attribute_selection, _receiver_value
from _pyroute_storage import replace_deferred_storage
from _pyroute_values import (DYNAMIC_KEY, DeferredAlternatives,
                             DeferredContainer, DeferredGenerator,
                             DeferredMethod, _known_value, merge_yielded,
                             sync_cells)

# The names a container surface carries that only read: the non-assigning
# operators and their reflected forms, the read protocol, and the named
# readers. Everything else in that surface mutates in place.
_READERS = frozenset({
    '__add__', '__and__', '__class_getitem__', '__contains__', '__doc__',
    '__eq__', '__ge__', '__getattribute__', '__getitem__', '__gt__',
    '__hash__', '__iter__', '__le__', '__len__', '__lt__', '__mul__', '__ne__',
    '__new__', '__or__', '__rand__', '__reduce__', '__repr__', '__reversed__',
    '__ror__', '__rmul__', '__rsub__', '__rxor__', '__sizeof__', '__sub__',
    '__xor__', 'copy', 'count', 'difference', 'fromkeys', 'get', 'index',
    'intersection', 'isdisjoint', 'issubset', 'issuperset', 'items', 'keys',
    'symmetric_difference', 'union', 'values',
})


def _mutating_surface(kind):
    return frozenset(name for name in vars(kind) if name not in _READERS)


CONTAINER_MUTATORS = (_mutating_surface(list) | _mutating_surface(dict)
                      | _mutating_surface(set))

_NESTED_SCOPES = (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef,
                  ast.ClassDef)
_SEQUENCE_KINDS = ('list', 'tuple', 'set')
# The container types the surface was taken from. A call through one of these
# names is the type, not an instance of it, so its first argument is the
# receiver the bound form writes as `func.value`.
_CONTAINER_TYPES = frozenset({'dict', 'list', 'set'})


def _own_nodes(statement):
    """The statement's own nodes. A nested scope is walked by its own flow
    against its own state, so descending into one would invalidate a
    container this statement never reached."""
    pending = [statement]
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, _NESTED_SCOPES):
            pending.extend(ast.iter_child_nodes(node))


def _containers(value):
    if isinstance(value, DeferredContainer):
        return (value,)
    if isinstance(value, DeferredAlternatives):
        return tuple(found for nested in value.values
                     for found in _containers(nested))
    return ()


def _mutated(call, state):
    """The tracked containers a call may mutate in place.

    The operation is a method invoked on a container, so the spelling decides
    only where the container is named: as the receiver of a bound call, as the
    first argument of an unbound one, behind another call, or in a name the
    model bound the method to.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        return _bound_mutation(call, func, state)
    if isinstance(func, ast.Call):
        return _indirect_mutation(call, func, state)
    return _held_mutation(func, state)


def _dunder_form(name):
    """`operator`'s spelling of a container method without the underscores --
    `setitem` for `__setitem__` -- or None when the name is not one. This is
    the same surface read a second way, not a list of what `operator` exports.
    """
    for spelling in (f'__{name}__', f'__{name}', f'{name}__'):
        if spelling in CONTAINER_MUTATORS:
            return spelling
    return None


def _unbound_callee(receiver, state):
    """Whether the callee is the container type rather than an instance of
    one, so `list.pop(x, 0)` names the container as an argument. An instance's
    own argument is an operand instead: `obj.append(x)` mutates `obj`.
    """
    return (isinstance(receiver, ast.Name)
            and receiver.id in _CONTAINER_TYPES
            and receiver.id not in state.bound)


def _bound_mutation(call, func, state):
    """A method called on the container it names, or on the one its first
    argument names when the callee is the container type."""
    if func.attr in CONTAINER_MUTATORS:
        unbound = _unbound_callee(func.value, state)
    elif _dunder_form(func.attr) is None:
        return ()
    else:
        # The operation is in the name, so the first argument is the
        # container however the callee itself is spelled.
        unbound = True
    found = _containers(_receiver_value(func.value, state))
    if found or not (unbound and call.args):
        return found
    return _containers(_receiver_value(call.args[0], state))


def _indirect_mutation(call, func, state):
    """A call whose callee is itself a call: a method selected into a name, a
    `functools.partial` of one, or an `operator` factory. The model cannot see
    through any of them, so every tracked container the operands name is a
    candidate the read must fail closed over."""
    selected = _attribute_selection(func, state)
    if selected is not None:
        receiver, name = selected
        if name is None or name in CONTAINER_MUTATORS:
            return _containers(_receiver_value(receiver, state))
    operands = [*func.args, *(keyword.value for keyword in func.keywords),
                *call.args, *(keyword.value for keyword in call.keywords)]
    return tuple(found for operand in operands
                 for found in _containers(_receiver_value(operand, state)))


def _held_mutation(func, state):
    """A method the model bound to a name: the binding records the container
    the call through that name mutates."""
    value = _known_value(func, state)
    return (value.owner,) if isinstance(value, DeferredMethod) else ()


def _store_targets(statement):
    """The targets a statement stores into or deletes. A tuple target nests,
    and `x[0:1], y = v` parses as one tuple target rather than two."""
    if isinstance(statement, (ast.Assign, ast.Delete)):
        return list(_flatten_targets(statement.targets))
    if isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        return list(_flatten_targets([statement.target]))
    return []


def _flatten_targets(targets):
    for target in targets:
        if isinstance(target, (ast.Tuple, ast.List)):
            yield from _flatten_targets(target.elts)
        else:
            yield target


def _stored(statement, state):
    """The sequences a store or delete moves, which the model cannot follow.

    A store at one position writes no position the model recorded and adds no
    length, so it leaves every recorded position where it was -- whether the
    index is literal or computed, the computed one landing in the unknown
    slot. Everything else moves what the container holds at those positions:
    a delete of any index, a slice store, and an augmented assignment. The
    store path claims the one augmented form it applies itself, so a mapping
    union keeps its precise merge and everything else is this rule's.
    """
    if isinstance(statement, ast.AugAssign):
        owner = _receiver_value(statement.target, state)
        return [owner] if (isinstance(owner, DeferredContainer)
                           and owner.kind in _SEQUENCE_KINDS) else []
    removing = isinstance(statement, ast.Delete)
    found = []
    for target in _store_targets(statement):
        if not isinstance(target, ast.Subscript):
            continue
        if not removing and not isinstance(target.slice, ast.Slice):
            continue
        owner = _receiver_value(target.value, state)
        if (isinstance(owner, DeferredContainer)
                and owner.kind in _SEQUENCE_KINDS):
            found.append(owner)
    return found


def _item(value):
    """The deferred values one operand carries, reaching through a container
    to what it holds: what is appended lands in the container it is appended
    to, so the value a later read can reach is what the operand held.

    A generator is not among them. What a list takes from one is each value
    the generator yields, and the generator itself is consumed as it goes --
    so folding the object in would put a value whose own signature changes
    with every step into the container's, and the state it belongs to could
    never merge with the one before it."""
    if isinstance(value, DeferredGenerator):
        return [] if value.yielded is None else [value.yielded]
    if isinstance(value, DeferredContainer):
        return list(value.items.values())
    if isinstance(value, DeferredAlternatives):
        return [found for nested in value.values for found in _item(nested)]
    return [value]


def _operands(node, state):
    """The deferred values a mutation was handed, which may land in the
    container it mutates: the call's own arguments, or the value a store
    writes. This is how a mutation leaves a value the model had recorded
    nowhere -- appending a callable to a list the model held no tracked item
    for is exactly the case an invalidation of recorded facts cannot reach."""
    if isinstance(node, ast.Call):
        return [_known_value(argument, state) for argument in node.args] + [
            _known_value(keyword.value, state)
            for keyword in node.keywords]
    value = getattr(node, 'value', None)
    return [] if value is None else [_known_value(value, state)]


def _invalidate(state, container, operands=()):
    """Drop every recorded fact of one container.

    Its values join the unknown slot and its count becomes unknown, so a
    later read answers with everything the container could hold rather than
    with one position from before the mutation. The identity is kept, so an
    alias bound to the same container is invalidated with it.

    `star_display` is deliberately left at its default: the join above has
    already made the shifted-position rule a no-op on this container, so
    setting it would say nothing the value does not. A plant that sets it
    anyway is caught -- by `test_tab_routing`, by this branch's own suite,
    and by `test_tab_routing_sequence_reads` -- so the field is pinned, not
    merely inert.
    """
    names = {name for name, value in state.callables.items()
             if isinstance(value, DeferredContainer)
             and value.identity is container.identity}
    joined = merge_yielded([
        *container.items.values(),
        *(item for value in operands for item in _item(value))])
    replacement = DeferredContainer(
        {} if joined is None else {DYNAMIC_KEY: joined}, None,
        container.kind, container.identity)
    replace_deferred_storage(state, container, replacement)
    sync_cells(state, names)


def invalidate_unmodelled(statement, state, claimed=()):
    """Drop the facts of every tracked container this statement mutates in
    place by a path the model does not follow.

    `claimed` names the calls and statements a precise handler already
    applied, so a mutation the model followed keeps its exact result. Every
    other mutation fails closed: the container's recorded values join the
    unknown slot and its count becomes unknown, so a later read answers with
    everything the container could hold rather than with one position from
    before the mutation.
    """
    claimed = set(claimed)
    done = set()
    for node in _own_nodes(statement):
        if id(node) in claimed:
            continue
        if isinstance(node, ast.Call):
            found = _mutated(node, state)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.Delete,
                               ast.AugAssign)):
            found = _stored(node, state)
        else:
            continue
        for container in found:
            if id(container) in done:
                continue
            done.add(id(container))
            _invalidate(state, container, _operands(node, state))
