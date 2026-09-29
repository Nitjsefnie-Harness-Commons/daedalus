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
an unreadable source could have replaced it. `_DISPOSITION` names every
member's outcome, and `test_every_axis_member_is_refused_or_declared` fails
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
whatever that callable would have done. WHICH read forms pay that is the
member's own split, not the domain's, and `_SILENT` carries it per member.

**Not this suite's bucket.** A store path that folds an unreadable source
into its OWNER's `DYNAMIC_KEY` slot -- `dict(...)`, `{**...}`, `|=`,
`update(...)`, `update(**...)`, a plain assignment -- leaves the value at the
key already joined to an unprovable sender. A bare routed-lambda call through
it still reads clean, because a `tab` living in the callee's body is not a
reporting shape for an unprovable sender. That is a different mechanism,
tracked as 1010, and a member reached only that way is not in this suite's
bucket. What separates the `_SILENT` rows from those is WHICH NAME carries
the fold, and not every member has a name to answer for. A store that marks
its owner answers joined at the key being read; a store that propagates an
unknown length from a source reached through a NAME leaves the fold on the
source name, so the owner's own reads answer whatever its recorded items
say; and a starred positional source marks neither name, its fold sitting on
no name at all. Those shapes are what `_SILENT` names, with the read form
each member is silent on carried per member.
"""
import ast
import sys
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_mapping import _apply_mapping_store  # noqa: E402
from _pyroute_reads import (_dict_call_value, _dict_value,  # noqa: E402
                            _mapping_lookup, _merge_or_value)
from _pyroute_state import FlowState  # noqa: E402
from _pyroute_stores import _subscript_store  # noqa: E402
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,  # noqa: E402
                             DeferredAlternatives, DeferredContainer)
from test_tab_routing import _tracked_focus_verdict  # noqa: E402

_LAMBDA = ("lambda *a, **k: send('_focus', 'focus-tab', "
           "tab=args.chrome_tab)")
_RELAY = ('def maker():\n    return ' + _LAMBDA + '\n'
          'def relay(): return maker()\n')
_PRE = f'send = ordinary\n{_RELAY}'
_CLEAN = ('send = ordinary\ndef maker():\n    return lambda *a, **k: '
          'ordinary()\ndef relay(): return maker()\n')

# The read under test binds its result, so what moves is the read's own
# verdict: an absent answer binds nothing, a joined answer binds an
# unprovable sender, and a `tab` through either is reported only for the
# second.
_READS = {'subscript': 'd["k"]', 'get': 'd.get("k")',
          'setdefault': 'd.setdefault("k")'}
_CALL = '(1, tab=args.flag)'

# A source the model cannot read as pairs.
_UNREADABLE = 'def mk():\n    return dict(zip(["k"], [relay()]))\n'

# A source the model cannot read as pairs, reached through a NAME.
_UNACCOUNTABLE = 'o = {}\no.update(zip(["k"], [relay()]))'

# A key the model recorded BEFORE a store that retires it, written back
# after it. What the model wrote there is what the runtime holds, so the
# read answers from it; the three forms differ only in how the key is made
# current again, and the third in a removal the model follows, which takes
# an entry out without writing anything anywhere.
#
# The retiring store has to STAY OPAQUE, which is the whole point of it: a
# source whose pairs its own syntax spells is read, folds its keys
# exactly, and retires nothing. `mk()` is a user call, so its source is
# never readable and the retirement is what this limb is measured against.
_OPAQUE_RETIRE = 'd = {"k": ordinary}\n' + _UNREADABLE + 'd.update(mk())\n'
_FRESH = _OPAQUE_RETIRE + '\nd["k"] = relay()'
_REFRESHED = _OPAQUE_RETIRE + '\nd.update({"k": relay()})'
_POPPED = _FRESH + '\nd.pop("j", None)'

# A store whose key the model cannot resolve may have named the key the
# container already held, but it also wrote the value it read to the
# unknown-key slot every read arm joins, so it retires nothing: the store's
# own fold already accounts for the computed key, and more precisely.
_COMPUTED = 'd = {"k": ordinary}\nd[args.values] = relay()'

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
    'stale-unaccountable-name': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd.update(o)'),
    'stale-unaccountable-name-star': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd.update(**o)'),
    'stale-unaccountable-name-doubled': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd.update(**{**o})'),
    'ior-stale-unaccountable-name': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd |= o'),
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
    'ior-unaccountable-name': _UNACCOUNTABLE + '\nd = {}\nd |= o',
    'update-unaccountable-name-mixed': (
        _UNACCOUNTABLE + '\nd = {}\nd.update([("a", 1)], **o)'),
    'update-unaccountable-name-mixed-keyed': (
        _UNACCOUNTABLE + '\nd = {}\nd.update(**{"a": 1}, **o)'),
    'update-unaccountable-name-mixed-doubled': (
        _UNACCOUNTABLE + '\nd = {}\nd.update(**{**{"a": 1}, **o})'),
    # The same source through a constructor store, which `_dict_call_value`
    # answers from the same fold. Filed as 1163: this branch repairs the
    # rows and closes 1154 and 1178, and 1163 stays open on its own rows.
    'dict-name': _UNACCOUNTABLE + '\nd = dict(o)',
    'dict-name-star': _UNACCOUNTABLE + '\nd = dict(**o)',
    'dict-stale-name': (
        _UNACCOUNTABLE + '\nd = {"k": ordinary}\nd = dict(o)'),
    'ior-after-dict-name': _UNACCOUNTABLE + '\nd = dict(o)\nd |= {"j": 1}',
    'literal': 'd = {"k": relay()}',
}

# `refused`: the read resolves to a tracked callable, so the guard reads the
# callable's own body. `declared`: the read joins to an unprovable sender.
# The second column is what the member's SHAPE costs once nothing is routed,
# and it is not the disposition alone: a member whose own name carries an
# unprovable alias costs `(0, 1)` whatever its read says, because the
# visible `tab` reaches that alias through the call site. `fresh-recorded-key`
# is the case that separates the two -- its read resolves, and its name is
# still marked.
_DISPOSITION = {
    'update-pairs': ('refused', (0, 0)),
    'update-pairs-tuple': ('refused', (0, 0)),
    'update-dict': ('refused', (0, 0)),
    'update-keyword': ('refused', (0, 0)),
    'update-zip': ('refused', (0, 0)),
    'update-frozenset': ('refused', (0, 0)),
    'update-unreadable': ('refused', (0, 0)),
    'update-star': ('refused', (0, 0)),
    'update-star-two': ('refused', (0, 0)),
    'update-star-mixed': ('refused', (0, 0)),
    'update-star-mixed-unreadable': ('refused', (0, 0)),
    'update-star-mixed-unreadable-other': ('refused', (0, 0)),
    'update-star-unreadable': ('refused', (0, 0)),
    'ior-unreadable': ('refused', (0, 0)),
    'ior-modelled': ('refused', (0, 0)),
    'setdefault-value': ('refused', (0, 0)),
    'setdefault-vacant': ('refused', (0, 0)),
    'dict-call': ('refused', (0, 0)),
    'dict-call-star': ('refused', (0, 0)),
    'dict-literal-star': ('refused', (0, 0)),
    'dict-literal-star-unreadable': ('refused', (0, 0)),
    'assign-unreadable': ('refused', (0, 0)),
    'copy-unreadable': ('refused', (0, 0)),
    'recorded-key': ('refused', (0, 0)),
    'fresh-recorded-key': ('refused', (0, 0)),
    'fresh-recorded-key-updated': ('refused', (0, 0)),
    'fresh-recorded-key-popped': ('refused', (0, 0)),
    'stale-recorded-zip': ('refused', (0, 0)),
    'stale-recorded-frozenset': ('refused', (0, 0)),
    'stale-recorded-later-store': ('refused', (0, 0)),
    'stale-unaccountable-name': ('refused', (0, 0)),
    'stale-unaccountable-name-star': ('refused', (0, 0)),
    'stale-unaccountable-name-doubled': ('refused', (0, 0)),
    'ior-stale-unaccountable-name': ('refused', (0, 0)),
    'literal': ('refused', (0, 0)),
    'update-unaccountable-name': ('refused', (0, 0)),
    'update-unaccountable-name-star': ('refused', (0, 0)),
    'update-unaccountable-name-doubled': ('refused', (0, 0)),
    'ior-unaccountable-name': ('refused', (0, 0)),
    'update-unaccountable-name-mixed': ('refused', (0, 0)),
    'update-unaccountable-name-mixed-keyed': ('refused', (0, 0)),
    'update-unaccountable-name-mixed-doubled': ('refused', (0, 0)),
    'dict-name': ('refused', (0, 0)),
    'dict-name-star': ('refused', (0, 0)),
    'dict-stale-name': ('refused', (0, 0)),
    'ior-after-dict-name': ('refused', (0, 0)),
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
    # The freshness limb on an ordinary value, where a join and the
    # recorded value are not the same verdict: a joined read binds an
    # unprovable sender and the call through it reports, so `(0, 1)` here
    # would be the false positive the rule is drawn to avoid.
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
        'd = {}\nd.update(*[zip(["k"], [relay()])])', 1162, _SUBSCRIPT,
        (0, 1)),
}


def _body(store, read, prefix):
    return f'{prefix}{store}\nx = {read}\nsend = ext_cmd\nreturn x{_CALL}'


def _verdict(tmp, store, read, prefix=_PRE):
    return _tracked_focus_verdict(
        tmp, _body(store, read, prefix), counts=True)


def test_every_axis_member_is_refused_or_declared(tmp):
    """The two tables agree in both directions: a member in one and not the
    other fails here rather than passing unnoticed."""
    assert sorted(_DISPOSITION) == sorted(_AXES), sorted(
        set(_AXES) ^ set(_DISPOSITION))
    assert {outcome for outcome, _ in _DISPOSITION.values()} \
        <= {'refused', 'declared'}


def test_no_axis_member_reads_a_key_the_model_never_recorded_clean(tmp):
    """Every member of every axis, over every read form: one runtime call
    really does reach `ext_cmd`, and the guard reports it. A member the
    read leaves silent fails here. The count is not pinned -- a member can
    be reported by the callable's own body, by an unprovable alias, or by
    both -- so the invariant is the lower bound, not an exact figure."""
    for label in sorted(_DISPOSITION):
        for name, read in sorted(_READS.items()):
            calls, found = _verdict(tmp, _AXES[label], read)
            assert (calls, found >= 1) == (1, True), (
                label, name, calls, found)


def test_a_declared_read_costs_where_a_refused_read_does_not(tmp):
    """The name's own alias and the read's verdict contribute to a
    member's cost separately, so it is pinned per member."""
    for label, (_, cost) in sorted(_DISPOSITION.items()):
        for name, read in sorted(_READS.items()):
            clean = _verdict(tmp, _AXES[label], read, _CLEAN)
            assert clean == cost, (label, name, clean)


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


# A source the model has retired a key in, and every spelling of a fold
# that builds a destination out of its items. The retirement is a fact
# about the values the source recorded, so it has to travel with them: the
# destination cannot claim at a key the source has disowned that its own
# value there is current.
#
# The end-to-end control below needs the source to retire the key the
# RUNTIME also replaces, or nothing routes through the destination and the
# read reads clean for a reason that has nothing to do with the marker: a
# `zip` over a different key leaves the recorded value standing, and the
# control then measures a false positive where the defect lives.
_RETIRED_SOURCE = _OPAQUE_RETIRE
_DESTINATION_FOLDS = ('o = {}\no.update(d)', 'o = {}\no |= d',
                      'o = dict(d)', 'o = {**d}')
_DESTINATION_READS = {'subscript': 'o["k"]', 'get': 'o.get("k")',
                      'setdefault': 'o.setdefault("k")'}
_FOLDS = ('update', 'update-star', 'ior', 'display', 'or-value', 'dict-call')


def _retired_source_state():
    """A state holding one source that recorded a key and then retired it."""
    state = FlowState({}, {}, {}, {}, set(), set(), {}, set())
    state.callables['d'] = DeferredContainer(
        {'k': None}, None, 'dict', stale=frozenset({'k'}))
    return state


def _fold_into_destination(state) -> DeferredContainer:
    """Run the store fold of a retired source into a bound destination."""
    owner = DeferredContainer({}, 0, 'dict')
    state.callables['o'] = owner
    _apply_mapping_store(state, owner, 'o',
                         [ast.Name(id='d', ctx=ast.Load())], {}, None)
    return cast(DeferredContainer, state.callables['o'])


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
    # A source that recorded a key gives every one of these something to
    # fold, so none of them is the empty result they answer with.
    assert folded is not None, spelling
    return folded


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
            assert _verdict(tmp, body, source) == (1, 1), (fold, read)
            assert _verdict(tmp, body, source, _CLEAN) == (0, 0), (
                fold, read)


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
    `_AXES` nor `_DISPOSITION` carries is a silent read nobody is looking
    for, so each one left is listed against the issue it is parked on and
    PINNED: the pin is the defect, so a repair turns this red on the very
    commit that has to move the member into `_AXES`. The clean counterpart
    is pinned beside it, on EVERY read form rather than only the silent one,
    because a fix that made every read of that key join would be a new false
    positive rather than a repair -- and which form that would be is the
    member's own split, so neither can be assumed."""
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
