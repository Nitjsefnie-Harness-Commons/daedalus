#!/usr/bin/env python3
"""`match` case captures pair from the subject the way assignment does."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402
from test_tab_routing_collapse import PREFIX  # noqa: E402

MERGE_DICT = ('def choose():\n'
              '    if args.flag: return {"k": relay()}\n'
              '    return {"j": 1}\n')
MERGE_DICT_B = ('def choose():\n'
                '    if args.flag: return {"k": relay()}\n'
                '    return {"k": ordinary}\n')
CLASS = ('class C:\n'
         '    __match_args__ = ("fn",)\n'
         '    def __init__(self, fn): self.fn = fn\n')
ATTR_KEY = 'class K:\n    k = "j"\n'
# A tab-accepting routed callable the runtime can call with a tab keyword.
ROUTED = ('def routed(tab=None):\n'
          '    return ext_cmd("_focus", "focus-tab", tab=tab) \\\n'
          '        if tab is not None else None\n'
          'def make_routed():\n    return routed\n')
HOLE = 'mystery = locals()["make_routed"]()\n'
MERGE_SEQ = ('def choose():\n'
             '    if args.flag: return pair()\n'
             '    return (relay(),)\n')
# A dict merge whose branches disagree: the first is benign, the second
# routed. Reading only the first branch would stay clean, so a row over this
# moves under a first-branch-only mutant (I2 / code-M2).
MERGE_DISAGREE = ('def choose():\n'
                  '    if args.flag: return {"k": ordinary}\n'
                  '    return {"k": routed}\n')
# A dict whose key the guard cannot fold, so it lives under DYNAMIC_KEY.
DYNKEY = 'd = {}\nd[chr(ord("z"))] = relay()\n'
DYNKEY_ORD = 'd = {}\nd[chr(ord("z"))] = ordinary\n'
# The assignment binder's hole, with a tab-accepting mystery so the row moves
# to (1, 1) under a shared-hole fix; the routed-lambda form would not.
ASSIGN_HOLE = (PREFIX + ROUTED
               + 'mystery = locals()["make_routed"]()\n'
               + 'a, b, c = [relay(), mystery, ordinary]\n'
               + 'send = ext_cmd\nb(tab=0)')


def guard_only_verdict(tmp, body):
    """Guard-only verdict (no runtime execution) for a body that would raise
    at runtime — a literal-None position, whose call is a TypeError there."""
    from _pyroute import py_tab_routing_violations
    from _repo import ROOT
    source = (ROOT / 'daedalus_cli' / 'commands_browser.py').read_text(
        encoding='utf-8')
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(
        node, ast.FunctionDef) and node.name == 'do_focus_tab')
    function.body = ast.parse(body).body
    ast.fix_missing_locations(tree)
    mutated = Path(tmp) / 'guard_only.py'
    mutated.write_text(ast.unparse(tree) + '\n', encoding='utf-8')
    return len(py_tab_routing_violations(mutated, mutated.name))


def case(subject, pattern, call, guard='', extra=''):
    return (PREFIX + extra + f'match {subject}:\n    case {pattern}{guard}:\n'
            f'        send = ext_cmd\n        {call}\n')


def _rows():
    """(label, body, expected) triples. Every reported spelling has a clean
    twin and every clean twin has a row that would move if the element moved.
    The guard-only cost row has no runtime twin (its call is a TypeError), so
    it is the one reported row without one.
    A row whose comment says `fail-closed` records the guard reporting an
    unprovable position the runtime does not reach; a row whose comment says
    `known gap` pins a measured limitation this round did not close."""
    r = [
        # The issue's reported spelling and its clean twin.
        ('issue-sequence', case('list(pair())', '[x, y]', 'x()'), (1, 1), ''),
        ('issue-twin', case('list(pair())', '[x, y]', 'y()'), (0, 0), ''),
        ('tuple-pattern', case('pair()', '(x, y)', 'x()'), (1, 1), ''),
        ('bare-capture', case('relay()', 'v', 'v()'), (1, 1), ''),
        ('bare-value', case('relay()', 'v', 'ordinary()'), (0, 0), ''),
        ('wildcard', case('relay()', '_', 'ordinary()'), (0, 0), ''),
        # A star binds the remaining items as a real container.
        ('star-prefix',
         case('[ordinary, relay()]', '[x, *rest]', 'rest[0]()'), (1, 1), ''),
        ('star-suffix', case('[relay(), ordinary]', '[*rest, y]',
                             'rest[0]()'), (1, 1), ''),
        ('star-twin', case('[ordinary, relay()]', '[x, *rest]', 'x()'),
         (0, 0), ''),
        # A bare star occupies its position and binds nothing; the name guard
        # that stops a `None` star name reaching a name-set is what pins the
        # round-0 crash (the target placeholder beside it is inert).
        ('bare-star', case('[relay(), 1]', '[handler, *_]', 'handler()'),
         (1, 1), 'F1'),
        ('bare-star-alone', case('[relay(), 1]', '[*_]', 'ordinary()'),
         (0, 0), 'F1'),
        ('bare-star-prefix', case('[relay(), 1]', '[x, *_]', 'x()'),
         (1, 1), 'F1'),
        # A nested capture pairs with the nested position, not the top one.
        ('nested', case('[pair()]', '[[x, y]]', 'x()'), (1, 1), ''),
        ('nested-twin', case('[pair()]', '[[x, y]]', 'y()'), (0, 0), ''),
        # `sub as name` binds both the sub-pattern and the name.
        ('pattern-as-name', case('pair()', '[a, b] as whole', 'a()'),
         (1, 1), ''),
        ('sub-as-name-in-sequence', case('[pair()]', '[[a, b] as inner]',
                                         'a()'), (1, 1), ''),
        # A guard clause does not change the binding.
        ('guard', case('list(pair())', '[x, y]', 'x()', ' if args.flag'),
         (1, 1), ''),
        ('guard-twin', case('list(pair())', '[x, y]', 'y()', ' if args.flag'),
         (0, 0), ''),
        # A wildcard holds its position without binding.
        ('wildcard-position', case('[ordinary, relay()]', '[_, x]', 'x()'),
         (1, 1), ''),
        ('wildcard-position-twin', case('[relay(), ordinary]', '[_, x]',
                                        'x()'), (0, 0), ''),
        # A case that cannot match leaves its captures unpaired.
        ('cannot-match-length', case('(relay(), ordinary, ordinary)', '[x, y]',
                                     'x()'), (0, 0), ''),
        ('cannot-match-nested', case('[relay(), ordinary]', '[[a], b]',
                                     'a()'), (0, 0), ''),
        # A merge the subject does not decide reports, fail-closed.
        ('merge-undecided', case('choose()', '[x, y]', 'x()',
                                 extra=MERGE_SEQ), (1, 1), ''),
        # A mapping pattern pairs a literal key; `**rest` is the remainder.
        ('mapping-key', case('{"k": relay()}', '{"k": v}', 'v()'), (1, 1), ''),
        ('mapping-cannot-match', case('{"j": relay()}', '{"k": v}', 'v()'),
         (0, 0), ''),
        ('mapping-rest',
         case('{"a": 1, "k": relay()}', '{"a": z, **rest}', 'rest["k"]()'),
         (1, 1), ''),
        # A merge subject pairs the key to the merge of its branches.
        ('mapping-merge',
         case('choose()', '{"k": v}', 'v()', extra=MERGE_DICT), (1, 1), 'F4'),
        ('mapping-merge-2',
         case('choose()', '{"k": v}', 'v()', extra=MERGE_DICT_B), (1, 1),
         'F4'),
        # An or-pattern: agreeing alternatives keep the value; disagreeing
        # ones leave the position unprovable (never last-write-wins).
        ('or-agree-routed', case('[relay()]', '[x] | [x]', 'x()'), (1, 1),
         'F3'),
        ('or-agree-clean', case('[ordinary]', '[x] | [x]', 'x()'), (0, 0),
         'F3'),
        ('or-swap-routed', case('[ordinary, relay()]', '[x, y] | [y, x]',
                                'y()'), (1, 1), 'F3'),
        ('or-swap-fail-closed', case('[ordinary, relay()]', '[x, y] | [y, x]',
                                     'x()'), (0, 1),
         'F3 fail-closed: x is ambiguous, the runtime took the other '
         'alternative'),
        # A value pattern that the subject cannot satisfy binds nothing.
        ('value-as-capture', case('relay()', '5 as v', 'v()'), (0, 0), 'F5'),
        # The singleton half of the same value-test rule (N3).
        ('none-as-capture', case('relay()', 'None as v', 'v()'), (0, 0), 'N3'),
        # A singleton binds nothing and reads clean.
        ('singleton', case('relay()', 'None', 'ordinary()'), (0, 0), 'F6'),
        # Pins this arm's known limitation: a class capture names an
        # attribute reached through the subject's class, which the binder
        # does not model, so it reads clean. Filed as issue 1003.
        ('class-capture-known-gap', case('C(relay())', 'C(fn)', 'fn()',
                                         extra=CLASS), (1, 0),
         'known gap: class pattern unmodelled, issue 1003'),
        # A key the guard cannot resolve leaves the slot unprovable; the
        # unprovable marker catches a `tab=`-keyword call, so the routed
        # lambda form reads clean here too (same limitation as the hole).
        ('attr-key-known-gap', case('{"j": relay()}', '{K.k: v}', 'v()',
                                    extra=ATTR_KEY), (1, 0),
         'known gap: attribute key unresolved'),
        # N2: the unresolvable-key marker fires on a live dict subject when
        # the slot is called with a tab keyword; the v() twin stays clean.
        ('attr-key-tab', case('{"j": routed}', '{K.k: v}', 'v(tab=0)',
                              extra=ATTR_KEY + ROUTED), (1, 1), 'N2'),
        ('attr-key-tab-twin', case('{"j": routed}', '{K.k: v}', 'v()',
                                   extra=ATTR_KEY + ROUTED), (0, 0), 'N2'),
        # N1: a provably-dead case (mapping pattern over a sequence subject)
        # reads clean, because the subject test is hoisted above the loop.
        ('attr-key-cannot-match', case('[1, 2]', '{K.k: v}', 'v(tab=0)',
                                       extra=ATTR_KEY), (0, 0), 'N1'),
        # F2a: an undecidable match position binds unprovable, so a tab-
        # keyword call through it is reported; the v() twin stays clean.
        ('hole-tab', case('[relay(), mystery, ordinary]', '[a, b, c]',
                          'b(tab=0)', extra=ROUTED + HOLE), (1, 1), 'F2a'),
        ('hole-twin', case('[relay(), mystery, ordinary]', '[a, b, c]',
                           'b()', extra=ROUTED + HOLE), (0, 0), 'F2a'),
        # The assignment binder's hole is pre-existing and unfixed here; it is
        # filed separately (value-model change). The mystery is tab-accepting
        # so the row reads (1, 1) under a shared-hole fix and discriminates.
        ('assignment-hole-known-gap', ASSIGN_HOLE, (1, 0),
         'known gap: assignment binder hole, filed #1010'),
        # C1: a dynamic-keyed subject folds the DYNAMIC_KEY entry into the
        # lookup, so a literal-key read must not read "no such key".
        ('dynkey-match', case('d', '{"z": v}', 'v()', extra=DYNKEY),
         (1, 1), 'C1'),
        ('dynkey-twin', case('d', '{"z": v}', 'ordinary()',
                             extra=DYNKEY_ORD), (0, 0), 'C1'),
        # I1-spec: a listed key no branch carries means the case cannot run,
        # so `**rest` is not paired either.
        ('map-rest-kw', case('{"k": relay()}', '{"n": z, **r}',
                             'r["k"]()'), (0, 0), 'I1spec'),
        # I2 / code-M2: branches disagree (benign first, routed second), so
        # reading only the first branch would read clean; the row moves under
        # a first-branch-only mutant. Fail-closed: runtime takes the benign
        # branch, the guard cannot decide, so it reports.
        ('merge-discriminating', case('choose()', '{"k": v}', 'v()',
                                      extra=ROUTED + MERGE_DISAGREE),
         (0, 1), 'I2 fail-closed: branches disagree, runtime took benign'),
        # I3: a star binds a real remaining-items container.
        ('star-alone-container', case('[relay(), ordinary]', '[*rest]',
                                      'rest[0]()'), (1, 1), 'I3'),
        # Section 6: a value pattern over a position the guard cannot
        # compare fails closed; runtime 0, reported. Disclosed, not suppressed.
        ('seq-value-in-seq', case('[relay(), ordinary]', '[x, 5]', 'x()'),
         (0, 1), 'section-6 fail-closed: guard cannot prove 5 != position'),
        # Clean twins for reported rows that lacked one.
        ('star-suffix-twin', case('[relay(), ordinary]', '[*rest, y]',
                                  'y()'), (0, 0), ''),
        ('pattern-as-name-twin', case('pair()', '[a, b] as whole', 'b()'),
         (0, 0), ''),
        ('sub-as-name-twin', case('[pair()]', '[[a, b] as inner]', 'b()'),
         (0, 0), ''),
        ('merge-undecided-twin', case('choose()', '[x, y]', 'y()',
                                      extra=MERGE_SEQ), (0, 0), ''),
        ('mapping-key-twin', case('{"k": relay()}', '{"k": v}',
                                  'ordinary()'), (0, 0), ''),
        ('mapping-rest-twin', case('{"a": 1, "k": relay()}',
                                   '{"a": z, **rest}',
                                   'ordinary()'), (0, 0), ''),
        ('mapping-merge-twin', case('choose()', '{"k": v}', 'ordinary()',
                                    extra=MERGE_DICT), (0, 0), ''),
        ('bare-star-twin', case('[relay(), 1]', '[handler, *_]',
                                'ordinary()'), (0, 0), ''),
    ]
    return r


def test_guard_only_cost_rows(tmp):
    """Rows whose call is a runtime TypeError, so they are measured guard-only.
    A literal-None position binds unprovable; calling it with a tab reports
    even though the runtime call would raise. That is the accepted
    fail-closed cost of the match-local hole fix (F2a); the routed-lambda
    form of the same hole is pinned by the F2 issue, not here."""
    rows = [
        ('literal-none-tab', PREFIX + 'match [relay(), None]:\n'
         '    case [a, b]:\n        send = ext_cmd\n        b(tab=0)', 1),
    ]
    observed = [(label, guard_only_verdict(tmp, body))
                for label, body, _ in rows]
    expected = [(label, value) for label, _, value in rows]
    assert observed == expected, observed


def test_match_capture_pairing(tmp):
    rows = _rows()
    observed = [(label, *_tracked_focus_verdict(tmp, body, counts=True))
                for label, body, _, _ in rows]
    expected = [(label, *value) for label, _, value, _ in rows]
    assert observed == expected, observed


_HANDLED = ('MatchSequence', 'MatchAs', 'MatchStar', 'MatchOr',
            'MatchMapping', 'MatchValue')
_FALLTHROUGH = ('MatchClass', 'MatchSingleton')


def test_pattern_node_sweep(tmp):
    """Sweep the pattern node types the listed grammar forms produce, keyed
    on type(node).__name__ rather than on any spelling in the table, and fail
    on any such type the binder neither names nor routes to its fall-through.
    Scope: a node type none of the listed forms produces would not enter the
    sweep, so this is not a claim about a future node type."""
    assert tmp is not None
    grammar = ('case 1', 'case None', 'case x', 'case [a] as w', 'case [*r]',
               'case [a, b]', 'case {"k": v}', 'case [a] | [b]', 'case C(a)')
    seen = set()
    for body in grammar:
        statement = ast.parse(f'match o:\n    {body}: pass').body[0]
        for node in ast.walk(statement):
            if (type(node).__name__.startswith('Match')
                    and node is not statement):
                seen.add(type(node).__name__)
    unreached = seen - set(_HANDLED) - set(_FALLTHROUGH)
    assert not unreached, f'no binder route: {unreached}'
    assert seen == set(_HANDLED) | set(_FALLTHROUGH), sorted(seen)


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='match_')


if __name__ == '__main__':
    raise SystemExit(main())
