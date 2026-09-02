#!/usr/bin/env python3
"""The refusal witness floor's own detection limbs, on synthetic input.

Every guard the tools reach is witnessed on a healthy tree, so the
registration-driven pass in test_mcp_tools.py never sees a gap. These drive
the floor directly, which is the only way its detection is banked.
"""
import importlib.util
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_guard_floor  # noqa: E402
import _util  # noqa: E402


def test_the_guard_scan_reads_every_raise_shape_the_tools_use(_tmp):
    """Each raise shape the scan can meet resolves to exactly one site key.

    A shape resolving to nothing would leave the floor passing vacuously. The
    fixture carries an `assert` too; the set equality is what holds it
    spelled at no site.
    """
    sites, _module = _mcp_guard_floor.guard_shape_probe(_tmp)

    assert set(_mcp_guard_floor.guard_keys(sites)) == (
        _mcp_guard_floor.GUARD_SHAPE_SITES), sites


def test_the_witness_names_the_innermost_raise_site(_tmp):
    """The innermost scanned raise site on the traceback is the one credited.

    The middle call shares its line with a scanned raise, making that line a
    site the walk meets: stopping at the first match would credit a raise
    that never fired.
    """
    sites, module = _mcp_guard_floor.guard_shape_probe(
        _tmp, _mcp_guard_floor.SELECT_SHAPES, 'select_shapes')
    witnessed = None
    try:
        module.middle(None)
    except ValueError as raised:
        witnessed = _mcp_guard_floor.witnessed_guard(sites, raised)
    assert witnessed == ('select_shapes', 'inner', 'value is None')


SAME_TEXT_SHAPES = '''
def twin(value):
    try:
        return int(value)
    except ValueError:
        raise ValueError('bad number') from None
    except TypeError:
        raise ValueError('bad number') from None
'''


def test_two_sites_spelling_one_condition_are_refused(_tmp):
    """One witness cannot answer for two sites in one function.

    The two handlers below raise the same message, so their site keys spell
    one condition text in one (module, function); the scan refuses them
    instead of silently merging, and the remedy is distinct messages.
    """
    try:
        _mcp_guard_floor.guard_shape_probe(
            _tmp, SAME_TEXT_SHAPES, 'same_text_shapes')
    except AssertionError as raised:
        assert 'same_text_shapes.twin' in str(raised), raised
        assert 'distinct' in str(raised), raised
    else:
        raise AssertionError('two identical raises merged into one key')


OR_SHAPES = '''
def armed(first, second):
    if first or second:
        raise ValueError('refused')
'''


def test_a_reached_or_guard_is_refused(_tmp):
    """A raise a tool reaches refuses on one condition only.

    The unit of witnessing is the raise site, so a guard whose test is an
    `or` of two conditions is refused rather than witnessed: the remedy is
    one raise per condition, because one witness cannot answer for two
    conditions on one line.
    """
    sites, module = _mcp_guard_floor.guard_shape_probe(
        _tmp, OR_SHAPES, 'or_shapes')
    try:
        _mcp_guard_floor.reachable_guards(sites, [module.armed.__code__])
    except AssertionError as raised:
        assert 'or_shapes.armed' in str(raised), raised
        assert 'split' in str(raised), raised
    else:
        raise AssertionError('an or guard was reached instead of refused')


NESTED_OR_SHAPES = '''
def armed(first, second):
    if bool(first or second):
        raise ValueError('refused')
'''


def test_an_or_nested_in_the_test_is_refused(_tmp):
    """The refusal walks the whole test expression, not its top node.

    Wrapping the `or` in a call keeps both conditions in one raise site,
    so the walk that reads the test finds it wherever it sits.
    """
    sites, module = _mcp_guard_floor.guard_shape_probe(
        _tmp, NESTED_OR_SHAPES, 'nested_or_shapes')
    try:
        _mcp_guard_floor.reachable_guards(sites, [module.armed.__code__])
    except AssertionError as raised:
        assert 'nested_or_shapes.armed' in str(raised), raised
        assert 'split' in str(raised), raised
    else:
        raise AssertionError('an or nested in the test was reached, not '
                             'refused')


TWIN_SHAPES = '''
def twin(value):
    raise ValueError('neg'); raise ValueError('pos')
'''


def test_two_raises_on_one_line_are_refused(_tmp):
    """A traceback names the line, not the statement.

    The two raises are unguarded and spell distinct messages, so no other
    refusal produces this red: sharing one physical line is the only
    shape at fault, and the scan refuses it — one raise per line —
    instead of collapsing the sites.
    """
    try:
        _mcp_guard_floor.guard_shape_probe(_tmp, TWIN_SHAPES, 'twin_shapes')
    except AssertionError as raised:
        assert 'twin_shapes:3' in str(raised), raised
        assert 'one raise per line' in str(raised), raised
    else:
        raise AssertionError('two raises on one line were collapsed, not '
                             'refused')


HELPER_TREE = '''
def _checked(value):
    if value < 1:
        raise ValueError('refused')


def tool(value):
    return _checked(value)
'''


def test_a_module_global_helper_site_is_reached(_tmp):
    """A tool reaches a guard through a module-global function.

    The most ordinary refactor — moving a guard into a helper — used to take
    the guard off the tool's reach, so declaring it off-surface then let its
    pins be deleted (round 3's plant D2).
    """
    sites, module = _mcp_guard_floor.guard_shape_probe(
        _tmp, HELPER_TREE, 'helper_tree')
    reached = _mcp_guard_floor.reachable_guards(
        sites,
        _mcp_guard_floor.tool_code_objects(module.tool, Path(_tmp)))

    assert ('helper_tree', '_checked', 'value < 1') in reached.values(), (
        reached)


def test_a_helper_defined_under_tests_is_not_walked(_tmp):
    """The reach walk stays out of tests/.

    A function whose file sits under a tests/ directory of the probe root
    is somebody else's raise: following it would put suite code on the
    tool's obligation set.
    """
    root = Path(_tmp)
    helper = root / 'tests' / 'helper.py'
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text(HELPER_TREE, encoding='utf-8')
    spec = importlib.util.spec_from_file_location('helper', helper)
    helper_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper_module)
    sites, module = _mcp_guard_floor.guard_shape_probe(
        _tmp, HELPER_TREE, 'helper_tree')
    module._checked = helper_module._checked
    sites.update(_mcp_guard_floor.tool_guards([helper], root))
    reached = _mcp_guard_floor.reachable_guards(
        sites, _mcp_guard_floor.tool_code_objects(module.tool, root))

    assert reached == {}, reached


def test_the_refusal_floor_refuses_a_stranded_guard(_tmp):
    """unwitnessed_guard_gaps detects, not merely passes a healthy table."""
    del _tmp
    a, b, c = ('m', 'f', 'a'), ('m', 'f', 'b'), ('m', 'g', 'a')
    gaps = _mcp_guard_floor.unwitnessed_guard_gaps
    assert gaps('t', [a, b], {a}) == [
        f'no refusal case of its own witnesses {b!r}']
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
    """A guard no tool reaches is surfaced, wherever its module sits."""
    del _tmp
    reached_guard = ('tools_network', 'net_capture', 'max_requests < 1')
    moved_guard = ('transport', 'validate', 'max_requests < 1')
    sites = {('net.py', 19): (reached_guard, False),
             ('transport.py', 99): (moved_guard, False)}

    off_surface = _mcp_guard_floor.guards_off_the_tool_surface(
        sites, {'net_capture': {('net.py', 19): reached_guard}})

    assert off_surface == {moved_guard}, off_surface
    assert _mcp_guard_floor.guards_off_the_tool_surface(
        sites,
        {'net_capture': {('net.py', 19): reached_guard,
                         ('transport.py', 99): moved_guard}}) == set()


def test_every_off_surface_citation_names_a_test_that_exists(_tmp):
    """A citation is data, so a renamed or deleted test goes red here.

    The assertion on the real table alone would pass vacuously the moment
    the check behind it broke, so the negative controls feed it tables
    that must be reported: a function absent from a real suite, and a
    suite path that does not exist.
    """
    del _tmp
    assert _mcp_guard_floor.missing_citations(
        _mcp_guard_floor.GUARDS_OFF_THE_TOOL_SURFACE, _util.ROOT) == []
    absent_function = (
        'tests/test_mcp_tools.py::test_this_test_does_not_exist_anywhere')
    absent_suite = 'tests/test_no_such_suite.py::test_absent'
    assert _mcp_guard_floor.missing_citations([
        ('m', 'f', 'c', absent_function),
        ('m', 'f', 'c', absent_suite),
    ], _util.ROOT) == [absent_function, absent_suite]


LAZY_COMPOSITION = '''
from pkg import leaf


def run():
    if leaf.dead:
        from pkg import hidden
'''


def _write_tree(directory, files):
    for name, source in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')


def test_the_scan_set_includes_a_module_imported_under_a_dead_branch(_tmp):
    """The scan set is the composition's static import graph.

    A module imported only inside a function body, under a branch nothing
    executes, is still a module the composition can import. The sys.modules
    snapshot this replaces missed exactly this one: round 3's plant B2 sat
    green at 13/13 with an undeclared raise.
    """
    _write_tree(Path(_tmp), {
        'composition.py': LAZY_COMPOSITION,
        'pkg/__init__.py': '',
        'pkg/leaf.py': 'dead = False\n',
        'pkg/hidden.py': "raise ValueError('unwitnessed')\n"})
    scanned = _mcp_guard_floor.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    names = {path.relative_to(Path(_tmp)).as_posix() for path in scanned}
    assert names == {
        'composition.py', 'pkg/__init__.py', 'pkg/leaf.py',
        'pkg/hidden.py'}, names


COMPUTED_IMPORT_COMPOSITION = '''
import importlib


def load(name):
    return importlib.import_module(name)
'''


def test_a_computed_import_refuses_the_scan(_tmp):
    """An import the walk cannot read statically fails the scan loudly.

    Naming the module and the import site is what makes the closure
    bounded rather than bounded-looking; a walk that silently omitted what
    it cannot resolve would be the fifth boundary variant.
    """
    _write_tree(Path(_tmp), {'composition.py': COMPUTED_IMPORT_COMPOSITION})
    try:
        _mcp_guard_floor.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError as raised:
        assert 'composition:6' in str(raised), raised
        assert 'import_module' in str(raised), raised
    else:
        raise AssertionError('a computed import was silently skipped')


FOREIGN_IMPORT_COMPOSITION = '''
import json
from mcp.server.mcpserver import MCPServer
'''


def _refuses_the_scan(_tmp, source, site):
    """The scan refuses this composition source, naming the import site."""
    _write_tree(Path(_tmp), {'composition.py': source})
    try:
        _mcp_guard_floor.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError as raised:
        assert f'composition:{site}' in str(raised), raised
        assert 'cannot read statically' in str(raised), raised
    else:
        raise AssertionError('a computed import was silently skipped')


def test_an_import_module_from_import_refuses_the_scan(_tmp):
    """`from importlib import import_module` binds the operation too.

    A plain name is the spelling a linter would suggest first; recognising
    only the attribute form walks past the unprovable state silently.
    """
    _refuses_the_scan(_tmp, '''
from importlib import import_module


def load(name):
    return import_module(name)
''', 6)


def test_an_aliased_importlib_module_refuses_the_scan(_tmp):
    _refuses_the_scan(_tmp, '''
import importlib as il


def load(name):
    return il.import_module(name)
''', 6)


def test_an_aliased_import_module_refuses_the_scan(_tmp):
    _refuses_the_scan(_tmp, '''
from importlib import import_module as im


def load(name):
    return im(name)
''', 6)


def test_a_builtin_import_refuses_the_scan(_tmp):
    _refuses_the_scan(_tmp, '''

def load(name):
    return __import__(name)
''', 4)


def test_an_importlib_import_refuses_the_scan(_tmp):
    _refuses_the_scan(_tmp, '''
import importlib


def load(name):
    return importlib.__import__(name)
''', 6)


def test_a_builtins_import_refuses_the_scan(_tmp):
    _refuses_the_scan(_tmp, '''
import builtins


def load(name):
    return builtins.__import__(name)
''', 6)


RELATIVE_IMPORT_COMPOSITION = '''
import importlib


def load(name):
    return importlib.import_module('.leaf', 'pkg')
'''


def test_a_relative_constant_import_name_refuses_the_scan(_tmp):
    """A leading-dot constant needs its package argument read at runtime.

    Resolving it from the repository root instead would name a module
    nothing asked for, so the form is refused like any other unprovable
    name.
    """
    _write_tree(Path(_tmp), {
        'composition.py': RELATIVE_IMPORT_COMPOSITION,
        'pkg/__init__.py': '',
        'pkg/leaf.py': 'leaf = True\n'})
    try:
        _mcp_guard_floor.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError as raised:
        assert 'composition:6' in str(raised), raised
        assert 'cannot read statically' in str(raised), raised
    else:
        raise AssertionError('a relative constant import name was resolved '
                             'from the root')


def test_a_non_repo_local_import_is_skipped(_tmp):
    """Stdlib and site-package targets are provably not this repository's."""
    _write_tree(Path(_tmp), {'composition.py': FOREIGN_IMPORT_COMPOSITION})
    scanned = _mcp_guard_floor.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


TESTS_TREE_COMPOSITION = '''
from tests import helper
'''


def test_the_scan_set_drops_something_imported_from_tests(_tmp):
    """The tests/ filter is load-bearing on the closure.

    A repo module can name a module under tests/, and the closure walk
    would resolve it; the floor scans what the composition can import, and
    what a suite defines is somebody else's raise to declare.
    """
    _write_tree(Path(_tmp), {
        'composition.py': TESTS_TREE_COMPOSITION,
        'tests/__init__.py': '',
        'tests/helper.py': "raise ValueError('a suite raise')\n"})
    scanned = _mcp_guard_floor.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


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


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
