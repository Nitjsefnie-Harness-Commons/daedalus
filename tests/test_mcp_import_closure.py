#!/usr/bin/env python3
"""The MCP composition's import closure, on synthetic input.

The witness floor scans the modules the composition can import, so this is
where the closure itself is driven: what a spelling resolves to, and what a
spelling it cannot follow is refused for.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402


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
    executes, is still a module the composition can import. A runtime
    sys.modules snapshot would miss it and leave its raise sites
    undeclared and unwitnessed.
    """
    _write_tree(Path(_tmp), {
        'composition.py': LAZY_COMPOSITION,
        'pkg/__init__.py': '',
        'pkg/leaf.py': 'dead = False\n',
        'pkg/hidden.py': "raise ValueError('unwitnessed')\n"})
    scanned = _mcp_import_closure.composition_scan_set(
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
    """An import the walk cannot read statically fails the scan loudly."""
    _write_tree(Path(_tmp), {'composition.py': COMPUTED_IMPORT_COMPOSITION})
    try:
        _mcp_import_closure.composition_scan_set(
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


def _refuses(_tmp, source, site, phrase):
    """The scan refuses this composition source, naming the site and why."""
    _write_tree(Path(_tmp), {'composition.py': source})
    try:
        _mcp_import_closure.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError as raised:
        assert f'composition:{site}' in str(raised), raised
        assert phrase in str(raised), raised
    else:
        raise AssertionError('a computed import was silently skipped')


def _refuses_the_scan(_tmp, source, site):
    """The scan refuses this composition source, naming the import site."""
    _refuses(_tmp, source, site, 'cannot read statically')


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


def test_a_from_builtins_import_refuses_the_scan(_tmp):
    """`from builtins import __import__` binds the operation too.

    The alias is the spelling a reader reaches for when the bare builtin
    is shadowed; recognising only importlib's from-imports walks past the
    unprovable call silently.
    """
    _refuses_the_scan(_tmp, '''
from builtins import __import__ as bi


def load(name):
    return bi(name)
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
        _mcp_import_closure.composition_scan_set(
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
    scanned = _mcp_import_closure.composition_scan_set(
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
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


DOTTED_IMPORTLIB_COMPOSITION = '''
import importlib.util


def load(name):
    return importlib.import_module(name)
'''


def test_a_dotted_importlib_import_binds_the_operation(_tmp):
    """`import importlib.util` binds the name the operation is called on.

    The alias's dotted spelling is not the name the statement binds, so a
    scan matching the whole alias walks past a real import-by-name call and
    the closure drops whatever it loads.
    """
    _refuses(_tmp, DOTTED_IMPORTLIB_COMPOSITION, 6, 'cannot read statically')


def test_a_dotted_importlib_import_resolves_a_constant_name(_tmp):
    """The same spelling resolves a constant the map can read."""
    _write_tree(Path(_tmp), {
        'composition.py': '''
import importlib.util


def load():
    return importlib.import_module('pkg.leaf')
''',
        'pkg/__init__.py': '',
        'pkg/leaf.py': 'leaf = True\n'})
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    names = {path.relative_to(Path(_tmp)).as_posix() for path in scanned}
    assert names == {
        'composition.py', 'pkg/__init__.py', 'pkg/leaf.py'}, names


ASSIGNED_ALIAS_COMPOSITION = '''
import importlib


def load(name):
    loader = importlib
    return loader.import_module(name)
'''


def test_an_assigned_alias_of_a_bound_name_refuses_the_scan(_tmp):
    """`loader = importlib` hands the operation to a name the map cannot see.

    Naming the assignment is what a reader can go and change; the call it
    feeds reads as an ordinary attribute on an ordinary local.
    """
    _refuses(_tmp, ASSIGNED_ALIAS_COMPOSITION, 6, 'cannot follow')


def test_an_assignment_of_the_operation_refuses_the_scan(_tmp):
    _refuses(_tmp, '''
import importlib


def load(name):
    load_module = importlib.import_module
    return load_module(name)
''', 6, 'cannot follow')


def test_a_getattr_of_the_operation_refuses_the_scan(_tmp):
    _refuses(_tmp, '''
import importlib


def load(name):
    return getattr(importlib, 'import_module')(name)
''', 6, 'cannot follow')


def test_a_getattr_of_an_unknown_attribute_on_a_bound_name_refuses(_tmp):
    """A non-constant attribute read off a known operation is still one."""
    _refuses(_tmp, '''
import importlib


def load(name, attribute):
    return getattr(importlib, attribute)(name)
''', 6, 'cannot follow')


def test_ordinary_aliases_and_lookups_are_scanned_silently(_tmp):
    """The refusals are scoped to the import-by-name operation.

    A name rebound to an ordinary object, and a `getattr` for an ordinary
    attribute, are ordinary code; refusing them would refuse the closure's
    modules for writing Python.
    """
    _write_tree(Path(_tmp), {'composition.py': '''
import os


def flags():
    return getattr(os, 'O_BINARY', 0)


def reader(stream, name):
    stream = os.fdopen(0, 'rb')
    return getattr(stream, name, None)
'''})
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


REAL_COMPOSITION_SCAN_SET = [
    'daedalus_bridge/__init__.py',
    'daedalus_bridge/env_config.py',
    'daedalus_bridge/log_safe.py',
    'daedalus_cli/__init__.py',
    'daedalus_cli/output.py',
    'daedalus_cli/result_view.py',
    'daedalus_cli/transport.py',
    'daedalus_mcp/__init__.py',
    'daedalus_mcp/auth.py',
    'daedalus_mcp/request_guard.py',
    'daedalus_mcp/server.py',
    'daedalus_mcp/tools_cookies.py',
    'daedalus_mcp/tools_css.py',
    'daedalus_mcp/tools_eval.py',
    'daedalus_mcp/tools_hotfixes.py',
    'daedalus_mcp/tools_media.py',
    'daedalus_mcp/tools_network.py',
    'daedalus_mcp/tools_tabs.py',
    'daedalus_mcp/transport.py',
]


def test_the_real_composition_scan_set_is_pinned(_tmp):
    """The composition's closure as data, compared both ways.

    A module joining or leaving the import graph is a change in what the
    floor scans, and every site it declares moves with it; held as a walk's
    own answer it would change silently with the code that changed it.
    """
    scanned = _mcp_import_closure.composition_scan_set(
        _util.ROOT / 'daedalus_mcp' / 'server.py', _util.ROOT)
    assert [path.relative_to(_util.ROOT).as_posix() for path in scanned] \
        == REAL_COMPOSITION_SCAN_SET, (
            'the composition scan set changed; a module entered or left the '
            'closure — re-read it and update REAL_COMPOSITION_SCAN_SET if the '
            'change is right')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
