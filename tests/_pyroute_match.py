"""Match-statement capture pairing for the Python routing guard.

A `match` case binds its capture names from the value the subject evaluates
to. That is the same pairing the assignment binder performs between a
destructuring target and a deferred container, so a case reuses it: a
sequence pattern is handed to `alias_target_pairs` as the assignment target
it is, and each capture is bound by `apply_assignment_bindings`. Where the
rule's answer is "this position is undecidable", the capture is marked
unprovable rather than left unpaired, so a call through one is reported.

An or-pattern offers every alternative the same subject; a name the
alternatives disagree on is the merge of their values, so differing
reachable states become unprovable (never last-write-wins). A mapping
pattern pairs a resolvable literal key to the subject's value under it, a
merge subject pairs to the merge of its branches, and a key the guard cannot
resolve leaves the slot unprovable rather than silent. A pattern that
provably cannot match the subject (a sequence of the wrong arity, a value
pattern over a deferred subject) leaves its names unpaired. A class pattern
or a value pattern that binds nothing leaves its names cleared and unpaired:
that is this arm's measured limitation, tracked in issue 951, and the
fail-closed alternative false-positives on
`test_destructured_and_walrus_alias_boundaries`.
"""
import ast

from _pyroute_mapping import (_UNRESOLVED_KEY, alias_target_pairs,
                              apply_assignment_bindings, _literal_key)
from _pyroute_state import (UNPROVABLE_SENDER, FlowState, bind_alias_target,
                            clear_names, dedupe_states, rebound_names)
from _pyroute_values import (DeferredAlternatives, DeferredContainer,
                             _known_value, is_deferred_value, merge_yielded)


_STORE = ast.Store()
_LOAD = ast.Load()
_VALUE_TESTS = (ast.MatchValue, ast.MatchSingleton)


def _name(identifier):
    return ast.Name(id=identifier, ctx=_STORE)


def _blank():
    return ast.Constant(value=None)


def _unprovable(pattern, state):
    for name in rebound_names(pattern):
        state.aliases[name] = UNPROVABLE_SENDER


def _bind_name(name, value, state):
    apply_assignment_bindings([_name(name)], value, state, bind_alias_target)


def _merge_bind(pattern, values, state):
    """Bind a pattern once per value and keep, for every name, the merge of
    what each value put there. Differing reachable states become unprovable;
    identical ones collapse to the single value."""
    per_name = {}
    for value in values:
        probe = state.copy()
        _bind_pattern(pattern, value, probe)
        for name in rebound_names(pattern):
            bound = probe.callables.get(name)
            if bound is None:
                bound = probe.aliases.get(name)
            per_name.setdefault(name, []).append(bound)
    for name, bound in per_name.items():
        _bind_name(name, merge_yielded(bound), state)


def _bind_sequence(patterns, value, state):
    """Pair a sequence pattern's positions through the assignment binder's
    positional pairing, then recurse on each sub-pattern with the value at
    that position. The pairing target is positional only -- one element per
    pattern, a star element where the pattern has one, a placeholder where
    the pattern's star is bare -- so arity, the star, the remaining-items
    container and the per-position unprovable marker all come from the same
    rule the assignment binder uses. It returns None when the subject is not
    a sequence or its length cannot match, which is a case that cannot run."""
    elements: list[ast.expr] = []
    for item in patterns:
        if isinstance(item, ast.MatchStar):
            inner = _name(item.name) if item.name else _blank()
            elements.append(ast.Starred(value=inner))
        else:
            elements.append(_blank())
    pairs = alias_target_pairs(ast.List(elts=elements, ctx=_LOAD), value)
    if pairs is None:
        return
    for item, (_, item_value) in zip(patterns, pairs):
        _bind_pattern(item, item_value, state)


def _mapping_branches(value):
    if isinstance(value, DeferredAlternatives):
        return list(value.values)
    return [value]


def _bind_mapping(pattern, value, state):
    """Pair a mapping pattern's keys, and `**rest`, to the subject. A
    resolvable literal key pairs to the value the subject carries under it,
    across a merge subject; a key the guard cannot resolve leaves the slot
    unprovable; a subject that is not a mapping, or a key it does not carry,
    cannot match, so the case leaves its names unpaired."""
    branches = _mapping_branches(value)
    usable = all(isinstance(branch, DeferredContainer)
                 and branch.kind == 'dict' for branch in branches)
    for key, sub in zip(pattern.keys, pattern.patterns):
        literal = _literal_key(key, state)
        if literal is _UNRESOLVED_KEY:
            _unprovable(sub, state)
            continue
        if not usable:
            continue
        holders = [branch.items.get(literal) for branch in branches
                   if literal in branch.items]
        if not holders:
            continue  # no branch carries the key; the case cannot match
        _merge_bind(sub, holders, state)
    if pattern.rest and usable:
        rests = []
        for branch in branches:
            rest = {k: item for k, item in branch.items.items()
                    if all(k != _literal_key(key, state)
                           for key in pattern.keys)}
            rests.append(DeferredContainer(rest, len(rest), 'dict',
                                           branch.identity))
        _merge_bind(ast.MatchAs(name=pattern.rest), rests, state)


def _bind_or(pattern, value, state):
    per_name = {}
    for alternative in pattern.patterns:
        probe = state.copy()
        _bind_pattern(alternative, value, probe)
        for name in rebound_names(alternative):
            bound = probe.callables.get(name)
            if bound is None:
                bound = probe.aliases.get(name)
            per_name.setdefault(name, []).append(bound)
    for name, bound in per_name.items():
        _bind_name(name, merge_yielded(bound), state)


def _bind_pattern(pattern, value, state):
    if isinstance(pattern, ast.MatchSequence):
        _bind_sequence(pattern.patterns, value, state)
        return
    if isinstance(pattern, ast.MatchAs):
        if pattern.pattern is not None:
            if isinstance(pattern.pattern, _VALUE_TESTS) and is_deferred_value(
                    value):
                return  # the subject cannot equal this literal
            _bind_pattern(pattern.pattern, value, state)
        if pattern.name is not None:
            _bind_name(pattern.name, value, state)
        return
    if isinstance(pattern, ast.MatchStar):
        if pattern.name is not None:
            _bind_name(pattern.name, value, state)
        return
    if isinstance(pattern, ast.MatchOr):
        _bind_or(pattern, value, state)
        return
    if isinstance(pattern, ast.MatchMapping):
        _bind_mapping(pattern, value, state)
        return
    if isinstance(pattern, ast.MatchValue):
        return
    # A class pattern (ast.MatchClass) or any future pattern node: this
    # binder does not model it, so its captures stay cleared and unpaired.


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
