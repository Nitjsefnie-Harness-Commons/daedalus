#!/usr/bin/env python3
"""Generator second-consumption controls for live expression routing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402


def test_generator_second_consumption_re_resolves_yielded_name(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    safe = (f'send = ext_cmd\ncallback = lambda: {call}\n'
            'gen = (callback for _ in [1, 2])\nfirst = next(gen)\n'
            'holder = first\ncallback = ordinary\nsecond = next(gen)\n'
            'del holder\nreturn second()')
    unsafe = ('send = ext_cmd\ncallback = lambda: ordinary()\n'
              'gen = (callback for _ in [1, 2])\nfirst = next(gen)\n'
              'holder = first\ncallback = lambda: ' + call + '\n'
              'second = next(gen)\ndel holder\nreturn second()')
    assert _tracked_focus_verdict(tmp, safe, counts=True) == (0, 0)
    assert _tracked_focus_verdict(tmp, unsafe, counts=True) == (1, 1)


def test_generator_second_subscript_consumption_safe_to_unsafe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'send = ext_cmd\nbox = [lambda: ordinary()]\n'
            f'gen = (box[0] for _ in [1, 2])\nfirst = next(gen)\n'
            f'box[0] = lambda: {call}\nsecond = next(gen)\nsecond()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (1, 1)


def test_generator_second_subscript_consumption_unsafe_to_safe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'send = ext_cmd\nbox = [lambda: {call}]\n'
            f'gen = (box[0] for _ in [1, 2])\nfirst = next(gen)\n'
            'box[0] = ordinary\nsecond = next(gen)\nsecond()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (0, 0)


def test_generator_second_attribute_consumption_safe_to_unsafe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
            f'holder.fn = lambda: ordinary()\n'
            f'gen = (holder.fn for _ in [1, 2])\nfirst = next(gen)\n'
            f'holder.fn = lambda: {call}\nsecond = next(gen)\nsecond()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (1, 1)


def test_generator_second_attribute_consumption_unsafe_to_safe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
            f'holder.fn = lambda: {call}\n'
            f'gen = (holder.fn for _ in [1, 2])\nfirst = next(gen)\n'
            'holder.fn = ordinary\nsecond = next(gen)\nsecond()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (0, 0)


def test_generator_ifexp_subscript_consumption_safe_to_unsafe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'send = ext_cmd\nbox = [lambda: ordinary()]\n'
            'gen = ((box[0] if True else ordinary) for _ in [1, 2])\n'
            'first = next(gen)\n'
            f'box[0] = lambda: {call}\n'
            'second = next(gen)\nsecond()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (1, 1)


def test_generator_ifexp_attribute_consumption_unsafe_to_safe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'class Holder: pass\nholder = Holder()\nsend = ext_cmd\n'
            f'holder.fn = lambda: {call}\n'
            'gen = ((holder.fn if True else ordinary) for _ in [1, 2])\n'
            'first = next(gen)\n'
            'holder.fn = ordinary\nsecond = next(gen)\nsecond()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (0, 0)


def test_generator_ifexp_list_subscript_consumption_safe_to_unsafe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'send = ext_cmd\nbox = [lambda: ordinary()]\n'
            'gen = (([box[0]] if True else [ordinary]) for _ in [1, 2])\n'
            'first = next(gen)\n'
            f'box[0] = lambda: {call}\n'
            'second = next(gen)\nsecond[0]()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (1, 1)


def test_generator_ifexp_yield_cache_runtime_unsafe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'send = ext_cmd\ncallback = lambda: {call}\n'
            'gen = ((callback if args.flag else ordinary) for _ in [1])\n'
            'next(gen)()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (1, 1)


def test_generator_ifexp_yield_cache_runtime_safe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = ('send = ext_cmd\ncallback = ordinary\n'
            'gen = ((callback if args.flag else lambda: ' + call + ') '
            'for _ in [1])\nnext(gen)()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (0, 1)


def test_generator_boolop_yield_cache_runtime_unsafe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'send = ext_cmd\ncallback = lambda: {call}\n'
            'gen = ((callback or ordinary) for _ in [1])\nnext(gen)()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (1, 1)


def test_generator_boolop_yield_cache_runtime_safe(tmp):
    call = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"
    body = (f'send = ext_cmd\ncallback = lambda: {call}\n'
            'gen = ((callback and ordinary) for _ in [1])\nnext(gen)()')
    assert _tracked_focus_verdict(tmp, body, counts=True) == (0, 1)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='generator_second_consumption_')


if __name__ == '__main__':
    raise SystemExit(main())
