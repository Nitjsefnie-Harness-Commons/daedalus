"""The analysis behind tests/test_coverage_environment.py.

Not a suite itself — run_tests.py only loads `test_*.py`.

A Python child inheriting COVERAGE_* into a working directory that
[tool.coverage.paths] does not map back onto the repository records
paths that vanish with the temporary tree, and `coverage combine` later
fails with `No source for code`. The guard fails closed: a recognised
launcher proves its working directory safe or declares on every call;
any other callee does so when it spells one readably (`cwd=`, a `**`
spread of a mapping literal naming `cwd`, or `dict(cwd=...)`); a
launcher bound where the alias walk cannot follow is refused there; a
root owner reached any way but a plain attribute read stops proving
ROOT; and `chdir` or `fchdir` on any base or through a from-import
alias moves the cwd launches inherit.

Outside it: a `child_coverage(...)` call itself, a launcher, owner or
chdir reached only by a string (`getattr(os, 'chdir')`) or a call
result, an unreadable `**` spread on an unrecognised callee, and a
launcher alias bound by a call result, default argument or match
capture.
"""
import ast

from _coverage_memo import analysed
from _coverage_scopes import (
    _is_root_spelling, _scope_shadows, _shadowed_names, root_owner_names)

_DECLARATION = 'child_coverage'
_LAUNCHERS = frozenset(
    {'run', 'Popen', 'call', 'check_call', 'check_output'})
_MUTATING_METHODS = frozenset({
    'clear', 'pop', 'popitem', 'setdefault', 'update',
})
_CHDIR = frozenset({'chdir', 'fchdir'})
# Keep launches as module::function, so an edit above a site does not
# churn the list; each tree sits under the `*/tree` anchor that
# [tool.coverage.paths] maps back onto the repository (pyproject.toml).
_KEEP_ALLOWLIST = frozenset({
    # A synthetic COVERAGE_PROCESS_START is the variable under test.
    'tests/_coverage_suite_fixture.py::coverage_tree',
    # The workflow's .pth program is the subject of the child probe.
    'tests/test_js_coverage_workflow.py::'
    'test_subprocess_startup_program_starts_coverage',
    # run_tests.py is measured where it stands in the copied tree.
    'tests/test_suite_runner.py::_runner_tree',
    # The copied checker runs from the mapped copy so its lines count.
    'tests/test_version_contract.py::_run_checker',
    'tests/test_version_contract.py::test_check_versions_detects_drift',
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
                    if alias.name in _CHDIR:
                        self.chdir_callables.add(bound)
        self._propagate_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                is_chdir = (isinstance(function, ast.Attribute)
                            and function.attr in _CHDIR)
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
        """Follow plain-name aliases of a launcher, _util or chdir."""
        aliases = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                aliases.append((node.targets[0].id, node.value))
            elif (isinstance(node, ast.AnnAssign) and node.value is not None
                  and isinstance(node.target, ast.Name)):
                aliases.append((node.target.id, node.value))
        followed = (
            (self.launch_callables,
             lambda value: _is_launch_value(value, self)),
            (self.declaration_modules,
             lambda value: _names_one_of(value, self.declaration_modules)),
            (self.subprocess_modules,
             lambda value: _names_one_of(value, self.subprocess_modules)),
            (self.chdir_callables,
             lambda value: _names_one_of(value, self.chdir_callables)
             or isinstance(value, ast.Attribute) and value.attr in _CHDIR),
        )
        changed = True
        while changed:
            changed = False
            for name, value in aliases:
                for names, follows in followed:
                    if name not in names and follows(value):
                        names.add(name)
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
        # `mode` arrives by keyword too; refusing it would reject a valid call.
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


def _names_one_of(value, names):
    return isinstance(value, ast.Name) and value.id in names


def _spread_names_cwd(node):
    """Whether a ** spread of the call is a mapping literal naming cwd."""
    for keyword in node.keywords:
        value = keyword.value
        if keyword.arg is not None:
            continue
        if isinstance(value, ast.Dict) and any(
                isinstance(key, ast.Constant) and key.value == 'cwd'
                for key in value.keys):
            return True
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == 'dict'
                and any(item.arg == 'cwd' for item in value.keywords)):
            return True
    return False


def _launch_method(node, facts):
    """A recognised launcher, or any callee that spells cwd readably."""
    function = node.func
    if (isinstance(function, ast.Attribute)
            and function.attr in _LAUNCHERS
            and isinstance(function.value, ast.Name)
            and function.value.id in facts.subprocess_modules):
        return f'subprocess.{function.attr}'
    if (isinstance(function, ast.Name)
            and function.id in facts.launch_callables):
        return f'subprocess.{function.id}'
    if facts.declaration_mode(node) is not None:
        return None
    if (any(keyword.arg == 'cwd' for keyword in node.keywords)
            or _spread_names_cwd(node)):
        return f'unresolved callee {ast.unparse(function)}'
    return None


def _unresolved_cwd(node, facts, shadowed, launcher):
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
    if (launcher and len(node.args) > 9
            and not any(isinstance(arg, ast.Starred)
                        for arg in node.args[:9])):
        if not _is_root_spelling(node.args[9], shadowed, facts.root_owners):
            return f'cwd={ast.unparse(node.args[9])} at position 10'
    if spread:
        return 'cwd may arrive through a ** spread'
    # Not `line < node.lineno`: a helper defined above a chdir still runs
    # after it.
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
    # Two `next(paths)` operands unparse alike and differ at runtime, so
    # the shared cwd must be one name looked up twice.
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
    why = _unresolved_cwd(node, facts, shadowed,
                          method.startswith('subprocess.'))
    if why is None:
        return
    problem, mode, call = _declaration(node, facts, tree)
    if problem is None and mode == 'keep':
        problem = _keep_cwd_problem(node, call, shadowed)
    if problem is not None:
        violations.append(
            f'{relative}:{node.lineno}: {method} {why} {problem}')
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

    An alias shares the dict the launches use and following aliases is
    data flow, so the appearance itself is the violation.
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

    globals()/locals()/vars() at module scope, or the __dict__ or
    vars(...) of an imported helper module.
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

    A rebound helper makes every later declaration a no-op while reading
    exactly like one. Assignment targets only: syntax, not control flow.
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


def _bound_values(node):
    """Every (statement line, value) the statement binds unreadably."""
    if isinstance(node, ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            return []
        return [(node.lineno, node.value)]
    if isinstance(node, ast.AnnAssign):
        if node.value is None or isinstance(node.target, ast.Name):
            return []
        return [(node.lineno, node.value)]
    if isinstance(node, ast.AugAssign):
        return [(node.lineno, node.value)]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [(node.lineno, node.iter)]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [(node.lineno, item.context_expr) for item in node.items
                if item.optional_vars is not None]
    if isinstance(node, ast.NamedExpr):
        return [(node.lineno, node.value)]
    return []


def _carried_parts(value):
    """A bound value's elements and call arguments; a callee is not entered."""
    if isinstance(value, ast.Call):
        for part in [*value.args,
                     *(keyword.value for keyword in value.keywords)]:
            yield from _carried_parts(part)
    elif isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        for part in value.elts:
            yield from _carried_parts(part)
    elif isinstance(value, ast.Starred):
        yield from _carried_parts(value.value)
    else:
        yield value


def _unfollowable_launcher_bindings(tree, facts, relative):
    """A launcher bound where the alias walk cannot see is refused there."""
    violations = []
    for node in ast.walk(tree):
        for line, value in _bound_values(node):
            if any(_names_one_of(part, facts.subprocess_modules)
                   or _is_launch_value(part, facts)
                   for part in _carried_parts(value)):
                violations.append(
                    f'{relative}:{line}: a launcher is bound '
                    'through a form the guard cannot follow')
    return violations


def _analyze(relative, source, keeps):
    tree = ast.parse(source, filename=relative)
    facts = _ModuleFacts(tree)
    violations = []
    _visit(tree, relative, '<module>', facts, tree, keeps, violations)
    violations.extend(_declaration_name_violations(tree, facts, relative))
    violations.extend(_helper_rebind_violations(tree, facts, relative))
    violations.extend(_unfollowable_launcher_bindings(tree, facts, relative))
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
        violations.extend(analysed(
            _analyze, relative, path.read_text(encoding='utf-8'), keeps))
    violations.extend(_unlisted_keeps(keeps))
    declared = {_keep_site(module, function) for module, function in keeps}
    for entry in sorted(_KEEP_ALLOWLIST - declared):
        violations.append(f'allowlisted keep site {entry} has no launch')
    return violations
