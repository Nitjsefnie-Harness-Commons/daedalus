#!/usr/bin/env python3
"""The rule the launch-routing walk enforces over the real tree.

The walk itself, and the tables that close its population, live in
`tests/_node_launch_routing.py` — a shared helper, because the plant-based
controls in `tests/test_node_launch_routing_shapes.py` need the same
decisions and a sibling SUITE import is a seam this repository refuses
(`tests/test_suite_import_boundaries.py`).

What is here is the one control that runs the walk over every tracked
module and asks the question the branch exists to keep true: is a Node
child whose cost is a fixed unit of work launched through the shared hang
detector, and does every launch the walk cannot classify appear in a table
that says why.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _launch_path import (  # noqa: E402
    _module_constants as module_constants)
from _node_launch_routing import _routing_sweep  # noqa: E402


def test_every_fixed_work_node_child_goes_through_the_shared_detector(tmp):
    """The rule, read off the tree rather than off a list of sites.

    One findings list and one assert over all four classes, so a run reports
    every class it found rather than the first. An early assert here is how
    a plant in a later class hides behind one caught by an earlier one.
    """
    del tmp
    unrouted, unbounded, unclassified, unused = _routing_sweep()
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


def _site_constants(module_name, stem):
    """A module's module-level bindings as source, read by the shared reader.

    `tests/_launch_path.py`'s own, not a second copy of it: a helper defined
    once in a shared module and imported by every user is the whole point of
    the rule that refuses a re-implementation.
    """
    source = Path(__file__).resolve().parent / module_name
    return {name: ast.unparse(value)
            for name, value in module_constants(
                ast.parse(source.read_text(encoding='utf-8'))).items()}


def _table_values(source):
    """The floats in a recorded sample table, read from its own source."""
    return [node.value for node in ast.walk(ast.parse('X = ' + source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)]


def _is_composed(constants, stem):
    """Whether a stem's deadline is `round()`ed from a table and the multiple.

    Composed means all three: a recorded table of samples, a `max()` taken
    over it, and a deadline rounded from that by the shared multiple. A
    number written at the call site is none of them.
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
    return has(f'{stem}_DEADLINE_S') and constants[f'{stem}_DEADLINE_S'] == (
        f'round({stem}_SLOWEST_S * SITE_HANG_MULTIPLE)')


def test_a_call_site_bound_is_derived_and_not_written(tmp):
    """The eight composed bounds, refused if any is a retyped number.

    A site that keeps its bound at its own call site is deliberately OUTSIDE
    `tests/_launch_census.py`'s audited path — that is what holds the path at
    57 modules rather than 76 — and the cost of that is this: the census's
    rule that a bound's figure must be COMPOSED rather than written reaches
    only path modules, so a bare `10` retyped at one of these call sites was
    read by nothing in the tree. Every control in the repository stayed
    green against that mutation, which is the observation this answers.

    The negative half is planted because a rule that cannot tell a composed
    figure from a typed one is the false green this branch exists to remove.
    """
    del tmp
    typed = []
    for module_name, stem in COMPOSED_BOUND_SITES:
        source = Path(__file__).resolve().parent / module_name
        tree = ast.parse(source.read_text(encoding='utf-8'))
        constants = {name: ast.unparse(value) for name, value
                     in module_constants(tree).items()}
        if not _is_composed(constants, stem):
            typed.append(f'{module_name}:{stem}')
    assert not typed, (
        'a call-site bound that is a retyped number rather than a figure '
        f'composed from its recorded table: {typed}')
    composed = {'GM_CHILD_SAMPLES_S': '(1.0, 2.0, 3.0)',
                'GM_CHILD_SLOWEST_S': 'max(*GM_CHILD_SAMPLES_S)',
                'GM_CHILD_DEADLINE_S': 'round(GM_CHILD_SLOWEST_S * '
                                       'SITE_HANG_MULTIPLE)'}
    assert _is_composed(composed, 'GM_CHILD')
    for broken, why in (
            ({**composed, 'GM_CHILD_DEADLINE_S': '90'},
             'the deadline is a retyped number'),
            ({**composed, 'GM_CHILD_SLOWEST_S': '3.0'},
             'the slowest sample is not taken from the table'),
            ({key: value for key, value in composed.items()
             if not key.endswith('_SAMPLES_S')},
             'the recorded table is gone')):
        assert not _is_composed(broken, 'GM_CHILD'), why


def _retyped_bounds(routing, tree, launch, parents):
    """Every retyped number that bounds this one child, with its line.

    Two shapes, read the way the module's own `_bounds_its_own_child` reads
    them so the two cannot disagree: the `timeout=` on the launch itself, and
    a `communicate(timeout=…)` or `wait(timeout=…)` on the name the launch
    bound the child to. A wait inside an expiry handler is skipped, because
    that is the cleanup and not the bound.
    """
    deadline = launch['deadline']
    if isinstance(deadline, ast.Constant):
        yield deadline, launch['line']
    if deadline is not None or '_bounds' not in dir(routing):
        return
    name = routing._child_name(tree, launch['node'])
    if name is None:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ('communicate', 'wait'):
            continue
        if ast.unparse(node.func.value) != name:
            continue
        if routing._inside_expiry_handler(node, parents):
            continue
        bound = next((word.value for word in node.keywords
                      if word.arg == 'timeout'), None)
        if isinstance(bound, ast.Constant):
            yield bound, node.lineno


def test_a_classifying_module_holds_no_retyped_launch_bound(tmp):
    """The other half of the exemption, which is per-MODULE and says so.

    `CLASSIFYING_MODULES` excuses a module from the routing rule, and the
    exemption the walk enforces on it is that every launch bounds its own
    child. That is a per-LAUNCH test inside a per-MODULE allowance, so a
    bound ADDED to an already-excused module passes it: a fresh
    `subprocess.run(..., timeout=30)` in `tests/_realbrowser.py` is read by
    nothing in the tree, which was measured rather than argued.

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

    It is scoped to the modules whose bound this branch COMPOSED. One module
    is out of scope in full, and the exclusion is the module rather than an
    instance: `tests/_realbrowser_workers.py`, whose `browser --version` at
    15s launches a real browser rather than a Node child, and which is this
    branch's pre-existing debt. Nothing added to that module is read here or
    anywhere else, and that is stated so the boundary is not assumed.
    """
    import _node_launch_routing as routing
    del tmp
    composed = {module_name for module_name, _ in COMPOSED_BOUND_SITES}
    typed = []
    for module_name in sorted(set(routing.CLASSIFYING_MODULES) & composed):
        path = Path(__file__).resolve().parent / module_name
        tree = ast.parse(path.read_text(encoding='utf-8'))
        parents = routing._parents(tree)
        for launch in routing._launches(tree):
            for bound, where in _retyped_bounds(routing, tree, launch,
                                                parents):
                typed.append(f'{module_name}:{where} timeout='
                             f'{ast.unparse(bound)}')
    assert not typed, (
        'a launch in a module the walk excuses carries a retyped number '
        f'rather than a named one: {typed}')


def test_a_composed_deadline_is_not_below_what_a_child_could_cost(tmp):
    """A table small enough to compose a zero is not a measurement.

    The composition rule reads the algebra DOWNSTREAM of the table, so a
    table nobody measured satisfies it as long as someone did the
    arithmetic: planting a fabricated table on `MINIMAL_SPAWN` composed a
    ZERO-second bound and left 96 tests green across six suites. What no
    control can check is whether a table is true, and no site here runs
    often enough for a slow bound to show up as a failure — so the one
    property a fabricated table cannot keep is the cheap one it has to
    cross: `round(max(table) * 5)` cannot be under a second, because that
    needs every recorded sample of a process launch under 200ms.
    """
    import _node_launch_routing as routing
    del tmp
    floor = 1 / routing.SITE_HANG_MULTIPLE
    thin = []
    for module_name, stem in COMPOSED_BOUND_SITES:
        constants = _site_constants(module_name, stem)
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
