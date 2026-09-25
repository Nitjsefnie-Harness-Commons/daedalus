#!/usr/bin/env python3
"""Taking a fix's site scope away again.

A stored scope could be set and never removed: an absent `match` is read as
"keep the scope this fix already has", which is the right ruling for a store
that means "update the code" and leaves an operator with no way to widen a
fix again short of clearing it and retyping the source.

The clear cannot be spelled as a value in the scope's own slot. The store
refuses a pattern it cannot parse, and it must: a scope the operator
believes exists and does not is worse than a visible refusal. An empty
pattern is therefore swallowed by that guard and reads as "nothing changed" —
indistinguishable, on the wire and in the answer, from the very outcome the
operator was trying to leave. So the clear is a boolean on its own field,
beside `match` and never in it, and these controls read what each of the four
instructions does to the record and to the pages the fix then runs on.

The vocabulary and the browser are the ones `test_hotfix_scope.py` already
settles; only the store's four cases are new.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _hotfixharness import run_hotfix_case  # noqa: E402
from test_hotfix_scope import (  # noqa: E402
    ELSEWHERE, FILE_SCOPE, FIX, SCOPE, SITE, _delivered, _errors, _logs)


# `store-hotfix` already carries `permanent` as a boolean that leaves an
# existing value alone when it is absent, so the clear is that same shape for
# the other field on the record.
CLEAR_FIELD = 'clearScope'


def test_a_cleared_scope_runs_the_fix_where_the_scope_had_excluded_it(tmp):
    """C12: a scope can be removed, and the fix runs again where it could not.

    A scope that can only be set is not a boundary an operator controls; the
    only route back to an unscoped fix would be a clear followed by a fresh
    store, which throws the operator's code away with the scope. So the page
    the scope had EXCLUDED is where the property is read — a page the scope
    allowed would run the fix either way.
    """
    del tmp
    seeded = {
        'documents': [ELSEWHERE], 'current': 0, 'asker': 0,
        'fixes': [{'id': 'fix1', 'code': FIX, 'match': SCOPE}],
    }
    cleared = run_hotfix_case(dict(seeded, store=[
        {'id': 'store-clear', 'fixId': 'fix1', 'code': FIX,
         CLEAR_FIELD: True},
    ]))
    # The anti-vacuity half: the same seeded record, the same page, the same
    # fix, with no store at all — the scope still keeps the fix off. So the
    # delivery below is the clear's doing and not a page that ran everything.
    scoped = run_hotfix_case(dict(seeded, store=[]))
    assert _delivered(scoped) == {}, scoped
    assert 'skipped 1 hotfix(es) by site scope' in ' '.join(_logs(scoped)), (
        scoped)

    posted = {row['id']: row for row in cleared['posted']}
    assert posted['store-clear']['error'] is None, cleared
    assert posted['store-clear']['result']['match'] is None, cleared
    # The flag the fix already had survives the clear; a store that rebuilt
    # the record from the command alone would demote this permanent fix.
    assert posted['store-clear']['result']['permanent'] is True, cleared
    row = cleared['record'][0]
    assert (row['id'], row['code'], row['permanent']) == (
        'fix1', FIX, True), cleared
    # Nothing is left in the record's scope position — not the clear's
    # spelling, not an empty pattern.
    assert 'match' not in row, cleared
    assert cleared['stored'] == [{'id': 'fix1', 'match': None}], cleared
    assert _delivered(cleared) == {'doc-1': ['fix1']}, cleared
    assert not [line for line in _logs(cleared)
                if 'by site scope' in line], cleared
    assert not _errors(cleared), cleared


def test_the_clear_spelling_is_refused_in_the_scope_slot(tmp):
    """C13: nothing about the clear can be stored as a scope.

    The clear is a boolean on its own field, so the honest way to pin that it
    can never become a scope is to offer its value in the scope's own slot:
    a boolean is not a Chrome match pattern, and the store that refuses it
    leaves the record it would have replaced exactly as it was. Without this
    the property above could be met by a clear that doubles as a scope value.
    """
    del tmp
    seed = {'id': 'fix1', 'code': FIX, 'match': SCOPE, 'permanent': False}
    as_scope = {'id': 'store-as-scope', 'fixId': 'fix1',
                'code': 'clobbered', 'match': True}
    refused = run_hotfix_case({
        'documents': [SITE], 'ask': False, 'fixes': [seed],
        'store': [as_scope],
    })
    assert refused['record'] == [seed], refused
    assert refused['posted'][0]['error'], refused
    assert refused['posted'][0]['result'] is None, refused
    # The anti-vacuity half: the same fix, the same slot, a pattern that
    # parses. So the refusal above reads the value and not the field, and no
    # stored scope is a spelling the clear introduced.
    accepted = run_hotfix_case({
        'documents': [SITE], 'ask': False, 'fixes': [seed],
        'store': [as_scope,
                  {'id': 'store-pattern', 'fixId': 'fix1', 'code': 'kept',
                   'match': FILE_SCOPE}],
    })
    row = accepted['record'][0]
    assert (row['code'], row['match']) == ('kept', FILE_SCOPE), accepted
    assert not isinstance(row['match'], bool), accepted


def test_an_absent_scope_is_kept_and_a_clear_is_not_swallowed_by_it(tmp):
    """C14: absence means keep and the clear means remove, and neither reads
    as the other.

    The store's fail-closed refusal is the trap this task exists to avoid: a
    clear spelled as an empty pattern would be refused as unparseable, which
    is indistinguishable from "the operator changed their mind and left the
    scope alone". So both instructions are driven on one fix id in one record
    — the scope a store does not mention, the scope a clear removes, the
    absence that follows a clear (which must not resurrect the old scope),
    and a store that does name a scope (the anti-vacuity half: without it,
    "keeps" is satisfied by a store that never updates the scope at all).
    """
    del tmp
    outcome = run_hotfix_case({
        'documents': [SITE],
        'ask': False,
        'fixes': [{'id': 'fix1', 'code': FIX, 'match': SCOPE,
                   'permanent': False}],
        'store': [
            {'id': 'store-absent', 'fixId': 'fix1', 'code': '2'},
            {'id': 'store-clear', 'fixId': 'fix1', 'code': '3',
             CLEAR_FIELD: True},
            {'id': 'store-absent-again', 'fixId': 'fix1', 'code': '4'},
            {'id': 'store-rescoped', 'fixId': 'fix1', 'code': '5',
             'match': FILE_SCOPE},
        ],
    })
    posted = {row['id']: row for row in outcome['posted']}
    assert [row['error'] for row in outcome['posted']] == [None] * 4, outcome
    assert posted['store-absent']['result']['match'] == SCOPE, outcome
    assert posted['store-clear']['result']['match'] is None, outcome
    assert posted['store-absent-again']['result']['match'] is None, outcome
    assert posted['store-rescoped']['result']['match'] == FILE_SCOPE, outcome
    # The code advanced at every store: a clear removes the scope, never the
    # fix.
    assert outcome['record'][0]['code'] == '5', outcome
    assert outcome['stored'] == [{'id': 'fix1', 'match': FILE_SCOPE}], outcome


def test_a_clear_alongside_a_pattern_is_refused(tmp):
    """Two instructions that contradict are refused, not silently ranked.

    Naming a scope and asking for it gone at the same time is not a spelling
    this command has, and honouring either one is a decision the operator did
    not make: the losing half is a scope they believe is set. A refusal
    leaves the record alone and says why.
    """
    del tmp
    seed = {'id': 'fix1', 'code': FIX, 'match': SCOPE, 'permanent': False}
    outcome = run_hotfix_case({
        'documents': [SITE], 'ask': False, 'fixes': [seed],
        'store': [{'id': 'store-both', 'fixId': 'fix1', 'code': 'clobbered',
                   'match': FILE_SCOPE, CLEAR_FIELD: True}],
    })
    assert outcome['record'] == [seed], outcome
    assert outcome['posted'][0]['error'], outcome
    assert outcome['posted'][0]['result'] is None, outcome


def test_an_unparseable_pattern_is_still_refused_with_the_record_intact(tmp):
    """C14, refusal half: the guard the clear must not swallow.

    A pattern the store cannot parse is still refused, and the refusal lands
    before the record is read, so the scope and the code it would have
    replaced are both still there. The acceptance half is the anti-vacuity: a
    store that never wrote anything at all would leave the record untouched
    too, and would pass this control's first half.
    """
    del tmp
    seed = {'id': 'fix1', 'code': FIX, 'match': SCOPE, 'permanent': False}
    bad = {'id': 'store-bad', 'fixId': 'fix1', 'code': 'clobbered',
           'match': 'ht!tp:/nonsense'}
    refused = run_hotfix_case({
        'documents': [SITE], 'ask': False, 'fixes': [seed], 'store': [bad],
    })
    assert refused['record'] == [seed], refused
    assert refused['posted'][0]['error'], refused
    assert refused['posted'][0]['result'] is None, refused
    accepted = run_hotfix_case({
        'documents': [SITE], 'ask': False, 'fixes': [seed],
        'store': [bad,
                  {'id': 'store-pattern', 'fixId': 'fix1', 'code': 'kept',
                   'match': FILE_SCOPE}],
    })
    row = accepted['record'][0]
    assert (row['code'], row['match']) == ('kept', FILE_SCOPE), accepted


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='hotfixclear_')


if __name__ == '__main__':
    raise SystemExit(main())
