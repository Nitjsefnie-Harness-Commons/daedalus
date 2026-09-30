#!/usr/bin/env python3
"""A constant-key read of a dict the model cannot enumerate fails closed.

A tracked dict has two legal readings. When the model can account for its
key set, a read answers the recorded value or the read's own default, and a
key it knows is absent really is absent. When it cannot -- an unknown length
with no unknown-key slot standing for what it never learned -- a read may
select anything the unenumerated part carried, so it joins instead of
answering "nothing". Two kinds of key are in that state: one the model never
recorded, and one it recorded BEFORE a store that could not read its source,
whose recorded value that store may since have replaced. A key written after
that store is untouched by it and keeps its recorded value. Answering a value
a store has replaced is the silent outcome this suite exists to police.

**What counts as a store that cannot read its source.** Only a source the
model has no account of: a user call, `vars(args)`, a source reached through a
name, a starred positional. A source whose PAIRS ITS OWN SYNTAX SPELLS is
read -- a `zip` of two equal-length literal columns pairs them by position,
and a single-argument sequence wrapper spells them one layer down -- so a
store that folds one writes keys the model can name and retires nothing. That
is what puts every `_AXES` row at a clean `(0, 0)`.

**The census is not a closed set.** `_AXES` is the census: one member per
way a tracked dict acquires a key the model did not learn, driven from the
mutator rather than from a spelling, over the three axes that domain
spans -- the mutator, the COMBINATION of a readable and an unreadable
source in one call, and the FRESHNESS of a value the model recorded before
an unreadable source could have replaced it. `_COST` carries what
each member costs once nothing is routed, and
`test_every_axis_member_reports_on_every_read_form_and_costs_nothing` fails
on a member present in one and not the other, so the bucket cannot grow by
omission.

It does not claim that every member of that domain is listed, because
measurement says otherwise. The domain is closed under {mutator shape} x
{COMBINATION, FRESHNESS}, and every round of enumeration this branch has
run has left members of the census's own domain unlisted -- nine at the
last, the name-source family crossed with the other two axes. They are rows
now. What the two tables' tests actually prove is narrower than a closed
set, and this is the whole of it: the silent-member test pins each
`_SILENT` row's own measurement, so a repair of a LISTED member turns this
suite red on the commit that has to move it into `_AXES`; and
`test_an_unlisted_member_of_the_domain_is_rejected` shows the census
CONSIDERS a member of its own stated domain that it does not list, which
is not a claim that no such member exists. A repair of an UNLISTED member
would leave this suite green, so each new row is here because the next
round should not have to find that member again.

The two outcomes differ in what the read costs once nothing is routed. A
refused read resolves to a tracked callable, so the guard reads that
callable's body and the shape stays clean. A declared read joins to an
unprovable sender, and a `tab` through a name holding one is reported
whatever that callable would have done. Once every source whose pairs the
model can read was read, no member pays the second cost but one: every
`_AXES` row costs `(0, 0)` but the row whose source sits behind a starred
positional, which no spelling of the call accounts for, and `_SILENT`
carries the one member whose read form does not.

**Not this suite's bucket.** A store path that folds an unreadable source
into its OWNER's `DYNAMIC_KEY` slot -- `dict(...)`, `{**...}`, `|=`,
`update(...)`, `update(**...)`, a plain assignment -- leaves the value at the
key already joined to an unprovable sender. A bare routed-lambda call through
it still reads clean, because a `tab` living in the callee's body is not a
reporting shape for an unprovable sender. That is a different mechanism,
tracked as 1010, and a member reached only that way is not in this suite's
bucket. What separated those rows from these is WHICH NAME carries the
fold, and not every member had a name to answer for. A store that marks
its owner answers joined at the key being read; a store that propagates an
unknown length from a source reached through a NAME left the fold on the
source name, so the owner's own reads answered whatever its recorded items
said; and a starred positional source marked neither name, its fold sitting
on no name at all. Two of those three are repaired and in `_AXES`; the
starred positional is the one member `_SILENT` still names, silent on the
subscript alone.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_reads import _mapping_lookup  # noqa: E402
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,  # noqa: E402
                             DeferredAlternatives, DeferredContainer)
from _tabroute_keyset import (_CLEAN, _COMPUTED, _FRESH,  # noqa: E402
                              _POPPED, _READS,
                              _REFRESHED, _UNACCOUNTABLE, _UNREADABLE,
                              _row_body, _row_verdict)

_verdict = _row_verdict
_body = _row_body

_AXES = {
    # `update`, a positional source the model reads as pairs.
    'update-pairs': 'd = {}\nd.update([("k", relay())])',
    'update-pairs-tuple': 'd = {}\nd.update((("k", relay()),))',
    'update-dict': 'd = {}\nd.update({"k": relay()})',
    'update-keyword': 'd = {}\nd.update(k=relay())',
    # `update`, a positional source it cannot.
    'update-zip': 'd = {}\nd.update(zip(["k"], [relay()]))',
    'update-frozenset': 'd = {}\nd.update(frozenset([("k", relay())]))',
    'update-unreadable': 'd = {}\n' + _UNREADABLE + 'd.update(mk())',
    # `update`, a `**` source.
    'update-star': 'o = {"k": relay()}\nd = {}\nd.update(**o)',
    'update-star-two': ('o = {"k": relay()}\np = {"j": 1}\nd = {}\n'
                        'd.update(**o, **p)'),
    'update-star-mixed': ('o = {"k": relay()}\nd = {}\n'
                          'd.update([("a", 1)], **o)'),
    # `update`, a positional source it reads and a `**` source it does
    # not. The readable pair is folded, then the unreadable mark keeps
    # the OWNER's items and discards the local fold, so a read of the
    # readable pair's own key joins. Both orders of the two are spellable
    # and neither is a KEEP: a readable source second is dropped either
    # way, because `_apply_mapping_store` returns on the first `None`
    # source. What is not spellable is the mixed POSITIONAL form --
    # `d.update(mk(), [("a", 1)])` is a runtime TypeError and
    # `d.update(**o, [("a", 1)])` a SyntaxError.
    'update-star-mixed-unreadable': (
        'def mk():\n    return dict(zip(["j"], [1]))\nd = {}\n'
        'd.update([("k", relay())], **mk())'),
    'update-star-mixed-unreadable-other': (
        'def mk():\n    return dict(zip(["k"], [relay()]))\n'
        'd = {"j": ordinary}\nd.update([("a", 1)], **mk())'),
    'update-star-unreadable': _UNREADABLE + 'd = {}\nd.update(**mk())',
    # `|=` and `setdefault`.
    'ior-unreadable': _UNREADABLE + 'd = {}\nd |= mk()',
    'ior-modelled': 'o = {"k": relay()}\nd = {}\nd |= o',
    'setdefault-value': 'd = {}\nd.setdefault("k", relay())',
    'setdefault-vacant': 'd = {"a": 1}\nd.setdefault("k", relay())',
    # Constructors, and assignment from a source the model cannot read.
    'dict-call': 'd = dict([("k", relay())])',
    'dict-call-star': 'o = {"k": relay()}\nd = dict(**o)',
    'dict-literal-star': 'o = {"k": relay()}\nd = {**o}',
    'dict-literal-star-unreadable': _UNREADABLE + 'd = {**mk()}',
    'assign-unreadable': _UNREADABLE + 'd = mk()',
    'copy-unreadable': _UNREADABLE + 'd = dict(mk())',
    # A key recorded between the store that lost the count and one after
    # it. The second store is one the model cannot read either, so it may
    # have replaced the key the first one left: the read keeps the value
    # recorded there and joins the unprovable sender onto it, which is
    # what keeps this row's cost where it was.
    'recorded-key': ('d = {}\nd.update(zip(["j"], [1]))\nd["k"] = relay()\n'
                     'd.update(zip(["x"], [1]))'),
    # A key recorded BEFORE the store that lost the count, and no store
    # since. The value the model wrote is the value the runtime holds, so
    # the read answers from it alone, with nothing joined onto it.
    # `recorded-key` is this row with one more unreadable store after it,
    # and the pair is the rule from both sides.
    'fresh-recorded-key': _FRESH,
    'fresh-recorded-key-updated': _REFRESHED,
    'fresh-recorded-key-popped': _POPPED,
    # The FRESHNESS axis proper: a store whose source the model cannot read
    # may have put something else at every key the container already held,
    # so the read of such a key joins on all three forms. Filed as 1154.
    'stale-recorded-zip': (
        'd = {"k": ordinary}\nd.update(zip(["k"], [relay()]))'),
    'stale-recorded-frozenset': (
        'd = {"k": ordinary}\nd.update(frozenset([("k", relay())]))'),
    'stale-recorded-later-store': (
        'd = {"k": ordinary}\nd.update(zip(["j"], [1]))'
        '\nd.update(zip(["k"], [relay()]))'),
    # The same crossing through a source reached through a NAME the model
    # already holds as unaccountable: the marking lands on that name and
    # nothing at all on the owner, so before the rule these four were
    # silent on every form. Filed as 1178.
    # The name-source family: a source reached through a NAME the model
    # already holds as unaccountable. Filed as 1162, except the two bare
    # rows, whose crossing with FRESHNESS is what 1178 was filed for. The
    # subscript was the silent one until the source's own pairs became
    # readable; the readable-source rule settled all seven.
    'update-unaccountable-name': _UNACCOUNTABLE + '\nd = {}\nd.update(o)',
    'update-unaccountable-name-star': (
        _UNACCOUNTABLE + '\nd = {}\nd.update(**o)'),
    'update-unaccountable-name-doubled': (
        _UNACCOUNTABLE + '\nd = {}\nd.update(**{**o})'),
    'ior-stale-unaccountable-name': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd |= o'),
    'stale-unaccountable-name': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd.update(o)'),
    'stale-unaccountable-name-doubled': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd.update(**o)'),
    'stale-unaccountable-name-star': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd.update(*[o])'),
    'ior-unaccountable-name': _UNACCOUNTABLE + '\nd = {}\nd |= o',
    'update-unaccountable-name-mixed': (
        _UNACCOUNTABLE + '\nd = {}\nd.update([("a", 1)], **o)'),
    'update-unaccountable-name-mixed-keyed': (
        _UNACCOUNTABLE + '\nd = {}\nd.update(**{"a": 1}, **o)'),
    'update-unaccountable-name-mixed-doubled': (
        _UNACCOUNTABLE + '\nd = {}\nd.update(**{**{"a": 1}, **o})'),
    # The same source through a constructor store, which `_dict_call_value`
    # answers from the same fold. Filed as 1163, and all four of its rows
    # are repaired here with no survivor -- its six filing bodies and three
    # named controls read `(1, 1)` / `(0, 0)` at this head against
    # `(1, 0)` / `(0, 0)` at base -- so this branch closes 1163 as well as
    # 1154 and 1178.
    'dict-name': _UNACCOUNTABLE + '\nd = dict(o)',
    'dict-name-star': _UNACCOUNTABLE + '\nd = dict(**o)',
    'dict-stale-name': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd = dict(o)'),
    'ior-after-dict-name': _UNACCOUNTABLE + '\nd = dict(o)\nd |= {"j": 1}',
    'literal': 'd = {"k": relay()}',
}

# What each member's SHAPE costs once nothing is routed. It was an outcome
# column beside this one -- `refused` when the read resolves to a tracked
# callable, `declared` when it joins to an unprovable sender -- and that
# column is gone, and so is its name: once the readable sources were read,
# no member took the second outcome and no member's name carried an
# unprovable alias, so every row reads `(0, 0)` and the outcome said nothing
# the cost did not. A source that starts marking a name again shows here.
# The clean cost is uniform again but for ONE row, and that row is a source
# the model cannot read by any spelling: its source sits behind a starred
# positional, where no syntax of the call says what is paired, so the
# unknown-key slot is all the model has and every read form joins it. The
# three rows that read `(0, 1)` while their source was a `zip` reached it
# the way the false positive this branch fixes did -- the arm's container
# for the pairing, which holds nothing at a key that is a plain string, so
# the value joined the slot and a read of a DIFFERENT key joined it too.
# Reading the spelling instead gives the destination the key, the value
# stays at it, and the read answers from the key it was asked about.
_COST = {
    'update-pairs': (0, 0),
    'update-pairs-tuple': (0, 0),
    'update-dict': (0, 0),
    'update-keyword': (0, 0),
    'update-zip': (0, 0),
    'update-frozenset': (0, 0),
    'update-unreadable': (0, 0),
    'update-star': (0, 0),
    'update-star-two': (0, 0),
    'update-star-mixed': (0, 0),
    'update-star-mixed-unreadable': (0, 0),
    'update-star-mixed-unreadable-other': (0, 0),
    'update-star-unreadable': (0, 0),
    'ior-unreadable': (0, 0),
    'ior-modelled': (0, 0),
    'setdefault-value': (0, 0),
    'setdefault-vacant': (0, 0),
    'dict-call': (0, 0),
    'dict-call-star': (0, 0),
    'dict-literal-star': (0, 0),
    'dict-literal-star-unreadable': (0, 0),
    'assign-unreadable': (0, 0),
    'copy-unreadable': (0, 0),
    'recorded-key': (0, 0),
    'fresh-recorded-key': (0, 0),
    'fresh-recorded-key-updated': (0, 0),
    'fresh-recorded-key-popped': (0, 0),
    'stale-recorded-zip': (0, 0),
    'stale-recorded-frozenset': (0, 0),
    'stale-recorded-later-store': (0, 0),
    'literal': (0, 0),
    'update-unaccountable-name': (0, 0),
    'update-unaccountable-name-star': (0, 0),
    'update-unaccountable-name-doubled': (0, 0),
    'ior-stale-unaccountable-name': (0, 0),
    'stale-unaccountable-name': (0, 0),
    'stale-unaccountable-name-doubled': (0, 0),
    'stale-unaccountable-name-star': (0, 1),
    'ior-unaccountable-name': (0, 0),
    'update-unaccountable-name-mixed': (0, 0),
    'update-unaccountable-name-mixed-keyed': (0, 0),
    'update-unaccountable-name-mixed-doubled': (0, 0),
    'dict-name': (0, 0),
    'dict-name-star': (0, 0),
    'dict-stale-name': (0, 0),
    'ior-after-dict-name': (0, 0),
}

# Shapes the model CAN account for: the key is visible, or its absence is,
# and the value the read yields routes nothing. `(0, 0)` on both prefixes,
# or a join has been paid for in precision the model never had to spend.
# Each names the read it drives, because an absent key leaves the model and
# the runtime with nothing to call.
_ACCOUNTED = {
    'key-visible': ('d = {"k": ordinary}', 'd["k"]'),
    'key-absent-empty': ('d = {}', 'd.get("k", ordinary)'),
    'key-absent-literal': ('d = {"j": ordinary}', 'd.get("k", ordinary)'),
    'update-key-visible': ('d = {}\nd.update(k=ordinary)', 'd["k"]'),
    'setdefault-vacant-clean': ('d = {"a": ordinary}\n'
                                'd.setdefault("k", ordinary)', 'd["k"]'),
    'star-modelled-clean': ('o = {"k": ordinary}\nd = {**o}', 'd["k"]'),
    'ior-modelled-clean': ('o = {"k": ordinary}\nd = {}\nd |= o', 'd["k"]'),
    'dict-call-clean': ('d = dict([("k", ordinary)])', 'd["k"]'),
    # A pair source whose own syntax SPELLS its keys. The `zip` names its
    # key half in the FIRST column, so the destination holds `"j"` with the
    # routed value at it, and the `"k"` written cleanly afterwards is
    # answered from what the model wrote there. The routed value never
    # reaches the unknown-key slot, so no read of `"k"` joins it -- which is
    # the whole of what `_UNDECIDED` named as undecidable.
    'fresh-key-ordinary': (
        'd = {"k": ordinary}\nd.update(zip(["j"], [relay()]))'
        '\nd.update({"k": ordinary})', 'd.get("k", ordinary)'),
}

# Which read forms a parked member is silent on, per member: the starred
# source reports on the subscript and reads clean on the container reads, and
# a constructor store that folds nothing out of an uncounted source reports on
# none of the three. The two read arms are the same rule over one key, so a
# member parked on one arm and not the other is a shape that reaches only
# that arm; the freshness rows that were parked on both arms until 1154 and
# 1178 were repaired are in `_AXES` with their filings beside them.
_CONTAINER_READS = ('get', 'setdefault')
_SUBSCRIPT = ('subscript',)
_ALL_READS = tuple(sorted(_READS))

# The rest of the domain, at the polarity that still reads silent: the read
# answers the recorded value or its own default, so a `relay()` the runtime
# really does call is reported nothing. Each names the issue it is parked
# against. The table is what the branch measured, not a closed set, so a
# member of the domain it does not carry is a silent read nothing here
# watches; the docstring says what that costs. The fourth field is the clean
# cost of the read forms the member does NOT name, which its own shape
# already reports through.
_SILENT = {
    # A positional source reached through a star, so the container answers
    # the subscript from the fold and joins the two container reads instead.
    # This is the LAST member: every other row this table once held is
    # repaired, and the suite's own rule sends a repaired member to `_AXES`
    # rather than leaving it here to be re-pinned.
    'update-starred-source': (
        'd = {}\nd.update(*[zip(["k"], [relay()])])', 1162, _ALL_READS,
        (0, 0)),
}


def test_every_axis_member_reports_on_every_read_form_and_costs_nothing(tmp):
    """Every member of every axis, over every read form: the read must not
    read clean, and it must cost nothing once nothing is routed.

    The cost is pinned per member and per form even though it is
    uniformly `(0, 0)`, because a shape that starts costing a read again
    has to show here rather than in a suite that only looks for a false
    green. The report count is not pinned: a member can be reported by the
    callable's own body, by an unprovable name, or by both, so the
    invariant is the lower bound.
    """
    assert sorted(_COST) == sorted(_AXES), sorted(
        set(_AXES) ^ set(_COST))
    for label, cost in sorted(_COST.items()):
        for name, read in sorted(_READS.items()):
            calls, found = _verdict(tmp, _AXES[label], read)
            assert (calls, found >= 1) == (1, True), (
                label, name, calls, found)
            assert _verdict(tmp, _AXES[label], read, _CLEAN) == cost, (
                label, name)


def test_an_unlisted_member_of_the_domain_is_rejected(tmp):
    """Hand the census a member of its own stated domain that it does not
    list: a second unreadable update over a dict the first already left
    unaccountable. The read must still not read clean."""
    member = 'd = {}\n' + _UNREADABLE + 'd.update(mk())\nd.update(mk())'
    assert member not in _AXES.values()
    for name, read in sorted(_READS.items()):
        assert _verdict(tmp, member, read) == (1, 1), (name, read)
        assert _verdict(tmp, member, read, _CLEAN) == (0, 0), (name, read)


def test_an_accountable_key_read_stays_clean(tmp):
    """The false-positive limb, end to end: the model can see the key, or
    can see that it is absent, and the read yields a value that routes
    nothing."""
    for label, (store, read) in sorted(_ACCOUNTED.items()):
        assert _verdict(tmp, store, read) == (0, 0), (label, read)
        assert _verdict(tmp, store, read, _CLEAN) == (0, 0), (label, read)


def test_the_mapping_read_joins_a_key_an_unreadable_store_replaced(tmp):
    """The read that answers from the container, on containers the store
    paths cannot be driven into.

    A store whose source the model cannot read may have put something else
    at every key the container already held, so a key it retired is one
    answer short of what the runtime holds there. It is one answer SHORT,
    not a different answer: the value the model recorded stays in the
    join beside the unprovable sender, because throwing it away is what
    would turn a real routed call into silence. A key the store left
    alone keeps the recorded value on its own -- that is what makes the
    marking a fact about the store rather than a property of a container
    that has lost its count. A countable mapping answers a held key from
    its items and an absent key with the read's own default, as before.
    """
    recorded = DeferredContainer({0: None}, 1, 'tuple')  # a tracked value
    default = object()  # the read's own default, which nothing records
    items = {'k': recorded}
    replaced = DeferredContainer(
        dict(items), None, 'dict', stale=frozenset({'k'}))
    untouched = DeferredContainer(dict(items), None, 'dict')
    countable = DeferredContainer(dict(items), 1, 'dict')
    marked = DeferredContainer(
        {**items, DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'dict')
    assert _mapping_lookup(replaced, 'k', default) == DeferredAlternatives(
        (recorded, UNPROVABLE_SENDER))
    assert _mapping_lookup(untouched, 'k', default) is recorded
    assert _mapping_lookup(countable, 'k', default) is recorded
    assert _mapping_lookup(untouched, 'j', default) == UNPROVABLE_SENDER
    assert _mapping_lookup(marked, 'j', default) == UNPROVABLE_SENDER
    assert _mapping_lookup(countable, 'j', default) is default
    assert _mapping_lookup(countable, 'j', None) is None


def test_a_key_written_after_an_unreadable_store_keeps_its_value(tmp):
    """The false-positive limb, over all three read forms.

    The key the model wrote after the store that retired it is one that
    store never touched, so the recorded value is the one the runtime holds
    and the read answers from it. The CLEAN figure is the discriminator: a
    read that resolved reports `(0, 0)` and a joined one `(0, 1)`, because
    the store that retires marks its own name, and a read bound to an
    unprovable sender reports through it. The second form writes the key
    back with a readable `update` source and the third removes an unrelated
    key in between, so a store that failed to un-retire on either of those
    would show here."""
    for store in (_FRESH, _REFRESHED, _POPPED):
        for read, source in sorted(_READS.items()):
            assert _verdict(tmp, store, source) == (1, 1), (store, read)
            assert _verdict(tmp, store, source, _CLEAN) == (0, 0), (
                store, read)


def test_a_store_through_a_computed_key_joins_on_its_unknown_slot(tmp):
    """The unaccountable store that retires nothing, and why.

    `d[<computed key>] = ...` may name any key the container holds, so on
    its own it is a store like any other that cannot read its own key. It
    also wrote the value the model DID read to the unknown-key slot, which
    every read arm joins, so the read already accounts for both outcomes:
    the key it named, and the recorded value that survived. Retiring the
    recorded keys on top of that would replace a value the model knows
    with the unprovable one, and the clean figure here would move off
    `(0, 0)`. These are the numbers the base commit measures. The runtime
    call count is zero because the recorded value at the key is an ordinary
    lambda, so this is a control and not a member of `_AXES`."""
    for read, source in sorted(_READS.items()):
        assert _verdict(tmp, _COMPUTED, source) == (0, 1), read
        assert _verdict(tmp, _COMPUTED, source, _CLEAN) == (0, 0), read


def test_every_silent_member_is_listed_and_not_refused(tmp):
    """The rest of the stated domain, named. A member of it that neither
    `_AXES` nor `_COST` carries is a silent read nobody is looking
    for, so each one left is listed against the issue it is parked on and
    PINNED: the pin is the defect, so a repair turns this red on the very
    commit that has to move the member into `_AXES`. The clean counterpart
    is pinned beside it, on EVERY read form rather than only the silent one,
    because a fix that made every read of that key join would be a new false
    positive rather than a repair, so the clean figure is pinned for every
    form and not only the silent one."""
    assert not {store for store, _, _, _ in _SILENT.values()} \
        & set(_AXES.values())
    assert all(issue is None or (isinstance(issue, int) and issue > 0)
               for _, issue, _, _ in _SILENT.values())
    for label, (store, _, names, reported) in sorted(_SILENT.items()):
        for name, read in sorted(_READS.items()):
            clean = _verdict(tmp, store, read, _CLEAN)
            assert clean == ((0, 0) if name in names else reported), (
                label, name, clean)
            if name not in names:
                continue
            calls, found = _verdict(tmp, store, read)
            assert (calls, found) == (1, 0), (
                label, name, calls, found,
                'a repair moves the member into _AXES, not into the suite')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dictkeyset_')


if __name__ == '__main__':
    raise SystemExit(main())
