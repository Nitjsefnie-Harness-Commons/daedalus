"""The code-evaluating axis of the import-closure walk: recognition, once.

`eval`, `exec` and `compile` evaluate a program, and a program can name the
import-by-name operation, so a constant string handed to one is a hole the
tracked-name map left open. This module owns that axis so the guard's shared
store grammar (`_hidden`) routes the code-eval store through the same
decision it routes the operation and the registry through.

The shadow test is a PROPERTY, not a list: `symtable` is Python's own binding
grammar, so a name no enclosing scope binds is the builtin. A `from builtins
import eval` binds the builtin itself and is not a shadow.
"""
import ast
import symtable


# The builtins that evaluate a program; one set reads every direct reach.
CODE_EVAL_BUILTINS = ('eval', 'exec', 'compile')


class _Scopes:
    """Which names one reference resolves to, so a builtin is told apart from
    a same-named local. A scope the walk cannot line up with the resolver's is
    read in its enclosing scope; for a module-level reference that errs
    toward the builtin and refuses, so a correlation miss does not accept a
    reachable builtin."""

    _SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                    ast.Lambda)

    def __init__(self, tree, source, filename):
        self._root = symtable.symtable(source, filename, 'exec')
        self._root_binds = {symbol.get_name() for symbol
                            in self._root.get_symbols()
                            if symbol.is_assigned() or symbol.is_imported()
                            or symbol.is_parameter() or symbol.is_namespace()}
        self._scope_of = {}
        self._from_builtins = set()
        self._module_bind_line = {}
        self._walk(tree, self._root)

    @staticmethod
    def _symbols(table):
        return {symbol.get_name(): symbol for symbol in table.get_symbols()}

    @staticmethod
    def _match(table, name, line):
        """The resolver's scope for a named scope node, or None when there
        is no match OR the match is ambiguous. Two sibling scopes on ONE
        line (two lambdas separated by `;`) share name and line, so an
        ambiguous match returns None and the node is read in its ENCLOSING
        scope — for a module-level reference, the module, where an unbound
        code-evaluating name is the builtin, so the miss errs toward the
        builtin and refuses."""
        matches = [child for child in table.get_children()
                   if child.get_name() == name and child.get_lineno() == line]
        return matches[0] if len(matches) == 1 else None

    def _note(self, name, lineno):
        if name in CODE_EVAL_BUILTINS:
            prior = self._module_bind_line.get(name)
            if prior is None or lineno < prior:
                self._module_bind_line[name] = lineno

    def _record_module_binding(self, node):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            self._note(node.id, node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            self._note(node.name, node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                self._note(alias.asname or alias.name.partition('.')[0],
                           node.lineno)

    def _walk(self, node, table):
        self._scope_of[id(node)] = table
        if table is self._root:
            # Only module-scope forms count; a nested function or
            # comprehension body is its own scope and is not walked as root.
            self._record_module_binding(node)
        if isinstance(node, ast.ImportFrom) and node.module == 'builtins' \
                and not node.level:
            self._from_builtins.update(
                alias.asname or alias.name for alias in node.names
                if alias.name in CODE_EVAL_BUILTINS)
        if isinstance(node, self._SCOPE_NODES):
            name = 'lambda' if isinstance(node, ast.Lambda) else node.name
            child = self._match(table, name, node.lineno)
            if child is not None:
                table = child
        for child in ast.iter_child_nodes(node):
            self._walk(child, table)

    def is_code_evaluating(self, node):
        """The name is a code-evaluating builtin at `node`'s own scope: an
        unshadowed builtin name, or one bound by a from-builtins import."""
        return node.id in self._from_builtins or (
            node.id in CODE_EVAL_BUILTINS and self.is_builtin(node))

    def is_builtin(self, node):
        """The name is an unshadowed builtin at `node`'s own scope. A module
        runs top to bottom, so a MODULE-TOP-LEVEL reference is the builtin
        only when the module leaves its name unbound BY THE TIME OF THE USE:
        a use PRECEDING its own later module-level binding still reads the
        real builtin, while one after it sees the bound name. A reference
        inside a function runs after the module has loaded, so any module
        binding shadows it and order is irrelevant there."""
        table = self._scope_of.get(id(node))
        symbol = self._symbols(table).get(node.id) if table else None
        if symbol is None:
            return True
        if table is self._root:
            if node.id not in self._root_binds:
                return True
            return node.lineno < self._module_bind_line.get(
                node.id, float('inf'))
        if symbol.is_local() or symbol.is_free():
            return False
        return node.id not in self._root_binds


def scopes_for(tree, source, filename):
    """The scope model one module's source resolves against."""
    return _Scopes(tree, source, filename)


def _names_builtins(node, bound):
    return isinstance(node, ast.Name) and bound.get(node.id) == 'builtins'


def _is_builtins_namespace(node, bound):
    """The module NAMESPACE the builtins live in — `builtins.__dict__` or the
    module's own `__builtins__` — so a constant key selects a builtin by the
    same `__dict__` route the operation axis already reads."""
    if isinstance(node, ast.Name):
        return node.id == '__builtins__'
    return isinstance(node, ast.Attribute) and node.attr == '__dict__' \
        and _names_builtins(node.value, bound)


def denotes_code_eval(node, bound, scopes):
    """The code-evaluating builtin this node evaluates to DIRECTLY, or
    nothing: a bare builtin name (`scopes` decides shadow), the module
    attribute, or the constant `getattr` off the builtins module — the same
    routes the import operation is recognised through.
    """
    if isinstance(node, ast.Name):
        return scopes.is_code_evaluating(node)
    if isinstance(node, ast.Attribute):
        return node.attr in CODE_EVAL_BUILTINS and _names_builtins(
            node.value, bound)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == 'getattr' and len(node.args) >= 2:
        attribute = node.args[1]
        if isinstance(attribute, ast.Constant):
            return attribute.value in CODE_EVAL_BUILTINS and _names_builtins(
                node.args[0], bound)
        return _names_builtins(node.args[0], bound)
    return False


def _is_plain_lambda(func):
    """Whether this lambda is callable with NO arguments, so a zero-argument
    call to it returns its body. A REQUIRED positional or keyword-only
    parameter (no default) blocks such a call; a vararg, a kwarg and a
    defaulted parameter do not. So `(lambda *a: eval)()` reaches the
    operation, while `(lambda a: eval)()` raises and is not a reach."""
    if not isinstance(func, ast.Lambda):
        return False
    args = func.args
    positional = args.posonlyargs + args.args
    if len(positional) > len(args.defaults):
        return False
    return not any(default is None for default in args.kw_defaults)


def _arguments(call):
    return list(call.args) + [keyword.value for keyword in call.keywords]


def _builtin_projection(node, bound, scopes):
    """A projection of the builtin that still RUNS it: `X.__call__` or
    `getattr(X, '__call__')` where X denotes a code-evaluating builtin.

    `__call__` is how Python spells "this object is callable", so calling the
    projection calls the builtin; the walk therefore reads it as the callee it
    denotes. A lambda is NOT modelled as transparent — it is a function the
    walk cannot follow, so a builtin passed to one is delivered.
    """
    if isinstance(node, ast.Attribute) and node.attr == '__call__':
        return may_be_code_eval(node.value, bound, scopes)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'getattr' and len(node.args) >= 2):
        attribute = node.args[1]
        if isinstance(attribute, ast.Constant) \
                and attribute.value != '__call__':
            return False
        return may_be_code_eval(node.args[0], bound, scopes)
    return False


def _element_node(subscript, bound, scopes):
    """The expression a subscript SELECTS, resolved through the container
    and key by their values and through any further subscript it lands on;
    None when the selected value is not readable. An int key reads a sequence
    element, a str key a mapping value, so a two-level or string-key
    selection resolves by the same rule as a one-level one."""
    key = subscript.slice
    container = subscript.value
    if isinstance(container, ast.Subscript):
        container = _element_node(container, bound, scopes)
        if container is None:
            return None
    if isinstance(key, ast.Constant) and isinstance(key.value, int) \
            and not isinstance(key.value, bool):
        if isinstance(container, (ast.Tuple, ast.List)) and \
                -len(container.elts) <= key.value < len(container.elts):
            element = container.elts[key.value]
        elif key.value == 0 and isinstance(
                container, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            element = container.elt
        else:
            return None
    elif isinstance(key, ast.Constant) and isinstance(key.value, str) \
            and isinstance(container, ast.Dict):
        element = None
        for dict_key, dict_value in zip(container.keys, container.values):
            if isinstance(dict_key, ast.Constant) \
                    and dict_key.value == key.value:
                element = dict_value
                break
        if element is None:
            return None
    else:
        return None
    if isinstance(element, ast.Starred):
        # `[*expr]` selects expr's value, not the star wrapper
        return element.value
    return element


def _holds_code_eval(node, bound, scopes):
    """This expression's value STRUCTURE holds a code-evaluating builtin —
    in a container element, a mapping value, or a comprehension element —
    whether or not the value is itself the builtin."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(may_be_code_eval(element, bound, scopes)
                   or _holds_code_eval(element, bound, scopes)
                   for element in node.elts)
    if isinstance(node, ast.Dict):
        return any(may_be_code_eval(value, bound, scopes)
                   or _holds_code_eval(value, bound, scopes)
                   for value in node.values)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return may_be_code_eval(node.elt, bound, scopes) \
            or _holds_code_eval(node.elt, bound, scopes)
    return False


def _scan(node, bound, scopes):
    """What value does this expression hold, and does it HAND a builtin on?

    Returns `(is_builtin, holds)`: `is_builtin` is true when the value is,
    or may be, a code-evaluating builtin (the EFFECTIVE callee or stored
    value); `holds` is true when the expression hands a builtin to something
    the walk cannot follow. This is the property, read from the VALUE, not
    from a list of node types: every way Python builds a value (reference,
    projection, choice, selection, return, container) reaches the builtin
    the same way, and one passed to a call or used as a lookup key is a
    hand-off. A sixth spelling is resolved by the same rules, not a branch.
    """
    if denotes_code_eval(node, bound, scopes) or _builtin_projection(
            node, bound, scopes):
        return True, False
    if isinstance(node, ast.Lambda):
        return False, False
    if isinstance(node, ast.IfExp):
        body = _scan(node.body, bound, scopes)
        orelse = _scan(node.orelse, bound, scopes)
        return body[0] or orelse[0], body[1] or orelse[1]
    if isinstance(node, ast.BoolOp):
        results = [_scan(value, bound, scopes) for value in node.values]
        return any(r[0] for r in results), any(r[1] for r in results)
    if isinstance(node, ast.Subscript):
        if _is_builtins_namespace(node.value, bound) \
                and isinstance(node.slice, ast.Constant) \
                and node.slice.value in CODE_EVAL_BUILTINS:
            # a constant key selects the builtin out of the builtins
            # namespace, the `__dict__` route the operation axis reads
            return True, False
        key_is, key_holds = _scan(node.slice, bound, scopes)
        element = _element_node(node, bound, scopes)
        if element is not None:
            value_is, value_holds = _scan(element, bound, scopes)
        else:
            # An unreadable key selects a value the walk cannot name; the
            # fail-closed answer reads the container's OWN value.
            value_is = _holds_code_eval(node.value, bound, scopes) \
                or _scan(node.value, bound, scopes)[0] \
                or _scan(node.value, bound, scopes)[1]
            value_holds = False
        return value_is, value_holds or key_is or key_holds
    if isinstance(node, ast.Call):
        result = _is_plain_lambda(node.func) and not _arguments(node) \
            and _scan(node.func.body, bound, scopes)[0]
        handed = any(_scan(argument, bound, scopes)[0]
                     or _scan(argument, bound, scopes)[1]
                     for argument in _arguments(node))
        return result, handed or _scan(node.func, bound, scopes)[1]
    return False, any(
        _scan(child, bound, scopes)[0] or _scan(child, bound, scopes)[1]
        for child in ast.iter_child_nodes(node))


def may_be_code_eval(node, bound, scopes):
    """The EFFECTIVE callee, however it is spelled: does this expression
    evaluate, or may it evaluate, to a code-evaluating builtin?"""
    return _scan(node, bound, scopes)[0]


def yields_code_eval(value, bound, scopes):
    """True when a store's value DELIVERS a code-evaluating builtin to a name
    the walk cannot follow. A store binds its value to a name, so it delivers
    when that value is the builtin or hands it on (an argument, a lookup key,
    a builtin in a bound container). A store binding a call's RESULT binds no
    builtin — a builtin that is the effective callee is a USE the declared
    call-result limit covers — which is the same value-resolution the call arm
    uses, so a constant program reaches the builtin however it is spelled."""
    is_builtin, holds = _scan(value, bound, scopes)
    return is_builtin or holds
