"""What a checked control may call, and how each name is bound.

Not a suite itself — run_tests.py only loads `test_*.py`.

Split out of tests/_control_writes.py, which owns the path analysis:
these answer "may this call happen at all, and where is its target",
which is a different question from "is that target control-owned".
A call outside these tables is refused rather than ignored, so a new
primitive a control needs is a reviewed line here, not a silent gap.
"""
import ast
from collections import Counter

_UNRESOLVED_MODE = object()
_READ_MODES = frozenset({'r', 'rb', 'rt'})
_WRITE_MODES = frozenset({'w', 'a', 'x', 'wb', 'ab', 'xb'})
_NAMESPACES = frozenset({'globals', 'vars', 'locals'})
_PURE_NAMES = frozenset({
    'AssertionError', 'Path', 'SystemExit', 'ValueError', 'all', 'any',
    'dict', 'len', 'list', 'locals', 'repr', 'sorted', 'str'})
_PURE_IMPORTS = frozenset({
    ('pathlib', 'Path'),
    ('_control_writes', 'control_write_violations'),
    ('_coverage_guard', '_coverage_environment_violations'),
    ('_coverage_guard', '_synthetic_violations'),
})
_PURE_METHODS = frozenset({
    'append', 'count', 'decode', 'encode', 'endswith', 'glob', 'index',
    'join', 'read_bytes', 'read_text', 'relative_to', 'resolve', 'rindex',
    'startswith'})
_PURE_MODULE_CALLS = frozenset({
    '_util.child_coverage', '_util.collect', '_util.runner',
    'os.path.join', 'sys.path.insert'})
# Writers, with where the written path arrives as (keyword, position);
# (None, None) is the receiver. A child runs where its cwd points and a
# loaded module runs where it stands, so both write that path.
_WRITER_METHODS = {'write_bytes': (None, None), 'write_text': (None, None),
                   '_runner_tree': (None, 0)}
_WRITER_MODULE_CALLS = {'_util.load': (None, 0),
                        'subprocess.run': ('cwd', None)}
_WRITER_IMPORTS = {('_owned_writes', 'copy_test_tree'): ('root', 0)}


def argument(call, name, position):
    """A call's argument by keyword, else by position, else None."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    if position is not None and len(call.args) > position:
        return call.args[position]
    return None


def has_spread(call):
    """A `*args` or `**kwargs` argument hides the call's real shape."""
    return (any(isinstance(arg, ast.Starred) for arg in call.args)
            or any(keyword.arg is None for keyword in call.keywords))


def pattern_names(node):
    """The names one match-pattern node captures; empty for any other."""
    if isinstance(node, (ast.MatchAs, ast.MatchStar)):
        return [node.name] if node.name else []
    if isinstance(node, ast.MatchMapping):
        return [node.rest] if node.rest else []
    return []


def _write_mode(node, position):
    if has_spread(node):
        return _UNRESOLVED_MODE
    mode = argument(node, 'mode', position)
    if mode is None:
        return None
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        if mode.value in _WRITE_MODES:
            return mode.value
        if mode.value in _READ_MODES:
            return None
    return _UNRESOLVED_MODE


def _is_namespace_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _NAMESPACES
            and not node.args and not node.keywords)


class ModuleNames:
    """How every name in a module is bound, counted across all scopes.

    A name proves something only when it is bound exactly once and the
    binding is one the checker can read: a def, an import, or nothing at
    all for a builtin. A module namespace written through a computed key
    or handled as a whole retires every name at once.
    """

    def __init__(self, tree):
        self.counts = Counter()
        self.imports = {}
        self.modules = set()
        self.defs = set()
        self.namespace_mutated = False
        subscripted = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Subscript)
                    and _is_namespace_call(node.value)):
                subscripted.add(id(node.value))
                key = node.slice
                if (isinstance(key, ast.Constant)
                        and isinstance(key.value, str)):
                    self.counts[key.value] += 1
                else:
                    self.namespace_mutated = True
        for node in ast.walk(tree):
            self._count(node, subscripted)

    def _count(self, node, subscripted):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            self.counts[node.name] += 1
            if not isinstance(node, ast.ClassDef):
                self.defs.add(node.name)
        elif (isinstance(node, ast.Name)
              and isinstance(node.ctx, (ast.Store, ast.Del))):
            self.counts[node.id] += 1
        elif isinstance(node, ast.arg):
            self.counts[node.arg] += 1
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self.counts[node.name] += 1
        elif isinstance(node, (ast.MatchAs, ast.MatchStar,
                               ast.MatchMapping)):
            for name in pattern_names(node):
                self.counts[name] += 1
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                self.counts[name] += 1
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split('.')[0]
                self.counts[bound] += 1
                self.modules.add(bound)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                self.counts[bound] += 1
                self.imports[bound] = (node.module, alias.name)
        elif (_is_namespace_call(node) and id(node) not in subscripted
              and node.func.id != 'locals'):
            self.namespace_mutated = True

    def is_unique_def(self, name):
        return (not self.namespace_mutated and self.counts[name] == 1
                and name in self.defs)

    def import_origin(self, name):
        if self.namespace_mutated or self.counts[name] != 1:
            return None
        return self.imports.get(name)

    def is_module_like(self, name):
        """An imported module, or a name nothing in this module binds."""
        if self.namespace_mutated:
            return False
        return (self.counts[name] == 0
                or (self.counts[name] == 1 and name in self.modules))

    def is_pure_name(self, name):
        return (not self.namespace_mutated and self.counts[name] == 0
                and name in _PURE_NAMES)


def _chain_base(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node


def _is_string_replace(node):
    """str.replace takes two positional arguments; Path.replace, one."""
    return (node.func.attr == 'replace' and len(node.args) >= 2
            and not has_spread(node))


def _writer_target(node, spec, receiver):
    keyword, position = spec
    if keyword is None and position is None:
        return receiver
    return argument(node, keyword, position)


def _open_judgement(node, label, names):
    """(problem, kind, target) for a builtin open call."""
    line = node.lineno
    if names.counts['open']:
        return f'{label}:{line}: open callable is unresolved', None, None
    mode = _write_mode(node, 1)
    if mode is _UNRESOLVED_MODE:
        return f'{label}:{line}: open mode is unresolved', None, None
    if mode is None:
        return None, None, None
    return None, f'open mode {mode!r}', argument(node, 'file', 0)


def _attribute_judgement(node, label, names):
    """(problem, kind, target) for a method or module-attribute call."""
    function = node.func
    line = node.lineno
    spelling = ast.unparse(function)
    if function.attr == 'open':
        mode = _write_mode(node, 0)
        if mode is _UNRESOLVED_MODE:
            return (f'{label}:{line}: Path.open mode is unresolved',
                    None, None)
        if mode is None:
            return None, None, None
        return None, f'Path.open mode {mode!r}', function.value
    if function.attr in _WRITER_METHODS:
        return None, function.attr, _writer_target(
            node, _WRITER_METHODS[function.attr], function.value)
    base = _chain_base(function)
    if isinstance(base, ast.Name) and names.is_module_like(base.id):
        if spelling in _PURE_MODULE_CALLS:
            return None, None, None
        if spelling in _WRITER_MODULE_CALLS:
            return None, spelling, _writer_target(
                node, _WRITER_MODULE_CALLS[spelling], None)
    elif function.attr in _PURE_METHODS or _is_string_replace(node):
        return None, None, None
    return f'{label}:{line}: {spelling} is not a modelled call', None, None


def _name_judgement(node, label, names):
    """(problem, kind, target) for a plain-name call."""
    name = node.func.id
    if name == 'open':
        return _open_judgement(node, label, names)
    if name in _NAMESPACES and not _is_namespace_call(node):
        return (f'{label}:{node.lineno}: {name} callable is unresolved',
                None, None)
    origin = names.import_origin(name)
    if (names.is_unique_def(name) or origin in _PURE_IMPORTS
            or names.is_pure_name(name)):
        return None, None, None
    if origin in _WRITER_IMPORTS:
        return None, name, _writer_target(
            node, _WRITER_IMPORTS[origin], None)
    return (f'{label}:{node.lineno}: {name} callable is unresolved',
            None, None)


def call_judgement(node, label, names):
    """(problem, kind, target): refused outright, or a write to prove.

    A call the tables do not name is a problem in itself; a writer comes
    back with the expression it writes to, for the path analysis to
    prove, and a pure call comes back as (None, None, None).
    """
    if isinstance(node.func, ast.Attribute):
        return _attribute_judgement(node, label, names)
    if isinstance(node.func, ast.Name):
        return _name_judgement(node, label, names)
    return (f'{label}:{node.lineno}: {ast.unparse(node.func)} is not a '
            'modelled call', None, None)
