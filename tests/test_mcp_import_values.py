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


def _scans_silently(_tmp, source):
    """The scan returns normally: this spelling is a declared limit."""
    _write_tree(Path(_tmp), {'composition.py': source})
    return _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)


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


def test_a_lambda_delivery_alias_refuses_the_scan(_tmp):
    """`get = lambda: importlib` hands the operation over on a later call.

    The store's value is not a call and not a module; it is a callable that
    returns one, and the call it is called with comes later, spelled on an
    expression this walk has already passed.
    """
    _refuses(_tmp, '''
import importlib


def load(name):
    get = lambda: importlib
    return get().import_module(name)
''', 6, 'cannot follow')


def test_a_lambda_returning_the_operation_refuses_the_scan(_tmp):
    _refuses(_tmp, '''
import importlib


def load(name):
    get = lambda: importlib.import_module
    return get()(name)
''', 6, 'cannot follow')


def test_a_yield_delivery_alias_refuses_the_scan(_tmp):
    """`loader = (yield importlib)` delivers on a later `send`.

    Deferred delivery, not a call, and the spelling that resumes it is
    written after the store the scan has already read.
    """
    _refuses(_tmp, '''
import importlib


def load(name):
    loader = (yield importlib)
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_an_attribute_over_a_comprehension_base_refuses_the_scan(_tmp):
    """`loader = [importlib][0].import_module` is `importlib.import_module`.

    The round's own comprehension case refuses `loader = [importlib][0]`;
    adding the operation's own attribute turns the same tracked name into
    the operation itself, and a base that is not a bare Name must be read
    through its own children rather than skipped.
    """
    _refuses(_tmp, '''
import importlib


def load(name):
    loader = [importlib][0].import_module
    return loader(name)
''', 6, 'cannot follow')


def test_an_attribute_over_a_subscript_key_base_refuses_the_scan(_tmp):
    """A tracked name under a subscript, then the operation's attribute.

    `table[importlib].import_module` reads the operation's own name off a
    base the two-member match cannot see, so the base's children decide.
    """
    _refuses(_tmp, '''
import importlib


def load(name, table):
    loader = table[importlib].import_module
    return loader(name)
''', 6, 'cannot follow')


def test_an_attribute_over_a_conditional_base_refuses_the_scan(_tmp):
    """A tracked name in one arm of a conditional, under the attribute.

    The base is not a bare Name, so the arm must descend into it; the
    tracked name is in the `IfExp` and the store is refused.
    """
    _refuses(_tmp, '''
import importlib


def load(name, flag):
    loader = (importlib if flag else None).import_module
    return loader(name)
''', 6, 'cannot follow')


def test_an_attribute_over_a_boolean_base_refuses_the_scan(_tmp):
    """A tracked name in one value of a `BoolOp`, under the attribute.

    Same mechanism as the conditional: the base is a `BoolOp`, not a Name,
    and the tracked name sits in its children.
    """
    _refuses(_tmp, '''
import importlib


def load(name):
    loader = (0 or importlib).import_module
    return loader(name)
''', 6, 'cannot follow')


def test_a_key_position_mention_refuses_the_scan(_tmp):
    """A tracked name used as a subscript key is refused, over-refusing.

    The result of `table[importlib]` is a lookup, not the operation, so
    this is the safe direction taken on purpose: the predicate reads one
    rule over the whole subtree rather than a hand-picked set of children
    per node type.
    """
    _refuses(_tmp, '''
import importlib


def load(name, table):
    loader = table[importlib]
    return loader.import_module(name)
''', 6, 'cannot follow')


def test_an_attribute_over_a_call_base_holds_at_the_call_limit(_tmp):
    """`(lambda: importlib)().import_module` is the declared call limit.

    The base is a call, and a call's result is followed by neither the map
    nor these refusals; a name bound to it is the accepted residual, not a
    new hole. Pinned here so the boundary is read off a real shape rather
    than assumed, and so closing it later is a deliberate change.
    """
    scanned = _scans_silently(_tmp, '''
import importlib


def load(name):
    loader = (lambda: importlib)().import_module
    return loader(name)
''')
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


def test_an_attribute_over_a_builtin_import_call_holds_at_the_call_limit(
        _tmp):
    """`__import__('importlib').import_module` is the same call limit.

    The main walk reads the `__import__` call as a dynamic import of a
    constant name, but the operation's own name reached on its RESULT is
    the call limit again — a name bound to a call's result. Named here
    because it needs no `importlib` import at all and so is the nearest
    spelling to a real one.
    """
    scanned = _scans_silently(_tmp, '''
def load(name):
    loader = __import__('importlib').import_module
    return loader(name)
''')
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


def test_a_bare_call_over_a_wrapped_base_is_read_as_dynamic(_tmp):
    """A dynamic-import call whose base is not a bare Name is resolved.

    `[importlib][0].import_module(name)` is a dynamic import at runtime.
    The main walk classifies a call's callee through the same base reading
    the store side uses; when it only recognised a bare-Name base, the name
    this call loaded was dropped from the scan set in silence.
    """
    _refuses(_tmp, '''
import importlib


def load(name):
    return [importlib][0].import_module(name)
''', 6, 'cannot read statically')


def test_a_string_literal_naming_the_operation_is_the_declared_limit(_tmp):
    """`sys.modules['importlib'].import_module` is a declared limit.

    The walk tracks names, not strings, so an operation reached through a
    string literal that NAMES it — `sys.modules['importlib']` or
    `importlib.__dict__['import_module']` — is accepted, not refused. This
    is a DISCLOSED limit, not a silent hole: the module's opening claim
    names it, and this case pins that it is still accepted, so a future
    change that starts refusing it is a deliberate one that reopens the
    disclosure. The mechanism is not recognised here on purpose.
    """
    scanned = _scans_silently(_tmp, '''
import sys


def load(name):
    loader = sys.modules['importlib'].import_module
    other = __import__('importlib').__dict__['import_module']
    return loader(name), other
''')
    assert scanned == [(Path(_tmp) / 'composition.py').resolve()], scanned


def test_ordinary_aliases_and_lookups_are_scanned_silently(_tmp):
    """The refusals are scoped to the import-by-name operation.

    A name rebound to an ordinary object, and a `getattr` for an ordinary
    attribute, are ordinary code; refusing them would refuse the closure's
    modules for writing Python. Every store form and every value shape the
    walk now reads appears here bound to an ordinary value — including the
    deferred-delivery shapes and the two declared limits — because the
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
    loaded = load_ordinary(handle)
    table['k'] = handle
    return (conditional, choice, compared, indexed, starred, listed, counted,
            nested, joined, made, grown, renamed, submodules, loaded,
            rows_of(handle), attribute_bases(handle, flag, table))


def load_ordinary(handle):
    get = lambda: handle
    produced = (yield handle)
    return get(), produced


def attribute_bases(handle, flag, table):
    """The operation's own name read off a base that does not mention it.

    Each of these is an ordinary attribute, because the walk reads the base
    before it decides the node is the operation: `import_module` on a
    subscript, a comprehension, a conditional, a boolean choice, a call's
    result and a subscript key is still an ordinary attribute of an ordinary
    object.
    """
    looked_up = table['k'].import_module
    listed = [handle][0].__import__
    chosen = (handle if flag else None).import_module
    combined = (flag and handle).__import__
    returned = load_ordinary(handle).import_module
    keyed = table[handle].import_module
    return (looked_up, listed, chosen, combined, returned, keyed)


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
