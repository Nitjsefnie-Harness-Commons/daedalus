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
_PRE = (
    "send = ordinary\n"
    "def maker():\n"
    "    return lambda: send('_focus', 'focus-tab', "
    "tab=args.chrome_tab)\n"
    "def relay(): return maker()\n")
_PRE_CLEAN = (
    "send = ordinary\n"
    "def maker():\n"
    "    return lambda: ordinary()\n"
    "def relay(): return maker()\n")


def _verdict(tmp, body):
    return _tracked_focus_verdict(tmp, body, counts=True)


def _body(store, invoke, prefix=_PRE):
    return prefix + store + "\nsend = ext_cmd\nreturn " + invoke


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
            'send = ext_cmd\ncopy = dict(box)\ndel box\n'
            'return copy["k"]()')
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


_UNKNOWN_STARS = {
    'opaque': ('x = [*args.values, relay()]', 'x[2]()'),
    'dynamic_dict': ('x = [*{str(args.chrome_tab): 1}, relay()]', 'x[1]()'),
    'dynamic_and_known_dict': (
        'x = [*{str(args.chrome_tab): 1, "b": 2}, relay()]', 'x[2]()'),
    'opaque_tuple': ('x = (*args.values, relay())', 'x[2]()'),
    'known_after_opaque': ('x = [*args.values, *[relay()]]', 'x[2]()'),
    'stored_dict': ('d = {}\nd[str(args.chrome_tab)] = 1\n'
                    'x = [*{**d}, relay()]', 'x[1]()'),
    'stored_dict_tuple': ('d = {}\nd[str(args.chrome_tab)] = 1\n'
                          'x = (*{**d}, relay())', 'x[1]()'),
    'stored_dict_direct': ('d = {}\nd[str(args.chrome_tab)] = 1\n'
                           'x = [*d, relay()]', 'x[1]()'),
}


def _star(tmp, name, prefix=_PRE):
    store, invoke = _UNKNOWN_STARS[name]
    return _verdict(tmp, _body(store, invoke, prefix=prefix))


def test_after_unknown_star_opaque_reports(tmp):
    assert _star(tmp, 'opaque') == (1, 1)


def test_after_unknown_star_opaque_stays_clean(tmp):
    assert _star(tmp, 'opaque', _PRE_CLEAN) == (0, 0)


def test_after_unknown_star_dynamic_dict_reports(tmp):
    assert _star(tmp, 'dynamic_dict') == (1, 1)


def test_after_unknown_star_dynamic_dict_stays_clean(tmp):
    assert _star(tmp, 'dynamic_dict', _PRE_CLEAN) == (0, 0)


def test_after_unknown_star_dynamic_and_known_dict_reports(tmp):
    assert _star(tmp, 'dynamic_and_known_dict') == (1, 1)


def test_after_unknown_star_dynamic_and_known_dict_stays_clean(tmp):
    assert _star(tmp, 'dynamic_and_known_dict', _PRE_CLEAN) == (0, 0)


def test_after_unknown_star_opaque_tuple_reports(tmp):
    assert _star(tmp, 'opaque_tuple') == (1, 1)


def test_after_unknown_star_opaque_tuple_stays_clean(tmp):
    assert _star(tmp, 'opaque_tuple', _PRE_CLEAN) == (0, 0)


def test_after_unknown_star_known_after_opaque_reports(tmp):
    assert _star(tmp, 'known_after_opaque') == (1, 1)


def test_after_unknown_star_known_after_opaque_stays_clean(tmp):
    assert _star(tmp, 'known_after_opaque', _PRE_CLEAN) == (0, 0)


def test_after_unknown_star_stored_dict_reports(tmp):
    assert _star(tmp, 'stored_dict') == (1, 1)


def test_after_unknown_star_stored_dict_stays_clean(tmp):
    assert _star(tmp, 'stored_dict', _PRE_CLEAN) == (0, 0)


def test_after_unknown_star_stored_dict_tuple_reports(tmp):
    assert _star(tmp, 'stored_dict_tuple') == (1, 1)


def test_after_unknown_star_stored_dict_tuple_stays_clean(tmp):
    assert _star(tmp, 'stored_dict_tuple', _PRE_CLEAN) == (0, 0)


def test_after_unknown_star_stored_dict_direct_reports(tmp):
    assert _star(tmp, 'stored_dict_direct') == (1, 1)


def test_after_unknown_star_stored_dict_direct_stays_clean(tmp):
    assert _star(tmp, 'stored_dict_direct', _PRE_CLEAN) == (0, 0)


_KNOWN_STAR = 'x = [*[relay(), ordinary], ordinary]'


def test_display_known_star_indexes_sender_exactly(tmp):
    assert _verdict(tmp, _body(_KNOWN_STAR, 'x[0]()')) == (1, 1)


def test_display_known_star_indexes_ordinary_exactly(tmp):
    assert _verdict(tmp, _body(_KNOWN_STAR, 'x[1]()')) == (0, 0)


def test_display_known_star_stays_clean(tmp):
    assert _verdict(tmp, _body(
        _KNOWN_STAR, 'x[0]()', prefix=_PRE_CLEAN)) == (0, 0)


_IN_PLACE_MERGES = {
    'dynamic': ('x = {}\nx[str(args.chrome_tab)] = relay()\n'
                'x |= {str(args.flag): ordinary}', 'x["323"]()'),
    'opaque': ('x = {"k": relay()}\nx |= vars(args)', 'x["k"]()'),
    'known': ('x = {"k": relay()}\nx |= {"j": ordinary}', 'x["k"]()'),
}


def _merge(tmp, name, prefix=_PRE):
    store, invoke = _IN_PLACE_MERGES[name]
    return _verdict(tmp, _body(store, invoke, prefix=prefix))


def test_in_place_merge_dynamic_reports(tmp):
    assert _merge(tmp, 'dynamic') == (1, 1)


def test_in_place_merge_dynamic_stays_clean(tmp):
    assert _merge(tmp, 'dynamic', _PRE_CLEAN) == (0, 0)


def test_in_place_merge_opaque_reports(tmp):
    assert _merge(tmp, 'opaque') == (1, 1)


def test_in_place_merge_opaque_stays_clean(tmp):
    assert _merge(tmp, 'opaque', _PRE_CLEAN) == (0, 0)


def test_in_place_merge_known_reports(tmp):
    assert _merge(tmp, 'known') == (1, 1)


def test_in_place_merge_known_stays_clean(tmp):
    assert _merge(tmp, 'known', _PRE_CLEAN) == (0, 0)


_QUIET = 'def quiet(): return lambda: ordinary()\nd = {}\n'
_RELAY_STORE = 'd[str(args.chrome_tab)] = relay()\n'
_QUIET_STORE = 'd[str(args.flag)] = quiet()\n'


def test_dynamic_store_then_quiet_store_reports(tmp):
    assert _verdict(tmp, _body(
        _QUIET + _RELAY_STORE + _QUIET_STORE, 'd["323"]()')) == (1, 1)


def test_dynamic_store_then_quiet_store_stays_clean(tmp):
    assert _verdict(tmp, _body(
        _QUIET + _RELAY_STORE + _QUIET_STORE, 'd["323"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


def test_quiet_store_then_dynamic_store_reports(tmp):
    assert _verdict(tmp, _body(
        _QUIET + _QUIET_STORE + _RELAY_STORE, 'd["323"]()')) == (1, 1)


def test_quiet_store_then_dynamic_store_stays_clean(tmp):
    assert _verdict(tmp, _body(
        _QUIET + _QUIET_STORE + _RELAY_STORE, 'd["323"]()',
        prefix=_PRE_CLEAN)) == (0, 0)


def _unknown_call_store(name, quiet_first):
    unknown = ('d[str(args.chrome_tab)] = '
               f'copy.copy(globals()["{name[:4]}" + "{name[4:]}"])\n')
    stores = _QUIET_STORE + unknown if quiet_first else unknown + _QUIET_STORE
    call = 'return d["323"]("_focus", "focus-tab", tab=args.chrome_tab)'
    return 'import copy\n' + _QUIET + stores + call


def test_unknown_call_store_after_quiet_reports(tmp):
    assert _verdict(tmp, _unknown_call_store('ext_cmd', True)) == (1, 1)


def test_unknown_call_store_after_quiet_is_unprovable(tmp):
    assert _verdict(tmp, _unknown_call_store('ordinary', True)) == (0, 1)


def test_unknown_call_store_before_quiet_reports(tmp):
    assert _verdict(tmp, _unknown_call_store('ext_cmd', False)) == (1, 1)


def test_unknown_call_store_before_quiet_is_unprovable(tmp):
    assert _verdict(tmp, _unknown_call_store('ordinary', False)) == (0, 1)


_TAB_CALL = '("_focus", "focus-tab", tab=args.chrome_tab)'


def test_starred_dict_values_stay_out_of_list(tmp):
    assert _verdict(tmp, _body(
        'x = [*{str(args.chrome_tab): relay()}, ordinary]',
        'x[1]' + _TAB_CALL)) == (0, 0)


def test_starred_dict_values_stay_out_of_tuple(tmp):
    assert _verdict(tmp, _body(
        'x = (*{str(args.chrome_tab): relay()}, ordinary)',
        'x[1]' + _TAB_CALL)) == (0, 0)


def test_relay_after_starred_dict_reports(tmp):
    assert _verdict(tmp, _body(
        'x = [*{str(args.chrome_tab): relay()}, relay()]', 'x[1]()')) \
        == (1, 1)


def test_relay_after_starred_dict_stays_clean(tmp):
    assert _verdict(tmp, _body(
        'x = [*{str(args.chrome_tab): relay()}, relay()]', 'x[1]()',
        prefix=_PRE_CLEAN)) == (0, 0)


_MUTATED_COUNTS = {
    'computed_del': ('d = {"a": 1}\ndel d[str(args.flag) and "a"]\n'
                     'x = [*d, relay()]', 'x[0]()'),
    'computed_pop': ('d = {"a": 1}\nd.pop(str(args.flag) and "a")\n'
                     'x = [*d, relay()]', 'x[0]()'),
    'update_uncounted': ('d = {}\nd[str(args.chrome_tab)] = 1\ne = {}\n'
                         'e.update(d)\nx = [*e, relay()]', 'x[1]()'),
    'merge_uncounted': ('d = {}\nd[str(args.chrome_tab)] = 1\ne = {}\n'
                        'e |= d\nx = [*e, relay()]', 'x[1]()'),
    'constant_store': ('d = {}\nd["k"] = 1\nx = [*d, relay()]', 'x[1]()'),
    'constant_del': ('d = {"a": 1}\ndel d["a"]\nx = [*d, relay()]',
                     'x[0]()'),
    'constant_pop': ('d = {"a": 1}\nd.pop("a")\nx = [*d, relay()]',
                     'x[0]()'),
    'branch_store': ('d = {}\nif args.flag:\n    d["k"] = 1\n'
                     'x = [*d, relay()]', 'x[1]()'),
}


def _counted(tmp, name, prefix=_PRE):
    store, invoke = _MUTATED_COUNTS[name]
    return _verdict(tmp, _body(store, invoke, prefix=prefix))


def test_star_after_computed_del_reports(tmp):
    assert _counted(tmp, 'computed_del') == (1, 1)


def test_star_after_computed_del_stays_clean(tmp):
    assert _counted(tmp, 'computed_del', _PRE_CLEAN) == (0, 0)


def test_star_after_computed_pop_reports(tmp):
    assert _counted(tmp, 'computed_pop') == (1, 1)


def test_star_after_computed_pop_stays_clean(tmp):
    assert _counted(tmp, 'computed_pop', _PRE_CLEAN) == (0, 0)


def test_star_after_update_uncounted_reports(tmp):
    assert _counted(tmp, 'update_uncounted') == (1, 1)


def test_star_after_update_uncounted_stays_clean(tmp):
    assert _counted(tmp, 'update_uncounted', _PRE_CLEAN) == (0, 0)


def test_star_after_merge_uncounted_reports(tmp):
    assert _counted(tmp, 'merge_uncounted') == (1, 1)


def test_star_after_merge_uncounted_stays_clean(tmp):
    assert _counted(tmp, 'merge_uncounted', _PRE_CLEAN) == (0, 0)


def test_star_after_constant_store_reports(tmp):
    assert _counted(tmp, 'constant_store') == (1, 1)


def test_star_after_constant_store_stays_clean(tmp):
    assert _counted(tmp, 'constant_store', _PRE_CLEAN) == (0, 0)


def test_star_after_constant_del_reports(tmp):
    assert _counted(tmp, 'constant_del') == (1, 1)


def test_star_after_constant_del_stays_clean(tmp):
    assert _counted(tmp, 'constant_del', _PRE_CLEAN) == (0, 0)


def test_star_after_constant_pop_reports(tmp):
    assert _counted(tmp, 'constant_pop') == (1, 1)


def test_star_after_constant_pop_stays_clean(tmp):
    assert _counted(tmp, 'constant_pop', _PRE_CLEAN) == (0, 0)


def test_star_after_branch_store_reports(tmp):
    assert _counted(tmp, 'branch_store') == (1, 1)


def test_star_after_branch_store_stays_clean(tmp):
    assert _counted(tmp, 'branch_store', _PRE_CLEAN) == (0, 0)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='dictstores_')


if __name__ == '__main__':
    raise SystemExit(main())
