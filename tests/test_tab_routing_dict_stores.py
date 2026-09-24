#!/usr/bin/env python3
"""Callables stored into mappings report when invoked through the mapping."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute import py_tab_routing_violations  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402
from test_tab_routing_store_sweep import _RELAY, _flow  # noqa: E402

_CALL = "send('_focus', 'focus-tab', tab=int(args.chrome_tab))"


def _verdict(tmp, body):
    return _tracked_focus_verdict(tmp, body, counts=True)


def test_subscript_store_reports(tmp):
    body = ('send = ordinary\nbox = {}\n'
            f'box["k"] = lambda: {_CALL}\n'
            'send = ext_cmd\nreturn box["k"]()')
    assert _verdict(tmp, body) == (1, 1)


def test_copied_mapping_invocation_reports(tmp):
    body = ('send = ordinary\nbox = {}\n'
            f'box["k"] = lambda: {_CALL}\n'
            'send = ext_cmd\ncopy = dict(box)\nreturn copy["k"]()')
    assert _verdict(tmp, body) == (1, 1)


def test_copied_key_iteration_stays_clean(tmp):
    body = ('send = ordinary\nbox = {}\n'
            f'box["k"] = lambda: {_CALL}\n'
            'send = ext_cmd\ncopy = dict(box)\nlist(copy)\nlist(box)')
    assert _verdict(tmp, body) == (0, 0)


def test_clean_store_invocation_stays_clean(tmp):
    body = ('send = ordinary\nbox = {}\n'
            'box["k"] = lambda: ordinary()\n'
            'send = ext_cmd\nreturn box["k"]()')
    assert _verdict(tmp, body) == (0, 0)


def test_deleted_holder_copy_invocation_reports(tmp):
    body = ('send = ordinary\nbox = {}\n'
            f'box["k"] = lambda: {_CALL}\n'
            'send = ext_cmd\ncopy = dict(box)\ndel box\nreturn copy["k"]()')
    assert _verdict(tmp, body) == (1, 1)


# The setdefault read of a key the evaluator cannot fold. The runtime
# resolves the key and returns the stored value, so a guard that named only
# the default reads clean on a path that reaches the sender. The rows vary
# one property each — the two spellings differ only in the key expression,
# the two orders only in the relay's position — so neither pair is
# satisfiable by a spelling-only or a position-only rule, and the relay
# sits second because a one-item store cannot tell "every item" from "the
# first". `relay-last` is the pin; `relay-first` is a variance control and
# passes under a first-item-only rule by design. The twins hold the same
# shape with clean data and must not move.
_SETDEFAULT_UNRESOLVED = [
    ('setdefault-concat-relay-last', _flow(
        _RELAY, 'd = {"a": ordinary, "k": relay()}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    ('setdefault-concat-relay-first', _flow(
        _RELAY, 'd = {"k": relay(), "a": ordinary}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    ('setdefault-fstring-relay-last', _flow(
        _RELAY, 'd = {"a": ordinary, "k": relay()}; key = f"k"',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (1, 1)),
    ('setdefault-concat-vacant', _flow(
        _RELAY, 'd = {}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (0, 0)),
    ('setdefault-concat-miss', _flow(
        _RELAY, 'd = {"a": ordinary}; key = "b" + ""',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (0, 0)),
    ('setdefault-fstring-vacant', _flow(
        _RELAY, 'd = {}; key = f"k"',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (0, 0)),
    ('setdefault-fstring-miss', _flow(
        _RELAY, 'd = {"a": ordinary}; key = f"b"',
        'x = d.setdefault(key, ordinary)', invoke='x()'), (0, 0)),
    ('setdefault-concat-no-default', _flow(
        _RELAY, 'd = {"a": ordinary}; key = "a" + ""',
        'x = d.setdefault(key)', invoke='x()'), (0, 0)),
    ('setdefault-unreadable-owner', _flow(
        _RELAY, 'pool = [{"k": relay()}]\ndef build():\n    return pool[0]',
        'd = build()\nkey = "k" + ""\nx = d.setdefault(key, ordinary)',
        invoke='x()'), (1, 1)),
    ('setdefault-resolvable-miss', _flow(
        _RELAY, 'd = {"a": relay()}', 'x = d.setdefault("k", ordinary)',
        invoke='x()'), (0, 0)),
    # The key spelled in place, so the probe reads the key EXPRESSION
    # rather than a name's absent literal. Clean twins over the shape a
    # guard reads as an unevaluable key; `_SETDEFAULT_ARMS` carries the
    # rows that tell that shape apart from an unhashable one.
    ('setdefault-concat-direct-vacant', _flow(
        _RELAY, 'd = {}', 'x = d.setdefault("k" + "", ordinary)',
        invoke='x()'), (0, 0)),
    ('setdefault-fstring-direct-vacant', _flow(
        _RELAY, 'd = {}', 'x = d.setdefault(f"k", ordinary)',
        invoke='x()'), (0, 0)),
]


def test_setdefault_unresolved_key_names_every_stored_item(tmp):
    bad = []
    for label, body, expected in _SETDEFAULT_UNRESOLVED:
        actual = _verdict(tmp, body)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


# The same value read back through a name: a verdict that stops at the store
# is a false green, because the value the runtime returns is the value the
# call reaches and a frame between them must not lose it. Each unresolved
# spelling travels as far as the other, and each twin covers the same travel
# with clean data.
_SETDEFAULT_CALL_THROUGH = [
    ('call-through-concat-statement', _flow(
        _RELAY, 'd = {"a": ordinary, "k": relay()}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)\nd.get("a", ordinary)',
        invoke='x()'), (1, 1)),
    ('call-through-concat-nested', _flow(
        _RELAY, 'd = {"a": ordinary, "k": relay()}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)\ndef inner():\n    return x()',
        invoke='inner()'), (1, 1)),
    ('call-through-concat-helper', _flow(
        _RELAY, 'd = {"a": ordinary, "k": relay()}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)\ndef inner():\n    return x',
        invoke='inner()()'), (1, 1)),
    ('call-through-fstring-statement', _flow(
        _RELAY, 'd = {"a": ordinary, "k": relay()}; key = f"k"',
        'x = d.setdefault(key, ordinary)\nd.get("a", ordinary)',
        invoke='x()'), (1, 1)),
    ('call-through-fstring-nested', _flow(
        _RELAY, 'd = {"a": ordinary, "k": relay()}; key = f"k"',
        'x = d.setdefault(key, ordinary)\ndef inner():\n    return x()',
        invoke='inner()'), (1, 1)),
    ('call-through-fstring-helper', _flow(
        _RELAY, 'd = {"a": ordinary, "k": relay()}; key = f"k"',
        'x = d.setdefault(key, ordinary)\ndef inner():\n    return x',
        invoke='inner()()'), (1, 1)),
    ('call-through-twin-concat-statement', _flow(
        _RELAY, 'd = {"a": ordinary}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)\nd.get("a", ordinary)',
        invoke='x()'), (0, 0)),
    ('call-through-twin-concat-nested', _flow(
        _RELAY, 'd = {"a": ordinary}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)\ndef inner():\n    return x()',
        invoke='inner()'), (0, 0)),
    ('call-through-twin-concat-helper', _flow(
        _RELAY, 'd = {"a": ordinary}; key = "k" + ""',
        'x = d.setdefault(key, ordinary)\ndef inner():\n    return x',
        invoke='inner()()'), (0, 0)),
    ('call-through-twin-fstring-statement', _flow(
        _RELAY, 'd = {"a": ordinary}; key = f"k"',
        'x = d.setdefault(key, ordinary)\nd.get("a", ordinary)',
        invoke='x()'), (0, 0)),
    ('call-through-twin-fstring-nested', _flow(
        _RELAY, 'd = {"a": ordinary}; key = f"k"',
        'x = d.setdefault(key, ordinary)\ndef inner():\n    return x()',
        invoke='inner()'), (0, 0)),
    ('call-through-twin-fstring-helper', _flow(
        _RELAY, 'd = {"a": ordinary}; key = f"k"',
        'x = d.setdefault(key, ordinary)\ndef inner():\n    return x',
        invoke='inner()()'), (0, 0)),
]


def test_setdefault_unresolved_value_survives_to_the_call(tmp):
    bad = []
    for label, body, expected in _SETDEFAULT_CALL_THROUGH:
        actual = _verdict(tmp, body)
        if actual != expected:
            bad.append((label, actual, expected))
    assert not bad, bad


# One arm of the same lookup per row, with a default the guard models, so a
# row is refused by that arm alone. The owner the model cannot read and the
# container that is not a dict both keep the unprovable sender; the dict the
# key misses and the dict the key hits are clean, so a guard that reported
# there would be the over-report, and the unhashable key is the one input
# the runtime rejects before it returns anything. The last three rows spell
# the key in the call rather than through a name, because that is the only
# shape that tells an unevaluable key EXPRESSION from a literal the runtime
# cannot hash: both resolve to no literal here, and only the second is a
# report. The value sits in the `send` position, where the guard reads it as
# the sender of the routed call below.
_SETDEFAULT_ARMS = [
    ('arm-owner-unreadable', 'd = pool[0]\nkey = "k" + ""',
     'd.setdefault(key, ordinary)', True),
    ('arm-non-dict-unresolved', 'd = [ordinary]\nkey = "k" + ""',
     'd.setdefault(key, ordinary)', True),
    ('arm-non-dict-literal', 'd = [ordinary]\nkey = "a"',
     'd.setdefault(key, ordinary)', False),
    ('arm-dict-unresolved-miss', 'd = {"a": ordinary}\nkey = "k" + ""',
     'd.setdefault(key, ordinary)', False),
    ('arm-dict-unhashable', 'd = {"a": ordinary}\nkey = [1]',
     'd.setdefault(key, ordinary)', True),
    ('arm-direct-concat-key', 'd = {}',
     'd.setdefault("k" + "", ordinary)', False),
    ('arm-direct-fstring-key', 'd = {}',
     'd.setdefault(f"k", ordinary)', False),
    ('arm-direct-fstring-occupied', 'd = {"a": ordinary}',
     'd.setdefault(f"k", ordinary)', False),
]


def test_each_setdefault_arm_has_a_discriminating_probe(tmp):
    wrong = []
    for label, body, call, expected in _SETDEFAULT_ARMS:
        setup = ''.join(f'    {line}\n' for line in body.splitlines())
        source = Path(tmp) / f'{label}.py'
        source.write_text(
            'def ordinary(*a, **k):\n'
            '    return 0\n'
            f'def probe():\n{setup}'
            f'    send = {call}\n'
            '    return send("_focus", "focus-tab", tab=5)\n',
            encoding='utf-8')
        actual = bool(py_tab_routing_violations(source, source.name))
        if actual != expected:
            wrong.append((label, actual, expected))
    assert not wrong, wrong


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dictstores_')


if __name__ == '__main__':
    raise SystemExit(main())
