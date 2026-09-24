"""Match-statement capture pairing for the Python routing guard.

A `match` case binds its capture names from the value the subject evaluates
to, through the assignment binder's own pairing: a sequence pattern is
handed to `alias_target_pairs` as the assignment target it is, and a mapping
key is read through the shared `_selected_values`. The bound: a position the
subject cannot decide is marked unprovable (a call through it is reported); a
pattern that provably cannot match the subject leaves its names unpaired; a
class pattern is the one unmodelled form (issue 1003), whose captures name an
attribute reached through the subject's class, and the fail-closed
alternative false-positives on `test_destructured_and_walrus_alias_boundaries`.

An or-pattern offers every alternative the same subject; a name they disagree
on is the merge of their values, so differing reachable states become
unprovable (never last-write-wins). Accepted fail-closed costs, each pinned by
a row: an or-pattern over disagreeing alternatives, a literal-None position,
a value test the guard cannot disprove, a merge over disagreeing branches, and
a literal key read that folds a distinct DYNAMIC_KEY entry (the same fold the
`**rest` arm and the shared subscript make, so the routes agree).

`merge-discriminating` moves under a first-branch-only read of the holders,
and the or-pattern merge is pinned on the last-write-wins axis
(`or-swap-routed` reds when `_bind_or` is rewritten to the last alternative).
Three inherited merge limbs have no row and are recorded here.
`merge_yielded`'s duplicate-sender clause is equivalent at the call surface by
construction
(no call distinguishes a sender from an unprovable one). `_merge_bind`
first-wins (`bound[0]`) has no discriminating row because the `None`-filter in
the holders read drops a benign branch before `_merge_bind` sees it, so in
every row it is handed a single deferred value and first-wins and the merge
agree. And `alias_target_pairs`' cross-branch merge is masked by the
unresolvable-slot hole filed as issue 1010, not equivalent.
"""
import ast

from _pyroute_mapping import (_UNRESOLVED_KEY, _literal_key,
                              _selected_values, alias_target_pairs,
                              apply_assignment_bindings)
from _pyroute_state import (UNPROVABLE_SENDER, FlowState, bind_alias_target,
                            clear_names, dedupe_states, rebound_names)
from _pyroute_values import (DYNAMIC_KEY, DeferredAlternatives,
                             DeferredContainer, _known_value,
                             is_deferred_value, merge_yielded)


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
        if item_value is None and not isinstance(item, ast.MatchStar):
            _unprovable(item, state)
            continue
        _bind_pattern(item, item_value, state)


def _mapping_branches(value):
    if isinstance(value, DeferredAlternatives):
        return list(value.values)
    return [value]


def _key_possible(literal, branches):
    """Whether any branch carries the key, literally or folded from a dynamic
    entry. A branch that lacks the key and holds no dynamic entry provably
    does not have it, so a mapping pattern naming it cannot run."""
    return any(literal in branch.items or DYNAMIC_KEY in branch.items
               for branch in branches)


def _bind_mapping(pattern, value, state):
    """Pair a mapping pattern's keys, and `**rest`, to the subject. A
    resolvable literal key pairs to the value the subject carries under it,
    across a merge subject, read through the shared `_selected_values` so a
    dynamic-keyed subject folds into the lookup; a key the guard cannot
    resolve leaves the slot unprovable; a subject that is not a mapping
    cannot match; and a listed key no branch carries means the case cannot
    run, so no capture — not even `**rest` — is paired."""
    branches = _mapping_branches(value)
    if not all(isinstance(branch, DeferredContainer)
               and branch.kind == 'dict' for branch in branches):
        return  # the subject is not a mapping; the case cannot match
    resolved = []
    for key, sub in zip(pattern.keys, pattern.patterns):
        literal = _literal_key(key, state)
        if literal is _UNRESOLVED_KEY:
            resolved.append((sub, None))
            continue
        if not _key_possible(literal, branches):
            return  # a listed key no branch carries; the case cannot run
        resolved.append((sub, [
            held for branch in branches
            for held in _selected_values(branch, literal)
            if held is not None]))
    for sub, holders in resolved:
        if holders is None:
            _unprovable(sub, state)
        else:
            _merge_bind(sub, holders, state)
    if pattern.rest:
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
