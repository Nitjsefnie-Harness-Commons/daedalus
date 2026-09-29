"""Every `ast.Call` a reached shared-helper scope owes, and does not get.

Not a suite itself — run_tests.py only loads `test_*.py`.

The invariant has no kind in it: every `ast.Call` inside a reached
top-level scope of a resolved `tests/_*.py` module is judged. The two
sides are computed by two different vocabularies, because computing them
by the same one makes them agree whatever the tree contains: a version
of this file gated the owed side on `isinstance(node, (ast.FunctionDef,
ast.AsyncFunctionDef))` and recorded the judged side with the guard's own
`_nested_scope_expressions`, so a class root's header was absent from
both and the diff read 0 on a tree with 105 holes.

- the OWED side walks the AST: the reach finds the top-level scopes, and
  `ast.walk` enumerates `ast.Call` under each with no `isinstance` and no
  helper of the guard's, so it cannot inherit the assembly's blind spot;
- the JUDGED side wraps `call_judgement` — the one point every call the
  guard reaches a decision about passes through. Not `_call_violation`,
  which a call that defers to a shared helper never reaches, and wrapping
  that would blind this side to exactly the calls the deferral hides.

If you can name a function both sides call, the diff can only confirm
that function. They share none.

The diff is ONE-DIRECTIONAL: it reports a site the reach owes and the
guard does not judge, and it cannot see a site the guard judges that this
check never owed — the permissive direction, and the one a blind check
fails in. Both halves derive from the same reach, so a root kind the
reach does not admit is outside this check's domain on BOTH sides rather
than invisible in one.
"""
import ast
import sys
from pathlib import Path

from _imported_calls import module_scopes, reached_functions


def _reached_roots(entry, tree):
    """The top-level scopes an entry reaches, and nothing else.

    The reach is the premise of the claim, not the thing under test, so
    it comes from the resolver, and the answer is one question — is this
    node's name reached — which is the question the assembly asks too. The
    REGION each root owes is the whole subtree, and that is where no kind
    is consulted.
    """
    names = reached_functions(entry, module_scopes(tree))
    return [node for node in tree.body
            if getattr(node, 'name', None) in names]


def _call_sites(label, tree, entry):
    """(file, line, column) of every call inside a reached root."""
    sites = set()
    for root in _reached_roots(entry, tree):
        for node in ast.walk(root):
            if isinstance(node, ast.Call):
                sites.add((label, node.lineno, node.col_offset))
    return sites


def unjudged_sites(root, controls):
    """The un-judged calls under `root`, one scan of every control.

    Returns a sorted list of (file, line, column), each a site the guard
    reaches and does not judge.
    """
    import _control_writes
    from _control_writes import _ModuleJudgement

    judged, resolved = set(), []

    def watch_call(node, label, names, root):
        judged.add((label, node.lineno, node.col_offset))
        return _CALL[0](node, label, names, root)

    def watch_import(self, name, seeding):
        imported = self.resolver.imported(self.names, name)
        if imported is not None:
            resolved.append((imported.module.label, imported.module.tree,
                             imported.function))
        return _IMPORT[0](self, name, seeding)

    _CALL = [_control_writes.call_judgement]
    _IMPORT = [_ModuleJudgement._judge_imported]
    _control_writes.call_judgement = watch_call
    _ModuleJudgement._judge_imported = watch_import
    try:
        for control in controls:
            _control_writes.control_write_violations(root / control, root)
    finally:
        _control_writes.call_judgement = _CALL[0]
        _ModuleJudgement._judge_imported = _IMPORT[0]

    owed = set()
    for label, tree, entry in resolved:
        owed |= _call_sites(label, tree, entry)
    return sorted(owed - judged)


def main(root):
    root = Path(root)
    controls = sorted(str(path.relative_to(root))
                      for path in (root / 'tests').glob('test_*.py'))
    unjudged = unjudged_sites(root, controls)
    for label, line, _ in unjudged:
        print(f'  UNJUDGED {label}:{line}')
    print(f'\n{len(unjudged)} un-judged calls under {len(controls)} controls')
    return unjudged


if __name__ == '__main__':
    sys.exit(1 if main(sys.argv[1]) else 0)
