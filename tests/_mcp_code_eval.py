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


def _selected_element(node):
    """The element a subscript selects, when that element is readable; None
    when the container or the index is not, so the walk never guesses a
    builtin the runtime would not select."""
    index = node.slice
    container = node.value
    if isinstance(index, ast.Constant) and isinstance(index.value, int):
        if isinstance(container, (ast.Tuple, ast.List)) and \
                -len(container.elts) <= index.value < len(container.elts):
            return container.elts[index.value]
        if index.value == 0 and isinstance(
                container, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return container.elt
    return None


def _arguments(call):
    return list(call.args) + [keyword.value for keyword in call.keywords]


def may_be_code_eval(node, bound, scopes):
    """Does this expression evaluate, or may it evaluate, to a code-evaluating
    builtin — the EFFECTIVE callee, however it is spelled?

    The property, not a list of callee spellings: a node that denotes the
    builtin directly is one; a conditional or boolean choice is one when
    either branch is; a subscript is one when the element it SELECTS is; and a
    no-parameter lambda called with no arguments is one when its body is. Each
    resolves the value the runtime would actually use, so a form nobody
    thought of is read by the same rule rather than a new branch.
    """
    if denotes_code_eval(node, bound, scopes):
        return True
    if isinstance(node, ast.IfExp):
        return may_be_code_eval(node.body, bound, scopes) or \
            may_be_code_eval(node.orelse, bound, scopes)
    if isinstance(node, ast.BoolOp):
        return any(may_be_code_eval(value, bound, scopes)
                   for value in node.values)
    if isinstance(node, ast.Subscript):
        if may_be_code_eval(node.slice, bound, scopes):
            return False
        element = _selected_element(node)
        return element is not None and may_be_code_eval(
            element, bound, scopes)
    if isinstance(node, ast.Call) and _is_plain_lambda(node.func) \
            and not _arguments(node):
        return may_be_code_eval(node.func.body, bound, scopes)
    return False


def _data_delivers(node, bound, scopes, is_callee=False):
    """Does evaluating this expression HAND a code-evaluating builtin to a
    callee or lookup the walk cannot follow — an argument, a lookup key, or a
    builtin held in a value that is itself passed or bound?

    A builtin in a data position is a DELIVERY. The one thing that is not a
    delivery is the builtin in CALLEE position: the call arm reads its program
    and the value that reaches the store is the call's result. A lambda is a
    function the walk cannot follow, so a builtin passed to one is delivered.
    """
    if denotes_code_eval(node, bound, scopes):
        return not is_callee
    if isinstance(node, ast.Lambda):
        return False
    if isinstance(node, ast.Call):
        if any(_data_delivers(argument, bound, scopes)
               for argument in _arguments(node)):
            return True
        return _data_delivers(node.func, bound, scopes, is_callee=True)
    if isinstance(node, ast.IfExp):
        return _data_delivers(node.body, bound, scopes, is_callee) or \
            _data_delivers(node.orelse, bound, scopes, is_callee)
    if isinstance(node, ast.BoolOp):
        return any(_data_delivers(value, bound, scopes, is_callee)
                   for value in node.values)
    if isinstance(node, ast.Subscript):
        if _data_delivers(node.slice, bound, scopes):
            return True
        element = _selected_element(node)
        if element is not None:
            return _data_delivers(element, bound, scopes, is_callee)
        return _data_delivers(node.value, bound, scopes, is_callee)
    return any(_data_delivers(child, bound, scopes, is_callee=False)
               for child in ast.iter_child_nodes(node))


def yields_code_eval(value, bound, scopes):
    """True when a store's value DELIVERS a code-evaluating builtin to a name
    the walk cannot follow.

    The property is delivery, decided over the value the store will hold. A
    store that binds a call's RESULT binds no builtin, so a builtin that is
    the call's EFFECTIVE callee — however it is reached, including through a
    conditional, a boolean choice, a comprehension, or a subscript that
    selects it — is a USE, and the declared call-result limit covers the
    result. A builtin in a DATA position of the value (an argument, a lookup
    key, a builtin held in a value that is passed or bound) is a DELIVERY, and
    that is the only thing a Call can be refused for. A store that binds the
    builtin itself — a bare builtin, or a conditional or subscript that
    resolves to one — is not a Call, and is a delivery to the name itself.
    """
    if isinstance(value, ast.Call):
        if may_be_code_eval(value, bound, scopes):
            return True
        if any(_data_delivers(argument, bound, scopes)
               for argument in _arguments(value)):
            return True
        return _data_delivers(value.func, bound, scopes, is_callee=True)
    return may_be_code_eval(value, bound, scopes) or \
        _data_delivers(value, bound, scopes)
