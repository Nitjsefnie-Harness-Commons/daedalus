"""Delete one guard arm at a time and report which verdicts move.

The mechanism issue #1144 rests on. A mutation that leaves every row
green is not a finding on its own; what makes it one is a shape whose
verdict the same mutation changes. So this cuts the ONE clause a row of
`tests/_launch_arms.py` names, re-asks every row and every control in a
fresh interpreter over a COPY of the test tree, and reports the labels
that moved. What it never does is decide: the table's state is the
reader's, and this only produces the evidence for it.

The cut is AST-located and the exact text it removed is returned, so a
reader can see which clause the verdict depended on rather than trust
that the right one went. A chain head is the one case where the span
that is cut and the clause that goes differ: the head's `end_lineno`
runs down the whole elif chain, and the promotion re-supplies that
chain, so `removed` stops at the head's own body and the text that took
its place comes back separately in `promoted`. Three shapes of clause
need three ops:

    drop_if    cut an `if`/`elif` arm, promoting a chain head's orelse
    drop_stmt  cut a statement outright
    drop_span  cut a run of statements at one indent
    replace    rewrite a header and keep its body
    boolop     cut one operand of a disjunction, the Nth on that line

Cutting a clause can leave its block empty — an `else:` with no body, a
`for` with no statements — so the cut walks upward until the file parses
again, and the extra lines it had to take come back with the removal.
"""
import ast
import copy
import subprocess
import sys

MAX_UPWARD = 8

# The fresh child's program: it imports the analyser from whatever source
# is in the tree it is handed, and prints one line per verdict.
CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
sys.dont_write_bytecode = True
from _argv_read import ArgvReader
from _bound_site_rows import BOUND_SITE_ROWS
from _launch_arms import ARM_CONTROLS
from _launch_audit import bound_sites, launch_refusals
from _launch_refusal_rows import LAUNCH_REFUSAL_ROWS


def verdict(call, *args):
    try:
        return repr(call(*args))
    except Exception as error:
        return 'RAISED %s: %s' % (type(error).__name__, error)


for label, source, _ in BOUND_SITE_ROWS:
    print('BOUND %s = %s' % (label, verdict(bound_sites, source, label)))
for label, source, _ in LAUNCH_REFUSAL_ROWS:
    print('REFUSE %s = %s' % (label,
                              verdict(launch_refusals, source, label)))
for label, source, _, _ in ARM_CONTROLS:
    print('CONTROL %s = %s || %s' % (
        label, verdict(bound_sites, source, label),
        verdict(launch_refusals, source, label)))
"""


def _offset(lines, upto_line):
    return sum(len(x) for x in lines[:upto_line - 1])


def _parses(source):
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def _locate(tree, line):
    """(node, is_elif) for the statement whose header is on `line`."""
    hits = []

    def walk(node, in_orelse):
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        walk(item, field == 'orelse')
            elif isinstance(value, ast.AST):
                walk(value, in_orelse)
        if getattr(node, 'lineno', None) == line:
            hits.append((node, in_orelse))

    walk(tree, False)
    return hits[-1] if hits else (None, False)


def _disjunction(tree, line):
    """(BoolOp, position, enclosing statement) for an operand on `line`."""
    found = []

    def walk(node, enclosing):
        if isinstance(node, ast.BoolOp):
            for position, operand in enumerate(node.values):
                if operand.lineno <= line <= operand.end_lineno:
                    found.append((node, position, enclosing))
        for child in ast.iter_child_nodes(node):
            walk(child, node if isinstance(node, ast.stmt) else enclosing)

    walk(tree, None)
    return found[0] if found else (None, None, None)


def _promote(lines, node):
    """A chain head's orelse becomes the body; `elif` becomes `if`."""
    orelse = node.orelse
    if not orelse:
        return ''
    rows = lines[orelse[0].lineno - 1:orelse[-1].end_lineno]
    # The indent is the FIRST line's: `str.lstrip()` on a multi-line block
    # eats the second line's indent too, so the two differ.
    shift = node.col_offset - (len(rows[0]) - len(rows[0].lstrip()))
    if shift > 0:
        rows[0] = ' ' * shift + rows[0].lstrip()
    elif shift < 0:
        rows = [row[-shift:] for row in rows]
    if rows[0].lstrip().startswith('elif'):
        rows[0] = rows[0].replace('elif', 'if', 1)
    return ''.join(rows)


def _drop_operand(source, lines, tree, line, index):
    """Re-render the enclosing statement without one operand.

    A text cut is wrong here: a disjunction spread over several lines
    leaves a leading `or` when its first operand goes, so the statement
    is re-rendered from the tree with the operand removed and that
    replaces the statement's own span.
    """
    node, _, enclosing = _disjunction(tree, line)
    if node is None or enclosing is None:
        raise ValueError(f'no BoolOp operand on line {line}')
    on_line = [i for i, operand in enumerate(node.values)
               if operand.lineno <= line <= operand.end_lineno]
    position = on_line[index]
    clone = copy.deepcopy(enclosing)

    def strip(statement):
        if isinstance(statement, ast.BoolOp) and len(statement.values) > 1:
            del statement.values[position]
            return True
        return any(strip(child) for child in ast.iter_child_nodes(statement)
                   if isinstance(child, ast.AST))

    if not strip(clone):
        raise ValueError(f'operand {position} not found on re-render')
    start, end = enclosing.lineno, enclosing.end_lineno
    removed = ''.join(lines[start - 1:end])
    cut = (_offset(lines, start), _offset(lines, end + 1))
    text = ast.unparse(clone).splitlines(keepends=True)
    if not text[-1].endswith('\n'):
        text[-1] += '\n'
    # `ast.unparse` re-indents from column zero, so the whole block moves
    # to the statement's own column, body lines included.
    pad = ' ' * enclosing.col_offset
    text = [pad + row for row in text]
    return source[:cut[0]] + ''.join(text) + source[cut[1]:], removed


def _end_of(node, line):
    """The node's end line, refused rather than guessed when absent.

    Every node here is parsed out of a real source, so it carries one.
    """
    if node.end_lineno is None:
        raise ValueError(f'no end for the statement on line {line}')
    return node.end_lineno


def cut_arm(source, spec):
    """`(mutated, removed, promoted)` for a `cut` field of the table."""
    parts = spec.split(':')
    op, line = parts[0], int(parts[1])
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    if op == 'boolop':
        mutated, removed = _drop_operand(source, lines, tree, line,
                                         int(parts[2]))
        return mutated, removed, ''
    node, is_elif = _locate(tree, line)
    if node is None:
        raise ValueError(f'no statement on line {line}')
    promoted = None
    if op == 'replace':
        # A replacement rewrites the header and keeps the body: a
        # `while <guard>:` becomes `while True:` with its body intact.
        start = line
        end = (node.body[0].lineno - 1 if getattr(node, 'body', None)
               else _end_of(node, line))
        new = ' ' * node.col_offset + ':'.join(parts[2:]) + '\n'
    elif is_elif and isinstance(node, ast.If):
        # An `elif` is nested in the head's orelse, so its own end_lineno
        # runs to the end of the whole chain. The arm is the header plus
        # its own body; the chain continues.
        start, end, new = line, _end_of(node.body[-1], line), ''
    else:
        end = int(parts[2]) if op == 'drop_span' and len(parts) > 2 \
            else _end_of(node, line)
        start = line
        if op in ('drop_stmt', 'drop_span'):
            new = ''
        elif op == 'drop_if':
            new = _promote(lines, node)
            if new:
                promoted = node
        else:
            raise ValueError(f'unknown cut op {op}')
    for extra in range(0, MAX_UPWARD + 1):
        cut = (_offset(lines, start - extra), _offset(lines, end + 1))
        candidate = source[:cut[0]] + new + source[cut[1]:]
        if _parses(candidate):
            return (candidate, _removed(lines, start, extra, end, promoted),
                    new)
    raise ValueError(f'no parsing cut for line {line}')


def cut_span(source, spec):
    """`(first line, last line)` of the clause a `cut` spec removes, or None.

    The line a spec names is where its node STARTS; the clause is that
    node's span. For a `boolop` the node is the disjunction's ENCLOSING
    statement, because the operand is re-rendered into it and the
    statement is what goes — so an entry on one operand of a
    multi-line condition is inside its cut's span, not at its edge.
    """
    op, line = spec.split(':')[0], int(spec.split(':')[1])
    tree = ast.parse(source)
    if op == 'boolop':
        _, _, node = _disjunction(tree, line)
    else:
        node, _ = _locate(tree, line)
    return (node.lineno, node.end_lineno) if node is not None else None


def _removed(lines, start, extra, end, promoted):
    """The clause that went, which is not always the span that was cut.

    A chain head's `end_lineno` runs down the whole elif chain, and the
    promotion re-supplies that chain, so reporting the cut span would
    name the elif arm the reader can still see in the file as deleted.
    `promoted` is set only for a chain head whose orelse was promoted, and
    the reported text stops at the end of the head's OWN body.
    """
    if promoted:
        return ''.join(
            lines[start - extra - 1:_end_of(promoted.body[-1], start)])
    return ''.join(lines[start - extra - 1:end])


RAISED = 'RAISED '


def _crashed(baseline, after):
    """The labels a mutation made RAISE, that were not raising before.

    A crash is a control, but a weaker one than a changed value: it says
    the analyser must not raise, not that the arm's own clause decided
    the answer. A robustness change can satisfy the first while the
    second goes unpinned, so the two are counted apart.
    """
    return sorted(key.split(' ', 1)[1] for key in after
                  if after[key].startswith(RAISED)
                  and not baseline.get(key, '').startswith(RAISED))


def _child_verdicts(tests_dir):
    """The child's verdict table, or None when it did not answer.

    A mutant whose fixpoint does not stop is a FINDING, so the child is
    bounded and the timeout is reported rather than raised: a sweep that
    dies on the first non-terminating mutant never reaches the ones
    after it.
    """
    try:
        result = subprocess.run(
            [sys.executable, '-B', '-S', '-c', CHILD, str(tests_dir)],
            capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    table = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(' = ')
        table[key] = value
    return table


def arm_sweep(tmp, arms):
    """`{arm: {'removed', 'promoted', 'moved', 'crash', 'timed_out',
    'refused'}}`.

    `arms` is read from the caller rather than imported, so a suite that
    narrows the sweep to two arms gets exactly those two. The mutation
    lands in a COPY of the test tree (`_owned_writes.copy_test_tree`),
    never in the checkout, and a fresh interpreter reads it, so no stale
    bytecode and no half-restored file can be mistaken for a verdict.

    `crash` is the subset of `moved` that moved by RAISING rather than to
    another value, because a reader must be able to tell the two apart.
    `timed_out` is a child that did not answer at all, which is neither:
    it moves nothing and proves nothing, so every consumer has to reject
    it rather than read the empty `moved` as an answer. `refused` is a
    cut the analyser could not be asked to make: a spec whose line no
    longer names its clause lands on whatever is there, and that node
    decides whether the walk refuses, raises, or succeeds on the wrong
    clause -- all three a finding naming the arm and the spec.
    """
    from _owned_writes import clear_bytecode, copy_test_tree

    root = tmp / 'arm-sweep'
    copy_test_tree(root)
    target = root / 'tests'
    baseline = _child_verdicts(target)
    assert baseline is not None, 'the unmutated child did not answer'
    findings = {}
    for arm_id, file_name, arm_line, spec, _, _, _, evidence in arms:
        path = target / file_name
        original = path.read_text(encoding='utf-8')
        try:
            mutated, removed, promoted = cut_arm(original, spec)
        # Which node a mis-keyed line lands on decides how the cut fails
        # and the shape is not enumerable, so the refusal names the type.
        except Exception as error:  # pylint: disable=broad-except
            findings[arm_id] = {
                'removed': '', 'promoted': '', 'moved': [], 'crash': [],
                'refused': (f'{file_name}:{arm_line} {arm_id} {spec}: '
                            f'{type(error).__name__}: {error}')}
            continue
        path.write_text(mutated, encoding='utf-8')
        clear_bytecode(target)
        after = _child_verdicts(target)
        path.write_text(original, encoding='utf-8')
        clear_bytecode(target)
        if after is None:
            findings[arm_id] = {'removed': removed, 'promoted': promoted,
                                'moved': [], 'crash': [],
                                'timed_out': True}
            continue
        moved = sorted(key.split(' ', 1)[1] for key in after
                       if after[key] != baseline[key])
        findings[arm_id] = {'removed': removed, 'promoted': promoted,
                            'moved': moved, 'crash': _crashed(baseline, after),
                            'evidence': evidence}
    return findings
