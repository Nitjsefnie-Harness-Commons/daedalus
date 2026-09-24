#!/usr/bin/env python3
"""The two `/result` routes as functions returning `(status, payload)`.

`daedalus_bridge/result_routes.py` owns the POST body that stores a result
and the GET body that peeks at or consumes one. Both take the result
directory as a parameter and call `daedalus_bridge.result_store` for the
locks and the slot primitives.

`DAEDALUS_DIR` and `DAEDALUS_PORT` are set here so the result directory
most of these tests pass in is the one configuration names; the controls
for the root parameter deliberately pass a different one. Each test uses
its own token and its own delivery ids, because the accepted-delivery
record is process-wide.
"""
import atexit
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _util  # noqa: E402

_BASE = tempfile.mkdtemp(prefix='resultroutes_base_')
atexit.register(shutil.rmtree, _BASE, ignore_errors=True)
os.environ['DAEDALUS_DIR'] = _BASE
os.environ['DAEDALUS_PORT'] = '0'
RES_DIR = Path(_BASE) / 'results'
# Above any delivery count these tests store, so eviction never fires in
# them; the cap has its own controls.
DELIVERY_CAP = 8


def _load(name):
    RES_DIR.mkdir(parents=True, exist_ok=True)
    return _util.load(
        _util.ROOT / 'daedalus_bridge' / 'result_routes.py', name)


def _slot(token, tab=''):
    return RES_DIR / (f'{token}_{tab}.json' if tab else f'{token}.json')


def _delivery(token, tab, did):
    key = f'{token}_{tab}' if tab else token
    return RES_DIR / 'deliveries' / key / f'{did}.json'


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _events(cmd_dir, token):
    """Every dashboard event the routes published under `cmd_dir`."""
    queue = Path(cmd_dir) / f'{token}_dashboard'
    if not queue.is_dir():
        return []
    return [json.loads(path.read_text(encoding='utf-8'))
            for path in sorted(queue.iterdir())]


def _configured_tree():
    """Every path under the configured results root, relative to it."""
    RES_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(str(path.relative_to(RES_DIR))
                  for path in RES_DIR.rglob('*'))


def test_a_slot_refusal_redacts_the_credential(tmp):
    """The slot's containment refusal is a [PATH-REFUSAL] line like any other.

    The per-site threading is what makes the invariant true, so this pin
    drives the real print machinery with whatever secret the route threads
    to its slot resolution: dropping the secret shows the credential on
    the line again.
    """
    routes = _load('fixture_result_routes_fetch_redact')
    safety = routes.path_safety
    real_under = safety.under
    output = io.StringIO()

    def refusing_under(root, *parts, **kwargs):
        safety.log_path_refusal(
            'containment', root, parts, [], secret=kwargs.get('secret', ''))
        raise ValueError('path escapes its root')

    try:
        safety.under = refusing_under
        with contextlib.redirect_stdout(output):
            status, payload = routes.fetch_result(
                RES_DIR, 'tok-verify', {'tab': ['t1']})
    finally:
        safety.under = real_under
    assert (status, payload) == (400, {'error': 'invalid path component'})
    line = output.getvalue()
    assert 'tok-verify' not in line, line
    assert 'tok-veri…' in line, line


def test_accept_writes_both_slots_and_the_delivery_file(tmp):
    routes = _load('fixture_result_routes_accept')
    token, did = 'accepttok', '1700000000000_1'
    status, payload = routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '7', 'id': 'cmd-1', 'value': 'hi', '_did': did},
        DELIVERY_CAP)
    assert (status, payload) == (200, {'ok': True})
    token_slot = _read(_slot(token))
    tab_slot = _read(_slot(token, '7'))
    delivery = _read(_delivery(token, '7', did))
    assert token_slot['value'] == 'hi'
    assert tab_slot == token_slot and delivery == token_slot
    assert token_slot['deliveryId'] == did
    assert token_slot['resultGeneration']
    assert isinstance(token_slot['roundtrip_ms'], int)


def test_accept_stores_the_result_without_the_credential(tmp):
    """The stored body is not a copy of the control credential.

    The body arrives carrying `token` because that is how the caller
    authenticated, so storing it verbatim made every slot and delivery file
    under results/ a copy of the credential. PUT /command strips `token`
    from what it stores, and POST /result now does the same; the dashboard
    event never carried it.
    """
    routes = _load('fixture_result_routes_cred')
    token, did = 'credtok', 'cred-1'
    status, payload = routes.accept_result(
        RES_DIR, tmp, token,
        {'token': token, 'tabId': '1', 'id': 'cmd-1', 'value': 'hi',
         '_did': did},
        DELIVERY_CAP)
    assert (status, payload) == (200, {'ok': True})
    for path in (_slot(token), _slot(token, '1'),
                 _delivery(token, '1', did)):
        stored = _read(path)
        assert 'token' not in stored, (path, stored)
        assert stored['value'] == 'hi', (path, stored)
    assert _events(tmp, token)[0]['type'] == 'result'


def test_accept_answers_duplicate_for_a_repeated_delivery_id(tmp):
    routes = _load('fixture_result_routes_duplicate')
    token, did = 'duptok', 'dup-1'
    routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '3', 'id': 'first', 'value': 'first', '_did': did},
        DELIVERY_CAP)
    first = _read(_slot(token, '3'))
    status, payload = routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '3', 'id': 'second', 'value': 'second', '_did': did},
        DELIVERY_CAP)
    assert (status, payload) == (200, {'ok': True, 'duplicate': True})
    assert _read(_slot(token, '3')) == first
    assert _read(_slot(token)) == first
    assert _read(_delivery(token, '3', did)) == first


def test_accept_publishes_one_dashboard_event_per_stored_result(tmp):
    routes = _load('fixture_result_routes_events')
    token, did = 'eventtok', 'event-1'
    body = {'tabId': '9', 'id': 'cmd-9', 'world': 'cdp', '_did': did}
    routes.accept_result(RES_DIR, tmp, token, dict(body), DELIVERY_CAP)
    routes.accept_result(RES_DIR, tmp, token, dict(body), DELIVERY_CAP)
    events = _events(tmp, token)
    assert len(events) == 1, events
    assert events[0]['type'] == 'result'
    assert events[0]['tabId'] == '9'
    assert events[0]['resultId'] == 'cmd-9'
    assert events[0]['world'] == 'cdp'
    assert events[0]['ok'] is True


def test_accept_treats_a_non_string_delivery_id_as_absent(tmp):
    routes = _load('fixture_result_routes_baddid')
    token = 'baddidtok'
    status, payload = routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '2', 'id': 'x', '_did': 12},
        DELIVERY_CAP)
    assert (status, payload) == (200, {'ok': True})
    stored = _read(_slot(token, '2'))
    assert 'deliveryId' not in stored
    assert not (RES_DIR / 'deliveries' / f'{token}_2').exists()


def test_accept_refuses_a_result_it_cannot_serialize(tmp):
    routes = _load('fixture_result_routes_unencodable')
    token = 'badbodytok'
    status, payload = routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '1', 'value': {1, 2}},
        DELIVERY_CAP)
    assert status == 400
    assert payload == {'error': 'result is not encodable'}
    assert not _slot(token).exists()
    assert not _slot(token, '1').exists()


def test_accept_refuses_an_unsafe_tab_id(tmp):
    routes = _load('fixture_result_routes_unsafe_post')
    status, payload = routes.accept_result(
        RES_DIR, tmp, 'unsafeposttok', {'tabId': '..', 'id': 'x'},
        DELIVERY_CAP)
    assert status == 400
    assert payload == {'error': 'invalid path component'}


def test_fetch_peeks_without_deleting_the_slot(tmp):
    routes = _load('fixture_result_routes_peek')
    token = 'peektok'
    routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '4', 'value': 'kept'},
        DELIVERY_CAP)
    status, payload = routes.fetch_result(RES_DIR, token, {'tab': ['4']})
    assert status == 200
    assert payload['value'] == 'kept'
    assert _slot(token, '4').exists()


def test_fetch_consumes_only_when_the_expected_generation_matches(tmp):
    routes = _load('fixture_result_routes_expected')
    token = 'expectedtok'
    routes.accept_result(
        RES_DIR, tmp, token, {'tabId': '5', 'value': 'once'},
        DELIVERY_CAP)
    generation = _read(_slot(token, '5'))['resultGeneration']
    status, payload = routes.fetch_result(
        RES_DIR, token,
        {'tab': ['5'], 'consume': ['1'], 'expected': ['not-the-one']})
    assert (status, payload) == (200, {'consumed': False})
    assert _slot(token, '5').exists()
    status, payload = routes.fetch_result(
        RES_DIR, token,
        {'tab': ['5'], 'consume': ['1'], 'expected': [generation]})
    assert status == 200
    assert payload == {'consumed': True, 'resultGeneration': generation}
    assert not _slot(token, '5').exists()


def test_fetch_consuming_a_delivery_removes_the_slot_copy(tmp):
    routes = _load('fixture_result_routes_delivery')
    token, did = 'deliverytok', 'delivery-1'
    routes.accept_result(
        RES_DIR, tmp, token,
        {'tabId': '6', 'value': 'paired', '_did': did},
        DELIVERY_CAP)
    generation = _read(_slot(token, '6'))['resultGeneration']
    status, payload = routes.fetch_result(
        RES_DIR, token,
        {'delivery': [did], 'consume': ['1'], 'expected': [generation]})
    assert status == 200
    assert payload == {'consumed': True, 'resultGeneration': generation}
    assert not _delivery(token, '6', did).exists()
    assert not _slot(token, '6').exists()
    assert not _slot(token).exists()


def test_fetch_answers_pending_for_an_absent_delivery(_tmp):
    routes = _load('fixture_result_routes_pending')
    status, payload = routes.fetch_result(
        RES_DIR, 'pendingtok', {'delivery': ['never-stored']})
    assert (status, payload) == (200, {'pending': True})


def test_fetch_refuses_an_unsafe_tab(_tmp):
    routes = _load('fixture_result_routes_unsafe_get')
    status, payload = routes.fetch_result(
        RES_DIR, 'unsafegettok', {'tab': ['..']})
    assert status == 400
    assert payload == {'error': 'invalid path component'}


def test_the_module_needs_no_configuration_of_its_own(_tmp):
    """Neither the route module nor anything it imports reads `config`.

    `daedalus_bridge.config` still exits without `DAEDALUS_DIR`, which is
    what proves the environment is stripped. The route module then imports
    for real, so nothing on its graph — `result_store` included — binds
    configuration.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('DAEDALUS_')}
    env['PYTHONPATH'] = str(_util.ROOT)
    refused = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.config'],
        env=env, capture_output=True, text=True, check=False)
    assert refused.returncode != 0, refused.stderr
    assert 'DAEDALUS_DIR' in refused.stderr, refused.stderr
    imported = subprocess.run(
        [sys.executable, '-c', 'import daedalus_bridge.result_routes'],
        env=env, capture_output=True, text=True, check=False)
    assert imported.returncode == 0, imported.stderr


def test_the_results_root_governs_where_a_delivery_lands(tmp):
    """Every file this POST writes lands under the root it was handed.

    All three: the delivery file, the tab slot and the broadcast slot.
    The negative half lists the whole configured tree rather than the
    three names this request would build, because a wrongly resolved
    call leaks under whatever name it chooses.
    """
    routes = _load('fixture_result_routes_root')
    before = _configured_tree()
    root = Path(tmp) / 'other-results'
    root.mkdir(parents=True)
    token, did = 'roottok', 'root-1'
    status, payload = routes.accept_result(
        root, tmp, token,
        {'tabId': '8', 'id': 'cmd-8', 'value': 'redirected', '_did': did},
        DELIVERY_CAP)
    assert (status, payload) == (200, {'ok': True})
    moved = root / 'deliveries' / f'{token}_8' / f'{did}.json'
    assert _read(moved)['value'] == 'redirected'
    assert _read(root / f'{token}_8.json')['value'] == 'redirected'
    assert _read(root / f'{token}.json')['value'] == 'redirected'
    after = _configured_tree()
    assert after == before, (after, before)


def test_the_results_root_governs_a_slot_read_and_its_cleanup(tmp):
    """A slot read and its cross-copy cleanup stay inside the passed root.

    Both roots hold a broadcast slot for this token, so a read resolved
    from configuration answers with the other tree's value and a consume
    resolved from configuration deletes the other tree's copy.
    """
    routes = _load('fixture_result_routes_slotroot')
    root = Path(tmp) / 'slot-results'
    root.mkdir(parents=True)
    token = 'slotroottok'
    routes.accept_result(
        RES_DIR, tmp, token,
        {'value': 'configured', '_did': 'slotroot-cfg'}, DELIVERY_CAP)
    routes.accept_result(
        root, tmp, token,
        {'value': 'redirected', '_did': 'slotroot-alt'}, DELIVERY_CAP)
    status, payload = routes.fetch_result(root, token, {})
    assert status == 200, (status, payload)
    assert payload.get('value') == 'redirected', payload
    status, payload = routes.fetch_result(
        root, token, {'delivery': ['slotroot-alt'], 'consume': ['1']})
    assert status == 200, (status, payload)
    assert not (root / f'{token}.json').exists()
    assert _read(_slot(token))['value'] == 'configured'


def test_the_results_root_governs_the_delivery_read(tmp):
    routes = _load('fixture_result_routes_readroot')
    root = Path(tmp) / 'read-results'
    empty = Path(tmp) / 'empty-results'
    root.mkdir(parents=True)
    empty.mkdir(parents=True)
    token, did = 'readroottok', 'read-1'
    routes.accept_result(
        root, tmp, token, {'tabId': '2', 'value': 'found', '_did': did},
        DELIVERY_CAP)
    status, payload = routes.fetch_result(root, token, {'delivery': [did]})
    assert status == 200, (status, payload)
    assert payload['value'] == 'found', payload
    status, payload = routes.fetch_result(empty, token, {'delivery': [did]})
    assert (status, payload) == (200, {'pending': True})


def test_the_passed_delivery_cap_is_the_one_enforced(tmp):
    """The cap comes from the argument, not from the configured 1024."""
    routes = _load('fixture_result_routes_cap')
    root = Path(tmp) / 'cap-results'
    root.mkdir(parents=True)
    token = 'captok'
    for did in ('cap-1', 'cap-2'):
        routes.accept_result(
            root, tmp, token, {'tabId': '1', 'value': did, '_did': did}, 1)
    kept = sorted(path.name for path
                  in (root / 'deliveries' / f'{token}_1').iterdir())
    assert kept == ['cap-2.json'], kept


def test_a_zero_delivery_cap_evicts_nothing(tmp):
    """Proves: cap 0 — the minimum `DAEDALUS_MAX_DELIVERY_RESULTS` admits —
    retains every delivery rather than evicting all of them. Does not prove the
    guard's `not max_results` clause, which is a no-op by arithmetic:
    `ordered[-0]` is `ordered[0]`, so the boundary is the oldest stamp;
    nothing is below it, and deleting that clause leaves this test green."""
    routes = _load('fixture_result_routes_nocap')
    root = Path(tmp) / 'nocap-results'
    root.mkdir(parents=True)
    token = 'nocaptok'
    for did in ('nocap-1', 'nocap-2'):
        routes.accept_result(
            root, tmp, token, {'tabId': '1', 'value': did, '_did': did}, 0)
    kept = sorted(path.name for path
                  in (root / 'deliveries' / f'{token}_1').iterdir())
    assert kept == ['nocap-1.json', 'nocap-2.json'], kept


def test_a_compat_consume_removes_the_delivery_copy_in_the_passed_root(tmp):
    """A `consume=1` read with no `delivery` finds the slot's own delivery
    copy through the root it was handed, and removes it there.

    Resolving that lookup from configuration looks for the copy in the
    configured tree, does not find it, and leaves the copy under the passed
    root behind for eviction.
    """
    routes = _load('fixture_result_routes_compatroot')
    root = Path(tmp) / 'compat-results'
    assert root != RES_DIR, 'the fixture made the roots equal'
    root.mkdir(parents=True)
    token, did = 'compatroottok', 'compatroot-1'
    status, payload = routes.accept_result(
        root, tmp, token,
        {'tabId': '2', 'value': 'paired', '_did': did}, DELIVERY_CAP)
    assert (status, payload) == (200, {'ok': True})
    delivery = root / 'deliveries' / f'{token}_2' / f'{did}.json'
    assert _read(delivery)['value'] == 'paired', delivery
    status, consumed = routes.fetch_result(
        root, token, {'tab': ['2'], 'consume': ['1']})
    assert status == 200 and consumed.get('value') == 'paired', consumed
    assert not (root / f'{token}_2.json').exists()
    assert not delivery.exists(), 'the delivery copy under this root stayed'


@contextlib.contextmanager
def _recorded_locks(store):
    """Record each stripe acquisition as (directory, key, lock id).

    All three, because each answers a different question. The directory is
    what the caller named; the key is what the stripe is decided on, and it
    is the half that is exact -- two keys can share one of 64 stripes by
    chance, so a lock count alone would let a mutation that keys on the
    caller's spelling through once in sixty-four runs; and the lock identity
    is the consequence, which one key always produces.
    """
    seen = []
    real_lock_for = store.delivery_lock_for
    real_key_for = store.delivery_stripe_key

    def recording_lock_for(target_dir):
        lock = real_lock_for(target_dir)
        seen.append((target_dir, real_key_for(target_dir), id(lock)))
        return lock

    store.delivery_lock_for = recording_lock_for
    try:
        yield seen
    finally:
        store.delivery_lock_for = real_lock_for


def _one_stripe(seen):
    """The directories named, the distinct keys, and the stripes reached."""
    return ([Path(d).name for d, _k, _l in seen],
            len({key for _d, key, _l in seen}),
            len({lock for _d, _k, lock in seen}))


def _folding_parent(tmp):
    """A real case-insensitive parent, or the reason this run has none.

    The scenario this suite cannot emulate honestly: a second caller that
    spells a target the other way has to CREATE and OPEN the files under
    that spelling, and name resolution is not the only thing a folding
    parent changes -- `open()` folds too, and no amount of patching
    `os.stat` makes a case-sensitive box fold an `open`. So the end-to-end
    fixtures run against a real one, named by the environment, and skip
    where there is none. The properties that ARE queries -- does the parent
    fold, does the guard accept, does one entry mean one stripe -- are
    emulated instead, by `tests/_case_fold.py`.
    """
    root = os.environ.get('DAEDALUS_CASE_FOLD_ROOT')
    if not root or not os.path.isdir(root):
        _util.skip('no case-folding parent named by DAEDALUS_CASE_FOLD_ROOT')
    probe = Path(root) / 'daedalus-case-fold-probe'
    probe.mkdir(exist_ok=True)
    try:
        variant = str(probe).replace('case-fold-probe', 'CASE-FOLD-PROBE')
        if not os.path.exists(variant):
            _util.skip(f'{root} does not fold case')
    finally:
        probe.rmdir()
    return Path(root)


def _folding_token(root, name):
    """A fresh subdirectory of the real folding parent to work in."""
    target = root / name
    if target.exists():
        _util.skip(f'{target} is already there from an earlier run')
    target.mkdir(parents=True)
    return target


def test_a_folded_target_is_one_directory_and_one_stripe(tmp):
    """Two spellings of one tab: one directory, one stripe, on a real parent.

    The filed scenario end to end, on a parent that actually folds: a
    delivery for `Foo` creates the directory, a delivery for `foo` reaches
    the same entry, and the two take one stripe at every site that locks the
    target -- the POST, the delivery read and the compatibility consume.
    """
    root = _folding_parent(tmp)
    work = _folding_token(root, Path(tmp).name)
    routes = _load('fixture_result_routes_real_fold')
    store = routes.result_store
    res_dir, cmd_dir = work / 'results', work / 'commands'
    cmd_dir.mkdir(parents=True)
    token = 'realfoldtok'
    with _recorded_locks(store) as seen:
        first = routes.accept_result(
            res_dir, cmd_dir, token,
            {'tabId': 'Foo', 'id': 'one', '_did': '1700000000000_a'},
            DELIVERY_CAP)
        second = routes.accept_result(
            res_dir, cmd_dir, token,
            {'tabId': 'foo', 'id': 'two', '_did': '1700000000000_b'},
            DELIVERY_CAP)
        landed = sorted(path.name for path in (
            res_dir / 'deliveries' / f'{token}_Foo').iterdir())
        read = routes.fetch_result(
            res_dir, token, {'tab': ['foo'], 'delivery': ['1700000000000_b']})
        kept = routes.fetch_result(
            res_dir, token,
            {'tab': ['Foo'], 'consume': ['1'],
             'expected': ['1700000000000_not_this_one']})
        consumed = routes.fetch_result(
            res_dir, token, {'tab': ['foo'], 'consume': ['1']})
    names, keys, stripes = _one_stripe(seen)
    assert first == (200, {'ok': True}), first
    assert second == (200, {'ok': True}), second
    assert read[0] == 200 and read[1].get('id') == 'two', read
    assert kept == (200, {'consumed': False}), kept
    assert consumed[0] == 200 and consumed[1].get('id') == 'two', consumed
    assert not (res_dir / 'deliveries' / f'{token}_Foo' / (
        '1700000000000_b.json')).exists(), 'the consumed copy stayed'
    # One entry on disk, both results in it, and one key for the lot.
    assert sorted(p.name for p in (res_dir / 'deliveries').iterdir()) == [
        f'{token}_Foo'], sorted(
            p.name for p in (res_dir / 'deliveries').iterdir())
    assert landed == ['1700000000000_a.json', '1700000000000_b.json'], (
        landed)
    # Two spellings reached it, which is the divergence the fixture exists
    # to drive, and one key and one lock came out the other side.
    assert set(names) == {f'{token}_Foo', f'{token}_foo'}, names
    assert keys == 1, seen
    assert stripes == 1, seen


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='resultroutes_')


if __name__ == '__main__':
    raise SystemExit(main())
