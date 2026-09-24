#!/usr/bin/env python3
"""`match` case captures pair from the subject the way assignment does."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from test_tab_routing import _tracked_focus_verdict  # noqa: E402
from test_tab_routing_collapse import PREFIX  # noqa: E402


def routed_case(subject, pattern, call, guard=''):
    return (PREFIX + f'match {subject}:\n    case {pattern}{guard}:\n'
            f'        send = ext_cmd\n        {call}\n')


def _rows():
    return [
        # The issue's reported spelling: a sequence capture reaches the
        # routed callable, and its clean twin calls the other position.
        ('issue-sequence', 'list(pair())', '[x, y]', 'x()', '', (1, 1)),
        ('issue-twin', 'list(pair())', '[x, y]', 'y()', '', (0, 0)),
        ('tuple-pattern', 'pair()', '(x, y)', 'x()', '', (1, 1)),
        ('bare-capture', 'relay()', 'v', 'v()', '', (1, 1)),
        ('bare-value', 'relay()', 'v', 'ordinary()', '', (0, 0)),
        ('wildcard', 'relay()', '_', 'ordinary()', '', (0, 0)),
        # A star binds the remaining items as a real container.
        ('star-prefix', '[ordinary, relay()]', '[x, *rest]',
         'rest[0]()', '', (1, 1)),
        ('star-suffix', '[relay(), ordinary]', '[*rest, y]',
         'rest[0]()', '', (1, 1)),
        ('star-twin', '[ordinary, relay()]', '[x, *rest]', 'x()', '', (0, 0)),
        # A nested capture pairs with the nested position, not the top one.
        ('nested', '[pair()]', '[[x, y]]', 'x()', '', (1, 1)),
        ('nested-twin', '[pair()]', '[[x, y]]', 'y()', '', (0, 0)),
        # `sub as name` binds both the sub-pattern and the name.
        ('pattern-as-name', 'pair()', '[a, b] as whole', 'a()', '', (1, 1)),
        ('sub-as-name-in-sequence', '[pair()]', '[[a, b] as inner]',
         'a()', '', (1, 1)),
        # A guard clause does not change the binding.
        ('guard', 'list(pair())', '[x, y]', 'x()', ' if args.flag', (1, 1)),
        ('guard-twin', 'list(pair())', '[x, y]', 'y()', ' if args.flag',
         (0, 0)),
        # A wildcard holds its position without binding.
        ('wildcard-position', '[ordinary, relay()]', '[_, x]',
         'x()', '', (1, 1)),
        ('wildcard-position-twin', '[relay(), ordinary]', '[_, x]',
         'x()', '', (0, 0)),
        # A case that cannot match leaves its captures unpaired: the subject
        # proves nothing lands at that position.
        ('cannot-match-length', '(relay(), ordinary, ordinary)', '[x, y]',
         'x()', '', (0, 0)),
        ('cannot-match-nested', '[relay(), ordinary]', '[[a], b]',
         'a()', '', (0, 0)),
        # A merge the subject does not decide reports, fail-closed.
        ('merge-undecided', 'choose()', '[x, y]', 'x()', '', (1, 1)),
        # A mapping pattern pairs each literal key; `**rest` is the rest.
        ('mapping-key', '{"k": relay()}', '{"k": v}', 'v()', '', (1, 1)),
        ('mapping-cannot-match', '{"j": relay()}', '{"k": v}',
         'v()', '', (0, 0)),
        ('mapping-rest', '{"a": 1, "k": relay()}', '{"a": z, **rest}',
         'rest["k"]()', '', (1, 1)),
        # An or-pattern offers every alternative the same subject.
        ('or-pattern', '[relay()]', '[x] | [x]', 'x()', '', (1, 1)),
        # A value pattern binds nothing.
        ('value-pattern', '5', '5', 'ordinary()', '', (0, 0)),
    ]


MERGE = ('def choose():\n'
         '    if args.flag: return pair()\n'
         '    return (relay(),)\n')


def test_match_capture_pairing(tmp):
    rows = _rows()
    prefix_extra = {'merge-undecided': MERGE}
    observed = []
    for label, subject, pattern, call, guard, expected in rows:
        body = prefix_extra.get(label, '') + routed_case(
            subject, pattern, call, guard)
        observed.append((label, *_tracked_focus_verdict(
            tmp, body, counts=True)))
    expected = [(label, *value) for label, _, _, _, _, value in rows]
    assert observed == expected, observed


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='match_')


if __name__ == '__main__':
    raise SystemExit(main())
