#!/usr/bin/env python3
"""Deferred callable bodies remain reachable through binders and joins."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402


PREFIX = (
    'send = ordinary\n'
    'def maker():\n'
    '    return lambda: send("_focus", "focus-tab", tab=args.chrome_tab)\n'
    'def relay(): return maker()\n'
    'def pair(): return relay(), ordinary\n')


def body(store, invoke, prefix=PREFIX):
    return prefix + store + '\nsend = ext_cmd\nreturn ' + invoke


def verdicts(tmp, cases):
    observed = [(label, *_tracked_focus_verdict(tmp, source, counts=True))
                for label, source, _ in cases]
    expected = [(label, *value) for label, _, value in cases]
    assert observed == expected, observed


def test_returned_container_destructuring(tmp):
    shapes = [
        ('tuple', 'x, y = pair()', 'x()', 'y()'),
        ('list-target', '[x, y] = pair()', 'x()', 'y()'),
        ('star-prefix', 'x, *rest = pair()', 'x()', 'rest[0]()'),
        ('star-suffix', '*rest, y = pair()', 'rest[0]()', 'y()'),
        ('nested', 'def nested(): return pair(), ordinary\n'
         '(x, y), z = nested()', 'x()', 'z()'),
        ('literal', 'x, y = relay(), ordinary', 'x()', 'y()'),
        ('literal-star', '*rest, y = relay(), ordinary',
         'rest[0]()', 'y()'),
        ('attribute', 'class C: pass\nc = C()\nc.fn, y = pair()',
         'c.fn()', 'y()'),
        ('literal-attribute', 'class C: pass\nc = C()\n'
         'c.fn, y = relay(), ordinary', 'c.fn()', 'y()'),
        ('nested-attribute', 'class C: pass\nc = C()\n'
         'def nested(): return pair(), ordinary\n'
         '(c.fn, y), z = nested()', 'c.fn()', 'y()'),
        ('subscript', 'd = {}\nd["k"], y = pair()',
         'd["k"]()', 'y()'),
        ('alternatives', 'def choose():\n'
         '    if args.flag: return pair()\n'
         '    return (lambda: ordinary()), ordinary\n'
         'x, y = choose()', 'x()', 'y()'),
        ('partial-alternatives', 'def choose():\n'
         '    if args.flag: return pair()\n'
         '    return (lambda: ordinary(),)\n'
         'x, y = choose()', 'x()', 'y()'),
    ]
    cases = [(label + direction, body(store, invoke), expected)
             for label, store, bad, good in shapes
             for direction, invoke, expected in (
                 ('-called', bad, (1, 1)), ('-other', good, (0, 0)),
                 ('-discarded', '0', (0, 0)))]
    verdicts(tmp, cases)


def test_unknown_setdefault_keeps_default_body(tmp):
    shapes = [
        ('unknown-owner', 'd = args.__dict__\n'
         'x = d.setdefault("k", relay())', 'x()'),
        ('unknown-key', 'd = {}\n'
         'x = d.setdefault(str(args.chrome_tab), relay())', 'x()'),
        ('nested-default', 'd = args.__dict__\n'
         'x = d.setdefault("k", [relay()])', 'x[0]()'),
    ]
    cases = [(label + direction, body(store, invoke), expected)
             for label, store, call in shapes
             for direction, invoke, expected in (
                 ('-called', call, (1, 1)), ('-discarded', '0', (0, 0)))]
    cases.extend((label + '-ordinary',
                  body(store.replace('relay()', 'ordinary'), call), (0, 0))
                 for label, store, call in shapes)
    verdicts(tmp, cases)


def test_callable_join_defaults(tmp):
    choices = [
        ('bad-first', 'relay()', 'lambda: ordinary()'),
        ('bad-second', 'lambda: ordinary()', 'relay()'),
        ('both-ordinary', 'lambda: ordinary()', 'lambda: ordinary()'),
    ]
    for label, first, second in choices:
        # Both runtime branches are exercised; static analysis sees both.
        for flag in (True, False):
            store = (f'if args.flag:\n    x = {first}\n'
                     f'else:\n    x = {second}\n'
                     'def invoke(value=x): return value()')
            expected = (int((label == 'bad-first' and flag)
                            or (label == 'bad-second' and not flag)),
                        int(label != 'both-ordinary'))
            source = body(store, 'invoke()')
            before = f'_args.flag = {flag}'
            actual = _tracked_focus_verdict(
                tmp, source, before=before, counts=True)
            assert actual == expected, (label, flag, actual)


def test_existing_collapse_controls(tmp):
    cases = [
        ('direct', body('x = relay()', 'x()'), (1, 1)),
        ('unused', body('x = relay()', '0'), (0, 0)),
        ('attribute', body('class C: pass\nc = C()\nc.fn = relay()',
                           'c.fn()'), (1, 1)),
        ('conditional', body('x = relay() if args.flag else ordinary',
                             'x()'), (1, 1)),
        ('known-tabless', body('', 'send("_focus", "focus-tab")'), (1, 0)),
    ]
    verdicts(tmp, cases)


def test_assignment_targets_use_updated_owners(tmp):
    setup = 'class C: pass\nc = C()\nold = c\nc.fn = ordinary\n'
    shapes = [
        ('new-alias', 'alias, c.fn = c, relay()', 'alias.fn()', (1, 1)),
        ('new-owner', 'other = C()\nc, c.fn = other, relay()',
         'c.fn()', (1, 1)),
        ('old-owner', 'other = C()\nc, c.fn = other, relay()',
         'old.fn()', (0, 0)),
        ('later-alias', 'c.fn, alias = relay(), c', 'alias.fn()', (1, 1)),
        ('closure-alias', 'alias, c.fn = c, relay()\n'
         'def invoke(): return alias.fn()', 'invoke()', (1, 1)),
        ('frozen-rhs', 'other = C()\nother.fn = relay()\n'
         'c, alias = other, c', 'alias.fn()', (0, 0)),
        ('repeated-target', 'c.fn, c.fn = relay(), ordinary',
         'c.fn()', (0, 0)),
    ]
    cases = []
    for label, store, invoke, expected in shapes:
        source = body(setup + store, invoke)
        cases.append((label, source, expected))
        clean = source.replace('tab=args.chrome_tab', 'tab="extension"')
        clean = clean.replace('lambda: send(', 'lambda: ordinary(')
        cases.append((label + '-clean', clean, (0, 0)))
    verdicts(tmp, cases)


def test_setdefault_respects_occupied_clean_keys(tmp):
    stores = [
        ('ordinary', 'd = {"k": ordinary}', (0, 0)),
        ('deferred-clean', 'd = {"k": lambda: ordinary()}', (0, 0)),
        ('other-deferred-key', 'd = {"k": ordinary, "j": relay()}', (0, 0)),
        ('constructor', 'd = dict(k=ordinary)', (0, 0)),
        ('subscript', 'd = {}\nd["k"] = ordinary', (0, 0)),
        ('updated', 'd = {}\nd.update(k=ordinary)', (0, 0)),
        ('prior-default', 'd = {}\nd.setdefault("k", ordinary)', (0, 0)),
        ('missing', 'd = {}', (1, 1)),
        ('unknown', 'd = args.__dict__', (1, 1)),
    ]
    cases = []
    for label, store, expected in stores:
        source = body(store + '\nx = d.setdefault("k", relay())', 'x()')
        cases.append((label, source, expected))
        clean = source.replace('lambda: send(', 'lambda: ordinary(')
        cases.append((label + '-clean', clean, (0, 0)))
    verdicts(tmp, cases)


def test_starred_sender_suffix_alignment(tmp):
    shapes = [
        ('prefix-star', 'def pair2(): return ordinary, relay()\n'
         '*rest, y = pair2()', 'rest[0]()'),
        ('middle-star', 'def pair2():\n'
         '    return ordinary, ordinary, ordinary, relay()\n'
         'first, *rest, y = pair2()', 'first()'),
    ]
    cases = [(label + direction, body(store, invoke), expected)
             for label, store, clean in shapes
             for direction, invoke, expected in (
                 ('-suffix', 'y()', (1, 1)), ('-prefix', clean, (0, 0)),
                 ('-discarded', '0', (0, 0)))]
    verdicts(tmp, cases)


def test_setdefault_unknown_existing_sender(tmp):
    prefix = ('d = args.__dict__\n'
              'x = d.setdefault("k", lambda *a, **kw: ordinary())\n')
    observed = [_tracked_focus_verdict(
        tmp, prefix + invoke, before='_args.k = ext_cmd', counts=True)
        for invoke in (
            'return x("_focus", "focus-tab", tab=args.chrome_tab)',
            'return x("_focus", "focus-tab")')]
    assert observed == [(1, 1), (1, 0)], observed


def test_dynamic_setdefault_stores_default(tmp):
    shapes = [
        ('dynamic', 'x = {}\n'
         'x.setdefault(str(args.chrome_tab), relay())',
         'x[str(args.chrome_tab)]()'),
        ('constant', 'x = {}\nx.setdefault("k", relay())', 'x["k"]()'),
        ('alias', 'x = {}\nalias = x\n'
         'alias.setdefault(str(args.chrome_tab), relay())',
         'x[str(args.chrome_tab)]()'),
    ]
    cases = []
    for label, store, invoke in shapes:
        cases.append((label, body(store, invoke), (1, 1)))
        cases.append((label + '-ordinary',
                      body(store.replace('relay()', 'ordinary'), invoke),
                      (0, 0)))
        cases.append((label + '-discarded', body(store, '0'), (0, 0)))
    verdicts(tmp, cases)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='collapse_')


if __name__ == '__main__':
    raise SystemExit(main())
