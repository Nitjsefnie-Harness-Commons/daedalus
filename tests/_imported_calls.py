"""How a call a control makes into another `tests/_*.py` file is resolved.

Not a suite itself — run_tests.py only loads `test_*.py`.

The guard resolves a callee through a `def` in the same file or a pair
tabled in `tests/_control_calls.py`; a control that imports its copy
helper is neither, so this reads the file the import names and hands the
callee back as a plain `ast.FunctionDef`, which is what lets the path
proof and the write rules work on it unchanged. A hop past the bound, a
file that will not parse, and a name the file does not define all answer
None, so the caller keeps refusing.
"""
import ast

from _control_calls import ModuleNames, shared_helper_path

# A HOP is one shared-helper file a resolution reads, so this bounds files
# crossed rather than the nesting _control_writes.py's _MAX_PROOF_DEPTH
# bounds inside one file: a helper two files deep can prove a path one
# call deep.
MAX_SHARED_HOPS = 2


def module_functions(tree, names):
    """The module-level functions a call in this file can resolve to."""
    return {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and names.is_unique_def(node.name)}


class Context:
    """The functions a call in one file resolves to, and their file.

    A name the file does not define is looked for in the module its
    import names, and the proof then moves to THAT module's own table, so
    a helper's body is read against the file that writes it.
    """

    def __init__(self, functions, names, resolver):
        self.functions = functions
        self.names = names
        self.resolver = resolver

    def resolve(self, name):
        """The function `name` names, and the context to prove it in."""
        if name in self.functions:
            return self.functions[name], self
        imported = self.resolver.imported(self.names, name)
        if imported is None:
            return None, None
        return imported.function, imported.module.context


class SharedModule:
    """One `tests/_*.py` file a control imported a callee out of."""

    def __init__(self, tree, label, context):
        self.tree = tree
        self.label = label
        self.context = context

    @staticmethod
    def read(path, root, resolver):
        """The module at `path`, or None when it cannot be read whole.

        An unreadable file is a refusal and not an empty module: "nothing
        found" and "nothing to look at" answer a subset check alike.
        """
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, SyntaxError):
            return None
        try:
            label = path.relative_to(root).as_posix()
        except (TypeError, ValueError):
            label = path.name
        names = ModuleNames(tree)
        return SharedModule(
            tree, label,
            Context(module_functions(tree, names), names, resolver))


class Import:
    """A callee resolved out of a shared helper module."""

    def __init__(self, module, function):
        self.module = module
        self.function = function


class SharedResolver:
    """The shared-helper imports of one file, and the files they name."""

    def __init__(self, root, hops=MAX_SHARED_HOPS):
        self.root = root
        self.hops = hops
        self.modules = {}

    def child(self):
        """The resolver one hop further from the control that started it."""
        return SharedResolver(self.root, self.hops - 1)

    def _module(self, path):
        if path not in self.modules:
            self.modules[path] = SharedModule.read(
                path, self.root, self.child())
        return self.modules[path]

    def imported(self, names, name):
        """The imported callee `name` binds, or None to keep refusing."""
        if self.hops < 1:
            return None
        path = shared_helper_path(names, name, self.root)
        if path is None:
            return None
        module = self._module(path)
        if module is None:
            return None
        function = module.context.functions.get(name)
        return None if function is None else Import(module, function)


def reached_functions(entry, functions):
    """The named functions `entry` reaches in the file it is written in.

    The rest of a shared module belongs to that module, not to the control
    that imported one of its names. The walk enters nested scopes too, so
    the set is a superset: judging more refuses more, never less.
    """
    reached = {entry.name}
    pending = [entry]
    while pending:
        for call in ast.walk(pending.pop()):
            if not isinstance(call, ast.Call):
                continue
            name = call.func.id if isinstance(call.func, ast.Name) else None
            if name in functions and name not in reached:
                reached.add(name)
                pending.append(functions[name])
    return reached
