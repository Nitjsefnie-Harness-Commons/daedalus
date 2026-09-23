#!/usr/bin/env python3
"""The value side of a store, and the ordinary code that must stay silent.

A store is refused when the value it receives can be the import-by-name
operation, and a value is read into rather than matched one level deep. The
control at the end is what keeps that structural read from turning into a
blanket refusal: every store form and every value shape the walk reads
appears there bound to an ordinary value.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402


def _write_tree(directory, files):
    for name, source in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')


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


def test_a_conditional_alias_refuses_the_scan(_tmp):
    """`loader = importlib if flag else None` is an ordinary refactor.

    The operation sits in one arm of a conditional, so a value classifier
    that reads one level deep walks straight past the store.
    """
    _refuses(_tmp, '''
import importlib


def load(name, flag):
    loader = importlib if flag else None
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_a_boolean_choice_alias_refuses_the_scan(_tmp):
    _refuses(_tmp, '''
import importlib


def load(name, flag):
    loader = importlib and flag
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_a_comparison_alias_refuses_the_scan(_tmp):
    """A comparison can only hold a boolean, and is refused anyway.

    The value is provably not the operation, so this is the over-refusal
    direction: the resolver reads a wrapper uniformly rather than special-
    casing the ones whose result type is known.
    """
    _refuses(_tmp, '''
import importlib


def load(name, flag):
    loader = importlib == flag
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_a_subscripted_container_alias_refuses_the_scan(_tmp):
    _refuses(_tmp, '''
import importlib


def load(name):
    loader = {'k': importlib}['k']
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_a_starred_value_alias_refuses_the_scan(_tmp):
    _refuses(_tmp, '''
import importlib


def load(name):
    loader, = (*[(importlib,)],)
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_a_container_element_alias_refuses_the_scan(_tmp):
    _refuses(_tmp, '''
import importlib


def load(name):
    loader = (importlib, 1)[0]
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_a_comprehension_value_alias_refuses_the_scan(_tmp):
    _refuses(_tmp, '''
import importlib


def load(name):
    loader = [importlib][0]
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_a_walrus_inside_a_value_refuses_the_scan(_tmp):
    _refuses(_tmp, '''
import importlib


def load(name):
    loader = [(found := importlib)][0]
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_ordinary_aliases_and_lookups_are_scanned_silently(_tmp):
    """The refusals are scoped to the import-by-name operation.

    A name rebound to an ordinary object, and a `getattr` for an ordinary
    attribute, are ordinary code; refusing them would refuse the closure's
    modules for writing Python. Every store form and every value shape the
    walk now reads appears here bound to an ordinary value, because the
    structural fix is only safe while ordinary code stays silent: an
    over-broad classifier is caught by name in this one case.
    """
    _write_tree(Path(_tmp), {'composition.py': '''
import contextlib
import importlib
import os


class Holder:
    kind = 'holder'

    def store(self, handle):
        self.loader = handle
        self.table = {}
        return self.loader


async def stream(handle, table):
    self_attr = handle
    for entry in (handle,):
        self_attr = entry
    async with contextlib.suppress(OSError):
        self_attr = handle
    async def inner():
        return self_attr
    return await inner()


def flags():
    return getattr(os, 'O_BINARY', 0)


def reader(stream, name):
    stream = os.fdopen(0, 'rb')
    return getattr(stream, name, None)


def ordinary(handle, table=()):
    (first, second) = (handle, handle)
    [third] = [handle]
    first, *rest = (handle, 1, 2)
    (found := handle)
    for entry in (handle,):
        first = entry
    with contextlib.suppress(OSError):
        second = handle
    rows = [item for item in (handle,)]
    try:
        handle.read()
    except OSError as failure:
        return first, second, third, rest, found, rows, failure
    return table


def shapes(handle, flag, table):
    conditional = handle if flag else None
    choice = handle and flag
    compared = handle == flag
    indexed = {'k': handle}['k']
    starred = (*[handle],)
    listed = [handle][0]
    counted = (handle, 1)[0]
    nested = [(found := handle)][0]
    joined = 'x'.join((handle,))
    made = len((handle,))
    grown = flag
    grown += 1
    del grown
    renamed = table
    renamed = Holder
    submodules = importlib.util
    return (conditional, choice, compared, indexed, starred, listed, counted,
            nested, joined, made, grown, renamed, submodules, rows_of(handle))


def rows_of(handle):
    match handle:
        case [item]:
            return item
    return [item for item in (handle,) if item]
'''})
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned

if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
