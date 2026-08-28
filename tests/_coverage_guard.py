"""The analysis behind tests/test_coverage_environment.py.

Not a suite itself — run_tests.py only loads `test_*.py`.

A Python child that inherits COVERAGE_* into a working directory that
[tool.coverage.paths] does not map back onto the repository records
coverage against paths that vanish with the temporary tree, and a later
`coverage combine` fails with `No source for code`. The guard fails
CLOSED WITHIN WHAT IT RECOGNISES: for a call it recognises as a launch,
it proves the working directory safe or requires a declaration, and an
expression it cannot resolve is a violation rather than an exemption.

Recognition itself is enumerated — the launch callables and their module
aliases, the namespaces that can rebind the helper, the writer calls in
tests/_control_writes.py — so a call reached through a route not in those
sets is not judged at all. That boundary is deliberate and is where to
look first when this guard passes something it should not.
"""
import ast

from _coverage_scopes import (
    _is_root_spelling, _scope_shadows, _shadowed_names, root_owner_names)

_DECLARATION = 'child_coverage'
_LAUNCHERS = frozenset(
    {'run', 'Popen', 'call', 'check_call', 'check_output'})
_MUTATING_METHODS = frozenset({
    'clear', 'pop', 'popitem', 'setdefault', 'update',
})
# The launches that declare 'keep', as module::function so an unrelated
# edit above a site does not churn the list. A keep site with no entry
# fails, and an entry with no keep site fails. Every tree below sits
# under the `*/tree` anchor that [tool.coverage.paths] maps back onto
# the repository (see pyproject.toml).
_KEEP_ALLOWLIST = frozenset({
    # The fixture hands the child a synthetic COVERAGE_PROCESS_START and
    # asserts it arrives; a scrub would strip the very variable under test.
    'tests/test_coverage_suites.py::_coverage_tree',
    # run_tests.py is measured where it stands in the copied tree;
    # scrubbing would report the file every one of those tests drives at 0%.
    'tests/test_suite_runner.py::_runner_tree',
    # The copied checker runs from the mapped copy so its lines count.
    'tests/test_version_contract.py::_run_checker',
    # Same mapped copy: drift detection is the checker's main path.
    'tests/test_version_contract.py::test_check_versions_detects_drift',
    # Same mapped copy: site completeness is asserted from its output.
    'tests/test_version_contract.py::'
    'test_check_versions_sites_all_present_in_copy',
})


class _ModuleFacts:
    """The syntactic facts one module's launches are judged against."""

    def __init__(self, tree):
        self.shadowed_names = _shadowed_names(tree)
        self.root_owners = root_owner_names(tree)
        self.scope_shadows = _scope_shadows(tree)
        self.module_shadows = set(self.scope_shadows[tree])
        if 'ROOT' not in self.shadowed_names:
            self.module_shadows.discard('ROOT')
        self.subprocess_modules = set()
        self.launch_callables = set()
        self.declaration_modules = set()
        self.declaration_functions = set()
        self.chdir_callables = set()
        self.module_bindings = {}
        self.chdir_calls = []
        self._collect(tree)

    def _collect(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == 'subprocess':
                        self.subprocess_modules.add(
                            alias.asname or alias.name)
                    if alias.name == '_util':
                        self.declaration_modules.add(
                            alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if (node.module == 'subprocess'
                            and alias.name in _LAUNCHERS):
                        self.launch_callables.add(bound)
                    if (node.module == '_util'
                            and alias.name == _DECLARATION):
                        self.declaration_functions.add(bound)
                    if node.module == 'os' and alias.name == 'chdir':
                        self.chdir_callables.add(bound)
        self._propagate_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                is_chdir = (
                    isinstance(function, ast.Attribute)
                    and function.attr == 'chdir'
                    and isinstance(function.value, ast.Name)
                    and function.value.id == 'os')
                is_chdir = (is_chdir
                            or isinstance(function, ast.Name)
                            and function.id in self.chdir_callables)
                if is_chdir:
                    is_root = (bool(node.args)
                               and _is_root_spelling(
                                   node.args[0], self.shadowed_names,
                                   self.root_owners))
                    self.chdir_calls.append((node.lineno, is_root))
        self._collect_bindings(tree.body)

    def _propagate_aliases(self, tree):
        """Follow plain-name aliases of a launch callable or _util.

        Iterated to a fixpoint rather than read once, so an alias of an
        alias resolves to the same object at any depth.
        """
        aliases = [(node.targets[0].id, node.value) for node in ast.walk(tree)
                   if isinstance(node, ast.Assign) and len(node.targets) == 1
                   and isinstance(node.targets[0], ast.Name)]
        changed = True
        while changed:
            changed = False
            for name, value in aliases:
                if (name not in self.launch_callables
                        and _is_launch_value(value, self)):
                    self.launch_callables.add(name)
                    changed = True
                if (name not in self.declaration_modules
                        and isinstance(value, ast.Name)
                        and value.id in self.declaration_modules):
                    self.declaration_modules.add(name)
                    changed = True
                if (name not in self.subprocess_modules
                        and isinstance(value, ast.Name)
                        and value.id in self.subprocess_modules):
                    self.subprocess_modules.add(name)
                    changed = True

    def _collect_bindings(self, statements):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            targets, value = [], None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            for target in targets:
                if isinstance(target, ast.Name):
                    self.module_bindings.setdefault(
                        target.id, []).append((node.lineno, value))
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    self._collect_bindings([child])

    def declaration_mode(self, node):
        """The mode when `node` is a direct child_coverage(mode) call."""
        if not isinstance(node, ast.Call):
            return None
        function = node.func
        if isinstance(function, ast.Name):
            if function.id not in self.declaration_functions:
                return None
        elif not (isinstance(function, ast.Attribute)
                  and function.attr == _DECLARATION
                  and isinstance(function.value, ast.Name)
                  and function.value.id in self.declaration_modules):
            return None
        # The helper takes `mode` by keyword too, and refusing that spelling
        # would reject a valid call of the API this guard exists to read.
        mode = _keyword_or_arg(node, 'mode', 0)
        if mode is None:
            return 'invalid'
        if (isinstance(mode, ast.Constant)
                and mode.value in {'scrub', 'keep'}):
            return mode.value
        return 'invalid'


def _is_launch_value(value, facts):
    """Whether an expression is a subprocess launcher or a known alias."""
    if isinstance(value, ast.Name):
        return value.id in facts.launch_callables
    return (isinstance(value, ast.Attribute)
            and value.attr in _LAUNCHERS
            and isinstance(value.value, ast.Name)
            and value.value.id in facts.subprocess_modules)


def _launch_method(node, facts):
    """'run' or 'Popen' when `node` launches through subprocess."""
    function = node.func
    if (isinstance(function, ast.Attribute)
            and function.attr in _LAUNCHERS
            and isinstance(function.value, ast.Name)
            and function.value.id in facts.subprocess_modules):
        return function.attr
    if (isinstance(function, ast.Name)
            and function.id in facts.launch_callables):
        return function.id
    return None


def _unresolved_cwd(node, facts, shadowed):
    """Why the launch's working directory is not provably safe, or None."""
    spread = False
    for keyword in node.keywords:
        if keyword.arg == 'cwd':
            if _is_root_spelling(keyword.value, shadowed,
                                 facts.root_owners):
                return None
            return f'cwd={ast.unparse(keyword.value)}'
        if keyword.arg is None:
            spread = True
    if spread:
        return 'cwd may arrive through a ** spread'
    # Not `line < node.lineno`: a helper defined above the chdir is still
    # called after it, so any non-root chdir in the module taints an
    # inherited cwd.
    moved = [line for line, is_root in facts.chdir_calls if not is_root]
    if moved:
        return f'os.chdir at line {moved[0]} may have moved the cwd'
    return None


def _assignment_target_lines(tree, name):
    """Every line where `name` itself is an assignment target."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                lines.append(node.lineno)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for part in ast.walk(target):
                    if isinstance(part, ast.Name) and part.id == name:
                        lines.append(node.lineno)
    return lines


def _mutation_lines(tree, name):
    """Every line where `name` is mutated rather than plainly rebound."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            for target in targets:
                for part in ast.walk(target):
                    if (isinstance(part, ast.Subscript)
                            and isinstance(part.value, ast.Name)
                            and part.value.id == name):
                        lines.append(node.lineno)
        elif isinstance(node, (ast.AugAssign, ast.Delete)):
            targets = ([node.target] if isinstance(node, ast.AugAssign)
                       else node.targets)
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name:
                    lines.append(node.lineno)
                elif (isinstance(target, ast.Subscript)
                      and isinstance(target.value, ast.Name)
                      and target.value.id == name):
                    lines.append(node.lineno)
        elif (isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr in _MUTATING_METHODS
              and isinstance(node.func.value, ast.Name)
              and node.func.value.id == name):
            lines.append(node.lineno)
    return lines


def _bare_name_problem(name, facts, tree):
    """Why a bare env= name is no declaration, or None when it is one."""
    bindings = facts.module_bindings.get(name, [])
    if len(bindings) != 1:
        return (f'env={name} has {len(bindings)} module-level bindings, '
                'not exactly one')
    target_lines = _assignment_target_lines(tree, name)
    if len(target_lines) != 1:
        return (f'env={name} is an assignment target at lines '
                f'{target_lines}')
    mutations = _mutation_lines(tree, name)
    if mutations:
        return f'env={name} is mutated at line {mutations[0]}'
    if facts.declaration_mode(bindings[0][1]) not in {'scrub', 'keep'}:
        return f'env={name} is not bound to {_DECLARATION}(...)'
    return None


def _keyword_or_arg(call, name, position=None):
    """A call's argument by keyword, else by position, else None."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    if position is None or len(call.args) <= position:
        return None
    return call.args[position]


def _cwd_core(node, shadowed):
    """A cwd expression with its str()/Path() wrappers removed."""
    while (isinstance(node, ast.Call) and len(node.args) == 1
           and not node.keywords and isinstance(node.func, ast.Name)
           and node.func.id in {'str', 'Path'}
           and node.func.id not in shadowed):
        node = node.args[0]
    return node


def _keep_cwd_problem(node, call, shadowed):
    """Why a keep does not prove its own launch's cwd, or None.

    The runtime helper can only judge the path it is handed, so nothing
    but this ties that path to the directory the child actually starts in.
    """
    if call is None:
        return None
    declared = _keyword_or_arg(call, 'cwd', 2)
    launch = _keyword_or_arg(node, 'cwd')
    if declared is None:
        return f"{_DECLARATION}('keep') names no cwd"
    if launch is None:
        return f"{_DECLARATION}('keep') cwd is not the launch's cwd"
    core = _cwd_core(declared, shadowed)
    # Equal spellings are not equal values: two `next(paths)` operands
    # unparse the same and yield different directories, so the shared cwd
    # has to be one name looked up twice.
    if not isinstance(core, ast.Name):
        return f"{_DECLARATION}('keep') cwd is not a plain name"
    if core.id != getattr(_cwd_core(launch, shadowed), 'id', None):
        return f"{_DECLARATION}('keep') cwd is not the launch's cwd"
    return None


def _declaration(node, facts, tree):
    """(problem, mode, call) for the launch's env= keyword."""
    env = None
    for keyword in node.keywords:
        if keyword.arg == 'env':
            env = keyword.value
    if env is None:
        return 'declares no env=', None, None
    mode = facts.declaration_mode(env)
    if mode == 'invalid':
        return "env= mode must be the literal 'scrub' or 'keep'", None, None
    if mode is not None:
        return None, mode, env
    if not isinstance(env, ast.Name):
        return (f'env={ast.unparse(env)} is not {_DECLARATION}(...) or a '
                'name bound once to it', None, None)
    problem = _bare_name_problem(env.id, facts, tree)
    if problem is not None:
        return problem, None, None
    bound = facts.module_bindings[env.id][0][1]
    return None, facts.declaration_mode(bound), bound


def _check_launch(node, method, relative, function, facts, tree, keeps,
                  violations, shadowed):
    why = _unresolved_cwd(node, facts, shadowed)
    if why is None:
        return
    problem, mode, call = _declaration(node, facts, tree)
    if problem is None and mode == 'keep':
        problem = _keep_cwd_problem(node, call, shadowed)
    if problem is not None:
        violations.append(
            f'{relative}:{node.lineno}: subprocess.{method} {why} {problem}')
    elif mode == 'keep':
        keeps.append((relative, function))


def _visit(node, relative, function, facts, tree, keeps, violations,
           shadowed=None):
    if shadowed is None:
        shadowed = frozenset(facts.module_shadows)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _visit(child, relative, child.name, facts, tree, keeps,
                   violations,
                   shadowed | facts.scope_shadows.get(child, set()))
            continue
        if isinstance(child, ast.Call):
            method = _launch_method(child, facts)
            if method is not None:
                _check_launch(child, method, relative, function, facts,
                              tree, keeps, violations, shadowed)
        _visit(child, relative, function, facts, tree, keeps, violations,
               shadowed)


def _declaration_names(facts):
    """Names bound exactly once at module level to child_coverage(...)."""
    return {name for name, bindings in facts.module_bindings.items()
            if len(bindings) == 1
            and facts.declaration_mode(bindings[0][1]) in {'scrub', 'keep'}}


def _env_keyword_values(tree):
    """The Name nodes appearing directly as an env= keyword's value."""
    values = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.keyword) and node.arg == 'env'
                and isinstance(node.value, ast.Name)):
            values.add(id(node.value))
    return values


def _namespace_lookups(tree):
    """globals()/vars() subscripts, with the literal key where there is one."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        namespace = node.value
        if (isinstance(namespace, ast.Call) and not namespace.args
                and not namespace.keywords
                and isinstance(namespace.func, ast.Name)
                and namespace.func.id in {'globals', 'locals', 'vars'}):
            key = node.slice
            yield node.lineno, (key.value if isinstance(key, ast.Constant)
                                and isinstance(key.value, str) else None)


def _declaration_name_violations(tree, facts, relative):
    """A declaration name appears at its binding and as env=, nowhere else.

    Aliasing the name mutates the same dict the launches use, and
    following aliases is data-flow analysis, so the appearance itself is
    the violation: a syntactic count of occurrences, not control flow.
    """
    violations = []
    env_values = _env_keyword_values(tree)
    names = _declaration_names(facts)
    for lineno, key in _namespace_lookups(tree):
        if names and (key is None or key in names):
            violations.append(
                f'{relative}:{lineno}: the module namespace reaches '
                f'declaration name {key or "computed at runtime"}')
    for name in sorted(names):
        binding_line = facts.module_bindings[name][0][0]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name) or node.id != name:
                continue
            if id(node) in env_values:
                continue
            if node.lineno == binding_line and isinstance(node.ctx,
                                                          ast.Store):
                continue
            violations.append(
                f'{relative}:{node.lineno}: declaration name {name} '
                'appears outside its binding and env=')
    return violations


def _is_module_namespace(node, facts):
    """Whether an expression is a mapping of module names.

    `globals()`, `locals()` and `vars()` at module scope, and the
    `__dict__` or `vars(...)` of an imported helper module: assigning
    through any of them rebinds the name it keys.
    """
    if (isinstance(node, ast.Attribute) and node.attr == '__dict__'
            and isinstance(node.value, ast.Name)
            and node.value.id in facts.declaration_modules):
        return True
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return False
    if node.func.id in {'globals', 'locals'}:
        return not node.args and not node.keywords
    if node.func.id != 'vars':
        return False
    if not node.args:
        return not node.keywords
    return (len(node.args) == 1 and isinstance(node.args[0], ast.Name)
            and node.args[0].id in facts.declaration_modules)


def _rebinds_declaration(part, facts):
    """Whether an assignment target names the declaration helper."""
    if isinstance(part, ast.Name):
        return (part.id == _DECLARATION
                or part.id in facts.declaration_functions
                or part.id in facts.declaration_modules)
    if (isinstance(part, ast.Attribute)
            and part.attr == _DECLARATION
            and isinstance(part.value, ast.Name)
            and part.value.id in facts.declaration_modules):
        return True
    if not isinstance(part, ast.Subscript):
        return False
    if not _is_module_namespace(part.value, facts):
        return False
    name = part.slice
    return (not isinstance(name, ast.Constant)
            or not isinstance(name.value, str)
            or name.value == _DECLARATION
            or name.value in facts.declaration_functions
            or name.value in facts.declaration_modules)


def _rebind_parts(target):
    """Assignment parts that themselves receive a new value."""
    if isinstance(target, ast.Starred):
        yield from _rebind_parts(target.value)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for part in target.elts:
            yield from _rebind_parts(part)
    else:
        yield target


def _setattr_rebinds_declaration(node, facts):
    """Whether setattr may replace the imported declaration helper."""
    if (not isinstance(node.func, ast.Name) or node.func.id != 'setattr'
            or len(node.args) < 2):
        return False
    owner, name = node.args[:2]
    if (not isinstance(owner, ast.Name)
            or owner.id not in facts.declaration_modules):
        return False
    return (not isinstance(name, ast.Constant)
            or not isinstance(name.value, str)
            or name.value == _DECLARATION)


def _mutates_module_namespace(node, facts):
    """Whether a call can rewrite a name in a module namespace."""
    function = node.func
    if (not isinstance(function, ast.Attribute)
            or function.attr not in _MUTATING_METHODS | {'__setitem__'}):
        return False
    return _is_module_namespace(function.value, facts)


def _helper_rebind_violations(tree, facts, relative):
    """A module may never assign to child_coverage or its local alias.

    `_util.child_coverage = lambda _mode: dict(os.environ)` makes every
    later declaration a no-op while reading exactly like one. Assignment
    targets only — syntax, not control flow.
    """
    violations = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and (_setattr_rebinds_declaration(node, facts)
                     or _mutates_module_namespace(node, facts))):
            violations.append(
                f'{relative}:{node.lineno}: the module rebinds '
                f'{_DECLARATION}')
            continue
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            for part in _rebind_parts(target):
                if _rebinds_declaration(part, facts):
                    violations.append(
                        f'{relative}:{node.lineno}: the module rebinds '
                        f'{_DECLARATION}')
    return violations


def _analyze(relative, source, keeps):
    tree = ast.parse(source, filename=relative)
    facts = _ModuleFacts(tree)
    violations = []
    _visit(tree, relative, '<module>', facts, tree, keeps, violations)
    violations.extend(_declaration_name_violations(tree, facts, relative))
    violations.extend(_helper_rebind_violations(tree, facts, relative))
    return violations


def _keep_site(module, function):
    return f'{module}::{function}'


def _unlisted_keeps(keeps):
    return [
        f'{_keep_site(module, function)} declares keep without an '
        'allowlist entry'
        for module, function in keeps
        if _keep_site(module, function) not in _KEEP_ALLOWLIST
    ]


def _synthetic_violations(source):
    """Run the guard over one source string for mutation-shaped cases."""
    keeps = []
    violations = _analyze('tests/synthetic.py', source, keeps)
    return violations + _unlisted_keeps(keeps)


def _coverage_environment_violations(root):
    violations = []
    keeps = []
    for path in sorted((root / 'tests').glob('*.py')):
        relative = path.relative_to(root).as_posix()
        violations.extend(_analyze(
            relative, path.read_text(encoding='utf-8'), keeps))
    violations.extend(_unlisted_keeps(keeps))
    declared = {_keep_site(module, function) for module, function in keeps}
    for entry in sorted(_KEEP_ALLOWLIST - declared):
        violations.append(f'allowlisted keep site {entry} has no launch')
    return violations
