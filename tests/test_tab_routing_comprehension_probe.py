#!/usr/bin/env python3
"""A comprehension reads its first output position exactly.

A comprehension does not reorder: a filter selects which elements appear,
never their order, so the element at output index 0 is the iterable's own
element 0 whenever a filter keeps it. The merge of every element belongs only
to a consumer that walks the result, because a walk visits every element.

These tests pin that contract end to end and at the probe's own boundary, so
each limb of `comprehension_first` fails a named test when it is deleted.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _pyroute_containers import iterated_key  # noqa: E402
from _pyroute_state import FlowState  # noqa: E402
from _pyroute_targets import (  # noqa: E402
    _known_ordered, comprehension_first, probe_comprehension)
from _pyroute_values import DeferredContainer  # noqa: E402
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
    # A consumer that walks the result visits every element, so the merge it
    # carries must keep reporting; the probe's narrowing is the positional
    # read only, and must not silence the walk.
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


def test_known_ordered_refuses_unordered_kinds(_tmp):
    """The probe reads a comprehension's first output position, so it accepts
    only a tuple or list operand: a set, dict or sorted result has no
    positional element 0 for it to bind the target to."""
    assert _known_ordered(_ordered_operand())
    assert _known_ordered(DeferredContainer({0: 'a', 1: 'b'}, 2, 'list'))
    for kind in ('set', 'dict', 'sorted'):
        assert not _known_ordered(
            DeferredContainer({0: 'a', 1: 'b'}, 2, kind)), kind


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
