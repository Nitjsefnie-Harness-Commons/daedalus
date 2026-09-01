"""What the coverage guard computes once and reads many times.

Not a suite itself — run_tests.py only loads `test_*.py`.

tests/test_coverage_environment.py scans the whole test tree once per
control, so a run analyses the same file over and over. The key is the
analyser, the path and the content together: a mutated copy of a real
module cannot be served the unmutated answer, two files carrying one
content are each reported under their own path, and a caller that swaps
the analyser gets that analyser's verdict rather than the first one's.

What the key cannot see is a caller that changes guard BEHAVIOUR without
changing any of the three — patching `_ROOT_MODULES` or another global
`_analyze` reads. Such a caller must not rely on a warm memo: there is
no invalidation hook and the memo is not the place for one.

`_analyze` also appends keep sites to the list it is handed, and the
allowlist reconciliation at the end of a scan reads that list. Storing
only the return value therefore drops every keep site a later scan would
have declared, and the reconciliation then reports allowlisted sites as
having no launch — so a hit replays the appends as well.

Within one analysis, pass after pass wants every node of the module.
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
    """`analyze(relative, source, keeps)`, computed once per key."""
    key = (analyze, relative, source)
    entry = _ANALYSES.get(key)
    if entry is None:
        appended = []
        entry = (analyze(relative, source, appended), appended)
        _ANALYSES[key] = entry
    violations, appended = entry
    keeps.extend(appended)
    return list(violations)
