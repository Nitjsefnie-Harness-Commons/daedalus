#!/usr/bin/env python3
"""A retirement travels with the values it is about, and every site that
carries or clears one is pinned.

A store whose source the model cannot read may have named any key the
container already held, so a constant-key read cannot answer from the
recorded value there. The marker is per key and it travels: a container
built out of a retired source's items carries the doubt, a key the same
expression writes settles it, and every place a container is rebuilt or
re-derived has to carry or clear it on the way.

Each control here is entered at the site that owes the behaviour. That
is deliberate and it is this suite's whole shape: the round-4 survivor
sweep reverted every pinned site in turn and found five with no red
anywhere, because no verdict distinguished them. A control that passes
both with and without the code it pins is not a control.

**On the read-back arms.** `values()` and `items()` project a retired
container's recorded values. That was carried for several rounds as a
limitation this branch left open, and it is not one: across 128 cells at
this head no read-back shape reads clean, against 25 at the base. The
projection does not carry a retirement onto the values it projects, and
it does not need to, because every read that reaches those values
already joins -- which is why the numbers moved without it.
"""
import ast
import sys
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_mapping import (_apply_mapping_store,  # noqa: E402
                             _apply_setdefault,
                             _mark_unprovable)
from _pyroute_match import _bind_mapping  # noqa: E402
from _pyroute_positions import at_position  # noqa: E402
from _pyroute_setops import set_operands  # noqa: E402
from _pyroute_reads import (_apply_pop, _dict_call_value,  # noqa: E402
                            _dict_value, _mapping_lookup,
                            _merge_or_value, _readback_copy,
                            _readback_popitem)
from _pyroute_state import FlowState  # noqa: E402
from _pyroute_storage import (container_copy,  # noqa: E402
                              join_clean_occupancy, retired_into)
from _pyroute_stores import (_seed_receiver,  # noqa: E402
                             _subscript_store)
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,  # noqa: E402
                             DeferredAlternatives, DeferredContainer,
                             merge_yielded, stored_signature)
from _retirement_sweep import (  # noqa: E402
                              NO_MUTATION as _DECIDED,
                              REVERTS as _REVERTS,
                              undecided_sites as _UNDECIDED,
                              retirement_sites)
from _tabroute_keyset import (_CLEAN, _DESTINATION_FOLDS,  # noqa: E402
                              _DESTINATION_READS, _RETIRED_SOURCE,
                              _row_verdict)


def _retired_source_state():
    """A state holding one source that recorded a key and then retired it."""
    state = FlowState({}, {}, {}, {}, set(), set(), {}, set())
    state.callables['d'] = DeferredContainer(
        {'k': None}, None, 'dict', stale=frozenset({'k'}))
    return state


def _fold_into_destination(state, keywords=None) -> DeferredContainer:
    """Run the store fold of a retired source into a bound destination."""
    owner = DeferredContainer({}, 0, 'dict')
    state.callables['o'] = owner
    _apply_mapping_store(state, owner, 'o',
                         [ast.Name(id='d', ctx=ast.Load())],
                         keywords or {}, None)
    return cast(DeferredContainer, state.callables['o'])


_FOLDS = ('update', 'update-star', 'ior', 'display', 'or-value',
          'dict-call')


def _folded(spelling) -> DeferredContainer:
    """The container one spelling of a fold builds from the retired source.

    Entered at each fold site's own function rather than through the flow,
    so what is asserted is the container that site itself returns. The
    three store spellings reach one function, which is why one entry covers
    them: a `**` source and an `|=` operand are both a `sources` entry.
    """
    state = _retired_source_state()
    if spelling in ('update', 'update-star', 'ior'):
        return _fold_into_destination(state)
    node = ast.parse(
        {'display': '{**d}', 'or-value': '{} | d', 'dict-call': 'dict(d)'}
        [spelling], mode='eval').body
    folded = (_dict_value(node, state) if isinstance(node, ast.Dict)
              else _merge_or_value(node, state)
              if isinstance(node, ast.BinOp)
              else _dict_call_value(node, state))
    # All three answer `None` for an empty result, and the state below
    # gives each of them a source that recorded a key, so this cannot fire
    # on the fixture. It is here because the checker cannot follow the
    # assignment otherwise, and it states the shape the fixture guarantees
    # rather than pretending to be a finding.
    assert folded is not None, spelling
    return folded


def test_the_retirement_census_is_the_guard_own_list(tmp):
    """Every site that propagates or consults a retirement, read out of
    the guard's own source rather than kept as a list here.

    Keyed on the PROPERTY -- a guard function that mentions `stale` as a
    name, an attribute or a keyword argument -- so a site spelled with a
    different helper is found. The earlier version of this check keyed on
    the callee name `_fold_items`, which finds today's four folds and
    misses the rest of the rule; that is the same finding one level up,
    where the sweep that ran over the derived list kept the LIST in a
    scratch report.

    `_retirement_sweep` is the module beside it that reverts each
    site and reports the controls that die. This is the half that runs on
    every commit; that one runs on request, and a site with no decision
    recorded for it stops it running at all.
    """
    assert not _UNDECIDED(), sorted(_UNDECIDED())
    # The count is DERIVED, not asserted as a literal: a twentieth site
    # planted in a module the old hand list never named would leave a
    # literal `== 19` still passing, which is the whole failure this
    # derivation exists to prevent. The floor is a tripwire for the
    # derivation going blind; the exactness is the undecided check above.
    # EQUALITY, both ways, not a subset and not a floor. A subset only
    # catches a site that APPEARS; the reviewer blinded the settling axis
    # with an early `return False`, the sites went 22 -> 21, `undecided`
    # stayed empty and this census stayed green at 15/15. A floor of 20
    # tolerates exactly that. A site that VANISHES has to be a failure,
    # because every entry in the decided set was derived from the tree once
    # and its absence now means the derivation stopped seeing it.
    decided = set(_REVERTS) | set(_DECIDED)
    assert set(retirement_sites()) == decided, sorted(
        set(retirement_sites()) ^ decided)


def test_a_fold_carries_the_source_retirement_with_its_items(tmp):
    """The propagation, on the sites that have to do it.

    A container built from a retired source's items carries the retirement
    with them. This is asserted on the containers each fold site returns
    rather than on a verdict, because no read form distinguishes the two
    answers on this tree: the conservative "may be ext_cmd" rule already
    reports a read whose value the model cannot name, so the end-to-end
    test below passes with or without this. A control that only proves the
    fix on the fixed tree proves nothing, and this is the half that does
    not.
    """
    for spelling in _FOLDS:
        destination = _folded(spelling)
        assert destination.stale == frozenset({'k'}), (spelling, destination)
    # The other half of the rule: a key the destination's own store writes
    # back afterwards is current again, so the fold must not leave it
    # retired and a later store must be able to clear it.
    state = _retired_source_state()
    _fold_into_destination(state)
    _subscript_store(
        state, ast.Subscript(value=ast.Name(id='o', ctx=ast.Load()),
                             slice=ast.Constant('k'), ctx=ast.Store()),
        None, state.callables['o'], 'o', False, False)
    refreshed = cast(DeferredContainer, state.callables['o'])
    assert refreshed.stale == frozenset(), refreshed


# A key the SAME expression writes settles it. The value the model wrote
# is the value the runtime holds there, so whatever a fold beside it
# retired must not survive. The one member that does survive is the
# literal written BEFORE the fold, because at runtime the fold overwrites
# it and the value the container ends up holding is the source's.
_SETTLED = {
    'update-keyword': ('o.update(d, k=relay())', frozenset()),
    'dict-keyword': ('dict(d, k=relay())', frozenset()),
    'display-after': ('{**d, "k": relay()}', frozenset()),
    'display-before': ('{"k": relay(), **d}', frozenset({'k'})),
}


def test_a_key_the_same_expression_writes_settles_it(tmp):
    """The un-retiring half, at each of the four sites that owes it.

    A `key=value` pair, a `dict()` keyword and a display literal are all
    values the model read, written at a key the source may have retired.
    Each settles that key; none of them settles any other, which is what
    the fourth member pins. Entered at the site itself, because a verdict
    cannot tell a settled key from one the fold simply never carried."""
    for spelling, (source, expected) in sorted(_SETTLED.items()):
        state = _retired_source_state()
        if spelling == 'update-keyword':
            settled = _fold_into_destination(
                state, {'k': ast.Constant('v')})
        else:
            node = ast.parse(source, mode='eval').body
            settled = (_dict_value(node, state) if isinstance(node, ast.Dict)
                       else _dict_call_value(node, state))
        # A source that recorded a key gives both of these something to
        # fold, so neither is the empty result they answer with.
        assert settled is not None, spelling
        assert settled.stale == expected, (spelling, settled.stale)


def test_a_copy_carries_the_retirement_it_copied(tmp):
    """`d.copy()` holds what the receiver holds, doubt included."""
    state = _retired_source_state()
    owner = state.callables['d']
    copied = _readback_copy(ast.parse('d.copy()', mode='eval').body,
                            state, owner)
    assert copied.stale == owner.stale, copied.stale
    assert _mapping_lookup(cast(DeferredContainer, copied), 'k',
                           object()) is not None


def test_a_setdefault_settles_a_key_the_join_left_retired(tmp):
    """The one un-retiring site whose key is NOT held when it runs.

    A join can drop a retired key's value while the retirement survives,
    so `setdefault` on that key does write after all, and the write
    settles it. This is also what pins `join_clean_occupancy`'s carry:
    without it the container reaching `setdefault` is not the one under
    test, and the site would look inert rather than unproven."""
    kept = FlowState({}, {}, {}, {}, set(), set(), {}, set())
    kept.callables['d'] = DeferredContainer(
        {'k': None}, None, 'dict', stale=frozenset({'k'}))
    other = kept.copy()
    other.callables['d'] = DeferredContainer({'j': None}, None, 'dict')
    joined = join_clean_occupancy(kept, other)
    owner = cast(DeferredContainer, joined.callables['d'])
    assert 'k' not in owner.items and owner.stale, owner
    call = ast.parse('d.setdefault("k", ordinary)').body[0]
    assert isinstance(call, ast.Expr)
    _apply_setdefault(joined, call.value, owner, 'd')
    written = cast(DeferredContainer, joined.callables['d'])
    assert written.stale == frozenset(), written.stale
    assert 'k' in written.items, written.items


def test_a_pop_of_a_retired_key_drops_its_retirement(tmp):
    """A `pop` takes the entry out, so it is no longer a held key at all."""
    state = _retired_source_state()
    call = ast.parse('d.pop("k")').body[0]
    assert isinstance(call, ast.Expr)
    _apply_pop(state, call.value)
    popped = cast(DeferredContainer, state.callables['d'])
    assert popped.stale == frozenset(), popped.stale
    assert 'k' not in popped.items, popped.items


def test_a_seeded_receiver_settles_the_key_it_wrote(tmp):
    """The one un-retiring site that writes through an attribute or a
    subscript the model has to create first."""
    state = _retired_source_state()
    seeded = _seed_receiver(state, ast.parse('d["k"]', mode='eval').body,
                            'd')
    assert seeded is True
    holder = cast(DeferredContainer, state.callables['d'])
    assert holder.stale == frozenset(), holder.stale
    assert 'k' in holder.items, holder.items


def test_the_subscript_read_honours_a_retired_key(tmp):
    """`at_position` is the subscript's own read, and it is a separate site
    from the one the container reads reach: `_mapping_lookup` passing says
    nothing about it."""
    recorded = DeferredContainer({0: None}, 1, 'tuple')
    retired = DeferredContainer({'k': recorded}, None, 'dict',
                                stale=frozenset({'k'}))
    current = DeferredContainer({'k': recorded}, None, 'dict')
    assert merge_yielded(at_position(retired, 'k')) \
        == DeferredAlternatives((recorded, UNPROVABLE_SENDER))
    assert merge_yielded(at_position(current, 'k')) is recorded


def test_a_store_that_cannot_read_its_source_retires_what_it_held(tmp):
    """The producer side, entered at `_mark_unprovable` itself.

    A store that folded a source the model could not read may have named
    any key the container already held, so every recorded value is
    retired along with the count. This is the row the #1154 mechanism
    turned on, and it has to be pinned here rather than through a
    verdict: the end-to-end rows that used to cover it are repaired and
    read clean, so reverting this site's `stale=` left the suites green
    until the census assertion caught that the SITE had gone. A control
    that says the site exists is not one that says it behaves.
    """
    # A container that has NOT retired anything yet: carrying its marker
    # forward unchanged would satisfy this control without the site doing
    # any work, which is the bug it was written to catch.
    state = FlowState({}, {}, {}, {}, set(), set(), {}, set())
    owner = DeferredContainer({'k': None}, 1, 'dict')
    state.callables['d'] = owner
    _mark_unprovable(state, owner, 'd')
    marked = cast(DeferredContainer, state.callables['d'])
    assert marked.stale == frozenset({'k'}), marked.stale
    assert marked.length is None, marked.length
    assert state.aliases['d'] == UNPROVABLE_SENDER, state.aliases


def test_the_state_signature_separates_a_retired_key_from_a_current_one(tmp):
    """The dedupe contract the signer's `stale` term exists to keep.

    Two containers that differ only in which keys a store has retired are
    not interchangeable: a state carrying the retirement answers a read at
    that key by joining, and one that does not answers from the recorded
    value. If they signed alike the flow would keep whichever came first
    and the other would never be consulted again.

    This is pinned as a property of the signer rather than as a verdict,
    because no program in the routing domain I could reach separates the
    two: a branch whose paths differ only in the marker reads the same
    either way, and the round-5 sweep over the reviewer's 27 cells agreed.
    The property is what the term is FOR, and dropping the term fails it.
    """
    items = {'k': None}
    # The SAME identity, which is what a replacement shares: a state's
    # container and the one that replaces it are the same object as far as
    # the flow is concerned, and two containers with different identities
    # never sign alike whatever the marker says. The first version of this
    # control used two identities and therefore passed with the term
    # removed.
    identity = object()
    current = DeferredContainer(dict(items), None, 'dict', identity=identity)
    retired = DeferredContainer(dict(items), None, 'dict', identity=identity,
                                stale=frozenset({'k'}))
    assert stored_signature(current) != stored_signature(retired), (
        'two containers differing only in freshness must not sign alike')


def test_a_container_copy_never_retires_the_unknown_key_slot(tmp):
    """The slot names no key, so a retirement at it would be a fact about
    nothing -- and the state signature hashes this set, so the field's
    type is settled here too."""
    owner = DeferredContainer({}, 0, 'dict')
    copied = container_copy(owner, {'k': None},
                            stale=frozenset({'k', DYNAMIC_KEY}))
    assert copied.stale == frozenset({'k'}), copied.stale
    assert isinstance(copied.stale, frozenset), type(copied.stale)


def test_a_pattern_rest_carries_the_retirement_it_projected(tmp):
    """A `case {**_rest}` binds the same values under a new name, and the
    retirement with them.

    Entered at `_bind_mapping` rather than end to end: the subscript of
    the projected name is reached through the source's own marking on that
    path, so no verdict distinguishes a carried retirement from a dropped
    one and the end-to-end form of this control survived its own revert in
    the round-4 sweep."""
    state = _retired_source_state()
    pattern = ast.MatchMapping(keys=[], patterns=[], rest='_rest')
    _bind_mapping(pattern, state.callables['d'], state)
    rest = cast(DeferredContainer, state.callables['_rest'])
    assert rest.stale == frozenset({'k'}), rest.stale
    assert 'k' in rest.items, rest.items


def test_a_set_fold_refuses_a_dict_operand(tmp):
    """The controlling evidence for `_apply_set_store`'s recorded decision.

    The decision is that its carry is unobservable because `set_operands`
    refuses a mapping operand, so the container it copies is a set and
    carries no retired dict keys. That was an argument; this measures it at
    the boundary the argument depends on, and what would refute the decision
    is a path delivering a dict operand -- which this drives, for each
    operator the fold answers.
    """
    retired = DeferredContainer({'k': None}, None, 'dict',
                                stale=frozenset({'k'}))
    state = _state_with(retired)
    for operator in (ast.BitOr, ast.BitAnd, ast.BitXor, ast.Sub):
        # A NODE, not the class. `set_operands` reads `isinstance(operator,
        # SET_OPERATORS)`, and a class is not an instance of itself, so
        # passing `ast.BitOr` returned None at that first check and this
        # control never reached the mapping refusal it names. It was green
        # for the wrong reason; deleting the refusal left it green too.
        operands = set_operands(operator(), ast.Name(id='d', ctx=ast.Load()),
                                ast.Name(id='e', ctx=ast.Load()), state)
        assert operands is None, (operator, operands)
        # And the refusal is the reason, shown rather than assumed: with the
        # dict operand replaced by nothing the model can call a set, the
        # same call reaches the return.
        plain = set_operands(
            operator(), ast.Constant('a'), ast.Constant('b'), state)
        assert plain is None, (operator, plain)


def _state_with(container):
    state = FlowState({}, {}, {}, {}, set(), set(), {}, set())
    state.callables['d'] = container
    state.callables['e'] = DeferredContainer({0: None}, 1, 'set')
    return state


def test_a_popitem_projection_reads_both_ways(tmp):
    """The controlling evidence for `_readback_popitem`'s recorded decision.

    The decision is that the carry is unobservable because the projection
    either joins every value into the unknown-key slot or deletes a key. The
    second branch is the one that could surprise — a retirement naming a
    key the container no longer holds — so both branches are driven and the
    result read at exactly that key.
    """
    call = ast.parse('d.popitem()', mode='eval').body
    # First branch: an unknown-length owner, so the projection folds every
    # value into the unknown-key slot, which every read consults anyway.
    joined_owner = DeferredContainer({'k': None}, None, 'dict',
                                     stale=frozenset({'k'}))
    state = _state_with(joined_owner)
    _readback_popitem(call, state, joined_owner)
    joined = cast(DeferredContainer, state.callables['d'])
    assert DYNAMIC_KEY in joined.items, joined.items
    # Second branch: a countable owner, so the projection deletes the key
    # and the retirement is left naming an absent one. A read at that key
    # joins rather than answering from a value -- the conservative
    # over-join the decision claims, measured rather than argued.
    tracked = DeferredContainer({'k': None}, 1, 'dict',
                                stale=frozenset({'k'}))
    state = _state_with(tracked)
    _readback_popitem(call, state, tracked)
    deleted = cast(DeferredContainer, state.callables['d'])
    assert 'k' not in deleted.items, deleted.items
    assert deleted.stale == frozenset({'k'}), deleted.stale
    assert merge_yielded(at_position(deleted, 'k')) is not None


def test_the_projection_drops_what_it_left_out(tmp):
    """The intersection, and the defect it exists to prevent.

    A fold carries a source's retirement only for the keys that survive
    into the destination: a key the projection dropped out is not a key
    the destination holds, so retiring it there would be a fact about
    nothing. Carrying the whole set instead is sound and imprecise, and
    the sweep's `retired_into` revert is that form rather than a stronger
    one -- `return frozenset()` kills four controls and this one too, but
    only this one distinguishes them.
    """
    marker = frozenset({'k', 'j'})
    assert retired_into(marker, {'k': None}) == frozenset({'k'})
    assert retired_into(marker, {}) == frozenset()
    assert retired_into(frozenset(), {'k': None}) == frozenset()
    # The unknown-key slot names no key, so a retirement at it is nothing.
    assert retired_into(frozenset({DYNAMIC_KEY}), {'k': None}) == frozenset()


def test_a_join_of_two_paths_keeps_the_retirement(tmp):
    """Two states that differ only in a clean key join; the retirement
    rides across that join like any other recorded fact.

    The fixture has to make the join REBUILD: `join_clean_occupancy` skips
    a state whose recorded items already agree, and the previous version of
    this test differed only in a key the join dropped on both sides, so it
    took that skip, the assertion short-circuited, and the test could not
    fail. The `is not kept` assertion below is what says the rebuild ran."""
    kept = _retired_source_state()
    kept.callables['d'] = DeferredContainer(
        {'k': None, 'dropped': None}, None, 'dict', stale=frozenset({'k'}))
    other = kept.copy()
    other.callables['d'] = DeferredContainer(
        {'k': None}, None, 'dict', stale=frozenset({'k'}))
    joined = join_clean_occupancy(kept, other)
    assert joined is not kept, 'the join skipped: the fixture did not differ'
    rebuilt = cast(DeferredContainer, joined.callables['d'])
    assert rebuilt.stale == frozenset({'k'}), rebuilt.stale
    assert 'k' in rebuilt.items, rebuilt.items


def test_a_fold_of_a_retired_source_reports_every_read_form(tmp):
    """The retirement travels with the items, over a runtime that routes.

    The fold builds a DESTINATION and the read is on the DESTINATION: a
    control that reads the source instead measures a key the source
    already reported on, which is a false positive standing exactly where
    the defect lives. And the source has to retire the key the RUNTIME
    replaces, so a routed `relay()` really does reach the call through the
    destination and the guard has to report it -- twelve cells, four fold
    spellings by three read forms, `(1, 1)` routed and `(0, 0)` clean.

    The source is the OPAQUE one: a source whose pairs its own syntax
    spells is read and retires nothing, so it is no longer a retirement to
    carry. Which cells go `(1, 0)` with the propagation removed is the
    round's survivor list, not a claim made here. The store-side test above
    is what pins that the retirement travels at all.
    """
    for fold in _DESTINATION_FOLDS:
        body = f'{_RETIRED_SOURCE}{fold}'
        for read, source in sorted(_DESTINATION_READS.items()):
            assert _row_verdict(tmp, body, source) == (1, 1), (fold, read)
            assert _row_verdict(tmp, body, source, _CLEAN) == (0, 0), (
                fold, read)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dictretire_')


if __name__ == '__main__':
    raise SystemExit(main())
