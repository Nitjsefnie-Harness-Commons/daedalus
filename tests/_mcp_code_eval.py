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
    """The code-evaluating builtin this node evaluates to, or nothing.

    A bare name is the builtin only where no enclosing scope binds it
    (`scopes`); the module attribute and the constant `getattr` are the same
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


def yields_code_eval(value, bound, scopes):
    """True when a store's value can hide a code-evaluating builtin, read
    the same recursive way the operation recogniser reads a hidden operation.

    The property, not the shapes that have been met: a node that IS a reach
    is a hide, and otherwise every child is read, so a builtin nested in any
    container — a dict, list, tuple, set, call argument, or a container of
    containers — is the same hole as a bare one. Unlike the operation
    recogniser it does not stop at a call, because a recognised reach call
    (`getattr(builtins, 'eval')`) evaluates to the builtin itself.
    """
    if denotes_code_eval(value, bound, scopes):
        return True
    return any(yields_code_eval(child, bound, scopes)
               for child in ast.iter_child_nodes(value))
