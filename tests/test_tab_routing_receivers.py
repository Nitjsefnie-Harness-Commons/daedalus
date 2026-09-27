#!/usr/bin/env python3
"""Mapping operations whose receiver is not a bare name.

The guard keys deferred storage by name, so every store, method call and
mutation resolved its owner only when the receiver was an `ast.Name`. A dict
reached through an attribute, a subscript or a call result resolved to
nothing, the deferred value it held was dropped, and a call that really does
reach `ext_cmd` with a `tab` read clean. The owner's shape is the whole
defect: each receiver here is the one spelling of it.
"""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_stores import base_owner, root_name  # noqa: E402
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
    # The rewritten container is read inside a closure, so the cell that
    # carries its name has to be snapshotted when the write lands.
    ('cell-over-one-name',
     'class C: pass\nc = C()\nc.d = dict()\nc.d["k"] = relay()\n'
     'def reader():\n    return c.d["k"]()\n'
     'send = ext_cmd\nreturn reader()', (1, 1)),
    ('cell-over-an-alias',
     'd = {}\ne = d\nd["k"] = relay()\n'
     'def reader():\n    return e["k"]()\n'
     'send = ext_cmd\nreturn reader()', (1, 1)),
    ('clean-cell-over-an-alias',
     'd = {}\ne = d\nd["k"] = ordinary\n'
     'def reader():\n    return e["k"]()\n'
     'send = ext_cmd\nreturn reader()', (0, 0)),
    ('attribute-tuple-target',
     'class C: pass\nc = C()\nc.fn, y = pair()\n'
     'send = ext_cmd\nreturn c.fn()', (1, 1)),
    ('subscript-tuple-target',
     'box = {}\nbox["k"], y = pair()\n'
     'send = ext_cmd\nreturn box["k"]()', (1, 1)),
]


def _verdict(tmp, body):
    return _tracked_focus_verdict(tmp, _PRELUDE + body, counts=True)


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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='tab_routing_receivers_')


if __name__ == '__main__':
    raise SystemExit(main())
