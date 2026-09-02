#!/usr/bin/env python3
"""The refusal witness floor's own detection limbs, on synthetic input.

Every guard the MCP tools reach is witnessed on a healthy tree, so the
registration-driven pass in test_mcp_tools.py never sees a gap. These
drive the floor directly, which is the only way its detection is banked
rather than assumed.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_guard_floor  # noqa: E402
import _util  # noqa: E402


def test_the_guard_scan_reads_every_raise_shape_the_tools_use(_tmp):
    """Each raise shape the scan can meet resolves to a deliberate condition.

    A raise in a nested helper belongs to that helper; an `or` test gives one
    condition per operand while an `and` test gives one for the whole test,
    since neither half of an `and` fires the raise alone; a raise no `if` body
    holds stands for itself, an `else` and an exception handler included; an
    `assert` is not a raise. A shape resolving to nothing would leave the floor
    passing vacuously, the defect it exists to close.
    """
    sites, _helpers = _mcp_guard_floor.guard_shape_probe(_tmp)

    spelled = {}
    for module, function, operands in sites.values():
        spelled.setdefault((module, function), []).extend(
            text for text, _node in operands)

    assert {key: sorted(texts) for key, texts in spelled.items()} == (
        _mcp_guard_floor.GUARD_SHAPE_CONDITIONS), spelled


def test_the_witness_reads_the_condition_that_fired(_tmp):
    """The traceback names the operand that fired, or says it cannot.

    Every case here runs the real raise and reads the real traceback. The
    single-condition path is what keeps a raise no `if` guards out of the
    compiler, and an operand that cannot be re-read - a comprehension has no
    meaning outside the frame that ran it - is reported as undeterminable
    rather than mis-attributed to whichever operand happens to evaluate.
    """
    sites, (helper, parser) = _mcp_guard_floor.guard_shape_probe(_tmp)

    def fired(call, argument):
        try:
            call(argument)
        except (ValueError, RuntimeError) as raised:
            return _mcp_guard_floor.witnessed_guard(sites, raised)
        raise AssertionError(f'{argument!r}: nothing was refused')

    for argument, condition in _mcp_guard_floor.GUARD_SHAPE_WITNESSES:
        assert fired(helper, argument) == (
            'guard_shapes', 'helper', condition), argument
    assert fired(parser, 'not a number') == (
        'guard_shapes', 'parser',
        "raise RuntimeError('re-raised from a handler')")


def test_the_refusal_floor_refuses_a_stranded_guard(_tmp):
    """unwitnessed_guard_gaps detects, not merely passes a healthy table.

    Called directly with synthetic guards, so the floor's detection limb is
    banked even though every guard the tools reach is witnessed and the
    registration-driven pass therefore sees no gaps.
    """
    a, b, c = ('m', 'f', 'a'), ('m', 'f', 'b'), ('m', 'g', 'a')
    gaps = _mcp_guard_floor.unwitnessed_guard_gaps
    assert gaps('t', [a, b], {a}) == [
        f'no refusal case of its own witnesses {b!r}']
    assert gaps('t', [a, a], {a}) == [f'{a!r} spells two of the guards']
    assert gaps('t', [a, c], {a, c}) == []
    with mock.patch.dict(_mcp_guard_floor.UNWITNESSED_GUARDS, {'t': {b}}):
        assert gaps('t', [a, b], {a}) == []
        assert gaps('t', [a, b], {b}) == [
            f'UNWITNESSED_GUARDS names {b!r}, which its own refusal case '
            'already witnesses',
            f'no refusal case of its own witnesses {a!r}']
    with mock.patch.dict(_mcp_guard_floor.UNWITNESSED_GUARDS, {'t': {c}}):
        assert gaps('t', [a, b], {a}) == [
            f'UNWITNESSED_GUARDS names {c!r}, which it does not reach',
            f'no refusal case of its own witnesses {b!r}']
    # A witness belongs to the tool that fired it: one tool reaching a guard
    # another tool pins is still stranded.
    assert _mcp_guard_floor.stranded_guards(
        {'x': [a], 'y': [a]}, {('x', a)}) == {
            'y': [f'no refusal case of its own witnesses {a!r}']}


def test_a_guard_outside_the_tool_surface_is_not_covered_by_reachability(_tmp):
    """A guard no tool reaches is surfaced, wherever its module sits.

    This is the reviewed escape it exists for: moving a tool's guards into a
    module the tools do not lexically reach used to take them out of the scan
    entirely, so the pins witnessing them could then be deleted and the mutant
    they killed shipped green. Reachability alone cannot see that, because the
    moved guard is exactly the one nothing reaches any more.
    """
    reached_guard = ('tools_network', 'net_capture', 'max_requests < 1')
    moved_guard = ('transport', 'validate', 'max_requests < 1')
    sites = {('net.py', 19): ('tools_network', 'net_capture',
                              [('max_requests < 1', None)]),
             ('transport.py', 99): ('transport', 'validate',
                                    [('max_requests < 1', None)])}

    off_surface = _mcp_guard_floor.guards_off_the_tool_surface(
        sites, {'net_capture': [reached_guard]})

    assert off_surface == {moved_guard}, off_surface
    assert _mcp_guard_floor.guards_off_the_tool_surface(
        sites, {'net_capture': [reached_guard, moved_guard]}) == set()


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
