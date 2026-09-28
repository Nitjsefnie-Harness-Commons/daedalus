"""The on-disk composition the import-closure suites hand the scan.

`composition_scan_set` reads a file tree, so every case in
`test_mcp_import_closure.py`, `test_mcp_import_values.py`,
`test_mcp_import_code_eval.py`, `test_mcp_import_selection.py` and
`test_mcp_selection_sweep.py` has to put a synthetic package on disk
before it can ask the scan anything. The tree writer, the refusal
assertion and the callee verdict were each a private `def` in more than
one of those suites, so a change to how a fixture is built reached some
of the cases and not others; they live here so there is one copy.

The refusal assertion is named for the assertion it makes rather than for
what it generically is, because `main` already binds a different
`_assert_refusal` in `test_js_lines.py`, `test_mcp_import_code_eval.py`
and `test_wfjobs.py`, and a shared helper that adopted that name would
make all three offenders of it (see `test_helper_reimplementation.py`).
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
