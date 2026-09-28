#!/usr/bin/env python3
"""What counts as a SECOND binding of a name, and what an add may override.

`tests/test_launch_census_boundaries.py` holds the shapes the census
discharges and refuses at its own boundary. This module holds the one
question those shapes keep asking — how many times a name is bound, and
whether an add may be honoured when a second binding exists — because the
answer is a rule in `tests/_receiver_resolution.py::_dotted_bindings` and
the rule is where a false claim does the most damage.

Every row runs with the import PRESENT, so a `REFUSED` is a real verdict
and not an artefact of there being nothing to discharge.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _launch_census import _faults  # noqa: E402
import _util  # noqa: E402


IMPORT_ALIAS_ROWS = {
    'H1 a from-import alias as the name, refused': (
        'from subprocess import run as urlopen\nimport urllib.request\n\n\n'
        'def g():\n    urlopen = urllib.request.urlopen\n\n\n'
        'def run_gate(cmd):\n    return urlopen(cmd, timeout=10)\n',
        'REFUSED'),
    'the same with a module alias, refused': (
        'import subprocess as urlopen\nimport urllib.request\n\n\n'
        'def g():\n    urlopen = urllib.request.urlopen\n\n\n'
        'def run_gate(cmd):\n    return urlopen(cmd, timeout=10)\n',
        'REFUSED'),
    'a from-import beside an add to the SAME path, DISCHARGED': (
        'from urllib.request import urlopen\nimport urllib.request\n\n\n'
        'def g(url):\n    urlopen = urllib.request.urlopen\n'
        '    return urlopen(url, timeout=10)\n',
        'DISCHARGED'),
    'a lone add with no import, DISCHARGED': (
        'import urllib.request\n\n\n'
        'def g(url):\n    _open = urllib.request.urlopen\n'
        '    return _open(url, timeout=10)\n',
        'DISCHARGED'),
}


def test_an_import_of_the_same_name_counts_unless_it_matches(tmp):
    """The second binding `_rebindings` excludes by design: imports.

    They live in the import pass's table, so the single-binding count
    never saw one and a lone function-local add looked alone. An import of
    the same name is now a second binding, and a REFUSAL unless it
    resolves to the same dotted path the add does — the same object, not a
    second one. The last two rows are the direction this must not move.
    """
    del tmp
    for label, (source, expected) in IMPORT_ALIAS_ROWS.items():
        tree = ast.parse(source)
        in_path = frozenset(
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        rows = sorted((r[1], r[2]) for r in _faults('p.py', tree, in_path))
        assert ('REFUSED' if rows else 'DISCHARGED') == expected, (
            label, rows)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchcensusadds_')


if __name__ == '__main__':
    raise SystemExit(main())
