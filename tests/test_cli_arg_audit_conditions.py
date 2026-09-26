"""The CONDITIONS_PINNED ledger, checked against the rule's own source.

The ledger is prose until something reads it, and a table nothing reads has
two failures with one symptom: a condition the rule adds with no row, and a
row naming a control that belongs to another condition's half. Deleting the
condition leaves the suite green in both cases, so counting rows tells them
apart no better. This derives the conditions from the guards the rule's own
functions select a refusal with, and for every row removes that condition,
re-imports the rule, and requires a control the row names to go red - which a
row naming another half's control does not.

Every control a row names also runs once with the condition in place, which
is what makes its death under the removal attributable rather than some other
failure. That costs a second run of every control - 3.1s to 8.9s - and it is
the thing to claw back if the suite ever has to be faster: without it a flake,
an environment failure, or an unrelated raise at the top of a control
satisfies its row.

Running this module with ``--reach`` re-derives the figures quoted in
``tests/_cli_arg_audit_conditions.py`` and exits nonzero if any of them has
moved. It is a reporting mode, not a test, and **nothing runs it**: no timed
leg, no coverage leg, no PR check, so a figure that has moved is invisible
until a reader runs the command. The run above is why the mode is opt-in."""
import ast
import contextlib
import hashlib
import importlib
import importlib.util
import re
import shutil
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _cli_arg_audit_conditions as ledger  # noqa: E402
sys.path.insert(0, str(_util.ROOT))

HERE = Path(__file__).resolve().parent
# (file, the module name a reload rebinds, the functions the rule decides in).
# A declared claim, and what defends it is narrower than the list looks:
# selection_shaped_outside_the_scope recognises one shape of an unnamed refusal
# - two or more positional arguments, returning the first by name - and nothing
# for a one-argument helper, a later argument, a tuple, a bool, or a decision
# nested inside a helper. Two refusals in the resolver are in that blind set:
# permitted_namespace_read is the mirror of _subscript_read and decides the
# exemption side, and reflective_builtin_call RETURNS a refusal from inside an
# unscoped function rather than delegating to one. A refusal added to either
# leaves this control and the audit suite both green.
SCOPE = (
    (HERE / '_cli_arg_audit_resolver.py', '_cli_arg_audit_resolver',
     ('frame_read', '_subscript_read', '_call_read',
      'reads_frame_namespace', 'resolve_origin')),
    (HERE / 'test_cli_arg_audit.py', 'test_cli_arg_audit',
     ('_package_roots',)),
)
UNGUARDED = 'return'
# The modules the mutation replaces, and the one the controls run against.
SUBJECT_FILES = ('_cli_arg_audit_resolver.py', '_cli_arg_audit_support.py',
                 'test_cli_arg_audit.py')
SUBJECT_MODULES = ('_cli_arg_audit_resolver', '_cli_arg_audit_support',
                   'test_cli_arg_audit')
ENTRY = 'test_cli_arg_audit'


def _line_starts(source):
    starts, total = [0], 0
    for line in source.splitlines(keepends=True):
        total += len(line)
        starts.append(total)
    return starts


def _first_line(source, span):
    return source.count('\n', 0, span[0]) + 1


def _last_line(source, span):
    return source.count('\n', 0, span[1]) + 1


def _span(starts, node):
    return (starts[node.lineno - 1] + node.col_offset,
            starts[node.end_lineno - 1] + node.end_col_offset)


def _operand_spans(starts, guard):
    """The span deleting each operand of a disjunction or a conjunction.

    Each span ends with the operator joining it to the operand it leaves, so
    the two joiners go with the two operands that leave and the rest stays.
    """
    values = guard.values
    for index, value in enumerate(values):
        if index == 0:
            yield (value, (_span(starts, value)[0],
                           _span(starts, values[1])[0]))
        else:
            yield (value, (_span(starts, values[index - 1])[1],
                           _span(starts, value)[1]))


def _own_returns(statement):
    """The returns written in this statement, not in a callable inside."""
    found = []
    stack = list(ast.iter_child_nodes(statement))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _is_allow(expression):
    return (isinstance(expression, ast.Constant)
            and expression.value is None)


def _refuses(statement):
    """Whether the arm's own return is the refusal, the allow, or neither.

    An arm that does not return, or whose returns disagree, is unclassified and
    its test is taken whole.
    """
    returns = _own_returns(statement)
    if not returns:
        return None
    verdicts = {not _is_allow(node.value)
                for node in returns if node.value is not None}
    if len(verdicts) != 1:
        return None
    return verdicts.pop()


def _splits(guard, refuses):
    """Whether each operand of a compound test is a condition of its own.

    True for a disjunction in front of a refusal - any one operand reaching the
    arm is a refusal on its own, so dropping it drops that refusal - and for a
    conjunction in front of an allow, where every operand must hold to allow,
    so dropping one allows what the arm used to refuse. The other two widen the
    rule when an operand goes, which is not what a row in this ledger claims,
    and a condition whose removal widens the rule is not a condition.
    """
    if not isinstance(guard, ast.BoolOp) or len(guard.values) < 2:
        return False
    if isinstance(guard.op, ast.Or):
        return refuses
    return refuses is False


def _arm_conditions(starts, function, statement):
    refuses = _refuses(statement)
    if _splits(statement.test, refuses):
        for operand, span in _operand_spans(starts, statement.test):
            yield (f'{function.name}|{ast.unparse(operand)}', span, '',
                   operand.lineno)
    else:
        first = statement.test
        if isinstance(first, ast.BoolOp):
            first = first.values[0]
        yield (f'{function.name}|{ast.unparse(first)}',
               _span(starts, statement), '', statement.lineno)
    for node in _own_returns(statement):
        yield from _inline_conditions(starts, function, node)


def _allows(expression):
    """Which branch of an inline conditional is the allow.

    ``return X if P else None`` and ``return None if P else X`` decide the same
    question with the allow in opposite positions, so the direction is read off
    the conditional rather than off the statement's return value, which calls
    both of them a refusal. Two allows or two refusals is not a decision this
    table can read, and is taken whole.
    """
    body_allows = _is_allow(expression.body)
    if body_allows == _is_allow(expression.orelse):
        return None
    return body_allows


def _inline_conditions(starts, function, statement):
    """Yield the decision a return makes inline, as a condition of its own.

    Removing the decision is leaving the allow, so the whole return is what the
    condition takes with it.
    """
    if not isinstance(statement.value, ast.IfExp):
        return
    guard = statement.value.test
    allows = _allows(statement.value)
    if _splits(guard, not allows if allows is not None else None):
        for operand, span in _operand_spans(starts, guard):
            yield (f'{function.name}|{ast.unparse(operand)}', span, '',
                   operand.lineno)
        return
    first = guard.values[0] if isinstance(guard, ast.BoolOp) else guard
    allow = (statement.value.body if allows
             else statement.value.orelse)
    yield (f'{function.name}|{ast.unparse(first)}', _span(starts, statement),
           f'return {ast.unparse(allow)}', guard.lineno)


def _chain(statement):
    node = statement
    while isinstance(node, ast.If):
        yield node
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
        else:
            return


def _conditions(source, names):
    """Yield (key, span, replacement) for every condition in the scope.

    Every arm of an if/elif chain is one however the chain is spelled, because
    an ``elif`` desugars to a nested ``if`` in the ``orelse`` and a refusal a
    keyword hides is one the walk would answer with silence. So is a decision
    a return makes inline.

    Sixteen refusals this walk does not reach, each planted behaviour-neutrally
    in the resolver with both suites green and named here rather than counted.
    One is not a return at all: raised, raised through a helper, asserted. One
    is not a top-level statement of a scoped function: inside a ``for``, a
    ``while``, a ``try``/``except``, a ``try``/``finally``, a ``with``, a
    ``match``, a ``try``'s ``else``, a lambda, a comprehension, a closure. One
    is nested inside an arm's body rather than beside it, and one nested twice.
    The sixteenth is written inside a function the scope does not name - the
    ``SCOPE`` comment names which, and why a refusal *delegated* to one is not
    among them: the call is itself a derived arm.
    """
    starts = _line_starts(source)
    for function in ast.parse(source).body:
        if not isinstance(function, ast.FunctionDef) \
                or function.name not in names:
            continue
        for statement in function.body:
            if isinstance(statement, ast.If) and _own_returns(statement):
                for arm in _chain(statement):
                    if not _own_returns(arm):
                        continue
                    yield from _arm_conditions(starts, function, arm)
            elif isinstance(statement, ast.Return) \
                    and statement.value is not None \
                    and not (isinstance(statement.value, ast.Constant)
                             and statement.value.value is None):
                if isinstance(statement.value, ast.IfExp):
                    yield from _inline_conditions(starts, function, statement)
                else:
                    yield (f'{function.name}|{UNGUARDED}',
                           _span(starts, statement), '', statement.lineno)
            elif isinstance(statement, ast.Expr) \
                    and isinstance(statement.value, ast.Yield) \
                    and statement.value.value is not None:
                yield (f'{function.name}|'
                       f'{ast.unparse(statement.value.value)}',
                       _span(starts, statement), '', statement.lineno)


def rule_conditions(root):
    found = {}
    for path, module, names in SCOPE:
        target = root / path.name
        source = target.read_text(encoding='utf-8')
        for key, span, replacement, line in _conditions(source, names):
            assert key not in found, f'two conditions answer to {key}'
            found[key] = (target, module, source, span, replacement, line)
    return found


def selection_shaped_outside_the_scope():
    """Scoped-file functions of that one shape, outside the scope.

    Returns nothing for a one-argument helper, for one returning a later
    argument, for a tuple or a bool, or for a decision nested inside a helper,
    so a refusal the rule delegates to an unnamed function of those shapes is
    not caught here. The claim is what this recognises, not that the scope
    list is defended.
    """
    unscoped = []
    for path, _module, names in SCOPE:
        for function in ast.parse(path.read_text(encoding='utf-8')).body:
            if not isinstance(function, ast.FunctionDef) \
                    or function.name in names \
                    or len(function.args.args) < 2:
                continue
            first = function.args.args[0].arg
            for node in ast.walk(function):
                if not isinstance(node, ast.Return):
                    continue
                returned = getattr(node.value, 'body', node.value)
                if isinstance(returned, ast.Name) and returned.id == first:
                    unscoped.append(f'{path.name}:{function.name}')
                    break
    return unscoped


def _copied_subject(destination):
    copied = destination / 'mutated'
    copied.mkdir(parents=True)
    for name in SUBJECT_FILES:
        shutil.copyfile(HERE / name, copied / name)
        assert (copied / name).read_bytes() == (HERE / name).read_bytes(), name
    return copied


@contextlib.contextmanager
def _subject_loaded(copied):
    """The copy in place of the rule, and the rule back afterwards.

    What is mutated is the rule's three modules and the real ``daedalus_cli``
    behind them, never a fixture: the copy is byte-identical to the files in
    the tree, which it checks. It is a copy rather than the tree because a
    killed run cannot leave the tree dirty, and because the byte-identity and
    ``__file__`` checks below are what make the mutation demonstrably reach the
    subject rather than a copy of the harness. The real tests directory stays
    on ``sys.path``, so ``_util`` and ``daedalus_cli`` are the real ones; the
    copy's names leave ``sys.modules`` for the length of the block, so its
    imports cannot fall through to the originals.

    That block is unguarded global state: two suites sharing one interpreter
    would break each other, and nothing here takes a lock. ``run_tests.py``
    gives each suite its own subprocess, so nothing this repository runs
    reaches it.
    """
    saved = {name: sys.modules.pop(name, None)
             for name in SUBJECT_MODULES}
    saved_path = sys.path[:]
    try:
        sys.path.insert(0, str(copied))
        spec = importlib.util.spec_from_file_location(
            ENTRY, copied / f'{ENTRY}.py')
        assert spec is not None and spec.loader is not None
        mutated = importlib.util.module_from_spec(spec)
        sys.modules[ENTRY] = mutated
        spec.loader.exec_module(mutated)
        assert Path(mutated.audit_support.resolver.__file__).parent == copied
        yield mutated
    finally:
        sys.path[:] = saved_path
        for name in (*SUBJECT_MODULES, ENTRY):
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in saved.items()
             if module is not None})


# What the ledger's docstring claims about this control's reach, in the form
# the code can check. `--reach` re-measures every one of them and exits
# nonzero on a mismatch; the test checks the identities and that the
# docstring still says each of these numbers.
RECORDED_REACH = {
    'rows': 20, 'controls': 52, 'cells': 1020, 'detected': 249,
    'undetected': 771, 'pairs': 190, 'unnoticed': 91, 'one_direction': 85,
    'both_directions': 14,
}
REACH_RULE = (
    'Domain one: every row against every OTHER control the tree offers - the '
    f"{RECORDED_REACH['controls']} the tree has - a cell is detected when "
    "that control does not survive the row's own condition removal. Domain "
    'two: every unordered pair of rows, exchanging the two named controls; '
    "the exchange is unnoticed when each row's control survives under the "
    "OTHER row's condition.")


def _live_counts(mutated):
    """The row and control counts this tree actually offers."""
    rows = len(ledger.CONDITIONS_PINNED)
    controls = len(_util.collect(vars(mutated))) + len(
        mutated.audit_support.FRAME_NAMESPACE_PLANTS)
    return rows, controls


def _assert_recorded_reach(mutated):
    """The recorded figures still describe this tree, and are still said.

    Cheap by construction: no condition is removed here, because the measured
    half of the claim is re-derived by `--reach`, which is where a figure that
    has moved stops being a warning and becomes a nonzero exit. What the suite
    can afford is checked here, and it is checked first so a stale figure reds
    before the 8.9s of mutation runs.
    """
    reach = RECORDED_REACH
    rows, controls = _live_counts(mutated)
    assert rows == reach['rows'] and controls == reach['controls'], (
        f'the docstring says {reach["rows"]} rows and {reach["controls"]} '
        f'controls; this tree offers {rows} and {controls}')
    assert reach['cells'] == rows * (controls - 1), reach
    assert reach['detected'] + reach['undetected'] == reach['cells'], reach
    assert reach['pairs'] == rows * (rows - 1) // 2, reach
    assert (reach['unnoticed'] + reach['one_direction']
            + reach['both_directions']) == reach['pairs'], reach
    assert reach['both_directions'] + reach['one_direction'] == (
        reach['pairs'] - reach['unnoticed']), reach
    assert _docstring_bindings() == reach, (
        'the ledger docstring publishes figures that are not the recorded '
        f'ones: {_docstring_bindings()}')


def _docstring_bindings():
    """The figures the ledger docstring binds, by value.

    The docstring publishes its reach twice over: the rule in prose, and the
    figures in a binding line this parses. Checking the binding against the
    recorded constants catches a docstring that states the complement -
    "771 detected, 249 not" - which a presence check passes, because both
    numbers appear either way. Each figure appearing exactly once in the whole
    docstring is the second half: a stray figure in prose cannot sit beside
    the binding and disagree with it.
    """
    document = ledger.__doc__ or ''
    bound = {}
    for line in document.splitlines():
        for name, value in re.findall(r'([a-z_]+) (\d+)(?: |$)', line):
            bound[name] = int(value)
    for value in RECORDED_REACH.values():
        assert len(re.findall(rf'(?<![\w.]){value}(?![\w.])',
                              document)) == 1, (
            f'{value} appears more than once in the ledger docstring, so a '
            f'figure in the prose can contradict the binding')
    return bound


def _measure_reach(copied, mutated, tmp):
    """The controls that survive each condition, measured on a fresh copy."""
    conditions = rule_conditions(copied)
    domain = [f'test:{test.__name__}'
              for test in _util.collect(vars(mutated))]
    domain += [f'plant:{plant[0]}'
               for plant in mutated.audit_support.FRAME_NAMESPACE_PLANTS]
    runners = {control: _control(mutated, control, tmp) for control in domain}
    for control, run in runners.items():
        assert not _failed(run), f'{control} fails with nothing removed'
    survived = {}
    for key in sorted(conditions):
        with _condition_removed(key, conditions[key], conditions):
            survived[key] = frozenset(
                control for control, run in runners.items()
                if not _failed(run))
        print(f'  {key[:56]:58} {len(survived[key]):2d} survive', flush=True)
    return domain, survived


def _partition(domain, survived):
    """The measured partition of the row pairs, by REACH_RULE's rule."""
    rows = {key: controls[0]
            for key, _what, controls in ledger.CONDITIONS_PINNED}
    cells = [(r, c) for r in rows for c in domain if c != rows[r]]
    detected = sum(1 for r, c in cells if c not in survived[r])
    unnoticed = one = both = 0
    keys = list(rows)
    for index, a in enumerate(keys):
        for b in keys[index + 1:]:
            survives_a, survives_b = (rows[b] in survived[a],
                                      rows[a] in survived[b])
            unnoticed += survives_a and survives_b
            one += survives_a != survives_b
            both += not (survives_a or survives_b)
    return {'rows': len(rows), 'controls': len(domain), 'cells': len(cells),
            'detected': detected, 'undetected': len(cells) - detected,
            'pairs': len(keys) * (len(keys) - 1) // 2,
            'unnoticed': unnoticed, 'one_direction': one,
            'both_directions': both}


def _report_reach():
    """Print the re-derived figures and fail if any has moved. See --reach."""
    print('The rule, as the ledger docstring states it:\n')
    print(f'  {REACH_RULE}\n')
    with tempfile.TemporaryDirectory() as td:
        copied = _copied_subject(Path(td))
        with _subject_loaded(copied) as mutated:
            domain, survived = _measure_reach(copied, mutated, td)
            measured = _partition(domain, survived)
    print('The partition, measured on a fresh copy of the rule:\n')
    width = max(len(name) for name in measured)
    for name, value in measured.items():
        recorded = RECORDED_REACH[name]
        verdict = 'ok' if recorded == value else f'MOVED (recorded {recorded})'
        print(f'  {name:<{width}}  {value:>5}   {verdict}')
    print()
    moved = sorted(name for name, value in measured.items()
                   if value != RECORDED_REACH[name])
    if moved:
        print(f'THE RECORDED FIGURES NO LONGER DESCRIBE THIS TREE: {moved}',
              file=sys.stderr)
        print("The docstring quotes them; fix the docstring and this "
              "module's RECORDED_REACH together, or re-measure "
              "deliberately.", file=sys.stderr)
        return 1
    print('Every recorded figure matches this tree.')
    return 0


def _failed(run):
    """Whether running a control raises, whatever it raises.

    Not a type filter, and deliberately not one. An AssertionError is the
    EXPECTED failure of a test control detecting a missing refusal, so
    filtering for it would reject genuine deaths while still admitting a
    structural TypeError; and a second assertion in the same test going red is
    an AssertionError too, which such a filter would not have caught.
    Attribution comes from
    where this is called - a control that raises with the condition still in
    place is reported, not counted. One row's control fails structurally rather
    than by assertion - taking the walk's only yield leaves ``_package_roots``
    a non-generator, so every control that walks the package raises TypeError,
    and no control in the tree fails by assertion under that removal.
    """
    try:
        run()
    except Exception:                      # noqa: BLE001
        return True
    return False


@contextlib.contextmanager
def _condition_removed(key, entry, found):
    """The condition taken out of the copied rule, for as long as it is.

    The bytes go back and are compared, and the removed region is re-derived
    from the mutated file before the reload: a mutation that did not apply is a
    green indistinguishable from a control that stopped discriminating.
    """
    path, module, source, span, replacement, _line = entry
    original = path.read_bytes()
    path.write_bytes(
        f'{source[:span[0]]}{replacement}{source[span[1]:]}'.encode())
    try:
        mutated = rule_conditions(path.parent)
        assert key not in mutated, f'the mutation left {key} in place'
        removed = set(range(_first_line(source, span),
                            _last_line(source, span) + 1))
        strayed = sorted(
            name for name in set(found) - set(mutated)
            if found[name][5] not in removed)
        assert not strayed, (
            f'the mutation changed conditions it did not touch: {strayed}')
        importlib.reload(sys.modules[module])
        yield source[span[0]:span[1]]
    finally:
        path.write_bytes(original)
        importlib.reload(sys.modules[module])
    assert path.read_bytes() == original, path
    assert hashlib.sha256(original).hexdigest() == hashlib.sha256(
        path.read_bytes()).hexdigest(), path


def _control(mutated, control, tmp):
    """Return a callable running the named control, or raise it does not name.

    A control identifier that resolves to nothing is a failure, not a skip: a
    row that quietly checks nothing is the shape this whole control exists to
    refuse. That includes a name the audit suite's runner never calls - such a
    callable still dies when the condition is removed, because the runner hands
    it a temp dir where it expects a parser or a tree, so it would satisfy a
    row through an argument shape rather than through the refusal it names.
    """
    kind, _, name = control.partition(':')
    if kind == 'test':
        runnable = {test.__name__ for test in _util.collect(vars(mutated))}
        assert name in runnable, f'no control named {control}'
        return lambda: getattr(mutated, name)(tmp)
    assert kind == 'plant', f'unknown control kind in {control}'
    row = next((plant for plant in mutated.audit_support.FRAME_NAMESPACE_PLANTS
                if plant[0] == name), None)
    assert row is not None, f'no plant row named {control}'
    return lambda: _plant_refused(mutated, *row)


def _plant_refused(mutated, name, prelude, _anchor, replacement, receiver):
    """One plant, through the real package walk, refused once.

    Spelled out rather than delegated to the whole-table helper, so what this
    control reads is the plant and the walk.
    """
    base = (mutated.CLI_PACKAGE / 'commands_eval.py').read_text(
        encoding='utf-8')
    escapes = mutated.package_frame_escapes(
        {'commands_eval': mutated.audit_support.plant_in_reload(
            base, replacement, prelude)})
    assert len(escapes) == 1, (name, escapes)
    assert escapes[0].endswith(f': {receiver}'), (name, escapes)


def test_the_ledger_names_the_control_that_dies_with_its_condition(tmp):
    unpinned = selection_shaped_outside_the_scope()
    assert not unpinned, (
        f'return their first argument, and the ledger scope does not name '
        f'them: {unpinned}')
    rows = {key: controls for key, _what, controls
            in ledger.CONDITIONS_PINNED}
    assert len(rows) == len(ledger.CONDITIONS_PINNED), (
        'the ledger names one condition twice')
    copied = _copied_subject(Path(tmp))
    with _subject_loaded(copied) as mutated:
        _assert_recorded_reach(mutated)
        conditions = rule_conditions(copied)
        unpinned_conditions = sorted(set(conditions) - set(rows))
        stale = sorted(set(rows) - set(conditions))
        assert not unpinned_conditions and not stale, (
            f'conditions with no ledger row: {unpinned_conditions}; '
            f'ledger rows no condition backs: {stale}')
        survivors = []
        for key, named in rows.items():
            assert named, f'row {key} names no control'
            runners = [_control(mutated, control, tmp)
                       for control in named]
            for control, run in zip(named, runners):
                assert not _failed(run), (
                    f'row {key} names {control}, which already fails with '
                    f'the condition in place, so its death under the removal '
                    f'would not be that control seeing the refusal go')
            with _condition_removed(
                    key, conditions[key], conditions) as removed:
                survivors.extend(
                    f'{key} -> {control}\n    removed: {removed.strip()!r}'
                    for control, run in zip(named, runners)
                    if not _failed(run))
    left = sorted(name for name in SUBJECT_MODULES
                  if str(getattr(sys.modules.get(name), '__file__', ''))
                  .startswith(str(copied)))
    assert not left, f'the copy is still bound in sys.modules: {left}'
    assert not survivors, (
        'every control these rows name stayed green with its own condition '
        'removed:\n' + '\n'.join(survivors))


if __name__ == '__main__':
    if '--reach' in sys.argv[1:]:
        sys.exit(_report_reach())
    sys.exit(_util.runner(_util.collect(dict(locals()))))
