"""A per-process memo for the coverage guard's per-file analysis.

Not a suite itself — run_tests.py only loads `test_*.py`.

tests/test_coverage_environment.py scans the whole test tree once per
control, so a run analyses the same content over and over. The key is
the content, not the path, so a mutated copy of a real module is a
different key and cannot be served the unmutated answer.

`_analyze` also appends keep sites to the list it is handed, and the
allowlist reconciliation at the end of a scan reads that list. Storing
only the return value therefore drops every keep site a later scan would
have declared, and the reconciliation then reports allowlisted sites as
having no launch — so a hit replays the appends as well.
"""
_ANALYSES = {}


def analysed(analyze, relative, source, keeps):
    """`analyze(relative, source, keeps)`, computed once per content."""
    entry = _ANALYSES.get((relative, source))
    if entry is None:
        appended = []
        entry = (analyze(relative, source, appended), appended)
        _ANALYSES[(relative, source)] = entry
    violations, appended = entry
    keeps.extend(appended)
    return list(violations)
