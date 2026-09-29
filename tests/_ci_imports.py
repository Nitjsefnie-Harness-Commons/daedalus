"""Which of the CI scripts select an import on how they were loaded.

Not a suite itself — run_tests.py only loads `test_*.py`. A module that
branches on `__package__` has two spellings, and only a package import
reaches the first. Every such module is therefore required to be
reachable from a package import: promoted itself, or imported by a
module that is. The requirement is derived from the tree rather than
declared, so a promotion cannot be deleted without the derivation
noticing — which is the whole point, since a hand-maintained list is a
promise and this is a consequence.
"""
import ast
from pathlib import Path

SCRIPTS = 'scripts'


def _modules(root):
    """Every dotted module name under `scripts/`, with its parsed tree."""
    for path in sorted((Path(root) / SCRIPTS).rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        yield relative[:-3].replace('/', '.'), ast.parse(
            path.read_text(encoding='utf-8'))


def _selectors(tree):
    """The `if __package__:` arms of a tree, empty when it has none."""
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == '__package__']


def _imports_both_arms(own, selector):
    """Every name a module's two arms can import, package-relative ones
    resolved against the module's own package.

    `from .yamlanchor import ...` inside `scripts.ci.workflow_yaml` names
    `scripts.ci.yamlanchor`, not `scripts.ci.workflow_yaml.yamlanchor`:
    the leading dot means the package the module sits IN.
    """
    names = set()
    for arm in (selector.body, selector.orelse):
        for node in ast.walk(ast.Module(body=arm, type_ignores=[])):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if not node.level:
                    if node.module:
                        names.add(node.module)
                    continue
                parts = own.split('.')
                base = '.'.join(parts[:max(0, len(parts) - node.level)])
                if node.module:
                    names.add(f'{base}.{node.module}')
                else:
                    names.update(f'{base}.{alias.name}'
                                 for alias in node.names)
    return names


def package_selectors(root):
    """Every module under `scripts/` that branches on `__package__`."""
    return sorted(name for name, tree in _modules(root) if _selectors(tree))


def package_edges(root):
    """What each selector can import down its `__package__` arm, both
    arms together, because both spellings are reachable somewhere."""
    return {name: {target for selector in _selectors(tree)
                   for target in _imports_both_arms(name, selector)
                   if target.startswith(f'{SCRIPTS}.')}
            for name, tree in _modules(root) if _selectors(tree)}


def package_closure(promoted, edges):
    """Every module a package import of `promoted` reaches, transitively."""
    reached, frontier = set(), sorted(promoted)
    while frontier:
        name = frontier.pop()
        if name in reached:
            continue
        reached.add(name)
        frontier.extend(edges.get(name, ()))
    return reached
