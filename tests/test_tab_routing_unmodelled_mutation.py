#!/usr/bin/env python3
"""A tracked container mutated in place by a path the model does not follow
fails closed: the mutation drops the positions, keys and length the model
recorded, so a later read answers with every value the container could hold
rather than with one from before the mutation.

The rule is one rule, not one per mutator. Every mutation row is a spelling
-- a bare statement, an expression, an unbound method, getattr, an alias or
named-expression receiver, a nested body -- of an in-place mutation of a list
or a dict, and each is paired with a clean twin that swaps the carried relay
for a carried quiet, so the row proves the mutation was why the read reported.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402

_PRE = (
    'send = ordinary\n'
    'args = _args\n'
    'def maker():\n'
    '    return lambda: send("_focus", "focus-tab", '
    'tab=args.chrome_tab)\n'
    'def relay(): return maker()\n'
    'def quiet(): return lambda: ordinary()\n')
_SEND = '\nsend = ext_cmd\nreturn '

_LIST = 'x = [ordinary, relay()]'
_ONE = 'x = [ordinary]'
_PAIR = 'x = [ordinary, ordinary]'
_STAR = 'x = [ordinary, relay(), *args.values]'
_DICT = 'd = {"a": 1, "b": 2}'

# (name, store, mutate, after, read). `after` runs once the mutation has, and
# `read` is the invocation the verdict is taken at.
_MUTATIONS = [
    ('bare_pop', _LIST, 'x.pop(0)', '', 'x[0]()'),
    ('assigned_pop', _LIST, 'y = x.pop(0)', '', 'x[0]()'),
    ('unbound_pop', _LIST, 'list.pop(x, 0)', '', 'x[0]()'),
    ('getattr_pop', _LIST, 'getattr(x, "pop")(0)', '', 'x[0]()'),
    ('getattribute_pop', _LIST, 'x.__getattribute__("pop")(0)', '',
     'x[0]()'),
    ('dynamic_getattr', _LIST, 'name = "pop"\ngetattr(x, name)(0)', '',
     'x[0]()'),
    ('call_argument_pop', _LIST, 'ordinary(x.pop(0))', '', 'x[0]()'),
    ('named_expression_receiver', _LIST, '(y := x).pop(0)', '', 'x[0]()'),
    ('alias_receiver', _LIST + '\ny = x', 'y.pop(0)', '', 'x[0]()'),
    ('lambda_body_pop', _LIST, 'f = lambda: x.pop(0)\nf()', '', 'x[0]()'),
    ('comprehension_pop', _LIST, 'go = [x.pop(0) for _ in [1]]', '', 'x[0]()'),
    ('if_body_pop', _LIST, 'if args.flag:\n    x.pop(0)', '', 'x[0]()'),
    ('for_body_pop', _LIST, 'for _ in [1]:\n    x.pop(0)', '', 'x[0]()'),
    ('try_body_pop', _LIST, 'try:\n    x.pop(0)\nexcept IndexError: pass',
     '', 'x[0]()'),
    ('nested_function_pop', _LIST, 'def inner():\n    return x.pop(0)\n'
     'inner()', '', 'x[0]()'),
    ('reverse', _LIST, 'x.reverse()', '', 'x[0]()'),
    ('sort', _LIST, 'x.sort(key=lambda f: 1 if f is ordinary else 0)', '',
     'x[0]()'),
    ('insert', _LIST, 'x.insert(0, relay())', '', 'x[0]()'),
    ('setitem_dunder', _LIST, 'x.__setitem__(0, relay())', '', 'x[0]()'),
    ('delitem_dunder', _LIST, 'x.__delitem__(0)', '', 'x[0]()'),
    ('remove', _STAR, 'y = x.remove(ordinary)', '', 'x[0]()'),
    ('append', _ONE, 'x.append(relay())', '', 'x[1]()'),
    ('extend', _ONE, 'x.extend([relay()])', '', 'x[1]()'),
    ('augmented_add', _ONE, 'x += [relay()]', '', 'x[1]()'),
    ('subscript_delete', _LIST, 'del x[0]', '', 'x[0]()'),
    ('subscript_slice_store', _LIST, 'x[0:1] = [relay()]', '', 'x[0]()'),
    ('annotated_slice_store', _LIST, 'x[0:1]: list = [relay()]', '',
     'x[0]()'),
    ('tuple_target_slice_store', _LIST, 'x[0:1], y = [relay()], 0', '',
     'x[0]()'),
    ('nested_subscript_delete', 'd = {"k": [ordinary, relay()]}',
     'del d["k"][0]', '', 'd["k"][0]()'),
    ('popitem', _DICT, 'd.popitem()', 'x = [*d, relay()]', 'x[1]()'),
    ('mapping_delitem_dunder', _DICT, 'd.__delitem__("a")',
     'x = [*d, relay()]', 'x[1]()'),
    ('mapping_unbound_pop', _DICT, 'dict.pop(d, "a")',
     'x = [*d, relay()]', 'x[1]()'),
    ('mapping_assigned_popitem', _DICT, 'y = d.popitem()',
     'x = [*d, relay()]', 'x[1]()'),
    ('mapping_getattr_popitem', _DICT, 'getattr(d, "popitem")()',
     'x = [*d, relay()]', 'x[1]()'),
    ('mapping_named_expression_receiver', _DICT, '(y := d).popitem()',
     'x = [*d, relay()]', 'x[1]()'),
    ('mapping_alias_receiver', _DICT + '\ny = d', 'y.popitem()',
     'x = [*d, relay()]', 'x[1]()'),
    ('mapping_lambda_body', _DICT, 'f = lambda: d.popitem()\nf()',
     'x = [*d, relay()]', 'x[1]()'),
    ('mapping_nested_function', _DICT, 'def inner():\n    d.popitem()\n'
     'inner()', 'x = [*d, relay()]', 'x[1]()'),
    ('mapping_for_body', _DICT, 'for _ in [1]:\n    d.popitem()',
     'x = [*d, relay()]', 'x[1]()'),
    ('mapping_try_body', _DICT, 'try:\n    d.popitem()\nexcept KeyError:'
     ' pass', 'x = [*d, relay()]', 'x[1]()'),
]

# Mutators the model already follows. The general invalidation must leave
# their precise result alone.
_PRECISE = [
    ('mapping_pop', 'd = {"a": 1, "b": relay()}', 'd.pop("a")', '',
     'd["b"]()'),
    ('mapping_subscript_delete', 'd = {"a": 1, "b": relay()}', 'del d["a"]',
     '', 'd["b"]()'),
    ('mapping_update', 'd = {"a": 1}', 'd.update({"b": relay()})', '',
     'd["b"]()'),
    ('mapping_setdefault', 'd = {"a": 1}', 'd.setdefault("b", relay())', '',
     'd["b"]()'),
    ('mapping_or', 'd = {"a": 1}', 'd |= {"b": relay()}', '', 'd["b"]()'),
    ('mapping_subscript_store', 'd = {"a": 1}', 'd["b"] = relay()', '',
     'd["b"]()'),
    ('mapping_setdefault_ordinary', 'd = {"a": 1, "b": relay()}',
     'd.setdefault("c", ordinary())', '', 'd["b"]()'),
]

# Recorded facts that a mutation does not move: a method that only reads, and
# a store at one position. If the rule fired on any of these, each would
# report a relay it cannot reach.
_READING = [
    ('sequence_count', _LIST, 'x.count(ordinary)', '', 'x[0]()', (0, 0)),
    ('sequence_index', _LIST, 'x.index(ordinary)', '', 'x[0]()', (0, 0)),
    ('sequence_copy', _LIST, 'x.copy()', '', 'x[0]()', (0, 0)),
    ('sequence_slice', _LIST, 'x[0:2]', '', 'x[0]()', (0, 0)),
    ('sequence_contains', 'x = [quiet(), relay()]', 'ordinary in x', '',
     'x[0]()', (0, 0)),
    ('mapping_get', 'd = {"a": quiet()}', 'd.get("a")', '', 'd["a"]()',
     (0, 0)),
    ('mapping_keys', 'd = {"a": quiet()}', 'd.keys()', '', 'd["a"]()', (0, 0)),
    ('mapping_values', 'd = {"a": quiet()}', 'd.values()', '', 'd["a"]()',
     (0, 0)),
    ('mapping_items', 'd = {"a": quiet()}', 'd.items()', '', 'd["a"]()',
     (0, 0)),
    ('mapping_copy', 'd = {"a": quiet()}', 'd.copy()', '', 'd["a"]()', (0, 0)),
    # A store at one position writes no position the model recorded and adds
    # no length, so every recorded position stands after it. Each clean row
    # fails if the rule fired on the store.
    ('computed_index_store', _PAIR, 'x[int(args.flag)] = relay()',
     'y = [*x, ordinary]', 'y[2]()', (0, 0)),
    ('literal_index_store', _PAIR, 'x[0] = relay()', 'y = [*x, ordinary]',
     'y[1]()', (0, 0)),
    ('literal_index_store_reads_its_own', _PAIR, 'x[0] = relay()',
     'y = [*x, ordinary]', 'y[0]()', (1, 1)),
    # The in-place operators the store path folds itself: `|=` into a tracked
    # mapping, and the set operators into a set container beside it. This
    # rule must leave them to that path rather than invalidate over them.
    ('set_or', 'q = quiet()\ns = {ordinary}', 's |= {q}', '',
     'next(iter(s))()', (0, 0)),
    ('set_and', 'q = quiet()\ns = {ordinary, q}', 's &= {q}', '',
     'next(iter(s))()', (0, 0)),
    ('set_xor', 'q = quiet()\ns = {ordinary, q}', 's ^= {q}', '',
     'next(iter(s))()', (0, 0)),
    ('set_sub', 'q = quiet()\ns = {ordinary, q}', 's -= {q}', '',
     'next(iter(s))()', (0, 0)),
]


def _swap(text):
    return re.sub(r'relay|quiet',
                  lambda m: 'quiet' if m[0] == 'relay' else 'relay', text)


def _verdict(tmp, store, mutate, after, read):
    body = _PRE + store + '\n' + mutate
    if after:
        body += '\n' + after
    return _tracked_focus_verdict(tmp, body + _SEND + read, counts=True)


def _rows(tmp, table, twin):
    for row in table:
        name, store, mutate, after, read = row[:5]
        if twin:
            store, mutate, after, read = (
                _swap(part) for part in (store, mutate, after, read))
        yield name, _verdict(tmp, store, mutate, after, read)


def _missed(tmp, table, expected):
    return [(name, verdict) for name, verdict in _rows(tmp, table, False)
            if verdict != expected]


def _flagged(tmp, table, expected, twin):
    return [(name, verdict) for name, verdict in _rows(tmp, table, twin)
            if verdict != expected]


def test_every_mutation_spelling_fails_closed(tmp):
    missed = _missed(tmp, _MUTATIONS, (1, 1))
    assert not missed, missed


def test_every_mutation_twin_stays_clean(tmp):
    flagged = _flagged(tmp, _MUTATIONS, (0, 0), True)
    assert not flagged, flagged


def test_a_modelled_mutator_keeps_its_precise_result(tmp):
    missed = _missed(tmp, _PRECISE, (1, 1))
    assert not missed, missed


def test_a_modelled_mutator_twin_stays_clean(tmp):
    flagged = _flagged(tmp, _PRECISE, (0, 0), True)
    assert not flagged, flagged


def test_recorded_positions_that_still_hold_stay_standing(tmp):
    observed = [(row[0], _verdict(tmp, *row[1:5])) for row in _READING]
    wrong = [item for item, row in zip(observed, _READING)
             if item[1] != row[5]]
    assert not wrong, wrong


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='unmodelledmutation_')


if __name__ == '__main__':
    raise SystemExit(main())
