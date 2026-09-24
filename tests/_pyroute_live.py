"""Resolve deferred expression values against live flow state."""
import ast

from _pyroute_mapping import (_selected_values, apply_assignment_bindings,
                              resolve_expression_value)
from _pyroute_values import (UNPROVABLE_SENDER, DeferredAlternatives,
                             DeferredCallable, DeferredClass,
                             DeferredContainer, DeferredInstance, _known_value,
                             deferred_expression_value, is_deferred_value,
                             merge_yielded, reachable_callables)

_LIVE_UNRESOLVED = object()
# In-place list/tuple methods the model does not fold back into the tracked
# container, so a measured length no longer matches the real one. Methods that
# only read (count, index, copy) leave the length provable and must not match.
LIST_LENGTH_MUTATIONS = frozenset(
    {'append', 'extend', 'insert', 'remove', 'pop', 'clear'})


def _mutation_receiver(node, state):
    """The tracked list/tuple a statement mutates in place, or None. Covers the
    three forms the model does not fold back (daedalus issue 990): a
    length-changing method call, a slice assignment, and an augmented assign.
    A subscript store only counts when its slice can resize the container: a
    slice assignment may change the length; an index store is length-neutral.
    Subscript deletion is not covered: the model folds it through its own
    store path, so a length-changing index del is a separate (unhandled)
    surface, not one of the forms claimed here."""
    target = None
    if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr in LIST_LENGTH_MUTATIONS):
        target = node.value.func.value
    elif isinstance(node, ast.AugAssign):
        target = node.target
    else:
        subs = [t for t in (node.targets if isinstance(node, ast.Assign)
                            else [node.target]
                            if isinstance(node, ast.AnnAssign) else [])
                if isinstance(t, ast.Subscript)
                and isinstance(t.slice, ast.Slice)]
        if len(subs) == 1: target = subs[0].value
    if not isinstance(target, ast.Name):
        return None, None
    tracked = state.callables.get(target.id)
    if not (isinstance(tracked, DeferredContainer)
            and tracked.kind in ('list', 'tuple')
            and tracked.length is not None):
        return None, None
    return target.id, tracked


def invalidate_mutated_length(node, state):
    """A list/tuple mutated in place by a form the model does not fold back has
    no provable length, so a later operand read of any name bound to that same
    container leaves the call's arity unprovable rather than a stale single
    fact (daedalus issue 990). Invalidation is by container identity across
    every bound name, so an alias mutated through another name is covered too.

    Returns ``(name, lengthless)`` for an augmented assign, whose name the
    caller re-binds after the generic rebind path drops it, so the operand read
    still fails closed with the maker in reach; other forms return None."""
    name, tracked = _mutation_receiver(node, state)
    if tracked is None:
        return None
    lengthless = DeferredContainer(
        tracked.items, None, tracked.kind, tracked.identity)
    for bound, value in list(state.callables.items()):
        if (isinstance(value, DeferredContainer)
                and value.identity is tracked.identity):
            state.callables[bound] = lengthless
    return (name, lengthless) if isinstance(node, ast.AugAssign) else None


def _getattr_call(value, state):
    """The single getattr shape gate the plain and spliced arms share."""
    if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == 'getattr'
            and not value.keywords
            and value.func.id not in state.bound):
        return None
    return value


def _plain_getattr(value, state):
    """The unbound getattr call of arity 2 or 3, or None."""
    call = _getattr_call(value, state)
    return call if call is not None and len(call.args) in (2, 3) else None


def _constant_getattr(value, state):
    """The constant attribute name of a plain getattr call, or None."""
    call = _plain_getattr(value, state)
    if call is None or not isinstance(call.args[1], ast.Constant):
        return None
    return call.args[1].value if isinstance(call.args[1].value,
                                            str) else None


def _attribute_values(owner):
    """Every deferred value an owner carries, for a name that cannot be
    resolved to one attribute."""
    if isinstance(owner, DeferredAlternatives):
        return [item for value in owner.values
                for item in _attribute_values(value)]
    if isinstance(owner, DeferredInstance):
        return list(owner.attributes.values())
    if isinstance(owner, DeferredClass):
        return list(owner.methods.values())
    return []


def _select(owner, name, has_default, default):
    """The value a getattr selects from an owner, a provable string name (or
    None for a name the model cannot read) and an optional default."""
    if name is not None:
        selected = merge_yielded(_selected_values(owner, name,
                                                  attribute=True))
        if selected is not None:
            return selected
    else:
        selected = merge_yielded(_attribute_values(owner))
    return merge_yielded((selected, default)) if has_default else selected


def _has_starred_arg(value, state):
    """Whether an unbound getattr call fills an argument in a starred
    position."""
    call = _getattr_call(value, state)
    return call is not None and any(
        isinstance(arg, ast.Starred) for arg in call.args)


def _expand_starred_args(value, state):
    """The getattr call's positional arguments flattened into (is_value,
    payload) entries, splicing every starred operand the model can prove.
    None when a starred operand resolves to no provable element list."""
    args = []
    for arg in value.args:
        if not isinstance(arg, ast.Starred):
            args.append((False, arg))
            continue
        operand = _known_value(arg.value, state)
        if not (isinstance(operand, DeferredContainer)
                and operand.kind in ('tuple', 'list')
                and operand.length is not None):
            return None
        args.extend((True, operand.items.get(index))
                    for index in range(operand.length))
    return args


def _argument_value(entry, state):
    is_value, payload = entry
    return payload if is_value else _known_value(payload, state)


def _argument_name(entry):
    """The string constant an argument position names, or None when the
    position is not a provable string (a dynamic name). A spliced position
    carries whatever its container holds: a deferred value, a raw Constant of
    any type, a raw sender alias, the normalised UNPROVABLE marker, or
    nothing. A raw alias is read as the name; the marker is refused."""
    is_value, payload = entry
    if is_value:
        # Value compare, not identity: _pyroute_state defines its own
        # equal-but-distinct UNPROVABLE_SENDER, so `is not` would read a
        # normalised marker as a name.
        return (payload if isinstance(payload, str)
                and payload != UNPROVABLE_SENDER else None)
    if isinstance(payload, ast.Constant) and isinstance(payload.value, str):
        return payload.value
    return None


def _starred_selection(value, state):
    """The value a getattr call with a starred argument selects, or
    UNPROVABLE_SENDER when a starred operand's elements cannot be proved.

    Each starred operand is spliced into the positional argument list, so the
    owner, name and default are read as if written plainly. A spliced name
    that is not a readable string joins the dynamic-name arm (every value the
    owner carries). A starred operand resolving to no provable element list
    hides the call's arity, so no position can be read and the whole selection
    stays unprovable."""
    args = _expand_starred_args(value, state)
    if args is None:
        return UNPROVABLE_SENDER
    if len(args) not in (2, 3):
        return None
    owner = _argument_value(args[0], state)
    name = _argument_name(args[1])
    has_default = len(args) == 3
    default = _argument_value(args[2], state) if has_default else None
    return _select(owner, name, has_default, default)


def _selection_value(value, state):
    """The value a plain getattr call resolves to, within what the model can
    prove: the named attribute when it resolves to a tracked value, else the
    default in the 3-argument form; every value the owner carries when the name
    is not a provable string constant.

    A starred argument is spliced into the positional list before the same
    selection runs (daedalus issue 977). The model records no occupancy, so an
    untracked attribute reads as absent and the default is selected (daedalus
    issue 978). A non-string constant name cannot match the model's string
    keys, so it resolves to nothing."""
    if _has_starred_arg(value, state):
        return _starred_selection(value, state)
    call = _plain_getattr(value, state)
    if call is None:
        return None
    name = _constant_getattr(value, state)
    if name is None and isinstance(call.args[1], ast.Constant):
        return None
    has_default = len(call.args) == 3
    default = _known_value(call.args[2], state) if has_default else None
    return _select(_known_value(call.args[0], state), name, has_default,
                   default)


def _generator_operand_yields(node, state):
    """The deferred values a generator-function operand yields, the element
    list the call splices. A generator function's yields live in its body, not
    in the call's own subtree, so without this the carriers reach only the
    function itself and a maker carried by a yield is lost. The genexp
    spelling needs no help: its iterable is in the call's own subtree."""
    for child in ast.walk(node):
        function = state.callables.get(child.id) if isinstance(
            child, ast.Name) else None
        if not (isinstance(function, DeferredCallable)
                and isinstance(function.scope, ast.FunctionDef)):
            continue
        for part in ast.walk(function.scope):
            if not isinstance(part, (ast.Yield, ast.YieldFrom)):
                continue
            if part.value is not None:
                yield deferred_expression_value(
                    part.value, function.state, lambda *_: None)


def seed_selection_value(value, state):
    """Seed the evaluated cache so an unprovable selection never reads
    clean: a getattr call resolves to the value it selects, and a
    maybe-sender keeps the deferred values it carries."""
    cached = state.evaluated.get(id(value))
    selected = _selection_value(value, state)
    if selected is not None:
        state.evaluated[id(value)] = selected
        cached = selected
    if cached == UNPROVABLE_SENDER:
        parts = [item for item in (_known_value(child, state)
                                   for child in ast.walk(value))
                 if is_deferred_value(item)]
        parts += [item for item in _generator_operand_yields(value, state)
                  if is_deferred_value(item)]
        if parts:
            carriers = (reachable_callables(DeferredAlternatives(tuple(parts)))
                        if selected == UNPROVABLE_SENDER else ())
            state.evaluated[id(value)] = merge_yielded(
                [cached, *parts, *carriers])


def seed_unprovable_selection(value, state):
    """Bind a selection the model cannot resolve to the deferred callables its
    operands carry, so every route that binds the result to a name keeps a
    callable to follow at a later call through it. Only the fail-closed
    verdict is widened: a provable selection is left to the ordinary resolver,
    so a selection the model reads cleanly keeps reading cleanly."""
    if _selection_value(value, state) != UNPROVABLE_SENDER:
        return
    seed_selection_value(value, state)


def seed_then_resolve(node, state, generator_factory, sender_resolver,
                      unprovable_sender):
    """Widen an unprovable selection's carriers, then resolve the expression
    the ordinary way. Every expression flows through here, so a selection the
    model cannot resolve keeps a callable to follow however it is later bound.
    The three forwarded parameters are named, not packed through ``*args``, so
    the checker resolves this return as ``resolve_expression_value``'s declared
    type rather than an opaque unpack; that inference otherwise reaches the
    latent diagnostics in ``_pyroute_mapping`` (daedalus issue 990).
    """
    seed_unprovable_selection(node, state)
    return resolve_expression_value(node, state, generator_factory,
                                    sender_resolver, unprovable_sender)


def bind_alias_statement(node, state, binder):
    """Resolve a selection's value first, then bind the statement's aliases."""
    seed_selection_value(node.value, state)
    targets = (node.targets if isinstance(node, ast.Assign)
               else [node.target])
    apply_assignment_bindings(targets, node.value, state, binder)


def clear_expression_cache(node, states):
    """Clear an expression tree's cache and return its root."""
    keys = {id(item) for item in ast.walk(node)}
    for state in states:
        for key in keys:
            state.evaluated.pop(key, None)
    return node


def live_expression_value(node, state, lambda_factory, captured):
    evaluated = state.evaluated
    cached = evaluated.get(id(node), _LIVE_UNRESOLVED)
    state.evaluated = {key: value for key, value in evaluated.items()
                       if key != id(node)}
    try:
        value = deferred_expression_value(
            node, state, lambda item, current:
            lambda_factory(item, current, captured))
    finally:
        state.evaluated = evaluated
    resolved = (value is not None or isinstance(node, ast.Constant)
                or (isinstance(node, ast.Name) and node.id in state.bound
                    and node.id not in state.aliases))
    if resolved: evaluated[id(node)] = value
    elif cached is not _LIVE_UNRESOLVED: evaluated[id(node)] = cached
    else: evaluated.pop(id(node), None)
    return value if resolved else evaluated.get(id(node), _LIVE_UNRESOLVED)
