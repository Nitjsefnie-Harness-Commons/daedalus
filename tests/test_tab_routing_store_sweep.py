#!/usr/bin/env python3
"""Runtime-and-guard verdict table for every mapping-store form."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
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
    ('name-key-unresolved-ordinary', _flow(
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
