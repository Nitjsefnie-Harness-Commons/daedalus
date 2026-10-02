"""The on-disk compositions handed to the import-closure scan.

`composition_scan_set` reads a file tree, so a case that drives it must
put a synthetic package on disk before it can ask the scan anything. The
refusal assertion is what `test_helper_assertion_pins.py` drives it with:
that suite pins every assertion a shared helper makes on its callers'
behalf, so the case there needs a real composition the scan refuses for a
real reason, not a stub of the call.

Two readers live here, one per question a case asks of the walk, so a
change to how a fixture is built cannot reach one consumer and miss
another: `_assert_scan_refusal` for the arms that REFUSE, and
`_scan_verdict` for a tree whose answer is read whichever way it falls.
`test_mcp_import_refusals.py` carries the enumeration those readers are
driven from, and `test_helper_assertion_pins.py` drives the first of them.
The CALLEE verdict is not a third reader: it is `_scan_verdict`'s answer in
three words over a tree the case writes, and it lives with the two cases
that read it, which is all of them once the synthetic-tree suites are gone.

The refusal assertion is named for the assertion it makes rather than for
what it generically is, because other test modules already bind that
name, and a shared helper that adopted a name other modules bind is a
re-implementation `tests/test_reserved_test_names.py` detects —
generically over `UNCONSOLIDATED_NAMES`, so no count of the offenders is
asserted anywhere.
"""
from pathlib import Path

import _mcp_import_closure


def _write_tree(directory, files):
    for name, source in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')


def _assert_scan_refusal(_tmp, source, site, phrase):
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


def _scan_verdict(_tmp, files):
    """`('refused', message)` or `('clean', names)` for a written tree.

    One reader for both directions, so a control states the arm it enters
    and the neighbour it must leave alone with the same call and one shape
    of answer. `names` are the tree's repo-local files relative to it, so
    a resolved module is told apart from a silence.
    """
    _write_tree(Path(_tmp), files)
    try:
        scanned = _mcp_import_closure.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError as refused:
        return 'refused', str(refused)
    return 'clean', sorted(path.relative_to(Path(_tmp)).as_posix()
                           for path in scanned)
