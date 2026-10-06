#!/usr/bin/env python3
"""Every refusal SITE the import-closure walk has, and a case per arm.

THE MARKER IS A PLACE THAT CAN RAISE, not a test name, not a node type and
not a message: a site is a `raise` whatever it raises, or a call to a
refusal raiser — the closure's own `_refuse`, the callback every arm
receives, or the `AssertionError` constructor both of those raise — keyed
on the function it stands in and its own line. An arm whose message is
assembled, passed by keyword, forwarded through a local, produced by a
helper, or raised as an exception type nobody anticipated is still an arm,
and a table keyed on message text cannot see one. The message is still
read, as the LABEL a row carries beside its site, and each row carries the
FIXED part of the message, never the node's own spelling. Two labels are
shaped so a plain grep of the analysers under-counts them:
`_refuse_default` names the thing it hands away through `_HIDDEN_NOUN`, a
VARIABLE, so one call site spells three refusals, and
`yields_the_operation` refuses a mention that is a FORMAT of the node.

The set is derived twice over and compared with the tables both ways, so
neither the analysers nor the tables can drift apart quietly. Both halves
of the derivation live in `_mcp_arm_derivation`, which recognises what a
thing IS rather than how it is written — a raiser through the name or
attribute it is bound to, through a partial built from one, a module the
walk REACHES — and the MODULES are the connected component of the `_mcp_*`
graph the walk's entry belongs to, keeping members that spell a site. The
comparison cannot pass by returning nothing on both sides: `CONTROL` and
`CONTROL_LEAF` are a two-module TREE, the second
reached from the first by nothing but a `__import__` call, so the control is
on the MODULE-SET axis as well as the detail axis, and the derivation is
asked to find every one of them before it compares anything. A key is a
LINE, the convention `_mcp_guard_floor` keys its own sites on. Each arm is
stated twice: the refusal, and a
NEAR MISS the walk must leave alone — the same shape with the separating
decision taken the other way, so a rule that over-reaches loses as
visibly as one that under-reaches; an arm with no near miss says so.
"""
import ast
import contextlib
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_arm_derivation  # noqa: E402
import _mcp_dead_code  # noqa: E402
import _util  # noqa: E402
from _mcp_import_fixtures import (  # noqa: E402
    _assert_scan_refusal, _scan_verdict)

# (analyser site, label, the FIXED part of its message, the site the
# refusal names, the composition the arm refuses). The first column
# `(enclosing function, line)` is the arm's IDENTITY and the only key every
# table below compares on. The leading newline in every source puts the
# first real line on line 2.
ARMS = (
    (('_alias', 219), 'a store binds the import-by-name operation to a name',
     'binds the import-by-name operation to a name this scan',
     6, '\nimport importlib\n\n\ndef load():\n'
        '    leak = importlib.import_module\n'),
    (('_registry_alias', 224), 'a store binds the module registry to a name',
     'binds the module registry to a name this scan',
     6, '\nimport sys\n\n\ndef load():\n    leak = sys\n'),
    (('_code_eval_alias', 229),
     'a store binds a code-evaluating builtin to a name',
     'binds a code-evaluating builtin to a name this',
     5, '\n\n\ndef load():\n    leak = eval\n'),
    (('_rebind', 234), 'a store rebinds a name the map tracks',
     "rebinds 'importlib', which this scan maps to the",
     3, '\nimport importlib\nimportlib = print\n'),
    (('_refuse_default', 307), 'a parameter default hands away the operation',
     'parameter op=importlib.import_module binds the import-by-name',
     5, '\nimport importlib\n\n\ndef load(op=importlib.import_module):\n'
        '    return op\n'),
    (('_refuse_default', 307), 'a parameter default hands away the registry',
     'parameter op=sys binds the module registry to',
     5, '\nimport sys\n\n\ndef load(op=sys):\n    return op\n'),
    (('_refuse_default', 307),
     'a parameter default hands away a code-evaluating builtin',
     'parameter op=eval binds a code-evaluating',
     4, '\n\n\ndef load(op=eval):\n    return op\n'),
    (('_refused_string_reads', 558),
     'a string names the import-by-name operation',
     "the string 'import_module' names the import-by-name operation",
     2, '\nSPELLING = "import_module"\n'),
    (('_refused_string_reads', 561), 'a module is read out of the registry',
     'a module is read out of the registry by',
     6, '\nimport sys\n\n\ndef load():\n'
        '    return sys.modules["pkg.leaf"]\n'),
    (('_import_targets', 616),
     'a star import binds names the map cannot hold',
     'a star import binds names this scan cannot',
     2, '\nfrom os import *\n'),
    (('_import_targets', 647),
     'the operation is called with a name the walk cannot read',
     'is called with a name this scan cannot read',
     6, '\nimport importlib\n\n\ndef load(name):\n'
        '    return importlib.import_module(name)\n'),
    (('_import_targets', 652),
     'a callee reaches the operation through a value it cannot resolve',
     'reaches the import-by-name operation through a',
     6, '\nimport importlib\n\n\ndef load(i):\n'
        '    return (0, importlib.import_module)[i]("pkg.leaf")\n'),
    (('_import_targets', 657), 'a lookup can hand out the operation',
     'can hand out the import-by-name operation through',
     6, '\nimport importlib\n\n\ndef load():\n'
        '    return getattr(importlib, "import_module")\n'),
    # No near miss: this arm refuses EVERY constant program a code-evaluating
    # builtin is handed, whatever it names, because the walk resolves the
    # program rather than reading it. Its boundary is the delivery axis
    # above (a builtin bound to a tool stays silent), not the program's
    # text, so a weaker row here would pin nothing.
    (('_import_targets', 666),
     'a constant program is handed to a code-evaluating builtin',
     'is handed to a code-evaluating builtin, which',
     5, '\n\n\ndef load():\n    return eval("importlib")\n'),
    # Parse ok past depth 2000; a frame per subscript level; no line named.
    (('composition_scan_set', 124), 'a composition nested deeper '
     'than the walk follows', 'is too deeply nested to follow', None,
     '\nimport importlib\n\n\ndef load():\n    return '
     '[[importlib.import_module]]' + '[0]' * 800 + '("pkg.leaf")\n'),
)
# (label, the arm whose near miss this is, the composition the walk must
# leave alone) — the second column names whose near miss it is.
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
    ('a chain the walk can follow it leaves alone', ARMS[14][1],
     '\nimport importlib\n\n\ndef load():\n'
     '    return [[importlib.import_module]][0][0](4)\n'),
)

# Sites no composition drives, each with the reason; they are arms in the
# sense this file counts, so omitting them would let a site vanish silently.
UNDRAWN = (
    (('_refuse', 90), 'the raiser itself: every driven site reaches it, so '
     'a composition names the SITE that called it rather than this one'),
    (('_refused_bindings', 408), 'the dead-node gate: it forwards a detail it '
     'did not spell and is where the arms behind a barrier are dropped'),
    (('_import_targets', 607), 'the lambda that binds path and root for the '
     'closure\'s own raiser'),
    (('_import_targets', 674), 'the same lambda one function on, for the '
     'string reads'),
)

# Sites in the other two modules the walk's surface is built from, each
# with the case that drives it — counted here rather than left to a set of
# function names, which is what let an arm inside `_module_guard_sites` go
# unseen.
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
    (('_assert_scan_refusal', 43), 'test_mcp_import_refusals.py drives '
     'the reader this site is the last assertion of'),
)

# The table claiming to cover each derived module, both ways.
COVERED = {
    '_mcp_import_closure.py': ARMS + UNDRAWN,
    '_mcp_guard_floor.py': FLOOR_ARMS,
    '_mcp_import_fixtures.py': FIXTURE_ARMS,
}

# The negative control the comparison needs: a TREE of two modules, the
# second in the component only because the WALK follows the one
# `__import__` call at the foot of `CONTROL` — so the control holds on the
# MODULE-SET axis, not only on the detail axis, and the case cannot pass by
# returning nothing on both sides.
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


__import__('control_leaf')
'''

# The sites `CONTROL` spells, same `(function, line)` identity as every
# other key, in the fixture's own line numbers.
CONTROL_SITES = (
    ('_refuse', 3),
    ('by_keyword', 11),
    ('by_helper', 15),
    ('through_a_local', 20),
    ('by_concatenation', 24),
    ('by_a_third_exception', 28),
)

# The leaf's arms are the RAISER axis in the three shapes a name-keyed
# reader cannot see: a raiser reached through a local, an attribute, and a
# `functools.partial`.
CONTROL_LEAF = '''import functools


def _refuse(node):
    raise AssertionError(f'control_leaf: {node}')


def through_a_local_alias(node):
    _ref = _refuse
    _ref(node)


class _Held:
    def __init__(self):
        self._r = _refuse

    def through_an_attribute(self, node):
        self._r(node)


class _Built:
    def __init__(self):
        self._p = functools.partial(_refuse)

    def through_a_partial(self, node):
        self._p(node)
'''

CONTROL_LEAF_SITES = (
    ('_refuse', 5),
    ('through_a_local_alias', 10),
    ('through_an_attribute', 18),
    ('through_a_partial', 26),
)

# The barrier kinds `_mcp_dead_code._BARRIERS` names, plus the `if` whose
# two branches both leave. Each is one tree: the call BEFORE the barrier
# still contributes a module, the one AFTER it does not — so a rule that
# stopped marking tails and a rule that over-reached both fail under one
# name.
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

# Shared near miss: an `if` with no `else` whose body leaves, so the tail
# is still reachable — the table pins ONLY-LEAVING blocks, not every block.
BARRIER_NEAR_MISS = (
    '\nimport importlib\n\n\ndef load(flag):\n'
    '    importlib.import_module("pkg.before")\n'
    '    if flag:\n'
    '        raise RuntimeError("left")\n'
    '    importlib.import_module("pkg.leaf")\n')

# What every barrier composition reads against.
BARRIER_TREE = {'pkg/__init__.py': '', 'pkg/before.py': 'before = True\n',
                'pkg/leaf.py': 'leaf = True\n'}
BARRIER_NAMES = ['composition.py', 'pkg/__init__.py', 'pkg/before.py']

# The tree a callee answer is read against: carries the leaf iff the
# operation was resolved, telling a resolution from a silence.
LEAF_TREE = {'pkg/__init__.py': '', 'pkg/leaf.py': 'leaf = True\n'}

# The index axis's own question, asked of the module's own symbol table:
# `bool` under its own name and an alias IS the builtin wherever the module
# leaves the name alone; `_Scopes.denotes_builtin` is the only kept
# declaration that answers it.
BOOL_INDEX = (
    ('', 'bool(0)', 'resolved'),
    ('from builtins import bool as b', 'b(0)', 'resolved'),
    ('', 'bool(2)', 'silent'),
    ('bool = print', 'bool(0)', 'refused'),
    ('from builtins import bool as b\nb = print', 'b(0)', 'refused'),
    ('from builtins import bool\nbool = print', 'bool(0)', 'refused'),
)

# The ORDER the module's statements run in: a `from builtins` binds the
# builtin once its own statement has run, so a use BEFORE it reads a name
# the module has not bound yet and the walk declines. Every BOOL_INDEX row
# puts the binding first, so the axis was carried by no row above.
BINDING_ORDER = (
    ('\nimport importlib\nfrom builtins import bool as b\n\n\ndef load():\n'
     '    return [importlib.import_module, 0][b(0)]("pkg.leaf")\n',
     'resolved', None),
    ('\nimport importlib\n\n\ndef load():\n'
     '    v = [importlib.import_module, 0][b(0)]("pkg.leaf")\n'
     '    from builtins import bool as b\n    return v\n',
     'refused', 'reaches the import-by-name operation through a'),
)


# A conditional settles a position only when BOTH arms settle and AGREE.
# Agreeing arms are the near miss; disagreeing arms are the positive — read
# either alone and the position resolves, putting a module in a closure
# only one of two values ever reaches.
def _conditional_index(index):
    """A composition whose INDEX is the one expression."""
    return (f'\nimport importlib\n\n\ndef load():\n'
            f'    return [0, importlib.import_module][{index}]("pkg.leaf")\n')


CONDITIONAL_INDEX = (
    (_conditional_index('1 if c else 1'), 'resolved', None),
    (_conditional_index('1 if c else 2'), 'refused',
     'reaches the import-by-name operation through a'),
)


# The constant folder's answers: literal, literal concatenation and
# field-less f-string fold and are refused; a runtime-computed value is the
# declared limit and is not.
FOLDED_STRING = (
    ('"import_module"', 'refused'),
    ("'import_' + 'module'", 'refused'),
    ("f'import_module'", 'refused'),
    ("'import_' + name", 'clean'),
)


def test_every_refusal_arm_is_refused_with_its_own_message(_tmp):
    """Each arm refuses, naming its own site and its own fixed phrase.

    The phrase is the part of the message that does not interpolate the
    node, so a refusal raised by a neighbouring arm cannot satisfy it; the
    failures are COLLECTED, because a planted arm has to name every row it
    silenced.
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

    The whole `_scan_verdict` answer is READ — verdict and names — the
    pair the barriers case already reads, so a walk that over-delivers
    loses this case the way it already loses that one. The failures are
    collected, for the same reason the refusal case collects them.
    """
    reached = []
    for label, beside, source in NEAR_MISSES:
        verdict, detail = _scan_verdict(_tmp, {'composition.py': source})
        if (verdict, detail) != ('clean', ['composition.py']):
            reached.append(
                f'{label} (near miss of {beside}) was {verdict}: {detail}')
    assert not reached, '; '.join(reached)


def test_a_callee_the_fold_cannot_read_is_refused_where_it_reads_one(_tmp):
    """The fold answers one question with three answers, and each is read
    (`_mcp_selection_fold` states the taxonomy): the middle answer is the
    one a rule that over-reaches loses, and the third is the one a rule
    that treats "not read" as "unreachable" loses.
    """
    for callee, spoken in (('importlib.import_module', 'resolved'),
                           ('(0, importlib.import_module)[4]', 'silent'),
                           ('(0, importlib.import_module)[i]', 'refused')):
        read = _answered(_tmp, {'composition.py': _callee_composition(callee),
                                **LEAF_TREE})
        assert read == spoken, f'{callee}: {read}, not {spoken}'


def test_a_bool_index_reads_the_position_it_names(_tmp):
    """`bool` settles an index under its own name and under an alias, and a
    name bound to something of its own does not settle it at all: a rule
    that stopped recognising the builtin refuses the rows the walk
    resolves, and one that recognised any name at all resolves rows it
    must refuse.
    """
    wrong = []
    for bindings, call, spoken in BOOL_INDEX:
        files = {'composition.py': _indexed_composition(bindings, call),
                 **LEAF_TREE}
        read = _answered(_tmp, files)
        if read != spoken:
            wrong.append(f'{bindings!r} {call}: {read}, not {spoken}')
    assert not wrong, '; '.join(wrong)


def test_an_alias_the_module_has_not_run_yet_is_not_the_builtin(_tmp):
    """A `from builtins` binds the builtin ONCE ITS OWN STATEMENT HAS RUN:
    the pair is the same binding and the same use written in the two
    orders, and the refusing row names the arm it enters — a rule that
    reads the binding and drops the order resolves it, one that declines
    every alias loses the other row.
    """
    assert not _index_verdicts(_tmp, BINDING_ORDER)


def test_a_conditional_index_settles_only_when_its_two_arms_agree(_tmp):
    """A conditional is a RUNTIME choice, so it settles a value only when
    both of its arms settle to the same one. The refusal is read against
    the arm it enters, so a rule that reads the binding and drops the
    agreement resolves a module only one of two values ever reaches.
    """
    assert not _index_verdicts(_tmp, CONDITIONAL_INDEX)


def test_a_string_the_folder_can_fold_is_refused_like_a_literal(_tmp):
    """The constant folder decides the string axis, and it folds a
    concatenation of literals and a field-less f-string to the same constant
    a literal already is; the clean row is the declared limit beside them,
    read here so the three refusals are not a rule that refuses every
    string it meets.
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
    block. Both halves ride in one tree, so a rule that stopped marking
    tails adds `pkg/leaf.py` and a rule that over-reached drops
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


def test_every_claimed_block_field_is_read_as_a_block(_tmp):
    """Every statement-list field the grammar declares is held, both ways.

    The claimed set is DERIVED from the ast declaration, never from
    `_blocks` -- a derivation through `_blocks` would shrink silently when
    the walk narrows. `body`, `orelse` and `finalbody` are the only field
    names the grammar gives statement lists, and the default-value check
    drops the single-expression `body` of Expression, Lambda and IfExp.
    Each row holds its field twice on one probe tree, the field under
    proof carrying [raise, probe-call] in the position a real module
    carries it: the live read marks the probe dead, and with exactly that
    field dropped from the read (`ast.iter_fields` patched, so the mutant
    is that field alone and cannot drift from the real predicate) the
    probe is reachable again. Failures are COLLECTED, because one narrowed
    field names every row it silences.
    """
    del _tmp
    quiet = []
    for cls, field in _claimed_block_fields():
        root, call = _block_probe(cls, field)
        if call not in _mcp_dead_code.dead_nodes(root):
            quiet.append(f'{cls.__name__}.{field}: live read leaves it '
                         'reachable')
        root, call = _block_probe(cls, field)
        with _field_unread(cls, field):
            if call in _mcp_dead_code.dead_nodes(root):
                quiet.append(f'{cls.__name__}.{field}: fieldless read '
                             'marks it dead')
    assert not quiet, '; '.join(quiet)


def test_the_enumeration_covers_every_arm_the_analysers_spell(_tmp):
    """Every site the analysers can raise has a row, on both sides.

    The module set is DERIVED (`analyser_modules`), the sites are read off
    each module's parse, and each set is compared with its table both ways:
    a site with no row fails, a row whose site is gone fails. A count sits
    beside each comparison because two wrong answers of the same size
    satisfy a set equality, and the FLOOR has its own table and count for
    the reason its sites were absorbed by a set of function names.
    `CONTROL_LEAF` is in the component only because the walk follows the
    one `__import__` call at the foot of `CONTROL`; a derivation that closed
    over static imports, or recognised a raiser by the letters its call is
    written with, reports half a control and this says which half.
    The deep-chain arm drives the walk's own recursion limit (site `None`).
    `_refuse_default` is one derived site and three rows:
    the message names the thing handed away through `_HIDDEN_NOUN`.
    """
    del _tmp
    sources = {'control': CONTROL, 'control_leaf': CONTROL_LEAF}
    assert _mcp_arm_derivation.component(sources, 'control') \
        == set(sources), sorted(sources)
    for stem, name, table in (('control', 'control.py', CONTROL_SITES),
                              ('control_leaf', 'control_leaf.py',
                               CONTROL_LEAF_SITES)):
        found = sorted(_mcp_arm_derivation.arm_sites(sources[stem], name))
        assert found == sorted(table), (
            f'{name}: the control\'s arms are {found}, so the comparison '
            'below cannot be trusted to fail on an arm it missed')
    modules = {path.name: path
               for path in _mcp_arm_derivation.analyser_modules()}
    assert sorted(modules) == sorted(COVERED), sorted(modules)
    for name, table in sorted(COVERED.items()):
        derived = _mcp_arm_derivation.arm_sites(
            modules[name].read_text(encoding='utf-8'), name)
        claimed = sorted({key for key, *_ in table})
        assert sorted(derived) == claimed, (
            f'{name}: the derived sites are {sorted(derived)}')
        assert len(derived) == len(claimed), (name, len(derived), len(claimed))
    assert len(ARMS) == 15, len(ARMS)
    assert len(UNDRAWN) == 4, len(UNDRAWN)
    assert len(FLOOR_ARMS) == 4, len(FLOOR_ARMS)
    assert len(FIXTURE_ARMS) == 1, len(FIXTURE_ARMS)
    assert len(CONTROL_SITES) == 6, len(CONTROL_SITES)
    assert len(CONTROL_LEAF_SITES) == 4, len(CONTROL_LEAF_SITES)


def _read(verdict, detail):
    """One scan answer in the three words the index cases read: `refused`,
    or `resolved` when the scan set carries the leaf module and `silent`
    when it does not."""
    if verdict == 'refused':
        return 'refused'
    return 'resolved' if 'pkg/leaf.py' in detail else 'silent'


def _answered(_tmp, files):
    """`_scan_verdict`'s answer in the three words the callee cases read.

    One reader for both directions, so a control states the arm it enters
    and the neighbour it must leave alone with the same call and one shape of
    answer. This classification used to live in the shared fixtures bolted to
    a single callee composition, the one shape no second case could reuse.
    """
    return _read(*_scan_verdict(_tmp, files))


def _index_verdicts(_tmp, rows):
    """Each row of an index table answered in those three words, and the ARM
    a row that must refuse read against for the phrase it names.

    The failures are COLLECTED, for the reason the arm cases collect them: a
    rule that moved a decision has moved it for more than one row.
    """
    wrong = []
    for source, spoken, phrase in rows:
        verdict, detail = _scan_verdict(
            _tmp, {'composition.py': source, **LEAF_TREE})
        wrong_arm = phrase and phrase not in detail
        if _read(verdict, detail) != spoken or wrong_arm:
            wrong.append(f'{spoken}: {verdict}: {detail}')
    assert not wrong, '; '.join(wrong)


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


def _claimed_block_fields():
    """Every (carrier, field) pair the grammar declares a statement list.

    One class in the ast module at a time: a pair is claimed when the
    grammar names the field `body`, `orelse` or `finalbody` and a bare
    instance's value for it is a list -- which is what keeps the
    single-expression `body` of Expression, Lambda and IfExp out.
    """
    claimed = []
    for cls in {value for value in vars(ast).values()
                if isinstance(value, type) and issubclass(value, ast.AST)}:
        for field in cls._fields:
            if field not in _BLOCK_FIELD_NAMES:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', DeprecationWarning)
                if isinstance(getattr(cls(), field, None), list):
                    claimed.append((cls, field))
    return sorted(claimed, key=lambda pair: (pair[0].__name__, pair[1]))


def _block_probe(cls, field):
    """One compilable tree whose `field` holds [raise, probe-call].

    Other fields get what the compiler and the walk's own `_leaves` ask of
    them: a non-proof statement list carries [Pass()] (the compiler
    refuses empty bodies, and `_leaves` reads an if-with-else's last body
    statement), the wrappers put the carrier where a real module carries
    it -- a handler in a `try`, a case in a `match`, an async loop in an
    async def.
    """
    call = ast.Call(func=ast.Name(id='probe', ctx=ast.Load()), args=[],
                    keywords=[])
    root = _probe_root(
        cls, _fill(cls, field, [ast.Raise(), ast.Expr(value=call)]))
    ast.fix_missing_locations(root)
    compile(root, '<probe>', 'single' if cls is ast.Interactive else 'exec')
    return root, call


def _fill(cls, field, body):
    """One carrier whose `field` holds `body`, every other field filled."""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        node = cls()
    for name in cls._fields:
        setattr(node, name, body if name == field
                else _field_filler(cls, name))
    return node


def _probe_root(cls, node):
    """The root the carrier compiles as, in the wrapper a module carries."""
    if cls in (ast.Module, ast.Interactive):
        return node
    if cls is ast.ExceptHandler:
        node = ast.Try(body=[ast.Pass()], handlers=[node], orelse=[],
                       finalbody=[])
    elif cls is ast.match_case:
        node = ast.Match(subject=ast.Constant(None), cases=[node])
    if cls in (ast.AsyncFor, ast.AsyncWith):
        node = _fill(ast.AsyncFunctionDef, 'body', [node])
    return ast.Module(body=[node], type_ignores=[])


def _field_filler(cls, name):
    """What a non-proof field carries: its filler, or the empty list."""
    if name in _SCALAR_FILL:
        return _SCALAR_FILL[name]()
    if (cls, name) in _LIST_FILL:
        return _LIST_FILL[(cls, name)]()
    if name in _BLOCK_FIELD_NAMES:
        return [ast.Pass()]
    return []


def _arguments():
    return ast.arguments(posonlyargs=[], args=[], vararg=None,
                         kwonlyargs=[], kw_defaults=[], kwarg=None,
                         defaults=[])


_BLOCK_FIELD_NAMES = ('body', 'orelse', 'finalbody')

_SCALAR_FILL = {
    'name': lambda: 'p',
    'args': _arguments,
    'returns': lambda: None,
    'test': lambda: ast.Constant(True),
    'target': lambda: ast.Name(id='t', ctx=ast.Store()),
    'iter': lambda: ast.Constant(()),
    'subject': lambda: ast.Constant(None),
    'pattern': lambda: ast.MatchAs(name=None, pattern=None),
    'guard': lambda: None,
    'type_comment': lambda: None,
    'type': lambda: ast.Name(id='Exception', ctx=ast.Load()),
}

_LIST_FILL = {
    (ast.Try, 'handlers'): lambda: [ast.ExceptHandler(
        type=ast.Name(id='Exception', ctx=ast.Load()), name=None,
        body=[ast.Pass()])],
    (ast.TryStar, 'handlers'): lambda: [ast.ExceptHandler(
        type=ast.Name(id='Exception', ctx=ast.Load()), name=None,
        body=[ast.Pass()])],
    (ast.With, 'items'): lambda: [ast.withitem(
        context_expr=ast.Constant(None), optional_vars=None)],
    (ast.AsyncWith, 'items'): lambda: [ast.withitem(
        context_expr=ast.Constant(None), optional_vars=None)],
}


@contextlib.contextmanager
def _field_unread(cls, field):
    """The walk's read with exactly this field dropped from it.

    A wrapper over `ast.iter_fields`, so `_blocks`'s own predicate still
    decides what a block is and the mutant is that field alone dropped --
    not a reimplementation that could drift from the real predicate.
    """
    real = ast.iter_fields

    def without(node):
        for name, value in real(node):
            # pylint: disable-next=unidiomatic-typecheck
            if type(node) is cls and name == field:
                continue
            yield name, value

    ast.iter_fields = without
    try:
        yield
    finally:
        ast.iter_fields = real


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
