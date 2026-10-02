#!/usr/bin/env python3
"""Every refusal SITE the import-closure walk has, and a case per arm.

THE MARKER IS A PLACE THAT CAN RAISE, not a test name, not a node type and
not a message. A site is a `raise` whatever it raises, or a call to a
refusal raiser — the closure's own `_refuse`, the callback every arm
receives, or the `AssertionError` constructor both of those raise — and it
is keyed on the function it stands in and its own line, because that is the
place a reader can go and look at. An arm whose message is assembled, passed
by keyword, forwarded through a local, produced by a helper, or raised as an
exception type nobody anticipated is still an arm, and a table keyed on
message text cannot see one of those: each was a real arm a reviewer planted
in a real analyser while this suite stayed green. The message is still read,
as the LABEL a row carries beside its site rather than as the site's
identity, so a row still reads like the refusal it names.

The set is derived twice over and compared with the tables both ways, so
neither the analysers nor the tables can drift apart quietly. The MODULES
come from the walk rather than from two literals: the connected component of
the `_mcp_*` module graph the walk's entry belongs to, keeping the members
that spell a refusal site at all (`analyser_modules`). The FLOOR's own sites
have a table and a count of their own, because a set of function names
absorbs an arm added inside `_module_guard_sites` and that hole was invisible
forever.

The comparison cannot pass by returning nothing on both sides. `CONTROL`
below is a module whose arms are spelled in every shape the old marker
missed, and the derivation is asked to find all of them.

Two details are shaped so a plain grep of the analysers under-counts them,
and the labels read both shapes rather than one:

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
take. `test_mcp_tools.py` carries the real-tree walk and the limits it
cannot exercise; this suite carries the arms.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_import_closure  # noqa: E402
import _util  # noqa: E402
from _mcp_import_fixtures import (  # noqa: E402
    _assert_scan_refusal, _scan_verdict, _write_tree)

ROOT = _util.ROOT
TESTS = ROOT / 'tests'

# The names a refusal can be raised through, spelled here rather than read
# out of the analysers because the two modules disagree about which is which
# — one DEFINES the raiser and the other only calls it — and because what
# makes a site recognizable is the shape of the call, not its message. A
# module that raises a refusal through some OTHER mechanism is still found,
# because every `raise` counts whatever exception it carries.
REFUSAL_RAISERS = frozenset({'_refuse', 'refuse', 'AssertionError'})

# The walk's entry is named by the FUNCTION it defines rather than by its
# path, so a rename carries the anchor with it. A derivation needs one seed;
# everything else about the module set follows from it.
WALK_ENTRY = 'composition_scan_set'

# (the analyser site that spells it, a label, the fixed part of its
# message, the site the refusal names, the composition the arm refuses).
# The first column is `(enclosing function, line in the analyser)` — the
# IDENTITY of the arm, and the only key every table below is compared on —
# and the message is the label beside it. The composition's own site is a
# line in the composition source: the leading newline in every source below
# puts the first real line on line 2, and each refusal line is counted, not
# guessed.
ARMS = (
    (('_alias', 219), 'a store binds the import-by-name operation to a name',
     'binds the import-by-name operation to a name this scan', 6, '\nimport importlib\n\n\ndef load():\n'
        '    leak = importlib.import_module\n'),
    (('_registry_alias', 224), 'a store binds the module registry to a name',
     'binds the module registry to a name this scan', 6, '\nimport sys\n\n\ndef load():\n    leak = sys\n'),
    (('_code_eval_alias', 229),
     'a store binds a code-evaluating builtin to a name',
     'binds a code-evaluating builtin to a name this', 5, '\n\n\ndef load():\n    leak = eval\n'),
    (('_rebind', 234), 'a store rebinds a name the map tracks',
     "rebinds 'importlib', which this scan maps to the", 3, '\nimport importlib\nimportlib = print\n'),
    (('_refuse_default', 307), 'a parameter default hands away the operation',
     'parameter op=importlib.import_module binds the import-by-name', 5, '\nimport importlib\n\n\ndef load(op=importlib.import_module):\n'
        '    return op\n'),
    (('_refuse_default', 307), 'a parameter default hands away the registry',
     'parameter op=sys binds the module registry to', 5, '\nimport sys\n\n\ndef load(op=sys):\n    return op\n'),
    (('_refuse_default', 307),
     'a parameter default hands away a code-evaluating builtin',
     'parameter op=eval binds a code-evaluating', 4, '\n\n\ndef load(op=eval):\n    return op\n'),
    (('_refused_string_reads', 558),
     'a string names the import-by-name operation',
     "the string 'import_module' names the import-by-name operation", 2, '\nSPELLING = "import_module"\n'),
    (('_refused_string_reads', 561), 'a module is read out of the registry',
     'a module is read out of the registry by', 6, '\nimport sys\n\n\ndef load():\n'
        '    return sys.modules["pkg.leaf"]\n'),
    (('_import_targets', 616), 'a star import binds names the map cannot hold',
     'a star import binds names this scan cannot', 2, '\nfrom os import *\n'),
    (('_import_targets', 647),
     'the operation is called with a name the walk cannot read',
     'is called with a name this scan cannot read', 6, '\nimport importlib\n\n\ndef load(name):\n'
        '    return importlib.import_module(name)\n'),
    (('_import_targets', 652),
     'a callee reaches the operation through a value it cannot resolve',
     'reaches the import-by-name operation through a', 6, '\nimport importlib\n\n\ndef load(i):\n'
        '    return (0, importlib.import_module)[i]("pkg.leaf")\n'),
    (('_import_targets', 657), 'a lookup can hand out the operation',
     'can hand out the import-by-name operation through', 6, '\nimport importlib\n\n\ndef load():\n'
        '    return getattr(importlib, "import_module")\n'),
    # No near miss: this arm refuses EVERY constant program a code-evaluating
    # builtin is handed, whatever it names, because the walk resolves the
    # program rather than reading it. Its boundary is the delivery axis
    # above (a builtin bound to a tool stays silent), not the program's
    # text, so a weaker row here would pin nothing.
    (('_import_targets', 666),
     'a constant program is handed to a code-evaluating builtin',
     'is handed to a code-evaluating builtin, which', 5, '\n\n\ndef load():\n    return eval("importlib")\n'),
)

# (label, the arm it sits beside, the composition the walk must leave
# alone). The second column is the assertion, not decoration: it says which
# arm's near miss this is, so a row cannot be read as covering an arm the
# test never named.
NEAR_MISSES = (
    ('a name bound to the module, not the operation', ARMS[0][1],
     '\nimport importlib\n\n\ndef load():\n    kept = importlib.util\n'),
    ('an ordinary attribute of a tracked sys', ARMS[1][1],
     '\nimport sys\n\n\ndef load():\n    kept = sys.path\n'),
    ('a builtin that evaluates nothing', ARMS[2][1],
     '\n\n\ndef load():\n    kept = print\n'),
    ('a name the map does not track', ARMS[3][1],
     '\nimport importlib\nother = print\n'),
    ('a default bound to the module, not the operation', ARMS[4][1],
     '\nimport importlib\n\n\ndef load(op=importlib.util):\n    return op\n'),
    ('a default bound to an ordinary attribute', ARMS[5][1],
     '\nimport sys\n\n\ndef load(op=sys.argv):\n    return op\n'),
    ('a default bound to a builtin that evaluates nothing', ARMS[6][1],
     '\n\n\ndef load(op=len):\n    return op\n'),
    ('a string that names no operation', ARMS[7][1],
     '\nSPELLING = "importlib"\n'),
    ('a subscript off a tracked sys that is not the registry', ARMS[8][1],
     '\nimport sys\n\n\ndef load():\n    return sys.path[0]\n'),
    ('a named import binds what it names', ARMS[9][1],
     '\nfrom os import path\n'),
    ('an argument the walk can see is not a name', ARMS[10][1],
     '\nimport importlib\n\n\ndef load():\n'
     '    return importlib.import_module(4)\n'),
    ('a position the fold reads, so the module resolves', ARMS[11][1],
     '\nimport importlib\n\n\ndef load():\n'
     '    return (0, importlib.import_module)[4]("pkg.leaf")\n'),
    ('a lookup of an ordinary attribute', ARMS[12][1],
     '\nimport importlib\n\n\ndef load():\n'
     '    return getattr(importlib, "util")\n'),
)

# The sites no composition drives, each with the reason it has no row of
# its own. They are arms in the sense this file counts — places that can
# raise — so a table carrying only the driven ones would still let a site
# appear in one of these without a word.
UNDRAWN = (
    (('_refuse', 90), 'the raiser itself: every driven site reaches it, so '
     'a composition names the SITE that called it rather than this one'),
    (('composition_scan_set', 124), 'the walk\'s own recursion limit, a '
     'declared limit no composition reaches — the case at the foot of this '
     'file carries the evidence'),
    (('_refused_bindings', 408), 'the dead-node gate: it forwards a detail it '
     'did not spell and is where the arms behind a barrier are dropped'),
    (('_import_targets', 607), 'the lambda that binds path and root for the '
     'closure\'s own raiser'),
    (('_import_targets', 674), 'the same lambda one function on, for the '
     'string reads'),
)

# The sites in the other two modules the walk's surface is built from, each
# with the case that drives it. They are counted here rather than left to a
# set of function names, which is exactly what let an arm added inside
# `_module_guard_sites` go unseen. The fixture module is in the derived set
# because it calls the walk, and its one site is the assertion it makes on
# the walk's behalf rather than a refusal of a composition.
FLOOR_ARMS = (
    (('_controlling_test', 196), 'test_mcp_guard_floor.py'
     '::test_a_raise_under_a_match_case_is_refused'),
    (('_module_guard_sites', 255), 'test_mcp_guard_floor.py'
     '::test_two_raises_on_one_line_are_refused'),
    (('_module_guard_sites', 277), 'test_mcp_guard_floor.py'
     '::test_two_sites_spelling_one_condition_are_refused'),
    (('reachable_guards', 393), 'test_mcp_guard_floor.py'
     '::test_a_reached_or_guard_is_refused'),
)
FIXTURE_ARMS = (
    (('_assert_scan_refusal', 46), 'test_helper_assertion_pins.py drives '
     'the reader this site is the last assertion of'),
)

# The table that claims to cover each derived module, so a module the
# derivation finds with no table fails and a table naming a module the
# derivation does not find fails too.
COVERED = {
    '_mcp_import_closure.py': ARMS + UNDRAWN,
    '_mcp_guard_floor.py': FLOOR_ARMS,
    '_mcp_import_fixtures.py': FIXTURE_ARMS,
}

# The negative control the comparison needs. Every arm here is spelled in a
# shape the old marker could not see, in a module the derivation reads by
# source rather than from the repository, so the case cannot pass by
# returning nothing on both sides: a reader that went back to reading
# messages would find none of the six.
CONTROL = '''
def _refuse(path, root, node, detail):
    raise AssertionError(f'{path}:{node.lineno}: {detail}')


def _detail(node):
    return f'spelled by a helper for {node}'


def by_keyword(node):
    _refuse(_refuse, _refuse, node, detail='passed by keyword')


def by_helper(node):
    _refuse(_refuse, _refuse, node, _detail(node))


def through_a_local(node):
    detail = f'spelled into a local for {node}'
    _refuse(_refuse, _refuse, node, detail)


def by_concatenation(node):
    _refuse(_refuse, _refuse, node, 'import_' + 'module')


def by_a_third_exception(node):
    raise RecursionError(f'not an AssertionError, for {node}')
'''

# The sites `CONTROL` spells, by the same `(function, line)` identity every
# other key uses. The line numbers are the fixture's own, so a fixture that
# grows moves them and the case says so.
CONTROL_SITES = (
    ('_refuse', 3),
    ('by_keyword', 11),
    ('by_helper', 15),
    ('through_a_local', 20),
    ('by_concatenation', 24),
    ('by_a_third_exception', 28),
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
    ('an if whose two branches both leave', '\nimport importlib\n'
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

# The tree a callee answer is read against: a resolvable leaf module, so a
# closure that resolved the operation carries it and one that declined does
# not, which is what tells a resolution apart from a silence.
LEAF_TREE = {'pkg/__init__.py': '', 'pkg/leaf.py': 'leaf = True\n'}

# The index axis's own question, asked of the module's own symbol table:
# `bool` under its own name and under an alias IS the builtin wherever the
# module leaves the name alone, and a name the module binds to something of
# its own is not it. `_Scopes.denotes_builtin` is the only declaration in a
# kept module that answers it, so nothing else in the tree can tell its
# answer from False.
BOOL_INDEX = (
    ('', 'bool(0)', 'resolved'),
    ('from builtins import bool as b', 'b(0)', 'resolved'),
    ('', 'bool(2)', 'silent'),
    ('bool = print', 'bool(0)', 'refused'),
    ('from builtins import bool as b\nb = print', 'b(0)', 'refused'),
    ('from builtins import bool\nbool = print', 'bool(0)', 'refused'),
)

# The constant folder's own answers, which are what decide whether a string
# names the operation: a literal is read, a concatenation of literals folds
# to the same constant and is read, a field-less f-string is read, and a
# value COMPUTED at runtime is the declared limit and is not.
FOLDED_STRING = (
    ('"import_module"', 'refused'),
    ("'import_' + 'module'", 'refused'),
    ("f'import_module'", 'refused'),
    ("'import_' + name", 'clean'),
)


def test_every_refusal_arm_is_refused_with_its_own_message(_tmp):
    """Each arm refuses, naming its own site and its own fixed phrase.

    The phrase is the part of the message that does not interpolate the
    node, so a refusal raised by a neighbouring arm cannot satisfy it. The
    failures are COLLECTED rather than raised at the first one, because a
    planted arm has to name every row it silenced, and a run that stops at
    the first cannot say which of them it reached.
    """
    quiet = []
    for _site, label, phrase, line, source in ARMS:
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
    for callee, spoken in (('importlib.import_module', 'resolved'),
                           ('(0, importlib.import_module)[4]', 'silent'),
                           ('(0, importlib.import_module)[i]', 'refused')):
        read = _answered(_tmp, {'composition.py': _callee_composition(callee),
                                **LEAF_TREE})
        assert read == spoken, f'{callee}: {read}, not {spoken}'


def test_a_bool_index_reads_the_position_it_names(_tmp):
    """`bool` settles an index under its own name and under an alias, and a
    name bound to something of its own does not settle it at all.

    The answer is read in all three words, and the rows carry both sides: a
    rule that stopped recognising the builtin refuses the rows the walk
    resolves, and one that recognised any name at all resolves rows it must
    refuse.
    """
    wrong = []
    for bindings, call, spoken in BOOL_INDEX:
        files = {'composition.py': _indexed_composition(bindings, call),
                 **LEAF_TREE}
        read = _answered(_tmp, files)
        if read != spoken:
            wrong.append(f'{bindings!r} {call}: {read}, not {spoken}')
    assert not wrong, '; '.join(wrong)


def test_a_string_the_folder_can_fold_is_refused_like_a_literal(_tmp):
    """The constant folder decides the string axis, and it folds a
    concatenation of literals and a field-less f-string to the same constant
    a literal already is.

    The row that must stay CLEAN is the declared limit beside them: a
    string assembled from a runtime value folds to nothing, so the walk
    follows nothing and the closure is quietly short. It is read here so the
    three refusals are not a rule that refuses every string it meets.
    """
    wrong = []
    for statement, spoken in FOLDED_STRING:
        verdict, detail = _scan_verdict(
            _tmp, {'composition.py': f'\nSPELLING = {statement}\n'})
        if verdict != spoken:
            wrong.append(f'{statement}: {verdict}, not {spoken} ({detail})')
    assert not wrong, '; '.join(wrong)


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


def test_the_enumeration_covers_every_arm_the_analysers_spell(_tmp):
    """Every site the analysers can raise has a row, on both sides.

    The module set is DERIVED (`analyser_modules`) rather than named, the
    sites are read off each module's parse, and each set is compared with
    the table that claims to cover it both ways: a site with no row fails,
    and a row whose site is gone fails, so neither the analysers nor the
    tables can move without this saying so. A count sits beside each
    comparison because two wrong answers of the same size satisfy a set
    equality, and the FLOOR has its own table and count for the reason its
    sites were absorbed by a set of function names.

    The comparison cannot pass vacuously. `CONTROL` is a module whose arms
    are spelled by keyword, through a helper, through a local, by
    concatenation and as a third exception type, and the derivation is
    asked to find all six — so a reader that had gone back to reading
    messages would report nothing there and fail.

    `composition_scan_set` is a site in UNDRAWN and not in ARMS. It is the
    walk's own recursion limit, and the declared limit of the arms here is
    that no composition reaches it: CPython's parser refuses the shape
    first. Deeply nested parentheses, list displays, `if` blocks and `try`
    blocks each raise `SyntaxError` from `ast.parse` at depth 201 or 100 —
    "too many nested parentheses" and "too many levels of indentation" —
    before `_import_targets` is entered, so the arm is a defensive refusal
    and a control for it could only pin that the walk refuses to parse.

    `_refuse_default` is one derived site and three rows, which is the arm
    count and the site count disagreeing on purpose: the message names the
    thing handed away through `_HIDDEN_NOUN`, so one call site spells three
    refusals.
    """
    del _tmp
    assert sorted(_arm_sites(CONTROL, 'control.py')) == sorted(CONTROL_SITES), (
        'the control module\'s arms are not all found, so the comparison '
        'below cannot be trusted to fail on an arm it missed')
    modules = {path.name: path for path in analyser_modules()}
    assert sorted(modules) == sorted(COVERED), sorted(modules)
    for name, table in sorted(COVERED.items()):
        derived = _arm_sites(
            modules[name].read_text(encoding='utf-8'), name)
        claimed = sorted({key for key, *_ in table})
        assert sorted(derived) == claimed, (
            f'{name}: the derived sites are {sorted(derived)}')
        assert len(derived) == len(claimed), (name, len(derived), len(claimed))
    assert len(ARMS) == 14, len(ARMS)
    assert len(UNDRAWN) == 5, len(UNDRAWN)
    assert len(FLOOR_ARMS) == 4, len(FLOOR_ARMS)
    assert len(FIXTURE_ARMS) == 1, len(FIXTURE_ARMS)


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


def _answered(_tmp, files):
    """`_scan_verdict`'s answer in the three words the callee cases read:
    `refused`, or `resolved` when the scan set carries the leaf module and
    `silent` when it does not.

    One reader for both directions, so a control states the arm it enters
    and the neighbour it must leave alone with the same call and one shape
    of answer. This classification used to live in the shared fixtures
    bolted to a single callee composition, which is the one shape no second
    case could reuse; the composition belongs to the case that drives it.
    """
    verdict, detail = _scan_verdict(_tmp, files)
    if verdict == 'refused':
        return 'refused'
    return 'resolved' if 'pkg/leaf.py' in detail else 'silent'


def _callee_composition(callee):
    """A composition whose callee is the one spelling, called for its
    module."""
    return ('\nimport importlib\n\n\ndef load(c, i):\n'
            f'    return {callee}("pkg.leaf")\n')


def _indexed_composition(bindings, call):
    """A composition that indexes a container holding the operation by a
    `bool` call, so the INDEX AXIS decides whether the module resolves."""
    return (f'\nimport importlib\n{bindings}\n\n\ndef load():\n'
            '    return [importlib.import_module, 0]'
            f'[{call}]("pkg.leaf")\n')


def _arm_sites(source, name):
    """Every site in one module's SOURCE that can raise a refusal, as
    `(enclosing function, line)`.

    A `raise` is a site whatever it raises, and so is a call to one of
    `REFUSAL_RAISERS`; a call that IS a raise's exception is the same site
    and is not counted twice. A module-level site carries the module's own
    name for its scope. The message is deliberately NOT read: an arm whose
    detail is assembled, passed by keyword, forwarded through a local,
    produced by a helper or raised as an exception type nobody anticipated
    is found here exactly as one that spells a literal is, which is the
    whole difference between a site and a message.
    """
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        raisable = isinstance(node, ast.Raise) or (
            isinstance(node, ast.Call)
            and (getattr(node.func, 'id', None)
                 or getattr(node.func, 'attr', None)) in REFUSAL_RAISERS
            and not isinstance(parents.get(node), ast.Raise))
        if raisable:
            found.append((_enclosing(node, parents, name), node.lineno))
    return found


def _enclosing(node, parents, name):
    """The innermost function or class a node sits in, or `name`."""
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            return parent.name
        parent = parents.get(parent)
    return name


def _local_imports(path):
    """The sibling `tests/` modules one file imports, as paths."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names = [node.module or '']
        else:
            continue
        for imported in names:
            sibling = TESTS / f'{imported}.py'
            if sibling.is_file():
                found.add(sibling)
    return found


def analyser_modules():
    """The analyser modules the walk is built from, derived.

    The set is the CONNECTED COMPONENT of the `_mcp_*` module graph the
    walk's entry belongs to, keeping the members that spell a refusal site
    at all. That is what two literals could not do: the helpers
    `_mcp_code_eval`, `_mcp_dead_code` and `_mcp_selection_fold` are in the
    component and are absent because they raise nothing, rather than by
    being named out; a module wired into the walk joins the component and has
    to be declared; and an arm added in `_module_guard_floor` is counted
    rather than absorbed by a set of function names.
    """
    modules = {path for path in TESTS.glob('_mcp_*.py') if path.is_file()}
    entry = {path for path in modules if _defines_function(path, WALK_ENTRY)}
    assert len(entry) == 1, f'{WALK_ENTRY} is defined by {sorted(entry)}'
    edges = {path: _local_imports(path) for path in modules}
    start = entry.pop()
    component = {start}
    frontier = {start}
    while frontier:
        reachable = set()
        for path in frontier:
            reachable |= edges[path] | {
                other for other in modules if path in edges[other]}
        frontier = reachable - component
        component |= frontier
    return sorted(path for path in component if _arm_sites(
        path.read_text(encoding='utf-8'), path.name))


def _defines_function(path, function):
    """Whether one module defines a top-level function of that name."""
    return any(isinstance(node, ast.FunctionDef) and node.name == function
               for node in ast.parse(
                   path.read_text(encoding='utf-8')).body)


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
