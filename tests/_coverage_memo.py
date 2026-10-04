"""What the coverage guard computes once and reads many times.

Not a suite itself — run_tests.py only loads `test_*.py`.

The coverage guard scans the whole test tree once per
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
same grounds the kept node list is: the tree is read, not edited. The map
is strong-keyed and `release_bound_census` empties it as each analysis
begins, which is what bounds it to the tree under analysis.

`node_types` is the third thing held here rather than in a guard module:
the set of node types the running grammar defines, which every arm chain
across these three modules wants and which one enumeration answers for all
of them.
"""
import ast
import weakref

_ANALYSES = {}
# Weak keys, so a module's nodes go when the tree does. The stored list
# leaves the root out because a value holding a strong reference to its
# own weak key keeps that entry alive for the life of the process.
_BELOW = weakref.WeakKeyDictionary()
# The same, keyed on the node itself — and NOT weakly, because a weak-key
# map builds and tears down a reference per node and that measured more
# expensive than the question the cache answers. `release_bound_census`
# is what makes a strong key safe, and it is load-bearing rather than
# tidiness: it is why this map is never wider than the tree being read.
_BOUNDS = {}


def nodes(tree):
    """Every node of `tree`, in the order `ast.walk` yields them."""
    below = _BELOW.get(tree)
    if below is None:
        below = list(ast.walk(tree))[1:]
        _BELOW[tree] = below
    return [tree] + below


def node_types(*declined):
    """Every node class this interpreter defines, bar the `declined` ones.

    A guard arm chain is a sequence of `isinstance` tests whose arms cannot
    both match, and almost every node falls through all of them. Naming
    the types no arm can match turns that fall-through into one lookup
    over a set built here once. The answer is a set of exact types, so a
    subclass of a named arm still reaches the chain and is judged by it;
    and a grammar that adds a form is a type this does not name, so it
    takes the chain exactly as it did before.
    """
    types, pending = {ast.AST}, [ast.AST]
    while pending:
        for subclass in pending.pop().__subclasses__():
            if subclass not in types:
                types.add(subclass)
                pending.append(subclass)
    return frozenset(types) - {form for form in declined if form is not None}


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


def release_bound_census():
    """Drop every cached name, so a finished analysis keeps no node alive.

    Called where an analysis begins rather than where it ends, so a tree
    that raised still leaves nothing behind once the next one starts, and
    so the map is never wider than the tree being read.
    """
    _BOUNDS.clear()


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
