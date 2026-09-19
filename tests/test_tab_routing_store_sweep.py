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


def _flow(*lines, invoke=None):
    body = ['send = ordinary', *lines]
    if invoke is not None:
        body.extend(('send = ext_cmd', f'return {invoke}'))
    return '\n'.join(body).replace('_CALL', _CALL)


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
    ('iteration-of-copy', _flow(
        'box = {}', f'box["k"] = {_LAMBDA}', 'copy = dict(box)',
        'list(copy)', 'list(box)'), (0, 0)),
    ('clean-store-invocation', _flow(
        'box = {}', 'box["k"] = lambda: ordinary()',
        invoke='box["k"]()'), (0, 0)),
    ('delete-keeps-pop', _flow(
        'box = {"k": ' + _LAMBDA + '}', 'del box["k"]',
        invoke='(box["k"]() if "k" in box else None)'), (0, 0)),
]


def test_store_form_verdicts(tmp):
    observed = []
    for label, body, expected in CASES:
        actual = _tracked_focus_verdict(tmp, body, counts=True)
        assert actual == expected, (label, actual, expected)
        observed.append((label, actual))


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='storesweep_')


if __name__ == '__main__':
    raise SystemExit(main())
