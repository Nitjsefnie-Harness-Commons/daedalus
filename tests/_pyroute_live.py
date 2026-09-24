"""Resolve deferred expression values against live flow state."""
import ast

from _pyroute_mapping import (_selected_values, apply_assignment_bindings)
from _pyroute_values import (UNPROVABLE_SENDER, DeferredAlternatives,
                             DeferredClass, DeferredContainer,
                             DeferredInstance, _known_value,
                             deferred_expression_value, is_deferred_value,
                             merge_yielded)

_LIVE_UNRESOLVED = object()


def _getattr_call(value, state):
    """The unbound keyword-free getattr call, or None."""
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
    position, so its positional list must be spliced rather than read
    plainly."""
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
    carries whatever the operand's container holds: a sender marker, a benign
    string (a genexp element stores a raw Constant), or nothing at all."""
    is_value, payload = entry
    if is_value:
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
        if parts:
            state.evaluated[id(value)] = merge_yielded([cached, *parts])


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
