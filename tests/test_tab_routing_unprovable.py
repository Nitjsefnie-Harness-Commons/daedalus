#!/usr/bin/env python3
"""An unprovable sender alias is reported through an opaque `**spread`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute import py_tab_routing_violations  # noqa: E402
from test_tab_routing import (  # noqa: E402
    _assert_focus_cases, _tracked_focus_verdict)


def _scan(tmp, body):
    source = Path(tmp) / 'unprovable.py'
    source.write_text('def go(args, tab):\n' + body, encoding='utf-8')
    return py_tab_routing_violations(source, source.name)


def _reported(tmp, body):
    found = _scan(tmp, body)
    assert found, f'unprovable-sender shape read clean:\n{body}'
    assert 'may be ext_cmd' in found[0], found


def _silent(tmp, body):
    found = _scan(tmp, body)
    assert not found, (f'provable-clean shape flagged:\n{body}\n'
                       + '\n'.join(found))


def _assert_export_cases(tmp, cases):
    observed = [
        (label, *_tracked_focus_verdict(
            tmp, body, before, '\n'.join((after, execute)).strip()))
        for label, body, before, after, execute, _ in cases]
    expected = [(label, value, value)
                for label, _, _, _, _, value in cases]
    assert observed == expected, observed


def test_opaque_spread_through_unprovable_sender(tmp):
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    send('x', 'y', **build(args))\n")
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    send('x', 'y', **args.payload)\n")


def test_unprovable_sender_reported_shapes(tmp):
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    send('x', 'y', tab=int(args))\n")
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    fields = {'tab': args.tab}\n"
                   "    send('x', 'y', **fields)\n")
    _reported(tmp, "    send = wrap(ext_cmd)\n"
                   "    fields = {'x': 1}\n"
                   "    fields.update(build(args))\n"
                   "    send('x', 'y', **fields)\n")


def test_unprovable_sender_silent_shapes(tmp):
    _silent(tmp, "    send = wrap(ext_cmd)\n"
                 "    fields = {'x': 1}\n"
                 "    send('x', 'y', **fields)\n")
    _silent(tmp, "    send = wrap(ext_cmd)\n"
                 "    fields = {'tab': 'extension', 'x': 1}\n"
                 "    send('x', 'y', **fields)\n")
    _silent(tmp, "    send = wrap(ext_cmd)\n"
                 "    send('x', 'y')\n")


def test_other_branches_unchanged(tmp):
    assert 'cannot be verified' in _scan(
        tmp, "    ext_cmd('x', 'y', **build(args))\n")[0]
    assert 'cannot be verified' in _scan(
        tmp, "    fields = {}\n"
             "    fields.update(build(args))\n"
             "    ext_cmd('x', 'y', **fields)\n")[0]
    assert 'typed /command payload' in _scan(
        tmp, "    cmd = {'id': '_x', 'type': 'close-tab', **extra}\n"
             "    api('PUT', '/command', cmd)\n")[0]
    assert not _scan(
        tmp, "    cmd = {'id': '_x', 'type': 'close-tab'}\n"
             "    api('PUT', '/command', cmd)\n")


def test_lambda_call_reachability_matches_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('alias-before-clear',
         f'send = ordinary\ncall = lambda: {call}\nother = call\n'
         'send = ext_cmd\nother()\nsend = ordinary', '', '', True),
        ('immediate-before-clear',
         f'send = ext_cmd\n(lambda: {call})()\nsend = ordinary',
         '', '', True),
        ('never-called',
         f'send = ext_cmd\ncall = lambda: {call}\nreturn 0',
         '', '', False),
    ]
    _assert_focus_cases(tmp, cases)


def test_compound_lambda_callees_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('conditional',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         '(call if True else ordinary)()', '', '', True),
        ('boolean',
         f'send = ext_cmd\ncall = lambda: {call}\n(True and call)()',
         '', '', True),
        ('conditional-stored',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'chosen = call if args.flag else ordinary\nchosen()\n'
         'send = ordinary', '', '', True),
        ('boolean-stored',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'chosen = True and call\nchosen()\nsend = ordinary',
         '', '', True),
        ('starred-list',
         f'send = ext_cmd\ncall = lambda: {call}\n[*[call]][0]()',
         '', '', True),
        ('list-comprehension',
         f'send = ext_cmd\nbox = [lambda: {call} for _ in [1]]\n'
         'box[0]()', '', '', True),
    ]
    _assert_focus_cases(tmp, cases)


def test_indirect_lambda_invocation_states_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('argument-invoked',
         f'def run(fn): return fn()\nsend = ext_cmd\n'
         f'call = lambda: {call}\nrun(call)\nsend = ordinary',
         '', '', True),
        ('argument-discarded',
         f'def discard(fn): return 0\nsend = ext_cmd\n'
         f'call = lambda: {call}\ndiscard(call)', '', '', False),
        ('list-invoked',
         f'send = ext_cmd\ncall = lambda: {call}\nbox = [call]\n'
         'box[0]()\nsend = ordinary', '', '', True),
        ('list-discarded',
         f'send = ext_cmd\ncall = lambda: {call}\nbox = [call]\n'
         'return 0', '', '', False),
        ('dict-invoked',
         f'send = ext_cmd\ncall = lambda: {call}\nbox = {{"fn": call}}\n'
         'box["fn"]()\nsend = ordinary', '', '', True),
        ('attribute-invoked',
         f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
         f'holder.fn = lambda: {call}\nholder.fn()\nsend = ordinary',
         '', '', True),
        ('attribute-discarded',
         f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
         f'holder.fn = lambda: {call}\nreturn 0', '', '', False),
        ('subscript-store-invoked',
         f'send = ext_cmd\nbox = [ordinary]\nbox[0] = lambda: {call}\n'
         'box[0]()\nsend = ordinary', '', '', True),
        ('subscript-store-discarded',
         f'send = ext_cmd\nbox = [ordinary]\nbox[0] = lambda: {call}\n'
         'return 0', '', '', False),
        ('return-invoked',
         f'send = ordinary\ndef make(): return lambda: {call}\n'
         'call = make()\nsend = ext_cmd\ncall()\nsend = ordinary',
         '', '', True),
        ('return-discarded',
         f'send = ext_cmd\ndef make(): return lambda: {call}\n'
         'make()\nreturn 0', '', '', False),
        ('class-member-invoked',
         f'send = ext_cmd\nclass Holder:\n    fn = lambda self: {call}\n'
         'Holder().fn()\nsend = ordinary', '', '', True),
        ('class-member-discarded',
         f'send = ext_cmd\nclass Holder:\n    fn = lambda self: {call}\n'
         'return 0', '', '', False),
    ]
    _assert_focus_cases(tmp, cases)


def test_module_lambda_invocation_states_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(_args.chrome_tab))"
    cases = [
        ('module-invoked', 'return 0',
         f'send = ext_cmd\ncall = lambda: {call}\n',
         'call()\nsend = ordinary', True),
        ('module-discarded', 'return 0',
         f'send = ext_cmd\ncall = lambda: {call}\n',
         'del call', False),
        ('module-class-invoked', 'return 0',
         f'send = ext_cmd\nclass Holder:\n    fn = lambda self: {call}\n',
         'Holder().fn()\nsend = ordinary', True),
        ('module-class-discarded', 'return 0',
         f'send = ext_cmd\nclass Holder:\n    fn = lambda self: {call}\n',
         'del Holder', False),
    ]
    _assert_focus_cases(tmp, cases)


def test_module_lambda_external_exposure(tmp):
    source = Path(tmp) / 'module_lambda.py'
    call = "send('_focus', 'focus-tab', tab=dynamic)"
    source.write_text(
        f'send = ext_cmd\ncall = lambda: {call}\n', encoding='utf-8')
    assert py_tab_routing_violations(source, source.name)
    source.write_text(
        f'send = ext_cmd\ncall = lambda: {call}\ndel call\n',
        encoding='utf-8')
    assert not py_tab_routing_violations(source, source.name)
    source.write_text(
        f'send = ext_cmd\nclass Holder:\n    fn = lambda self: {call}\n',
        encoding='utf-8')
    assert py_tab_routing_violations(source, source.name)
    source.write_text(
        f'send = ext_cmd\nclass Holder:\n    fn = lambda self: {call}\n'
        'del Holder\n', encoding='utf-8')
    assert not py_tab_routing_violations(source, source.name)


def test_factory_returned_module_exposure_matches_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('factory-direct-survives',
         f'send = ext_cmd\nreturn lambda: {call}', '',
         'call = do_focus_tab(_args)', 'call()', True),
        ('factory-direct-deleted',
         f'send = ext_cmd\nreturn lambda: {call}', '',
         'call = do_focus_tab(_args)\ndel call', '', False),
        ('factory-list-survives',
         f'send = ext_cmd\nreturn [lambda: {call}]', '',
         'box = do_focus_tab(_args)', 'box[0]()', True),
        ('factory-list-deleted',
         f'send = ext_cmd\nreturn [lambda: {call}]', '',
         'box = do_focus_tab(_args)\ndel box', '', False),
        ('factory-instance-survives',
         f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
         f'holder.fn = lambda: {call}\nreturn holder', '',
         'holder = do_focus_tab(_args)', 'holder.fn()', True),
        ('factory-instance-deleted',
         f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
         f'holder.fn = lambda: {call}\nreturn holder', '',
         'holder = do_focus_tab(_args)\ndel holder', '', False),
    ]
    _assert_export_cases(tmp, cases)


def test_factory_closure_cells_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('direct-setting',
         f'send = ordinary\ncall = lambda: {call}\n'
         'send = ext_cmd\nreturn call', '',
         'call = do_focus_tab(_args)', 'call()', True),
        ('direct-clearing',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'send = ordinary\nreturn call', '',
         'call = do_focus_tab(_args)', 'call()', False),
        ('list-setting',
         f'send = ordinary\ncall = lambda: {call}\n'
         'send = ext_cmd\nreturn [call]', '',
         'box = do_focus_tab(_args)', 'box[0]()', True),
        ('list-clearing',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'send = ordinary\nreturn [call]', '',
         'box = do_focus_tab(_args)', 'box[0]()', False),
        ('setter-setting',
         f'send = ordinary\ndef call(): return {call}\n'
         'def update():\n    nonlocal send\n    send = ext_cmd\n'
         'return call, update', '',
         'pair = do_focus_tab(_args)', 'pair[1](); pair[0]()', True),
        ('setter-clearing',
         f'send = ext_cmd\ndef call(): return {call}\n'
         'def update():\n    nonlocal send\n    send = ordinary\n'
         'return call, update', '',
         'pair = do_focus_tab(_args)', 'pair[1](); pair[0]()', False),
        ('conditional-preserve',
         f'send = ext_cmd\ndef call(): return {call}\n'
         'def update(clear):\n    nonlocal send\n'
         '    if clear:\n        send = ordinary\n'
         'return call, update', '',
         'pair = do_focus_tab(_args)', 'pair[1](False); pair[0]()', True),
    ]
    _assert_export_cases(tmp, cases)


def test_returned_alternative_selections_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('returned-list-invoked',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'def choose():\n    return [call] if args.flag else [ordinary]\n'
         'box = choose()\nbox[0]()\nsend = ordinary', '', '', True),
        ('returned-list-discarded',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'def choose():\n    return [call] if args.flag else [ordinary]\n'
         'box = choose()\nreturn 0', '', '', False),
        ('returned-instance-invoked',
         f'class Holder: pass\nbad = Holder()\ngood = Holder()\n'
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'bad.fn = call\ngood.fn = ordinary\n'
         'def choose():\n    return bad if args.flag else good\n'
         'holder = choose()\nholder.fn()\nsend = ordinary', '', '', True),
        ('returned-instance-discarded',
         f'class Holder: pass\nbad = Holder()\ngood = Holder()\n'
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'bad.fn = call\ngood.fn = ordinary\n'
         'def choose():\n    return bad if args.flag else good\n'
         'holder = choose()\nreturn 0', '', '', False),
    ]
    _assert_focus_cases(tmp, cases)


def test_generator_yielded_callables_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('named-next-invoked',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'gen = (call for _ in [1])\nnext(gen)()\nsend = ordinary',
         '', '', True),
        ('inline-next-invoked',
         f'send = ext_cmd\ngen = (lambda: {call} for _ in [1])\n'
         'next(gen)()\nsend = ordinary', '', '', True),
        ('materialized-invoked',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'gen = (call for _ in [1])\nlist(gen)[0]()\nsend = ordinary',
         '', '', True),
        ('never-consumed',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'gen = (call for _ in [1])\nreturn 0', '', '', False),
        ('consumed-not-called',
         f'send = ext_cmd\ncall = lambda: {call}\n'
         'gen = (call for _ in [1])\nnext(gen)\nsend = ordinary',
         '', '', False),
        ('rebound-unsafe',
         f'send = ext_cmd\ncallback = lambda: ordinary()\n'
         f'gen = (callback for _ in [1])\ncallback = lambda: {call}\n'
         'next(gen)()\nsend = ordinary', '', '', True),
        ('rebound-safe',
         f'send = ext_cmd\ncallback = lambda: {call}\n'
         'gen = (callback for _ in [1])\ncallback = ordinary\n'
         'next(gen)()\nsend = ordinary', '', '', False),
    ]
    _assert_focus_cases(tmp, cases)


def test_generator_iteration_forms_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    local_cases = [
        ('for-invoked',
         f'send = ext_cmd\ngen = (lambda: {call} for _ in [1])\n'
         'for callback in gen:\n    callback()', '', '', True),
        ('for-not-invoked',
         f'send = ext_cmd\ngen = (lambda: {call} for _ in [1])\n'
         'for callback in gen:\n    pass', '', '', False),
        ('comprehension-invoked',
         f'send = ext_cmd\ngen = (lambda: {call} for _ in [1])\n'
         '[callback() for callback in gen]', '', '', True),
        ('comprehension-not-invoked',
         f'send = ext_cmd\ngen = (lambda: {call} for _ in [1])\n'
         '[callback for callback in gen]', '', '', False),
        ('dict-invoked',
         f'send = ext_cmd\ncallback = lambda: {call}\n'
         "gen = (('x', callback) for _ in [1])\n"
         "dict(gen)['x']()", '', '', True),
        ('dict-not-invoked',
         f'send = ext_cmd\ncallback = lambda: {call}\n'
         "gen = (('x', callback) for _ in [1])\n"
         'dict(gen)', '', '', False),
    ]
    _assert_focus_cases(tmp, local_cases)


def test_returned_generator_values_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('returned-invoked',
         f'send = ext_cmd\nreturn (lambda: {call} for _ in [1])', '',
         'gen = do_focus_tab(_args)', 'next(gen)()', True),
        ('returned-discarded',
         f'send = ext_cmd\nreturn (lambda: {call} for _ in [1])', '',
         'gen = do_focus_tab(_args)\ndel gen', '', False),
        ('returned-rebound-unsafe',
         f'send = ext_cmd\ncallback = lambda: ordinary()\n'
         f'gen = (callback for _ in [1])\ncallback = lambda: {call}\n'
         'return gen', '', 'gen = do_focus_tab(_args)', 'next(gen)()', True),
        ('returned-rebound-safe',
         f'send = ext_cmd\ncallback = lambda: {call}\n'
         'gen = (callback for _ in [1])\ncallback = lambda: ordinary()\n'
         'return gen', '', 'gen = do_focus_tab(_args)', 'next(gen)()', False),
    ]
    _assert_export_cases(tmp, cases)


def test_factory_returned_sets_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('set-invoked',
         f'send = ext_cmd\nreturn {{lambda: {call}}}', '',
         'box = do_focus_tab(_args)', 'next(iter(box))()', True),
        ('set-deleted',
         f'send = ext_cmd\nreturn {{lambda: {call}}}', '',
         'box = do_focus_tab(_args)\ndel box', '', False),
    ]
    _assert_export_cases(tmp, cases)


def test_deferred_storage_invalidation_matches_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    list_body = f'send = ext_cmd\nreturn [lambda: {call}]'
    holder_body = (
        f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
        f'holder.fn = lambda: {call}\nreturn holder')
    cases = [
        ('list-delete-retained', list_body, '',
         'box = do_focus_tab(_args)', 'box[0]()', True),
        ('list-delete-cleared', list_body, '',
         'box = do_focus_tab(_args)\ndel box[0]', '', False),
        ('list-overwrite-retained', list_body, '',
         'box = do_focus_tab(_args)', 'box[0]()', True),
        ('list-overwrite-cleared', list_body, '',
         'box = do_focus_tab(_args)\nbox[0] = ordinary', '', False),
        ('list-clear-retained', list_body, '',
         'box = do_focus_tab(_args)', 'box[0]()', True),
        ('list-clear-cleared', list_body, '',
         'box = do_focus_tab(_args)\nbox.clear()', '', False),
        ('attribute-delete-retained', holder_body, '',
         'holder = do_focus_tab(_args)', 'holder.fn()', True),
        ('attribute-delete-cleared', holder_body, '',
         'holder = do_focus_tab(_args)\ndel holder.fn', '', False),
        ('attribute-overwrite-retained', holder_body, '',
         'holder = do_focus_tab(_args)', 'holder.fn()', True),
        ('attribute-overwrite-cleared', holder_body, '',
         'holder = do_focus_tab(_args)\nholder.fn = ordinary', '', False),
    ]
    _assert_export_cases(tmp, cases)


def test_generator_materializer_shape_matches_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    prefix = (f'send = ext_cmd\ncallback = lambda: {call}\n'
              'gen = ([callback] for _ in [1])\n')
    cases = [
        ('outer-item-not-callable',
         prefix + 'try:\n    list(gen)[0]()\nexcept TypeError:\n    pass',
         '', '', False),
        ('nested-item-invoked',
         prefix + 'list(gen)[0][0]()', '', '', True),
    ]
    _assert_focus_cases(tmp, cases)


def test_consumer_runtime_shapes_match_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    prefix = f'send = ext_cmd\ncallback = lambda: {call}\n'
    cases = [
        ('max-element-invoked',
         prefix + 'gen = (callback for _ in [1])\nmax(gen)()\nsend = ordinary',
         '', '', True),
        ('min-element-invoked',
         prefix + 'gen = (callback for _ in [1])\nmin(gen)()\nsend = ordinary',
         '', '', True),
        ('max-element-not-invoked',
         prefix + 'gen = (callback for _ in [1])\nmax(gen)\nsend = ordinary',
         '', '', False),
        ('dict-invalid-arity',
         prefix + "gen = (('x', callback, 0) for _ in [1])\n"
         "try:\n    dict(gen)['x']()\nexcept ValueError:\n    pass\n"
         'send = ordinary', '', '', False),
        ('dict-key-iteration',
         prefix + "box = {'x': callback}\n"
         'for key in box:\n    try:\n        key()\n'
         '    except TypeError:\n        pass\ndel box\nsend = ordinary',
         '', '', False),
        ('dict-value-invoked',
         prefix + "box = {'x': callback}\nbox['x']()\nsend = ordinary",
         '', '', True),
    ]
    _assert_focus_cases(tmp, cases)


def test_nested_function_reachability_matches_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    cases = [
        ('returned-nested-invoked',
         f'send = ext_cmd\ndef make():\n    def call(): return {call}\n'
         '    return call\nholder = make()\nholder()\nsend = ordinary',
         '', '', True),
        ('returned-nested-discarded',
         f'send = ext_cmd\ndef make():\n    def call(): return {call}\n'
         '    return call\nholder = make()', '', '', False),
        ('inner-nested-discarded',
         f'send = ext_cmd\ndef make():\n    def call(): return {call}\n'
         'make()', '', '', False),
    ]
    _assert_focus_cases(tmp, cases)


def test_module_container_exposure_matches_runtime(tmp):
    call = "send('_focus', 'focus-tab', tab=int(_args.chrome_tab))"
    cases = [
        ('module-block-survives', 'return 0',
         f'send = ext_cmd\nif True:\n    call = lambda: {call}', '',
         'call()', True),
        ('module-block-deleted', 'return 0',
         f'send = ext_cmd\nif True:\n    call = lambda: {call}', 'del call',
         '', False),
        ('module-list-survives', 'return 0',
         f'send = ext_cmd\nbox = [lambda: {call}]', '',
         'box[0]()', True),
        ('module-list-deleted', 'return 0',
         f'send = ext_cmd\nbox = [lambda: {call}]', 'del box',
         '', False),
        ('module-instance-survives', 'return 0',
         f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
         f'holder.fn = lambda: {call}', '',
         'holder.fn()', True),
        ('module-instance-deleted', 'return 0',
         f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
         f'holder.fn = lambda: {call}', 'del holder',
         '', False),
    ]
    _assert_export_cases(tmp, cases)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='unprovable_')


if __name__ == '__main__':
    raise SystemExit(main())
