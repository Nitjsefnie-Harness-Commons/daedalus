"""The analysis behind tests/test_bash_resolver_scan.py.

`_util.workflow_bash()` keeps Windows from selecting the WSL launcher. This
scan catches subprocess launches that bypass it through a literal executable,
a literal leading word under `shell=True`, or `shutil.which` of that name.

Outside the scan: runtime-computed shells, unresolved names (including
parameters and imports), chains beyond `_MAX_PROGRAM_DEPTH`, and `which` of
a non-literal name. The last case includes `tests/_workflowrun.py` resolving
a workflow's `shell:` template. Computed command strings such as
`'bash -c ' + script` and non-literal leading words under `shell=True` are
also outside the scan.
"""
import ast
import re

from _coverage_scopes import (
    _containing_binding_scope, _evaluation_scopes, _scope_shadows)

_HELPER = 'workflow_bash'
_WHICH = 'which'
_LAUNCHERS = frozenset(
    {'run', 'Popen', 'call', 'check_call', 'check_output'})
_SHELL_NAMES = frozenset({'bash', 'bash.exe'})
_ANY_SEPARATOR = re.compile(r'[/\\]')
_MAX_PROGRAM_DEPTH = 4


def _binding_of(node):
    """(target names, value) when `node` plainly binds a name."""
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets, value = [node.target], node.value
    elif isinstance(node, ast.NamedExpr):
        targets, value = [node.target], node.value
    else:
        return (), None
    names = [part.id for target in targets for part in ast.walk(target)
             if isinstance(part, ast.Name)
             and isinstance(part.ctx, ast.Store)]
    return names, value


class _ModuleFacts:
    """The names one module binds to a resolver, a which, or a launcher."""

    def __init__(self, tree):
        self.helper_modules = set()
        self.shell_modules = set()
        self.subprocess_modules = set()
        self.helper_functions = set()
        self.which_functions = set()
        self.launch_callables = set()
        self.scoped_nodes, self.parents = _evaluation_scopes(tree)
        layout = self.scoped_nodes, self.parents
        self.scopes = _scope_shadows(tree, layout)
        self.bindings = {scope: {} for scope in self.parents}
        for node, scope in self.scoped_nodes:
            names, value = _binding_of(node)
            if isinstance(node, ast.NamedExpr):
                scope = _containing_binding_scope(scope, self.parents)
            for name in names:
                self.bindings[scope].setdefault(name, []).append(value)
        self._collect(tree)
        self._propagate_aliases(tree)

    def binding_values(self, scope, name):
        """Values `name` is bound to in the innermost scope that binds it.

        Empty is "not provable": the name may be a parameter, an import or a
        comprehension target, which is outside what this scan judges.
        """
        current = scope
        while current is not None:
            bound = self.bindings.get(current, {}).get(name)
            if bound is not None:
                return bound
            if name in self.scopes.get(current, ()):
                return []
            current = self.parents[current]
        return []

    def _collect(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name == 'subprocess':
                        self.subprocess_modules.add(bound)
                    elif alias.name == 'shutil':
                        self.shell_modules.add(bound)
                    elif alias.name == '_util':
                        self.helper_modules.add(bound)
            elif isinstance(node, ast.ImportFrom):
                module = node.module
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if module == 'subprocess' and alias.name in _LAUNCHERS:
                        self.launch_callables.add(bound)
                    elif module == 'shutil' and alias.name == _WHICH:
                        self.which_functions.add(bound)
                    elif module == '_util' and alias.name == _HELPER:
                        self.helper_functions.add(bound)

    def _propagate_aliases(self, tree):
        """Follow plain-name aliases of any name the sets above hold."""
        groups = (self.helper_modules, self.shell_modules,
                  self.subprocess_modules, self.helper_functions,
                  self.which_functions, self.launch_callables)
        aliases = [(node.targets[0].id, node.value) for node in ast.walk(tree)
                   if isinstance(node, ast.Assign) and len(node.targets) == 1
                   and isinstance(node.targets[0], ast.Name)]
        changed = True
        while changed:
            changed = False
            for name, value in aliases:
                if not isinstance(value, ast.Name):
                    continue
                for group in groups:
                    if name not in group and value.id in group:
                        group.add(name)
                        changed = True


def _launch_method(node, facts):
    function = node.func
    if (isinstance(function, ast.Attribute)
            and function.attr in _LAUNCHERS
            and isinstance(function.value, ast.Name)
            and function.value.id in facts.subprocess_modules):
        return function.attr
    return (function.id if isinstance(function, ast.Name)
            and function.id in facts.launch_callables else None)


def _argument(call, name, position):
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def _names_shell(value):
    """Whether a literal names the executable the shared resolver returns."""
    if not isinstance(value, str) or not value:
        return False
    return _ANY_SEPARATOR.split(value)[-1].lower() in _SHELL_NAMES


def _command_names_shell(value):
    """Whether a command string's leading word names the shell executable."""
    if not isinstance(value, str) or not value:
        return False
    words = value.split()
    return bool(words) and _names_shell(words[0].strip('"\''))


def _shell_is_true(call):
    """Whether the launch provably carries `shell=True`."""
    for keyword in call.keywords:
        if keyword.arg == 'shell':
            return (isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True)
    return False


def _is_resolver_call(node, facts):
    function = node.func
    if isinstance(function, ast.Attribute):
        return (function.attr == _HELPER
                and isinstance(function.value, ast.Name)
                and function.value.id in facts.helper_modules)
    return (isinstance(function, ast.Name)
            and function.id in facts.helper_functions)


def _is_which_call(node, facts):
    function = node.func
    if isinstance(function, ast.Attribute):
        return (function.attr == _WHICH
                and isinstance(function.value, ast.Name)
                and function.value.id in facts.shell_modules)
    return (isinstance(function, ast.Name)
            and function.id in facts.which_functions)


def _route(value, facts, shell=False):
    """How `value` reaches the workflow shell, or None when unreadable."""
    if isinstance(value, ast.Constant):
        named = _names_shell(value.value) or (
            shell and _command_names_shell(value.value))
        return 'shell' if named else None
    if not isinstance(value, ast.Call):
        return None
    if _is_resolver_call(value, facts):
        return 'resolver'
    if _is_which_call(value, facts):
        argument = _argument(value, 'cmd', 0)
        if isinstance(argument, ast.Constant) and _names_shell(argument.value):
            return 'shell'
    return None


def _program_values(node, facts, scope, depth=0):
    """Candidate expressions for a launch's program element.

    An argv literal contributes its first element and a name the values it is
    bound to where the launch stands, so a site that binds the resolver's
    result stays clean and one that binds a spelling is read as what it is.
    The walk follows `_MAX_PROGRAM_DEPTH` links and judges nothing beyond it.
    """
    if depth > _MAX_PROGRAM_DEPTH or node is None:
        return []
    if isinstance(node, (ast.List, ast.Tuple)):
        if not node.elts or isinstance(node.elts[0], ast.Starred):
            return []
        return _program_values(node.elts[0], facts, scope, depth + 1)
    if isinstance(node, ast.Name):
        return [value
                for binding in facts.binding_values(scope, node.id)
                for value in _program_values(binding, facts, scope, depth + 1)]
    return [node]


def _check_launch(node, method, scope, facts, relative, violations):
    argv = node.args[0] if node.args else _argument(node, 'args', 0)
    shell = _shell_is_true(node)
    for value in _program_values(argv, facts, scope):
        if _route(value, facts, shell) != 'shell':
            continue
        violations.append(
            f'{relative}:{node.lineno}: {method} resolves the workflow shell '
            f'as {ast.unparse(value)}, not {_HELPER}()')


def _visit(facts, relative, violations):
    for child, scope in facts.scoped_nodes:
        if not isinstance(child, ast.Call):
            continue
        method = _launch_method(child, facts)
        if method:
            _check_launch(child, method, scope, facts, relative, violations)


def _analyze(relative, source):
    tree = ast.parse(source, filename=relative)
    facts = _ModuleFacts(tree)
    violations = []
    _visit(facts, relative, violations)
    return violations


def _synthetic_violations(source):
    """The scan over one source string, for mutation-shaped cases."""
    return _analyze('tests/synthetic.py', source)


def _test_modules(root):
    return sorted((root / 'tests').glob('*.py'))


def _tree_violations(root):
    violations = []
    for path in _test_modules(root):
        relative = path.relative_to(root).as_posix()
        violations.extend(
            _analyze(relative, path.read_text(encoding='utf-8')))
    return violations
