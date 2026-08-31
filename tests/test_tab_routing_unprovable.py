#!/usr/bin/env python3
"""An unprovable sender alias is reported through an opaque `**spread`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute import py_tab_routing_violations  # noqa: E402
from test_tab_routing import _assert_focus_cases  # noqa: E402


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


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='unprovable_')


if __name__ == '__main__':
    raise SystemExit(main())
