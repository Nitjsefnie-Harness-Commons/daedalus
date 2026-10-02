"""The on-disk compositions handed to the import-closure scan.

`composition_scan_set` reads a file tree, so a case that drives it must
put a synthetic package on disk before it can ask the scan anything. The
refusal assertion is what `test_helper_assertion_pins.py` drives it with:
that suite pins every assertion a shared helper makes on its callers'
behalf, so the case there needs a real composition the scan refuses for a
real reason, not a stub of the call.

Three readers live here, one per question a case asks of the walk, so a
change to how a fixture is built cannot reach one consumer and miss
another: `_assert_scan_refusal` for the arms that REFUSE, `_scan_verdict`
for a tree whose answer is read whichever way it falls, and `_callee_scan`
for the callee spellings whose answer is a resolved module, a refusal, or
silence. `test_mcp_import_refusals.py` carries the enumeration those
readers are driven from, and `test_helper_assertion_pins.py` drives the
first of them.

The refusal assertion is named for the assertion it makes rather than for
what it generically is, because other test modules already bind that
name, and a shared helper that adopted a name other modules bind is a
re-implementation `test_helper_reimplementation.py` detects — generically
over `UNCONSOLIDATED_NAMES`, so no count of the offenders is asserted
anywhere.
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


def _callee_scan(_tmp, callee):
    """`resolved`, `refused`, or `silent` for one callee, with a resolvable
    `pkg/leaf.py` on disk so a resolved value is told apart from a silence."""
    _write_tree(Path(_tmp), {
        'pkg/__init__.py': '', 'pkg/leaf.py': 'leaf = True\n',
        'composition.py': ('\nimport importlib\n\n\ndef load(c, i):\n'
                           f'    return {callee}("pkg.leaf")\n')})
    try:
        scanned = _mcp_import_closure.composition_scan_set(
            Path(_tmp) / 'composition.py', _tmp)
    except AssertionError:
        return 'refused'
    names = {path.relative_to(Path(_tmp)).as_posix() for path in scanned}
    return 'resolved' if 'pkg/leaf.py' in names else 'silent'
