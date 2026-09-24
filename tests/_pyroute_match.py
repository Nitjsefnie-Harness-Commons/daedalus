"""Match-statement capture pairing for the Python routing guard.

A `match` case binds its capture names from the value the subject evaluates
to. That is the same pairing the assignment binder performs between a
destructuring target and a deferred container, so a case reuses it: a
sequence pattern is handed to `alias_target_pairs` as the assignment target
it is, and each capture is bound by `apply_assignment_bindings`. A pattern
that provably cannot match the subject leaves its names unpaired, and a name
bound to a plain value carries nothing. A form this binder does not model --
a class pattern, or a mapping key that is not a literal -- leaves its names
cleared and unpaired, the same as a capture the subject does not place: the
guard's convention is that an unmodelled capture is not a sender, so a call
through one is not reported.
"""
import ast

from _pyroute_mapping import alias_target_pairs, apply_assignment_bindings
from _pyroute_state import (FlowState, bind_alias_target, clear_names,
                            dedupe_states, rebound_names)
from _pyroute_values import DeferredContainer, _known_value


_STORE = ast.Store()
_LOAD = ast.Load()


def _name(identifier):
    return ast.Name(id=identifier, ctx=_STORE)


def _blank():
    return ast.Constant(value=None)


def _bind_name(name, value, state):
    apply_assignment_bindings([_name(name)], value, state, bind_alias_target)


def _bind_sequence(patterns, value, state):
    """Pair a sequence pattern's positions through the assignment binder's
    positional pairing, then recurse on each sub-pattern with the value at
    that position. The pairing target is positional only -- one element per
    pattern, a star element where the pattern has one -- so arity, the star
    and the remaining-items container come from the same rule the
    assignment binder uses. It returns None when the subject is not a
    sequence or its length cannot match, which is a case that cannot run."""
    elements: list[ast.expr] = [
        ast.Starred(value=_name(item.name)) if isinstance(
            item, ast.MatchStar) else _blank() for item in patterns]
    pairs = alias_target_pairs(ast.List(elts=elements, ctx=_LOAD), value)
    if pairs is None:
        return
    for item, (_, item_value) in zip(patterns, pairs):
        _bind_pattern(item, item_value, state)


def _bind_mapping(pattern, value, state):
    """Pair a mapping pattern's literal keys, and `**rest`, to the subject.
    A key the subject does not provably carry, or a subject that is not a
    mapping, cannot match, so the case leaves its names unpaired; a key that
    is not a literal names a value this binder does not model, so its
    captures are left cleared and unpaired."""
    if not all(isinstance(key, ast.Constant) for key in pattern.keys):
        return
    if not isinstance(value, DeferredContainer) or value.kind != 'dict':
        return
    keys = [key.value for key in pattern.keys]
    if any(key not in value.items for key in keys):
        return
    for key, sub in zip(keys, pattern.patterns):
        _bind_pattern(sub, value.items.get(key), state)
    if pattern.rest:
        rest = {key: item for key, item in value.items.items()
                if key not in keys}
        _bind_name(pattern.rest, DeferredContainer(
            rest, len(rest), 'dict', value.identity), state)


def _bind_pattern(pattern, value, state):
    if isinstance(pattern, ast.MatchSequence):
        _bind_sequence(pattern.patterns, value, state)
        return
    if isinstance(pattern, ast.MatchAs):
        if pattern.pattern is not None:
            _bind_pattern(pattern.pattern, value, state)
        if pattern.name is not None:
            _bind_name(pattern.name, value, state)
        return
    if isinstance(pattern, ast.MatchStar):
        _bind_name(pattern.name, value, state)
        return
    if isinstance(pattern, ast.MatchOr):
        for alternative in pattern.patterns:
            _bind_pattern(alternative, value, state)
        return
    if isinstance(pattern, ast.MatchMapping):
        _bind_mapping(pattern, value, state)
        return
    if isinstance(pattern, ast.MatchValue):
        return
    # A class pattern: its captures name attributes reached through the
    # subject's class, which this binder does not model, so it leaves them
    # cleared and unpaired.


def bind_match_case(case, subject, states):
    """Clear a case's capture names, then pair each from the value the
    subject carries."""
    pattern = case.pattern
    clear_names(states, rebound_names(pattern))
    for state in states:
        _bind_pattern(pattern, _known_value(subject, state), state)


def walk_match(statement, pairs, check_expression, walk):
    found = []
    pairs = check_expression(statement.subject, pairs)
    incoming = [FlowState.copy(pair) for pair in pairs]
    case_pairs = []
    for case in statement.cases:
        entered = [FlowState.copy(pair) for pair in incoming]
        bind_match_case(case, statement.subject, entered)
        if case.guard is not None:
            entered = check_expression(case.guard, entered)
        case_found, matched = walk(case.body, entered)
        found.extend(case_found)
        case_pairs.extend(matched)
    return found, dedupe_states([*incoming, *case_pairs])
