"""The MCP composition's static import closure, and what it refuses.

The refusal witness floor scans the modules the composition can import, so
this walk is what makes that set closed: every `import` and `from` at any
depth resolves to a repository file or is provably elsewhere, and a dynamic
import is either read or refused by name and line. What it cannot follow is
refused too — a spelling that hands the import-by-name operation to a name
the map does not track would leave the closure quietly short of the modules
that composition can reach.
"""
import ast
from pathlib import Path


DYNAMIC_ATTRIBUTES = ('import_module', '__import__')

# The remedy every refusal names, and the principle it rests on.
CLOSURE_TAIL = ('; import it normally or pass an absolute constant, because '
                'an import closure that silently skips a module it cannot '
                'resolve is not closed')


def dotted_module(path, root):
    """The module's name relative to the repository root: two modules that
    share a stem are different modules, each keyed on its own."""
    return '.'.join(
        path.resolve().relative_to(Path(root).resolve()).with_suffix('')
        .parts)


def _refuse(path, root, node, detail):
    """Refuse a spelling the closure cannot follow, naming module and line."""
    raise AssertionError(
        f'{dotted_module(path, root)}:{node.lineno}: {detail}{CLOSURE_TAIL}')


def composition_scan_set(composition, root):
    """The repo-local modules the composition's SOURCE FILE can import.

    A static walk, not a runtime snapshot: every `import` and `from` at any
    depth — function bodies, `try` blocks, dead branches — resolves to
    files under the repository root or is provably elsewhere (stdlib, site
    packages), and the walk iterates to a fixed point. A target the walk
    cannot determine statically is unprovable and fails loudly, naming the
    module and the import site: a walk that silently omitted what it cannot
    resolve would be the next blind spot, not a closure.
    """
    root = Path(root).resolve()
    composition = Path(composition).resolve()
    seen = {composition}
    pending = [composition]
    while pending:
        for target in _import_targets(pending.pop(), root):
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return sorted(path for path in seen
                  if 'tests' not in path.relative_to(root).parts)


def _dynamic_callees(tree):
    """The names one module's imports bind to the import-by-name operation.

    `import importlib [as x]`, `import importlib.util` and `import builtins
    [as x]` bind their module aliases — an import binds its FIRST dotted
    component, since that is the name the statement puts in the namespace —
    `from importlib import import_module [as y]`, `from importlib import
    __import__ [as z]` and `from builtins import __import__ [as z]` bind
    their function names, and the builtin `__import__` is bound before
    anything runs. Classification then resolves a call's callee through
    this map instead of matching spellings.
    """
    bound = {'__import__': 'by name'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.partition('.')[0]
                if head in ('importlib', 'builtins'):
                    bound[alias.asname or head] = head
        elif isinstance(node, ast.ImportFrom):
            if not node.level:
                names = {'importlib': DYNAMIC_ATTRIBUTES,
                         'builtins': ('__import__',)}.get(node.module, ())
                for alias in node.names:
                    if alias.name in names:
                        bound[alias.asname or alias.name] = 'by name'
    return bound


def _is_dynamic_import(func, bound):
    """A call to import_module or __import__, per the module's own bindings.

    Loading a module by PATH — `spec_from_file_location`, `SourceFileLoader`
    — is a different operation and stays outside this recognition.
    """
    if isinstance(func, ast.Name):
        return bound.get(func.id) == 'by name'
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        source = bound.get(func.value.id)
        if source == 'importlib':
            return func.attr in DYNAMIC_ATTRIBUTES
        if source == 'builtins':
            return func.attr == '__import__'
    return False


def _yields_the_operation(value, bound):
    """True when an expression hands out the import-by-name operation under
    a name this map already tracks."""
    if isinstance(value, ast.Name):
        return value.id in bound
    return _is_dynamic_import(value, bound)


def _aliases_the_operation(node, bound):
    """True when a statement binds the import-by-name operation to a plain
    name, which the map cannot see and therefore cannot follow."""
    value = node.value
    if value is None or not _yields_the_operation(value, bound):
        return False
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(target, ast.Name) for target in targets)


def _looks_the_operation_up(node, bound):
    """True when a `getattr` call can hand the import-by-name operation out
    without naming it: a constant spelling of the operation's attribute, or
    an unknown attribute read off a name this map tracks. Anything else is
    an ordinary lookup for an ordinary attribute."""
    if not (isinstance(node.func, ast.Name) and node.func.id == 'getattr'):
        return False
    if len(node.args) < 2:
        return False
    obj, attribute = node.args[0], node.args[1]
    if isinstance(attribute, ast.Constant):
        return attribute.value in DYNAMIC_ATTRIBUTES
    return isinstance(obj, ast.Name) and obj.id in bound


def _import_targets(path, root):
    """The repo-local files one module's source can import.

    Empty when nothing the module names lives under root — stdlib and
    site-package targets are provably not this repository's. A dynamic
    import whose argument is not a constant string is unprovable and raises,
    naming the module and the import site; a constant resolves like an
    import. A spelling that hides the operation behind a name this map
    cannot follow — an assignment alias, a `getattr` — raises too, because
    a walk that skipped it would be the next blind spot. What stays outside
    the property: an operation reached through an object this map never
    bound, such as a module a resolved constant import was stored under.
    """
    targets = set()
    tree = ast.parse(path.read_text(encoding='utf-8'))
    bound = _dynamic_callees(tree)
    package = path.resolve().relative_to(Path(root).resolve()).parent.parts
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets |= _resolve_name(alias.name, (), root)
        elif isinstance(node, ast.ImportFrom):
            base = package[:max(len(package) + 1 - node.level, 0)] \
                if node.level else ()
            if node.module:
                targets |= _resolve_name(node.module, base, root)
            for alias in node.names:
                if alias.name != '*':
                    name = f'{node.module}.{alias.name}' if node.module \
                        else alias.name
                    targets |= _resolve_name(name, base, root)
        elif isinstance(node, ast.Call) and _is_dynamic_import(
                node.func, bound):
            argument = node.args[0] if node.args else None
            if isinstance(argument, ast.Constant) \
                    and isinstance(argument.value, str) \
                    and not argument.value.startswith('.'):
                targets |= _resolve_name(argument.value, (), root)
            else:
                _refuse(path, root, node,
                        'import_module/__import__ is called with a name '
                        'this scan cannot read statically')
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) \
                and _aliases_the_operation(node, bound):
            _refuse(path, root, node,
                    f'{ast.unparse(node)} binds the import-by-name operation '
                    'to a name this scan cannot follow')
        elif isinstance(node, ast.Call) and _looks_the_operation_up(
                node, bound):
            _refuse(path, root, node,
                    f'{ast.unparse(node)} can hand out the import-by-name '
                    'operation through a lookup this scan cannot follow')
    return targets


def _resolve_name(name, base, root):
    """One import target's repo-local files: the package `__init__` files
    importing it executes, then the module file itself. Relative imports
    arrive resolved against the importing module's package in `base`. Empty
    when no prefix of the dotted name matches the repository, which is what
    makes stdlib and site packages provably irrelevant."""
    parts = (*base, *name.split('.'))
    found = set()
    probe = Path(root).resolve()
    for index, part in enumerate(parts):
        package = probe / part
        if package.is_dir():
            probe = package
            init = package / '__init__.py'
            if init.is_file():
                found.add(init.resolve())
            continue
        module = probe / f'{part}.py'
        if index == len(parts) - 1 and module.is_file():
            found.add(module.resolve())
        break
    return found
