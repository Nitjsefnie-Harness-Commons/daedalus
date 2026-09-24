#!/usr/bin/env python3
"""GM.setValue and its neighbours: what the page may store, where, and how
much of it.

Page-written GM keys are partitioned by the calling page's origin inside the
shared chrome.storage.local, and each origin's partition carries its own byte
budget so a page cannot make the extension's own writes fail. The Node-VM
harnesses live in _gm_harness; these are the pins over them — the reserved and
invalid-key refusals, the per-origin partition (no origin may read, overwrite,
list or delete another's key, and a spoofed origin in the payload is ignored),
the per-origin quota, and that a write Chrome refused rejects rather than
resolving, since Chrome reports that only through lastError.
"""
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import _gm_harness  # noqa: E402


def _gm_key(key, origin):
    """The storage key an origin's GM key is stored under."""
    return 'gm:' + quote(origin, safe='') + ':' + key


def test_a_failed_storage_write_rejects_instead_of_resolving(tmp):
    """Chrome reports a storage failure only through lastError.

    The callbacks fire on failure exactly as on success, with the store
    unchanged, so a relay that did not read chrome.runtime.lastError could
    not tell the two apart — GM.setValue resolved successfully having stored
    nothing, and the page had no way to find out.
    """
    del tmp
    outcomes = _gm_harness.run_failure()
    assert set(outcomes) == {
        'getValue', 'setValue', 'deleteValue', 'listValues'}, outcomes
    for name, outcome in sorted(outcomes.items()):
        assert outcome['settled'] == 'rejected', (name, outcome)
        assert 'QUOTA_BYTES quota exceeded' in outcome['error'], (
            name, outcome)


def test_page_storage_rejects_coercible_reserved_keys(tmp):
    """GM.setValue rejects coercible reserved keys before storage is called."""
    del tmp
    result = _gm_harness.run_relay()
    cases = {case['label']: case for case in result['gmSetCases']}
    for key in ('daedalus-server', 'daedalus-hotfixes', 'daedalus-token'):
        assert cases[f'array:{key}'] == {
            'label': f'array:{key}',
            'status': 'rejected',
            'error': 'invalid key',
            'calls': [],
            'storedKeys': [],
        }, cases[f'array:{key}']
    assert cases['string:daedalus-server'] == {
        'label': 'string:daedalus-server',
        'status': 'rejected',
        'error': 'reserved key',
        'calls': [],
        'storedKeys': [],
    }, cases['string:daedalus-server']
    ordinary = _gm_key('ordinary', _gm_harness.SINGLE_ORIGIN)
    assert cases['string:ordinary'] == {
        'label': 'string:ordinary',
        'status': 'resolved',
        'error': None,
        'calls': ['get', 'set'],
        'storedKeys': [ordinary],
    }, cases['string:ordinary']


def test_page_storage_validates_every_keyed_handler(tmp):
    """Every keyed relay rejects non-strings without touching storage."""
    del tmp
    result = _gm_harness.run_relay()
    for handler in ('getValue', 'setValue', 'deleteValue'):
        case = result['invalidHandlers'][handler]
        assert case['error'] == 'invalid key', (handler, case)
        assert case['calls'] == [], (handler, case)
        assert case['storedKeys'] == ['daedalus-server'], (handler, case)
        assert case['protectedValue'] == 'protected', (handler, case)
    for label, case in result['coercible'].items():
        assert case['error'] == 'invalid key', (label, case)
        assert case['calls'] == [], (label, case)
        assert case['protectedValue'] == 'protected', (label, case)


def test_page_storage_allows_string_keys_and_filters_list_values(tmp):
    """Ordinary strings work and listValues omits every reserved string key."""
    del tmp
    result = _gm_harness.run_relay()
    handlers = result['ordinaryHandlers']
    ordinary = _gm_key('ordinary', _gm_harness.SINGLE_ORIGIN)
    assert handlers['getValue']['value'] == 'kept', handlers['getValue']
    assert handlers['getValue']['calls'] == ['get'], handlers['getValue']
    assert handlers['setValue']['storedKeys'] == [ordinary], (
        handlers['setValue'])
    assert handlers['setValue']['calls'] == ['get', 'set'], (
        handlers['setValue'])
    assert handlers['deleteValue']['storedKeys'] == [], handlers['deleteValue']
    assert handlers['deleteValue']['calls'] == ['remove'], (
        handlers['deleteValue'])
    assert result['listValues'] == {
        'keys': ['ordinary'],
        'calls': ['get'],
    }, result['listValues']


def test_origin_cannot_read_another_origins_value(tmp):
    """B reading A's GM key gets B's default, not A's value."""
    del tmp
    case = _gm_harness.run_two_origin()['readIsolation']
    assert case['bValue'] == 'DEFAULT', case
    assert case['aValue'] == 'A-value', case


def test_origin_cannot_overwrite_another_origins_value(tmp):
    """B writing the same GM key name leaves A's value untouched."""
    del tmp
    case = _gm_harness.run_two_origin()['overwriteIsolation']
    assert case['aValue'] == 'A-value', case
    assert case['bValue'] == 'B-value', case
    assert len(case['storeKeys']) == 2, case


def test_list_values_shows_only_the_calling_origins_keys(tmp):
    """B's listValues does not name A's GM key; A's does."""
    del tmp
    case = _gm_harness.run_two_origin()['listIsolation']
    assert case['bKeys'] == [], case
    assert case['aKeys'] == ['a-only'], case


def test_origin_cannot_delete_another_origins_key(tmp):
    """B's deleteValue leaves A's GM key and value in place."""
    del tmp
    case = _gm_harness.run_two_origin()['deleteIsolation']
    assert case['aValue'] == 'A-value', case
    assert len(case['storeKeys']) == 1, case


def test_quota_is_per_origin_and_excludes_extension_keys(tmp):
    """A large daedalus-token never charges a page's own partition.

    A's own write still fits beside it, and A's oversized write is refused
    with set uncalled, because the sum covers A's partition only.
    """
    del tmp
    case = _gm_harness.run_two_origin()['quota']
    assert case['fitsError'] is None, case
    assert case['fitsCalls'] == ['get', 'set'], case
    assert case['crossError'] == 'gm storage quota exceeded', case
    assert case['crossCalls'] == ['get'], case
    assert 'daedalus-token' in case['crossStoreKeys'], case


def test_origin_is_taken_from_location_not_the_message(tmp):
    """A spoofed origin in the payload is ignored; the frame's own is used."""
    del tmp
    case = _gm_harness.run_two_origin()['spoof']
    assert case['value'] == 'DEFAULT', case
    assert case['aKeys'] == ['secret'], case
    assert case['bKeys'] == ['spoofed'], case


def test_concurrent_setvalue_calls_never_exceed_the_cap(tmp):
    """A burst of setValue in one turn cannot overflow the partition.

    chrome.storage's get -> sum -> set is a read-modify-write; concurrent
    calls issued in a single turn each read the pre-write store, so writes
    for one origin are serialized. Twelve 90,000-byte writes: only those
    that fit are stored, and the partition total never exceeds the cap.
    """
    del tmp
    case = _gm_harness.run_two_origin()['concurrent']
    assert case['total'] <= case['cap'], case
    assert case['refusals'] >= 1, case


def test_map_and_set_are_charged_by_json_form(tmp):
    """A Map/Set is charged its JSON form, matching Chrome's QUOTA_BYTES.

    Chrome's local quota is "measured by the JSON stringification of every
    value" and its values are JSON-serialisable, so a 100k-entry Map is
    stored and charged as {} (2 bytes), not as a structured clone. The Map
    and Set are admitted and a following cap-100 value still fits beside
    them, which it would not if either were charged by its entries.
    """
    del tmp
    case = _gm_harness.run_two_origin()['mapSet']
    assert case['mapError'] is None, case
    assert case['mapCalls'] == ['get', 'set'], case
    assert case['setError'] is None, case
    assert case['setCalls'] == ['get', 'set'], case
    assert case['fillError'] is None, case
    assert case['fillCalls'] == ['get', 'set'], case


def test_an_opaque_origin_refuses_gm_storage(tmp):
    """location.origin "null" has no owner, so every GM handler refuses."""
    del tmp
    case = _gm_harness.run_two_origin()['opaque']
    assert case == {
        'get': 'opaque origin', 'set': 'opaque origin',
        'list': 'opaque origin', 'del': 'opaque origin',
    }, case


def test_a_write_that_fits_reaches_set_and_replies_without_error(tmp):
    """A small write reaches set and the reply carries no error."""
    del tmp
    case = _gm_harness.run_two_origin()['fits']
    assert case['error'] is None, case
    assert case['calls'] == ['get', 'set'], case


def test_a_write_past_the_quota_is_refused_and_set_is_not_called(tmp):
    """A write past the cap is refused, distinguishable from Chrome's quota."""
    del tmp
    case = _gm_harness.run_two_origin()['cross']
    assert case['error'] == 'gm storage quota exceeded', case
    assert case['error'] != 'QUOTA_BYTES quota exceeded', case
    assert case['calls'] == ['get'], case
    assert case['storeKeys'] == [], case


def test_replacing_a_key_is_accounted_old_out_new_in(tmp):
    """A rewrite only fits because the value it replaces stops counting."""
    del tmp
    case = _gm_harness.run_two_origin()['replace']
    assert case['error'] is None, case
    assert case['calls'] == ['get', 'set'], case


def test_delete_value_frees_budget_by_recompute(tmp):
    """A delete frees budget: the add refused before it is accepted after."""
    del tmp
    case = _gm_harness.run_two_origin()['deleteFrees']
    assert case['refusedError'] == 'gm storage quota exceeded', case
    assert case['refusedCalls'] == ['get'], case
    assert case['acceptedError'] is None, case
    assert case['acceptedCalls'] == ['get', 'set'], case


def test_a_partition_already_over_quota_refuses_every_page_write(tmp):
    """A store already over the cap refuses a page write, set uncalled."""
    del tmp
    case = _gm_harness.run_two_origin()['alreadyOver']
    assert case['error'] == 'gm storage quota exceeded', case
    assert case['calls'] == ['get'], case


def test_an_unserialisable_value_is_refused_and_never_reaches_set(tmp):
    """A value JSON.stringify cannot produce is refused before any get."""
    del tmp
    case = _gm_harness.run_two_origin()['unserialisable']
    assert case['error'] == 'value could not be measured', case
    assert case['calls'] == [], case
    assert case['storeKeys'] == [], case


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='gmstorage_')


if __name__ == '__main__':
    raise SystemExit(main())
