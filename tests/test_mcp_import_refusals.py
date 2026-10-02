#!/usr/bin/env python3
"""Every refusal arm the import-closure walk has, and a case per arm.

THE MARKER IS A REFUSAL THE ANALYSERS SPELL, not a test name and not a node
type. A site is an arm when it hands `_refuse` (or raises directly) a
DETAIL it spells itself: a string literal, or an f-string whose holes are
interpolations of a name the caller already holds. The pass-through sites
that forward somebody else's detail — `refuse(node, detail)` inside a
lambda, `_refuse(path, root, node, detail)` — spell none and are wiring,
not arms. A reader re-derives the set with the case at the foot of this
file, which parses both analysers and prints the arms it found, so the
table below and the analysers cannot drift apart quietly: a site either
analyser gains without a row here fails that case.

Two details are shaped so a plain grep of the analysers under-counts them,
and the derivation reads both shapes rather than one:

- `_refuse_default` names the thing it hands away through `_HIDDEN_NOUN`,
  a VARIABLE, so its one call site spells three refusals — the operation,
  the registry and a code-evaluating builtin. The table carries three rows
  over that one site, and the derivation counts the site once.
- `yields_the_operation` and `_yields_the_registry` refuse a mention that
  is a FORMAT of the node rather than of the text, so the phrase in the
  table is the fixed part of each message and never the node's own
  spelling.

Each arm is stated twice: the refusal, and a NEAR MISS the walk must leave
alone. A near miss is the same shape with the one decision that separates
the two arms taken the other way, so a rule that over-reaches loses as
visibly as one that under-reaches. An arm with no near miss says so in its
row rather than borrowing a weak one.

The dead-code barrier kinds are arms of the same question asked of
POSITIONS: each kind is what makes the tail behind it contribute nothing,
so each is carried with the near miss that keeps the case a walk does not
take. `test_mcp_tools.py` carries the real-tree walk and the three limits
it cannot exercise; this suite carries the arms.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402
from _mcp_import_fixtures import (  # noqa: E402
    _assert_scan_refusal, _callee_scan, _scan_verdict, _write_tree)

ROOT = _util.ROOT

# The analyser modules the walk is built from, and the tree the floor
# scans, named so the derivation below reads the analysers rather than a
# list of the refusals this suite happens to know about.
CLOSURE = ROOT / 'tests' / '_mcp_import_closure.py'
GUARD_FLOOR = ROOT / 'tests' / '_mcp_guard_floor.py'

# (label, the analyser site that spells it, the fixed part of its message,
# the site the refusal names, the composition the arm refuses). The site is
# a line in the composition source; the leading newline in every source
# below puts the first real line on line 2, and each refusal line is
# counted, not guessed.
ARMS = (
    ('a store binds the import-by-name operation to a name',
     '_alias', 'binds the import-by-name operation to a name this scan',
     6, '\nimport importlib\n\n\ndef load():\n'
        '    leak = importlib.import_module\n'),
    ('a store binds the module registry to a name',
     '_registry_alias', 'binds the module registry to a name this scan',
     6, '\nimport sys\n\n\ndef load():\n    leak = sys\n'),
    ('a store binds a code-evaluating builtin to a name',
     '_code_eval_alias', 'binds a code-evaluating builtin to a name this',
     5, '\n\n\ndef load():\n    leak = eval\n'),
    ('a store rebinds a name the map tracks',
     '_rebind', "rebinds 'importlib', which this scan maps to the",
     3, '\nimport importlib\nimportlib = print\n'),
    ('a parameter default hands away the operation',
     '_refuse_default',
     'parameter op=importlib.import_module binds the import-by-name',
     5, '\nimport importlib\n\n\ndef load(op=importlib.import_module):\n'
        '    return op\n'),
    ('a parameter default hands away the registry',
     '_refuse_default', 'parameter op=sys binds the module registry to',
     5, '\nimport sys\n\n\ndef load(op=sys):\n    return op\n'),
    ('a parameter default hands away a code-evaluating builtin',
     '_refuse_default', 'parameter op=eval binds a code-evaluating',
     4, '\n\n\ndef load(op=eval):\n    return op\n'),
    ('a string names the import-by-name operation',
     '_refused_string_reads',
     "the string 'import_module' names the import-by-name operation",
     2, '\nSPELLING = "import_module"\n'),
    ('a module is read out of the registry',
     '_refused_string_reads', 'a module is read out of the registry by',
     6, '\nimport sys\n\n\ndef load():\n'
        '    return sys.modules["pkg.leaf"]\n'),
    ('a star import binds names the map cannot hold',
     '_import_targets', 'a star import binds names this scan cannot',
     2, '\nfrom os import *\n'),
    ('the operation is called with a name the walk cannot read',
     '_import_targets', 'is called with a name this scan cannot read',
     6, '\nimport importlib\n\n\ndef load(name):\n'
        '    return importlib.import_module(name)\n'),
    ('a callee reaches the operation through a value it cannot resolve',
     '_import_targets', 'reaches the import-by-name operation through a',
     6, '\nimport importlib\n\n\ndef load(i):\n'
        '    return (0, importlib.import_module)[i]("pkg.leaf")\n'),
    ('a lookup can hand out the operation',
     '_import_targets', 'can hand out the import-by-name operation through',
     6, '\nimport importlib\n\n\ndef load():\n'
        '    return getattr(importlib, "import_module")\n'),
    # No near miss: this arm refuses EVERY constant program a code-evaluating
    # builtin is handed, whatever it names, because the walk resolves the
    # program rather than reading it. Its boundary is the delivery axis
    # above (a builtin bound to a tool stays silent), not the program's
    # text, so a weaker row here would pin nothing.
    ('a constant program is handed to a code-evaluating builtin',
     '_import_targets', 'is handed to a code-evaluating builtin, which',
     5, '\n\n\ndef load():\n    return eval("importlib")\n'),
)

# (label, the arm it sits beside, the composition the walk must leave
# alone). The second column is the assertion, not decoration: it says which
# arm's near miss this is, so a row cannot be read as covering an arm the
# test never named.
NEAR_MISSES = (
    ('a name bound to the module, not the operation', ARMS[0][0],
     '\nimport importlib\n\n\ndef load():\n    kept = importlib.util\n'),
    ('an ordinary attribute of a tracked sys', ARMS[1][0],
     '\nimport sys\n\n\ndef load():\n    kept = sys.path\n'),
    ('a builtin that evaluates nothing', ARMS[2][0],
     '\n\n\ndef load():\n    kept = print\n'),
    ('a name the map does not track', ARMS[3][0],
     '\nimport importlib\nother = print\n'),
    ('a default bound to the module, not the operation', ARMS[4][0],
     '\nimport importlib\n\n\ndef load(op=importlib.util):\n    return op\n'),
    ('a default bound to an ordinary attribute', ARMS[5][0],
     '\nimport sys\n\n\ndef load(op=sys.argv):\n    return op\n'),
    ('a default bound to a builtin that evaluates nothing', ARMS[6][0],
     '\n\n\ndef load(op=len):\n    return op\n'),
    ('a string that names no operation', ARMS[7][0],
     '\nSPELLING = "importlib"\n'),
    ('a subscript off a tracked sys that is not the registry', ARMS[8][0],
     '\nimport sys\n\n\ndef load():\n    return sys.path[0]\n'),
    ('a named import binds what it names', ARMS[9][0],
     '\nfrom os import path\n'),
    ('a position the fold reads, so the module resolves', ARMS[11][0],
     '\nimport importlib\n\n\ndef load():\n'
     '    return (0, importlib.import_module)[4]("pkg.leaf")\n'),
    ('a lookup of an ordinary attribute', ARMS[12][0],
     '\nimport importlib\n\n\ndef load():\n'
     '    return getattr(importlib, "util")\n'),
)

# The barrier kinds `_mcp_dead_code._BARRIERS` names, plus the `if` whose
# two branches both leave. Each is the same tree: the call BEFORE the
# barrier still contributes a module, the one AFTER it does not. Carrying
# both in one composition is what stops a rule that stopped marking tails
# (which adds the leaf) and a rule that over-reached (which drops the
# before) from passing under the same name.
BARRIERS = (
    ('a raise', '\nimport importlib\n\n\ndef load():\n'
     '    importlib.import_module("pkg.before")\n'
     '    raise RuntimeError("barrier")\n'
     '    importlib.import_module("pkg.leaf")\n'),
    ('a return', '\nimport importlib\n\n\ndef load():\n'
     '    importlib.import_module("pkg.before")\n'
     '    return None\n'
     '    importlib.import_module("pkg.leaf")\n'),
    ('a break', '\nimport importlib\n\n\ndef load(items):\n'
     '    for item in items:\n'
     '        importlib.import_module("pkg.before")\n'
     '        break\n'
     '        importlib.import_module("pkg.leaf")\n'),
    ('a continue', '\nimport importlib\n\n\ndef load(items):\n'
     '    for item in items:\n'
     '        importlib.import_module("pkg.before")\n'
     '        continue\n'
     '        importlib.import_module("pkg.leaf")\n'),
    ('an if whose two branches both leave', '\nimport importlib\n\n'
     '\n\ndef load(flag):\n'
     '    importlib.import_module("pkg.before")\n'
     '    if flag:\n'
     '        raise RuntimeError("left")\n'
     '    else:\n'
     '        return None\n'
     '    importlib.import_module("pkg.leaf")\n'),
)

# The near miss every barrier kind shares: an `if` with no `else` whose
# body leaves, so a false condition falls straight through it and the tail
# is still reachable. Without this the table pins that tails are marked
# and not that only the leaving ones are.
BARRIER_NEAR_MISS = (
    '\nimport importlib\n\n\ndef load(flag):\n'
    '    importlib.import_module("pkg.before")\n'
    '    if flag:\n'
    '        raise RuntimeError("left")\n'
    '    importlib.import_module("pkg.leaf")\n')

# The package every barrier composition reads against, so a resolved module
# and a dropped one are the only two answers the walk can give.
BARRIER_TREE = {'pkg/__init__.py': '', 'pkg/before.py': 'before = True\n',
                'pkg/leaf.py': 'leaf = True\n'}
BARRIER_NAMES = ['composition.py', 'pkg/__init__.py', 'pkg/before.py']


def test_every_refusal_arm_is_refused_with_its_own_message(_tmp):
    """Each arm refuses, naming its own site and its own fixed phrase.

    The phrase is the part of the message that does not interpolate the
    node, so a refusal raised by a neighbouring arm cannot satisfy it. The
    failures are COLLECTED rather than raised at the first one, because a
    planted arm has to name every row it silenced, and a run that stops at
    the first cannot say which of them it reached.
    """
    quiet = []
    for label, _site, phrase, line, source in ARMS:
        try:
            _assert_scan_refusal(_tmp, source, line, phrase)
        except AssertionError as refused:
            quiet.append(f'{label}: {refused}')
    assert not quiet, '; '.join(quiet)


def test_every_refusal_arm_has_a_near_miss_the_walk_leaves_alone(_tmp):
    """Each arm's neighbour is clean, so an over-reaching rule loses too.

    The `_scan_verdict` answer is READ rather than merely not an
    exception: a composition that raised some other arm's refusal would
    answer `refused` here, which is the failure this row exists to catch.
    The failures are collected, for the same reason the refusal case
    collects them.
    """
    reached = []
    for label, beside, source in NEAR_MISSES:
        verdict, detail = _scan_verdict(_tmp, {'composition.py': source})
        if verdict != 'clean':
            reached.append(
                f'{label} (near miss of {beside}) was {verdict}: {detail}')
    assert not reached, '; '.join(reached)


def test_a_callee_the_fold_cannot_read_is_refused_where_it_reads_one(_tmp):
    """The fold answers one question with three answers, and each is read.

    A callee the fold reads is the operation and resolves the module; a
    position the runtime cannot reach raises on the expression and is
    SILENT; a position the fold cannot read is undecided, and the walk then
    refuses because the callee still mentions the operation. The middle
    answer is the one a rule that over-reaches loses, and the third is the
    one a rule that treats "not read" as "unreachable" loses.
    """
    assert _callee_scan(
        _tmp, '(0, importlib.import_module)[1]') == 'resolved'
    assert _callee_scan(
        _tmp, '(0, importlib.import_module)[4]') == 'silent'
    assert _callee_scan(
        _tmp, '(0, importlib.import_module)[i]') == 'refused'


def test_every_dead_code_barrier_kind_marks_the_tail_behind_it(_tmp):
    """Raise, return, break, continue and a two-leaving `if` each end a
    block, and the call before the barrier still contributes a module.

    Both halves ride in one tree, so a rule that stopped marking tails
    adds `pkg/leaf.py` and a rule that over-reached drops
    `pkg/before.py`; the assertion reads the whole set, not a membership.
    """
    over = []
    for label, source in BARRIERS:
        verdict, detail = _scan_verdict(
            _tmp, {'composition.py': source, **BARRIER_TREE})
        if (verdict, detail) != ('clean', BARRIER_NAMES):
            over.append(f'{label}: {verdict} {detail}')
    verdict, detail = _scan_verdict(
        _tmp, {'composition.py': BARRIER_NEAR_MISS, **BARRIER_TREE})
    if (verdict, detail) != ('clean', sorted(BARRIER_NAMES + ['pkg/leaf.py'])):
        over.append(f'a fall-through if: {verdict} {detail}')
    assert not over, '; '.join(over)


def _spelled_detail(node):
    """Whether a refusal argument SPELLS its message, and the fixed part.

    A literal is one, and an f-string is one: its holes become a
    placeholder, so what is left is the text that does not vary with the
    node the message quotes. A hole may interpolate anything — `_alias`
    quotes the node and `_refuse_default` quotes a NOUN chosen by the
    caller, and both still spell the refusal — which is why the hole is
    kept rather than refused. A bare name forwards somebody else's message
    and spells none, which is what tells a pass-through from an arm.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ''.join(
            part.value if isinstance(part, ast.Constant) else '\x00'
            for part in node.values)
    return None


def _arms_in(module_path):
    """Every refusal arm one analyser module spells, as (function, phrase).

    The reader is a parse of the module rather than a grep for a call: it
    walks for a call to `refuse` or `_refuse` whose detail argument is a
    spelled message, and for a `raise AssertionError` the module spells
    itself. The two call shapes differ — the closure's own raiser takes
    `(path, root, node, detail)` and every callback takes `(node, detail)` —
    so the detail is read from each shape's own position rather than from a
    fixed index, which is what would silently drop the whole
    `_BindingWalk` half of the grammar. `_refuse` itself is the raiser every
    arm reaches and is not an arm itself, and the lambda pass-throughs
    forward a name rather than spelling a message, so neither is reported.
    """
    tree = ast.parse(module_path.read_text(encoding='utf-8'))
    found = []

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            detail = None
            if isinstance(child, ast.Call):
                callee = child.func
                name = getattr(callee, 'id', None) or getattr(
                    callee, 'attr', None)
                position = {'_refuse': 3, 'refuse': 1}.get(name)
                if position is not None and len(child.args) > position:
                    detail = _spelled_detail(child.args[position])
            elif isinstance(child, ast.Raise) and scope != '_refuse' \
                    and isinstance(child.exc, ast.Call) and getattr(
                        child.exc.func, 'id', None) == 'AssertionError':
                detail = _spelled_detail(
                    child.exc.args[0] if child.exc.args else None)
            if detail is not None:
                found.append((scope, detail))
            inner = child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef,
                        ast.ClassDef)) else scope
            visit(child, inner)

    visit(tree, module_path.name)
    return found


def test_the_enumeration_covers_every_arm_the_analysers_spell(_tmp):
    """The table and the analysers cannot drift apart quietly.

    A refusal the analysers spell that no row here names would be an arm
    this suite claims to cover and does not, so the derived set of sites is
    compared with the table's, both ways. `_refuse_default` is one derived
    site and three rows, which is the arm count and the site count
    disagreeing on purpose: the message names the thing handed away through
    `_HIDDEN_NOUN`, so one call site spells three refusals.

    `composition_scan_set` is the derived site ARMS deliberately does not
    carry. It is the walk's own recursion limit, and the declared limit of
    the arms here is that no composition reaches it: CPython's parser
    refuses the shape first. Deeply nested parentheses and nested `if`
    blocks both raise `SyntaxError` from `ast.parse` — "too many nested
    parentheses" and "too many levels of indentation" — before
    `_import_targets` is entered, so the arm is a defensive refusal and a
    control for it could only pin that the walk refuses to parse.

    The guard floor's four refusals are named rather than carried:
    `test_mcp_guard_floor.py` drives each on synthetic input, which is the
    whole of that suite's subject.
    """
    del _tmp
    closure = _arms_in(CLOSURE)
    derived = {scope for scope, _phrase in closure}
    assert derived == {'_alias', '_registry_alias', '_code_eval_alias',
                       '_rebind', '_refuse_default',
                       '_refused_string_reads', '_import_targets',
                       'composition_scan_set'}, sorted(derived)
    assert len(closure) == 13, closure
    named = {site for _label, site, _phrase, _line, _source in ARMS}
    assert named == derived - {'composition_scan_set'}, sorted(named)
    assert len(ARMS) == 14, len(ARMS)
    floor = {scope for scope, _phrase in _arms_in(GUARD_FLOOR)}
    assert floor == {'_controlling_test', '_module_guard_sites',
                     'reachable_guards'}, sorted(floor)


def test_a_scanned_composition_is_read_from_disk_on_every_call(_tmp):
    """The fixture writes what it is handed, so two cases cannot share a
    tree by accident.

    `_scan_verdict` and `_assert_scan_refusal` both write `composition.py`
    into the one directory a case is handed, so a stale file from an
    earlier case would answer for a later one. The composition here draws
    no refusal, so the case reads the file back and the walk's own answer
    without claiming any arm.
    """
    _write_tree(Path(_tmp), {'composition.py': 'FIRST = 1\n'})
    assert (Path(_tmp) / 'composition.py').read_text(
        encoding='utf-8') == 'FIRST = 1\n'
    tree = {'composition.py': '\nSPELLING = "importlib"\n'}
    assert _scan_verdict(_tmp, tree) == ('clean', ['composition.py'])
    assert _mcp_import_closure.dotted_module(
        Path(_tmp) / 'composition.py', _tmp) == 'composition'


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
