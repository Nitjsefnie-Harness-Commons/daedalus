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
# A function outside this list returning its first parameter is a refusal arm
# the ledger cannot see, so the control refuses to start until the list names
# it: a new helper the rule delegates to is what a hand list would drop.
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


def _span(starts, node):
    return (starts[node.lineno - 1] + node.col_offset,
            starts[node.end_lineno - 1] + node.end_col_offset)


def _guard_spans(starts, guard, arm):
    """The span removing one condition from a guard, and its identity.

    Each operand of a top-level ``or`` reaches the arm alone, so each is a
    condition and each is removable alone; a conjunct is not, because dropping
    one widens the arm instead of removing it, so the whole test is the
    condition and is named by its first conjunct. ``arm`` is the whole ``if``
    statement, which is what a condition that is the entire test takes.
    """
    if isinstance(guard, ast.BoolOp) and isinstance(guard.op, ast.Or) \
            and len(guard.values) > 1:
        for index, value in enumerate(guard.values):
            if index == 0:
                begin = _span(starts, value)[0]
                end = _span(starts, guard.values[1])[0]
            else:
                begin = _span(starts, guard.values[index - 1])[1]
                end = _span(starts, value)[1]
            yield (f'{ast.unparse(value)}', (begin, end))
        return
    first = guard.values[0] if isinstance(guard, ast.BoolOp) else guard
    yield (ast.unparse(first), arm)


def _conditions(source, names):
    """Yield (key, span) for every condition the named functions implement."""
    starts = _line_starts(source)
    for function in ast.parse(source).body:
        if not isinstance(function, ast.FunctionDef) \
                or function.name not in names:
            continue
        for statement in function.body:
            if isinstance(statement, ast.If) and any(
                    isinstance(child, ast.Return)
                    for child in ast.walk(statement)):
                for identity, span in _guard_spans(
                        starts, statement.test, _span(starts, statement)):
                    yield (f'{function.name}|{identity}', span)
            elif isinstance(statement, ast.Return) \
                    and statement.value is not None \
                    and not (isinstance(statement.value, ast.Constant)
                             and statement.value.value is None):
                yield (f'{function.name}|{UNGUARDED}',
                       _span(starts, statement))
            elif isinstance(statement, ast.Expr) \
                    and isinstance(statement.value, ast.Yield) \
                    and statement.value.value is not None:
                yield (f'{function.name}|'
                       f'{ast.unparse(statement.value.value)}',
                       _span(starts, statement))


def rule_conditions(root):
    """Return {key: entry} for every condition, from the files under root."""
    found = {}
    for path, module, names in SCOPE:
        target = root / path.name
        source = target.read_text(encoding='utf-8')
        for key, span in _conditions(source, names):
            assert key not in found, f'two conditions answer to {key}'
            found[key] = (target, module, source, span)
    return found


def selection_shaped_outside_the_scope():
    """Functions returning a selection the ledger's scope does not name."""
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
        sys.modules.pop(ENTRY, None)
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
    path, module, source, span = entry
    original = path.read_bytes()
    path.write_bytes(f'{source[:span[0]]}{source[span[1]:]}'.encode())
    try:
        mutated = rule_conditions(path.parent)
        assert key not in mutated, f'the mutation left {key} in place'
        assert len(mutated) == len(found) - 1, (
            f'the mutation took {len(found) - len(mutated)} conditions, '
            f'not the one it was aimed at')
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
        f'return a selection, and the ledger scope does not name them: '
        f'{unpinned}')
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
            runners = [_control(mutated, control, tmp)
                       for control in named]
            with _condition_removed(
                    key, conditions[key], conditions) as removed:
                survivors.extend(
                    f'{key} -> {control}\n    removed: {removed.strip()!r}'
                    for control, run in zip(named, runners)
                    if not _died(run))
    assert not survivors, (
        'every control these rows name stayed green with its own condition '
        'removed:\n' + '\n'.join(survivors))


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
