"""The analysis behind tests/test_coverage_environment.py.

Not a suite itself — run_tests.py only loads `test_*.py`.

A Python child that inherits COVERAGE_* into a working directory that
[tool.coverage.paths] does not map back onto the repository records
coverage against paths that vanish with the temporary tree, and a later
`coverage combine` fails with `No source for code`. The guard fails
CLOSED: it proves each launch safe, and a launch it cannot prove safe is
a violation, so a spelling it does not understand can never slip past.
"""
import ast

from _repo import ROOT

_DECLARATION = 'child_coverage'
_MUTATING_METHODS = frozenset({
    'clear', 'pop', 'popitem', 'setdefault', 'update',
})
_ROOT_SPELLINGS = frozenset({
    'ROOT', 'str(ROOT)', '_util.ROOT', 'str(_util.ROOT)',
    'behaviour.ROOT', 'str(behaviour.ROOT)',
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


def _is_root_spelling(node):
    """Whether an expression provably names the repository root."""
    if ast.unparse(node) in _ROOT_SPELLINGS:
        return True
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, str)):
        return _is_root_spelling(node.left)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'str' and len(node.args) == 1
            and not node.keywords):
        return _is_root_spelling(node.args[0])
    return False


class _ModuleFacts:
    """The syntactic facts one module's launches are judged against."""

    def __init__(self, tree):
        self.subprocess_modules = set()
        self.launch_callables = set()
        self.declaration_modules = set()
        self.declaration_functions = set()
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
                            and alias.name in {'run', 'Popen'}):
                        self.launch_callables.add(bound)
                    if (node.module == '_util'
                            and alias.name == _DECLARATION):
                        self.declaration_functions.add(bound)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if _is_launch_alias(node, self.subprocess_modules):
                    self.launch_callables.add(node.targets[0].id)
            elif isinstance(node, ast.Call):
                function = node.func
                if (isinstance(function, ast.Attribute)
                        and function.attr == 'chdir'
                        and isinstance(function.value, ast.Name)
                        and function.value.id == 'os'):
                    is_root = (bool(node.args)
                               and _is_root_spelling(node.args[0]))
                    self.chdir_calls.append((node.lineno, is_root))
        self._collect_bindings(tree.body)

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
        if not isinstance(node, ast.Call) or not node.args:
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
        mode = node.args[0]
        if (isinstance(mode, ast.Constant)
                and mode.value in {'scrub', 'keep'}):
            return mode.value
        return 'invalid'


def _is_launch_alias(node, subprocess_modules):
    """Whether an Assign binds a plain name to subprocess.run/Popen."""
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return False
    value = node.value
    return (isinstance(value, ast.Attribute)
            and value.attr in {'run', 'Popen'}
            and isinstance(value.value, ast.Name)
            and value.value.id in subprocess_modules)


def _launch_method(node, facts):
    """'run' or 'Popen' when `node` launches through subprocess."""
    function = node.func
    if (isinstance(function, ast.Attribute)
            and function.attr in {'run', 'Popen'}
            and isinstance(function.value, ast.Name)
            and function.value.id in facts.subprocess_modules):
        return function.attr
    if (isinstance(function, ast.Name)
            and function.id in facts.launch_callables):
        return function.id
    return None


def _unresolved_cwd(node, facts):
    """Why the launch's working directory is not provably safe, or None."""
    spread = False
    for keyword in node.keywords:
        if keyword.arg == 'cwd':
            if _is_root_spelling(keyword.value):
                return None
            return f'cwd={ast.unparse(keyword.value)}'
        if keyword.arg is None:
            spread = True
    if spread:
        return 'cwd may arrive through a ** spread'
    earlier = [line for line, is_root in facts.chdir_calls
               if not is_root and line < node.lineno]
    if earlier:
        return f'os.chdir at line {earlier[0]} may have moved the cwd'
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


def _declaration(node, facts, tree):
    """(problem, mode) for the launch's env= keyword."""
    env = None
    for keyword in node.keywords:
        if keyword.arg == 'env':
            env = keyword.value
    if env is None:
        return 'declares no env=', None
    mode = facts.declaration_mode(env)
    if mode == 'invalid':
        return "env= mode must be the literal 'scrub' or 'keep'", None
    if mode is not None:
        return None, mode
    if not isinstance(env, ast.Name):
        return (f'env={ast.unparse(env)} is not {_DECLARATION}(...) or a '
                'name bound once to it', None)
    problem = _bare_name_problem(env.id, facts, tree)
    if problem is not None:
        return problem, None
    return None, facts.declaration_mode(facts.module_bindings[env.id][0][1])


def _check_launch(node, method, relative, function, facts, tree, keeps,
                  violations):
    why = _unresolved_cwd(node, facts)
    if why is None:
        return
    problem, mode = _declaration(node, facts, tree)
    if problem is not None:
        violations.append(
            f'{relative}:{node.lineno}: subprocess.{method} {why} {problem}')
    elif mode == 'keep':
        keeps.append((relative, function))


def _visit(node, relative, function, facts, tree, keeps, violations):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _visit(child, relative, child.name, facts, tree, keeps,
                   violations)
            continue
        if isinstance(child, ast.Call):
            method = _launch_method(child, facts)
            if method is not None:
                _check_launch(child, method, relative, function, facts,
                              tree, keeps, violations)
        _visit(child, relative, function, facts, tree, keeps, violations)


def _analyze(relative, source, keeps):
    tree = ast.parse(source, filename=relative)
    facts = _ModuleFacts(tree)
    violations = []
    _visit(tree, relative, '<module>', facts, tree, keeps, violations)
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


def _coverage_environment_violations():
    violations = []
    keeps = []
    for path in sorted((ROOT / 'tests').glob('*.py')):
        relative = path.relative_to(ROOT).as_posix()
        violations.extend(_analyze(
            relative, path.read_text(encoding='utf-8'), keeps))
    violations.extend(_unlisted_keeps(keeps))
    declared = {_keep_site(module, function) for module, function in keeps}
    for entry in sorted(_KEEP_ALLOWLIST - declared):
        violations.append(f'allowlisted keep site {entry} has no launch')
    return violations
