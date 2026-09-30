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

`_bound_census` caches the other kind: one node's answer to a question that
does not mention the file, the path or the analyser, so its key is the
node alone. The aggregate shadow set, the per-scope shadow set and the
binding destinations each ask every node that same question, and a node
they do not share is not a node one of them has not already walked. The
value is the set every caller read, so a caller that mutated it would
change what the next one is served; that is why the stored answer is a
frozenset. A caller that mutates a node BETWEEN two analyses — changing a
Name's context, an alias's asname — is served the earlier answer, on the
same grounds the kept node list is: the tree is read, not edited.
"""
import ast
import weakref

_ANALYSES = {}
# Weak keys, so a module's nodes go when the tree does. The stored list
# leaves the root out because a value holding a strong reference to its
# own weak key keeps that entry alive for the life of the process.
_BELOW = weakref.WeakKeyDictionary()
# The same, keyed on the node itself: a node is held by the tree that owns
# it, so the same entry dies with the same tree and no analysis can pin a
# tree it did not already hold.
_BOUNDS = weakref.WeakKeyDictionary()


def nodes(tree):
    """Every node of `tree`, in the order `ast.walk` yields them."""
    below = _BELOW.get(tree)
    if below is None:
        below = list(ast.walk(tree))[1:]
        _BELOW[tree] = below
    return [tree] + below


def _bound_census(node, compute):
    """`compute(node)`, computed once per node, as a frozenset.

    A miss stores a frozenset rather than the set `compute` returned,
    because every pass that reads a hit shares this one object and one of
    them mutating it would change what the rest are served.
    """
    names = _BOUNDS.get(node)
    if names is None:
        names = frozenset(compute(node))
        _BOUNDS[node] = names
    return names


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
