"""What the coverage guard computes once and reads many times.

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

Within one analysis, fifteen passes each want every node of the module.
`nodes` walks it once and hands the same list to all of them.
"""
import ast
import weakref

_ANALYSES = {}
# Weak keys, so a module's nodes go when the tree does. The stored list
# leaves the root out because a value holding a strong reference to its
# own weak key keeps that entry alive for the life of the process.
_BELOW = weakref.WeakKeyDictionary()


def nodes(tree):
    """Every node of `tree`, in the order `ast.walk` yields them."""
    below = _BELOW.get(tree)
    if below is None:
        below = list(ast.walk(tree))[1:]
        _BELOW[tree] = below
    return [tree] + below


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
