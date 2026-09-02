#!/usr/bin/env python3
"""The refusal witness floor's own detection limbs, on synthetic input.

Every guard the tools reach is witnessed on a healthy tree, so the
registration-driven pass in test_mcp_tools.py never sees a gap. These drive
the floor directly, which is the only way its detection is banked.
"""
import sys
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_guard_floor  # noqa: E402
import _util  # noqa: E402


def test_the_guard_scan_reads_every_raise_shape_the_tools_use(_tmp):
    """Each raise shape the scan can meet resolves to a deliberate condition.

    A shape resolving to nothing would leave the floor passing vacuously, the
    defect it exists to close.
    """
    sites, _module = _mcp_guard_floor.guard_shape_probe(_tmp)

    spelled = {}
    for module, function, operands in sites.values():
        spelled.setdefault((module, function), []).extend(
            text for text, _node in operands)

    assert {key: sorted(texts) for key, texts in spelled.items()} == (
        _mcp_guard_floor.GUARD_SHAPE_CONDITIONS), spelled


def test_the_witness_reads_the_condition_that_fired(_tmp):
    """The traceback names the operand that fired, or says it cannot.

    Every case runs the real raise and reads the real traceback; an operand
    that cannot be re-read hides only itself, and one that fired is None,
    never a guess.
    """
    sites, module = _mcp_guard_floor.guard_shape_probe(_tmp)
    helper, parser = module.outer()

    def fired(call, *arguments):
        try:
            call(*arguments)
        except (ValueError, RuntimeError) as raised:
            return _mcp_guard_floor.witnessed_guard(sites, raised)
        raise AssertionError(f'{arguments!r}: nothing was refused')

    for argument, condition in _mcp_guard_floor.GUARD_SHAPE_WITNESSES:
        assert fired(helper, argument) == (
            'guard_shapes', 'helper', condition), argument
    assert fired(parser, 'not a number') == (
        'guard_shapes', 'parser',
        "raise RuntimeError('re-raised from a handler')")


def test_the_refusal_floor_refuses_a_stranded_guard(_tmp):
    """unwitnessed_guard_gaps detects, not merely passes a healthy table."""
    a, b, c = ('m', 'f', 'a'), ('m', 'f', 'b'), ('m', 'g', 'a')
    gaps = _mcp_guard_floor.unwitnessed_guard_gaps
    assert gaps('t', [a, b], {a}) == [
        f'no refusal case of its own witnesses {b!r}']
    assert gaps('t', [a, a], {a}) == [f'{a!r} spells two of the guards']
    assert gaps('t', [a, c], {a, c}) == []
    # A same-named function in another module is its own obligation, so one
    # allowlist entry cannot exempt every module that spells that name.
    elsewhere = ('n', 'f', 'a')
    assert gaps('t', [a, elsewhere], {a}) == [
        f'no refusal case of its own witnesses {elsewhere!r}']
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

    Moving a tool's guards into a module the tools do not lexically reach
    used to take them out of the scan entirely, so the pins witnessing them
    could then be deleted and the mutant they killed shipped green.
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


def test_the_scan_set_follows_the_imports_not_a_directory(_tmp):
    """A module the composition imports is scanned wherever it sits.

    A guard moved into a module the composition pulls in used to fall outside
    a directory-named scan entirely — the blind spot two rounds of widening
    never closed.
    """
    composition = types.ModuleType('floor_scan_composition')
    composition.__file__ = str(_util.ROOT / 'daedalus_mcp' / 'server.py')

    def scan():
        return _mcp_guard_floor.composition_scan_set(composition, _util.ROOT)

    with mock.patch.dict(sys.modules):
        stranger = types.ModuleType('floor_scan_stranger')
        stranger.__file__ = str(_util.ROOT / 'daedalus_cli' / 'transport.py')
        suite = types.ModuleType('floor_scan_suite')
        suite.__file__ = str(Path(__file__).resolve())
        foreign = types.ModuleType('floor_scan_foreign')
        foreign.__file__ = str(Path(_tmp) / 'foreign.py')
        sys.modules.update({
            'floor_scan_stranger': stranger,
            'floor_scan_suite': suite,
            'floor_scan_foreign': foreign})
        imported = scan()
    assert (_util.ROOT / 'daedalus_cli' / 'transport.py').resolve() in imported
    assert Path(_tmp, 'foreign.py').resolve() not in imported
    assert Path(__file__).resolve() not in imported
    assert (_util.ROOT / 'daedalus_mcp' / 'server.py').resolve() in imported


TWIN_SHAPES = '''
def twin(value):
    if value < 0: raise ValueError('neg'); raise ValueError('pos')
'''


def test_two_raises_sharing_a_line_are_collapsed(_tmp):
    """One witness can never answer for two raises on one line.

    A traceback names the line, not the statement, so neither raise can be
    told apart; the collapse marks both unwitnessable instead of letting one
    refusal case discharge the pair.
    """
    sites, _module = _mcp_guard_floor.guard_shape_probe(
        _tmp, TWIN_SHAPES, 'twin_shapes')
    assert _mcp_guard_floor.guard_keys(sites) == [
        ('twin_shapes', 'twin', 'two raises share line 3')]


RERAISE_SHAPES = '''
def inner(value):
    if value is None:
        raise ValueError('none refused')


def outer(value):
    try:
        inner(value)
    except ValueError:
        raise
'''


def test_a_bare_reraise_carries_its_own_line(_tmp):
    """A bare re-raise is its own site, spelled by its line.

    `raise` unparses identically everywhere, so the line is the only text
    that keeps two bare re-raises in one module from colliding into one
    condition.
    """
    sites, _module = _mcp_guard_floor.guard_shape_probe(
        _tmp, RERAISE_SHAPES, 'reraise_shapes')
    assert _mcp_guard_floor.guard_keys(sites) == [
        ('reraise_shapes', 'inner', 'value is None'),
        ('reraise_shapes', 'outer', 'bare raise at line 11')]


SELECT_SHAPES = '''
def inner(value):
    if value is None:
        raise ValueError('none refused')


def middle(value):
    inner(value); raise RuntimeError('shares the call line')
'''


def test_the_witness_reads_the_innermost_raise_site(_tmp):
    """The innermost scanned raise site on the traceback is the one credited.

    Frames between the catch and the raise appear on the traceback at their
    call lines, so `guard` names where the refusal was written even when the
    exception crosses a re-raising frame. The middle call below shares its
    line with a scanned raise, which makes that line a site the walk meets:
    stopping at the first match would credit a raise that never fired.
    """
    sites, module = _mcp_guard_floor.guard_shape_probe(
        _tmp, SELECT_SHAPES, 'select_shapes')
    witnessed = None
    try:
        module.middle(None)
    except ValueError as raised:
        witnessed = _mcp_guard_floor.witnessed_guard(sites, raised)
    assert witnessed == ('select_shapes', 'inner', 'value is None')


EXPLODES_SHAPES = '''
class Explodes:
    def __init__(self, value):
        self.reads = 0
        self.value = value

    def __bool__(self):
        self.reads += 1
        if self.reads > 1:
            raise RuntimeError('a second read explodes')
        return self.value


def armed(first, second):
    if first or second:
        raise ValueError('refused')
'''


def test_an_operand_that_explodes_when_reread_stays_contained(_tmp):
    """Re-reading an operand that raises is reported, not propagated.

    The re-evaluation catch is what turns a hostile operand into a
    `None` condition; without it the second read's error would escape the
    floor and abort the pinning run mid-suite.
    """
    sites, module = _mcp_guard_floor.guard_shape_probe(
        _tmp, EXPLODES_SHAPES, 'explodes_shapes')
    witnessed = None
    try:
        module.armed(module.Explodes(False), module.Explodes(True))
    except ValueError as raised:
        witnessed = _mcp_guard_floor.witnessed_guard(sites, raised)
    assert witnessed == ('explodes_shapes', 'armed', None)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
