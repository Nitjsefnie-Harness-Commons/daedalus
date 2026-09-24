"""The code-evaluating axis of the import-closure walk: recognition, once.

`eval`, `exec` and `compile` evaluate a program, and a program can name the
import-by-name operation, so a constant string handed to one is a hole the
tracked-name map left open. This module owns that axis end to end — the one
named set of builtins, the shadow-aware resolution that tells a builtin from a
same-named local, the reach recogniser, and the recursive store recogniser —
so the guard's shared store grammar (`_hidden`) can route the code-eval store
through the same decision it routes the operation and the registry through,
rather than bolting a second check onto one store form.

The shadow test is a PROPERTY, not a list: `symtable` is Python's own binding
grammar, so a name no enclosing scope binds is the builtin, and a local,
parameter, closure, class body or module store is read by the resolver itself.
A `from builtins import eval` binds the builtin itself and is not a shadow.
"""
import ast
import symtable


# The builtins that evaluate a program; one set reads every direct reach.
CODE_EVAL_BUILTINS = ('eval', 'exec', 'compile')


class _Scopes:
    """Which names one reference resolves to, so a builtin is told apart from
    a same-named local. A scope the walk cannot line up with the resolver's
    errs toward the builtin, so a correlation miss refuses, never accepts."""

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
        self._walk(tree, self._root)

    @staticmethod
    def _symbols(table):
        return {symbol.get_name(): symbol for symbol in table.get_symbols()}

    @staticmethod
    def _match(table, name, line):
        for child in table.get_children():
            if child.get_name() == name and child.get_lineno() == line:
                return child
        return None

    def _walk(self, node, table):
        self._scope_of[id(node)] = table
        if isinstance(node, ast.ImportFrom) and node.module == 'builtins' \
                and not node.level:
            # A from-builtins import binds the builtin ITSELF, so the
            # module-bound rule must not read it as a shadow.
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
        """The name is an unshadowed builtin at `node`'s own scope."""
        table = self._scope_of.get(id(node))
        symbol = self._symbols(table).get(node.id) if table else None
        if symbol is None:
            return True
        if symbol.is_local() or symbol.is_free():
            return False
        return node.id not in self._root_binds


def scopes_for(tree, source, filename):
    """The scope model one module's source resolves against."""
    return _Scopes(tree, source, filename)


def _names_builtins(node, bound):
    return isinstance(node, ast.Name) and bound.get(node.id) == 'builtins'


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
    """A lambda that takes no parameters, so a no-argument call to it returns
    the lambda's body directly."""
    return isinstance(func, ast.Lambda) and not func.args.args \
        and not func.args.posonlyargs and not func.args.kwonlyargs \
        and not func.args.vararg and not func.args.kwarg


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
    and the key by their values, and through any further subscripts the
    selection lands on; None when the value it selects is not readable.

    An int key reads a sequence element, a str key reads a mapping value, and
    a selection that lands on another subscript is followed, so a two-level
    or string-key selection resolves by the same rule as a one-level one.
    """
    key = subscript.slice
    container = subscript.value
    if isinstance(container, ast.Subscript):
        # A selection whose container is itself a selection: resolve the
        # container's value first, so a two-level or string-key selection
        # reads by the same rule as a one-level one.
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

    Returns `(is_builtin, holds)`. `is_builtin` is true when the value the
    expression evaluates to is, or may be, a code-evaluating builtin — the
    EFFECTIVE callee or stored value. `holds` is true when the expression
    hands a builtin to something the walk cannot follow: an argument, a lookup
    key, or a builtin sitting in a container that is itself bound or passed.

    This is the property, read from the value rather than from a list of node
    types. Every way Python builds a value is one of: a reference, a
    projection, a choice, a selection, a return, or a container — and a
    builtin reached through any of them is the value, while one passed to
    another call or used as a lookup key is a hand-off. A sixth spelling of
    "evaluates to the builtin" is resolved by the same rules, not a new branch.
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
        key_is, key_holds = _scan(node.slice, bound, scopes)
        element = _element_node(node, bound, scopes)
        if element is not None:
            value_is, value_holds = _scan(element, bound, scopes)
        else:
            # An unreadable key (or container) selects a value the walk
            # cannot name; it is the builtin only when the container provably
            # holds one, which is the fail-closed answer.
            value_is = _holds_code_eval(node.value, bound, scopes)
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
    the walk cannot follow.

    A store binds its value to a name, so it delivers when that value is the
    builtin (a use that reaches a name) or when it hands the builtin on (an
    argument, a lookup key, or a builtin held in a container that is bound).
    A store that binds a call's RESULT binds no builtin, so a builtin that is
    the call's effective callee is a USE, and the declared call-result limit
    covers the result; that is the same value-resolution the call arm uses to
    read a program, so a constant program reaches the builtin however the
    callee is spelled.
    """
    is_builtin, holds = _scan(value, bound, scopes)
    return is_builtin or holds
