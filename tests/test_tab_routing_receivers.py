#!/usr/bin/env python3
"""Mapping operations that dropped the value their receiver held.

The guard keys deferred storage by name, so a store, a method call and a
mutation resolved its owner only when the receiver was an `ast.Name`; a dict
reached any other way resolved to nothing and the value it held was dropped.
Each receiver here is one spelling of that one defect. A read-back is the
same drop by a different route: `values`, `items`, `popitem` and `copy` hand
a mapping's recorded values back, and the call reader had no arm for any of
them, so every spelling of the read dropped the value, named receiver or not.
"""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_reads import _receiver_value  # noqa: E402
from _pyroute_stores import base_owner, root_name  # noqa: E402
from _pyroute_values import (DeferredAlternatives,  # noqa: E402
                             DeferredContainer)
from _tabroute_focus import _tracked_focus_verdict  # noqa: E402

_CALL = 'send("_focus", "focus-tab", tab=args.chrome_tab)'
_PRELUDE = (f'send = ordinary\n'
            'def maker():\n'
            f'    return lambda: {_CALL}\n'
            'def relay(): return maker()\n'
            'def pair(): return relay(), ordinary\n'
            'def quiet(): return lambda: ordinary\n')

_D = 'def getd(): return d\n'

# The receivers the guard has to resolve, one operation each: a value it
# dropped made a real routing call read clean.
_DEFECTIVE = [
    ('attribute-setdefault', 'class C: pass\nc = C(); c.d = {}\n'
                             'x = c.d.setdefault("k", relay())\n'
                             'send = ext_cmd\nreturn x()'),
    ('attribute-store', 'class C: pass\nc = C(); c.d = {}\n'
                        'c.d["k"] = relay()\n'
                        'send = ext_cmd\nreturn c.d["k"]()'),
    ('subscript-setdefault', 'box = {"d": {}}\n'
                             'x = box["d"].setdefault("k", relay())\n'
                             'send = ext_cmd\nreturn x()'),
    ('call-result-get', f'd = {{"k": relay()}}\n{_D}'
                        'x = getd().get("k", ordinary)\n'
                        'send = ext_cmd\nreturn x()'),
    ('class-attribute-augassign', 'class K: pass\nK.s = {quiet()}\n'
                                  'K.s |= {relay()}\n'
                                  'send = ext_cmd\nreturn [f() for f in K.s]'),
    ('class-attribute-subtract', 'class K: pass\nK.s = {quiet(), relay()}\n'
                                 'K.s -= {quiet()}\n'
                                 'send = ext_cmd\nreturn [f() for f in K.s]'),
    ('subscript-dict-merge', 'box = {}\nbox["d"] = {"k": ordinary}\n'
                             'box["d"] |= {"j": relay()}\n'
                             'send = ext_cmd\nreturn box["d"]["j"]()'),
    ('call-result-setdefault', f'd = {{}}\n{_D}'
                               'x = getd().setdefault("k", relay())\n'
                               'send = ext_cmd\nreturn x()'),
]

# The other half of dropping the owner: a store the guard could not place it
# in was still claimed, so a setdefault on an already-occupied key stored the
# default over the value the runtime keeps, and a clean body was reported.
_OVER_REPORTED = [
    ('occupied-setdefault', 'class C: pass\nc = C(); c.d = {"k": ordinary}\n'
                            'c.d.setdefault("k", relay())\n'
                            'send = ext_cmd\nreturn c.d["k"]()'),
]

# A named receiver is the shape the guard already resolves, and a clean body
# is the shape it must keep reading clean. Both are regressions if they move.
_CONTROLS = [
    ('name-setdefault', 'd = {}\nx = d.setdefault("k", relay())\n'
                        'send = ext_cmd\nreturn x()', (1, 1)),
    ('name-store', 'd = {}\nd["k"] = relay()\n'
                   'send = ext_cmd\nreturn d["k"]()', (1, 1)),
    ('name-get', 'd = {"k": relay()}\nx = d.get("k", ordinary)\n'
                 'send = ext_cmd\nreturn x()', (1, 1)),
    ('call-result-read', f'd = {{"k": relay()}}\n{_D}'
                         'send = ext_cmd\nreturn getd()["k"]()', (1, 1)),
    ('attribute-update', 'class C: pass\nc = C(); c.d = {}\n'
                         'c.d.update(k=relay())\n'
                         'send = ext_cmd\nreturn c.d["k"]()', (1, 1)),
    ('clean-store', 'd = {}\nd["k"] = ordinary\n'
                    'send = ext_cmd\nreturn d["k"]()', (0, 0)),
    ('clean-attribute', 'class C: pass\nc = C(); c.d = {}\n'
                        'c.d["k"] = ordinary\n'
                        'send = ext_cmd\nreturn c.d["k"]()', (0, 0)),
    ('clean-class-attribute', 'class K: pass\nK.s = {quiet()}\n'
                              'K.s |= {quiet()}\n'
                              'send = ext_cmd\nreturn [f() for f in K.s]',
     (0, 0)),
    ('clean-class-subtract', 'class K: pass\nK.s = {quiet()}\n'
                             'K.s -= {quiet()}\n'
                             'send = ext_cmd\nreturn [f() for f in K.s]',
     (0, 0)),
    ('clean-subscript-merge', 'box = {}\nbox["d"] = {"k": ordinary}\n'
                              'box["d"] |= {"k": ordinary}\n'
                              'send = ext_cmd\nreturn box["d"]["k"]()',
     (0, 0)),
    ('name-augment',
     's = {quiet()}\ns |= {relay()}\n'
     'send = ext_cmd\nreturn [f() for f in s]', (1, 1)),
    ('subscript-augment',
     'box = {}\nbox["k"] = {quiet()}\n'
     'box["k"] |= {relay()}\n'
     'send = ext_cmd\nreturn [f() for f in box["k"]]', (1, 1)),
    # These two cross the stored item with the default in both directions, so
    # an unprovable mark, or a resolution that answered with the default
    # whatever the mapping holds, fails one of them. Only resolving the
    # stored-or-default item the `ast.Name` path resolves passes both.
    ('setdefault-stored-clean-default-deferred',
     f'd = {{"k": ordinary}}\n{_D}'
     'x = getd().setdefault("k", relay())\n'
     'send = ext_cmd\nreturn x()', (0, 0)),
    ('setdefault-stored-deferred-default-clean',
     f'd = {{"k": relay()}}\n{_D}'
     'x = getd().setdefault("k", ordinary)\n'
     'send = ext_cmd\nreturn x()', (1, 1)),
    ('setdefault-call-clean', f'd = {{}}\n{_D}'
                              'x = getd().setdefault("k", ordinary)\n'
                              'send = ext_cmd\nreturn x()', (0, 0)),
    # A receiver the model holds nothing for at all: the aggregate is created
    # against the receiver's own base, so the root name keeps naming what it
    # named and the value lands where a later read of the receiver looks.
    ('seed-against-the-base', 'class C: pass\nc = C()\n'
                              'c.d = dict()\nc.d["k"] = relay()\n'
                              'send = ext_cmd\nreturn c.d["k"]()', (1, 1)),
    ('seed-a-class-base', 'class K: pass\nK.d = dict()\n'
                          'K.d["k"] = relay()\n'
                          'send = ext_cmd\nreturn K.d["k"]()', (1, 1)),
    ('clean-seed-a-class-base', 'class K: pass\nK.d = dict()\n'
                                'K.d["k"] = ordinary\n'
                                'send = ext_cmd\nreturn K.d["k"]()', (0, 0)),
    # A receiver two attribute steps from its root name, so the root has to be
    # found by walking the chain rather than by one step.
    ('chain-of-two-receivers', 'class C: pass\nc = C()\n'
                               'box = {"inner": c}\n'
                               'box["inner"].d = dict()\n'
                               'box["inner"].d["k"] = relay()\n'
                               'send = ext_cmd\nreturn box["inner"].d["k"]()',
     (1, 1)),
    # The same slot the plain form pins, read from a callee rather than
    # from the flow that wrote it. The seed is what makes this row go red.
    ('cell-over-one-name',
     'class C: pass\nc = C()\nc.d = dict()\nc.d["k"] = relay()\n'
     'def reader():\n    return c.d["k"]()\n'
     'send = ext_cmd\nreturn reader()', (1, 1)),
    ('attribute-tuple-target',
     'class C: pass\nc = C()\nc.fn, y = pair()\n'
     'send = ext_cmd\nreturn c.fn()', (1, 1)),
    ('subscript-tuple-target',
     'box = {}\nbox["k"], y = pair()\n'
     'send = ext_cmd\nreturn box["k"]()', (1, 1)),
]


def _verdict(tmp, body):
    return _tracked_focus_verdict(tmp, _PRELUDE + body, counts=True)


# A read-back names no key, so it cannot drop a value the way a constant-key
# read does by answering a default: it dropped every one of them, through
# every receiver, because the call reader had no arm for the operation.
_D = 'd = {"k": relay()}\n'
_SEND = 'send = ext_cmd\n'
_TAIL = '\nreturn [f() for f in d.values()]'
_READBACKS = [
    ('values-comprehension', _D + _SEND + _TAIL),
    ('values-loop', _D + _SEND + 'for f in d.values():\n    f()'),
    ('values-bound-view', _D + 'v = d.values()\n' + _SEND
     + '\nreturn [f() for f in v]'),
    ('values-alias-receiver', _D + 'e = d\n' + _SEND
     + '\nreturn [f() for f in e.values()]'),
    # Both receiver rows name the SPELLING, not the operation: a store
    # evaluated at module level. A store in a class body or in `__init__` is
    # dropped by the store side, which keeps only a class's callables and
    # discards a container, so `get()` through the same receiver fails the
    # same way and no read arm can close it.
    ('values-module-level-class-store', 'class K: pass\nK.d = {"k": relay()}\n'
     + _SEND + '\nreturn [f() for f in K.d.values()]'),
    ('values-module-level-instance-store',
     'class K: pass\nk = K()\nk.d = {"k": relay()}\n'
     + _SEND + '\nreturn [f() for f in k.d.values()]'),
    ('values-subscript-receiver', 'box = {"d": {"k": relay()}}\n' + _SEND
     + '\nreturn [f() for f in box["d"].values()]'),
    ('values-after-a-store', 'd = {}\nd["k"] = relay()\n' + _SEND + _TAIL),
    ('values-after-an-update', 'd = {}\nd.update(k=relay())\n'
     + _SEND + _TAIL),
    ('values-dict-call-receiver', 'd = dict(k=relay())\n' + _SEND + _TAIL),
    ('values-two-keys', 'd = {"k": relay(), "j": ordinary}\n' + _SEND + _TAIL),
    ('items-comprehension', _D + _SEND
     + '\nreturn [v() for k, v in d.items()]'),
    ('items-loop', _D + _SEND + 'for k, v in d.items():\n    v()'),
    ('items-through-dict-call', _D + _SEND
     + '\nreturn [f() for f in dict(d.items()).values()]'),
    ('popitem-subscript', _D + _SEND + '\nreturn d.popitem()[1]()'),
    ('popitem-unpack', _D + 'k, v = d.popitem()\n' + _SEND + '\nreturn v()'),
    # A pop on a key the model cannot name leaves the mapping with a count it
    # has lost, and the read-back still has to hand back the value it holds:
    # an unknown count is not an empty mapping.
    ('popitem-after-an-unresolvable-pop',
     _D + 'd.pop("".join(["zz"]), None)\n' + _SEND
     + '\nreturn d.popitem()[1]()'),
    ('popitem-after-an-unresolvable-pop-values',
     _D + 'd.pop("".join(["zz"]), None)\n' + _SEND + _TAIL),
    ('copy-subscript', _D + _SEND + '\nreturn d.copy()["k"]()'),
    ('copy-then-values', _D + _SEND
     + '\nreturn [f() for f in d.copy().values()]'),
    # The wrappers consume the view the read-back hands back, so a value the
    # read dropped is dropped through them as well.
    ('values-in-a-list', _D + _SEND
     + '\nreturn [f() for f in list(d.values())]'),
    ('values-in-a-tuple', _D + _SEND
     + '\nreturn [f() for f in tuple(d.values())]'),
    ('values-in-a-set', _D + _SEND + '\nreturn [f() for f in {*d.values()}]'),
    ('values-in-a-star', _D + _SEND + '\nreturn [f() for f in [*d.values()]]'),
    ('values-sorted', _D + _SEND
     + '\nreturn [f() for f in sorted(d.values(), key=lambda g: 0)]'),
]

# The other half: a mapping holding nothing the guard may route has to keep
# reading clean through every one of those spellings, and a read-back nobody
# consumes is not a route either.
_READBACK_CLEAN = [
    ('clean-values', 'd = {"k": ordinary}\n' + _SEND + _TAIL, (0, 0)),
    ('clean-quiet-value', 'd = {"k": quiet()}\n' + _SEND + _TAIL, (0, 0)),
    ('clean-mixed-values', 'd = {"k": ordinary, "j": quiet()}\n'
     + _SEND + _TAIL, (0, 0)),
    ('clean-items', 'd = {"k": ordinary, "j": quiet()}\n' + _SEND
     + '\nreturn [v() for k, v in d.items()]', (0, 0)),
    ('clean-popitem', 'd = {"k": ordinary}\n' + _SEND
     + '\nreturn d.popitem()[1]()', (0, 0)),
    ('clean-copy', 'd = {"k": ordinary}\n' + _SEND
     + '\nreturn d.copy()["k"]()', (0, 0)),
    ('clean-empty-values', 'd = {}\n' + _SEND + _TAIL, (0, 0)),
    ('clean-unread-value', _D + _SEND + '\nreturn 0', (0, 0)),
    ('clean-never-read', _D + '\nreturn 0', (0, 0)),
    ('clean-copy-unread', _D + _SEND + '\nreturn d.copy()', (0, 0)),
    # A callable as a dict KEY is issue 1012, tracked on its own: a
    # values() read-back hands back the value half, and that half is clean.
    ('clean-callable-key',
     'd = {relay(): ordinary}\n' + _SEND + _TAIL, (0, 0)),
    # A popitem takes the LAST entry, and the model records its mappings in
    # insertion order, so a read after one reads the entries that are left.
    # Each of these fails if the read-back merges every value instead of
    # following the order, or if the removal is not applied at all.
    ('clean-after-popitem', 'd = {"k": ordinary, "j": ordinary}\n'
     'd.popitem()\n' + _SEND + '\nreturn d.get("k", ordinary)()', (0, 0)),
    ('clean-after-popitem-values',
     'd = {"k": ordinary, "j": ordinary}\nd.popitem()\n' + _SEND + _TAIL,
     (0, 0)),
    ('clean-popitem-takes-the-removed-entry',
     'd = {"k": ordinary, "j": relay()}\nd.popitem()\n' + _SEND + _TAIL,
     (0, 0)),
    ('clean-popitem-pairs-the-removed-entry',
     'd = {"k": ordinary, "j": relay()}\nd.popitem()\n' + _SEND
     + '\nreturn d.get("k", ordinary)()', (0, 0)),
    ('clean-one-entry-popitem',
     'd = {"k": ordinary}\nd.popitem()\n' + _SEND + _TAIL, (0, 0)),
    ('clean-popitem-unknown-count', 'd = {"k": ordinary}\n'
     'd.pop("".join(["zz"]), None)\nd.popitem()\n' + _SEND + _TAIL, (0, 0)),
    # A read-back consumed by something that does not CALL it: the value is
    # read and the runtime routes nothing, so the arm must not report.
    ('clean-read-back-not-called', _D.replace('relay()', 'quiet()') + _SEND
     + '\nreturn len(list(d.values()))', (0, 0)),
    # A store through a copy: the copy is a second mapping, so the callable
    # it is given never comes back on a read of the original. A copy holding
    # the ORIGINAL's identity is one container under two names, and this row
    # reports the difference -- the runtime makes no call either way.
    ('clean-store-through-a-copy',
     'd = {"k": ordinary}\nc = d.copy()\nc["j"] = relay()\n'
     + _SEND + _TAIL, (0, 0)),
    ('clean-set-control', 's = {relay()}\n' + _SEND
     + '\nreturn [f() for f in s]', (1, 1)),
]


def test_a_mapping_read_back_reports_the_value_it_holds(tmp):
    for label, body in _READBACKS:
        actual = _verdict(tmp, body)
        assert actual == (1, 1), f'{label}: expected (1, 1), got {actual}'


def test_a_mapping_read_back_stays_clean(tmp):
    for label, body, expected in _READBACK_CLEAN:
        actual = _verdict(tmp, body)
        assert actual == expected, \
            f'{label}: expected {expected}, got {actual}'


# A receiver that is itself a call result resolves the receiver into the
# CALL's own cache slot, so the reader's first line answers the call with the
# mapping and never consults an arm. The read-back arm therefore has to be
# reached before that answer, or this receiver drops the value however the
# read is spelled. Issue 1302.
_CALL_RESULT = 'd = {"k": relay()}\ndef getd(): return d\n'
_READBACK_CALL_RESULT = {
    'call-result-values': _CALL_RESULT + _SEND + _TAIL,
    'call-result-items': (_CALL_RESULT + _SEND
                          + '\nreturn [v() for k, v in getd().items()]'),
    'call-result-popitem': (_CALL_RESULT + _SEND
                            + '\nreturn getd().popitem()[1]()'),
    'call-result-copy': (_CALL_RESULT + _SEND
                         + '\nreturn getd().copy()["k"]()'),
    'call-result-copy-values': (_CALL_RESULT + _SEND
                                + '\nreturn [f() for f in'
                                + ' getd().copy().values()]'),
    'call-result-bound-view': (_CALL_RESULT + 'v = getd().values()\n' + _SEND
                               + '\nreturn [f() for f in v]'),
}
_READBACK_CALL_RESULT_CLEAN = {
    'clean-call-result-values': (
        'd = {"k": ordinary}\ndef getd(): return d\n' + _SEND + _TAIL, (0, 0)),
    'clean-call-result-items': (
        'd = {"k": ordinary, "j": quiet()}\ndef getd(): return d\n' + _SEND
        + '\nreturn [v() for k, v in getd().items()]', (0, 0)),
    'clean-call-result-popitem': (
        'd = {"k": ordinary}\ndef getd(): return d\n' + _SEND
        + '\nreturn getd().popitem()[1]()', (0, 0)),
    'clean-call-result-copy': (
        'd = {"k": ordinary}\ndef getd(): return d\n' + _SEND
        + '\nreturn getd().copy()["k"]()', (0, 0)),
}


def test_a_call_result_receiver_reads_the_mapping_back(tmp):
    verdicts = {label: _verdict(tmp, body)
                for label, body in _READBACK_CALL_RESULT.items()}
    assert verdicts == dict.fromkeys(_READBACK_CALL_RESULT, (1, 1)), verdicts


def test_a_call_result_receiver_stays_clean(tmp):
    verdicts = {label: _verdict(tmp, body) for label, (body, _)
                in _READBACK_CALL_RESULT_CLEAN.items()}
    assert verdicts == {label: expected for label, (_, expected)
                        in _READBACK_CALL_RESULT_CLEAN.items()}, verdicts


def test_a_receiver_that_is_not_a_name_still_reports(tmp):
    for label, body in _DEFECTIVE:
        actual = _verdict(tmp, body)
        assert actual == (1, 1), f'{label}: expected (1, 1), got {actual}'


def test_a_store_the_guard_could_not_place_stays_clean(tmp):
    for label, body in _OVER_REPORTED:
        actual = _verdict(tmp, body)
        assert actual == (0, 0), f'{label}: expected (0, 0), got {actual}'


def test_a_named_receiver_and_a_clean_body_are_unmoved(tmp):
    for label, body, expected in _CONTROLS:
        actual = _verdict(tmp, body)
        assert actual == expected, \
            f'{label}: expected {expected}, got {actual}'


def test_root_name_walks_a_chain_and_stops_at_the_first_name(tmp):
    """The handle a store through any spelling has, pinned directly.

    Every verdict above is a consequence of this, but each reaches it
    through a receiver the tree already holds, so a chain the store path
    never has to walk is not something a verdict can distinguish."""
    del tmp
    chain = [node.value for node in ast.parse(
        'outer.inner.d\nouter["inner"].d\nc\ngetd()').body
        if isinstance(node, ast.Expr)]
    assert [root_name(node) for node in chain[:2]] == ['outer', 'outer']
    assert root_name(chain[2]) == 'c'
    assert root_name(chain[3]) is None


def test_base_owner_names_the_root_and_carries_the_value(tmp):
    """A receiver that is not a name answers both halves, or the write path
    has no handle to write through."""
    del tmp
    state = SimpleNamespace(callables={}, evaluated={})
    name, attribute, call = [node.value for node
                             in ast.parse('c\nc.d\ngetd()').body
                             if isinstance(node, ast.Expr)]
    assert base_owner(name, state) == ('c', None)
    assert base_owner(attribute, state) == ('c', None)
    assert base_owner(call, state) == (None, None)


def test_a_subscript_receiver_resolves_through_its_own_binding(tmp):
    """No structural subscript arm: this is the assertion that fails if one
    returns. The arm was reached but never decided an outcome, so the
    receiver resolves through the binding the flow already recorded."""
    del tmp
    marked = DeferredAlternatives(('ext_cmd',))
    state = SimpleNamespace(
        callables={'box': DeferredContainer({0: marked}, 1, 'list')},
        evaluated={})
    receiver = ast.parse('box[0]', mode='eval').body
    assert _receiver_value(receiver, state) is None


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='tab_routing_receivers_')


if __name__ == '__main__':
    raise SystemExit(main())
