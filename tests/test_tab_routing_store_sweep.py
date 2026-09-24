#!/usr/bin/env python3
"""Runtime-and-guard verdict table for every mapping-store form."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute import py_tab_routing_violations  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402

_CALL = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
_LAMBDA = f'lambda: {_CALL}'
_FORWARD = 'lambda *a, **k: send(*a, **k)'
_UNKNOWN = ('pool = []\n'
            'def unknown():\n'
            '    return pool[0]\n')
_TABCALL = "box['k']('_focus', 'focus-tab', tab=int(args.chrome_tab))"
_RELAY = ('def maker():\n    return ' + _LAMBDA + '\n'
          'def relay(): return maker()\n'
          'def pair(): return relay(), ordinary\n')


def _flow(*lines, invoke=None):
    body = ['send = ordinary', *lines]
    if invoke is not None:
        body.extend(('send = ext_cmd', f'return {invoke}'))
    return '\n'.join(body)


def _ext_flow(*lines):
    """A body whose every sender is the extension one."""
    return '\n'.join(('send = ext_cmd', _RELAY, *lines))


CASES = [
    ('subscript-store', _flow(
        'box = {}', f'box["k"] = {_LAMBDA}', invoke='box["k"]()'), (1, 1)),
    ('update-literal', _flow(
        'box = {}', 'box.update({"k": ' + _LAMBDA + '})',
        invoke='box["k"]()'), (1, 1)),
    ('update-name', _flow(
        'box = {}', 'other = {}', f'other["k"] = {_LAMBDA}',
        'box.update(other)', invoke='box["k"]()'), (1, 1)),
    ('update-untracked-call', _flow(
        'box = {}', 'def build():\n    return {"k": ' + _LAMBDA + '}',
        'box.update(build())', invoke='box["k"]()'), (1, 1)),
    ('update-keyword', _flow(
        'box = {}', 'box.update(k=' + _LAMBDA + ')',
        invoke='box["k"]()'), (1, 1)),
    ('update-pairs', _flow(
        'box = {}', 'box.update([("k", ' + _LAMBDA + ')])',
        invoke='box["k"]()'), (1, 1)),
    ('setdefault-store', _flow(
        'box = {}', f'box.setdefault("k", {_LAMBDA})',
        invoke='box["k"]()'), (1, 1)),
    ('setdefault-return', _flow(
        'box = {}', f'box.setdefault("k", {_LAMBDA})',
        invoke='box.setdefault("k", lambda: ordinary())()'), (1, 1)),
    ('dict-comp', _flow(
        'd = {key: ' + _LAMBDA + ' for key in ["a"]}',
        invoke='d["a"]()'), (1, 1)),
    ('dict-pairs', _flow(
        'd = dict([("k", ' + _LAMBDA + ')])', invoke='d["k"]()'), (1, 1)),
    ('dict-kwargs', _flow(
        'd = dict(k=' + _LAMBDA + ')', invoke='d["k"]()'), (1, 1)),
    ('update-positional-unknown', _flow(
        'box = {}', _UNKNOWN, "pool.append({'k': " + _FORWARD + '})',
        'box.update(unknown())', invoke=_TABCALL), (1, 1)),
    ('dict-copy', _flow(
        'box = {}', f'box["k"] = {_LAMBDA}', 'd = dict(box)',
        invoke='d["k"]()'), (1, 1)),
    ('ior-literal', _flow(
        'box = {}', 'box |= {"k": ' + _LAMBDA + '}',
        invoke='box["k"]()'), (1, 1)),
    ('fromkeys', _flow(
        'd = dict.fromkeys(["k"], ' + _LAMBDA + ')',
        invoke='d["k"]()'), (1, 1)),
    ('spread-merge', _flow(
        'box = {}', f'box["j"] = {_LAMBDA}', 'd = {**box}',
        invoke='d["j"]()'), (1, 1)),
    ('pipe-merge', _flow(
        'box = {}', 'box["j"] = lambda: ordinary()',
        'd = box | {"k": ' + _LAMBDA + '}',
        invoke='d["k"]()'), (1, 1)),
    ('computed-key', _flow(
        'box = {}', 'key = "k"', 'box[key] = ' + _LAMBDA,
        invoke='box[key]()'), (1, 1)),
    ('assign-unknown-tracked', _flow(
        'box = {"k": ' + _LAMBDA + '}', _UNKNOWN,
        f'pool.append({_LAMBDA})', 'box["k"] = unknown()',
        invoke='box["k"]()'), (1, 1)),
    ('assign-unknown-untracked', _flow(
        'box = {}', _UNKNOWN, f'pool.append({_FORWARD})',
        'box["k"] = unknown()', invoke=_TABCALL), (1, 1)),
    ('spread-unknown', _flow(
        _UNKNOWN, "pool.append({'j': " + _FORWARD + '})',
        'd = {**unknown()}',
        invoke="d['j']('_focus', 'focus-tab', tab=int(args.chrome_tab))"),
     (1, 1)),
    ('pipe-unknown', _flow(
        'box = {}', _UNKNOWN, "pool.append({'k': " + _FORWARD + '})',
        'd = box | unknown()',
        invoke="d['k']('_focus', 'focus-tab', tab=int(args.chrome_tab))"),
     (1, 1)),
    ('update-keyword-unknown', _flow(
        'box = {}', _UNKNOWN, f'pool.append({_FORWARD})',
        'box.update(k=unknown())', invoke=_TABCALL), (1, 1)),
    ('setdefault-unknown', _flow(
        'box = {}', _UNKNOWN, f'pool.append({_FORWARD})',
        'box.setdefault("k", unknown())', invoke=_TABCALL), (1, 1)),
    ('dict-unknown', _flow(
        _UNKNOWN, "pool.append({'k': " + _FORWARD + '})',
        'd = dict(unknown())',
        invoke="d['k']('_focus', 'focus-tab', tab=int(args.chrome_tab))"),
     (1, 1)),
    ('annassign-unknown', _flow(
        'box = {}', _UNKNOWN, f'pool.append({_FORWARD})',
        'box["k"]: object = unknown()', invoke=_TABCALL), (1, 1)),
    ('spread-unknown-name', _flow(
        _UNKNOWN, "pool.append({'j': " + _FORWARD + '})',
        'box = unknown()', 'd = {**box}',
        invoke="d['j']('_focus', 'focus-tab', tab=int(args.chrome_tab))"),
     (1, 1)),
    ('iteration-of-copy', _flow(
        'box = {}', f'box["k"] = {_LAMBDA}', 'copy = dict(box)',
        'list(copy)', 'list(box)'), (0, 0)),
    ('clean-store-invocation', _flow(
        'box = {}', 'box["k"] = lambda: ordinary()',
        invoke='box["k"]()'), (0, 0)),
    ('delete-keeps-pop', _flow(
        'box = {"k": ' + _LAMBDA + '}', 'del box["k"]',
        invoke='(box["k"]() if "k" in box else None)'), (0, 0)),
    ('issue857-pair-list-dynamic', _flow(
        _RELAY, 'e = {}; e.update([("k", ordinary), (None, relay())])',
        'x = e.setdefault("k", relay())', 'y = e.get(None, ordinary)',
        invoke='x(), y()'), (1, 1)),
    ('issue857-pair-list-literal', _flow(
        _RELAY, 'e = {}; e.update([("k", ordinary), (None, relay())])',
        'x = e.setdefault("k", relay())', invoke='x()'), (0, 0)),
    ('issue857-update-pairs-defect', _ext_flow(
        'e = {}; e.update([("k", relay())])',
        'x = e.setdefault("k", relay())', 'x()'), (1, 1)),
    ('issue857-setdefault-defect', _ext_flow(
        'd = {}', 'x = d.setdefault("k", relay())', 'x()'), (1, 1)),
    ('issue857-pop-direct-defect', _ext_flow(
        'd = {"k": relay()}', 'd.pop("k")', 'd.get("k", relay())()'),
     (1, 1)),
    ('issue857-pop-name-defect', _ext_flow(
        'd = {"k": relay()}; k = "k"', 'd.pop(k)',
        'd.get("k", relay())()'), (1, 1)),

    # A pop that names an ABSENT key is the discriminator between resolving
    # the key and erasing or marking the container: `j` still holds a relay.
    ('issue857-pop-absent-key', _flow(
        _RELAY, 'd = {"k": relay(), "j": relay()}; k = "zz"',
        'd.pop(k, None)', invoke='d.get("j", ordinary)()'), (1, 1)),
]

# The shapes issue 857 over-reported, each with the real defect of the same
# shape: same operator, key kind and receiver kind, the key occupied.
_ISSUE857 = [
    ('update-pairs',
     _flow(_RELAY, 'e = {}; e.update([("k", ordinary)])',
           'x = e.setdefault("k", relay())', invoke='x()'),
     _flow(_RELAY, 'e = {}; e.update([("k", relay())])',
           'x = e.setdefault("k", relay())', invoke='x()')),
    ('setdefault-int-key',
     _flow(_RELAY, 'd = {}; d[1] = ordinary',
           'x = d.setdefault(1, relay())', invoke='x()'),
     _flow(_RELAY, 'd = {}; d[1] = ordinary',
           'x = d.setdefault(2, relay())', invoke='x()')),
    ('pop-via-getd',
     _flow(_RELAY, 'd = {"k": relay()}', 'def getd(): return d',
           'getd().pop("k")', invoke='d.get("k", ordinary)()'),
     _flow(_RELAY, 'd = {"k": relay(), "j": ordinary}',
           'def getd(): return d', 'getd().pop("j")',
           invoke='d.get("k", ordinary)()')),
    ('pop-variable-key',
     _flow(_RELAY, 'd = {"k": relay()}; k = "k"', 'd.pop(k)',
           invoke='d.get("k", ordinary)()'),
     _flow(_RELAY, 'd = {"k": relay(), "j": ordinary}; k = "j"', 'd.pop(k)',
           invoke='d.get("k", ordinary)()')),
]


def test_issue857_shapes_read_clean_and_their_defects_are_caught(tmp):
    for label, over_reported, defect in _ISSUE857:
        actual = _tracked_focus_verdict(tmp, over_reported, counts=True)
        assert actual == (0, 0), f'{label}: expected (0, 0), got {actual}'
        actual = _tracked_focus_verdict(tmp, defect, counts=True)
        assert actual == (1, 1), f'{label}: expected (1, 1), got {actual}'


# The setdefault key forms around the occupancy lookup: a name bound to a
# literal is a known key, a name bound to a non-literal is not.
_ISSUE962 = [
    ('name-key-occupied', _flow(
        _RELAY, 'd = {"k": relay()}; key = "k"',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    ('literal-key-occupied', _flow(
        _RELAY, 'd = {"k": relay()}', 'x = d.setdefault("k", ordinary)',
        invoke='x()'), (1, 1)),
    ('name-key-vacant', _flow(
        _RELAY, 'd = {}; key = "k"', 'x = d.setdefault(key, relay())',
        invoke='x()'), (1, 1)),
    ('name-key-non-string', _flow(
        _RELAY, 'd = {1: relay()}; key = 1',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    # Unresolvable key, deferred default: reported. Unresolvable key,
    # ordinary default: the stored callable is lost and the call reads
    # clean — issue 963, pinned at its current verdict.
    ('name-key-unresolved-relay', _flow(
        _RELAY, 'd = {"k": relay()}; key = "k" + ""',
        'x = d.setdefault(key, relay())', invoke='x()'), (1, 1)),
    ('known-defect-963-name-key-unresolved-ordinary', _flow(
        _RELAY, 'd = {"k": relay()}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 0)),
]


def test_issue962_name_bound_setdefault_key(tmp):
    bad = []
    for label, body, expected in _ISSUE962:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


# The literal key forms around that lookup. A key the shared evaluator folds
# reaches the model the same way on the three operations that resolve one: a
# dict literal's key, the key a `pop` removes and the key a `get`/`pop`
# reads, which is the property `test_pop_then_read_resolves_one_key` pins and
# which the read side satisfied only from the third wave on. A subscript
# store is not one of the three: it parks a non-constant key in the dynamic
# slot, as it always has. An f-string is not folded by the evaluator at all,
# so both f-string rows stay unresolved and read clean, as issue 967 records;
# the label carries that, so the row cannot be read as a claim that a clean
# verdict is correct.
_ISSUE967 = [
    ('tuple-key-by-name', _flow(
        _RELAY, 'd = {(1, 2): relay()}; key = (1, 2)',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    ('tuple-key-literal', _flow(
        _RELAY, 'd = {(1, 2): relay()}',
        'x = d.setdefault((1, 2), ordinary)', invoke='x()'), (1, 1)),
    ('unaryminus-key-by-name', _flow(
        _RELAY, 'd = {-1: relay()}; key = -1',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    ('known-defect-967-fstring-key-by-name', _flow(
        _RELAY, 'd = {"k": relay()}; key = f"k"',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 0)),
    ('known-defect-967-fstring-key-interpolated', _flow(
        _RELAY, 'd = {"k": relay()}; key = f"{\'k\'}"',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 0)),
]


def test_issue967_literal_key_forms(tmp):
    bad = []
    for label, body, expected in _ISSUE967:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


# A rebound name must not resolve to its first binding, and the rebind has
# to be to something the table cannot hold: rebinding to a second literal
# overwrites the entry and proves nothing. Both rows rebind to a call, so a
# guard that keeps the first binding resolves the key it must not know. The
# pop row is the false-green discriminator and the setdefault row the
# over-report one: a guard keeping the first binding turns the pair into
# `(1, 0)` and `(1, 1)`.
_REBINDING = [
    ('pop-rebound-to-non-literal', _flow(
        _RELAY, 'd = {"k": relay(), "j": relay()}; k = "k"; k = relay()',
        'd.pop(k, None)', invoke='d.get("k", ordinary)()'), (1, 1)),
    ('setdefault-rebound-to-non-literal', _flow(
        _RELAY, 'd = {"k": relay()}; k = "k"; k = relay()',
        'x = d.setdefault(k, ordinary)', invoke='x()'), (0, 0)),
]

# A key the runtime rejects is a key the guard must read unprovable rather
# than crash on: `dict.setdefault` raises `TypeError: unhashable type` for
# every spelling below before it routes anything, so the guard's verdict is
# about the call, and a guard that raises must fail a row instead of a suite.
_UNUSABLE_KEYS = [
    ('unusable-key-list', 'key = [1]'),
    ('unusable-key-tuple-of-list', 'key = ([1],)'),
    ('unusable-key-dict', 'key = {"a": 1}'),
    ('unusable-key-set', 'key = {1, 2}'),
]

# One key, three operations. Every row pops a key and reads that same key
# back, so a guard that removes the key and then cannot resolve the read
# reports a value the program never reads. The string-keyed twins are the
# negative space: the point is that the evaluable form behaves like the
# constant form, not that every pop reads clean. The last row is the read
# whose key the guard cannot evaluate at all, which stays #963's class.
_TUPLE_D = 'd = {(1, 2): relay(), (3, 4): relay()}'
_STRING_D = 'd = {"k": relay(), "j": relay()}'
_POP_THEN_READ = [
    ('pop-tuple-lit', _flow(
        _RELAY, _TUPLE_D, 'd.pop((1, 2), None)',
        invoke='d.get((1, 2), ordinary)()'), (0, 0)),
    ('pop-tuple-name', _flow(
        _RELAY, f'{_TUPLE_D}; key = (1, 2)', 'd.pop(key, None)',
        invoke='d.get((1, 2), ordinary)()'), (0, 0)),
    ('pop-tuple-read-by-name', _flow(
        _RELAY, f'{_TUPLE_D}; r = (1, 2)', 'd.pop((1, 2), None)',
        invoke='d.get(r, ordinary)()'), (0, 0)),
    ('rebind-to-tuple-then-pop', _flow(
        _RELAY, 'key = (1, 2)', _TUPLE_D, 'd.pop(key, None)',
        invoke='d.get((1, 2), ordinary)()'), (0, 0)),
    ('pop-str-lit', _flow(
        _RELAY, _STRING_D, 'd.pop("k", None)',
        invoke='d.get("k", ordinary)()'), (0, 0)),
    ('pop-str-name', _flow(
        _RELAY, f'{_STRING_D}; key = "k"', 'd.pop(key, None)',
        invoke='d.get("k", ordinary)()'), (0, 0)),
    ('pop-str-read-by-name', _flow(
        _RELAY, f'{_STRING_D}; r = "k"', 'd.pop("k", None)',
        invoke='d.get(r, ordinary)()'), (0, 0)),
    ('rebind-to-str-then-pop', _flow(
        _RELAY, 'key = "k"', _STRING_D, 'd.pop(key, None)',
        invoke='d.get("k", ordinary)()'), (0, 0)),
    ('read-unevaluable-key', _flow(
        _RELAY, 'd = {"k": relay()}', 'x = d.get("k" + "", ordinary)',
        invoke='x()'), (1, 1)),
]

def test_pop_then_read_resolves_one_key(tmp):
    bad = []
    for label, body, expected in _POP_THEN_READ:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


def test_store_form_verdicts(tmp):
    bad = []
    for label, body, expected in CASES:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='storesweep_')


if __name__ == '__main__':
    raise SystemExit(main())
