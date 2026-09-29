"""Which bound a fixed-unit-of-work Node child is started with.

A Node child whose real cost is a fixed unit of work gets a HANG DETECTOR,
and there are two shapes of one here. A site that launches through
`tests/_noderun.py` is bounded by that module's `CHILD_DEADLINE_S`, and
that is the default. A site that does not — a real-browser capability
probe, a GM storage harness over the shipped scripts — keeps its bound at
its own call site, because reaching the shared launcher puts that module
and everything it calls inside `tests/_launch_census.py`'s audited path.
What neither shape may be is a wall-clock literal; the constant below says
what a call-site bound is composed from instead.

The cost of keeping those sites out of the shared launcher is a figure, so
it is measured rather than asserted, and the measurement SAYS WHICH DIRECTION
it moves. This branch did not hold the audited path down; it grew it. The
path went 49 -> 62, and routing the eight `CLASSIFYING_MODULES` through the
shared launcher as well would take it to 70, eight more, which is the price
the call-site shape is paying:

    cd tests && python3 -c "from pathlib import Path; import _launch_path \
as p, _node_launch_routing as r; a = set(p.path_functions( \
Path('.').resolve(), p.LAZY_MODULES)); print(len(a), \
len(a | {m for m in r.CLASSIFYING_MODULES}))"

The base is this branch's MERGE BASE, `1181af8b`, named by the commit rather
than by `origin/main` because `origin/main` moves under the branch — which is
what happened to the sentence above, carried across a rebase unmeasured.

So the count is a COUNT AND A LIST, because a bare count is what the next
commit falsifies silently. The thirteen: `_jsroute_controls.py`,
`_jsroute_harness.py`, `_mainworldharness.py`, `test_examples_parse.py`,
`test_gm_transfers.py`, `test_jsread_mask.py`, `test_overlap_bound.py`,
`test_tab_routing_js.py`, `test_tab_routing_js_await.py`,
`test_tab_routing_js_closure.py`, `test_tab_routing_js_keys.py`,
`test_tab_routing_js_reach.py` and `test_tab_routing_js_sweep.py`. "Ten,
being exactly those modules" was wrong twice over: ten is what Task 2
routed, and routing a caller brings its callers' helpers too.

The second number is the counterfactual: it adds the eight classifying
modules, and every one of them is outside the path today, which is the check
that keeps the addition honest. It is a simulation, and a simulation is not
a number the repository states, so it is stated here with its method beside
it rather than as a fact.

The success near the deadline is the site itself: every child here runs in
a suite run, so seven of the eight figures are exercised in the PASSING
path. The E2BIG probe is the eighth, and it is the DEADLINE that is
lowered rather than the child: `tests/test_real_browser_environment.py`
runs its real `python -c ''` child against a 1ms bound, so the figure 80s
is one no suite run reaches.

It is a shared helper rather than a suite because two suites need it and
neither owns it — one holds the rule over the real tree, the other the
shapes it must read and refuse — and a sibling SUITE import is a seam the
repository refuses. The underscore makes it a shared helper by that alone,
so it owns every name it declares; the names it shares are suffixed.

`tests/_node_launch_sweep.py` holds the other half — the population a
sweep reads and the findings the tree produces — and imports everything it
needs from here, so the dependency runs one way and a question about a
launch and a question about the tree are answered from two files rather
than one.

What the walk does NOT reach, stated here so it is declared rather than
discovered: two launches in `tests/test_worker_runtime.py` that start
`sys.executable` on a suite which reaches the shipped worker, at a
hand-typed `timeout=30`. They are outside the rule above because the rule
is about a Node child and these are an interpreter running a suite, and
`_executable_verdict` answers `other` for `sys.executable` on purpose — a
resolver that guessed would make the population close on its own
vocabulary. The census already records where the `timeout` policy for such
a child lives (issue #1170), so the gap is named in both places rather than
left to the next reader of either.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_TESTS_DIR = Path(__file__).resolve().parent

# --- the bound a site keeps at its own call site ----------------------------
#
# The same three-step chain `tests/_noderun.py` composes, with the multiple
# named here rather than retyped per site: five modules scaling EIGHT
# numbers by one literal is eight numbers a fix has to reach, and the
# figures below are the eight. The samples are taken with the machine BUSY,
# not idle, because a bound is two margins and only the second is what a
# bare number measures.
#
#   SITE_HANG_MULTIPLE  5   a wedged child, not a slow one
#
# What that buys is a RATIO, and one number would hide the site that matters.
# Divide the literal this branch REPLACED by the slowest sample in its own
# table — `10` everywhere but `GM_CHILD`, which was `90`: `NODE_PROBE`
# 3.397, `MINIMAL_SPAWN` 0.629, `GM_CHILD` 4.996, `CONTROL_CHILD` 4.049,
# `REPO_PROBE` 2.450, `WORKER_PROBE` 3.885, `WORKER_CHECK` 2.781, `CDP_HARNESS`
# 5.238 — a band of 0.63x to 5.24x, ONE site UNDER the child it guarded.
SITE_HANG_MULTIPLE = 5


class NodeBoundExceeded(AssertionError):
    """A child did not finish inside the bound its own site composed.

    An `AssertionError`, so it reads as the test failure it is, and named
    because a bare `subprocess.TimeoutExpired` is the other half of the
    defect: it names a figure nobody can re-derive, and it carries the
    child's output as BYTES even when the launch asked for text. It carries
    the child, the deadline and that output, because a child which stopped
    answering is exactly the case where its output is all there is.
    `context` names the site when the child is a DIAGNOSTIC — an interpreter,
    not the `node` this class is named for.
    """

    def __init__(self, command, deadline_s, stdout, stderr, context=""):
        self.command = command
        self.deadline_s = deadline_s
        self.stdout = stdout
        self.stderr = stderr
        # Each argv element is bounded too: a source handed to `node -e` is
        # arbitrarily long, and the child line is the one a reader reads
        # first.
        child = ' '.join(str(part)[:60] for part in command or ())
        super().__init__(
            f'a harness child did not finish within {deadline_s}s and was '
            f'killed; this bound is a hang detector, not a health margin, '
            f'so nothing correct reaches it{context}.\n'
            f'  child: {child}\n'
            f'  deadline: {deadline_s}s\n'
            f'  stdout: {stdout[:2000]!r}\n'
            f'  stderr: {stderr[:2000]!r}')


def _as_text(stream):
    """A `TimeoutExpired` stream as text, whatever the launch asked for.

    `subprocess.run` hands the bytes it read straight to the exception, so a
    launch with `text=True` still raises one carrying `bytes`; a report
    printing `b'partial\\n'` where the child wrote text has lost the only
    thing a reader needs. Undecodable bytes are replaced, not raised.
    """
    if stream is None:
        return ''
    return stream.decode('utf-8', 'replace')


def node_bound_expiry(why, deadline_s, context=""):
    """A site's `TimeoutExpired` as this module's named failure.

    The figure beside `why` is the composed deadline that actually fired, so
    the report names the number a maintainer re-derives. `context` is for a
    site whose child is a DIAGNOSTIC: the E2BIG probe names the command.
    """
    return NodeBoundExceeded(
        getattr(why, 'cmd', None), deadline_s,
        _as_text(getattr(why, 'stdout', None)),
        _as_text(getattr(why, 'stderr', None)), context)


# The modules the sweep does not walk, each carrying its own reason rather
# than one rule applied to all of them. A member is a reason string, not a
# name in a tuple, because a member is skipped WHOLE: every launch in it is
# skipped with it, so a table that can be appended to without saying why is a
# table whose next member silences a real defect. `_carve_outs` is what
# closes that — it asks every member for the verdicts the walk WOULD report
# and requires it to report none, so an entry added to make a red go away
# reds here instead.
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
NOT_SITES = {
    '_noderun.py':
        "the shared launcher's own Popen IS the detector, not a site of it",
    '_node_launch_routing.py':
        'the walk itself; a rule is not a site of itself',
    'test_node_launch_routing.py':
        'the control that runs the walk over the real tree',
    'test_node_launch_routing_shapes.py':
        'every plant in it is a string literal, so it contributes no launch '
        'to any walk — a fact about how it is WRITTEN, not a property of it, '
        'so this entry fails OPEN',
}

# The boundary: a module whose child is NOT a fixed unit of work, or whose
# expiry is already classified, keeps a bound of its own. The population is
# derived and never listed, so this is closed only because every member is
# separately required to still bound its own child.
#
# Each reason NAMES the function it excuses, in backticks and nothing else
# in backticks, because the key is a module while the requirement it
# carries is per-launch: a reason that reads as though it covered the whole
# file is a reader's licence to add a fourth launch under it.
# `tests/test_node_launch_routing.py` requires every backticked name to be a
# function the module still defines, so a rename reds rather than quietly
# widening the exemption the sentence claims to cover.
CLASSIFYING_MODULES = {
    '_dashnode.py': '`_run_dashboard_node_once` scales its own bound per '
                    'retry attempt',
    '_gm_harness.py': '`_run_node` is a real-browser storage boundary '
                      '(task 3)',
    '_overlap.py': '`run_background_overlap` has its expiry already '
                   'classified by the harness',
    '_realbrowser.py': '`browser_requirements` is a real-browser probe with '
                       'a composed bound (task 3)',
    # Its executable is a function PARAMETER named `node`, so the walk
    # admits it without binding it — a name spelled `node` is in scope
    # whether or not it resolves, because admitting a site only makes the
    # control ask for more, while leaving one out loses it. The requirement
    # that keeps it honest is the one every member carries.
    '_realbrowser_workers.py': '`cdp_call` is a CDP call whose bound IS the '
                               'response deadline it asserts, classified '
                               'into CDPTimeout by the module (task 3). Its '
                               'other two launches are a real browser rather '
                               'than a Node child, and UNRESOLVED_LAUNCHES '
                               'names them by shape',
    'test_real_browser_control_extension.py':
        '`test_the_control_extension_satisfies_its_own_probe` is a '
        'real-browser probe (task 3)',
    'test_real_browser_environment.py':
        'the two real-browser probes, in '
        '`test_repository_node_probe_starts_and_terminates` and in '
        '`evaluate`, whose executable is its own node parameter (task 3)',
    'test_real_browser_harness.py':
        '`_bounded` is a real-browser probe whose executable is its own '
        'node parameter (task 3)',
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
    ('test_plant_restore.py', '_as_nobody', 'subprocess.run'):
        'a sys.executable child behind the `command` parameter, running the '
        "plant helper rather than a Node child: the same shape "
        '`tests/test_worker_runtime.py` carries and for the same reason',
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
# reading. Such a site would be named here, and the table is empty because
# the two `spelled` sites the tree actually holds are answered by
# `CLASSIFYING_MODULES` before this table is ever consulted, not by a row.
# What a row may and may not excuse is the `bound`/`spelled` asymmetry,
# stated once in `_executable_verdict`, and
# `tests/test_node_launch_routing_shapes.py` pins both halves of it.
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


def _resolved_constant(name, own, imported):
    """A constant as the module under test BINDS it, as source.

    A module-level assignment shadows the import it shares a name with, so
    the two are read in that order. This exists for `SITE_HANG_MULTIPLE`:
    a rule that checks a deadline is spelled `round(X * SITE_HANG_MULTIPLE)`
    has checked that the NAME is used and nothing about what the name is
    worth, and the name is rebindable at module scope. One line in the real
    `tests/_gm_harness.py` composing a 208-day bound left the walk, the
    census and the control that reads the spelling all green.
    """
    found = own.get(name) or imported.get(name)
    if not found:
        return None
    # A module-level assignment arrives as the one node it bound; an import
    # arrives as a LIST of the sources it resolved to, because a name bound
    # more than once is what a shadowing import looks like.
    return ast.unparse(found[0] if isinstance(found, list) else found)


def _deadline(launch):
    """The value node of this launch's `timeout=`, or None.

    The VALUE, not its printed form — `_bounds` says what makes one a
    deadline.
    """
    for keyword in launch.keywords:
        if keyword.arg == 'timeout':
            return keyword.value
    return None


def _names_a_timeout(handler):
    return (isinstance(handler, ast.ExceptHandler) and handler.type is not None
            and 'TimeoutExpired' in ast.unparse(handler.type))


def _inside_expiry_handler(node, parents):
    """Whether a call sits in an `except` for a child that already timed out.

    A drain or a reap of an already-killed child bounds nothing about that
    child's execution, and `_dashnode.py` carries three `timeout=` keywords
    on one receiver for exactly that reason — only the first bounds the
    child. The `finally:` of a `try` is NOT read here, and whether a
    `finally` is a drain is a question about the retyped-bound rule rather
    than about this one; `tests/test_node_launch_routing.py`'s reader
    answers it, with the condition that makes the answer right.
    """
    current = node
    while (parent := parents.get(id(current))) is not None:
        if _names_a_timeout(parent):
            return True
        current = parent
    return False


def _bounds_its_own_child(launch, parents):
    """Whether a wait on THIS child, outside any expiry handler, is bounded.

    `subprocess.run(..., timeout=…)` is the bound at the launch itself. A
    `Popen` is not, and needs a later `child.communicate`/`child.wait` on
    the name the launch bound the child to. A `timeout=` anywhere else in
    the function is not this; `_inside_expiry_handler` is what tells a
    cleanup apart from a bound.

    The search is over the launch's OWN scope, which is the whole of the
    difference between this question and the question it used to ask. A
    module-wide search is answered by any sibling that binds the same name:
    two `Popen`s in one file, one bounded and one not, each certify the
    other, and the unbounded one is reported as bounded — a false green,
    invisible to every other gate, and the direction that loses a site. A
    bound that is not reachable from the launch it bounds is not this
    launch's bound, so the walk stays inside the scope the launch was found
    in.
    """
    if _bounds(launch['deadline']):
        return True
    name = _child_name(launch['scope'], launch['node'])
    if name is None:
        return False
    for node in ast.walk(launch['scope']):
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


def _child_name(scope, launch):
    """The name the launch call binds the launched child to, or None.

    `scope` is the launch's own, for the reason `_bounds_its_own_child`
    states: a name another function binds is not this launch's name, and
    matching on it is how one child's bound came to certify another's.
    """
    for node in ast.walk(scope):
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
