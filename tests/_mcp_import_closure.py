"""The MCP composition's static import closure, and what it refuses.

The refusal witness floor scans the modules the composition can import, so
this walk is what makes that set closed: every `import` and `from` at any
depth resolves to a repository file or is provably elsewhere, and a dynamic
import is either read or refused by name and line. A spelling that binds
the import-by-name operation to a NAME the map does not track is refused
too, and so is one that reaches it through a string: a string that NAMES
the operation, and a module read out of the registry by string, are the
same hole the tracked-name map left open, and both are refused. The
registry is read at every level — the base structurally, the key by folding
it — and a store that hands it away and a star import are refused too. A
code-evaluating builtin (`eval`/`exec`/`compile`) is the same hole one step
on: a CONSTANT program handed to one is refused, and so is any store that
DELIVERS the builtin to a name (as a name, a parameter default, a container
or a call argument), read through the SAME store grammar as the operation
and the registry. A store that USES the builtin as a call's EFFECTIVE CALLEE
receives the call's RESULT — the declared call-result limit below, not a
delivery — and the callee is resolved by the VALUE it produces, so a builtin
in a DATA position of it (an argument, a lookup key) stays a delivery. Three
shapes it cannot follow are ACCEPTED as declared limits: a value reached
through a call's result, a tracked module or the operation handed as a call
ARGUMENT (`use(sys)`), and a value the walk cannot fold to a constant — an
import name or a program. The third is a MECHANISM: a value COMPUTED at
runtime is accepted, so an interpolated f-string, a subscript that selects
it (`['n'][0]`, `('n',)[0]`, `{'k':'n'}['k']`) and a starred argument are
facets of it, pinned in `test_the_fold_limit_facets_are_accepted`. Any
accepted shape leaves the closure quietly short.
"""
import ast
from pathlib import Path

import _mcp_code_eval


DYNAMIC_ATTRIBUTES = ('import_module', '__import__')

# The map's values that name the module registry rather than the operation.
# A registry name is tracked so a read of it can be refused, not because it
# mentions the import-by-name operation.
REGISTRY_NAMES = ('sys', 'registry')

# The one name the module registry answers to, whether it is read as a dotted
# attribute or as the key of a module namespace.
REGISTRY_ATTRIBUTE = 'modules'

# The noun each refusal names, so the store and default paths word one thing
# the same way.
_HIDDEN_NOUN = {
    'operation': 'the import-by-name operation',
    'registry': 'the module registry',
    'code eval': 'a code-evaluating builtin',
}

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
    raise AssertionError(
        f'{dotted_module(path, root)}:{node.lineno}: {detail}{CLOSURE_TAIL}')


def composition_scan_set(composition, root):
    """The repo-local modules the composition's SOURCE FILE can import.

    A static walk, not a runtime snapshot: every `import` and `from` at any
    depth — function bodies, `try` blocks, dead branches — resolves to
    files under the repository root or is provably elsewhere (stdlib, site
    packages), and the walk iterates to a fixed point. A target the walk
    cannot determine statically is unprovable and fails loudly, naming the
    module and the import site.
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
    """The names one module's imports bind to the import-by-name operation,
    and the names they bind to the module registry.

    `import importlib [as x]`, `import importlib.util` and `import builtins
    [as x]` bind their module aliases — an import binds its FIRST dotted
    component, since that is the name the statement puts in the namespace —
    `from importlib import import_module [as y]`, `from importlib import
    __import__ [as z]` and `from builtins import __import__ [as z]` bind
    their function names, and the builtin `__import__` is bound before
    anything runs. `import sys [as s]` binds the name whose `modules`
    attribute is the module registry, and `from sys import modules [as r]`
    and `import sys.modules [as r]` bind the registry itself.
    """
    bound = {'__import__': 'by name'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.partition('.')[0]
                if alias.name == 'sys.modules' and alias.asname:
                    bound[alias.asname] = 'registry'
                elif head in ('importlib', 'builtins', 'sys'):
                    bound[alias.asname or head] = head
        elif isinstance(node, ast.ImportFrom):
            if not node.level:
                names = {'importlib': DYNAMIC_ATTRIBUTES,
                         'builtins': ('__import__',)}.get(node.module, ())
                for alias in node.names:
                    if alias.name in names:
                        bound[alias.asname or alias.name] = 'by name'
                    elif node.module == 'sys' and alias.name == 'modules':
                        bound[alias.asname or 'modules'] = 'registry'
    return bound


def _is_dynamic_import(func, bound):
    """A call to import_module or __import__, per the module's own bindings.

    An attribute names the operation by its own name, so one whose attribute
    is not one of the operation's (`importlib.util`) is readable to a
    specific other object and is not the operation; one whose attribute IS
    the operation's is the operation exactly when its base mentions the
    operation, and that base is read through the same property the store
    side uses. Loading a module by PATH —
    `spec_from_file_location`, `SourceFileLoader` — is a different
    operation and stays outside this recognition.
    """
    if isinstance(func, ast.Name):
        return bound.get(func.id) == 'by name'
    if isinstance(func, ast.Attribute):
        if func.attr not in DYNAMIC_ATTRIBUTES:
            return False
        return _yields_the_operation(func.value, bound)
    return False


def _yields_the_operation(value, bound):
    """True when an expression's own subtree mentions the import-by-name
    operation, so a store of it can hand the operation to a name this map
    cannot follow.

    The property, not a list of the shapes that have been met: a tracked
    name anywhere inside an expression is a mention, and every type nobody
    has thought of is read the same way, by its own children. A registry
    name is the one tracked name that is not a mention: the map tracks it
    so a read of it can be refused. Two early
    returns do NOT answer by their own children, and each is accounted for.
    An attribute is one: it is the operation exactly when its own name is
    the operation's AND its base mentions the operation — a test that reads
    the whole base however it is spelled, so `importlib.util` is not the
    operation and `[importlib][0].import_module` is. The OTHER is the
    property's single limit, a call: a call evaluates to whatever its
    callee returns, not to the callee, so a name bound to a call's result
    is followed by neither this map nor these refusals.
    """
    if isinstance(value, ast.Name):
        tracked = bound.get(value.id)
        return tracked is not None and tracked not in REGISTRY_NAMES
    if isinstance(value, ast.Attribute):
        return _is_dynamic_import(value, bound)
    if isinstance(value, ast.Call):
        return False
    return any(_yields_the_operation(child, bound)
               for child in ast.iter_child_nodes(value))


def _store_leaves(target):
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
    target, a comprehension target and a parameter default all bind a name,
    and each is refused when it binds the import-by-name operation, the
    module registry or a code-evaluating builtin to somewhere the map cannot
    see, or when it overwrites a name the map tracks. An `except ... as`
    name is the one store this read separately, and only the operation
    axis and the rebind apply to it: an except-name binds the caught
    EXCEPTION, never the registry or a code-evaluating builtin, so neither
    is reachable through it. Because a tracked name is never allowed to be
    rebound, the map needs no rewriting to stay a fixed point: the
    refusals are what
    keep it one.
    """

    def __init__(self, bound, scopes, refuse):
        self.bound = bound
        self.scopes = scopes
        self.refuse = refuse

    def _alias(self, node):
        self.refuse(
            node, f'{ast.unparse(node)} binds the import-by-name operation to '
            'a name this scan cannot follow')

    def _registry_alias(self, node):
        self.refuse(
            node, f'{ast.unparse(node)} binds the module registry to a name '
            'this scan cannot follow')

    def _code_eval_alias(self, node):
        self.refuse(
            node, f'{ast.unparse(node)} binds a code-evaluating builtin to a '
            'name this scan cannot follow')

    def _rebind(self, node, name):
        self.refuse(
            node, f'{ast.unparse(node)} rebinds {name!r}, which this scan '
            'maps to the import-by-name operation, to a value it cannot '
            'follow')

    def _hidden(self, values):
        """Which thing these values hide: the operation, the registry, a
        code-evaluating builtin, or nothing. The store path (`_leaf`) and the
        default path (`_defaults`) both route through this, so no axis can
        reach a store form another axis already reaches and be weaker there."""
        if any(value is not None and _yields_the_operation(value, self.bound)
               for value in values):
            return 'operation'
        if any(value is not None and _yields_the_registry(value, self.bound)
               for value in values):
            return 'registry'
        if any(value is not None
               and _mcp_code_eval.yields_code_eval(
                   value, self.bound, self.scopes) for value in values):
            return 'code eval'
        return None

    def _leaf(self, node, target, values):
        """One store, offered the values it can receive.

        A target the pairing could not read is offered all of them: any one
        of them may land in any one leaf, so the refusal cannot name which.
        """
        hidden = self._hidden(values)
        if hidden == 'operation':
            self._alias(node)
        elif hidden == 'registry':
            self._registry_alias(node)
        elif isinstance(target, ast.Name) and target.id in self.bound:
            self._rebind(node, target.id)
        elif hidden == 'code eval':
            self._code_eval_alias(node)

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

    def _refuse_default(self, node, name, default, hidden):
        """Refuse a default that hands the operation, the registry or a
        code-evaluating builtin to its parameter.

        The message names the offending parameter, not the whole
        definition, so a maintainer reads one line in the traceback.
        """
        self.refuse(
            node, f'parameter {name}={ast.unparse(default)} binds '
            f'{_HIDDEN_NOUN[hidden]} to a name this scan cannot follow')

    def _defaults(self, node, names, defaults):
        """A parameter default binds its parameter. A parameter with no
        default is a fresh name, and one that shadows a tracked name leaves
        the map's answer standing on the conservative side."""
        for name, default in zip(names, defaults):
            hidden = self._hidden([default])
            if hidden is not None:
                self._refuse_default(node, name, default, hidden)

    def _positional_defaults(self, node):
        args = node.args
        positional = [arg.arg for arg in args.posonlyargs + args.args]
        self._defaults(
            node, positional[len(positional) - len(args.defaults):],
            args.defaults)

    def _keyword_defaults(self, node):
        args = node.args
        self._defaults(
            node, [arg.arg for arg in args.kwonlyargs], args.kw_defaults)

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
        self._positional_defaults(node)
        self._keyword_defaults(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        self.generic_visit(node)
        self._positional_defaults(node)
        self._keyword_defaults(node)


def _refused_bindings(tree, bound, scopes, refuse):
    """Refuse every store that hides the import-by-name operation, the
    registry or a code-evaluating builtin from the map, whatever form the
    store takes."""
    _BindingWalk(bound, scopes, refuse).visit(tree)


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


def _registry_mention(value, bound, stop_at_call=False):
    """True when a registry-carrying name appears anywhere in an expression's
    own subtree — the property `_yields_the_operation` reads for the
    operation, applied to the registry. Only a name bound to `sys` or the
    registry counts, so an `importlib.modules` is not a mention.
    `stop_at_call` does not follow a call's result, for the one base test
    that has only the base to consult.
    """
    if stop_at_call and isinstance(value, ast.Call):
        return False
    if isinstance(value, ast.Name):
        return bound.get(value.id) in REGISTRY_NAMES
    return any(_registry_mention(child, bound, stop_at_call)
               for child in ast.iter_child_nodes(value))


def _is_namespace(value, bound):
    """True when this expression is a module NAMESPACE — a tracked `sys`
    module's `__dict__` — which is where the registry is reachable under a
    `'modules'` key.

    The property is that the base IS a namespace, NOT that it mentions a
    registry-carrying name: `sys.argv` and `sys.path` are ordinary
    attributes. A call is not crossed.
    """
    return isinstance(value, ast.Attribute) and value.attr == '__dict__' \
        and _registry_mention(value.value, bound, stop_at_call=True)


def _is_registry(node, bound):
    """The module registry itself, read through the map's tracked names.

    A name bound to the registry is the registry; a `.modules` attribute is
    it when its base MENTIONS a registry-carrying name; a subscript keyed by
    the registry's name is it when its value mentions one either (the
    `__dict__` route). A subscript's KEY is decided by the constant-folder:
    a key folding to the registry's name is the registry, a key folding to
    another constant resolves that attribute, and an unreadable key is
    unresolvable — refused only when the base is a NAMESPACE
    (`_is_namespace`) or a call's result, which the call-result limit covers.
    """
    if isinstance(node, ast.Name):
        return bound.get(node.id) == 'registry'
    if isinstance(node, ast.Attribute) and node.attr == REGISTRY_ATTRIBUTE:
        return _registry_mention(node.value, bound)
    if isinstance(node, ast.Subscript):
        key = _folded_string(node.slice)
        if key is not None and key != REGISTRY_ATTRIBUTE:
            return False
        if key == REGISTRY_ATTRIBUTE:
            return _registry_mention(node.value, bound)
        return _is_namespace(node.value, bound)
    return False


def _yields_the_registry(value, bound):
    """True when a store's value can hand the registry to a name this map
    cannot follow, the store-side counterpart of `_is_registry`.

    A bare registry-carrying name IS the thing a store must not hide; a
    `.modules` attribute or a `['modules']` subscript is the registry by
    `_is_registry`; a call is the declared call-result limit. `m = sys` is
    the shape.
    """
    if isinstance(value, ast.Name):
        return bound.get(value.id) in REGISTRY_NAMES
    if isinstance(value, ast.Attribute):
        return _is_registry(value, bound)
    if isinstance(value, ast.Call):
        return False
    return any(_yields_the_registry(child, bound)
               for child in ast.iter_child_nodes(value))


def _program_argument(call):
    """The expression a code-evaluating call passes as its program: the first
    positional argument, or the first positional-or-keyword parameter bound by
    keyword. For `eval`/`exec`/`compile` that parameter is `source`, so
    `compile(source=PROG, ...)` hands over the program the positional spelling
    does; None when the call passes no source at all."""
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == 'source':
            return keyword.value
    return None


def _folded_string(node):
    """The constant string a node spells, folding a concatenation of string
    constants so `'import_' + 'module'` reads as the one name it is, and a
    field-less f-string so `f'importlib.import_module'` reads as the constant
    it is. A field-less f-string has no formatted value, so it depends on no
    runtime name; one that DOES interpolate falls through unreadable."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and all(
            isinstance(part, ast.Constant) and isinstance(part.value, str)
            for part in node.values):
        return ''.join(part.value for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _folded_string(node.left)
        right = _folded_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _refused_string_reads(tree, bound, refuse):
    """A string that NAMES the operation, or a module read by string from
    the registry, reaches the import-by-name operation the way a name does.

    Both are matched against the operation's names, never a spelling of their
    own, and both go through the one constant-folder, the single authority
    for a readable string.
    """
    for node in ast.walk(tree):
        folded = _folded_string(node)
        if folded is not None and folded in DYNAMIC_ATTRIBUTES:
            refuse(node, f'the string {folded!r} names the import-by-name'
                   ' operation, which this scan cannot follow')
        elif _is_registry(node, bound):
            refuse(node, f'a module is read out of the registry by'
                   f' {ast.unparse(node)}, which this scan cannot resolve')


def _import_targets(path, root):
    """The repo-local files one module's source can import.

    A call's result stays outside the property on purpose, and is accepted
    rather than skipped in silence: a call evaluates to whatever its callee
    returns, so refusing every store of one would refuse
    `mod = importlib.import_module('fcntl')` and every `x = f()` with it; a
    value reached through a call's result is followed by neither the operation
    map nor these refusals, whether it is bound to a name or read inline as a
    base, attribute or subscript (`__import__('sys').modules[k]`). A tracked
    module or the operation delivered as a call ARGUMENT is likewise accepted
    — `use(importlib)` and `use(sys)`, whose registry is reachable only past
    the argument — because the walk follows nothing a call returns or is
    handed. A string ASSEMBLED at runtime that the walk cannot fold to a
    constant is the third declared limit, named by the mechanism rather than
    one spelling: an interpolated f-string, a concatenation with a name, a
    `''.join`, a `.format()`, `%`. A string that DOES fold to a constant — a
    field-less f-string, a concatenation of literals — is refused like any
    other literal. A star import is refused because it binds names this map
    cannot hold.
    """
    targets = set()
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    bound = _dynamic_callees(tree)
    scopes = _mcp_code_eval.scopes_for(tree, source, str(path))
    package = path.resolve().relative_to(Path(root).resolve()).parent.parts
    _refused_bindings(
        tree, bound, scopes,
        lambda node, detail: _refuse(path, root, node, detail))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets |= _resolve_name(alias.name, (), root)
        elif isinstance(node, ast.ImportFrom):
            base = package[:max(len(package) + 1 - node.level, 0)] \
                if node.level else ()
            if any(alias.name == '*' for alias in node.names):
                _refuse(path, root, node,
                        'a star import binds names this scan cannot follow')
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
            folded = _folded_string(argument)
            if folded is not None and not folded.startswith('.'):
                targets |= _resolve_name(folded, (), root)
            else:
                _refuse(path, root, node,
                        'import_module/__import__ is called with a name '
                        'this scan cannot read statically')
        elif isinstance(node, ast.Call) and _looks_the_operation_up(
                node, bound):
            _refuse(path, root, node,
                    f'{ast.unparse(node)} can hand out the import-by-name '
                    'operation through a lookup this scan cannot follow')
        elif isinstance(node, ast.Call) and _mcp_code_eval.may_be_code_eval(
                node.func, bound, scopes):
            program = _program_argument(node)
            if program is not None:
                program = _folded_string(program)
            if program is not None:
                _refuse(path, root, node,
                        f'the program {program!r} is handed to a '
                        'code-evaluating builtin, which this scan cannot '
                        'resolve')
            # A program the folder cannot read as a constant is a declared
            # limit, shared with the import name.
    _refused_string_reads(
        tree, bound,
        lambda node, detail: _refuse(path, root, node, detail))
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
