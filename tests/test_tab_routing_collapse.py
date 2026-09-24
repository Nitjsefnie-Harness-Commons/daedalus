#!/usr/bin/env python3
"""Deferred callable bodies remain reachable through binders and joins."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_state import FlowState  # noqa: E402
from _pyroute_targets import materialized_order  # noqa: E402
from _pyroute_values import DeferredContainer  # noqa: E402
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


def test_unknown_lookup_keeps_default_body(tmp):
    """get and pop on an owner the model cannot read keep a known default
    beside the unprovable sender, as setdefault does."""
    shapes = [
        ('bound-get', 'd = args.__dict__\nx = d.get("k", relay())', 'x()',
         (1, 1), (0, 0)),
        ('inline-get', 'x = args.__dict__.get("k", relay())', 'x()',
         (1, 1), (0, 0)),
        ('inline-pop', 'x = args.__dict__.pop("k", relay())', 'x()',
         (1, 1), (0, 0)),
        ('unbound-call', '', 'args.__dict__.get("k", relay())()',
         (1, 1), (0, 0)),
        # The call carrying tab is reported as well: x may be the sender.
        ('keyword-call', 'x = args.__dict__.get("k", lambda tab: relay()())',
         'x(tab=args.chrome_tab)', (1, 2), (0, 1)),
    ]
    cases = [(label, body(store, invoke), expected)
             for label, store, invoke, expected, _ in shapes]
    for label, store, invoke, _, clean in shapes:
        cases.append((label + '-clean', body(store, invoke).replace(
            'lambda: send(', 'lambda: ordinary('), clean))
    cases.append(('no-default', body(
        'd = args.__dict__\nx = d.get("k")', 'x'), (0, 0)))
    verdicts(tmp, cases)


def test_callable_join_defaults(tmp):
    choices = [
        ('bad-first', 'relay()', 'lambda: ordinary()'),
        ('bad-second', 'lambda: ordinary()', 'relay()'),
        ('both-ordinary', 'lambda: ordinary()', 'lambda: ordinary()'),
    ]
    for label, first, second in choices:
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


def test_clean_occupancy_joins_keep_only_shared_keys(tmp):
    """A key occupied on only some paths is missing at the join: setdefault
    binds the default there, as the runtime does on the path lacking it.
    The bodies define no nested scope, so the join is decided by the
    deferred state alone."""
    stores = [
        ('conditional', 'd = {}\nif args.flag: d["k"] = ordinary', (0, 1)),
        ('conditional-removal',
         'd = {"k": ordinary}\nif args.flag: del d["k"]', (1, 1)),
        ('literal-condition', 'd = {}\nif True: d["k"] = ordinary', (0, 0)),
        ('both-arms', 'd = {}\nif args.flag: d["k"] = ordinary\n'
         'else: d["k"] = ordinary', (0, 0)),
        ('other-key', 'd = {"k": ordinary}\n'
         'if args.flag: d["j"] = ordinary', (0, 0)),
        ('loop', 'd = {}\nfor _ in args.values: d["k"] = ordinary', (0, 1)),
        ('loop-shared', 'd = {"k": ordinary}\n'
         'for _ in args.values: d["j"] = ordinary', (0, 0)),
    ]
    cases = [(label, store + '\nx = d.setdefault("k", ext_cmd)\n'
              'x("_focus", "focus-tab", tab=int(args.chrome_tab))', expected)
             for label, store, expected in stores]
    verdicts(tmp, cases)


def test_lambda_body_value_is_its_return(tmp):
    """A lambda returns what its body evaluates to, as a def's return does,
    so a deferred default handed back by a mapping lookup is followed."""
    lookup = 'read = lambda: d.get("k", relay())'
    rows = [
        ('lambda-get', 'd = {}\n' + lookup, (1, 1)),
        ('lambda-get-occupied', 'd = {"k": ordinary}\n' + lookup, (0, 0)),
        ('lambda-get-then-store',
         'd = {}\n' + lookup + '\nif args.flag: d["k"] = ordinary', (0, 1)),
        ('lambda-get-then-store-other',
         'd = {}\n' + lookup + '\nif not args.flag: d["k"] = ordinary',
         (1, 1)),
        ('def-get', 'd = {}\ndef read(): return d.get("k", relay())',
         (1, 1)),
        ('lambda-setdefault',
         'd = {}\nread = lambda: d.setdefault("k", relay())', (1, 1)),
        ('lambda-pop', 'd = {}\nread = lambda: d.pop("k", relay())', (1, 1)),
        ('lambda-call', 'read = lambda: relay()', (1, 1)),
    ]
    verdicts(tmp, [(label, body(store, 'read()()'), expected)
                   for label, store, expected in rows])


def test_callee_replay_keeps_occupancy_apart(tmp):
    """A callee analysed from a state where a clean key is occupied and
    again from one where it is missing must be walked twice: the joined
    signature would merge the two entries, and a replay applies no join."""
    use = 'd = {"k": ordinary}\ndef use(): return d.get("k", relay())\n'
    rows = []
    for arm, removal in (('pop', 'd.pop("k")'), ('del', 'del d["k"]')):
        for flag, taken in (('args.flag', True), ('not args.flag', False)):
            rows.append((f'occupied-first-{arm}-{flag}',
                         f'{use}if {flag}: x = use()\nelse: {removal}; '
                         'x = use()', (int(not taken), 1)))
            rows.append((f'missing-first-{arm}-{flag}',
                         f'{use}if {flag}: {removal}; x = use()\n'
                         'else: x = use()', (int(taken), 1)))
    rows.append(('occupied-alone', use + 'x = use()', (0, 0)))
    verdicts(tmp, [(label, body(store, 'x()'), expected)
                   for label, store, expected in rows])


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


def test_mixed_key_containers_join(tmp):
    """A container holding string and dynamic keys still has a signature
    order at a join and on callee replay; keys are ordered by type name."""
    stores = [
        ('setdefault', 'd = {"k": relay()}\n'
         'd.setdefault(str(args.chrome_tab), relay())\nif args.flag: pass'),
        ('update', 'd = {"k": relay()}\n'
         'd.update({str(n): relay() for n in args.values})\n'
         'if args.flag: pass'),
    ]
    cases = [(label, body(store, 'd["k"]()'), (1, 1))
             for label, store in stores]
    for label, store in stores:
        cases.append((label + '-clean', body(store, 'd["k"]()').replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)))
    verdicts(tmp, cases)


def _opaque_mapping_verdicts(tmp, store):
    source = body(store, 'x["k"]()')
    clean = source.replace('lambda: send(', 'lambda: ordinary(')
    # Issue 815 owns the remaining miss; issue 816 must return a verdict.
    verdicts(tmp, [('sender', source, (1, 0)), ('clean', clean, (0, 0))])


def test_opaque_dict_payload_signature_answers(tmp):
    _opaque_mapping_verdicts(tmp, 'x = {"k": relay(), **args.__dict__}')


def test_opaque_constructor_payload_signature_answers(tmp):
    _opaque_mapping_verdicts(tmp, 'x = dict(k=relay(), **args.__dict__)')


def test_mapping_get_and_pop_follow_deferred_items(tmp):
    prefix = ('send = ordinary\n'
              'def forward(**kw):\n'
              '    return send("_focus", "focus-tab", **kw)\n')
    cases = []
    for method in ('get', 'pop'):
        shapes = [
            ('assigned', 'd = {"k": forward}\nx = d.' + method + '("k")',
             'x(tab=args.chrome_tab)', (1, 1)),
            ('inline', 'd = {"k": forward}',
             'd.' + method + '("k")(tab=args.chrome_tab)', (1, 1)),
            ('missing', 'd = {"k": forward}',
             'd.' + method + '("missing", ordinary)(tab=args.chrome_tab)',
             (0, 0)),
            ('default', 'd = {}',
             'd.' + method + '("missing", forward)(tab=args.chrome_tab)',
             (1, 1)),
            ('occupied', 'd = {"k": ordinary}',
             'd.' + method + '("k", forward)(tab=args.chrome_tab)', (0, 0)),
            ('dynamic', 'd = {key: forward for key in ["k"]}',
             'd.' + method + '("k", ordinary)(tab=args.chrome_tab)',
             (1, 1)),
            ('dynamic-default', 'd = {key: (lambda **kw: ordinary())\n'
             '     for key in ["k"]}',
             'd.' + method + '("missing", forward)(tab=args.chrome_tab)',
             (1, 1)),
        ]
        for label, store, invoke, expected in shapes:
            source = body(store, invoke, prefix)
            cases.append((method + '-' + label, source, expected))
            clean = source.replace('return send(', 'return ordinary(')
            cases.append((method + '-' + label + '-clean', clean, (0, 0)))
    cases.append(('subscript', body('d = {"k": forward}\nx = d["k"]',
                                    'x(tab=args.chrome_tab)', prefix), (1, 1)))
    verdicts(tmp, cases)


def test_standalone_pop_removes_deferred_item(tmp):
    cases = []
    for alias in ('d', 'alias'):
        store = ('d = {"k": relay()}\nalias = d\n'
                 + alias + '.pop("k")')
        cases.append((alias, body(store, 'd.get("k", ordinary)()'), (0, 0)))
        cases.append((alias + '-default', body(
            store, 'd.get("k", relay())()'), (1, 1)))
    verdicts(tmp, cases)


def test_assigned_pop_updates_aliases_and_keeps_return(tmp):
    cases = []
    for target in ('x', 'd', 'x: object'):
        store = ('d = {"k": relay()}\nalias = d\n'
                 + target + ' = d.pop("k")')
        cases.append((target + '-removed', body(
            store, 'alias.get("k", ordinary)()'), (0, 0)))
        invoke = target.split(':', maxsplit=1)[0] + '()'
        source = body(store, invoke)
        cases.append((target + '-returned', source, (1, 1)))
        cases.append((target + '-clean', source.replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)))
    verdicts(tmp, cases)


def test_pop_effect_follows_expression_evaluation(tmp):
    shapes = [
        ('tuple', 'x = (d.pop("k"), ordinary)', 'x[0]()'),
        ('list', 'x = [d.pop("k")]', 'x[0]()'),
        ('argument', 'ordinary(d.pop("k"))', None),
        ('returned-argument', 'def keep(value): return value\n'
         'x = keep(d.pop("k"))', 'x()'),
        ('same-expression', 'x = (d.pop("k"), d.get("k", ordinary))',
         'x[0]()'),
    ]
    cases = []
    for label, expression, invoke in shapes:
        store = 'd = {"k": relay()}\n' + expression
        calls = [('removed', 'd.get("k", ordinary)()', (0, 0))]
        if invoke is not None:
            calls.append(('returned', invoke, (1, 1)))
        if label == 'same-expression':
            calls.append(('later-read', 'x[1]()', (0, 0)))
        for direction, call, expected in calls:
            source = body(store, call)
            cases.append((label + '-' + direction, source, expected))
            cases.append((label + '-' + direction + '-clean', source.replace(
                'lambda: send(', 'lambda: ordinary('), (0, 0)))
    verdicts(tmp, cases)


def test_pop_uses_resolved_receiver_identity(tmp):
    receivers = [
        ('name', 'd = {"k": relay()}', 'd'),
        ('attribute', 'class C: pass\nc = C()\n'
         'c.d = {"k": relay()}', 'c.d'),
        ('subscript', 'box = {"d": {"k": relay()}}', 'box["d"]'),
    ]
    cases = []
    for label, setup, owner in receivers:
        for form in ('standalone', 'assigned', 'inline'):
            store = setup + '\nalias = ' + owner
            if form != 'inline':
                store += '\n' + ('x = ' if form == 'assigned' else '')
                store += owner + '.pop("k")'
            calls = [('returned', owner + '.pop("k")()', (1, 1))] \
                if form == 'inline' else [
                    ('removed', owner + '.get("k", ordinary)()', (0, 0)),
                    ('alias', 'alias.get("k", ordinary)()', (0, 0)),
                    ('second-pop', owner + '.pop("k", relay())()', (1, 1))]
            if form == 'assigned':
                calls.append(('returned', 'x()', (1, 1)))
            for direction, invoke, expected in calls:
                initial = store.replace('{"k": relay()}', '{"k": ordinary}') \
                    if direction == 'second-pop' else store
                source = body(initial, invoke)
                name = label + '-' + form + '-' + direction
                cases.append((name, source, expected))
                cases.append((name + '-clean', source.replace(
                    'lambda: send(', 'lambda: ordinary('), (0, 0)))
    verdicts(tmp, cases)


def test_materialized_container_destructuring(tmp):
    """A materializer that yields a deferred container pairs with a tuple or
    list target the way that container pairs when it is the right-hand side
    itself, whatever expression produced it."""
    shapes = [
        ('list', 'x, y = list(pair())', 'x()', 'y()'),
        ('tuple', 'x, y = tuple(pair())', 'x()', 'y()'),
        ('list-target', '[x, y] = list(pair())', 'x()', 'y()'),
        ('nested-list', 'x, y = list(list(pair()))', 'x()', 'y()'),
        ('reversed', 'x, y = reversed(pair())', 'y()', 'x()'),
        ('slice-reversed', 'x, y = pair()[::-1]', 'y()', 'x()'),
        ('slice-open', 'x, y = pair()[:]', 'x()', 'y()'),
        ('star', 'x, *rest = list(pair())', 'x()', 'rest[0]()'),
        ('alternatives', 'def choose():\n'
         '    if args.flag: return list(pair())\n'
         '    return tuple(pair())\n'
         'x, y = list(choose())', 'x()', 'y()'),
    ]
    cases = []
    for label, store, bad, good in shapes:
        source = body(store, bad)
        cases.append((label + '-called', source, (1, 1)))
        cases.append((label + '-other', body(store, good), (0, 0)))
        cases.append((label + '-discarded', body(store, '0'), (0, 0)))
        cases.append((label + '-clean', source.replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)))
    verdicts(tmp, cases)


def test_materialized_container_controls(tmp):
    """The materializer pairing must not move the controls that already
    answered: the eager consumer's own index, a plain container, iter, and a
    single-name target that subscripts the materialized container."""
    cases = [
        ('list-index', body('x = list(pair())', 'x[0]()'), (1, 1)),
        ('plain-pair', body('x, y = pair()', 'x()'), (1, 1)),
        ('iter', body('x, y = iter(pair())', 'x()'), (1, 1)),
        ('list-index-clean', body('x = list(pair())', 'x[0]()').replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)),
    ]
    verdicts(tmp, cases)


def test_with_tuple_target_pairs_enter_result(tmp):
    """`with ... as (x, y)` pairs the __enter__ result the way the
    assignment binder pairs its right-hand side."""
    store = ('class C:\n'
             '    def __enter__(self): return pair()\n'
             '    def __exit__(self, *a): pass\n'
             'def ctx(): return C()\n'
             'with ctx() as (x, y):\n'
             '    pass\n')
    called = body(store, 'x()')
    cases = [
        ('with-tuple-called', called, (1, 1)),
        ('with-tuple-other', body(store, 'y()'), (0, 0)),
        ('with-tuple-clean', called.replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)),
    ]
    verdicts(tmp, cases)


def test_with_name_target_pairs_enter_result(tmp):
    """`with ... as x` pairs the __enter__ result to the name whole, the way
    the assignment binder pairs a single-name target."""
    store = ('class C:\n'
             '    def __enter__(self): return relay()\n'
             '    def __exit__(self, *a): pass\n'
             'def ctx(): return C()\n'
             'with ctx() as x:\n'
             '    pass\n')
    called = body(store, 'x()')
    cases = [
        ('with-name-called', called, (1, 1)),
        ('with-name-discarded', body(store, '0'), (0, 0)),
        ('with-name-clean', called.replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)),
    ]
    verdicts(tmp, cases)


def test_with_enter_send_is_reported(tmp):
    """A send inside __enter__ reaches ext_cmd when the with statement runs
    with send already rebound; the walk that reads __enter__'s return value
    is what reports it, and its clean counterpart stays clean."""
    enter = ('send = ext_cmd\n'
             'class C:\n'
             '    def __enter__(self):\n'
             '        send("_focus", "focus-tab", tab=args.chrome_tab)\n'
             '        return 1\n'
             '    def __exit__(self, *a): pass\n'
             'def ctx(): return C()\n'
             'with ctx() as x:\n    pass\n')
    plain = enter.replace('send("_focus"', 'ordinary("_focus"')
    verdicts(tmp, [
        ('enter-send', body(enter, '0'), (1, 1)),
        ('enter-plain', body(plain, '0'), (0, 0)),
    ])


def test_reversed_producer_comprehension(tmp):
    """A comprehension does not reorder: its element at output index i is the
    producer's element at index i, so an order-reversing producer's routed
    element lands at index 1 and index 0 reads clean. x[1] stays the tracked
    false green of #948; the order-preserving rows are the regression set."""
    reversing = ['reversed(pair())', 'list(reversed(pair()))',
                 'pair()[::-1]']
    preserving = ['pair()', 'list(pair())', 'tuple(pair())', 'pair()[:]',
                  'list(list(pair()))']
    cases = []
    for index, producer in enumerate(reversing):
        store = f'x = [c for c in {producer}]'
        first = body(store, 'x[0]()')
        cases.append((f'reversing-{index}-first', first, (0, 0)))
        cases.append((f'reversing-{index}-routed', body(store, 'x[1]()'),
                      (1, 0)))
        cases.append((f'reversing-{index}-clean', first.replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)))
    for index, producer in enumerate(preserving):
        store = f'x = [c for c in {producer}]'
        routed = body(store, 'x[0]()')
        cases.append((f'preserving-{index}-routed', routed, (1, 1)))
        cases.append((f'preserving-{index}-other', body(store, 'x[1]()'),
                      (0, 0)))
        cases.append((f'preserving-{index}-clean', routed.replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)))
    verdicts(tmp, cases)


def test_reversed_producer_comprehension_consumers(tmp):
    """A consumer that walks a comprehension's result visits every element,
    so the routed element must still be reported there; the result's own
    positional reads stay exact."""
    loop = ('send = ext_cmd\n'
            'for v in [c for c in reversed(pair())]:\n    v()')
    loop_pair = ('send = ext_cmd\n'
                 'for v in [c for c in pair()]:\n    v()')
    nested = 'x = [d for c in reversed(pair()) for d in [c]]'
    nested_pair = 'x = [d for c in pair() for d in [c]]'
    cases = [
        ('loop-reversed', body(loop, '0'), (1, 1)),
        ('loop-preserving', body(loop_pair, '0'), (1, 1)),
        ('loop-reversed-clean', body(loop, '0').replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)),
        ('nested-first', body(nested, 'x[0]()'), (0, 0)),
        ('nested-routed', body(nested, 'x[1]()'), (1, 0)),
        ('nested-preserving', body(nested_pair, 'x[0]()'), (1, 1)),
        ('nested-clean', body(nested, 'x[0]()').replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)),
    ]
    verdicts(tmp, cases)


def test_shadowed_materializer_names(tmp):
    """A name that shadows list or reversed is not the builtin, so its call
    gets no ordered pairing: the shadow decides the element order at runtime,
    not the builtin the spelling matches."""
    cases = [
        ('shadowed-list', body(
            'list = lambda seq: seq[::-1]\nx, y = list(pair())', 'x()'),
         (0, 0)),
        ('shadowed-list-other', body(
            'list = lambda seq: seq[::-1]\nx, y = list(pair())', 'y()'),
         (1, 1)),
        ('shadowed-reversed', body(
            'reversed = list\nx, y = reversed(pair())', 'y()'), (0, 0)),
        ('shadowed-reversed-clean', body(
            'reversed = list\nx, y = reversed(pair())', 'x()').replace(
                'lambda: send(', 'lambda: ordinary('), (0, 0)),
    ]
    verdicts(tmp, cases)


def test_materialized_order_only_for_order_preserving_consumers(_tmp):
    """list and tuple re-kind a container without reordering or dropping an
    element, so their result keeps the operand's indices. Every other eager
    consumer either reorders or deduplicates, or -- as dict does -- projects
    each item into a key, so its result is not a positional pairing of the
    operand's own elements. dict preserves insertion order, so order alone is
    not what excludes it: the projection is."""
    node = ast.parse('pair()').body[0].value
    container = DeferredContainer({0: 'first', 1: 'second'}, 2, 'tuple')
    state = FlowState({}, {}, {}, {id(node): container}, set(), set(),
                      {}, set())
    for consumer in ('list', 'tuple'):
        ordered = materialized_order(consumer, node, [state])
        assert ordered is not None, consumer
        assert ordered.kind == consumer, consumer
        assert ordered.items == {0: 'first', 1: 'second'}, consumer
    for consumer in ('dict', 'frozenset', 'max', 'min', 'set', 'sorted',
                     'sum'):
        assert materialized_order(consumer, node, [state]) is None, consumer
    # Why dict is excluded: iterating it yields the keys -- each pair's first
    # element -- not the pairs the operand held, so a positional read cannot
    # recover the operand's own elements. This is the projection, and it is
    # what the exclusion is for; nothing here relies on order.
    pairs = [('first', 1), ('second', 2)]
    assert list(dict(pairs)) == ['first', 'second']
    assert list(pairs) == pairs


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='collapse_')


if __name__ == '__main__':
    raise SystemExit(main())
