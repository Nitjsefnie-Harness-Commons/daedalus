"""Which launcher a fixed-unit-of-work Node child is started with.

A Node child whose real cost is a fixed unit of work is bounded by the
shared hang detector in `tests/_noderun.py`, not by a number typed at its
call site: a wall-clock literal measures the runner's busyness and nothing
else, and that is what failed correct children on a loaded CI runner.

This is a shared helper rather than a suite because two suites need the
walk and neither owns it: one holds the rule enforced over the real tree,
the other holds the shapes the walk has to read and the shapes it must
refuse. A sibling SUITE import is a seam the repository refuses — see
`tests/test_suite_import_boundaries.py` — so the walk lives here and both
suites read it.

It is named with an underscore, so it is a shared helper by that alone and
owns every name it declares. `TESTS`, `OTHER`, `UNRESOLVED` and `_sweep` are
each bound by an untouched sibling, so the names here are suffixed to say
what they are for this walk rather than to collide with them.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launch_census as census  # noqa: E402
# The parent map is that module's helper, not a second copy of it: a helper
# defined once in a shared module and imported by every user is the whole
# point of the rule that would otherwise red this file for re-implementing.
from _command_type_readers import _parents  # noqa: E402

_TESTS_DIR = Path(__file__).resolve().parent

# The modules the sweep does not walk, each for its own reason rather than
# one rule applied to all of them.
#
# `_noderun.py` holds the launcher's own `Popen`, so it is the subject
# rather than a site. This module and the routing suite are the walk and
# the control that runs it: a control cannot be a site of the rule it
# states, and this module is the rule.
#
# The shapes suite is a different case and is named for it: it contributes
# zero launches today, because every plant in it is a string literal and
# `ast.walk` never sees a `Call` inside one. That is a fact about how it
# is WRITTEN, not a property of it, so the entry fails OPEN — a real launch
# added to a shapes test would be suppressed rather than reported. It is
# named anyway, because the alternative is a suite whose correctness
# depends on a property of a file nobody is thinking about when they edit
# it, and the honest direction to fail is stated here rather than left for
# a reader to infer.
NOT_SITES = ('_noderun.py', '_node_launch_routing.py',
             'test_node_launch_routing.py',
             'test_node_launch_routing_shapes.py')

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

# A `node` verdict can be a FALSE one — a control that plants its own stub
# executable to prove routing writes a function whose parameter is named
# `node` — and a finding its author cannot answer is a finding they stop
# reading. Such a site is named here. What a row may and may not excuse is
# the `bound`/`spelled` asymmetry, stated once in `_executable_verdict`.
NOT_FIXED_WORK = {}

VERDICT_NODE = 'node'
VERDICT_OTHER = 'other'
VERDICT_UNRESOLVED = 'unresolved'
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
        return (VERDICT_UNRESOLVED, None)
    if isinstance(expression, (ast.List, ast.Tuple)):
        if not expression.elts:
            return (VERDICT_UNRESOLVED, None)
        return _executable_verdict(expression.elts[0], scope, depth)
    if isinstance(expression, ast.Constant):
        return ((VERDICT_NODE, 'bound') if expression.value == 'node'
                else (VERDICT_OTHER, 'bound'))
    if isinstance(expression, ast.BinOp):
        # `CLI + ['exec', …]` is how most of the CLI suites spell their
        # argv: the executable is the left operand and the `+` only adds
        # arguments. A computed STRING is different — `'no' + 'de'` names
        # node, and folding it would read as "not node", so a constant on
        # the left stays unresolved.
        if isinstance(expression.left, (ast.Constant, ast.BinOp)):
            return (VERDICT_UNRESOLVED, None)
        return _executable_verdict(expression.left, scope, depth)
    if isinstance(expression, ast.Attribute):
        if ast.unparse(expression) == 'sys.executable':
            return (VERDICT_OTHER, 'bound')
        # `test_cli.CLI + [...]` reaches a sibling's constant through the
        # module object. The same constant imported by name resolves
        # through `bound`; this is the other spelling, and both are used.
        owner = expression.value
        if isinstance(owner, ast.Name) and owner.id in scope['stems']:
            return _verdicts_over(
                scope['exported'].get(owner.id, {}).get(expression.attr, []),
                scope, depth)
        return (VERDICT_UNRESOLVED, None)
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
            return (VERDICT_NODE, 'spelled')
        if expression.id not in scope['bound']:
            return (VERDICT_UNRESOLVED, None)
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
                    return ((VERDICT_NODE, 'bound') if argument.value == 'node'
                            else (VERDICT_UNRESOLVED, None))
            return (VERDICT_UNRESOLVED, None)
        # No other call's first argument IS the executable.
        # `os.environ.get('RUNTIME', 'node')` takes a KEY, and resolving it
        # would assert `other` on an executable read from the environment
        # that may be node; `os.path.basename(sys.argv[0])` and
        # `Path(x).name` return a derivation, not their argument. Only the
        # `which` family returns the executable it is handed.
        return (VERDICT_UNRESOLVED, None)
    return (VERDICT_UNRESOLVED, None)


def _verdicts_over(expressions, scope, depth):
    """One verdict for several bindings of one name, most specific first."""
    verdicts = {_executable_verdict(expression, scope, depth)
                for expression in expressions}
    if VERDICT_NODE in {verdict for verdict, _ in verdicts}:
        return (VERDICT_NODE, 'bound')
    seen = {verdict for verdict, _ in verdicts}
    if seen == {VERDICT_OTHER}:
        return (VERDICT_OTHER, 'bound')
    return (VERDICT_UNRESOLVED, None)


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
    for path in sorted(_TESTS_DIR.glob('*.py')):
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
        path = _TESTS_DIR / f'{stem}.py'
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


# Annotated, not inferred: a `{'table': None}` literal types its values as
# `None`, and the cache then refuses the dict it is built to hold.
_SIBLINGS: dict = {'table': None}


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
            scopes.append((node.name, _own_statements(node.body)))
    # A launch at module scope, in a class body, or in a nested function is
    # a site too, and a walk that only entered top-level functions would
    # miss every one of them. Each is built the same way: the container's
    # own statements, minus the definitions that are scopes of their own —
    # so a method is counted once under its own name and a class-body
    # statement once under the class, rather than twice or not at all.
    scopes.append(('<module>', _own_statements(tree.body)))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            scopes.append((node.name, _own_statements(node.body)))
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
                'verdict': (
                    _executable_verdict(argv, scope_context)
                    if argv is not None else (VERDICT_UNRESOLVED, None)),
                'deadline': _deadline(node),
                'node': node,
            })
    return sorted(found, key=lambda row: row['line'])


def _own_statements(body):
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

    The VALUE, not its printed form — `_bounds` says what makes one a
    deadline.
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
    `timeout=0` expires before the child is launched, `timeout=False` is
    `0`, and `timeout=-1` is a negative one that expires the same way —
    the last is a `UnaryOp` rather than a `Constant`, which is why this
    folds the expression instead of reading the node's type. Reading any of
    them off the printed form reported all of them as a bound. A composed
    name is a deadline; a foldable literal is only one if it is positive.
    """
    if value is None:
        return False
    if isinstance(value, (ast.Name, ast.Attribute)):
        return True
    try:
        folded = ast.literal_eval(value)
    except (ValueError, SyntaxError, TypeError):
        # A computation this walk cannot fold — `round(30 * 2)`, a ternary.
        # Refusing it would refuse every real bound in the tree.
        return True
    if folded is None or isinstance(folded, bool):
        return False
    if isinstance(folded, (int, float)):
        return folded > 0
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

    Keyed on the call's shape, exactly as `UNRESOLVED_LAUNCHES` is, and
    restricted by the `bound`/`spelled` asymmetry stated in
    `_executable_verdict`, which is where that rule is written down.
    """
    return shape in NOT_FIXED_WORK and launch['verdict'][1] == 'spelled'


def _routing_sweep():
    """The four failure lists the real tree produces."""
    unrouted, unbounded, unclassified = [], [], []
    used = set()
    for path in sorted(_TESTS_DIR.glob('*.py')):
        if path.name in NOT_SITES:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        parents = _parents(tree)
        for launch in _launches(tree):
            shape = (path.name, launch['function'], launch['callee'])
            verdict = launch['verdict'][0]
            if verdict == VERDICT_UNRESOLVED:
                if shape in UNRESOLVED_LAUNCHES:
                    used.add(shape)
                else:
                    unclassified.append(f'{path.name}:{launch["line"]} in '
                                        f'{launch["function"]}()')
                continue
            if verdict != VERDICT_NODE:
                continue
            if path.name in CLASSIFYING_MODULES:
                if not _bounds_its_own_child(tree, launch, parents):
                    unbounded.append(f'{path.name}:{launch["line"]}')
                continue
            if _exempt(shape, launch):
                used.add(shape)
                continue
            # A `Popen` carries no `timeout=` to print, and that is the most
            # likely real finding this control exists to report, so the
            # message names the child rather than raising on the way there.
            shown = launch['deadline']
            unrouted.append(
                f'{path.name}:{launch["line"]} (timeout='
                f'{ast.unparse(shown) if shown is not None else "none"})')
    unused = [(table, row) for table, rows in (
        ('UNRESOLVED_LAUNCHES', UNRESOLVED_LAUNCHES),
        ('NOT_FIXED_WORK', NOT_FIXED_WORK))
        for row in sorted(set(rows) - used)]
    return unrouted, unbounded, unclassified, unused
