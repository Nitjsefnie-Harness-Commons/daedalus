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
    """True when an expression can evaluate to the import-by-name operation.

    Structural, not one level deep: a tracked name inside a conditional, a
    boolean choice, a comparison, a container, a starred element, a
    subscripted value or a comprehension can still reach the store, so each
    of those wrappers is read into. Two shapes are read as NOT yielding it,
    and both are readable to a specific other object: an attribute of a
    known module that is not one of the operation's own (`importlib.util`),
    and a call — a call evaluates to whatever its callee returns, not to
    the callee. Those are the deliberate limits, and a name bound to a
    call's result stays outside the property rather than refused, because
    refusing every one of those would refuse ordinary code.
    """
    if isinstance(value, ast.Name):
        return value.id in bound
    if isinstance(value, ast.Attribute):
        return _is_dynamic_import(value, bound)
    if isinstance(value, ast.IfExp):
        return _yields_the_operation(value.body, bound) \
            or _yields_the_operation(value.orelse, bound)
    if isinstance(value, ast.BoolOp):
        return any(_yields_the_operation(item, bound) for item in value.values)
    if isinstance(value, ast.Compare):
        return any(_yields_the_operation(item, bound)
                   for item in (value.left, *value.comparators))
    if isinstance(value, ast.Starred):
        return _yields_the_operation(value.value, bound)
    if isinstance(value, ast.Subscript):
        return _yields_the_operation(value.value, bound)
    if isinstance(value, ast.NamedExpr):
        return _yields_the_operation(value.value, bound)
    if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        return any(_yields_the_operation(item, bound) for item in value.elts)
    if isinstance(value, ast.Dict):
        return any(key is not None and _yields_the_operation(key, bound)
                   for key in value.keys) \
            or any(_yields_the_operation(item, bound) for item in value.values)
    if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return _yields_the_operation(value.elt, bound)
    if isinstance(value, ast.DictComp):
        return _yields_the_operation(value.key, bound) \
            or _yields_the_operation(value.value, bound)
    return False


def _store_leaves(target):
    """Every name, attribute or subscript a store target binds."""
    if isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _store_leaves(element)
    elif isinstance(target, ast.Starred):
        yield from _store_leaves(target.value)
    else:
        yield target


class _BindingWalk(ast.NodeVisitor):
    """Every place the module binds a name, and what each binding takes.

    The binding grammar is not a list of statement kinds, so this reads
    stores rather than statements: a walrus, an unpack, a `for` or `with`
    target, a comprehension target, an `except ... as` name and a parameter
    default all bind a name, and each is refused when it binds the
    import-by-name operation to somewhere the map cannot see, or when it
    overwrites a name the map tracks. Because a tracked name is never
    allowed to be rebound, the map needs no rewriting to stay a fixed
    point: the two refusals are what keep it one.
    """

    def __init__(self, bound, refuse):
        self.bound = bound
        self.refuse = refuse

    def _alias(self, node):
        self.refuse(
            node, f'{ast.unparse(node)} binds the import-by-name operation to '
            'a name this scan cannot follow')

    def _rebind(self, node, name):
        self.refuse(
            node, f'{ast.unparse(node)} rebinds {name!r}, which this scan '
            'maps to the import-by-name operation, to a value it cannot '
            'follow')

    def _leaf(self, node, target, values):
        """One store, offered the values it can receive.

        A target the pairing could not read is offered all of them: any one
        of them may land in any one leaf, so the refusal cannot name which.
        """
        if any(value is not None and _yields_the_operation(value, self.bound)
               for value in values):
            self._alias(node)
        elif isinstance(target, ast.Name) and target.id in self.bound:
            self._rebind(node, target.id)

    def _pooled(self, node, target, values):
        for leaf in _store_leaves(target):
            self._leaf(node, leaf, values)

    def _paired(self, node, target, value):
        """Bind a target from the one value it receives, unpacking a
        sequence element-wise when both sides have the same shape."""
        if isinstance(target, (ast.Tuple, ast.List)) \
                and isinstance(value, (ast.Tuple, ast.List)):
            if len(target.elts) == len(value.elts):
                for element, item in zip(target.elts, value.elts):
                    self._paired(node, element, item)
                return
            self._pooled(node, target, list(value.elts))
            return
        if isinstance(target, (ast.Tuple, ast.List, ast.Starred)):
            self._pooled(node, target, [value])
            return
        self._leaf(node, target, [value])

    def _each(self, node, target, expression):
        """Bind a target from every value an expression can offer it."""
        if isinstance(expression, (ast.Tuple, ast.List)):
            for item in expression.elts:
                self._paired(node, target, item)
        else:
            self._paired(node, target, expression)

    def _defaults(self, node, defaults):
        """A parameter default binds its parameter. A parameter with no
        default is a fresh name, and one that shadows a tracked name leaves
        the map's answer standing on the conservative side."""
        for default in defaults:
            if default is not None \
                    and _yields_the_operation(default, self.bound):
                self._alias(node)

    def visit_Assign(self, node):
        self.generic_visit(node)
        for target in node.targets:
            self._paired(node, target, node.value)

    def visit_AugAssign(self, node):
        self.generic_visit(node)
        self._paired(node, node.target, node.value)

    def visit_AnnAssign(self, node):
        self.generic_visit(node)
        if node.value is not None:
            self._paired(node, node.target, node.value)

    def visit_NamedExpr(self, node):
        self.generic_visit(node)
        self._paired(node, node.target, node.value)

    def visit_For(self, node):
        self.generic_visit(node)
        self._each(node, node.target, node.iter)

    visit_AsyncFor = visit_For

    def _comprehension(self, node):
        self.generic_visit(node)
        # A generator node carries no line of its own, so the enclosing
        # expression is what a refusal has to name.
        for generator in node.generators:
            self._each(node, generator.target, generator.iter)

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension

    def visit_With(self, node):
        self.generic_visit(node)
        for item in node.items:
            if item.optional_vars is not None:
                self._paired(node, item.optional_vars, item.context_expr)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node):
        self.generic_visit(node)
        if not node.name:
            return
        if node.type is not None \
                and _yields_the_operation(node.type, self.bound):
            self._alias(node)
        elif node.name in self.bound:
            self._rebind(node, node.name)

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        self._defaults(node, node.args.defaults)
        self._defaults(node, node.args.kw_defaults)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self.generic_visit(node)
        self._defaults(node, node.args.defaults)
        self._defaults(node, node.args.kw_defaults)


def _refused_bindings(tree, bound, refuse):
    """Refuse every store that hides the import-by-name operation from the
    map, whatever form the store takes."""
    _BindingWalk(bound, refuse).visit(tree)


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
    cannot follow — a store of any binding form, a `getattr` — raises too,
    because a walk that skipped it would be the next blind spot. Two value
    shapes stay outside the property on purpose, and are accepted rather
    than skipped in silence: a call's result, and an attribute of a known
    module that is not the operation (`importlib.util`). Both are readable
    to a specific other object, and refusing every store of either would
    refuse `mod = importlib.import_module('fcntl')` and every `x = f()[0]`
    with it; a name holding a call's result is followed by neither the
    operation map nor these refusals.
    """
    targets = set()
    tree = ast.parse(path.read_text(encoding='utf-8'))
    bound = _dynamic_callees(tree)
    package = path.resolve().relative_to(Path(root).resolve()).parent.parts
    _refused_bindings(
        tree, bound,
        lambda node, detail: _refuse(path, root, node, detail))
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
