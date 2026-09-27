#!/usr/bin/env python3
"""Which launcher a fixed-unit-of-work Node child is started with.

A Node child whose real cost is a fixed unit of work is bounded by the
shared hang detector in `tests/_noderun.py`, not by a number typed at its
call site: a wall-clock literal measures the runner's busyness and nothing
else, and that is what failed correct children on a loaded CI runner.

A separate suite from `tests/test_harness_launch_bounds.py` because the
subjects differ and the size ceiling would not hold both: that one holds
how a number may reach a child THROUGH the census's routes, this one holds
which launcher a call site picks at all.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
import _util  # noqa: E402
# The parent map is that module's helper, not a second copy of it: a helper
# defined once in a shared module and imported by every user is the whole
# point of the rule that would otherwise red this file for re-implementing.
from _command_type_readers import _parents  # noqa: E402

TESTS = Path(__file__).resolve().parent

# Two modules the sweep does not walk: `_noderun.py` because its `Popen` IS
# the launcher rather than a site of the rule, and this file because a
# control cannot be a site of the rule it states.
NOT_SITES = ('_noderun.py', 'test_node_launch_routing.py')

# The boundary: a module whose child is NOT a fixed unit of work, or whose
# expiry is already classified, keeps a bound of its own. The population is
# derived and never listed, so this is closed only because every member is
# separately required to still bound its own child.
CLASSIFYING_MODULES = {
    '_dashnode.py': 'scales its own bound per retry attempt',
    '_gm_harness.py': 'a real-browser storage boundary (task 3)',
    '_overlap.py': 'its expiry is already classified by the harness',
    '_realbrowser.py': 'a real-browser probe with a composed bound (task 3)',
    # Its executable is a function PARAMETER named `node`, so the walk
    # admits it without binding it — a name spelled `node` is in scope
    # whether or not it resolves, because admitting a site only makes the
    # control ask for more, while leaving one out loses it. The requirement
    # that keeps it honest is the one every member carries.
    '_realbrowser_workers.py': 'a CDP call whose bound IS the response '
                               'deadline it asserts, classified into '
                               'CDPTimeout by the module (task 3)',
    'test_real_browser_classification.py': 'a real-browser probe (task 3)',
    'test_real_browser_environment.py': 'a real-browser probe (task 3)',
    'test_real_browser_harness.py': 'a real-browser probe (task 3)',
}

# A launch whose executable this walk cannot resolve is a FINDING, not a
# non-launch. Proving it is not node is what closes the population; a
# resolver that quietly answers "not node" for a shape it does not know
# makes the set close on its own vocabulary instead, and a rename is all it
# takes for a real site to vanish. Every such site is named here, keyed on
# the call's SHAPE — module, function, callee — so moving a line does not
# disown a row and renaming a variable does not re-own it.
#
# A row that matches nothing is a failure, so the table cannot outlive the
# site it excused. That is what keeps it a construction rather than a list.
UNRESOLVED_LAUNCHES = {
    ('_branch_boundary.py', '_git_text', 'subprocess.run'):
        'a git child behind a local helper',
    ('_coverage_comment_workflow.py', 'run_shell_block', 'subprocess.run'):
        'a bash child',
    ('_fake_gh.py', '_self_test', 'subprocess.run'):
        'a bash child',
    ('_overlap_clients.py', 'run_same_id_client_overlap', 'subprocess.Popen'):
        "a sys.executable child behind `client_argv`",
    ('_realbrowser.py', '_launch_and_reach', 'subprocess.Popen'):
        'a sys.executable child',
    ('_realbrowser_workers.py', '_browser_version', 'subprocess.run'):
        "a NODE child: the executable is this function's `browser` "
        "parameter, which no walk of one module can bind. The census "
        "reads the file, and the child is a real browser rather than a "
        "fixed unit of work.",
    ('_realbrowser_workers.py', '_diagnosis_launch', 'subprocess.Popen'):
        'a NODE child on the unresolved-parameter shape _browser_version '
        'has, and the same real-browser reason applies',
    ('_speedharness.py', 'run_workflow_script', 'subprocess.Popen'):
        "a workflow shell child behind the `command` parameter",
    ('_version_contract.py', '_versioned_git_tree', 'subprocess.run'):
        'a git child',
    ('_watcher_waits.py', '__init__', 'subprocess.Popen'):
        "a child behind the `argv` this constructor is handed",
    ('_workflowrun.py', 'run_step', 'subprocess.run'):
        "a workflow shell child: `command[0] = executable` reassigns the "
        "first element from PATH resolution the walk does not follow",
    ('test_ci_ratchets.py',
     'test_publisher_python_carries_posix_and_windows_paths_without_embedding',
     'subprocess.run'):
        "a bash child behind `_util.workflow_bash()`",
    ('test_cli_waits.py', '_run', 'subprocess.run'):
        'a sys.executable child behind the `argv` parameter',
    ('test_coverage_config.py', '_coverage_report', 'subprocess.run'):
        'a sys.executable child: the commands are sliced out of a tuple '
        "whose elements the walk reads, but `commands[-1]` is an index",
    ('test_dashboard_node_retry.py',
     'test_two_dashboard_children_cannot_be_inside_the_gate_together',
     'subprocess.Popen'):
        'a sys.executable child behind the `_gate_worker` helper',
    ('test_diff_coverage.py',
     'test_real_workflow_diffs_binary_attributed_python_as_text',
     'subprocess.run'):
        'a sys.executable child: `command[:-2]` is a slice of a list the '
        'walk does not resolve',
    ('test_helper_reimplementation.py',
     'test_the_boundary_says_which_declaration_the_branch_wrote',
     'subprocess.run'):
        'a git child',
    ('test_file_sizes.py', '_size_fixture', 'subprocess.run'):
        'a git child',
    ('test_helper_reimplementation_js.py',
     'test_the_javascript_boundary_decides_a_row_it_is_asked_about',
     'subprocess.run'):
        'a git child',
    ('test_js_coverage_workflow.py', '_capture_updates', 'subprocess.run'):
        "a bash child behind `_util.workflow_bash()`",
    ('test_reserved_test_names.py', '_fixture_checkout', 'subprocess.run'):
        'a git child',
    ('test_static_guard_regressions.py', 'run', 'real_run'):
        'a mutation-planting DOUBLE for subprocess.run, holding the real '
        "one under a local and calling it through `*args`, installed by "
        "each of the two controls that patch subprocess.run",
    ('test_workflow_bash.py',
     'test_workflow_bash_resolves_relative_candidate_for_other_cwd',
     'subprocess.run'):
        "a bash child behind `_util.workflow_bash()`, the thing under test",
}

# A `node` verdict can be a FALSE one: a control that plants its own stub
# executable to prove routing writes a function whose parameter is named
# `node` and launches it, and the walk admits that spelling for exactly the
# reason it admits `_realbrowser_workers.py`. Such a site is named here
# rather than being a finding its author cannot answer — a control that
# cries wolf is one whose findings stop being read.
#
# The rows are keyed on the call's shape exactly as `UNRESOLVED_LAUNCHES`
# is, and the unused-row check covers this class too. **A row here may only
# excuse a `node` verdict the walk could not BIND.** A launch that resolves
# to a real `shutil.which('node')` is never exempteable, so a row written
# to silence a control's own stub cannot become the place a real one hides.
NOT_FIXED_WORK = {}

NODE = 'node'
OTHER = 'other'
UNRESOLVED = 'unresolved'
RESOLUTION_DEPTH = 8


def _assignments_in(function):
    """Every name a function body binds, mapped to what it was bound to.

    Not `tests/_coverage_scopes.py`'s `_bound_names`, which answers a
    different question about a single node — the set of names THAT node
    binds. This one needs the values, because resolving an executable means
    following what a name was assigned.
    """
    bound = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.setdefault(target.id, []).append(node.value)
    return bound


def _executable_verdict(expression, scope, depth=0):
    """`(verdict, why)` for a launch's executable.

    `verdict` is `node`, `other` or `unresolved`. `unresolved` is the default
    and is what anything the walk cannot PROVE is not node reaches: a
    parameter, a subscript, a computed string, a call it does not
    recognise. Answering `other` for those would make the population close
    on the resolver's own vocabulary, and a rename is all it takes for a
    real site to disappear.

    `why` is what a `node` verdict rests on, and it is what the exemption
    table is checked against: `bound` means the walk followed the name to
    something that names node, and `spelled` means it could not bind the
    name at all and admitted it for saying `node`. Only a `spelled` one may
    be exempted — otherwise a row written to silence a control's own stub
    would also silence a real `which('node')` launch dropped into the same
    function.

    `scope` carries the three tables resolution reads — the names this
    module binds, the constants its siblings export, and the sibling stems
    it imports — so nothing is reached through a global.
    """
    if depth > RESOLUTION_DEPTH:
        return (UNRESOLVED, None)
    if isinstance(expression, (ast.List, ast.Tuple)):
        if not expression.elts:
            return (UNRESOLVED, None)
        return _executable_verdict(expression.elts[0], scope, depth)
    if isinstance(expression, ast.Constant):
        return ((NODE, 'bound') if expression.value == 'node'
                else (OTHER, 'bound'))
    if isinstance(expression, ast.BinOp):
        # `CLI + ['exec', …]` is how most of the CLI suites spell their
        # argv: the executable is the left operand and the `+` only adds
        # arguments. A computed STRING is different — `'no' + 'de'` names
        # node, and folding it would read as "not node", so a constant on
        # the left stays unresolved.
        if isinstance(expression.left, (ast.Constant, ast.BinOp)):
            return (UNRESOLVED, None)
        return _executable_verdict(expression.left, scope, depth)
    if isinstance(expression, ast.Attribute):
        if ast.unparse(expression) == 'sys.executable':
            return (OTHER, 'bound')
        # `test_cli.CLI + [...]` reaches a sibling's constant through the
        # module object. The same constant imported by name resolves
        # through `bound`; this is the other spelling, and both are used.
        owner = expression.value
        if isinstance(owner, ast.Name) and owner.id in scope['stems']:
            return _verdicts_over(
                scope['exported'].get(owner.id, {}).get(expression.attr, []),
                scope, depth)
        return (UNRESOLVED, None)
    if isinstance(expression, ast.Name):
        # A name spelled `node` is admitted as the node executable whether
        # or not this walk can bind it. That is the conservative
        # direction and it is deliberate: admitting a site only makes the
        # control ask for more of it, while leaving one out loses it, and
        # `_realbrowser_workers.py` binds its executable as a parameter
        # precisely so that this admits it.
        if expression.id == 'node':
            if expression.id in scope['bound']:
                return _verdicts_over(
                    reversed(scope['bound'][expression.id]), scope, depth + 1)
            return (NODE, 'spelled')
        if expression.id not in scope['bound']:
            return (UNRESOLVED, None)
        return _verdicts_over(
            reversed(scope['bound'][expression.id]), scope, depth + 1)
    if isinstance(expression, ast.Call):
        callee = ast.unparse(expression.func)
        if callee.rsplit('.', 1)[-1] == 'which':
            for argument in expression.args:
                if isinstance(argument, ast.Constant):
                    # Only the exact spelling is provable. `which('nodejs')`
                    # may name the same executable, so it stays unresolved
                    # rather than being read as "not node".
                    return ((NODE, 'bound') if argument.value == 'node'
                            else (UNRESOLVED, None))
            return (UNRESOLVED, None)
        # No other call's first argument IS the executable.
        # `os.environ.get('RUNTIME', 'node')` takes a KEY, and resolving it
        # would assert `other` on an executable read from the environment
        # that may be node; `os.path.basename(sys.argv[0])` and
        # `Path(x).name` return a derivation, not their argument. Only the
        # `which` family returns the executable it is handed.
        return (UNRESOLVED, None)
    return (UNRESOLVED, None)


def _verdicts_over(expressions, scope, depth):
    """One verdict for several bindings of one name, most specific first."""
    verdicts = {_executable_verdict(expression, scope, depth)
                for expression in expressions}
    if NODE in {verdict for verdict, _ in verdicts}:
        return (NODE, 'bound')
    return ((OTHER, 'bound') if {verdict for verdict, _ in verdicts} == {OTHER}
            else (UNRESOLVED, None))


def _sibling_constants():
    """Every module-level constant under `tests/`, by module then name.

    Most of the CLI suites spell their executable as a constant IMPORTED
    from a sibling (`from _cli_helpers import CLI`), so a walk that reads
    only the file in front of it reads every one of them as unprovable and
    the table grows to the size of the tree. Only module-level constants
    are followed: a helper FUNCTION's value is a question about its body,
    not its name.

    Read once and kept, because the sweep asks for it once per module and
    re-parsing every file in `tests/` each time is quadratic in a tree
    this size.
    """
    if _SIBLINGS.get('table') is not None:
        return _SIBLINGS['table']
    exported = {}
    for path in sorted(TESTS.glob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        exported.setdefault(path.stem, {}).setdefault(
                            target.id, []).append(node.value)
    _SIBLINGS['table'] = _follow_reexports(exported)
    return _SIBLINGS['table']


def _follow_reexports(exported):
    """Let a module's own exports include what it imported from a sibling.

    `test_cli.py` does not assign `CLI`; it imports it from
    `_cli_helpers.py`, and the CLI suites reach it as `test_cli.CLI`. One
    level is enough and one level is all that is claimed: a re-export chain
    deeper than that shows up as unresolved rather than as a guess.
    """
    trees = {}
    for stem in exported:
        path = TESTS / f'{stem}.py'
        if path.is_file():
            trees[stem] = ast.parse(path.read_text(encoding='utf-8'))
    resolved = {}
    for stem, tree in trees.items():
        names = dict(exported.get(stem, {}))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level or node.module not in trees:
                continue
            for alias in node.names:
                source = exported.get(node.module, {}).get(alias.name)
                if source and alias.name not in names:
                    names[alias.name] = source
        resolved[stem] = names
    return resolved


_SIBLINGS = {'table': None}


def _imported_constants(tree, exported):
    """Bind the constants this module imports from a sibling under `tests/`."""
    bound = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level or node.module not in exported:
            continue
        for alias in node.names:
            source = exported[node.module].get(alias.name)
            if source:
                bound.setdefault(alias.asname or alias.name, []).extend(source)
    return bound


def _imported_stems(tree, exported):
    """The sibling stems this module imports as modules (`import test_cli`)."""
    return {alias.asname or alias.name
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
            if (alias.asname or alias.name) in exported}


def _launches(tree, exported=None):
    """Every launch in a module, with the shape the exemption table keys on.

    The population is every call the repository's own predicate says places
    a child. Nothing is filtered out before that, so a module the walk
    cannot parse, or a launcher spelled a way this does not follow, shows up
    as an unclassified site rather than as a clean tree.
    """
    if exported is None:
        exported = _sibling_constants()
    receivers = census._subprocess_receivers(tree)
    direct = census._from_import_launches(tree)
    aliases = census._member_aliases(tree, receivers, direct)
    module_constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    module_constants.setdefault(
                        target.id, []).append(node.value)
    found = []
    shared = {
        'exported': exported,
        'stems': _imported_stems(tree, exported),
    }
    scopes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append((node.name, _own_statements(node, node.body)))
    # A launch at module scope, in a class body, or in a nested function is
    # a site too, and a walk that only entered top-level functions would
    # miss every one of them. Each is built the same way: the container's
    # own statements, minus the definitions that are scopes of their own —
    # so a method is counted once under its own name and a class-body
    # statement once under the class, rather than twice or not at all.
    for owner, label in ((tree, '<module>'),):
        scopes.append((label, _own_statements(owner, tree.body)))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            scopes.append((node.name,
                           _own_statements(node, node.body)))
    for name, scope in scopes:
        scope_context = dict(shared)
        scope_context['bound'] = dict(module_constants)
        scope_context['bound'].update(_imported_constants(tree, exported))
        if name != '<module>':
            scope_context['bound'].update(_assignments_in(scope))
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            if not census._is_launch(node, receivers, direct, aliases):
                continue
            argv = node.args[0] if node.args else next(
                (k.value for k in node.keywords if k.arg in ('args', 'argv')),
                None)
            found.append({
                'line': node.lineno,
                'callee': ast.unparse(node.func),
                'function': name,
                'verdict': (_executable_verdict(argv, scope_context)
                            if argv is not None else (UNRESOLVED, None)),
                'deadline': _deadline(node),
                'node': node,
            })
    return sorted(found, key=lambda row: row['line'])


def _own_statements(owner, body):
    """A container's own statements: its body minus every nested definition.

    A method, a nested function and a nested class are each a scope the walk
    enters on its own, so leaving one in here would report its launches a
    second time under the enclosing name — and a table row keyed on the
    inner name would then silence both, which is wider than the key reads.
    """
    definitions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return ast.Module(
        body=[statement for statement in body
              if not isinstance(statement, definitions)],
        type_ignores=[])


def _deadline(launch):
    """The value node of this launch's `timeout=`, or None.

    The VALUE, not its printed form. `timeout=None`, `timeout=0` and
    `timeout=False` are a deadline that never arrives, and reading them off
    `ast.unparse` reported all three as a bound.
    """
    for keyword in launch.keywords:
        if keyword.arg == 'timeout':
            return keyword.value
    return None


def _inside_expiry_handler(node, parents):
    """Whether a call sits in a handler for a child that already timed out.

    A drain or a reap of an already-killed child bounds nothing about that
    child's execution. The bound this control is about ends a child still
    running on its own, and `_dashnode.py` carries three `timeout=` keywords
    on the same receiver for exactly this reason — only the first bounds
    the child.
    """
    current = parents.get(id(node))
    while current is not None:
        if isinstance(current, ast.ExceptHandler) and current.type is not None:
            if 'TimeoutExpired' in ast.unparse(current.type):
                return True
        current = parents.get(id(current))
    return False


def _bounds_its_own_child(tree, launch, parents):
    """Whether a wait on THIS child, outside any expiry handler, is bounded.

    `subprocess.run(..., timeout=…)` is the bound at the launch itself. A
    `Popen` is not, and needs a later `child.communicate`/`child.wait` on
    the name the launch bound the child to. A `timeout=` anywhere else in
    the enclosing function is not this: the drain of a killed process and
    the reap after it are both bounded and neither bounds the child.
    """
    if _bounds(launch['deadline']):
        return True
    name = _child_name(tree, launch['node'])
    if name is None:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _bounds(next((word.value for word in node.keywords
                             if word.arg == 'timeout'), None)):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute):
            continue
        if function.attr not in ('communicate', 'wait'):
            continue
        if ast.unparse(function.value) != name:
            continue
        if _inside_expiry_handler(node, parents):
            continue
        return True
    return False


def _bounds(value):
    """Whether this `timeout=` value is a deadline that can actually expire.

    A keyword that is present is not a bound. `timeout=None` waits forever,
    `timeout=0` expires before the child is launched, and `timeout=False`
    is `0` — each is the stdlib's own "no deadline" spelling, and reading
    them off the printed form reported all three as a bound. A composed
    name is a deadline; a constant is only one if it is a positive number.
    """
    if value is None:
        return False
    if isinstance(value, ast.Constant):
        number = value.value
        if number is None or number is False:
            return False
        if isinstance(number, (int, float)) and number <= 0:
            return False
        return True
    return True


def _child_name(tree, launch):
    """The name the launch call binds the launched child to, or None."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(child is launch for child in ast.walk(node.value)):
            continue
        if isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
    return None


def _exempt(shape, launch):
    """Whether a `node` verdict this table excuses.

    Keyed on the call's shape, exactly as `UNRESOLVED_LAUNCHES` is, so a
    row is as auditable as the rows it joins. And restricted to a verdict
    the walk could not BIND: a launch that resolves to a real
    `shutil.which('node')` is never exempteable, so a row written to
    silence a control's own stub cannot become the place a real one hides.
    """
    return shape in NOT_FIXED_WORK and launch['verdict'][1] == 'spelled'


def _sweep():
    """The four failure lists the real tree produces."""
    unrouted, unbounded, unclassified = [], [], []
    used = set()
    for path in sorted(TESTS.glob('*.py')):
        if path.name in NOT_SITES:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        parents = _parents(tree)
        for launch in _launches(tree):
            shape = (path.name, launch['function'], launch['callee'])
            verdict = launch['verdict'][0]
            if verdict == UNRESOLVED:
                if shape in UNRESOLVED_LAUNCHES:
                    used.add(shape)
                else:
                    unclassified.append(f'{path.name}:{launch["line"]} in '
                                        f'{launch["function"]}()')
                continue
            if verdict != NODE:
                continue
            if path.name in CLASSIFYING_MODULES:
                if not _bounds_its_own_child(tree, launch, parents):
                    unbounded.append(f'{path.name}:{launch["line"]}')
                continue
            # A control that plants its own stub node to prove routing is a
            # real thing in this tree, and a finding it cannot be excused
            # from is a finding its author learns to ignore. So the table is
            # consulted here too — but ONLY for a `node` verdict the walk
            # could not bind. A row cannot silence a launch that resolves
            # to a real `which('node')`, so it cannot become the hiding
            # place for one, and the unused-row check covers this class
            # exactly as it covers the other.
            if _exempt(shape, launch):
                used.add(shape)
                continue
            unrouted.append(f'{path.name}:{launch["line"]} '
                            f'(timeout={ast.unparse(launch["deadline"])})')
    unused = sorted(
        (set(UNRESOLVED_LAUNCHES) | set(NOT_FIXED_WORK)) - used)
    return unrouted, unbounded, unclassified, unused


def test_every_fixed_work_node_child_goes_through_the_shared_detector(tmp):
    """The rule, read off the tree rather than off a list of sites.

    One findings list and one assert over all four classes, so a run reports
    every class it found rather than the first. An early assert here is how
    a plant in a later class hides behind one caught by an earlier one.
    """
    del tmp
    unrouted, unbounded, unclassified, unused = _sweep()
    findings = []
    if unrouted:
        findings.append(
            'a Node child whose cost is a fixed unit of work is launched '
            'outside the shared hang detector:\n  '
            + '\n  '.join(unrouted))
    if unbounded:
        findings.append(
            'a classifying module left its child with no bound of its own:\n  '
            + '\n  '.join(unbounded))
    # Fail-closed: an executable the walk cannot resolve is a site it has
    # not discharged, not a site it has decided is not node.
    if unclassified:
        findings.append(
            'a launch whose executable this walk cannot resolve, so it '
            'cannot prove the child is not node. Name it in '
            'UNRESOLVED_LAUNCHES with the reason, or teach the resolver '
            'the shape:\n  ' + '\n  '.join(unclassified))
    # And the table cannot outlive what it excused, or it becomes a set of
    # permissions rather than a record of what could not be classified.
    if unused:
        findings.append(
            'a row in UNRESOLVED_LAUNCHES or NOT_FIXED_WORK that no '
            'longer matches any launch:\n  '
            + '\n  '.join(str(row) for row in unused))
    assert not findings, '\n'.join(findings)


def test_a_deadline_that_cannot_expire_is_not_a_deadline(tmp):
    """`timeout=` being present is not a bound, in all five spellings.

    The value is read as a node, not off its printed form, so the stdlib's
    own "no deadline" spellings are refused. A `Constant` that is not a
    positive number bounds nothing; a composed name is a deadline, because
    what it composes to is not visible from here and refusing it would
    refuse every real bound in the tree.
    """
    del tmp
    for value in ('None', '0', 'False', '0.0'):
        source = ('import subprocess\n'
                  'def launch():\n'
                  "    return subprocess.run(['node', 'c.js'],\n"
                  f'                          timeout={value})\n')
        launch = _launches(ast.parse(source))[0]
        assert not _bounds(launch['deadline']), (value, launch['deadline'])
    for value in ('CHILD_DEADLINE_S', 'round(30 * 2)', 'None if x else 5',
                 "'30'"):
        source = ('import subprocess\n'
                  'def launch():\n'
                  "    return subprocess.run(['node', 'c.js'],\n"
                  f'                          timeout={value})\n')
        launch = _launches(ast.parse(source))[0]
        assert _bounds(launch['deadline']), (value, launch['deadline'])
    # And the same rule on the wait that bounds a `Popen`.
    tree = ast.parse(
        'import subprocess\n'
        'def launch():\n'
        "    process = subprocess.Popen(['node', 'c.js'])\n"
        '    return process.communicate(timeout=None)\n')
    launch = _launches(tree)[0]
    assert not _bounds_its_own_child(tree, launch, _parents(tree)), (
        'timeout=None was read as a bound on the child')


def test_a_launch_in_a_class_body_is_a_site(tmp):
    """Liveness of the class-body scope, in both of its halves.

    A class body is walked by no method scope, so a launch declared as a
    class attribute is in neither the class nor any method. The second half
    is the other side of the same construction: a METHOD is counted once,
    under its own name, and not again under the class.
    """
    del tmp
    in_a_body = ('import subprocess\n'
                 'class Harness:\n'
                 "    LAUNCH = subprocess.run(['node', 'c.js'], timeout=30)\n")
    launches = _launches(ast.parse(in_a_body))
    assert [row['verdict'][0] for row in launches] == [NODE], launches
    assert [row['function'] for row in launches] == ['Harness'], launches
    in_a_method = ('import subprocess\n'
                   'class Harness:\n'
                   '    def probe(self):\n'
                   "        return subprocess.run(['node', 'c.js'],\n"
                   '                          timeout=30)\n')
    launches = _launches(ast.parse(in_a_method))
    assert len(launches) == 1, launches
    assert launches[0]['function'] == 'probe', launches
    # A nested function is its own scope too, and is counted once.
    nested = ('import subprocess\n'
              'def outer():\n'
              '    def inner():\n'
              "        return subprocess.run(['node', 'c.js'], timeout=30)\n"
              '    return inner\n')
    launches = _launches(ast.parse(nested))
    assert len(launches) == 1, launches
    assert launches[0]['function'] == 'inner', launches


def test_a_call_the_walk_does_not_recognise_is_not_resolved_through_it(tmp):
    """A call's first argument is not the call's result.

    `os.environ.get('RUNTIME', 'node')` takes a KEY, and resolving it
    asserted `other` — a positive proof of non-node about an executable
    read from the environment that may be node. `os.path.basename` and
    `Path(x).name` return a derivation, not their argument. Only the
    `which` family returns the executable it is handed.
    """
    del tmp
    for line in ("name = os.environ.get('RUNTIME', 'node')",
                 'name = os.path.basename(sys.argv[0])',
                 'name = Path(sys.argv[0]).name'):
        source = ('import os\n'
                  'import sys\n'
                  'from pathlib import Path\n'
                  'import subprocess\n'
                  'def launch():\n'
                  f'    {line}\n'
                  "    return subprocess.run([name, 'c.js'], timeout=30)\n")
        launch = _launches(ast.parse(source))[0]
        assert launch['verdict'][0] == UNRESOLVED, (line, launch['verdict'])
    # And the one call that DOES return its argument still resolves.
    which = ('import shutil\n'
             'import subprocess\n'
             'def launch():\n'
             "    name = shutil.which('node')\n"
             "    return subprocess.run([name, 'c.js'], timeout=30)\n")
    assert _launches(ast.parse(which))[0]['verdict'][0] == NODE


def test_a_node_row_silences_a_stub_and_not_a_real_child(tmp):
    """The false positive is answerable, and the answer is narrow.

    A control that plants its own stub executable to prove routing is a
    real thing in this tree, and a finding it cannot be excused from is a
    finding its author learns to ignore. So a `node` verdict is exempteable
    — but only one the walk could not BIND, so the row cannot become the
    place a real `which('node')` launch hides. Both halves are checked
    against the decision the sweep actually makes.
    """
    del tmp
    stub = ('import subprocess\n'
            'def launch(node, tmp):\n'
            "    return subprocess.run([node, 'probe.js'],\n"
            '                          capture_output=True, timeout=30)\n')
    real = ('import shutil\n'
            'import subprocess\n'
            'def launch(node=None, tmp=None):\n'
            "    node = shutil.which('node')\n"
            "    return subprocess.run([node, 'probe.js'], timeout=30)\n")
    stub_launch = _launches(ast.parse(stub))[0]
    real_launch = _launches(ast.parse(real))[0]
    assert stub_launch['verdict'] == (NODE, 'spelled'), stub_launch['verdict']
    assert real_launch['verdict'] == (NODE, 'bound'), real_launch['verdict']
    # Same module, same function, same callee — the shape a row is keyed on
    # — so the row cannot tell them apart by its key alone and must not try.
    shape = ('planted.py', 'launch', 'subprocess.run')
    original = dict(NOT_FIXED_WORK)
    NOT_FIXED_WORK[shape] = "a control's own stub node"
    try:
        assert _exempt(shape, stub_launch), 'a row could not silence it'
        assert not _exempt(shape, real_launch), (
            'a row silenced a launch that resolves to a real which(node)')
    finally:
        NOT_FIXED_WORK.clear()
        NOT_FIXED_WORK.update(original)
    # And the table is back to what it was, so the control leaves nothing
    # behind for the sweep that runs after it.
    assert NOT_FIXED_WORK == original, 'the table did not restore'


def test_a_launch_the_walk_cannot_read_is_not_a_non_launch(tmp):
    """Liveness of the fail-closed direction, on the shapes that defeat it.

    Each shape below is one a resolver that answered "not node" for
    anything it did not recognise would pass. Planting only the shapes it
    already handles would be the same defect one level down.
    """
    del tmp
    constant = ('import subprocess\n'
                "subprocess.run(['node', 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(constant))[0]['verdict'][0] == NODE
    # A function PARAMETER named `node` is the shape that was live in this
    # tree and invisible: no walk of one module can bind it. It is admitted
    # anyway, so the control demands an answer for it rather than passing.
    parameter = ('import shutil\n'
                 'import subprocess\n'
                 'def launch(node):\n'
                 "    return subprocess.run([node, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(parameter))[0]['verdict'][0] == NODE
    # A parameter under any OTHER name is unprovable, and that is the case
    # UNRESOLVED_LAUNCHES exists to discharge.
    other_parameter = ('import subprocess\n'
                       'def launch(exe):\n'
                       "    return subprocess.run([exe, 'child.js'],\n"
                       '                          timeout=30)\n')
    assert _launches(ast.parse(other_parameter))[0]['verdict'][0] == UNRESOLVED
    # A local bound to a parameter, and a second name for the executable.
    rebound = ('import subprocess\n'
               'def launch(exe):\n'
               "    other = exe\n"
               "    return subprocess.run([other, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(rebound))[0]['verdict'][0] == UNRESOLVED
    # A different spelling of the same executable, which may be node.
    alias = ('import shutil\n'
             'import subprocess\n'
             'def launch():\n'
             "    n = shutil.which('nodejs')\n"
             "    return subprocess.run([n, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(alias))[0]['verdict'][0] == UNRESOLVED
    # A computed executable, and one read out of the environment.
    computed = ('import subprocess\n'
                'def launch():\n'
                "    name = 'no' + 'de'\n"
                "    return subprocess.run([name, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(computed))[0]['verdict'][0] == UNRESOLVED
    # And the two it IS allowed to discharge, so the cases above are not
    # passing because nothing is ever classified.
    resolved = ('import shutil\n'
                'import subprocess\n'
                'def launch():\n'
                "    node = shutil.which('node')\n"
                "    return subprocess.run([node, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(resolved))[0]['verdict'][0] == NODE
    python = ('import sys\n'
              'import subprocess\n'
              'def launch():\n'
              '    return subprocess.run([sys.executable, "s.py"],\n'
              '                          timeout=30)\n')
    assert _launches(ast.parse(python))[0]['verdict'][0] == OTHER


def test_a_wait_inside_an_expiry_handler_is_not_the_bounds_of_the_child(tmp):
    """Liveness of the second direction, in the shape that made it short.

    `_dashnode.py` bounds one child three times over: the wait that ends
    it, the drain of it after it was killed, and the reap that follows the
    drain. A check that asks only whether the enclosing function mentions
    a `timeout=` is satisfied by the second two, and deleting the first
    leaves it green — which is the mutation this control exists to catch.
    """
    del tmp
    source = (
        'import subprocess\n'
        'def launch():\n'
        "    process = subprocess.Popen(['node', 'child.js'])\n"
        '    try:\n'
        '        out = process.communicate(timeout=30)\n'
        '    except subprocess.TimeoutExpired:\n'
        '        process.kill()\n'
        '        out = process.communicate(timeout=5)\n'
        '    return out\n')
    tree = ast.parse(source)
    parents = _parents(tree)
    launch = _launches(tree)[0]
    assert _bounds_its_own_child(tree, launch, parents), 'the real wait missed'
    dropped = source.replace('out = process.communicate(timeout=30)',
                             'out = process.communicate()')
    assert dropped != source, 'the plant did not reach the real code'
    tree = ast.parse(dropped)
    assert not _bounds_its_own_child(
        tree, _launches(tree)[0], _parents(tree)), (
        'a drain of a killed child satisfied the child\'s own bound')
    # A bound at the launch itself needs no later wait.
    inline = ('import subprocess\n'
              'def launch():\n'
              "    return subprocess.run(['node', 'child.js'], timeout=30)\n")
    tree = ast.parse(inline)
    assert _bounds_its_own_child(
        tree, _launches(tree)[0], _parents(tree))


def test_the_boundary_direction_can_still_tell_bounded_from_unbounded(tmp):
    """Liveness of the second direction, in both of its spellings.

    A classifying module is exempt from the routing rule, so the only thing
    stopping the exemption from becoming "no bound anywhere" is this check.
    """
    del tmp
    on_the_launch = ('import subprocess\n'
                     'def launch():\n'
                     "    return subprocess.run(['node', 'c.js'],\n"
                     '                          timeout=30)\n')
    tree = ast.parse(on_the_launch)
    assert _bounds_its_own_child(tree, _launches(tree)[0], _parents(tree))
    with_nothing = ('import subprocess\n'
                    'def launch():\n'
                    "    return subprocess.run(['node', 'c.js'])\n")
    tree = ast.parse(with_nothing)
    assert not _bounds_its_own_child(
        tree, _launches(tree)[0], _parents(tree))
    # A bound on a DIFFERENT process does not bound this one.
    other_process = ('import subprocess\n'
                     'def launch():\n'
                     "    process = subprocess.Popen(['node', 'c.js'])\n"
                     '    other = subprocess.Popen(["node", "d.js"])\n'
                     '    other.communicate(timeout=5)\n'
                     '    return process.wait()\n')
    tree = ast.parse(other_process)
    assert not _bounds_its_own_child(
        tree, _launches(tree)[0], _parents(tree))


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='nodelaunchrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
