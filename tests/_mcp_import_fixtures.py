"""The on-disk composition handed to the import-closure scan.

`composition_scan_set` reads a file tree, so a case that drives it must
put a synthetic package on disk before it can ask the scan anything. The
refusal assertion is what `test_helper_assertion_pins.py` drives it with:
that suite pins every assertion a shared helper makes on its callers'
behalf, so the case there needs a real composition the scan refuses for a
real reason, not a stub of the call. The tree writer it shares carries
that case and the closure limits in `test_mcp_guard_floor.py`, so a change
to how a fixture is built cannot reach one and miss the other.

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
