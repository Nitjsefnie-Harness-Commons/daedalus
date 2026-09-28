#!/usr/bin/env python3
"""The rule the launch-routing walk enforces over the real tree.

The rule and the tables that close its population live in
`tests/_node_launch_routing.py`, the walk in `tests/_node_launch_sweep.py`,
and both are shared helpers: the plant-based controls in
`tests/test_node_launch_routing_shapes.py` need the same decisions, and a
sibling SUITE import is a seam this repository refuses
(`tests/test_suite_import_boundaries.py`).

What is here is the one control that runs the walk over every module
under `tests/` and asks the question the branch exists to keep true: is a
Node child whose cost is a fixed unit of work launched through the shared
hang detector, and does every launch the walk cannot classify appear in a
table that says why. The population is every module in that directory, not
the tracked ones: the two are the same set today, and a claim about which
one is read is a claim that drifts the moment they are not.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _node_launch_routing as routing  # noqa: E402
import _node_launch_sweep as sweep  # noqa: E402
from _command_type_readers import _parents  # noqa: E402
from _launch_path import (  # noqa: E402
    _module_constants as module_constants)

TESTS = Path(__file__).resolve().parent


def test_every_fixed_work_node_child_goes_through_the_shared_detector(tmp):
    """The rule, read off the tree rather than off a list of sites.

    One findings list and one assert over all five classes, so a run reports
    every class it found rather than the first. An early assert here is how
    a plant in a later class hides behind one caught by an earlier one.
    """
    del tmp
    unrouted, unbounded, unclassified, carved, unused = (
        sweep._routing_sweep())
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
    # A module the walk skips is skipped whole, so this is the only thing
    # that notices one being added to hide a site rather than to state a
    # reason: each member is asked the question the sweep asks, and answers
    # none of it today.
    if carved:
        findings.append(
            'a module NOT_SITES excuses carries a launch the walk would '
            'have reported, so the exemption is hiding a site rather than '
            'naming a non-site:\n  ' + '\n  '.join(carved))
    # And the table cannot outlive what it excused, or it becomes a set of
    # permissions rather than a record of what could not be classified.
    if unused:
        findings.append(
            'a row that no longer matches any launch, and the table it is '
            'in:\n  '
            + '\n  '.join(f'{table}: {row}' for table, row in unused))
    assert not findings, '\n'.join(findings)


# The eight sites that keep a bound at their own call site, as (module,
# stem). The stem names three constants: the recorded table(s)
# `<stem>*_SAMPLES_S`, the `<stem>_SLOWEST_S` taken from them, and the
# `<stem>_DEADLINE_S`. This is the OTHER direction of the rule above — the
# sites that do NOT reach the shared detector still have to stop a wedged
# child, and the census cannot be what holds them honest.
COMPOSED_BOUND_SITES = (
    ('_realbrowser.py', 'NODE_PROBE'),
    ('_realbrowser.py', 'MINIMAL_SPAWN'),
    ('_gm_harness.py', 'GM_CHILD'),
    ('test_real_browser_classification.py', 'CONTROL_CHILD'),
    ('test_real_browser_environment.py', 'REPO_PROBE'),
    ('test_real_browser_environment.py', 'WORKER_PROBE'),
    ('test_real_browser_harness.py', 'WORKER_CHECK'),
    ('test_real_browser_harness.py', 'CDP_HARNESS'),
)


def test_the_walk_reads_every_module_the_tables_name(tmp):
    """The population, measured rather than assumed.

    Neutering the walk outright IS caught — every `UNRESOLVED_LAUNCHES` row
    goes stale and names itself. Narrowing it is not: skipping
    `tests/_gm_harness.py` and nothing else left all four classes empty and
    all four controls green, because a module with no allowance row of its
    own takes no row with it when it stops being read. The guard therefore
    has a floor on TOTAL silence and none on partial blindness, which is
    the shape a check that reports nothing is not a measurement describes.

    So the sweep is asked which modules it read, and that list is required
    to hold every module the tables name, to be the whole tracked tree less
    the carve-outs, and to carry at least one launch per classifying
    module — a module the walk excuses while reading nothing in it is an
    exemption over no requirement at all.
    """
    del tmp
    walked = []
    sweep._routing_sweep(walked=walked)
    read = set(walked)
    expected = {path.name for path in TESTS.glob('*.py')} - set(
        routing.NOT_SITES)
    assert read == expected, (
        'the modules the walk read are not the tracked tree less the '
        f'carve-outs: {sorted(expected ^ read)}')
    for module_name, reason in sorted(routing.CLASSIFYING_MODULES.items()):
        assert module_name in read, (module_name, reason)
        source = (TESTS / module_name).read_text(encoding='utf-8')
        tree = ast.parse(source)
        launches = sweep._launches(tree)
        assert launches, (
            f'{module_name} is excused by the walk and contributes no '
            'launch, so the requirement that keeps it honest is applied to '
            'nothing')
        # The key is a module and the requirement is per-launch, so each
        # reason names the functions it covers — in backticks, and nothing
        # else in backticks. A reason that reads as though it covered the
        # whole file is a reader's licence to add a launch under it, and a
        # rename must red rather than quietly widen the exemption.
        functions = (ast.FunctionDef, ast.AsyncFunctionDef)
        defined = {node.name for node in ast.walk(tree)
                   if isinstance(node, functions)}
        for named in re.findall(r'`([A-Za-z_]\w*)`', reason):
            assert named in defined, (
                f'{module_name} is exempt for {named}, which it no longer '
                f'defines; it defines {sorted(defined)}')
    for module_name, stem in COMPOSED_BOUND_SITES:
        assert module_name in read, (module_name, stem)


def test_the_named_bound_failure_is_an_assertion_error(tmp):
    """`NodeBoundExceeded` is an `AssertionError`, and two artifacts say so.

    Its own docstring says it "reads as the test failure it is", and
    `tests/_realbrowser.py` keeps a whole classification on the same
    ground. The one control that used to pin the relationship lost its case
    when the `TimeoutExpired` branch was removed, and nothing replaced it —
    so a class that stopped being an `AssertionError` would keep every
    assertion in the tree green and stop reading as a test failure in the
    runner's own output.
    """
    del tmp
    assert issubclass(routing.NodeBoundExceeded, AssertionError), (
        'the named bound failure is no longer an AssertionError, so it no '
        'longer reads as the test failure it is')


def _site_constants(module_name, root=None):
    """A module's module-level bindings as source, read by the shared reader.

    `tests/_launch_path.py`'s own, not a second copy of it: a helper defined
    once in a shared module and imported by every user is the whole point of
    the rule that refuses a re-implementation.
    """
    source = (TESTS if root is None else root) / module_name
    return {name: ast.unparse(value)
            for name, value in module_constants(
                ast.parse(source.read_text(encoding='utf-8'))).items()}


def _shared_multiple(module_name, root=None):
    """`SITE_HANG_MULTIPLE` as the module under test binds it, by VALUE.

    Checking that a deadline is spelled with the shared multiple checks
    that the NAME is used; it says nothing about what the name is worth, and
    the name is rebindable at module scope. One line in the real
    `tests/_gm_harness.py` — `SITE_HANG_MULTIPLE = 10 ** 6` — composes a
    208-day hang detector, and the walk, the census and the control that
    reads the spelling all stayed green on it, because the thin-table
    control catches a fabricated TABLE and this is a fabricated MULTIPLE.

    So the value is resolved where the deadline is composed — the module's
    own assignment shadowing the import it shares a name with — and
    compared to the shared constant rather than to its name. A value this
    cannot fold is not the shared multiple, which is the fail-closed
    direction: a reader that cannot prove the figure is the one this
    repository documents must not certify it.
    """
    source = (TESTS if root is None else root) / module_name
    tree = ast.parse(source.read_text(encoding='utf-8'))
    resolved = routing._resolved_constant(
        'SITE_HANG_MULTIPLE', dict(module_constants(tree)),
        routing._imported_constants(tree, routing._sibling_constants()))
    if resolved is None:
        return None
    try:
        return ast.literal_eval(resolved)
    except (ValueError, SyntaxError, TypeError):
        return None


def _table_values(source):
    """The floats in a recorded sample table, read from its own source."""
    return [node.value for node in ast.walk(ast.parse('X = ' + source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)]


def _is_composed(constants, stem, multiple):
    """Whether a stem's deadline is `round()`ed from a table and the multiple.

    Composed means all three: a recorded table of samples, a `max()` taken
    over it, and a deadline rounded from that by the shared multiple. A
    number written at the call site is none of them. `multiple` is that
    multiple resolved where the deadline is composed, so the last step is a
    comparison of VALUES rather than of names, and a caller that cannot
    resolve it passes `None` and is refused rather than believed.
    """
    def has(name):
        # `.get`, not `[...]`: a site that deleted one of the three must be
        # REFUSED by this rule and named by the caller, not raise a KeyError
        # naming a dictionary key at a reader who cannot act on it.
        return name in constants

    tables = [name for name in constants
              if name.startswith(stem) and name.endswith('_SAMPLES_S')]
    if not tables or not has(f'{stem}_SLOWEST_S'):
        return False
    taken = ast.parse('X = ' + constants[f'{stem}_SLOWEST_S']).body[0].value
    if not (isinstance(taken, ast.Call)
            and ast.unparse(taken.func) == 'max'):
        return False
    # `*TABLE` unparses with its star, so a substring test reads both
    # the one-table `max(TABLE)` and the two-table `max(*A, *B)`.
    sources = [ast.unparse(argument) for argument in taken.args]
    if not any(any(table in source for source in sources)
               for table in tables):
        return False
    if multiple != routing.SITE_HANG_MULTIPLE:
        return False
    return has(f'{stem}_DEADLINE_S') and constants[f'{stem}_DEADLINE_S'] == (
        f'round({stem}_SLOWEST_S * SITE_HANG_MULTIPLE)')


def _retyped_bound_sites(root=None):
    """The composed-bound sites that no longer compose their figure."""
    typed = []
    for module_name, stem in COMPOSED_BOUND_SITES:
        if not _is_composed(_site_constants(module_name, root), stem,
                            _shared_multiple(module_name, root)):
            typed.append(f'{module_name}:{stem}')
    return typed


def _composed_population(root, module_name, plant):
    """Every composed-bound module, copied under `root`, one of them planted.

    The whole population, not the planted module alone: a control pointed
    at a directory holding one file answers a question about that file, and
    the question here is whether the control still ACCEPTS the four
    unplanted ones.
    """
    for name, _ in COMPOSED_BOUND_SITES:
        sweep._planted_module_copy(
            root, name, plant if name == module_name else 'VALUE = 1')
    return root


def test_a_call_site_bound_is_derived_and_not_written(tmp):
    """The eight composed bounds, refused if any is a retyped number.

    A site that keeps its bound at its own call site is deliberately OUTSIDE
    `tests/_launch_census.py`'s audited path, and the cost of that is this:
    the census's rule that a bound's figure must be COMPOSED rather than
    written reaches only path modules, so a bare `10` retyped at one of these
    call sites was read by nothing in the tree. Every control in the
    repository stayed green against that mutation, which is the observation
    this answers. The figure behind "deliberately outside" is in
    `tests/_node_launch_routing.py`'s docstring, measured at this head and
    stated with the direction it moves: the audited path is 58 modules, it
    was 48 on `origin/main`, and this branch is what raised it.

    The negative half is planted because a rule that cannot tell a composed
    figure from a typed one is the false green this branch exists to remove,
    and the second plant is in a real module because the first one only
    proves the reader against itself.
    """
    del tmp
    assert not _retyped_bound_sites(), (
        'a call-site bound that is a retyped number rather than a figure '
        f'composed from its recorded table: {_retyped_bound_sites()}')
    composed = {'GM_CHILD_SAMPLES_S': '(1.0, 2.0, 3.0)',
                'GM_CHILD_SLOWEST_S': 'max(*GM_CHILD_SAMPLES_S)',
                'GM_CHILD_DEADLINE_S': 'round(GM_CHILD_SLOWEST_S * '
                                       'SITE_HANG_MULTIPLE)'}
    assert _is_composed(composed, 'GM_CHILD', routing.SITE_HANG_MULTIPLE)
    for broken, why in (
            ({**composed, 'GM_CHILD_DEADLINE_S': '90'},
             'the deadline is a retyped number'),
            ({**composed, 'GM_CHILD_SLOWEST_S': '3.0'},
             'the slowest sample is not taken from the table'),
            ({key: value for key, value in composed.items()
             if not key.endswith('_SAMPLES_S')},
             'the recorded table is gone')):
        assert not _is_composed(
            broken, 'GM_CHILD', routing.SITE_HANG_MULTIPLE), why


def test_a_module_cannot_rebind_the_shared_hang_multiple(tmp):
    """The multiple is compared by VALUE, in the module that composes with it.

    A deadline spelled `round(X * SITE_HANG_MULTIPLE)` proves the name is
    used, and the name is a module-scope binding a real module can shadow.
    One line appended to the real `tests/_gm_harness.py` — the same table,
    the same algebra, the same spelling, and a `GM_CHILD_DEADLINE_S` of
    18,015,000s — left this file, `tests/test_harness_launch_bounds.py` and
    `tests/test_repo_layout.py` all green, because the thin-table control
    catches a fabricated table and a fabricated multiple is not a table.

    The plant is a real module's own bytes with one line appended, read back
    from disk, so what the control reads is what the walk would read. The
    other four composed modules are copied unplanted beside it, so the
    assertion is that this ONE is refused and not that the control refuses
    everything.
    """
    root = _composed_population(Path(tmp) / 'planted', '_gm_harness.py',
                                'SITE_HANG_MULTIPLE = 10 ** 6')
    plant = (root / '_gm_harness.py').read_text(encoding='utf-8')
    assert plant.rstrip().endswith('SITE_HANG_MULTIPLE = 10 ** 6'), (
        'the plant did not reach the real module')
    assert _retyped_bound_sites(root) == ['_gm_harness.py:GM_CHILD'], (
        'a module that rebinds SITE_HANG_MULTIPLE composed its deadline '
        f'from a multiple this control never read: '
        f'{_retyped_bound_sites(root)}')
    assert _shared_multiple('_gm_harness.py') == routing.SITE_HANG_MULTIPLE, (
        'the healthy reading of the real module is not the shared multiple')
    # `10 ** 6` does not fold, and a value this cannot read is refused
    # rather than believed — which is the same verdict by a second route.
    assert _shared_multiple('_gm_harness.py', root) is None, (
        'the planted multiple resolved where the deadline is composed')
    assert not _is_composed(
        _site_constants('_gm_harness.py', root), 'GM_CHILD', 10 ** 6), (
        'a folded fabricated multiple was certified')


def _in_a_drain(node, parents, receiver):
    """Whether this call is a DRAIN of a child that already timed out.

    A drain bounds nothing about the child's execution, so a retyped number
    in one is a false positive. TWO shapes are a drain: the `except
    subprocess.TimeoutExpired:` body, which the module's own reader already
    recognises, and the `finally:` of a `try` that HAS such a handler AND
    already bounds this same receiver in its own body.

    That last clause is what keeps the rule from emptying a bound. In
    `try: child.wait(timeout=10) finally: child.kill()` the bound is in the
    `try` and the `finally` drains. In `try: child.communicate() ... finally:
    child.wait(timeout=30)` the `try` is unbounded, the `finally` IS the
    bound, and reading it as a drain would leave that child unbounded. The
    statement under test is excluded from its own search, or every `finally`
    would vouch for itself.
    """
    current = node
    while (parent := parents.get(id(current))) is not None:
        if _is_timeout_handler(parent):
            return True
        if (isinstance(parent, ast.Try) and current in parent.finalbody
                and any(map(_is_timeout_handler, parent.handlers))
                and _bounded_in_the_try(parent, receiver, current)):
            return True
        current = parent
    return False


def _is_timeout_handler(node):
    """Whether a node is an `except` for a child that already timed out."""
    return (isinstance(node, ast.ExceptHandler)
            and node.type is not None
            and 'TimeoutExpired' in ast.unparse(node.type))


def _bounded_in_the_try(try_node, receiver, statement):
    """Whether this `try` already carries a `timeout=` on the same receiver.

    The statement under test is excluded, or every `finally` would vouch
    for itself and the rule would empty the very bound it is protecting.
    """
    for part in (*try_node.body, *try_node.finalbody):
        if part is statement:
            continue
        for inner in ast.walk(part):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and ast.unparse(inner.func.value) == receiver
                    and any(word.arg == 'timeout'
                            for word in inner.keywords)):
                return True
    return False


def _retyped_bounds(routing, launch, parents):
    """Every retyped number on this child's NAME, with its line.

    The walk is over the launch's OWN scope, and that is load-bearing in
    both directions rather than one. A module-wide walk lets two launches
    that bind the same name report each other's bounds: a bounded sibling
    is then reported TWICE, once for each launch, at the bounded one's
    line — a false positive that points a reader at code that is right. The
    same walk answers the far worse question in the other direction, inside
    the sweep rather than here: an unbounded sibling is CERTIFIED bounded
    by the bound beside it, and the sweep's own `unbounded` class comes back
    empty. A false positive is a nuisance; a false green is a lost site.

    Two further things differ from `_bounds`, on purpose. The bound
    PREDICATE: `_bounds` folds and rejects `None`, a bool or a
    non-positive, so `timeout=0` reads as no bound there and as a retyped
    number here. And the DRAIN, because a retyped number in a drain is a
    false positive this control must not report while the sweep's own
    question is not the same one — see `_in_a_drain`.
    """
    deadline = launch['deadline']
    if isinstance(deadline, ast.Constant):
        yield deadline, launch['line']
    if deadline is not None:
        return
    name = routing._child_name(launch['scope'], launch['node'])
    if name is None:
        return
    for node in ast.walk(launch['scope']):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ('communicate', 'wait'):
            continue
        if ast.unparse(node.func.value) != name:
            continue
        if _in_a_drain(node, parents, ast.unparse(node.func.value)):
            continue
        bound = next((word.value for word in node.keywords
                      if word.arg == 'timeout'), None)
        if isinstance(bound, ast.Constant):
            yield bound, node.lineno


def _retyped_bound_readings(root=None):
    """Every retyped number the composed modules' launches carry."""
    composed = {module_name for module_name, _ in COMPOSED_BOUND_SITES}
    typed = []
    for module_name in sorted(set(routing.CLASSIFYING_MODULES) & composed):
        path = (TESTS if root is None else root) / module_name
        tree = ast.parse(path.read_text(encoding='utf-8'))
        parents = _parents(tree)
        for launch in sweep._launches(tree):
            for bound, where in _retyped_bounds(routing, launch, parents):
                typed.append(f'{module_name}:{where} timeout='
                             f'{ast.unparse(bound)}')
    return typed


def test_a_classifying_module_holds_no_retyped_launch_bound(tmp):
    """The other half of the exemption, which is per-MODULE and says so.

    `CLASSIFYING_MODULES` excuses a module from the routing rule, and the
    exemption the walk enforces on it is that every launch bounds its own
    child. That is a per-LAUNCH test inside a per-MODULE allowance, so a
    bound ADDED to an already-excused module passes it: a fresh
    `subprocess.run(..., timeout=30)` in `tests/_realbrowser.py` satisfied
    it, and was read by nothing else in the tree, which was measured
    rather than argued.

    So the allowance's own blind spot is stated here, and the rule that
    closes it is narrow on purpose: a launch in an exempt module may not
    carry a retyped number. A composed constant is still a Name and still
    passes; only a literal at the call site is refused, which is exactly
    the shape that was invisible.

    It reads BOTH spellings, because they are two spellings of one number:
    a `timeout=` on the launch call, and a `communicate(timeout=…)` or
    `wait(timeout=…)` on the name the launch bound the child to. Reading
    only the first left `Popen`-shaped bounds unread, and a `Popen` is how a
    child is launched wherever the caller wants its pipes.

    It is scoped to the modules whose bound this branch COMPOSED, which is
    five of the eight in `CLASSIFYING_MODULES`, so THREE are out of scope
    in full — the exclusion being the module rather than the instance that
    prompted it. `tests/_realbrowser_workers.py`, whose `browser
    --version` at 15s launches a real browser rather than a Node child, and
    which is this branch's pre-existing debt; and `tests/_dashnode.py` and
    `tests/_overlap.py`, whose bounds this branch never composed. A literal
    added to ANY of the three is not read by this control, and saying so
    is the point.

    What else reads it is narrower than the last two versions of this
    sentence claimed. `tests/test_repo_layout.py`'s `_bound_sites` reports
    a launch there on one condition, and the condition is the whole of it:
    the analyser's own head is `git` or `ambiguous`, or it refused to place
    the call at all (`unplaced`). A head it merely could not READ is
    neither, so a `node = shutil.which('node')` launch is named by the
    analyser and dropped by the gate. Measured with four plants appended at
    EOF so no keyed row moved: `['git', 'status']` was reported as a bound
    launch, and a parameter-headed literal, a `node = 'node'` literal, and
    the `shutil.which` binding were all green.
    """
    del tmp
    assert not _retyped_bound_readings(), (
        'a launch in a module the walk excuses carries a retyped number '
        f'rather than a named one: {_retyped_bound_readings()}')


def test_a_sibling_child_cannot_report_or_certify_this_ones_bound(tmp):
    """The scope of the child-name search, in a real module, both ways.

    Two `Popen`s in one function that bind the same name is the shape the
    module-wide walk could not tell apart, and it is a property of the
    READER rather than of the tree, so the way to settle it is to plant it:
    one child bounded with a retyped `timeout=30`, one not bounded at all,
    beside the real `tests/_realbrowser.py`.

    Reported twice, at the bounded one's line, is the false-positive half —
    a reader sent to code that is right. Reported as neither child being
    unbounded, by the sweep, is the half that loses a site, and it is
    asserted here against the same planted module so the two answers come
    from one plant.
    """
    root = _composed_population(
        Path(tmp) / 'planted', '_realbrowser.py',
        'def _planted_bounded(node):\n'
        '    child = subprocess.Popen([node, "-e", "console.log(1)"])\n'
        '    return child.communicate(timeout=30)\n'
        '\n'
        '\n'
        'def _planted_unbounded(node):\n'
        '    child = subprocess.Popen([node, "-e", "console.log(2)"])\n'
        '    return child.wait()\n')
    planted = (root / '_realbrowser.py').read_text(encoding='utf-8')
    assert planted.count('def _planted_') == 2, (
        'the plant did not reach the real module')
    bounded_line = planted.splitlines().index(
        '    return child.communicate(timeout=30)') + 1
    unbounded_line = planted.splitlines().index(
        '    child = subprocess.Popen([node, "-e", "console.log(2)"])') + 1
    readings = _retyped_bound_readings(root)
    assert readings == [f'_realbrowser.py:{bounded_line} timeout=30'], (
        'a sibling launch reported the bounded child’s own retyped number '
        f'a second time, or the bounded child was missed: {readings}')
    unrouted, unbounded, _, carved, _ = sweep._routing_sweep(root)
    assert not carved, carved
    assert not unrouted, unrouted
    assert unbounded == [f'_realbrowser.py:{unbounded_line}'], (
        'the walk read the bounded sibling’s bound as this child’s: '
        f'{unbounded}')


def test_a_composed_deadline_is_not_below_what_a_child_could_cost(tmp):
    """A table small enough to compose a zero is not a measurement.

    The composition rule reads the algebra DOWNSTREAM of the table, so a
    table nobody measured satisfies it as long as someone did the
    arithmetic: a fabricated `(0.01, 0.02, 0.03)` on `MINIMAL_SPAWN`
    composes a ZERO-second bound, and of the thirteen SUITES that import
    `tests/_realbrowser.py` every one but this file stayed green. What no
    control can check is whether a table is true, and no site here runs
    often enough for a slow bound to show up as a failure — so the one
    property a fabricated table cannot keep is the cheap one it has to
    cross: `round(max(table) * 5)`
    cannot be under a second, because that needs every recorded sample of a
    process launch under 200ms.
    """
    del tmp
    floor = 1 / routing.SITE_HANG_MULTIPLE
    thin = []
    for module_name, stem in COMPOSED_BOUND_SITES:
        constants = _site_constants(module_name)
        figures = [value for value in constants
                   if value.startswith(stem) and value.endswith('_SAMPLES_S')]
        for table in figures:
            slowest = max(_table_values(constants[table]), default=0.0)
            if slowest < floor:
                thin.append(f'{module_name}:{table} slowest={slowest}')
    assert not thin, (
        'a recorded table whose slowest sample is below '
        f'{round(floor, 3)}s, which composes a deadline no child could '
        f'reach: {thin}')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='nodelaunchrouting_')


if __name__ == '__main__':
    raise SystemExit(main())
