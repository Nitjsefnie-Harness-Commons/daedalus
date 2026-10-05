"""The code-evaluating axis of the import-closure walk: recognition, once.

`eval`, `exec` and `compile` evaluate a program, and a program can name the
import-by-name operation, so a constant string handed to one is a hole the
tracked-name map left open. This module owns that axis so the guard's shared
store grammar (`_hidden`) routes the code-eval store through the same
decision it routes the operation and the registry through.

It also owns the SCOPE model both axes resolve a name against, because there
is one question behind both: does this name resolve to the builtin here?
`_Scopes.denotes_builtin` answers it for any builtin, and the fold asks it
about `bool` — the builtin whose value the index axis computes. The shadow
test is a PROPERTY, not a list: `symtable` is Python's own binding grammar,
so a name no enclosing scope binds is the builtin, and a `from builtins
import X` binds the builtin ITSELF and is not a shadow either.

`is_code_evaluating` deliberately asks the looser question, over every alias
in the module rather than over the one the use resolves to: a false
positive there costs a refusal, and a false negative would cost a closure
entry. The two directions are not the same, so they are not the same answer.

`denotes_builtin` asks the alias question at full strength, and a `from
builtins` binding is EVIDENCE of a builtin rather than a fact of one. Which
scope binds the name is half the question; the other half is whether that
binding has been ESTABLISHED at the use, and `symtable` is a static grammar
and cannot answer it, so the walk reads it off the source: a binding the
module may not have executed yet — one ordered after the use, or one inside a
statement that runs only sometimes — is not one, and the name is not the
builtin there. A scope the walk cannot line up with the resolver's is not one
either. Both are refusals, which is the direction a false negative is cheap in.

The same grammar does not answer the SHADOW question either: a store under a
`global` declaration is a module binding that leaves the root symbol imported
and not assigned, and the resolver's answer is on the NESTED side of it — a
symbol the compiler says a scope binds — so the walk reads the module's own
rebindings from there rather than from the nodes it walks.
"""
import ast
import symtable
from typing import TypeGuard


# The builtins that evaluate a program; one set reads every direct reach.
CODE_EVAL_BUILTINS = ('eval', 'exec', 'compile')

# The scope kinds a name can be local to, INCLUDING the comprehensions: a
# generator's body is its own scope, and a list or dict comprehension is one
# on every interpreter that has not inlined it. Which of them the running
# interpreter actually gives a scope of its own is the resolver's answer and
# not this table's, so a comprehension with no scope is simply not matched.
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                ast.Lambda) + _COMPREHENSIONS
_COMP_NAMES = {ast.ListComp: 'listcomp', ast.SetComp: 'setcomp',
               ast.DictComp: 'dictcomp', ast.GeneratorExp: 'genexpr'}

# The statement kinds whose body runs only SOMETIMES. A `from builtins`
# inside one is not a binding at the next statement, so nothing there can be
# decided from it. A comprehension's own `if` clauses cannot bind a name and
# are not here.
_GUARDED = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
            ast.With, ast.AsyncWith, ast.Match)


class _Scopes:
    """Which names one reference resolves to, so a builtin is told apart from
    a same-named local.

    A scope the walk cannot line up with the resolver's is read in its
    enclosing scope, and every node inside it is marked UNCERTAIN: for a
    module-level reference the enclosing scope errs toward the builtin and
    refuses, but a name that is local to the scope the walk lost is not the
    builtin, and `denotes_builtin` reads the mark and declines instead of
    answering for a scope it does not have.
    """

    def __init__(self, tree, source, filename):
        self._root = symtable.symtable(source, filename, 'exec')
        self._root_binds = {symbol.get_name() for symbol
                            in self._root.get_symbols()
                            if symbol.is_assigned() or symbol.is_imported()
                            or symbol.is_parameter() or symbol.is_namespace()}
        self._scope_of = {}
        self._from_builtins = set()
        self._module_bind_line = {}
        # The scope model the alias question is read off: which table
        # encloses which, which names each binds of its own, what each one
        # bound from `builtins` and under which name, and whether that
        # statement runs every time the scope does.
        self._enclosing = {}
        self._local = {}
        self._alias = {}
        self._uncertain = {}
        # The names a NESTED scope rebinds in the MODULE, read off the
        # resolver's own tables before the walk rather than off the nodes it
        # walks. `symtable` is the compiler's answer to "does this scope bind
        # this name", and it is an answer about every binding form rather than
        # about the ones an AST walk enumerates — see `_rebindings`.
        self._rebound = self._rebindings()
        # How far into each table's children the walk has read. The resolver
        # builds them in source order, and so does this walk EXCEPT that a
        # function's body is walked before its decorator list, so the cursor
        # has to be able to go back.
        self._cursor = {}
        self._walk(tree, self._root)

    def _rebindings(self):
        """The names a NESTED scope binds in the module it belongs to.

        A `global b` says `b` belongs to the module, so a binding a nested
        scope makes under one is a MODULE binding — and the root symbol does
        not report it, because the store is in a table the resolver does not
        fold back into its parent. The resolver's answer is on the NESTED side
        instead, and it is the COMPILER's: a symbol that `is_global()` is not
        local to the scope it is in, so binding it binds the module's, and the
        compiler reports every form that binds one — a store, an augmented
        store, a `del`, an `except ... as`, a `for` or `with` target, a
        walrus, a `def`, a class and an import all read as `is_assigned()` or
        `is_imported()`, while a USE, a subscript or attribute store and a
        bare declaration read as neither. So the set comes from the resolver's
        own tables rather than from a list of node types, and a binding form
        nobody thought of is in it for free.

        The ROOT is not walked: a module-level name is global to the resolver
        and local to the module at once, so taking its own symbols would make
        every alias a rebinding. A `nonlocal` store is not here either, for
        the reason it never needed to be: the resolver reports the ENCLOSING
        function's own symbol as assigned.

        The cost is one name set with no scope on it, so a module-level use
        standing ABOVE a rebinding is refused where the runtime reaches. That
        is this walk's cheap direction, and it is bounded: only a name some
        nested scope binds is in it.
        """
        rebound = set()
        pending = list(self._root.get_children())
        while pending:
            table = pending.pop()
            pending.extend(table.get_children())
            rebound.update(symbol.get_name() for symbol in table.get_symbols()
                           if symbol.is_global()
                           and (symbol.is_assigned() or symbol.is_imported()))
        return rebound

    @staticmethod
    def _symbols(table):
        return {symbol.get_name(): symbol for symbol in table.get_symbols()}

    def _match(self, table, name, line):
        """The resolver's scope for a named scope node, and whether one was
        there and not taken.

        Children arrive in SOURCE order, and so do the nodes that name them —
        except that `ast` walks a function's BODY before its decorator list,
        so a scope written ABOVE the `def` is named after one below it. A
        cursor that may only move forwards cannot answer that, so it returns
        to the start when the line it is at has already passed. That is the
        one place the cursor discards rather than seeks, and it is bounded by
        the number of scopes in one table rather than by the module: a rescan
        is a pass over a symbol table's own children. It cannot produce a
        wrong answer, because every candidate it re-examines is still matched
        on its name AND its line — a rewind makes more candidates available to
        that test, never fewer, and a name the table does not hold at that
        line is still a miss. A name and a line are not enough on their own
        either: two sibling scopes can share both — two lambdas separated by
        `;` — and telling them apart by which is next is what a cursor is for.

        The second answer is the walk's own miss. No scope of that kind here
        is a real answer (a comprehension the running interpreter has
        inlined has no scope), and is not one; a scope that IS there and was
        not taken is a miss, and every node inside it is marked uncertain.
        """
        children = table.get_children()
        index = self._cursor.get(id(table), 0)
        if index and children[index - 1].get_lineno() > line:
            index = 0
        while index < len(children) and children[index].get_lineno() < line:
            index += 1
        for later in range(index, len(children)):
            child = children[later]
            if child.get_lineno() > line:
                return None, False
            if child.get_name() == name:
                self._cursor[id(table)] = later + 1
                return child, False
        return None, (index < len(children)
                      and children[index].get_lineno() == line)

    def _note(self, name, lineno):
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

    def _walk(self, node, table, guarded=False, uncertain=False):
        self._scope_of[id(node)] = table
        if uncertain:
            self._uncertain[id(node)] = True
        if table is self._root:
            # Only module-scope forms count; a nested function or
            # comprehension body is its own scope and is not walked as root.
            self._record_module_binding(node)
        if isinstance(node, ast.ImportFrom) and node.module == 'builtins' \
                and not node.level:
            self._record_builtin_alias(node, table, guarded)
        enclosing = table
        if isinstance(node, _SCOPE_NODES):
            name = 'lambda' if isinstance(node, ast.Lambda) else (
                _COMP_NAMES[type(node)] if isinstance(node, _COMPREHENSIONS)
                else node.name)
            child, missed = self._match(table, name, node.lineno)
            if child is not None:
                self._enclosing[id(child)] = table
                self._local[id(child)] = {symbol.get_name() for symbol
                                          in child.get_symbols()
                                          if symbol.is_local()}
                # A scope's own statements are its own: a `def` under a
                # conditional binds inside it whenever the function is
                # called, whatever ran before it.
                table, guarded = child, False
            else:
                uncertain = uncertain or missed
        inside = guarded or isinstance(node, _GUARDED)
        # Python evaluates a comprehension's FIRST iterable in the scope that
        # ENCLOSES it and every other part inside its own, so the iterable is
        # walked where the runtime reads it. A DECORATOR is the same: it runs
        # where the `def` it decorates is written, and the function's own
        # scope does not exist while its decorators are evaluated.
        outside = {id(node.generators[0].iter)} \
            if isinstance(node, _COMPREHENSIONS) else set()
        outside.update(id(decorator)
                       for decorator in getattr(node, 'decorator_list', ()))
        for child in ast.iter_child_nodes(node):
            self._walk(child, enclosing if id(child) in outside else table,
                       inside, uncertain)

    def _record_builtin_alias(self, node, table, guarded):
        """A `from builtins import X [as y]` binds the builtin ITSELF, so `y`
        is the builtin and not a shadow of it — once the statement has RUN.

        Recorded against the scope the statement is in rather than against
        the module, because a nearer binding is what takes it back, and a
        name this walk resolves to a nearer scope's alias is that alias.
        `guarded` says the statement sits inside a body that runs only
        sometimes, so the binding is possible at a use and not established.
        """
        aliases = self._alias.setdefault(id(table), {})
        for alias in node.names:
            aliases[alias.asname or alias.name] = (alias.name, node.lineno,
                                                   guarded)
            if alias.name in CODE_EVAL_BUILTINS:
                self._from_builtins.add(alias.asname or alias.name)

    def is_code_evaluating(self, node):
        """The name is a code-evaluating builtin at `node`'s own scope: an
        unshadowed builtin name, or one bound by a from-builtins import
        anywhere in the module. The looser half of the alias question, kept
        loose on purpose — see `denotes_builtin`."""
        return node.id in self._from_builtins or (
            node.id in CODE_EVAL_BUILTINS and self.is_builtin(node))

    def _owner(self, table, name):
        """The nearest enclosing scope that binds `name`, or None when no
        scope does — which is what leaves it the builtin."""
        while table is not None:
            bound = self._root_binds if table is self._root \
                else self._local.get(id(table), ())
            if name in bound:
                return table
            table = self._enclosing.get(id(table))
        return None

    def denotes_builtin(self, node, name) -> bool:
        """Whether a name reference IS the builtin `name` at its own scope.

        The question is what the name RESOLVES TO, so it is read off the
        module's own symbol table rather than off a spelling and a map that
        carries no builtin: the builtin under its own name where nothing
        binds it, and a `from builtins import name [as x]` alias where the
        scope that binds it imports the builtin itself. `_is_the_import`
        reads the two apart, and the ORDER the module runs in says the rest:
        an alias is the builtin only where its own import has already run by
        the time the name is read.
        """
        if self._uncertain.get(id(node)):
            return False
        if node.id == name and self.is_builtin(node):
            return True
        table = self._scope_of.get(id(node))
        owner = self._owner(table, node.id)
        if owner is None or not self._is_the_import(owner, node.id):
            return False
        bound = self._alias.get(id(owner), {}).get(node.id)
        if bound is None or bound[0] != name:
            return False
        # The binding has to have RUN by the time the name is read: a
        # statement the module may skip leaves the name unbound, and one
        # ordered after the use has not run either. Neither is a builtin.
        return not bound[2] and node.lineno >= bound[1]

    def _is_the_import(self, table, name):
        """Whether `symtable` says `name` is an IMPORT here and not a shadow
        of one.

        It reports a store as assigned, a parameter as a parameter and a
        class as a namespace, and any of those shadows the import over the
        same name. What it does NOT report is a binding a nested scope makes
        under a `global` declaration — the root symbol is left imported and
        not assigned all the same — so `_rebound` carries that.
        """
        symbol = self._symbols(table).get(name)
        return (symbol is not None and symbol.is_imported()
                and not symbol.is_assigned() and not symbol.is_parameter()
                and not symbol.is_namespace() and name not in self._rebound)

    def _binds_itself(self, name):
        """Whether the module binds `name` to the builtin `name` IS.

        A `from builtins import bool` under its own name binds the builtin
        to itself, so it is not a shadow of the name whichever way the
        statement goes: whether it runs or not, and whether it runs before
        the use or after, the name is the builtin. Only a store over the
        same name takes it back.
        """
        bound = self._alias.get(id(self._root), {}).get(name)
        return (bound is not None and bound[0] == name
                and self._is_the_import(self._root, name))

    def is_builtin(self, node):
        """The name is an unshadowed builtin at `node`'s own scope. A module
        runs top to bottom, so a MODULE-TOP-LEVEL reference is the builtin
        only when the module leaves its name unbound BY THE TIME OF THE USE:
        a use PRECEDING its own later module-level binding still reads the
        real builtin, while one after it sees the bound name. A reference
        inside a function runs after the module has loaded, so any module
        binding shadows it and order is irrelevant there. A binding that is
        the name bound to ITSELF is not a binding at all."""
        table = self._scope_of.get(id(node))
        symbol = self._symbols(table).get(node.id) if table else None
        if symbol is None:
            return True
        if table is self._root:
            if node.id not in self._root_binds:
                return True
            if self._binds_itself(node.id):
                return True
            return node.lineno < self._module_bind_line.get(
                node.id, float('inf'))
        if symbol.is_local() or symbol.is_free():
            return False
        # A reference inside a function runs after the module has loaded, so
        # any module binding shadows it and order is irrelevant there — with
        # the one exception, which is a binding that is not a shadow at all.
        if node.id not in self._root_binds:
            return True
        return self._binds_itself(node.id)


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


def _is_plain_lambda(func: ast.expr) -> TypeGuard[ast.Lambda]:
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


def _element_node(subscript, bound, scopes, elements):
    """The expression a subscript SELECTS, resolved once per node: the first
    ask walks the chain and folds each level's answer into `elements`, and a
    walk that revisits the level reads the fold. None when the selected value
    is not readable."""
    if subscript not in elements:
        elements[subscript] = _select_element(
            subscript, bound, scopes, elements)
    return elements[subscript]


def _select_element(subscript, bound, scopes, elements):
    """The element one subscript names, or None when it is not readable. An
    int key reads a sequence element, a str key a mapping value, so a
    two-level or string-key selection resolves by the same rule as a
    one-level one."""
    key = subscript.slice
    container = subscript.value
    if isinstance(container, ast.Subscript):
        container = _element_node(container, bound, scopes, elements)
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


def _scan(node, bound, scopes, elements):
    """What value does this expression hold, and does it HAND a builtin on?

    Returns `(is_builtin, holds)`: `is_builtin` is true when the value is,
    or may be, a code-evaluating builtin (the EFFECTIVE callee or stored
    value); `holds` is true when the expression hands a builtin to something
    the walk cannot follow. This is the property, read from the VALUE, not
    from a list of node types: every way Python builds a value (reference,
    projection, choice, selection, return, container) reaches the builtin
    the same way, and one passed to a call or used as a lookup key is a
    hand-off. A sixth spelling is resolved by the same rules, not a branch.
    Each verdict is asked ONCE: where two arms of one node read the same
    subtree, the pair is computed in a single descent, and `elements` is the
    fold that lets a revisited subscript re-read its selection instead of
    re-walking the chain — so the cost tracks the nodes, not the revisits.
    """
    if denotes_code_eval(node, bound, scopes) or _builtin_projection(
            node, bound, scopes):
        return True, False
    if isinstance(node, ast.Lambda):
        return False, False
    if isinstance(node, ast.IfExp):
        body = _scan(node.body, bound, scopes, elements)
        orelse = _scan(node.orelse, bound, scopes, elements)
        return body[0] or orelse[0], body[1] or orelse[1]
    if isinstance(node, ast.BoolOp):
        results = [_scan(value, bound, scopes, elements)
                   for value in node.values]
        return any(r[0] for r in results), any(r[1] for r in results)
    if isinstance(node, ast.Subscript):
        if _is_builtins_namespace(node.value, bound) \
                and isinstance(node.slice, ast.Constant) \
                and node.slice.value in CODE_EVAL_BUILTINS:
            # a constant key selects the builtin out of the builtins
            # namespace, the `__dict__` route the operation axis reads
            return True, False
        key_is, key_holds = _scan(node.slice, bound, scopes, elements)
        element = _element_node(node, bound, scopes, elements)
        if element is not None:
            value_is, value_holds = _scan(element, bound, scopes, elements)
        else:
            # An unreadable key selects a value the walk cannot name; the
            # fail-closed answer reads the container's OWN value.
            container = _scan(node.value, bound, scopes, elements)
            value_is = _holds_code_eval(node.value, bound, scopes) \
                or container[0] or container[1]
            value_holds = False
        return value_is, value_holds or key_is or key_holds
    if isinstance(node, ast.Call):
        result = _is_plain_lambda(node.func) and not _arguments(node) \
            and _scan(node.func.body, bound, scopes, elements)[0]
        handed = any((pair := _scan(argument, bound, scopes, elements))[0]
                     or pair[1]
                     for argument in _arguments(node))
        return result, handed or _scan(node.func, bound, scopes, elements)[1]
    return False, any((pair := _scan(child, bound, scopes, elements))[0]
                      or pair[1]
                      for child in ast.iter_child_nodes(node))


def may_be_code_eval(node, bound, scopes):
    """The EFFECTIVE callee, however it is spelled: does this expression
    evaluate, or may it evaluate, to a code-evaluating builtin?"""
    return _scan(node, bound, scopes, {})[0]


def yields_code_eval(value, bound, scopes):
    """True when a store's value DELIVERS a code-evaluating builtin to a name
    the walk cannot follow. A store binds its value to a name, so it delivers
    when that value is the builtin or hands it on (an argument, a lookup key,
    a builtin in a bound container). A store binding a call's RESULT binds no
    builtin — a builtin that is the effective callee is a USE the declared
    call-result limit covers — which is the same value-resolution the call arm
    uses, so a constant program reaches the builtin however it is spelled."""
    is_builtin, holds = _scan(value, bound, scopes, {})
    return is_builtin or holds
