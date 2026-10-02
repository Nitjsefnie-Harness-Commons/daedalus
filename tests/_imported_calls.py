"""How a call a control makes into another `tests/_*.py` file is resolved.

Not a suite itself — run_tests.py only loads `test_*.py`.

The guard resolves a callee through a `def` in the same file or a pair
tabled in `tests/_control_calls.py`; a control that imports its copy
helper is neither, so this reads the file the import names and hands the
callee back as a plain `ast.FunctionDef`, which is what lets the path
proof and the write rules work on it unchanged. A hop past the bound, a
file that will not parse, and a name the file does not define all answer
None, so the caller keeps refusing. Each of those refusals has its own
control in `tests/test_shared_helper_calls.py` -- the hop bound, a helper
that cannot be read, a name the file does not define, a cycle -- each
reached through the guard rather than by importing this module.
"""
import ast

from _control_calls import ModuleNames, shared_helper_path

# A HOP is one shared-helper file a resolution reads, so this bounds files
# crossed rather than the nesting _control_paths.py's _MAX_PROOF_DEPTH
# bounds inside one file: a helper two files deep can prove a path one
# call deep.
MAX_SHARED_HOPS = 2


def module_functions(tree, names):
    return {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and names.is_unique_def(node.name)}


def module_scopes(tree):
    """Every module-level scope a call can name; the functions are a subset.

    The proof context holds the functions only — a class is not a callee
    and has no signature to seed — while the judgement needs a class body
    too, because a local `def` reaching one gets it judged.
    """
    return {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef))}


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
        if name in self.functions:
            return self.functions[name], self
        imported = self.resolver.imported(self.names, name)
        if imported is None:
            return None, None
        return imported.function, imported.module.context


class SharedModule:
    def __init__(self, tree, label, context):
        self.tree = tree
        self.label = label
        self.context = context

    @staticmethod
    def read(path, root, resolver):
        """The module at `path`, or None when it cannot be read whole.

        An unreadable file is a refusal and not an empty module: "nothing
        found" and "nothing to look at" answer a subset check alike. The
        three failures are separate arms, and the one the control plants a
        file for is the one a narrowed `except` drops.
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
    def __init__(self, module, function):
        self.module = module
        self.function = function


class SharedResolver:
    def __init__(self, root, hops=MAX_SHARED_HOPS):
        self.root = root
        self.hops = hops
        self.modules = {}

    def child(self):
        return SharedResolver(self.root, self.hops - 1)

    def _module(self, path):
        if path not in self.modules:
            self.modules[path] = SharedModule.read(
                path, self.root, self.child())
        return self.modules[path]

    def imported(self, names, name):
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


def reached_functions(entry, scopes):
    """The names of the module-level scopes `entry` reaches.

    A name reaches, not only a callee: `sorted(items, key=_evil)` passes
    the function as a value, and a class is a scope the region can name.
    What the entry does not name is not returned, and neither is a nested
    scope: those are regions of a reached function, and
    `tests/_control_writes.py` judges them from the name set returned
    here. A callback or a class body that is not reached is not judged.
    """
    reached = {entry.name}
    pending = [entry]
    while pending:
        for node in ast.walk(pending.pop()):
            if not isinstance(node, ast.Name):
                continue
            if not isinstance(node.ctx, ast.Load):
                continue
            if node.id in scopes and node.id not in reached:
                reached.add(node.id)
                pending.append(scopes[node.id])
    return reached
