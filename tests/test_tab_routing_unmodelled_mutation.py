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
    'def quiet(): return lambda: ordinary()\n'
    'class ctx:\n'
    '    def __enter__(self): return self\n'
    '    def __exit__(self, *exc): return False\n')
_SEND = '\nsend = ext_cmd\nreturn '

_LIST = 'x = [ordinary, relay()]'
_ONE = 'x = [ordinary]'
_PAIR = 'x = [ordinary, ordinary]'
_STAR = 'x = [ordinary, relay(), *args.values]'
_DICT = 'd = {"a": 1, "b": 2}'
_SET = 's = {quiet()}'
# The read is the comprehension itself, so the runtime count does not depend
# on the order a set iterates in.
SET_COMPREHENSION = '[f() for f in s]'

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

# The six positions a statement loop never reaches: a mutating call in a
# header, a test, a context expression or a case guard is evaluated by the
# expression checker alone, so a rule wired only into the per-statement
# store hook never sees it.
_POSITIONS = [
    ('if_test', _LIST, 'if x.pop(0):\n    pass', '', 'x[0]()'),
    ('while_test', _LIST, 'i = 0\nwhile i < 1 and x.pop(0):\n    i = i + 1',
     '', 'x[0]()'),
    ('for_iterable', _LIST, 'for _ in (x.pop(0),):\n    pass', '', 'x[0]()'),
    ('with_context', _LIST, 'with (x.pop(0), ctx())[1]:\n    pass', '',
     'x[0]()'),
    ('match_guard', _LIST + '\nz = args.flag',
     'match z:\n    case _ if x.pop(0):\n        pass', '', 'x[0]()'),
    ('mapping_if_test', _DICT, 'if d.popitem():\n    pass',
     'x = [*d, relay()]', 'x[1]()'),
    # A raise ends the path the statement was on, and the handler is entered
    # from wherever the exception left it -- which is after the mutation, not
    # before it.
    ('raise_argument', _LIST, 'try:\n    raise ValueError(x.pop(0))\n'
     'except ValueError:\n    pass', '', 'x[0]()'),
]

# A mutating call whose receiver the model cannot resolve is not a no-op the
# guard may read clean: the call's own value stays unproved, so a `tab` routed
# through it later reports. Each row is paired with a control whose receiver
# DOES resolve to a tracked container, where the same construction is provable
# and stays clean. A tabless call through an unproved value is deliberately
# clean: the guard's contract is about the `tab` on the call being made.
_TAB = '("_focus", "focus-tab", tab=args.chrome_tab)'
_FORWARD = 'forward = lambda *a, **k: send(*a, **k)\n'
_UNFORWARD = 'forward = lambda *a, **k: ordinary()\n'
# The tab may live inside the callee's body rather than on the call: `y` is
# bound to the deferred callable `x[0]` would have selected, so a call
# through it may reach a sender carrying one. A tabless call through an
# unproved value is the shape the review filed and the lead measured.
_CALL = '()'
_UNRESOLVED = [
    ('attribute_receiver_tableless', 'args.box = [relay(), ordinary]',
     'y = args.box.pop(0)', '', 'y' + _CALL, (1, 1)),
    ('attribute_receiver_tableless_twin', 'args.box = [ordinary, relay()]',
     'y = args.box.pop(0)', '', 'y' + _CALL, (0, 0)),
    ('getattr_receiver_tableless', 'args.box = [relay(), ordinary]',
     'y = getattr(args, "box").pop(0)', '', 'y' + _CALL, (1, 1)),
    ('getattr_receiver_tableless_twin', 'args.box = [ordinary, relay()]',
     'y = getattr(args, "box").pop(0)', '', 'y' + _CALL, (0, 0)),
    ('attribute_receiver', _FORWARD + 'args.box = [forward, ordinary]',
     'y = args.box.pop(0)', '', 'y' + _TAB, (1, 1)),
    ('attribute_receiver_control', _UNFORWARD + 'box = [forward, ordinary]',
     'y = box.pop(0)', '', 'y' + _TAB, (0, 0)),
    ('getattr_receiver', _FORWARD + 'args.box = [forward, ordinary]',
     'y = getattr(args, "box").pop(0)', '', 'y' + _TAB, (1, 1)),
    ('getattr_receiver_control', _UNFORWARD + 'box = [forward, ordinary]',
     'y = getattr(box, "pop")(0)', '', 'y' + _TAB, (0, 0)),
]

# The operation is a method invoked on a container, not an `Attribute` func
# node: a method held in a name, and the unbound forms that reach a container
# through `operator` and `functools`, all take the same path.
_METHODS = [
    ('method_in_a_name', _LIST, 'f = x.pop\nf(0)', '', 'x[0]()'),
    ('getattr_rebound', _LIST, 'g = getattr\ng(x, "pop")(0)', '', 'x[0]()'),
    ('operator_setitem', _LIST, 'import operator\noperator.setitem(x, 0, '
     'relay())', '', 'x[0]()'),
    ('operator_methodcaller', _LIST,
     'import operator\noperator.methodcaller("pop", 0)(x)', '', 'x[0]()'),
    ('functools_partial', _LIST,
     'from functools import partial\npartial(list.pop, x)(0)', '', 'x[0]()'),
    ('operator_delitem', _LIST, 'import operator\noperator.delitem(x, 0)',
     '', 'x[0]()'),
]

# A set operation read over the set, so the runtime count does not depend on
# the order a set iterates in. The store path folds `|=` into a tracked
# mapping and nothing else; every other set operation is this rule's, so a
# `&=` or `-=` that empties the set at runtime is a disclosed over-report
# rather than a miss.
_SET_FOLD = [
    ('set_or_fold', _SET, 's |= {relay()}', SET_COMPREHENSION, (1, 1)),
    ('set_or_control', _SET, 'ordinary()', SET_COMPREHENSION, (0, 0)),
    ('set_and_over_reports', 'q = quiet()\ns = {q}', 's &= {relay()}',
     SET_COMPREHENSION, (0, 1)),
    ('set_sub_over_reports', 'q = quiet()\ns = {q}', 's -= {relay()}',
     SET_COMPREHENSION, (0, 1)),
    ('set_xor_fold', 'q = quiet()\ns = {q}', 's ^= {relay()}',
     SET_COMPREHENSION, (1, 1)),
    ('set_add_fold', 'q = quiet()\ns = {q}', 's.add(relay())',
     SET_COMPREHENSION, (1, 1)),
    ('set_control', 'q = quiet()\ns = {q}', 'ordinary()',
     SET_COMPREHENSION, (0, 0)),
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
    ('sequence_clear', 'x = [relay()]', 'x.clear()', 'x = [*x, relay()]',
     'x[0]()'),
    ('set_clear', 's = {relay()}', 's.clear()', 'x = [*s, relay()]', 'x[0]()'),
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
    # `__iter__` walks a container and `fromkeys` builds a new one. Both sit on
    # the derived surface and neither mutates its receiver.
    ('sequence_iter', _LIST, 'x.__iter__()', '', 'x[0]()', (0, 0)),
    ('mapping_fromkeys', 'd = {"a": quiet()}', 'd.fromkeys(["z"], relay())',
     '', 'd["a"]()', (0, 0)),
    # A bound call on a receiver the model does not track hands it a tracked
    # container as an argument. The argument is an operand, never the thing
    # being mutated.
    ('bound_call_keeps_its_operand', 'args.lst = [quiet()]\nx = [ordinary, '
     'relay()]', 'args.lst.append(x)', '', 'x[0]()', (0, 0)),
    ('unbound_constructor_keeps_its_argument',
     'd = {"a": quiet()}\nx = [ordinary, relay()]',
     'dict.fromkeys(d, relay())', '', 'x[0]()', (0, 0)),
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


def test_every_unreached_position_fails_closed(tmp):
    missed = _missed(tmp, _POSITIONS, (1, 1))
    assert not missed, missed


def test_every_unreached_position_twin_stays_clean(tmp):
    flagged = _flagged(tmp, _POSITIONS, (0, 0), True)
    assert not flagged, flagged


def test_an_unresolvable_receiver_fails_closed(tmp):
    observed = [(row[0], _verdict(tmp, row[1], row[2], '', row[4]))
                for row in _UNRESOLVED]
    wrong = [item for item, row in zip(observed, _UNRESOLVED)
             if item[1] != row[5]]
    assert not wrong, wrong


def test_every_method_invocation_spelling_fails_closed(tmp):
    missed = _missed(tmp, _METHODS, (1, 1))
    assert not missed, missed


def test_every_method_invocation_twin_stays_clean(tmp):
    flagged = _flagged(tmp, _METHODS, (0, 0), True)
    assert not flagged, flagged


def test_a_set_fold_matches_what_the_set_holds(tmp):
    observed = [(row[0], _verdict(tmp, row[1], row[2], '', row[3]))
                for row in _SET_FOLD]
    wrong = [item for item, row in zip(observed, _SET_FOLD)
             if item[1] != row[4]]
    assert not wrong, wrong


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='unmodelledmutation_')


if __name__ == '__main__':
    raise SystemExit(main())
