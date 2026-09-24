#!/usr/bin/env python3
"""A comprehension reads its first output position exactly.

A comprehension does not reorder: a filter selects which elements appear,
never their order, so the element at output index 0 is the iterable's own
element 0 -- but only while that element is determinable on both counts: the
operand has to yield one element there, and no filter may drop it. Where
either is not determinable the position is unprovable, and unprovable in
this guard means reported, never silently clean and never a union written as
though it were one element. The merge of every element belongs only to a
consumer that walks the result, because a walk visits every element, and it
is kept either way.

These tests pin that contract end to end and at the probe's own boundary.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_containers import iterated_key  # noqa: E402
from _pyroute_state import FlowState  # noqa: E402
from _pyroute_targets import (  # noqa: E402
    _UNPROVABLE, _first_element, comprehension_first, probe_comprehension)
from _pyroute_values import (  # noqa: E402
    DeferredAlternatives, DeferredContainer)
from test_tab_routing_collapse import body, verdicts  # noqa: E402


def test_filtered_reversed_comprehension_first_position(tmp):
    """A filter does not make the comprehension's first output position the
    merge of every element. Each filter here keeps every element, and a
    comprehension does not reorder, so index 0 is the producer's element 0
    whatever the filter is; the merge belongs only to a consumer that walks
    the result, which visits every element. The order-preserving control
    keeps reporting at index 0."""
    filters = ['c', 'True', 'ordinary', 'not False', 'True if ordinary']
    cases = []
    for index, condition in enumerate(filters):
        store = f'x = [c for c in reversed(pair()) if {condition}]'
        first = body(store, 'x[0]()')
        cases.append((f'filtered-reversed-{index}', first, (0, 0)))
        cases.append((f'filtered-reversed-{index}-clean', first.replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)))
    control = body('x = [c for c in pair() if c]', 'x[0]()')
    cases.append(('filtered-preserving', control, (1, 1)))
    cases.append(('filtered-preserving-clean', control.replace(
        'lambda: send(', 'lambda: ordinary('), (0, 0)))
    walk = body('send = ext_cmd\n'
                'for v in [c for c in reversed(pair()) if c]:\n'
                '    v()', '0')
    cases.append(('filtered-walk', walk, (1, 1)))
    cases.append(('filtered-walk-clean', walk.replace(
        'lambda: send(', 'lambda: ordinary('), (0, 0)))
    verdicts(tmp, cases)


def _ordered_operand():
    return DeferredContainer({0: 'first', 1: 'second'}, 2, 'tuple')


def _ordered_probe():
    """A state whose comprehension iterable models a two-element ordered
    container, and a `check` that records findings the probe will discard."""
    node = ast.parse('[c for c in src]').body[0].value
    source = node.generators[0].iter
    state = FlowState({}, {}, {}, {}, set(), set(), {}, set())
    violations = []

    def check(expr, outputs):
        if expr is source:
            for output in outputs:
                output.evaluated[id(expr)] = _ordered_operand()
        else:
            violations.append('probe-finding')
            for output in outputs:
                output.evaluated.setdefault(id(expr), 'bodyvalue')
        return outputs

    return node, state, check, violations


def test_comprehension_probe_discards_its_own_findings(_tmp):
    """A finding the probe's own re-check of the body makes is a narrowing of
    the merge the primary run already made, so it is discarded rather than
    counted a second time."""
    node, state, check, violations = _ordered_probe()
    assert comprehension_first(
        node, [node.elt], [state], FlowState.copy, check, violations)
    assert violations == []


def test_first_element_refuses_unordered_kinds(_tmp):
    """The probe binds a comprehension's first output position to the
    operand's own element 0, so it accepts only a tuple or list operand: a
    set, dict or sorted result has no positional element 0 to bind the
    target to, and neither has a container of unknown length."""
    assert _first_element(_ordered_operand()) == 'first'
    assert _first_element(
        DeferredContainer({0: 'a', 1: 'b'}, 2, 'list')) == 'a'
    assert _first_element(
        DeferredContainer({1: 'b'}, 2, 'tuple')) is None, 'an absent key'
    for kind in ('set', 'dict', 'sorted'):
        assert _first_element(
            DeferredContainer({0: 'a', 1: 'b'}, 2, kind)) is _UNPROVABLE, kind
    assert _first_element(
        DeferredContainer({0: 'a', 1: 'b'}, None, 'tuple')) is _UNPROVABLE


def test_first_element_binds_alternatives_only_on_agreement(_tmp):
    """An alternatives operand places the position only when every branch is
    an ordered container and they agree on that element. A union of the
    branches is not an agreement, so a disagreement -- and a branch that is
    not an ordered container at all -- leaves the position unprovable."""
    first = DeferredContainer({0: 'routed'}, 1, 'list')
    agree = DeferredAlternatives(
        (first, DeferredContainer({0: 'routed'}, 1, 'tuple')))
    assert _first_element(agree) == 'routed'
    disagree = DeferredAlternatives(
        (first, DeferredContainer({0: 'other'}, 1, 'tuple')))
    assert _first_element(disagree) is _UNPROVABLE
    assert _first_element(DeferredAlternatives(
        (first, DeferredContainer({0: 'other'}, 1, 'set')))) is _UNPROVABLE


def test_filter_that_may_drop_the_element_leaves_the_position_unprovable(
        tmp):
    """A filter that may drop the element at index 0 puts a later element
    there, so the position is unprovable and the merge the primary run made
    stands: reported when that merge routes, and also where the runtime
    happens to leave the result clean, because the guard cannot tell which
    element the filter kept. Inertness is the filter's shape, not its
    spelling: a bare name, whose value the guard holds truthy, and a literal
    the filter reads nothing from are inert; a comparison, an attribute, a
    call or a subscript is not. The boundary is drawn toward false greens."""
    drops = 'x = [c for c in reversed(pair()) if c is not ordinary]'
    verdicts(tmp, [
        ('filter-drops-routed', body(drops, 'x[0]()'), (1, 1)),
        ('filter-drops-routed-clean', body(drops, 'x[0]()').replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)),
        ('filter-drops-walk', body(
            'send = ext_cmd\n' + drops + '\nfor v in x: v()', '0'), (1, 1)),
        ('filter-drops-walk-clean', body(
            'send = ext_cmd\n' + drops + '\nfor v in x: v()', '0').replace(
                'lambda: send(', 'lambda: ordinary('), (0, 0)),
        ('filter-drops-preserving', body(
            'x = [c for c in pair() if c is ordinary]', 'x[0]()'), (0, 1)),
        ('filter-unknown', body(
            'x = [c for c in reversed(pair()) if args.flag]',
            'x[0]()'), (0, 1)),
        ('unprovable-call', body(
            'x = [c for c in reversed(pair()) if bool(c)]',
            'x[0]()'), (0, 1)),
        ('unprovable-subscript', body(
            'x = [c for c in reversed(pair()) if args.values[0]]',
            'x[0]()'), (0, 1)),
        ('inert-second-generator', body(
            'x = [c for c in reversed(pair()) for _ in [0] if True]',
            'x[0]()'), (0, 0)),
    ])


def test_alternatives_operand_agrees_at_the_first_position(tmp):
    """Where every branch's element 0 agrees the position is bound, so a
    result the runtime proves clean is not reported. Where the branches
    disagree the position is unprovable, which is the same reported side a
    filter the guard cannot evaluate lands on. Index 1 stays the unplaced
    position #948 records, and the walk keeps seeing every element either
    way."""
    agree = ('def alt():\n'
             '    if args.flag: return list(pair())\n'
             '    return tuple(pair())\n'
             'x = [c for c in reversed(alt())]')
    disagree = ('def alt():\n'
                '    if args.flag: return (relay(), ordinary)\n'
                '    return (ordinary, relay())\n'
                'x = [c for c in reversed(alt())]')
    both_clean = ('def alt():\n'
                  '    if args.flag: return (ordinary, ordinary)\n'
                  '    return (ordinary, ordinary)\n'
                  'x = [c for c in reversed(alt())]')
    verdicts(tmp, [
        ('alt-agree-first', body(agree, 'x[0]()'), (0, 0)),
        ('alt-agree-first-clean', body(agree, 'x[0]()').replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)),
        ('alt-agree-other', body(agree, 'x[1]()'), (1, 0)),
        ('alt-agree-walk', body('send = ext_cmd\n' + agree
                                + '\nfor v in x: v()', '0'), (1, 1)),
        ('alt-agree-walk-clean', body('send = ext_cmd\n' + agree
                                      + '\nfor v in x: v()', '0').replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)),
        ('alt-disagree-first', body(disagree, 'x[0]()'), (0, 1)),
        ('alt-disagree-first-clean', body(disagree, 'x[0]()').replace(
            'lambda: send(', 'lambda: ordinary('), (0, 0)),
        ('alt-disagree-walk', body('send = ext_cmd\n' + disagree
                                   + '\nfor v in x: v()', '0'), (1, 1)),
        ('alt-both-clean-agree', body(both_clean, 'x[0]()'), (0, 0)),
    ])


def test_comprehension_probe_refuses_a_fork(_tmp):
    """The probe narrows one state at a time, so a producer that re-checks
    into two states is not one branch standing in for the other: the probe
    returns False, stops at the fork and writes nothing. Dropping the
    len(outputs) != 1 half of the early return lets the probe keep only the
    first state, write a value derived from one branch of a two-branch
    answer, and collapse the discarded branch silently."""
    node = ast.parse('x = [c for c in seq]').body[0].value
    generator = node.generators[0]
    ordered = DeferredContainer({0: 'routed'}, 1, 'list')
    other = DeferredContainer({0: 'other'}, 1, 'list')
    state = FlowState({}, {}, {}, {id(generator.iter): ordered},
                      set(), set(), {}, set())
    seen = []

    def check(expression, states):
        seen.append(ast.unparse(expression))
        if expression is generator.iter:
            first, second = states[0].copy(), states[0].copy()
            first.evaluated[id(generator.iter)] = ordered
            second.evaluated[id(generator.iter)] = other
            return [first, second]
        for entry in states:
            entry.evaluated[id(node.elt)] = 'routed'
        return states

    violations = []
    assert comprehension_first(node, [node.elt], [state], FlowState.copy,
                               check, violations) is False
    assert seen == ['seq'], seen
    assert id(node.elt) not in state.evaluated
    assert id(node) not in state.evaluated
    assert not violations


def test_comprehension_probe_clears_a_stale_iterated_slot(_tmp):
    """The probe records the walked merge in a slot keyed by the node, so a
    stale value from an earlier pass over the same node is cleared before the
    probe writes the current one."""
    node, state, _check, violations = _ordered_probe()
    # An operand of unknown length is not positional, so the probe writes
    # nothing; the stale slot must still be gone afterwards.
    source = node.generators[0].iter

    def check_unknown_length(expr, outputs):
        if expr is source:
            for output in outputs:
                output.evaluated[id(expr)] = DeferredContainer(
                    {0: 'x', 1: 'y'}, None, 'tuple')
        return outputs

    state.evaluated[iterated_key(node)] = 'STALE'
    probe_comprehension(node, [state], [], FlowState.copy,
                        check_unknown_length, violations)
    assert state.evaluated.get(iterated_key(node)) is None


def test_set_comprehension_is_probed(_tmp):
    """A set comprehension is probed like a list one, so the merge a walker
    must see is recorded beside its element value."""
    source = ast.parse('{c for c in src}').body[0].value
    iterable = source.generators[0].iter
    state = FlowState({}, {}, {}, {}, set(), set(), {}, set())

    def check(expr, outputs):
        if expr is iterable:
            for output in outputs:
                output.evaluated[id(expr)] = _ordered_operand()
        else:
            for output in outputs:
                output.evaluated.setdefault(id(expr), 'bodyvalue')
        return outputs

    probe_comprehension(source, [state], [], FlowState.copy, check, [])
    assert iterated_key(source) in state.evaluated


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='comprobe_')


if __name__ == '__main__':
    raise SystemExit(main())
