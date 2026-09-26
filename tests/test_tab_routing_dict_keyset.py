#!/usr/bin/env python3
"""A constant-key read of a dict the model cannot enumerate fails closed.

A tracked dict has two legal readings. When the model can account for its
key set, a read answers the recorded value or the read's own default, and a
key it knows is absent really is absent. When it cannot -- an unknown length
with no unknown-key slot standing for what it never learned -- a read of a
key the model never recorded may select anything the unenumerated part
carried, so it joins instead of answering "nothing". Answering "nothing" is
the silent outcome this suite exists to keep empty.

**The silent bucket is empty by name.** `_AXES` is the census: one member
per way a tracked dict acquires a key the model did not learn, driven from
the mutator rather than from a spelling, over the three axes that domain
spans -- the mutator, the COMBINATION of a readable and an unreadable
source in one call, and the FRESHNESS of a value the model recorded before
an unreadable source could have replaced it. `_DISPOSITION` names every
member's outcome, and `test_every_axis_member_is_refused_or_declared` fails
on a member present in one and not the other, so the bucket cannot grow by
omission. `test_an_unlisted_member_of_the_domain_is_rejected` hands the
census a member of its own stated domain that it does not list and requires
the census to consider it. `_SILENT` carries the members of the same domain
that do read silent: each names the issue it is parked against, and
`test_every_silent_member_is_listed_and_not_refused` pins the measurement,
so a repair turns this suite red on the commit that moves the member into
`_AXES` rather than leaving it listed as unconsidered.

The two outcomes differ in what the read costs once nothing is routed. A
refused read resolves to a tracked callable, so the guard reads that
callable's body and the shape stays clean. A declared read joins to an
unprovable sender, and a `tab` through a name holding one is reported
whatever that callable would have done. WHICH read forms pay that is the
member's own split, not the domain's, and `_SILENT` carries it per member.

**Not this suite's bucket.** A store path that folds an unreadable source
into a `DYNAMIC_KEY` slot -- `dict(...)`, `{**...}`, `|=`, `update(...)`,
`update(**...)`, a plain assignment -- leaves the value at the key already
joined to an unprovable sender. A bare routed-lambda call through it still
reads clean, because a `tab` living in the callee's body is not a reporting
shape for an unprovable sender. That is a different mechanism, tracked as
1010, and none of those members is a row here -- except through a source the
model already holds unaccountable, which no store folds at all and which
`_SILENT` carries by read form.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_reads import _mapping_lookup  # noqa: E402
from _pyroute_values import (DYNAMIC_KEY, UNPROVABLE_SENDER,  # noqa: E402
                             DeferredContainer)
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
    # A key recorded before the dict stopped being enumerable: the read
    # still resolves, because the recorded value is the one the MODEL
    # recorded, and nothing unreadable has overwritten that key since.
    'recorded-key': ('d = {}\nd.update(zip(["j"], [1]))\nd["k"] = relay()\n'
                     'd.update(zip(["x"], [1]))'),
    'literal': 'd = {"k": relay()}',
}

# `refused`: the read resolves to a tracked callable, so the guard reads the
# callable's own body. `declared`: the read joins to an unprovable sender.
# The second column is what the member's SHAPE costs once nothing is routed,
# and it is not the disposition alone: a member whose own name carries an
# unprovable alias costs `(0, 1)` whatever its read says, because the
# visible `tab` reaches that alias through the call site. `recorded-key`
# is the case that separates the two -- its read resolves, and its name is
# still marked.
_DISPOSITION = {
    'update-pairs': ('refused', (0, 0)),
    'update-pairs-tuple': ('refused', (0, 0)),
    'update-dict': ('refused', (0, 0)),
    'update-keyword': ('refused', (0, 0)),
    'update-zip': ('declared', (0, 1)),
    'update-frozenset': ('declared', (0, 1)),
    'update-unreadable': ('declared', (0, 1)),
    'update-star': ('refused', (0, 0)),
    'update-star-two': ('refused', (0, 0)),
    'update-star-mixed': ('refused', (0, 0)),
    'update-star-mixed-unreadable': ('declared', (0, 1)),
    'update-star-mixed-unreadable-other': ('declared', (0, 1)),
    'update-star-unreadable': ('declared', (0, 1)),
    'ior-unreadable': ('declared', (0, 1)),
    'ior-modelled': ('refused', (0, 0)),
    'setdefault-value': ('refused', (0, 0)),
    'setdefault-vacant': ('refused', (0, 0)),
    'dict-call': ('refused', (0, 0)),
    'dict-call-star': ('refused', (0, 0)),
    'dict-literal-star': ('refused', (0, 0)),
    'dict-literal-star-unreadable': ('declared', (0, 1)),
    'assign-unreadable': ('declared', (0, 1)),
    'copy-unreadable': ('declared', (0, 1)),
    'recorded-key': ('refused', (0, 1)),
    'literal': ('refused', (0, 0)),
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
}

# Which read forms a parked member is silent on, per member: the freshness
# stores report on the subscript and read clean on the container reads, the
# starred source the other way round.
_CONTAINER_READS = ('get', 'setdefault')
_SUBSCRIPT = ('subscript',)

# The rest of the domain, at the polarity that still reads silent. A key the
# model recorded while it could still see the value, and an unreadable source
# has since put something else there, or a source the model folds and then
# loses: the read answers the recorded value or its own default, so a
# `relay()` the runtime really does call is reported nothing. Each names the
# issue it is parked against, because the census's value is that its
# unconsidered bucket is empty BY NAME. The fourth field is the clean cost of
# the read forms the member does NOT name, which its own shape already
# reports through.
_SILENT = {
    'stale-recorded-zip': (
        'd = {"k": ordinary}\nd.update(zip(["k"], [relay()]))', 1154,
        _CONTAINER_READS, (0, 1)),
    'stale-recorded-frozenset': (
        'd = {"k": ordinary}\nd.update(frozenset([("k", relay())]))', 1154,
        _CONTAINER_READS, (0, 1)),
    'stale-recorded-later-store': (
        'd = {"k": ordinary}\nd.update(zip(["j"], [1]))'
        '\nd.update(zip(["k"], [relay()]))', 1154, _CONTAINER_READS, (0, 1)),
    # A positional source reached through a star, so the container answers
    # the subscript from the fold and joins the two container reads instead.
    'update-starred-source': (
        'd = {}\nd.update(*[zip(["k"], [relay()])])', 1162, _SUBSCRIPT,
        (0, 1)),
    # A source reached through a NAME the model already holds as
    # unaccountable. It takes `_source_items`' `(items, False)` branch, so the
    # store never reaches `_mark_unprovable` on the OWNER: the container lands
    # at `items=[]`, `length=None`, no unknown-key slot, and every read of it
    # answers a clean absence. The container reads join anyway, off the marked
    # SOURCE name; the subscript binds the callable itself, which is #1010's
    # invoke arm on a bare source and #1162's on a starred one. `dict(<name>)`
    # is the same defect silent on all three forms, filed as 1163.
    'update-unaccountable-name': (
        _UNACCOUNTABLE + '\nd = {}\nd.update(o)', 1010, _SUBSCRIPT, (0, 1)),
    'update-unaccountable-name-star': (
        _UNACCOUNTABLE + '\nd = {}\nd.update(**o)', 1162, _SUBSCRIPT, (0, 1)),
    'update-unaccountable-name-doubled': (
        _UNACCOUNTABLE + '\nd = {}\nd.update(**{**o})', 1162, _SUBSCRIPT,
        (0, 1)),
    'ior-unaccountable-name': (
        _UNACCOUNTABLE + '\nd = {}\nd |= o', 1010, _SUBSCRIPT, (0, 1)),
}


def _body(store, read, prefix):
    return f'{prefix}{store}\nx = {read}\nsend = ext_cmd\nreturn x{_CALL}'


def _verdict(tmp, store, read, prefix=_PRE):
    return _tracked_focus_verdict(
        tmp, _body(store, read, prefix), counts=True)


def test_every_axis_member_is_refused_or_declared(tmp):
    """The silent bucket is empty by name, not by assertion: both
    directions of the census fail here rather than passing unnoticed."""
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
    member = ('d = {}\nd.update(zip(["j"], [1]))'
              '\nd.update(zip(["k"], [relay()]))')
    assert member not in _AXES.values()
    for name, read in sorted(_READS.items()):
        assert _verdict(tmp, member, read) == (1, 1), (name, read)
        assert _verdict(tmp, member, read, _CLEAN) == (0, 1), (name, read)


def test_an_accountable_key_read_stays_clean(tmp):
    """The false-positive limb, end to end: the model can see the key, or
    can see that it is absent, and the read yields a value that routes
    nothing."""
    for label, (store, read) in sorted(_ACCOUNTED.items()):
        assert _verdict(tmp, store, read) == (0, 0), (label, read)
        assert _verdict(tmp, store, read, _CLEAN) == (0, 0), (label, read)


def test_the_mapping_read_joins_only_the_keys_it_never_recorded(tmp):
    """The read that answers from the container, on containers the store
    paths cannot be driven into. An unknown length with no unknown-key slot
    is the state the join is for; a recorded key is the state it leaves
    alone, because the recorded value is what the MODEL recorded, which is
    the value the runtime holds only while nothing unreadable has
    overwritten that key since -- `_SILENT` is the other polarity of that
    condition and no container-level pair can express it, since a held
    key's recorded value is whatever this table puts there. A countable
    mapping answers an absent key with the read's own default, as before.
    A join that ignored `items` would answer an unprovable sender for `k`
    as well."""
    recorded = 'ext_cmd'  # a recorded sender value, held at its key
    default = object()  # the read's own default, which nothing records
    items = {'k': recorded}
    unaccountable = DeferredContainer(dict(items), None, 'dict')
    countable = DeferredContainer(dict(items), 1, 'dict')
    marked = DeferredContainer(
        {**items, DYNAMIC_KEY: UNPROVABLE_SENDER}, None, 'dict')
    assert _mapping_lookup(unaccountable, 'k', default) == recorded
    assert _mapping_lookup(countable, 'k', default) == recorded
    assert _mapping_lookup(unaccountable, 'j', default) \
        == UNPROVABLE_SENDER
    assert _mapping_lookup(marked, 'j', default) == UNPROVABLE_SENDER
    assert _mapping_lookup(countable, 'j', default) is default
    assert _mapping_lookup(unaccountable, 'j', None) == UNPROVABLE_SENDER
    assert _mapping_lookup(countable, 'j', None) is None


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
    assert all(isinstance(issue, int) and issue > 0
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
