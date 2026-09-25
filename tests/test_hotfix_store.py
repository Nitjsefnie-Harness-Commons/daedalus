#!/usr/bin/env python3
"""The hotfix record's byte bound: what crosses it is refused, and nothing
but the extension can fill the area its own state depends on.

`handleStoreHotfix` read the whole record, filtered the replaced fixId, and
wrote the result back with no measure. 160 fixes of 64 KiB pass Chrome's
10,485,760-byte `local` quota, after which the extension's OTHER writes are
the ones that fail. Chrome measures QUOTA_BYTES as the JSON stringification
of every value plus every key's length, and the record is one key, so its
charge is the record's JSON bytes plus the key's length. The bound is
therefore bytes, not count.

Overflow REFUSES, naming the limit, and never reaches storage. Eviction
would destroy operator-persisted code — a `permanent: true` fix is exactly
the fix whose loss is unrecoverable — while the refusal is reversible
through the clear commands the operator already has.

These drive the shipped worker in the Node-VM boundary harness. The
thresholds are placed by this file's OWN measure, computed from its own
construction and never from the production one, so a change to either
term in the production measure moves an admission and the two disagree.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _boundary import run_extension_hotfix_quota  # noqa: E402
from _repo import EXTENSION_ROOT  # noqa: E402

# The constant as the production module declares it, read out of the shipped
# source: a boundary derived from it moves with the cap rather than restating
# it, and a module that declares no cap fails to parse here.
HOTFIX_KEY = 'daedalus-hotfixes'


def _background_version():
    """The record's `version` field: the version background.js declares."""
    source = (EXTENSION_ROOT / 'background.js').read_text(encoding='utf-8')
    match = re.search(r"const VERSION = '([^']+)'", source)
    assert match, 'background.js declares no VERSION'
    return match.group(1)


VERSION = _background_version()
# Chrome's documented QUOTA_BYTES for `local`, which is why every cap in the
# worker sums below it. The relationship is checked, not the constants: the
# two caps are numbers the modules own, and what must hold is that they fit.
CHROME_AREA_BYTES = 10 * 1024 * 1024
# The other count-bounded term the 3 MiB reserve is sized against: the
# segment-origin allowlist, charged as Chrome charges it — a stored
# canonical origin is one JSON string in a stored array, so an entry
# costs the origin's own length plus the two quotes and the comma that
# join it to its neighbours. The ledger's term is the 22 KB the worker
# comments name for 1000 `<ms>_<counter>` delivery ids. The control
# below holds the RELATIONSHIP between the terms, not these numerals.
CANONICAL_ORIGIN = 'https://example.com'
JSON_ENTRY_OVERHEAD = 3
LEDGER_TERM_BYTES = 22 * 1000


def _declared(source, name):
    """The numeric value a `const NAME = <product of integers>;` declares."""
    match = re.search(rf'const {name} = ([0-9 *]+);', source)
    assert match, f'{name} is not declared as a product of integers'
    product = 1
    for factor in match.group(1).split('*'):
        product *= int(factor.strip())
    return product


def _hotfix_quota():
    return _declared(
        (EXTENSION_ROOT / 'worker' / 'hotfixes.js').read_text(
            encoding='utf-8'), 'HOTFIX_QUOTA_BYTES')


def _segment_origin_cap():
    return _declared(
        (EXTENSION_ROOT / 'worker' / 'segment_mint.js').read_text(
            encoding='utf-8'), 'SEGMENT_ORIGIN_CAP')


def _charge(version, fixes):
    """This file's own measure of the record's charge, never production's.

    Chrome measures QUOTA_BYTES as the JSON stringification of every value
    plus every key's length; the record is one key, so its charge is the
    record's JSON bytes plus the key's length. Computed from this
    construction, so it is independent of the module under test. The
    compact separators are what `JSON.stringify` emits, and they are the
    difference between this measure and a spaced one.
    """
    return len(_stringify({'version': version, 'fixes': fixes}).encode(
        'utf-8')) + len(HOTFIX_KEY.encode('utf-8'))


def _stringify(value):
    """`JSON.stringify`'s form: no whitespace between tokens."""
    return json.dumps(value, separators=(',', ':'))


def _code_length_for(version, seeds, fix_id, permanent, target):
    """The code length whose composed record lands on `target` bytes."""
    length = max(0, target - _charge(version, seeds)
                 - len(_stringify(fix_id).encode('utf-8')) - 60)
    for _unused in range(3):
        fix = {'id': fix_id, 'code': 'x' * length, 'ts': 1700000000000,
               'permanent': permanent}
        length = max(0, length + target - _charge(version, seeds + [fix]))
    return length


def _plan(steps, cap=None, version=VERSION):
    return {'cap': cap or _hotfix_quota(), 'version': version,
            'steps': steps}


def test_the_hotfix_store_refuses_a_record_that_would_cross_the_cap(tmp):
    """One byte over is refused, with an error that names the limit.

    The refusal reaches the operator as the command's result, and the
    record in storage is byte-for-byte what it was — the write never
    happened. A silent success is the harm: the operator believes the fix
    is persisted, and the next write to the area fails instead.
    """
    del tmp
    quota = _hotfix_quota()
    seed = [{'id': 'base', 'codeLength': 100, 'permanent': False}]
    seeded = [{'id': 'base', 'code': 'x' * 100, 'ts': 1700000000000,
               'permanent': False}]
    for label, delta, expect in (('one over the cap', 1, 'refused'),
                                 ('exactly on the cap', 0, 'admitted')):
        fix_id = label.replace(' ', '-')
        incoming = _code_length_for(
            VERSION, seeded, fix_id, False, quota + delta)
        plan = _plan([{'id': fix_id,
                       'codeLength': incoming, 'seed': seed}])
        actual = run_extension_hotfix_quota(plan)
        assert len(actual) == 1, actual
        step = actual[0]
        # The scenario's own measure and this file's agree on where the
        # boundary sits; that is what lets a change to the production
        # measure show up as a disagreement rather than as agreement.
        assert step['projected'] == quota + delta, step
        if expect == 'refused':
            assert step['result'] is None, step
            assert step['error'] and str(quota) in step['error'], step
            # The stored record is exactly the seed: the refused fix never
            # reached storage and nothing was evicted to make room.
            assert step['fixes'] == [
                {'id': 'base', 'codeLength': 100, 'permanent': False}], step
        else:
            assert step['error'] is None, step
            assert step['result']['stored'] == fix_id, step
            assert [fix['id'] for fix in step['fixes']] == [
                'base', fix_id], step


def test_a_small_fix_onto_a_full_record_is_refused_by_the_record_charge(tmp):
    """The charge is the whole record, not the code that arrives.

    A measure that looked only at the incoming code would admit this: ten
    characters is nothing beside a 2 MiB cap. What crosses the cap is the
    record the store would leave behind, and the seed here is sized so the
    record alone is already within reach of it — the crossing is done by
    the pair, which is the only place it can happen.
    """
    del tmp
    quota = _hotfix_quota()
    full = _code_length_for(VERSION, [], 'full', False, quota - 1)
    seed = [{'id': 'full', 'codeLength': full, 'permanent': False}]
    plan = _plan([{'id': 'small', 'codeLength': 10, 'seed': seed}])
    step = run_extension_hotfix_quota(plan)[0]
    assert step['projected'] > quota, step
    assert 10 < quota, step
    assert step['error'] and str(quota) in step['error'], step
    assert step['result'] is None, step
    assert [fix['id'] for fix in step['fixes']] == ['full'], step


def test_a_single_fix_larger_than_the_cap_is_refused_not_truncated(tmp):
    """A fix whose code alone exceeds the cap is refused whole.

    It is not stored truncated and not partially stored: the only two
    outcomes a partial write could have are a silently-mutilated fix and a
    cap that does not bound anything. Neither reaches storage.
    """
    del tmp
    quota = _hotfix_quota()
    plan = _plan([{'id': 'huge', 'codeLength': quota + 1,
                   'permanent': False}])
    actual = run_extension_hotfix_quota(plan)
    step = actual[0]
    assert step['result'] is None, step
    assert step['error'] and str(quota) in step['error'], step
    assert step['fixes'] == [], step
    assert step['charge'] == _charge(VERSION, []), step


def test_a_replacing_fix_is_charged_after_the_old_one_is_filtered_out(tmp):
    """Replacing a fix the operator already stored is measured as a
    replacement, not as the old and new entries together.

    `stored.fixes.filter(f => f.id !== cmd.fixId)` runs before the record
    is written, so the old entry stops counting the moment the new one
    arrives. Measuring before that filter would double-charge the
    replaced entry and refuse a fix the operator is entitled to store.
    The seed here is sized so that old-plus-new exceeds the cap while
    new alone does not — the replacement must be ADMITTED.
    """
    del tmp
    quota = _hotfix_quota()
    old = {'id': 'replace', 'code': 'x' * 100, 'ts': 1700000000000,
           'permanent': False}
    other = {'id': 'other', 'code': 'x' * 100, 'ts': 1700000000000,
             'permanent': False}
    seeds = [old, other]
    base = _charge(VERSION, seeds)
    # New alone would fit; old + new together exceed the cap.
    assert base <= quota, base
    incoming = _code_length_for(
        VERSION, [other], 'replace', False, quota)
    replaced = dict(old, code='x' * incoming)
    assert _charge(VERSION, [other, replaced]) <= quota
    assert _charge(VERSION, seeds + [replaced]) > quota
    plan = _plan([{'id': 'replace', 'codeLength': incoming,
                   'seed': [{'id': 'other', 'codeLength': 100,
                             'permanent': False},
                            {'id': 'replace', 'codeLength': 100,
                             'permanent': False}]}])
    step = run_extension_hotfix_quota(plan)[0]
    assert step['error'] is None, step
    assert step['result']['stored'] == 'replace', step
    assert {fix['id'] for fix in step['fixes']} == {'other', 'replace'}, step
    assert [fix for fix in step['fixes']
            if fix['id'] == 'replace'][0]['codeLength'] == incoming, step


def test_a_refused_store_releases_the_lock_for_the_next_operation(tmp):
    """The refusal path runs inside the same critical section as a store,
    so it has to reach the serializer's release exactly as an admitted one
    does.

    `_serializer` chains each mutation onto the previous run's settled
    promise, so a throw inside the critical section is a settled rejection
    the next mutation chains onto — a release is a property of the chain,
    not a statement a refusal path could forget. What a missing release
    would look like from outside is the NEXT command never answering, so
    the evidence is that a `clear-hotfix` and a further `store-hotfix`
    dispatched after the refusal both complete. Neither could answer if
    the refusal had wedged the serializer.
    """
    del tmp
    quota = _hotfix_quota()
    seed = [{'id': 'resident', 'codeLength': 100, 'permanent': False}]
    over = _code_length_for(VERSION, seed, 'too-big', False, quota + 1)
    small = _code_length_for(VERSION, [], 'after', False, quota // 2)
    plan = _plan([
        {'id': 'too-big', 'codeLength': over, 'seed': seed},
        {'id': 'resident', 'clear': True},
        {'id': 'after', 'codeLength': small},
    ])
    refused, cleared, admitted = run_extension_hotfix_quota(plan)
    assert refused['error'] and str(quota) in refused['error'], refused
    assert refused['result'] is None, refused
    # The clear ran: it removed the seeded fix, which the refusal left in
    # place. A wedged serializer answers neither of the last two.
    assert cleared['error'] is None, cleared
    assert cleared['result'] == {'cleared': 'resident', 'found': True,
                                 'remaining': 0}, cleared
    # And the lock was free again: a further store was admitted once the
    # room the clear made was there.
    assert admitted['error'] is None, admitted
    assert admitted['result'] == {'stored': 'after', 'total': 1,
                                  'permanent': False}, admitted
    assert [fix['id'] for fix in admitted['fixes']] == ['after'], admitted


def test_the_two_quotas_leave_the_reserve_the_comment_promises(tmp):
    """GM's aggregate and the hotfix record's cap both fit inside Chrome's
    `local` area, the reserve left is the 3 MiB the worker comments name,
    and the other count-bounded term it is sized against stays under the
    delivery-id ledger's.

    This is a CLAIM ABOUT VALUES, so it is checked independently of
    every module's exported constant: the numbers are read out of the
    shipped sources and the relationships are asserted in words. A cap
    that grew past the area, or an allowlist that outgrew the ledger it
    is compared against, would be caught here by name rather than by an
    incidental fixture that overflows first.
    """
    del tmp
    gm_total = _declared(
        (EXTENSION_ROOT / 'worker' / 'gm_storage.js').read_text(
            encoding='utf-8'), 'GM_TOTAL_QUOTA_BYTES')
    hotfix_quota = _hotfix_quota()
    assert gm_total > 0, gm_total
    assert hotfix_quota > 0, hotfix_quota
    assert gm_total + hotfix_quota <= CHROME_AREA_BYTES, (
        f'GM_TOTAL_QUOTA_BYTES ({gm_total}) + HOTFIX_QUOTA_BYTES '
        f'({hotfix_quota}) exceeds Chrome\'s local area '
        f'({CHROME_AREA_BYTES})')
    assert CHROME_AREA_BYTES - gm_total - hotfix_quota == 3 * 1024 * 1024, (
        'the reserve the worker comments promise is no longer 3 MiB')
    origin_cap = _segment_origin_cap()
    assert origin_cap > 0, origin_cap
    per_entry = len(CANONICAL_ORIGIN) + JSON_ENTRY_OVERHEAD
    allowlist_term = origin_cap * per_entry
    assert allowlist_term < LEDGER_TERM_BYTES, (
        f'SEGMENT_ORIGIN_CAP ({origin_cap}) at {per_entry} bytes per '
        f'entry ({allowlist_term} bytes) is no longer under the '
        f'delivery-id ledger\'s term ({LEDGER_TERM_BYTES} bytes) the '
        'worker comments size the 3 MiB reserve against')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='hotfixstore_')


if __name__ == '__main__':
    raise SystemExit(main())
