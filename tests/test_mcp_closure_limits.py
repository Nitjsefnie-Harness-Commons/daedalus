#!/usr/bin/env python3
"""Import-closure shapes the real `daedalus_mcp` tree cannot show.

The refusal pass in `test_mcp_tools.py` walks the real tree, so it can only
witness a closure property the tree actually presents. Each shape below is
one it does not present, driven on a synthetic composition instead, and the
arms of the walk are enumerated in `test_mcp_import_refusals.py`. The
lambda case stays because dropping the lambda arm from `static_value` reds
it alone — nothing else on this tree catches a call's callee being read as
a VALUE. The settled-callee families and their bounds are the callee-value
question at the five forms issue 1213 records.
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_code_eval  # noqa: E402
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402
from _mcp_import_fixtures import _scan_verdict, _write_tree  # noqa: E402


def _composition_names(_tmp, tree):
    """The repo-local files this composition's scan set names, relative to
    the tree it was written into."""
    _write_tree(Path(_tmp), tree)
    scanned = _mcp_import_closure.composition_scan_set(
        Path(_tmp) / 'composition.py', _tmp)
    return {path.relative_to(Path(_tmp)).as_posix() for path in scanned}


def test_a_nullary_lambda_callee_of_the_operation_resolves_the_module(_tmp):
    """A call's callee is a VALUE, and `(lambda: op)()` produces the
    operation, so the module resolves exactly as the direct spelling does.

    The near miss is a lambda the SAME call cannot fill: a lambda with a
    required parameter raises `TypeError` before it produces anything, so
    it resolves nothing and refuses nothing. The zero-argument CALL after
    the lambda is what makes it that arm — without it the row would test "a
    lambda read in place", which the walk answers by the call-result limit
    instead.
    """
    for callee in ('(lambda: importlib.import_module)()',
                   '(lambda *a: importlib.import_module)()',
                   '(lambda a=0: importlib.import_module)()',
                   '(lambda **k: importlib.import_module)()'):
        assert 'pkg/leaf.py' in _composition_names(_tmp, {
            'composition.py': '\nimport importlib\n'
                              '\n\ndef load():\n'
                              f'    return {callee}("pkg.leaf")\n',
            'pkg/__init__.py': '',
            'pkg/leaf.py': 'leaf = True\n'}), callee
    assert _composition_names(_tmp, {
        'composition.py': '\nimport importlib\n'
                          '\n\ndef load():\n'
                          '    return (lambda a: importlib.import_module)()'
                          '("pkg.leaf")\n',
        'pkg/__init__.py': '',
        'pkg/leaf.py': 'leaf = True\n'}) == {'composition.py'}


def test_scan_set_walks_a_deep_subscript_chain_in_linear_cost(_tmp):
    """A depth-20 chain costs at most a small constant times a depth-10
    one; re-asking a subtree's verdict twice per level scores x2 here, in
    whichever arm the descent runs — subscript nesting, nested calls, or
    the one-tuple store's fall-through. Each depth's set is asserted, so
    the composition alone must be the answer at every depth, not only the
    last one's."""
    real_scan, real_element = (_mcp_code_eval._scan,
                               _mcp_code_eval._element_node)
    tally, counts, names = [0], {}, {}

    def surrogate(real):
        def counting(*args):
            tally[0] += 1
            return real(*args)
        return counting

    def subscript(depth):
        return ('\nimport importlib\n\n\ndef load():\n'
                '    return [[importlib.import_module]]'
                f'{"[0]" * (depth - 1)}("pkg.leaf")\n')

    def calls(depth):
        return ('\nimport importlib\n\n\ndef load():\n'
                f'    return {"f(" * depth}importlib.import_module'
                f'{")" * depth}("pkg.leaf")\n')

    def tuples(depth):
        return ('\n\n\ndef load():\n'
                f'    x = {"(" * depth}0{",)" * depth}\n')

    shapes = {'subscript': subscript, 'call': calls,
              'fall-through': tuples}
    with mock.patch.multiple(
            _mcp_code_eval, _scan=surrogate(real_scan),
            _element_node=surrogate(real_element)):
        for label, build in shapes.items():
            for depth in (10, 20):
                tally[0] = 0
                names[label, depth] = _composition_names(
                    _tmp, {'composition.py': build(depth)})
                counts[label, depth] = tally[0]
    for label in shapes:
        assert counts[label, 20] <= 3 * counts[label, 10], counts
        assert names[label, 10] == names[label, 20] == {'composition.py'}, \
            names


# The five callee families issue 1213 records: each refused at base while
# the runtime imports the leaf. Where a wrong choice could still pass, the
# driven values differ (condition runs both routes; defaulted position is
# 1, not 0).
_LEAF = {'pkg/__init__.py': '', 'pkg/leaf.py': 'leaf = True\n'}
SETTLED_CALLEES = (
    ('a conditional the default decides, truthy route',
     '\nimport importlib\n\n\ndef load(c=True):\n'
     '    return ([importlib.import_module][0] if c else print)'
     '("pkg.leaf")\n'),
    ('a conditional the default decides, falsy route',
     '\n\ndef load(c=False):\n    return (0 if c else __import__)'
     '("pkg.leaf")\n'),
    ('a boolean whose left operand is the operation',
     '\n\ndef load():\n    return ([__import__][0] or print)("pkg.leaf")\n'),
    ('a boolean that falls through to the operation',
     '\n\ndef load():\n    return (0 or __import__)("pkg.leaf")\n'),
    ('an `and` that falls through to the operation',
     '\n\ndef load():\n    return (1 and __import__)("pkg.leaf")\n'),
    ('a projection read through a defaulted position',
     '\n\ndef load(i=1):\n'
     '    return getattr([print, __import__][i], "__call__")("pkg.leaf")\n'),
    ('a display carrying a `**` merge',
     '\n\ndef load():\n    return ({**{0: 1}, "a": __import__}["a"])'
     '("pkg.leaf")\n'),
    ('a field-less f-string key',
     '\n\ndef load():\n    return ({f"a": __import__}[f"a"])("pkg.leaf")\n'),
)

# The same widening's negative space. The first two rows are the direction
# the short-circuit and last-wins rules take AWAY from the operation, so an
# over-wide arm that reads the other operand or entry first loses them; the
# rest are the raise positions the runtime settles to a KeyError or a
# ZeroDivisionError, which name nothing and never enter the set either way.
BOUNDS = (
    ('a boolean that short-circuits away from the operation',
     '\n\ndef load():\n    return ([__import__][0] and print)("pkg.leaf")\n'),
    ('a merge whose repeated key the last entry replaces',
     '\n\ndef load():\n'
     '    return ({"a": __import__, **{"a": print}}["a"])("pkg.leaf")\n'),
    ('a merge display that does not carry the key',
     '\n\ndef load():\n    return ({**{0: 1}, "a": __import__}["b"])'
     '("pkg.leaf")\n'),
    ('a merge whose carried display raises on its own key',
     '\n\ndef load():\n'
     '    return ({**{1 // 0: 2}, "a": __import__}["a"])("pkg.leaf")\n'),
    ('a store that takes the defaulted parameter back',
     '\n\ndef load(c=True):\n    c = False\n'
     '    return ([__import__][0] if c else print)("pkg.leaf")\n'),
)

# The genuinely-undecidable forms beside them: a callee the fold cannot
# decide still mentions the operation, and that refusal is the fail-closed
# default the widening must not soften.
UNDECIDED_CALLEES = (
    ('a conditional whose condition does not settle',
     '\n\ndef load(c):\n'
     '    return ([__import__][0] if c else print)("pkg.leaf")\n'),
    ('a boolean whose left operand declines',
     '\n\ndef load(i):\n    return ([__import__][i] or print)("pkg.leaf")\n'),
    ('an index whose key the display cannot be asked',
     '\n\ndef load(k):\n    return ({"a": __import__}[k])("pkg.leaf")\n'),
    ('a projection whose key does not fold to `__call__`',
     '\n\ndef load(k):\n'
     '    return getattr([__import__][0], k)("pkg.leaf")\n'),
)


def test_the_settled_callee_families_resolve_their_module(_tmp):
    """Each family the issue names resolves the leaf it reaches at runtime.

    The leaf in the scan set is the only witness: a resolution is told
    apart from a silence by it. The failures are collected, because the
    five families are five arms of one fold and a plant that silences two
    of them has to name both.
    """
    wrong = []
    for label, source in SETTLED_CALLEES:
        if 'pkg/leaf.py' not in _composition_names(
                _tmp, {'composition.py': source, **_LEAF}):
            wrong.append(label)
    assert not wrong, f'still refused: {"; ".join(wrong)}'


def test_the_short_circuit_and_raise_positions_keep_the_leaf_out(_tmp):
    """The widening's bound: no raise position and no short-circuit away
    from the operation puts the leaf in the set. A raise position names
    nothing, so the leaf's absence is the witness on both sides of the fix;
    a boolean that hands out the WRONG operand is the over-wide mutant of
    the family above.
    """
    wrong = []
    for label, source in BOUNDS:
        verdict, detail = _scan_verdict(
            _tmp, {'composition.py': source, **_LEAF})
        if 'pkg/leaf.py' in detail:
            wrong.append(f'{label}: {verdict}')
    assert not wrong, f'leaf reached: {"; ".join(wrong)}'


def test_the_undecided_callees_stay_refused(_tmp):
    """A callee the fold cannot decide still mentions the operation, and
    that refusal is the fail-closed default this widening is bounded by.
    Each row is read against the arm it enters, so a rule that settles an
    undecided condition, an unread operand, an unread key or an unread
    projection loses the row named for it.
    """
    wrong = []
    for label, source in UNDECIDED_CALLEES:
        verdict, detail = _scan_verdict(
            _tmp, {'composition.py': source, **_LEAF})
        if verdict != 'refused' \
                or 'reaches the import-by-name operation' not in detail:
            wrong.append(f'{label}: {verdict}: {detail}')
    assert not wrong, '; '.join(wrong)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
