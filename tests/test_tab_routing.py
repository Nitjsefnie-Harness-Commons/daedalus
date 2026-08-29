#!/usr/bin/env python3
"""Guard queue-routing `tab` separately from browser identity `tabId`."""
import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _jsroute import js_tab_routing_violations  # noqa: E402
from _pyroute import (dict_assignments, payload_keys,  # noqa: E402
                      py_tab_routing_violations)
from _pyroute_state import literal_iterable_cardinality  # noqa: E402
from _repo import ROOT  # noqa: E402


def _generator_flow(initial, effect, *steps, iterable='(1,)'):
    lines = [f'send = b.{initial}',
             f'gen = ((send := b.{effect}) for _ in {iterable})', *steps]
    return '\n    '.join(lines)


def _focus_flow(initial, effect, *steps, iterable='[1]'):
    lines = [f'send = {initial}',
             f'gen = ((send := {effect}) for _ in {iterable})', *steps,
             "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"]
    return '\n'.join(lines)


def _tracked_focus_verdict(tmp, body, before='', after='', counts=False):
    source = ROOT / 'daedalus_cli' / 'commands_browser.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(
        node, ast.FunctionDef) and node.name == 'do_focus_tab')
    function.body = ast.parse(body).body
    before_nodes, after_nodes = (ast.parse(value).body
                                 for value in (before, after))
    future_nodes = [node for node in before_nodes if isinstance(
        node, ast.ImportFrom) and node.module == '__future__']
    before_nodes = [node for node in before_nodes
                    if node not in future_nodes]
    tree.body[1:1] = future_nodes
    index = tree.body.index(function)
    tree.body[index:index] = before_nodes
    tree.body.extend(after_nodes)
    ast.fix_missing_locations(tree)
    mutated = Path(tmp) / 'commands_browser.py'
    mutated.write_text(ast.unparse(tree) + '\n', encoding='utf-8')
    calls = []

    def ext_cmd(*args, **kwargs):
        calls.append((args, kwargs))
        return 0

    namespace = {'ext_cmd': ext_cmd,
                 'ordinary': lambda *args, **kwargs: 0,
                 '_args': SimpleNamespace(
                     chrome_tab=323, flag=True, values=(1, 2))}
    sender_module = ModuleType('_pyroute_test_sender')
    sender_module.__dict__.update(namespace)
    sys.modules['_pyroute_test_sender'] = sender_module
    isolated = ast.fix_missing_locations(ast.Module(
        body=[*future_nodes, *before_nodes, function, *after_nodes],
        type_ignores=[]))
    try:
        # pylint: disable-next=exec-used
        exec(compile(isolated, str(mutated), 'exec'), namespace)
        if not after_nodes:
            namespace['do_focus_tab'](namespace['_args'])
    finally:
        sys.modules.pop('_pyroute_test_sender', None)
    verdict = (len(calls), len(py_tab_routing_violations(
        mutated, mutated.name)))
    return verdict if counts else tuple(bool(value) for value in verdict)


_DIRECTIONS = [('setting', 'ordinary', 'ext_cmd', True),
               ('clearing', 'ext_cmd', 'ordinary', False)]


def _assert_focus_cases(tmp, cases):
    observed = [(label, *_tracked_focus_verdict(tmp, body, before, after))
                for label, body, before, after, _ in cases]
    expected = [(label, *(value if isinstance(value, tuple)
                else (value, value))) for label, _, _, _, value in cases]
    assert observed == expected, observed


def test_generator_break_tracks_only_remaining_effects(tmp):
    specs = [
        ('one-setting', 'ordinary', 'ext_cmd', '[1]', False),
        ('one-clearing', 'ext_cmd', 'ordinary', '[1]', True),
        ('two-setting', 'ordinary', 'ext_cmd', '[1, 2]', True),
        ('two-clearing', 'ext_cmd', 'ordinary', '[1, 2]', False),
        ('empty', 'ext_cmd', 'ordinary', '[]', True),
        ('unknown', 'ordinary', 'ext_cmd', 'args.values', True),
    ]
    for label, iterable in [
        ('set', '{1, 1}'), ('dict', "{'x': 1, 'x': 2}"),
        ('set-star', '{*{1}, *{1}}'),
        ('dict-star', "{**{'x': 1}, **{'x': 2}}"),
        ('equality', '{1, True}'),
        ('nested', "{('x', (1, True)): [1], ('x', (True, 1)): {2}}"),
    ]:
        specs.extend([(f'{label}-setting', 'ordinary', 'ext_cmd', iterable,
                       False),
                      (f'{label}-clearing', 'ext_cmd', 'ordinary', iterable,
                       True)])
    for condition, two in [('True', True), ('False', False)]:
        specs.extend([
            (f'filter-{condition}-setting', 'ordinary', 'ext_cmd',
             f'[1, 2] if {condition}', two),
            (f'filter-{condition}-clearing', 'ext_cmd', 'ordinary',
             f'[1, 2] if {condition}', not two),
            (f'filter-{condition}-one', 'ordinary', 'ext_cmd',
             f'[1] if {condition}', False),
        ])
    cases = [(label, _focus_flow(
        initial, effect, 'for _ in gen: break', f'send = {initial}',
        'list(gen)', iterable=iterable), '', '', expected)
        for label, initial, effect, iterable, expected in specs]
    cases.extend([
        ('filter-dynamic-clearing', _focus_flow(
            'ext_cmd', 'ordinary', 'for _ in gen: break', 'send = ext_cmd',
            'list(gen)', iterable='[1, 2] if args.flag'), '', '',
         (False, True)),
    ])
    _assert_focus_cases(tmp, cases)
    for expression in ('{value, value}', '{[1]}', '{1 / 0}', '{**[]}',
                       "{'x': value, 'x': 2}"):
        node = ast.parse(expression, mode='eval').body
        assert literal_iterable_cardinality(node) is None, expression


def test_generator_filter_short_circuit_skips_later_effects(tmp):
    specs = [
        ('skip-set', 'ordinary', 'False if (send := ext_cmd)', False),
        ('skip-clear', 'ext_cmd', 'False if (send := ordinary)', True),
        ('prior-set', 'ordinary', '(send := ext_cmd) if False', True),
        ('prior-clear', 'ext_cmd', '(send := ordinary) if False', False),
    ]
    cases = [(label, _focus_flow(initial, initial, 'list(gen)',
                                 iterable=f'[1] if {filters}'), '', '', result)
             for label, initial, filters, result in specs]
    _assert_focus_cases(tmp, cases)


def test_builtin_consumers_follow_python_scope_identity(tmp):
    unbound = ('try:\n    list(gen)\nexcept UnboundLocalError:\n'
               '    pass')
    local_walrus = ('try:\n    sorted(gen, key=(sorted := ordinary))\n'
                    'except UnboundLocalError:\n    pass')
    directions = [('setting', 'ordinary', 'ext_cmd'),
                  ('clearing', 'ext_cmd', 'ordinary')]
    scenarios = [
        ('later-local', (unbound, 'if args.flag: list = ordinary'), False),
        ('walrus-local', (local_walrus,), False),
        ('deleted-local', ('list = ordinary', 'del list', unbound), False),
        ('deleted-global', ('global list', 'list = ordinary', 'del list',
                            'list(gen)'), True),
        ('frozen-global-builtin', ('global sorted',
                                   'sorted(gen, key=(sorted := ordinary))'),
         True),
    ]
    if sys.version_info < (3, 14):
        scenarios.append(('annotation-local', (unbound,
                          'def inner(x: (list := ordinary)): pass'), False))
    cases = [(f'{label}-{direction}', _focus_flow(initial, effect, *steps),
              '', '', expected if direction == 'setting' else not expected)
             for label, steps, expected in scenarios
             for direction, initial, effect in directions]
    _assert_focus_cases(tmp, cases)
    plain = "ordinary = lambda *args, **kwargs: 0\n"
    shadowed = plain + 'list = ordinary\n'
    module_cases = []

    def add_pair(label, before, after, consumes, wrapper=lambda flow: flow):
        for direction, initial, effect in directions:
            body = wrapper(_focus_flow(initial, effect, 'list(gen)'))
            expected = consumes if direction == 'setting' else not consumes
            module_cases.append((f'{label}-{direction}', body, before, after,
                                 expected))

    call = 'do_focus_tab(_args)'
    add_pair('bind', plain, f'list = ordinary\n{call}', False)
    add_pair('delete', shadowed, f'del list\n{call}', True)
    add_pair('if-bind', plain, f'if True:\n    list = ordinary\n{call}', False)
    add_pair('if-delete', shadowed, f'if True:\n    del list\n{call}', True)
    add_pair('try-bind', plain, 'try:\n    list = ordinary\nexcept '
             f'NameError:\n    pass\n{call}', False)
    add_pair('try-delete', shadowed, 'try:\n    del list\nexcept '
             f'NameError:\n    pass\n{call}', True)
    add_pair('early', plain, f'{call}\ndo_focus_tab = ordinary\n'
             'list = ordinary', True)
    add_pair('global', plain, f'list = ordinary\n{call}', False,
             lambda flow: 'global list\n' + flow)
    add_pair('nested', plain, f'list = ordinary\n{call}', False,
             lambda flow: ('def inner():\n    '
                           + flow.replace('\n', '\n    ')
                           + '\nreturn inner()'))
    _assert_focus_cases(tmp, module_cases)
    for direction, initial, effect, expected_count in [
            ('setting', 'ordinary', 'ext_cmd', 0),
            ('clearing', 'ext_cmd', 'ordinary', 2)]:
        flow = _focus_flow(initial, effect, 'list(gen)')
        later = ('list = ordinary\ndef later(args):\n    '
                 + flow.replace('\n', '\n    ')
                 + '\ndo_focus_tab(_args)\nlater(_args)')
        counts = _tracked_focus_verdict(
            tmp, flow, plain, later, counts=True)
        assert counts == (expected_count, expected_count), (direction, counts)


def test_deferred_annotation_and_class_state_flow_matches_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    module_call = "send('_focus', 'focus-tab', tab=int(_args.chrome_tab))"
    cases = []
    for label, initial, effect, expected in _DIRECTIONS:
        before = f'send = {initial}\n'
        after = f'send = {effect}\ndo_focus_tab(_args)'
        old_gen = f'gen = ((send := {initial}) for _ in [1])\n'
        new_gen = f'gen = ((send := {effect}) for _ in [1])\n'
        imported = 'from _pyroute_test_sender import {} as send\n'
        cases.extend([
            (f'assignment-{label}', call, before, after, expected),
            (f'import-{label}', call, imported.format(initial),
             imported.format(effect) + 'do_focus_tab(_args)', expected),
            (f'if-{label}', call, before,
             f'if True:\n    send = {effect}\ndo_focus_tab(_args)', expected),
            (f'try-{label}', call, 'ordinary=lambda *a,**k:0\n' + before,
             f'try:\n    send = {effect}\nexcept NameError: pass\n'
             'do_focus_tab(_args)', expected),
            (f'generator-{label}', f'list(gen)\n{call}', before + old_gen,
             new_gen + 'do_focus_tab(_args)', expected),
            (f'propagation-{label}', 'list(gen)',
             before + old_gen, (new_gen
                                + f'do_focus_tab(_args)\n{module_call}'),
             expected),
            (f'global-{label}', f'global send\n{call}', before,
             after, expected),
            (f'early-{label}', call, before,
             'do_focus_tab(_args)\ndo_focus_tab = ordinary\n'
             f'send = {effect}', initial == 'ext_cmd'),
            (f'closure-{label}',
             f'send = {initial}\ndef inner():\n    return {call}\n'
             f'send = {effect}\nreturn inner()', '', '', expected),
            (f'nonlocal-{label}',
             f'send = {initial}\ndef inner():\n    nonlocal send\n'
             f'    send = {effect}\ninner()\n{call}', '', '', expected),
        ])
    local = ('try:\n    ' + call + '\nexcept UnboundLocalError:\n    pass\n'
             'if False:\n    send = ordinary')
    cases.append(('lexical-local', local, 'send = ext_cmd\n', '', False))
    eager = sys.version_info < (3, 14)
    for label, initial, effect, _ in _DIRECTIONS:
        initial_sender = initial == 'ext_cmd'
        effect_sender = effect == 'ext_cmd'
        cases.extend([
            (f'postponed-{label}', _focus_flow(
                initial, effect, 'def inner(value: list(gen)): pass'),
             'from __future__ import annotations', '', initial_sender),
            (f'default-{label}', _focus_flow(
                initial, effect, 'def inner(value=list(gen)): pass'),
             '', '', effect_sender),
            (f'default-annotation-{label}', _focus_flow(
                initial, effect, 'def inner(value: list(gen)): pass'), '',
             '', effect_sender if eager else initial_sender),
        ])
    support = ('class Base: pass\n'
               'def decorate(value): return lambda cls: cls\n'
               'def base_factory(*values): return Base\n'
               'def meta_factory(*values): return type\n')
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    shapes = [
        ('body', 'class Inner:\n    list(gen)'),
        ('decorator', '@decorate(list(gen))\nclass Inner: pass'),
        ('base', 'class Inner(base_factory(list(gen))): pass'),
        ('keyword', 'class Inner(metaclass=meta_factory(list(gen))): pass'),
        ('call', 'class Inner:\n    def run(self): list(gen)\nInner().run()'),
        ('direct', 'class Inner:\n    def run(self): list(gen)\n'
                   'Inner.run(None)'),
    ]
    for label, initial, effect, expected in _DIRECTIONS:
        prefix = (f'send = {initial}\n'
                  f'gen = ((send := {effect}) for _ in [1])\n')
        cases.extend((f'{shape}-{label}', prefix + body + '\n' + call,
                      support, '', expected) for shape, body in shapes)
    setting = 'send = ordinary\ngen = ((send := ext_cmd) for _ in [1])\n'
    orders = [
        ('decorator-base', '@decorate(list(gen))\nclass Inner(base_factory('
         f'{call})): pass'),
        ('base-keyword', 'class Inner(base_factory(list(gen)), '
         f'metaclass=meta_factory({call})): pass'),
        ('keyword-body', 'class Inner(metaclass=meta_factory(list(gen))):\n'
         f'    {call}'),
    ]
    cases.extend((label, setting + body, support, '', True)
                 for label, body in orders)
    local = f'send = ext_cmd\nclass Inner:\n    send = ordinary\n{call}'
    cases.append(('class-local', local, support, '', True))
    _assert_focus_cases(tmp, cases)


def test_destructured_and_walrus_sender_aliases_are_caught(tmp):
    bodies = [
        "send = _ext_cmd", "send = b.ext_cmd", "send: object = _ext_cmd",
        "a = _ext_cmd\n    send = a", "send = getattr(b, 'ext_cmd')",
        "send = b.ext_cmd if flag else b.ext_cmd",
        "send = lambda *a, **k: b.ext_cmd(*a, **k)",
        "send, other = b.ext_cmd, None",
        "[send, other] = [b.ext_cmd, None]",
        "([send], other) = ([b.ext_cmd], None)",
        "send, *other = b.ext_cmd, None",
        "send, other = wrap(b.ext_cmd)",
        "send = b.ext_cmd\n    other, send = send, None\n"
        "    return await other('x', 'y', tab=tab)",
        "send = b.ext_cmd\n    send, other = wrap(send)",
        "send = b.ext_cmd\n    other, send = send, (send := b.get)\n"
        "    return await other('x', 'y', tab=tab)",
        "send = b.get\n    send, other = (send := b.ext_cmd), send\n"
        "    return await other('x', 'y', tab=tab)",
        "send = b.get\n    inner = lambda x=(send := b.ext_cmd): x",
        "send = b.get\n    def inner(x=(send := b.ext_cmd)): pass",
        "send = b.get\n    @(send := b.ext_cmd)\n    def inner(): pass",
        "send = b.ext_cmd\n    def inner(other=send, "
        "reset=(send := b.get)):\n        return await other(tab=tab)",
        "send = b.get\n    def inner(reset=(send := b.ext_cmd), "
        "other=send):\n        return await other(tab=tab)",
        "send = b.ext_cmd\n    inner = lambda other=send, "
        "reset=(send := b.get): other(tab=tab)\n    return await inner()",
        "send = b.get\n    @(send := b.ext_cmd)\n"
        "    def inner(other=send):\n        return await other(tab=tab)",
        "return await (send := b.ext_cmd)('x', 'y', tab=tab)",
        "(send := b.ext_cmd)",
        "[(send := b.ext_cmd) for _ in values]",
        "send = b.ext_cmd\n    [(send := b.get) for _ in [*()]]",
        "send = b.get\n    [(send := b.ext_cmd) for _ in [None, *()]]",
        "send = b.ext_cmd\n    [(send := b.get) for _ in [*{}]]",
        "send = b.get\n    [(send := b.ext_cmd) for _ in [*{'x': 1}]]",
        "send = b.ext_cmd\n    [(send := b.get) for _ in [*{**{}}]]",
        "send = b.get\n    [(send := b.ext_cmd) for _ in [*{**{'x': 1}}]]",
        _generator_flow('ext_cmd', 'get'),
        _generator_flow('get', 'ext_cmd', 'alias = gen', 'tuple(alias)'),
        _generator_flow('get', 'ext_cmd', 'for _ in gen: pass'),
        _generator_flow('get', 'ext_cmd', '[value for value in gen]'),
        _generator_flow('get', 'ext_cmd', 'flag and list(gen)'),
        _generator_flow('ext_cmd', 'get', 'flag and list(gen)'),
        _generator_flow('get', 'ext_cmd', 'if flag: list(gen)'),
        _generator_flow('ext_cmd', 'get', 'if flag: list(gen)'),
        "if flag:\n        send = b.ext_cmd\n"
        "    else:\n        send = b.get",
        "if flag:\n        send = b.get\n"
        "    else:\n        send = b.ext_cmd",
        _generator_flow('ext_cmd', 'get', 'for _ in gen: pass',
                        'send = b.ext_cmd', 'list(gen)'),
        _generator_flow('ext_cmd', 'get',
                        'for _ in gen:\n        for _ in (1,): break',
                        'send = b.ext_cmd', 'list(gen)'),
        _generator_flow('get', 'ext_cmd', 'for _ in gen: break',
                        'send = b.get', 'list(gen)', iterable='(1, 2)'),
        _generator_flow('get', 'ext_cmd',
                        'for _ in gen:\n        break\n    else:\n'
                        '        send = b.get', iterable='(1, 2)'),
        _generator_flow('ext_cmd', 'get', 'for _ in gen: pass',
                        'send = b.ext_cmd', 'list(gen)', iterable='()'),
        "list = b.get\n    send = b.ext_cmd\n"
        "    gen = ((send := b.get) for _ in (1,))\n    list(gen)",
        _generator_flow('ext_cmd', 'get', 'list = b.get', 'list(gen)'),
        _generator_flow('ext_cmd', 'get',
                        'if flag: list = b.get', 'list(gen)'),
        "from tools import list\n    send = b.ext_cmd\n"
        "    gen = ((send := b.get) for _ in (1,))\n    list(gen)",
        _generator_flow('ext_cmd', 'get',
                        'def list(value): pass', 'list(gen)'),
        *(_generator_flow('ext_cmd', 'get', f'b.{method}(gen)')
          for method in ('extend', 'join', 'update', 'writelines')),
        "send = b.get\n    tuple((send := b.ext_cmd) for _ in (1,))",
        "flag and (send := b.ext_cmd)",
        "((send := b.ext_cmd) if flag else (send := b.get))",
        "send = b.ext_cmd\n    return await send((send := b.get), tab=tab)",
        "send = b.ext_cmd\n    def inner():\n        (send := b.get)",
        "send = b.ext_cmd\n    inner = lambda: (send := b.get)",
        *(["send = b.get\n    def inner(x: (send := b.ext_cmd)): pass"]
          if sys.version_info < (3, 14) else []),
    ]
    source = Path(tmp) / 'new_sender_alias.py'
    call = "\n    return await send('x', 'y', tab=tab)"
    for body in bodies:
        tail = call if 'return await' not in body else ''
        text = "async def f(b, tab, values, flag):\n    " + body + tail + "\n"
        source.write_text(text, encoding='utf-8')
        assert py_tab_routing_violations(source, source.name), body


def test_destructured_and_walrus_rebindings_stay_positioned_and_scoped(tmp):
    bodies = [
        "send, sender = b.get, b.ext_cmd",
        "send = b.ext_cmd\n    send, other = make_pair()",
        "send = b.ext_cmd\n    send, other = (b.get,)",
        "send = b.ext_cmd\n    other, send = send, b.get",
        "send = b.ext_cmd\n    send, other = wrap(b.get)",
        "send = b.get\n    other, send = send, (send := b.ext_cmd)\n"
        "    return await other('x', 'y', tab=tab)",
        "send = b.ext_cmd\n    send, other = (send := b.get), send\n"
        "    return await other('x', 'y', tab=tab)",
        "send = b.ext_cmd\n    inner = lambda x=(send := b.get): x",
        "send = b.ext_cmd\n    def inner(x=(send := b.get)): pass",
        "send = b.ext_cmd\n    @(send := b.get)\n    def inner(): pass",
        "send = b.get\n    def inner(other=send, "
        "reset=(send := b.ext_cmd)):\n        return await other(tab=tab)\n"
        "    send = b.get",
        "send = b.ext_cmd\n    def inner(reset=(send := b.get), "
        "other=send):\n        return await other(tab=tab)",
        "send = b.get\n    inner = lambda other=send, "
        "reset=(send := b.ext_cmd): other(tab=tab)\n    return await inner()",
        "send = b.ext_cmd\n    @(send := b.get)\n"
        "    def inner(other=send):\n        return await other(tab=tab)",
        "send = b.ext_cmd\n    (send := b.get)",
        "send = b.ext_cmd\n    [(send := b.get) for _ in (1,)]",
        "send = b.get\n    [(send := b.ext_cmd) for _ in [*()]]",
        "send = b.ext_cmd\n    [(send := b.get) for _ in [None, *()]]",
        "send = b.get\n    [(send := b.ext_cmd) for _ in [*{}]]",
        "send = b.ext_cmd\n    [(send := b.get) for _ in [*{'x': 1}]]",
        "send = b.get\n    [(send := b.ext_cmd) for _ in [*{**{}}]]",
        "send = b.ext_cmd\n    [(send := b.get) for _ in [*{**{'x': 1}}]]",
        _generator_flow('get', 'ext_cmd'),
        _generator_flow('ext_cmd', 'get', 'alias = gen', 'tuple(alias)'),
        _generator_flow('ext_cmd', 'get', 'for _ in gen: pass'),
        _generator_flow('ext_cmd', 'get', '[value for value in gen]'),
        _generator_flow('get', 'ext_cmd', 'list(gen)', iterable='()'),
        _generator_flow('get', 'ext_cmd', 'for _ in gen: pass',
                        'send = b.get', 'list(gen)'),
        _generator_flow('get', 'ext_cmd',
                        'for _ in gen:\n        for _ in (1,): break',
                        'send = b.get', 'list(gen)'),
        _generator_flow('ext_cmd', 'get', 'for _ in gen: break',
                        'send = b.ext_cmd', 'list(gen)', iterable='(1, 2)'),
        _generator_flow('get', 'ext_cmd', 'for _ in gen: pass',
                        'else: send = b.get', 'list(gen)'),
        _generator_flow('get', 'ext_cmd', 'for _ in gen: pass',
                        'list(gen)', iterable='()'),
        "send = b.get\n"
        "    gen = ((send := b.ext_cmd) async for _ in values)\n"
        "    async for _ in gen: pass\n    send = b.get\n"
        "    async for _ in gen: pass",
        "list = b.get\n    send = b.get\n"
        "    gen = ((send := b.ext_cmd) for _ in (1,))\n    list(gen)",
        _generator_flow('get', 'ext_cmd', 'list = b.get', 'list(gen)'),
        _generator_flow('get', 'ext_cmd',
                        'if flag: list = b.get\n    else: list = print',
                        'list(gen)'),
        "send = b.get\n    gen = ((send := b.ext_cmd) for _ in (1,))\n"
        "    async def inner(list):\n        list(gen)\n"
        "        return await send('x', 'y', tab=tab)",
        "send = b.ext_cmd\n    gen = ((send := b.get) for _ in (1,))\n"
        "    async def inner(list):\n        list(gen)\n"
        "        return await send('x', 'y', tab=tab)\n    send = b.get",
        "from tools import list\n    send = b.get\n"
        "    gen = ((send := b.ext_cmd) for _ in (1,))\n    list(gen)",
        _generator_flow('get', 'ext_cmd',
                        'def list(value): pass', 'list(gen)'),
        *(_generator_flow('get', 'ext_cmd', f'b.{method}(gen)')
          for method in ('extend', 'join', 'update', 'writelines')),
        "send = b.ext_cmd\n    tuple((send := b.get) for _ in (1,))",
        "def inner():\n        (send := b.ext_cmd)",
        "inner = lambda: (send := b.ext_cmd)",
        *(["send = b.ext_cmd\n    def inner(x: (send := b.get)): pass"]
          if sys.version_info < (3, 14) else []),
    ]
    rebinding = [
        "send = b.ext_cmd\nsend = b.get",
        "send = b.ext_cmd\nwhile values:\n    send = b.get\n"
        "    await send(tab=tab)\nbreak",
        "send = b.ext_cmd\nfor send in (b.get,): pass",
        "send = b.ext_cmd\ndef send(*a, **k): pass",
        "send = b.ext_cmd\nwith b as send:\n    await send(tab=tab)",
        "send = b.ext_cmd\ntry: raise OSError\n"
        "except OSError as send:\n    await send(tab=tab)\nreturn None",
        "send = b.ext_cmd\nfor _ in values:\n"
        "    def send(*a, **k): pass\n    send(tab=tab)\nreturn None",
        "send = b.ext_cmd\nreturn await b.send('x', 'y', tab=tab)",
    ]
    patterns = ["object() as send", "{'s': send}", "[_, *send]",
                "{'a': 1, **send}", "BaseException(args=send)"]
    rebinding.extend("send = b.ext_cmd\nmatch b.mode:\n"
                     f"    case {pattern}:\n        await send(tab=0)\nreturn"
                     for pattern in patterns)
    bodies.extend(body.replace('\n', '\n    ') for body in rebinding)
    source = Path(tmp) / 'clean_sender_alias.py'
    for body in bodies:
        text = ("async def f(b, tab, values, flag):\n    " + body
                + "\n    return await send('x', 'y', tab=tab)\n")
        source.write_text(text, encoding='utf-8')
        assert not py_tab_routing_violations(source, source.name), body


def test_no_client_sends_the_browser_target_as_the_routing_field(tmp):
    """Typed commands route to extension; eval payloads may route by tab."""
    tree = ast.parse("cmd = dict(BASE)\ncmd['tab'] = tab_id\n"
                     "api('PUT', '/command', cmd)\n")
    assert payload_keys(tree.body[0].value, {}) is None
    assert dict_assignments(tree)['cmd']['tab'][0] == 2

    def js(body, args='tid'):
        return 'js', f'async function f({args}) {{\n{body}\n}}\n'

    senders_py = [ROOT / 'daedalus_mcp' / 'server.py',
                  *(ROOT / 'daedalus_mcp').glob('tools_*.py'),
                  *(ROOT / 'daedalus_cli').glob('*.py')]
    senders_js = sorted((ROOT / 'dashboard').rglob('*.js'))
    scanned_py = [p for p in senders_py if p.is_file()]
    assert len(scanned_py) >= 18, ('Python senders moved', len(scanned_py))
    assert len(senders_js) >= 10, ('dashboard senders moved', len(senders_js))
    violations = []
    scanners = [(path, py_tab_routing_violations) for path in scanned_py]
    scanners += [(path, js_tab_routing_violations) for path in senders_js]
    for path, scanner in scanners:
        violations.extend(scanner(path, path.relative_to(ROOT)))
    assert not violations, (
        'browser tab sent as typed-command `tab`:\n' + '\n'.join(violations))
    skipped_bodies = [
        "    for send in ():\n        pass\n",
        "    while False:\n        send = bridge.get\n",
        "    try: pass\n    except OSError as send: pass\n",
        "    match 0:\n        case 1 as send:\n            pass\n",
    ]
    entered_bodies = [
        "    try:\n        send = bridge.ext_cmd; "
        "return await send('x', 'y', tab=x)\n"
        "    except OSError:\n        return None\n",
        "    async with bridge.session():\n"
        "        send = bridge.ext_cmd; return await send('x','y',tab=x)\n",
        "    for item in xs:\n        send = bridge.ext_cmd; "
        "await send('x','y',tab=x)\n",
        "    while xs:\n        send = bridge.ext_cmd; "
        "await send('x','y',tab=x); break\n",
    ]
    reversions = [
        ('py', "class Tabs:\n    async def focus(self, tab):\n"
               "        return await _ext_cmd('x', 'y', tab=tab)\n"),
        ('py', "async def f(chrome_tab):\n"
               "    fields = {}\n"
               "    fields['tab'] = str(chrome_tab)\n"
               "    return await _ext_cmd('_ss', 'screenshot', **fields)\n"),
        ('py', "def f(args):\n"
               "    cmd = {'id': '_ss', 'type': 'screenshot', 'tab': 'extension'}\n"
               "    cmd[\"tab\"] = int(args.chrome_tab)\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "async def f(t):\n"
               "    extra = {'tab': str(t)}\n"
               "    return await _ext_cmd('_cdp', 'cdp', **extra)\n"),
        js("  const fields = {};\n  fields.tab = Number(tabSel.value);\n"
           "  await extCmd('screenshot', fields);", ''),
        js("  await extCmd('cdp', { method: m.trim(), params: {}, "
           "tab: tid });", 'm, tid'),
        js("  const f = { tab: tid };\n  await extCmd('cdp', f);"),
        js("  await extCmd('net-capture', { method: 'Network.enable',"
           " params: { maxTotalBufferSize: 10000000, maxResourceBufferSize:"
           " 5000000, maxPostDataSize: 65536 }, note: 'padding padding"
           " padding padding padding padding padding padding', tab: tid });"),
        ('py', "def f(args):\n"
               "    cmd: dict = {'id': '_x', 'type': 'close-tab', 'tab': 'extension'}\n"
               "    cmd['tab'] = int(args.chrome_tab)\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "async def f(t):\n"
               "    fields: dict = {'css': 'x'}\n"
               "    fields['tab'] = t\n"
               "    return await _ext_cmd('_css', 'inject-css', **fields)\n"),
        ('py', "def f(tid):\n"
               "    cmd = dict(id='_x', type='close-tab', tab=tid)\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "def f(tid):\n"
               "    cmd = {'id': '_x', 'type': 'close-tab'}\n"
               "    cmd.update({'tab': tid})\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "def f(tid):\n"
               "    cmd = {'id': '_x', 'type': 'close-tab'}\n"
               "    cmd |= {'tab': tid}\n"
               "    api('PUT', '/command', cmd)\n"),
        ('js', "async function load() {\n"
               "  const fields = {};\n"
               "  fields.tab = Number(tabSel.value);\n"
               "  await extCmd('cookies', fields);\n"
               "}\n"
               "function later(q) {\n"
               "  const fields = { url: q };\n"
               "  extCmd('set-cookie', fields);\n"
               "}\n"),
        js("  const fields = {};\n  fields['tab'] = tid;\n"
           "  await extCmd('screenshot', fields);"),
        ('py', "def f(tid):\n"
               "    spread = build_fields(tid)\n"
               "    cmd = {'id': '_x', 'type': 'close-tab',"
               " 'tab': 'extension', **spread}\n"
               "    api('PUT', '/command', cmd)\n"),
        ('py', "def f(flag, tid):\n"
               "    cmd = {'id': '_x', 'type': 'close-tab',"
               " 'tab': 'extension'}\n"
               "    if flag:\n"
               "        cmd['tab'] = tid\n"
               "        api('PUT', '/command', cmd)\n"
               "    else:\n"
               "        cmd['tab'] = 'extension'\n"),
        js("  await extCmd('cdp', { ...{ ['tab']: tid } });"),
        js("  const command = { type: 'cdp', tab: tid };\n"
           "  await runCommand(command);"),
        js("  await extCmd('cdp', {}, { tab: target });", 'target'),
        js("  const fields = {};\n  const alias = fields;\n"
           "  alias.tab = tid;\n  await extCmd('screenshot', fields);"),
        ('js', "function addTab(target, tid) {\n"
               "  target.tab = tid;\n"
               "}\n"
               "async function f(tid) {\n"
               "  const fields = {};\n"
               "  addTab(fields, tid);\n"
               "  await extCmd('screenshot', fields);\n"
               "}\n"),
        js("  const fields = flag ? { tab: tid } : {};\n"
           "  await extCmd('screenshot', fields);", 'flag, tid'),
        js("  const fields = {};\n  Object.assign(fields, { tab: tid });\n"
           "  await extCmd('screenshot', fields);"),
        ('js', "function build(tid) {\n"
               "  const f = {};\n"
               "  f.tab = tid;\n"
               "  return f;\n"
               "}\n"
               "async function g(tid) {\n"
               "  await extCmd('screenshot', build(tid));\n"
               "}\n"),
    ]
    reversions.extend(
        ('py', "async def f(bridge):\n    send = bridge.ext_cmd\n"
         + body + "    return await send('x', 'y', tab=309)\n")
        for body in skipped_bodies)
    reversions.extend(
        ('py', "async def f(bridge, x, xs):\n" + body)
        for body in entered_bodies)
    legitimate = [
        ('py', "def f():\n    send = _ext_cmd\n"
               "    async def inner(send):\n"
               "        return await send('x', 'y', tab=307)\n"),
        ('py', "async def f(cmd_id, code, tab_id):\n"
               "    payload = {'id': cmd_id, 'code': code}\n"
               "    payload['tab'] = tab_id\n"
               "    await _put('/command', payload)\n"),
        ('py', "async def f(chrome_tab):\n"
               "    return await _ext_cmd('_focus', 'focus-tab',"
               " tabId=int(chrome_tab))\n"),
        js("  await runCommand({ tab: tabId, code });", 'tabId, code'),
        js("  await extCmd('screenshot', { tabId: Number(tid) });"),
        ('py', "async def f(chrome_tab):\n"
               "    fields: dict = {'css': 'x'}\n"
               "    fields['tabId'] = int(chrome_tab)\n"
               "    return await _ext_cmd('_css', 'inject-css', **fields)\n"),
        js("  // never do fields['tab'] = Number(tabSel.value) here\n"
           "  await extCmd('cookies', fields);", 'fields'),
        js("  const fields = { url: q };\n"
           "  // fields['tab'] = Number(tabSel.value) would be wrong\n"
           "  let done = false;\n  await extCmd('cookies', fields);", 'q'),
    ]

    disclosed_js_limits = [
        ('assignment after the call that runs before it',
         "async function send() {\n"
         "  await extCmd('screenshot', fields);\n"
         "}\n"
         "const fields = { tab: 'not-extension' };\n"),
        ('fields arriving as a parameter',
         "async function f(fields) {\n"
         "  await extCmd('screenshot', fields);\n"
         "}\n"),
    ]
    fixture = Path(tmp) / 'sender'

    def scan(lang, source, label):
        path = fixture.with_suffix('.py' if lang == 'py' else '.js')
        path.write_text(source, encoding='utf-8')
        scanner = (py_tab_routing_violations if lang == 'py'
                   else js_tab_routing_violations)
        return scanner(path, label)

    for i, (lang, src) in enumerate(reversions):
        assert scan(lang, src, f'reversion-{i}'), (
            f'reversion {i} was not caught:\n{src}')
    for i, (lang, src) in enumerate(legitimate):
        found = scan(lang, src, f'legitimate-{i}')
        assert not found, (
            f'legitimate shape {i} was flagged:\n{src}\n{found}')
    for i, (label, src) in enumerate(disclosed_js_limits):
        found = scan('js', src, f'disclosed-{i}')
        assert not found, (
            f'disclosed JS limit {label!r} caught:\n{src}\n{found}')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='tabrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
