"""The CONDITIONS_PINNED ledger, checked against the rule's own source.

The ledger is prose until something reads it, and a table nothing reads has
two failures with one symptom: a condition the rule adds with no row, and a
row naming a control that belongs to another condition's half. Deleting the
condition leaves the suite green in both cases, so counting rows tells them
apart no better. This derives the conditions from the guards the rule's own
functions select a refusal with, and for every row removes that condition,
re-imports the rule, and requires a control the row names to go red - which a
row naming another half's control does not."""
import ast
import contextlib
import hashlib
import importlib
import importlib.util
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _cli_arg_audit_conditions as ledger  # noqa: E402
sys.path.insert(0, str(_util.ROOT))

HERE = Path(__file__).resolve().parent
# (file, the module name a reload rebinds, the functions the rule decides in).
# A declared claim, and what defends it is narrower than the list looks:
# selection_shaped_outside_the_scope recognises one shape of an unnamed refusal
# - two or more positional arguments, returning the first by name - and
# nothing for a one-argument helper, a later argument, a tuple, a bool, or a
# decision nested inside a helper.
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
# The copy is registered under the name a reload re-finds it by, and the
# copy's own directory is on sys.path while it is loaded.
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

    Each span runs from the end of the previous operand to the end of its own,
    so the operator joining them goes with the operand that leaves; the first
    operand's span runs to the start of the second.
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
    """The returns written in this statement, not in a callable inside it."""
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

    The direction is what tells a removal from a widening: a guard that
    refuses loses refusals as its test stops holding, and a guard that allows
    loses them as its test stops allowing. An arm that does not return, or
    whose returns disagree, is unclassified and its test is taken whole.
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

    True for a disjunction in front of a refusal - any one operand reaching
    the arm is a refusal on its own, and dropping it drops that refusal - and
    for a conjunction in front of an allow, where every operand has to hold to
    allow and dropping one allows what the arm used to refuse. The other two
    combinations widen the rule when an operand is dropped, which is not what
    a row in this ledger claims.
    """
    if not isinstance(guard, ast.BoolOp) or len(guard.values) < 2:
        return False
    if isinstance(guard.op, ast.Or):
        return refuses
    return refuses is False


def _arm_conditions(starts, function, statement):
    """Yield (key, span, replacement) for one arm of an if/elif chain."""
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
    """Whether an expression is the allow, the refusal, or neither.

    An inline conditional decides by returning one branch or the other, so the
    direction is read off the conditional itself: ``return X if P else None``
    and ``return None if P else X`` make the same decision with the allow in
    opposite positions, and reading it off the statement's return value calls
    both of them a refusal. A conditional with two allows or two refusals is
    not a decision this table can read, and is taken whole.
    """
    body_allows = _is_allow(expression.body)
    if body_allows == _is_allow(expression.orelse):
        return None
    return body_allows


def _inline_conditions(starts, function, statement):
    """Yield the decision a return makes inline, as a condition of its own.

    ``return <one branch> if <test> else <the other>`` decides the same
    question an ``if`` arm does, in the return rather than beside it. Removing
    the decision is leaving the allow, so the return is what the condition
    takes with it - and which branch that is depends on the conditional.
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
    """Yield each arm of an if/elif chain, the first one first."""
    node = statement
    while isinstance(node, ast.If):
        yield node
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
        else:
            return


def _conditions(source, names):
    """Yield (key, span, replacement) for every condition in the scope.

    Every arm of an if/elif chain is one however the chain is spelled: an
    ``elif`` desugars to a nested ``if`` in the ``orelse``, and a refusal a
    keyword can hide is one the walk would answer with silence. A return that
    decides inline carries its own decision. A nested statement inside an arm
    is not descended into: it has its own control flow.
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
    """Return {key: entry} for every condition, from the files under root."""
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

    Recognises a function of two or more positional arguments that returns its
    first argument by name. Returns nothing for a one-argument helper, for one
    returning a later argument, for a tuple or a bool, or for a decision nested
    inside a helper - so a refusal the rule delegates to an unnamed function
    of those shapes is not caught here. The claim is what this recognises, not
    that the scope list is defended.
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
    """The rule's three modules, copied byte for byte, to be mutated."""
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
    the tree, which it checks. It is a copy rather than the tree because
    ``run_tests.py`` starts every suite in one parallel wave, so an in-place
    edit would hand a half-mutated module to whichever suite imports it next.
    The real tests directory stays on ``sys.path``, so ``_util`` and
    ``daedalus_cli`` are the real ones; the copy's names leave ``sys.modules``
    for the length of the block, so its imports cannot fall through to the
    originals.
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


def _died(run):
    try:
        run()
    except Exception:                      # noqa: BLE001
        return True
    return False


@contextlib.contextmanager
def _condition_removed(key, entry, found):
    """The condition taken out of the copied rule, for as long as it is.

    The bytes go back and are compared; the removed region is re-derived from
    the mutated file before the reload and is in the failure message, because
    a mutation that did not apply is a green indistinguishable from a control
    that stopped discriminating - and a green is the only answer this control
    normally produces.
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
    refuse.
    """
    kind, _, name = control.partition(':')
    if kind == 'test':
        target = getattr(mutated, name, None)
        assert callable(target), f'no control named {control}'
        return lambda: target(tmp)
    assert kind == 'plant', f'unknown control kind in {control}'
    row = next((plant for plant in mutated.audit_support.FRAME_NAMESPACE_PLANTS
                if plant[0] == name), None)
    assert row is not None, f'no plant row named {control}'
    return lambda: _plant_refused(mutated, *row)


def _plant_refused(mutated, name, prelude, _anchor, replacement, receiver):
    """One plant, through the real package walk, refused once.

    Spelled out rather than delegated to the whole-table helper, so what this
    control reads is the plant and the walk, not the helper that would report
    a red for some other row.
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
            with _condition_removed(
                    key, conditions[key], conditions) as removed:
                survivors.extend(
                    f'{key} -> {control}\n    removed: {removed.strip()!r}'
                    for control, run in zip(named, runners)
                    if not _died(run))
    left = sorted(name for name in SUBJECT_MODULES
                  if str(getattr(sys.modules.get(name), '__file__', ''))
                  .startswith(str(copied)))
    assert not left, f'the copy is still bound in sys.modules: {left}'
    assert not survivors, (
        'every control these rows name stayed green with its own condition '
        'removed:\n' + '\n'.join(survivors))


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
