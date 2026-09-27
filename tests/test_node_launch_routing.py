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
    ('test_static_guard_regressions.py', '_cache_collision_sequence',
     'real_run'):
        'a mutation-planting DOUBLE for subprocess.run, holding the real '
        "one under a local and calling it through `*args`",
    ('test_static_guard_regressions.py', 'run', 'real_run'):
        'the same double, reached through the patched subprocess.run',
    ('test_static_guard_regressions.py',
     'test_mutation_gate_refuses_site_initialization', 'real_run'):
        'the same double, with the `-S` argument filtered out of a caller '
        "command the walk does not resolve",
    ('test_workflow_bash.py',
     'test_workflow_bash_resolves_relative_candidate_for_other_cwd',
     'subprocess.run'):
        "a bash child behind `_util.workflow_bash()`, the thing under test",
}

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
    """`node`, `other` or `unresolved` for a launch's executable.

    `unresolved` is the default and is what anything the walk cannot PROVE
    is not node reaches: a parameter, a subscript, a computed string, a
    call it does not recognise. Answering `other` for those would make the
    population close on the resolver's own vocabulary, and a rename is all
    it takes for a real site to disappear.

    `scope` carries the three tables resolution reads — the names this
    module binds, the constants its siblings export, and the sibling stems
    it imports — so nothing is reached through a global.
    """
    if depth > RESOLUTION_DEPTH:
        return UNRESOLVED
    if isinstance(expression, (ast.List, ast.Tuple)):
        if not expression.elts:
            return UNRESOLVED
        return _executable_verdict(expression.elts[0], scope, depth)
    if isinstance(expression, ast.Constant):
        return NODE if expression.value == 'node' else OTHER
    if isinstance(expression, ast.BinOp):
        # `CLI + ['exec', …]` is how most of the CLI suites spell their
        # argv: the executable is the left operand and the `+` only adds
        # arguments. A computed STRING is different — `'no' + 'de'` names
        # node, and folding it would read as "not node", so a constant on
        # the left stays unresolved.
        if isinstance(expression.left, (ast.Constant, ast.BinOp)):
            return UNRESOLVED
        return _executable_verdict(expression.left, scope, depth)
    if isinstance(expression, ast.Attribute):
        if ast.unparse(expression) == 'sys.executable':
            return OTHER
        # `test_cli.CLI + [...]` reaches a sibling's constant through the
        # module object. The same constant imported by name resolves
        # through `bound`; this is the other spelling, and both are used.
        owner = expression.value
        if isinstance(owner, ast.Name) and owner.id in scope['stems']:
            return _verdicts_over(
                scope['exported'].get(owner.id, {}).get(expression.attr, []),
                scope, depth)
        return UNRESOLVED
    if isinstance(expression, ast.Name):
        # A name spelled `node` is admitted as the node executable whether
        # or not this walk can bind it. That is the conservative
        # direction and it is deliberate: admitting a site only makes the
        # control ask for more of it, while leaving one out loses it, and
        # `_realbrowser_workers.py` binds its executable as a parameter
        # precisely so that this admits it.
        if expression.id == 'node':
            return NODE
        if expression.id not in scope['bound']:
            return UNRESOLVED
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
                    return (NODE if argument.value == 'node' else UNRESOLVED)
            return UNRESOLVED
        if expression.args:
            return _executable_verdict(expression.args[0], scope, depth + 1)
    return UNRESOLVED


def _verdicts_over(expressions, scope, depth):
    """One verdict for several bindings of one name, most specific first."""
    verdicts = {_executable_verdict(expression, scope, depth)
                for expression in expressions}
    if NODE in verdicts:
        return NODE
    return OTHER if verdicts == {OTHER} else UNRESOLVED


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
    scopes = [(node.name, node) for node in ast.walk(tree)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    # A launch at module scope is a site too, and a walk that only entered
    # functions would miss every one of them. Classes are excluded as well
    # as functions: a method is a scope of its own and is reached by the
    # walk above, so leaving a class in would count every one of them
    # twice under two different names.
    scopes.append(('<module>', ast.Module(body=[
        statement for statement in tree.body
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef))],
        type_ignores=[])))
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
            deadline = next(
                (ast.unparse(k.value) for k in node.keywords
                 if k.arg == 'timeout'),
                None)
            found.append({
                'line': node.lineno,
                'callee': ast.unparse(node.func),
                'function': name,
                'verdict': (_executable_verdict(argv, scope_context)
                            if argv is not None else UNRESOLVED),
                'deadline': deadline,
                'node': node,
            })
    return sorted(found, key=lambda row: row['line'])


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
    if launch['deadline'] is not None:
        return True
    name = _child_name(tree, launch['node'])
    if name is None:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(word.arg == 'timeout' for word in node.keywords):
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
            if launch['verdict'] == UNRESOLVED:
                if shape in UNRESOLVED_LAUNCHES:
                    used.add(shape)
                else:
                    unclassified.append(f'{path.name}:{launch["line"]} in '
                                        f'{launch["function"]}()')
                continue
            if launch['verdict'] != NODE:
                continue
            if path.name in CLASSIFYING_MODULES:
                if not _bounds_its_own_child(tree, launch, parents):
                    unbounded.append(f'{path.name}:{launch["line"]}')
                continue
            unrouted.append(f'{path.name}:{launch["line"]} '
                            f'(timeout={launch["deadline"]})')
    unused = sorted(set(UNRESOLVED_LAUNCHES) - used)
    return unrouted, unbounded, unclassified, unused


def test_every_fixed_work_node_child_goes_through_the_shared_detector(tmp):
    """The rule, read off the tree rather than off a list of sites."""
    del tmp
    unrouted, unbounded, unclassified, unused = _sweep()
    assert not unrouted, (
        'a Node child whose cost is a fixed unit of work is launched '
        'outside the shared hang detector:\n' + '\n'.join(unrouted))
    assert not unbounded, (
        'a classifying module left its child with no bound of its own:\n'
        + '\n'.join(unbounded))
    # Fail-closed: an executable the walk cannot resolve is a site it has
    # not discharged, not a site it has decided is not node.
    assert not unclassified, (
        'a launch whose executable this walk cannot resolve, so it cannot '
        'prove the child is not node. Name it in UNRESOLVED_LAUNCHES with '
        'the reason, or teach the resolver the shape:\n'
        + '\n'.join(unclassified))
    # And the table cannot outlive what it excused, or it becomes a set of
    # permissions rather than a record of what could not be classified.
    assert not unused, (
        'a UNRESOLVED_LAUNCHES row that no longer matches any launch:\n'
        + '\n'.join(str(row) for row in unused))


def test_a_launch_the_walk_cannot_read_is_not_a_non_launch(tmp):
    """Liveness of the fail-closed direction, on the shapes that defeat it.

    Each shape below is one a resolver that answered "not node" for
    anything it did not recognise would pass. Planting only the shapes it
    already handles would be the same defect one level down.
    """
    del tmp
    constant = ('import subprocess\n'
                "subprocess.run(['node', 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(constant))[0]['verdict'] == NODE
    # A function PARAMETER named `node` is the shape that was live in this
    # tree and invisible: no walk of one module can bind it. It is admitted
    # anyway, so the control demands an answer for it rather than passing.
    parameter = ('import shutil\n'
                 'import subprocess\n'
                 'def launch(node):\n'
                 "    return subprocess.run([node, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(parameter))[0]['verdict'] == NODE
    # A parameter under any OTHER name is unprovable, and that is the case
    # UNRESOLVED_LAUNCHES exists to discharge.
    other_parameter = ('import subprocess\n'
                       'def launch(exe):\n'
                       "    return subprocess.run([exe, 'child.js'],\n"
                       '                          timeout=30)\n')
    assert _launches(ast.parse(other_parameter))[0]['verdict'] == UNRESOLVED
    # A local bound to a parameter, and a second name for the executable.
    rebound = ('import subprocess\n'
               'def launch(exe):\n'
               "    other = exe\n"
               "    return subprocess.run([other, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(rebound))[0]['verdict'] == UNRESOLVED
    # A different spelling of the same executable, which may be node.
    alias = ('import shutil\n'
             'import subprocess\n'
             'def launch():\n'
             "    n = shutil.which('nodejs')\n"
             "    return subprocess.run([n, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(alias))[0]['verdict'] == UNRESOLVED
    # A computed executable, and one read out of the environment.
    computed = ('import subprocess\n'
                'def launch():\n'
                "    name = 'no' + 'de'\n"
                "    return subprocess.run([name, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(computed))[0]['verdict'] == UNRESOLVED
    # And the two it IS allowed to discharge, so the cases above are not
    # passing because nothing is ever classified.
    resolved = ('import shutil\n'
                'import subprocess\n'
                'def launch():\n'
                "    node = shutil.which('node')\n"
                "    return subprocess.run([node, 'child.js'], timeout=30)\n")
    assert _launches(ast.parse(resolved))[0]['verdict'] == NODE
    python = ('import sys\n'
              'import subprocess\n'
              'def launch():\n'
              '    return subprocess.run([sys.executable, "s.py"],\n'
              '                          timeout=30)\n')
    assert _launches(ast.parse(python))[0]['verdict'] == OTHER


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
