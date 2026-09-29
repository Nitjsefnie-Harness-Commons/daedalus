"""Every `ast.Call` a reached shared-helper scope owes, and does not get.

Not a suite itself — run_tests.py only loads `test_*.py`.

The invariant is one sentence with no kind in it: **every `ast.Call`
inside a reached top-level scope of a resolved `tests/_*.py` module is
judged.** The two sides are computed by two different vocabularies,
because the first version of this scan was circular — its owed side and
its judged side both went through `_nested_scope_expressions`, and its
owed side was gated on `isinstance(node, (ast.FunctionDef,
ast.AsyncFunctionDef))` — so a class root's header was absent from both
and the diff read 0 on a tree with 53 holes in it.

- the OWED side walks the AST: find the reached top-level scopes with
  the reach, then enumerate `ast.Call` under each with `ast.walk` and
  nothing else. No `isinstance`, no `_nested_scope_expressions`, no
  helper of the guard's, so it cannot inherit the assembly's blind spot;
- the JUDGED side records what the guard inspects, by wrapping
  `call_judgement` — the one point every call the guard reaches a
  decision about passes through. Not `_call_violation`, which a call
  that defers to a shared helper never reaches, so wrapping that would
  blind this side to exactly the calls the deferral hides.

If you can name a function both sides call, the diff can only confirm
that function. They share none.
"""
import ast
import sys
from pathlib import Path

from _imported_calls import module_scopes, reached_functions

SCOPE_KINDS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _reached_roots(entry, tree):
    """The top-level scopes an entry reaches, and nothing else.

    The reach is the premise of the claim, not the thing under test, so
    it comes from the resolver. The REGION each root owes is the whole
    subtree, and that is where no kind is consulted.
    """
    names = reached_functions(entry, module_scopes(tree))
    return [node for node in tree.body
            if node.__class__ in SCOPE_KINDS and node.name in names]


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


def verify(root, controls, sites):
    """Plant a checkout write at each site and ask the guard to name it.

    The reduction, and why it is closed: the question is whether the site
    is JUDGED, and a site is judged the same way whichever control
    reached the helper that holds it — the call is inside a scope the
    helper's own seeding fixes, and the planted target is `ROOT`, which
    no seeding can prove owned, so the answer is a violation or nothing
    and never depends on the caller. One control per site is therefore
    the whole question, and it is the control that first resolved the
    helper the site is in.
    """
    import _control_writes
    from _control_writes import _ModuleJudgement

    resolved = {}

    def watch_import(self, name, seeding):
        imported = self.resolver.imported(self.names, name)
        if imported is not None:
            resolved.setdefault(imported.module.label, self.label)
        return _IMPORT[0](self, name, seeding)

    _IMPORT = [_ModuleJudgement._judge_imported]
    _ModuleJudgement._judge_imported = watch_import
    try:
        for control in controls:
            _control_writes.control_write_violations(root / control, root)
    finally:
        _ModuleJudgement._judge_imported = _IMPORT[0]

    named, silent = [], []
    for label, line, _ in sites:
        helper = root / label
        text = helper.read_text(encoding='utf-8')
        original = text
        control = resolved.get(label)
        before = set(_control_writes.control_write_violations(
            root / control, root)) if control else set()
        indented = (' ' * 4) + "(ROOT / '.unjudged_probe').write_text('p')\n"
        helper.write_text('\n'.join(text.split('\n')[:line]
                                    + [indented]
                                    + text.split('\n')[line:]),
                          encoding='utf-8')
        try:
            found = False
            if control is not None:
                # A NEW violation that NAMES THE HELPER'S FILE. A new
                # violation that does not is the guard noticing the
                # plant broke the file, which is a refusal and not a
                # judgement of the site.
                found = any(message.startswith(label) for message in
                            set(_control_writes.control_write_violations(
                                root / control, root)) - before)
        finally:
            helper.write_text(original, encoding='utf-8')
        (named if found else silent).append((label, line))
    return named, silent


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
