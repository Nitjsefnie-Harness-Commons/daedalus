#!/usr/bin/env python3
"""What each of the worker's hotfix commands answers, arm by arm.

`store-hotfix` and `clear-hotfix` had behavioural controls. The other three
handlers the worker publishes — `clear-all-hotfixes`, `set-permanent` and
`list-hotfixes` — had none, so every arm of them had run only in a browser.
An operator's clear-all that quietly kept every permanent fix, or a
`set-permanent` that answered success without touching the record, was a
line nobody had read. That is the gap issue 495 measured: a reachable line
is not a controlled one.

Every control drives the shipped worker the way the bridge does. The command
is dispatched by its `type` through `dispatchCommand` — the routing
`tests/_worker_routes.py` records — never called directly, because a handler
called directly is not reachable from a shipped client and a control over it
proves nothing about the one the bridge uses. The state each acts on was left
by a real producer: a stored fix, or an earlier command in the same case.
What is asserted is the result the worker POSTS and the record left in
storage afterwards.

Each refusal is pinned in both directions. Every guard has a row that trips
it and a row on the same field and the same record that must not, so a guard
that refused everything and a guard that never refused are each caught by one
half of the pair. The two halves that carry the most weight are the ones a
widening and a narrowing would each break: `clear-all-hotfixes` keeping a
permanent fix nobody asked it to keep, and `clear-hotfix` dropping a fix
nobody named.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _hotfixharness import run_hotfix_case  # noqa: E402
from _repo import EXTENSION_ROOT  # noqa: E402

SITE = 'https://shop.example.com/cart'
# A fix whose code a real page can act on, so a control that reads the record
# and a control that watches the delivery are reading the same stored value.
CODE = "daedalusHits.push('fix1')"
# The version the seeded record carries in every case here. A value no shipped
# `VERSION` can equal, so "the key is gone" and "a record is still there" are
# told apart by what `list-hotfixes` answers — stated, not inherited from two
# constants that happen to differ.
FIXTURE_VERSION = '0.00.0-fixture'
# What the double says when a case names both command keys. Carried here so
# the control can tell that refusal from any other way a case can fail; the
# double spells it independently, and a control that recomputed it from the
# double's text would pass on a double that stopped refusing for it.
BOTH_KEYS_REFUSAL = 'names both `commands` and `store`'


def _declared(source_path, name):
    """The value a `const NAME = 'literal';` in a shipped source declares."""
    source = source_path.read_text(encoding='utf-8')
    match = re.search(rf"const {name} = '([^']+)';", source)
    assert match, f'{source_path.name} declares no {name}'
    return match.group(1)


def _version():
    """The version the shipped worker stamps on a record it writes.

    Read out of the source rather than restated, so a worker that kept
    posting the version it was told at build time fails here instead of
    agreeing with a constant the test also carries.
    """
    return _declared(EXTENSION_ROOT / 'background.js', 'VERSION')


def _key():
    """The key the record lives under, read out of the module that reads it.

    A case plants a storage fault on this key and asserts the reason the
    browser gave, so a literal chosen here would let the test and the double
    agree with each other while the worker's own key went unnamed.
    """
    return _declared(EXTENSION_ROOT / 'worker' / 'hotfixes.js', 'HOTFIX_KEY')


def _run(commands, fixes=(), **case):
    """Drive the worker's command dispatch with no page asking for replay."""
    return run_hotfix_case(dict(
        {'documents': [SITE], 'ask': False, 'fixes': list(fixes)},
        commands=commands, **case))


def _rows(outcome):
    return {row['id']: row for row in outcome['posted']}


def _flags(outcome):
    """The `(id, permanent)` of every fix the record now holds."""
    return [(fix['id'], fix.get('permanent')) for fix in outcome['record']]


# A record with one fix the operator marked permanent and one they did not.
# `permanent` is stated on both rather than left to the harness's default,
# because "left to the default" and "permanent" are the same value here and
# only one of them is the flag a command reads.
MIXED = [{'id': 'kept', 'code': CODE, 'permanent': True},
         {'id': 'dropped', 'code': CODE, 'permanent': False}]
# The one command that takes a permanent fix with it. Shared by the cases
# that need a record to be ABSENT afterwards, so they reach that state
# through the worker rather than by not setting one up. The harness copies
# each command before dispatching it, so this is never written to.
DROP_RECORD = {'id': 'remove', 'type': 'clear-all-hotfixes',
               'includePermanent': True}


def test_a_store_naming_no_fix_id_or_no_code_is_refused(tmp):
    """The store refuses a command that cannot name a fix to write.

    Three ways to arrive with nothing to store, and they are the same refusal
    because they are the same fact: an absent `fixId` has nowhere to write,
    and an absent or empty `code` has nothing to write there. The refusal
    lands before the record is read, so the record the operator already had
    is untouched — and the admitted store in the second case is what proves
    the refusals above read the command rather than the record.
    """
    del tmp
    seed = [{'id': 'resident', 'code': CODE, 'permanent': False}]
    refusals = [
        {'id': 'no-fix-id', 'code': CODE},
        {'id': 'no-code', 'fixId': 'resident'},
        {'id': 'empty-code', 'fixId': 'resident', 'code': ''},
    ]
    refused = _run(refusals, seed)
    assert [row['id'] for row in refused['posted']] == [
        'no-fix-id', 'no-code', 'empty-code'], refused
    for row in refused['posted']:
        assert row['error'] == 'Missing fixId or code', refused
        assert row['result'] is None, refused
    assert _flags(refused) == [('resident', False)], refused
    # The anti-vacuity half: the same field, the same record, a command
    # naming both. A store that refused everything would leave this record
    # untouched too.
    accepted = _run(refusals + [
        {'id': 'complete', 'fixId': 'resident', 'code': '2'}], seed)
    row = _rows(accepted)['complete']
    assert row['error'] is None, accepted
    assert row['result']['stored'] == 'resident', accepted
    assert accepted['record'][0]['code'] == '2', accepted


def test_a_clear_naming_no_fix_id_is_refused(tmp):
    """The clear refuses rather than reporting a fix it never removed.

    `found: false` is a real answer for an id the record does not hold — that
    is a clear that did its work and found nothing. A command with no id at
    all is not that: the operator named nothing, so a success would claim a
    removal that cannot be checked.
    """
    del tmp
    seed = [{'id': 'resident', 'code': CODE, 'permanent': False}]
    refused = _run([{'id': 'no-fix-id', 'type': 'clear-hotfix'}], seed)
    assert refused['posted'][0]['error'] == 'Missing fixId', refused
    assert refused['posted'][0]['result'] is None, refused
    assert _flags(refused) == [('resident', False)], refused
    # A named id the record does not hold is a different case, and it is
    # answered rather than refused — so the refusal above reads the absent
    # field and not the absent fix. What it answers is the next row.
    absent = _run([{'id': 'unknown', 'type': 'clear-hotfix',
                    'fixId': 'never-stored'}], seed)
    assert absent['posted'][0]['error'] is None, absent
    assert _flags(absent) == [('resident', False)], absent


def test_a_clear_of_an_id_the_record_does_not_hold_says_so(tmp):
    """The two commands must agree about an id the record never held.

    `set-permanent` looks the fix up before answering and reports
    `found: false` for an id the record does not hold. `clear-hotfix` used to
    filter and answer `found: true` whatever it removed, so one command told
    an operator a fix was cleared and the other told them it was never there
    — and `daedalus-cli` prints both answers verbatim. #1185.

    The count is asserted as well as the flag, because `remaining` is what an
    operator reads to find out whether anything went: a clear that found
    nothing left the record's length exactly where it was.

    The second half is the state #1185's Expected Behavior names explicitly —
    no record at all. The clear answers that on its own early return, which
    is a different line from the one the fix changed, and a control that
    reached only the filter would leave it unheld. The record is taken away
    by the worker's own `includePermanent` clear, so the state is one the
    worker produced rather than one this case built.
    """
    del tmp
    outcome = _run([{'id': 'clear-misspelled', 'type': 'clear-hotfix',
                     'fixId': 'keptt'}], MIXED)
    row = _rows(outcome)['clear-misspelled']
    assert row['error'] is None, outcome
    # The record is what the operator would have to restore by hand, and it
    # is untouched.
    assert _flags(outcome) == [('kept', True), ('dropped', False)], outcome
    assert row['result'] == {'cleared': 'keptt', 'found': False,
                             'remaining': 2}, outcome
    no_record = _run([DROP_RECORD,
                      {'id': 'clear-no-record', 'type': 'clear-hotfix',
                       'fixId': 'kept'}], MIXED)
    row = _rows(no_record)['clear-no-record']
    assert row['error'] is None, no_record
    assert row['result'] == {'cleared': 'kept', 'found': False}, no_record
    assert no_record['record'] == [], no_record
    # The anti-vacuity half on the same state: the id IS in a record that
    # exists, so a handler that answered `found: false` for everything would
    # be caught by the first half rather than passing both.
    with_record = _run([{'id': 'clear-present', 'type': 'clear-hotfix',
                         'fixId': 'kept'}], MIXED)
    assert _rows(with_record)['clear-present']['result'] == {
        'cleared': 'kept', 'found': True, 'remaining': 1}, with_record


def test_a_clear_removes_the_fix_it_names_and_leaves_the_others(tmp):
    """The narrowing half: one named fix, and only that one.

    The clear filters the record on the id it was given. A filter that
    compared against anything else — a flag, a truthiness test, an undefined
    — would empty the record and answer a `remaining` no operator asked for,
    so both the named removal and the survival of its neighbour are asserted.
    """
    del tmp
    outcome = _run([{'id': 'clear-one', 'type': 'clear-hotfix',
                     'fixId': 'dropped'}], MIXED)
    row = _rows(outcome)['clear-one']
    assert row['error'] is None, outcome
    assert row['result'] == {'cleared': 'dropped', 'found': True,
                             'remaining': 1}, outcome
    assert _flags(outcome) == [('kept', True)], outcome


def test_a_read_the_store_refuses_answers_with_the_reason(tmp):
    """A clear that cannot read the record says so instead of reporting one.

    The lock is the only thing serialising these mutations, and it is held
    across the read, so a read Chrome refuses takes the command down the
    failure path rather than leaving a half-applied change to report. The
    operator gets the browser's own reason, and the record keeps the fixes
    the refusal never got to touch.
    """
    del tmp
    refused = _run([{'id': 'clear', 'type': 'clear-hotfix',
                     'fixId': 'dropped'}], MIXED,
                   storageReadFails=_key())
    assert refused['posted'][0]['error'] == (
        'storage read refused for ' + _key()), refused
    assert refused['posted'][0]['result'] is None, refused
    assert _flags(refused) == [('kept', True), ('dropped', False)], refused
    # The anti-vacuity half: the same clear, the same record, no fault.
    clear = _run([{'id': 'clear', 'type': 'clear-hotfix',
                   'fixId': 'dropped'}], MIXED)
    assert clear['posted'][0]['error'] is None, clear
    assert _flags(clear) == [('kept', True)], clear


def test_clear_all_keeps_the_permanent_fixes_and_drops_the_rest(tmp):
    """The widening half: a permanent fix nobody asked to clear survives.

    `permanent` is the flag that makes a fix outlive an extension upgrade, so
    a clear-all that ignored it would destroy the one kind of fix that cannot
    be recovered. The seeded pair is the only state that shows both halves at
    once: the permanent fix is still there, and the ordinary one is gone —
    which a handler that dropped everything, or kept everything, would each
    fail.
    """
    del tmp
    outcome = _run([{'id': 'clear-all', 'type': 'clear-all-hotfixes'}],
                   MIXED)
    row = _rows(outcome)['clear-all']
    assert row['error'] is None, outcome
    assert row['result'] == {'cleared': True, 'removed': 1, 'kept': 1}, outcome
    assert _flags(outcome) == [('kept', True)], outcome


def test_clear_all_with_include_permanent_removes_the_whole_record(tmp):
    """The one instruction that takes a permanent fix with it.

    Without the flag the record survives; with it the key goes, so a fix that
    could not be recovered by any other command is recoverable by this one.
    The two rows differ in one boolean on the command, and the records they
    leave are opposites — which is what pins the flag's meaning rather than
    the handler's presence.
    """
    del tmp
    kept = _run([{'id': 'without', 'type': 'clear-all-hotfixes'}], MIXED)
    assert _flags(kept) == [('kept', True)], kept
    cleared = _run([{'id': 'with', 'type': 'clear-all-hotfixes',
                     'includePermanent': True}], MIXED)
    row = _rows(cleared)['with']
    assert row['error'] is None, cleared
    assert row['result'] == {
        'cleared': True, 'includePermanent': True}, cleared
    assert cleared['record'] == [], cleared
    # The flag is read as `=== true`, so a value it cannot act on is an
    # ordinary clear rather than a silent clear of the permanent fixes.
    truthy = _run([{'id': 'truthy', 'type': 'clear-all-hotfixes',
                    'includePermanent': 'true'}], MIXED)
    assert truthy['posted'][0]['error'] is None, truthy
    assert truthy['record'] == [{'id': 'kept', 'code': CODE,
                                 'permanent': True}], truthy


def test_clear_all_of_a_record_with_nothing_to_keep_removes_the_key(tmp):
    """Two records with nothing to keep answer differently and both empty.

    A record whose fixes are all ordinary leaves nothing behind, so the key
    goes rather than a record holding an empty array; a record that was never
    written leaves nothing to remove at all. The second is reached through
    the first — one command takes the key away, the next finds it absent —
    so both are the real call site rather than a case that skipped setup.

    An empty record and an absent one are indistinguishable through the
    stored fixes, so the key going is read from what `list-hotfixes` answers
    afterwards: an absent record is answered with the worker's own version,
    while a record left behind carries the version that wrote it. That
    version is what `_eligibleHotfixes` compares to decide whether a
    non-permanent fix runs at all, so a leftover empty record keeps an old
    one gating replay for no reason.

    The seeded record is stamped with a version no shipped `VERSION` can
    equal, and the difference is asserted rather than left to two constants
    happening to diverge — the first assertion below is what makes the rest
    of this control mean anything.
    """
    del tmp
    assert FIXTURE_VERSION != _version(), (
        'the seeded version must differ from the worker\'s, or this control '
        'reads a record left behind as a key that is gone')
    ordinary = _run([{'id': 'clear-all', 'type': 'clear-all-hotfixes'},
                     {'id': 'list', 'type': 'list-hotfixes'}],
                    [MIXED[1], dict(MIXED[1], id='also-ordinary')],
                    recordVersion=FIXTURE_VERSION)
    row = _rows(ordinary)['clear-all']
    assert row['error'] is None, ordinary
    assert row['result'] == {
        'cleared': True, 'removed': 2, 'kept': 0}, ordinary
    assert ordinary['record'] == [], ordinary
    assert _rows(ordinary)['list']['result'] == {
        'version': _version(), 'fixes': []}, ordinary
    absent = _run([DROP_RECORD,
                   {'id': 'clear-all', 'type': 'clear-all-hotfixes'}], MIXED)
    row = _rows(absent)['clear-all']
    assert row['error'] is None, absent
    assert row['result'] == {'cleared': True, 'kept': 0}, absent


def test_clear_all_whose_record_read_is_refused_answers_with_the_reason(tmp):
    """The clear-all failure path, which is the same one the clear has.

    A read that throws takes the command out through the catch arm, so the
    operator is told the browser refused rather than told the record is clear
    — and the seeded fixes are still there, because the filter never ran.
    """
    del tmp
    refused = _run([{'id': 'clear-all', 'type': 'clear-all-hotfixes'}],
                   MIXED, storageReadFails=_key())
    assert refused['posted'][0]['error'] == (
        'storage read refused for ' + _key()), refused
    assert refused['posted'][0]['result'] is None, refused
    assert _flags(refused) == [('kept', True), ('dropped', False)], refused


def test_set_permanent_requires_a_fix_id_and_a_boolean(tmp):
    """Every way of arriving without both is one refusal, and `false` is not.

    The flag is read with a type test, so a command carrying a string in its
    slot is a value the handler cannot act on — the same shape the clear's own
    boolean has and the same reason it is refused. The row that matters beside
    it is `permanent: false`: a boolean that is false is a value the handler
    must act on, and a guard reading presence rather than type refuses it.
    """
    del tmp
    seed = [{'id': 'resident', 'code': CODE, 'permanent': True}]
    refusals = [
        {'id': 'no-fix-id', 'type': 'set-permanent', 'permanent': False},
        {'id': 'no-flag', 'type': 'set-permanent', 'fixId': 'resident'},
        {'id': 'string-flag', 'type': 'set-permanent', 'fixId': 'resident',
         'permanent': 'true'},
    ]
    refused = _run(refusals, seed)
    for row in refused['posted']:
        assert row['error'] == 'Missing fixId or permanent (bool)', refused
        assert row['result'] is None, refused
    assert _flags(refused) == [('resident', True)], refused
    demoted = _run(refusals + [
        {'id': 'demote', 'type': 'set-permanent', 'fixId': 'resident',
         'permanent': False}], seed)
    row = _rows(demoted)['demote']
    assert row['error'] is None, demoted
    assert row['result'] == {'id': 'resident', 'permanent': False,
                             'found': True}, demoted
    assert _flags(demoted) == [('resident', False)], demoted


def test_set_permanent_flips_the_flag_and_never_removes_the_fix(tmp):
    """The flag is the only thing that moves; the fix and its scope stay.

    An operator demoting a permanent fix is turning off its survival across
    an upgrade, not throwing the fix away, so the code and the site scope
    have to survive the flip. The seeded record carries both, and a handler
    that rebuilt the entry from the command alone would drop the scope and
    widen the fix to every site — the harm this assertion exists for.
    """
    del tmp
    scoped = [dict(MIXED[0], match='*://shop.example.com/*'),
              MIXED[1]]
    outcome = _run([{'id': 'demote', 'type': 'set-permanent',
                     'fixId': 'kept', 'permanent': False}], scoped)
    row = _rows(outcome)['demote']
    assert row['error'] is None, outcome
    assert row['result'] == {'id': 'kept', 'permanent': False,
                             'found': True}, outcome
    assert _flags(outcome) == [('kept', False), ('dropped', False)], outcome
    # The fix is still there, still scoped to the site it was scoped to.
    assert outcome['record'][0]['code'] == CODE, outcome
    assert outcome['record'][0]['match'] == '*://shop.example.com/*', outcome
    assert outcome['stored'] == [
        {'id': 'kept', 'match': '*://shop.example.com/*'},
        {'id': 'dropped', 'match': None}], outcome


def test_set_permanent_reports_an_id_the_record_does_not_hold(tmp):
    """`found: false` names the id and the flag, and changes nothing.

    A record that holds other fixes, and a record that was never written, are
    both states where the named fix is not there. The second is reached
    through the first's own `includePermanent` clear, so neither is a record
    this case built by other means.
    """
    del tmp
    unknown = _run([{'id': 'unknown', 'type': 'set-permanent',
                     'fixId': 'never-stored', 'permanent': True}], MIXED)
    row = _rows(unknown)['unknown']
    assert row['error'] is None, unknown
    assert row['result'] == {'id': 'never-stored', 'permanent': True,
                             'found': False}, unknown
    assert _flags(unknown) == [('kept', True), ('dropped', False)], unknown
    absent = _run([DROP_RECORD,
                   {'id': 'no-record', 'type': 'set-permanent',
                    'fixId': 'kept', 'permanent': True}], MIXED)
    row = _rows(absent)['no-record']
    assert row['error'] is None, absent
    assert row['result'] == {'id': 'kept', 'permanent': True,
                             'found': False}, absent


def test_set_permanent_whose_record_read_is_refused_answers_the_reason(tmp):
    """The flag's failure path, and the flag itself never moves."""
    del tmp
    refused = _run([{'id': 'promote', 'type': 'set-permanent',
                     'fixId': 'dropped', 'permanent': True}], MIXED,
                   storageReadFails=_key())
    assert refused['posted'][0]['error'] == (
        'storage read refused for ' + _key()), refused
    assert refused['posted'][0]['result'] is None, refused
    assert _flags(refused) == [('kept', True), ('dropped', False)], refused


def test_list_hotfixes_answers_the_record_the_store_wrote(tmp):
    """The list is what the worker's own store left there, not a summary.

    The record is built by two `store-hotfix` commands in the same case, so
    what the list answers is a read of state a real producer wrote, and the
    version it carries is the one the shipped worker stamps rather than one
    the test restates. A handler that answered an empty record, or summarised
    the fixes away, would pass a control that only counted them.
    """
    del tmp
    outcome = _run([
        {'id': 'store-one', 'fixId': 'first', 'code': CODE,
         'match': '*://shop.example.com/*'},
        {'id': 'store-two', 'fixId': 'second', 'code': CODE},
        {'id': 'list', 'type': 'list-hotfixes'},
    ])
    row = _rows(outcome)['list']
    assert row['error'] is None, outcome
    assert [fix['id'] for fix in row['result']['fixes']] == [
        'first', 'second'], outcome
    assert [fix['code'] for fix in row['result']['fixes']] == [
        CODE, CODE], outcome
    assert row['result']['version'] == _version(), outcome
    # The scope is in the answer, not dropped on the way out of storage.
    assert row['result']['fixes'][0]['match'] == '*://shop.example.com/*', (
        outcome)


def test_list_hotfixes_answers_an_empty_record_when_there_is_none(tmp):
    """No record is a record of no fixes, and says which version it is.

    The version is the worker's own, so a client that stored nothing can
    still tell whether what it is reading was written by this build. Asserted
    against the version read out of the shipped source, which is the only
    thing that makes the row a test rather than a restatement.

    The mirror half is a record the worker did NOT write, answering with the
    version that wrote it rather than with its own. That row is what makes
    the pair a discriminator instead of two spellings of one fact: without
    it, "the version is the worker's" is satisfied by a handler that always
    answers `_version()` and never read the record at all.
    """
    del tmp
    outcome = _run([DROP_RECORD,
                    {'id': 'list', 'type': 'list-hotfixes'}], MIXED)
    row = _rows(outcome)['list']
    assert row['error'] is None, outcome
    assert row['result'] == {'version': _version(), 'fixes': []}, outcome
    leftover = _run([{'id': 'list', 'type': 'list-hotfixes'}], MIXED,
                    recordVersion=FIXTURE_VERSION)
    row = _rows(leftover)['list']
    assert row['error'] is None, leftover
    assert row['result']['version'] == FIXTURE_VERSION, leftover
    assert row['result']['version'] != _version(), leftover
    assert [fix['id'] for fix in row['result']['fixes']] == [
        'kept', 'dropped'], leftover


def test_list_hotfixes_whose_record_read_is_refused_answers_the_reason(tmp):
    """A read that throws is an error answer, never an empty record.

    An empty record is a real answer, and a handler that answered it on a
    failed read would tell the operator their permanent fixes are gone.
    """
    del tmp
    refused = _run([{'id': 'list', 'type': 'list-hotfixes'}], MIXED,
                   storageReadFails=_key())
    assert refused['posted'][0]['error'] == (
        'storage read refused for ' + _key()), refused
    assert refused['posted'][0]['result'] is None, refused


def test_the_seeded_record_version_is_one_the_worker_cannot_stamp(tmp):
    """The double's OWN default is the discriminator, and this asserts it.

    `FIXTURE_VERSION` above is this file's copy; the double carries its own,
    and every case that does not pass `recordVersion` seeds with the double's
    — across four consumer suites, and it is the wider of the two. Both
    controls that read "the key is gone" off a version pass the parameter
    explicitly, so the default was bypassed by both of them, and the only
    thing standing behind it was a comment saying the worker cannot produce
    it. Aligning the double's copy to the worker's own left the whole set
    green.

    The two copies are deliberately not the same binding. This file's
    asserts the double's: reading the value out of the harness instead would
    move the expectation with the mutation, which is the one thing a control
    over it must not do.
    """
    del tmp
    outcome = _run([{'id': 'list', 'type': 'list-hotfixes'}], MIXED)
    row = _rows(outcome)['list']
    assert row['error'] is None, outcome
    assert row['result']['version'] == FIXTURE_VERSION, (
        'the double seeded the record with a version other than the one '
        'this file asserts; the two copies have drifted')
    assert row['result']['version'] != _version(), outcome


def test_a_case_naming_both_command_spellings_is_refused(tmp):
    """Two spellings of one thing is a case the double cannot resolve.

    `commands` and `store` drive the same loop, and an empty `commands` is
    truthy in JavaScript — so picking the first that reads as present drops
    a populated `store` without a word, and the worker's own
    `data[HOTFIX_KEY] || {version, fixes: []}` default papers over the
    result. A case that would run neither list looks exactly like a case
    whose commands all did nothing.

    The refusal is identified by its own words, not by the fact that node
    exited nonzero. `run_hotfix_case` raises a bare `AssertionError` for
    every failure the child can have, so a bare `except` would read "the
    double refused" for a harness broken for any other reason at all — and
    report the refusal working at a point where it was never reached.
    """
    del tmp
    try:
        _run([{'id': 'via-commands', 'fixId': 'first', 'code': CODE}],
             store=[{'id': 'via-store', 'fixId': 'second', 'code': CODE}])
    except AssertionError as failure:
        # `(returncode, stdout, stderr)` is what the helper puts in the
        # assertion's message.
        assert len(failure.args) == 1 and len(failure.args[0]) == 3, failure
        _returncode, _stdout, stderr = failure.args[0]
        assert BOTH_KEYS_REFUSAL in stderr, (
            'the case failed, but not for the reason this control is about; '
            f'the double said: {stderr!r}')
        return
    raise AssertionError(
        'a case naming both `commands` and `store` was accepted; the double '
        'has to refuse the shape rather than pick one of the two lists')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='hotfixcommands_')


if __name__ == '__main__':
    raise SystemExit(main())
