#!/usr/bin/env python3
"""Keep yielded sender aliases through generator consumption shapes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402


_CALL = "'_focus', 'focus-tab', tab=int(args.chrome_tab)"
_CONSUMERS = [
    ('list', f'list(gen)[0]({_CALL})'),
    ('tuple', f'tuple(gen)[0]({_CALL})'),
    ('sorted', f'sorted(gen)[0]({_CALL})'),
    ('for', f'for callback in gen:\n    callback({_CALL})'),
    ('comprehension', f'[callback({_CALL}) for callback in gen]'),
    ('nested-next', f'next(callback for callback in gen)({_CALL})'),
]


def _consumer_cases():
    for label, consume in _CONSUMERS:
        direct = f'send = ext_cmd\ngen = (send for _ in [1])\n{consume}'
        divergent = ('send = ordinary\nif not args.flag:\n    send = ext_cmd\n'
                     f'gen = (send for _ in [1])\n{consume}')
        yield label, direct, (1, 1)
        yield f'{label}-divergent', divergent, (0, 1)


def test_yielded_sender_survives_consumers(tmp):
    observed = [(label, _tracked_focus_verdict(tmp, body, counts=True))
                for label, body, _ in _consumer_cases()]
    expected = [(label, result) for label, _, result in _consumer_cases()]
    assert observed == expected, observed


_BRANCHES = [
    ('if-ext-first', 'ext_cmd if args.flag else (lambda *a, **k: 0)', 1),
    ('if-lambda-first', '(lambda *a, **k: 0) if args.flag else ext_cmd', 0),
    ('and-ext-left', 'ext_cmd and (lambda *a, **k: 0)', 0),
    ('and-ext-right', '(lambda *a, **k: 0) and ext_cmd', 1),
    ('or-ext-left', 'ext_cmd or (lambda *a, **k: 0)', 1),
    ('or-ext-right', '(lambda *a, **k: 0) or ext_cmd', 0),
]


def test_yielded_sender_survives_expression_merges(tmp):
    observed = []
    for label, expression, calls in _BRANCHES:
        body = (f'gen = ({expression} for _ in [1])\n'
                f'next(gen)({_CALL})')
        observed.append((label, _tracked_focus_verdict(
            tmp, body, counts=True)))
        assert observed[-1][1] == (calls, 1), observed[-1]


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='yielded_sender_')


if __name__ == '__main__':
    raise SystemExit(main())
