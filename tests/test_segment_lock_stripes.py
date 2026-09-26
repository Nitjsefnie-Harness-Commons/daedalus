#!/usr/bin/env python3
"""Segment storage locks: one job's stripe, and never two jobs'.

The segment relay used to hold one module-level lock across every job, so
independent HLS relays serialised behind each other's disk writes. The
invariant that made that lock load-bearing — two segments of the SAME job
cannot both spend the same remaining bytes — does not need it, because a
job's files are disjoint from every other job's. They are not entirely
disjoint: the namespace is flat, so the job named `a` and the job named
`a.json` own one filesystem entry between them. Both halves are pinned
here, and neither is pinned with a clock: the injected patch records which
lock object each caller was given, so a claim about who blocked whom is
settled by the record and not by how fast the machine was.
"""
import concurrent.futures
import json
import sys
import threading
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _segments import (BRIDGE_ENV, TOK, mint_job,  # noqa: E402
                       post_segment, seg_job)


# The stripe is selected inside the bridge process, whose per-process seed
# this test process does not share, so the unrelated job's name is chosen
# THERE: the holder walks candidates until one lands on a different lock and
# publishes the winner. A skip on "these two happened to collide" would be a
# 1-in-64 hole in the control, and a control that sometimes does not run is
# not a control.
_SITE_CUSTOMIZE = r'''
import os
import pathlib
import sys;sys.path.insert(0,'.');from daedalus_bridge import segment_store
import threading
import time
import traceback
gate = pathlib.Path(os.environ["SEG_GATE_DIR"])
held_job = os.environ.get("SEG_HELD_JOB", "")
call_lock = threading.Lock()
def record_lock_call(job, lock):
    with call_lock:
        with (gate / "lock-calls").open("a", encoding="utf-8") as handle:
            handle.write(f"{job}\t{id(lock)}\n")
def install():
    try:
        while not hasattr(segment_store, "seg_lock_for"):
            time.sleep(0.001)
        real_lock_for = segment_store.seg_lock_for
        def recording_lock_for(job):
            lock = real_lock_for(job)
            record_lock_call(job, lock)
            return lock
        segment_store.seg_lock_for = recording_lock_for
        if not held_job:
            (gate / "ready").write_text("y", encoding="utf-8")
            return
        held_lock = real_lock_for(held_job)
        (gate / "holder-lock").write_text(
            f"{held_job}\t{id(held_lock)}\n", encoding="utf-8")
        # An unrelated name is one whose stripe is provably not the held one,
        # so "this write completed" means it was not waiting on that lock
        # rather than that the machine got there first. The search is
        # bounded: against a scheme that gives every job the same lock, no
        # candidate differs, and an unbounded walk would turn that into a
        # hung fixture instead of the ordering failure this control exists
        # to report.
        # Candidates stay short: a name past the component cap is not a job
        # name, and a mint would refuse it before any lock was reached.
        unrelated = "unrelated"
        for attempt in range(256):
            unrelated = f"unrelated-{attempt}"
            if real_lock_for(unrelated) is not held_lock:
                break
        (gate / "unrelated-job").write_text(unrelated, encoding="utf-8")
        (gate / "ready").write_text("y", encoding="utf-8")
        while not (gate / "arm").exists():
            time.sleep(0.01)
        # Calls recorded before this point are the fixture's own mints, which
        # take a stripe too. Clearing them leaves the record holding only
        # what the test's requests did.
        (gate / "lock-calls").unlink(missing_ok=True)
        with held_lock:
            (gate / "holding").write_text("y", encoding="utf-8")
            try:
                (gate / "held").write_text("held", encoding="utf-8")
                while not (gate / "release").exists():
                    time.sleep(0.01)
            finally:
                (gate / "holding").unlink()
    except BaseException:
        (gate / "holder-error").write_text(
            traceback.format_exc(), encoding="utf-8")
threading.Thread(target=install, daemon=True).start()
'''


def _load_store():
    """Import segment_store the way the bridge does, with no config set."""
    return _util.load(_util.ROOT / 'daedalus_bridge' / 'segment_store.py',
                      name='segstripe_store')


def _recording_setup(tmp, held_job=''):
    """The in-process lock recorder, and the env that installs it."""
    patch_dir = Path(tmp) / 'segstripe-patch'
    patch_dir.mkdir()
    gate_dir = Path(tmp) / 'segstripe-gate'
    gate_dir.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        _SITE_CUSTOMIZE.lstrip(), encoding='utf-8')
    return gate_dir, {
        **BRIDGE_ENV, 'PYTHONPATH': str(patch_dir),
        'SEG_GATE_DIR': str(gate_dir), 'SEG_HELD_JOB': held_job}


def _lock_calls(gate_dir):
    """Every (job, lock id) the bridge child recorded, in call order."""
    path = gate_dir / 'lock-calls'
    if not path.is_file():
        return []
    return [tuple(line.split('\t', 1))
            for line in path.read_text(encoding='utf-8').splitlines()
            if line]


def _job_lock_ids(gate_dir, job):
    return [lock_id for name, lock_id in _lock_calls(gate_dir) if name == job]


def _holder_lock(gate_dir):
    """The (job, lock id) pair the injected holder took."""
    text = (gate_dir / 'holder-lock').read_text(encoding='utf-8')
    parts = text.strip().split('\t', 1)
    assert len(parts) == 2, parts
    return parts


def _await_lock_call(gate_dir, job, count):
    """Wait until the child recorded `count` stripe selections for `job`."""
    deadline = time.time() + 30
    while len(_job_lock_ids(gate_dir, job)) < count:
        assert time.time() < deadline, (
            f'{job!r} never selected a segment stripe: '
            f'{_lock_calls(gate_dir)!r}')
        time.sleep(0.01)


def _await_file(gate_dir, name, what):
    """Wait for the child to reach a state. Bounded, because a bridge that
    never starts is a broken fixture rather than a property under test."""
    deadline = time.time() + 30
    while not (gate_dir / name).exists():
        assert time.time() < deadline, f'{what} never happened'
        time.sleep(0.01)


def _holder_failure(gate_dir):
    """The holder's traceback, or None while it is still in charge."""
    path = gate_dir / 'holder-error'
    return path.read_text(encoding='utf-8') if path.is_file() else None


def _require_holder_holding(gate_dir):
    """Refuse to grade a run whose holder was not actually holding.

    A holder that failed, or that let the stripe go before the request
    reached it, would satisfy a "the other job was not blocked" assertion
    with nothing in the way. `holding` is removed by the holder's own
    `finally`, and `holder-error` outranks it: a holder that died and failed
    to clean up leaves a stale marker behind, and trusting that would call a
    run that held nothing a passing one.
    """
    failed = _holder_failure(gate_dir)
    if failed is not None:
        _util.skip(
            'the injected holder failed, so the property was never '
            'exercised: ' + failed)
    if not (gate_dir / 'holding').exists():
        _util.skip(
            'the injected holder released the job stripe before the request '
            'reached it, so the property was never exercised')


def test_a_dotted_name_and_its_own_name_take_one_stripe(tmp):
    """`a` and `a.json` own one filesystem entry, so they take one lock.

    The flat namespace is what makes this true: the job named `a` keeps its
    record at `<job>.json`, which is the very directory the job named
    `a.json` keeps its segments in. Stripping every trailing `.json` names
    the chain those names belong to, and the whole chain then shares a
    stripe — the two writes that touch one path cannot interleave.
    """
    store = _load_store()
    for base in ('a', 'relay-1', 'x.y', '.hidden', '.json', 'seg'):
        first = store.seg_lock_for(base)
        assert first is store.seg_lock_for(base), base
        assert first is store.seg_lock_for(base + '.json'), base
        assert first is store.seg_lock_for(base + '.json.json'), base
        assert first is store.seg_lock_for(base + '.json.json.json'), base
    # The strip is on the record affix and not on any dot: `a.b` keeps its
    # own name. The bookkeeping names a job also spends are refused at mint
    # (see `test_the_dirty_bookkeeping_name_is_refused_beside_its_job`), so
    # no other name can reach a path this chain does not already name.
    assert (store.seg_lock_for('a.b')
            is store.seg_lock_for('a.b.json')), 'a.b'
    unrelated = 'relay-2'
    for attempt in range(256):
        unrelated = f'relay-2-{attempt}'
        if store.seg_lock_for(unrelated) is not store.seg_lock_for('relay-1'):
            break
    assert store.seg_lock_for(unrelated) is not store.seg_lock_for(
        'relay-1'), (unrelated, 'two unrelated jobs share one stripe')


def test_job_stripes_come_from_a_table_that_never_grows(tmp):
    """Ten thousand caller-chosen job names reuse one fixed table.

    A job name is authenticated but arbitrary, so a table keyed by name
    would be a leak; the stripe index is what makes the table finite.
    """
    store = _load_store()
    table = tuple(store.seg_locks)
    assert len(table) == store.SEGMENT_LOCK_STRIPES
    for index in range(10_000):
        lock = store.seg_lock_for(f'job-{index}')
        assert any(lock is original for original in table), index
    assert len(store.seg_locks) == len(table), 'the lock table grew'


def test_a_held_job_stripe_blocks_only_that_job(tmp):
    """A held job stripe blocks that job's segment write and nothing else.

    The stripe is held inside the bridge process; this test only observes
    what that does to real requests. The injected patch records the lock
    every caller was given, so a failure can tell a request that took the
    wrong lock from a holder that fell over.
    """
    held_job = 'stripe-held'
    gate_dir, env = _recording_setup(tmp, held_job)
    with _util.bridge(tmp, env=env) as (base, _docroot):
        _await_file(gate_dir, 'ready', 'the lock recorder was installed')
        unrelated = (gate_dir / 'unrelated-job').read_text(encoding='utf-8')
        # Both jobs are minted before the holder arms: the mint takes a
        # stripe too, and this control is about the segment write.
        for job in (held_job, unrelated):
            status, body = mint_job(base, TOK, job)
            assert status == 200, (job, status, body)
        sigs = {job: mint_job(base, TOK, job)[1]['sig']
                for job in (held_job, unrelated)}

        (gate_dir / 'arm').write_text('arm', encoding='utf-8')
        _await_file(gate_dir, 'held', 'the job stripe was held')

        held_box = {}

        def post_held():
            try:
                held_box['value'] = post_segment(
                    base, held_job, sigs[held_job], '0', payload=b'abcdef')
            except Exception as exc:  # pylint: disable=broad-except
                held_box['error'] = exc

        held_thread = threading.Thread(target=post_held)
        held_thread.start()
        # The write is recorded when it SELECTS the stripe, which happens
        # before it blocks on it, so waiting for the record is how the test
        # learns the request reached the lock rather than dying earlier.
        _await_lock_call(gate_dir, held_job, 1)

        # A segment write to an unrelated job must complete while the held
        # job's write is provably still waiting on that same held stripe.
        try:
            status, body = post_segment(
                base, unrelated, sigs[unrelated], '0', payload=b'abcdef')
        except Exception as exc:  # pylint: disable=broad-except
            _require_holder_holding(gate_dir)
            raise AssertionError(
                'the unrelated job\'s segment write did not complete while '
                f'the {held_job!r} stripe was held: {exc!r}\n'
                f'lock-calls: {_lock_calls(gate_dir)!r}') from exc
        assert status == 200, (status, body)
        _require_holder_holding(gate_dir)
        holder = _holder_lock(gate_dir)
        # Its stripe is not the held one, so completing is what the property
        # predicts rather than an accident of scheduling, and the held
        # write is still inside the lock the holder owns.
        unrelated_ids = _job_lock_ids(gate_dir, unrelated)
        assert unrelated_ids, (holder, _lock_calls(gate_dir))
        assert set(unrelated_ids).isdisjoint({holder[1]}), (
            holder, unrelated_ids, _lock_calls(gate_dir))
        assert held_thread.is_alive(), (
            'the held job\'s write completed while its stripe was held: '
            f'{_lock_calls(gate_dir)!r}')

        (gate_dir / 'release').write_text('release', encoding='utf-8')
        held_thread.join(timeout=30)
        assert not held_thread.is_alive(), held_box
        assert held_box.get('error') is None, held_box
        assert held_box.get('value') == (200, b'{"ok": true}'), held_box

        failed = _holder_failure(gate_dir)
        if failed is not None:
            _util.skip(
                'the injected holder failed while the request was waiting, '
                'so the property was never exercised end to end: ' + failed)
        held_ids = _job_lock_ids(gate_dir, held_job)
        assert held_ids, (holder, _lock_calls(gate_dir))
        assert all(lock_id == holder[1] for lock_id in held_ids), (
            holder, held_ids, _lock_calls(gate_dir))


def test_two_writes_to_one_job_take_one_stripe_and_one_budget(tmp):
    """The same job's two segments still serialise on one lock object.

    This is the coupling the single lock existed for, and the one a
    per-job scheme has to keep: the usage read, the quota check, the
    publish and the record write stay under one hold, so two barrier-
    released requests cannot both spend the same remaining bytes.
    """
    gate_dir, env = _recording_setup(tmp)
    env.update({
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    })
    with _util.bridge(tmp, env=env) as (base, docroot):
        _await_file(gate_dir, 'ready', 'the lock recorder was installed')
        job = 'one-budget'
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        barrier = threading.Barrier(3)

        def post(segment):
            barrier.wait(timeout=10)
            return post_segment(base, job, sig, segment, payload=b'abc')

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(post, segment) for segment in ('0', '1')]
            barrier.wait(timeout=10)
            replies = [future.result(timeout=30) for future in futures]

        assert sorted(status for status, _body in replies) == [200, 413], (
            replies)
        stored = list((Path(docroot) / 'segments' / job).glob('*.ts'))
        assert len(stored) == 1 and stored[0].read_bytes() == b'abc', stored
        lock_ids = set(_job_lock_ids(gate_dir, job))
        assert len(lock_ids) == 1, (
            'one job reached more than one lock object: '
            f'{_lock_calls(gate_dir)!r}')


def _store_one_segment(base, job, sig):
    """POST one admitted segment, answering (status, body)."""
    return post_segment(base, job, sig, '0', payload=b'abc')


def _job_record(docroot, job):
    return json.loads(
        (Path(docroot) / 'segments' / f'{job}.json').read_text(
            encoding='utf-8'))


def test_the_dirty_bookkeeping_name_is_refused_beside_its_job(tmp):
    """`.{job}.json.dirty` is the marker `mark_dirty` writes, not a job name.

    A job named K spends four names under the segments root, and this is
    the marker among them. Minted as a job of its own it parks a directory
    exactly where the victim's first segment write has to put a file, and
    mark_dirty cannot, so every write for the victim answers 500 from then
    on. The refusal is the collision refusal's own answer, so a caller
    cannot read the reservation as an oracle for which names are in use.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        job = seg_job()
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        reserved = f'.{job}.json.dirty'
        status, body = mint_job(base, TOK, reserved)
        assert (status, body) == (409, {'error': 'job name unavailable'}), (
            status, body)
        # A refused mint writes nothing, so it cannot leave the directory
        # that would have broken the victim either.
        assert not (Path(docroot) / 'segments' / reserved).exists()
        # And the victim still stores: on the unfixed tree this is the
        # 500 the squatter causes, from this write onwards.
        status, body = _store_one_segment(base, job, sig)
        assert status == 200, (status, body)


def test_the_temp_bookkeeping_name_is_refused_beside_its_job(tmp):
    """`.{job}.json.tmp` is the temp `write_usage` replaces from.

    The same reservation as the dirty marker, with the quieter harm: the
    record write cannot land, so the job's stored totals stay at zero and
    every later write rescans the whole directory instead of trusting them.
    The control therefore checks the record after a successful write, not
    only the mint's answer.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        job = seg_job()
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        reserved = f'.{job}.json.tmp'
        status, body = mint_job(base, TOK, reserved)
        assert (status, body) == (409, {'error': 'job name unavailable'}), (
            status, body)
        assert not (Path(docroot) / 'segments' / reserved).exists()
        status, body = _store_one_segment(base, job, sig)
        assert status == 200, (status, body)
        record = _job_record(docroot, job)
        assert (record['stored_count'], record['stored_bytes']) == (1, 3), (
            record)
        mark = Path(docroot) / 'segments' / f'.{job}.json.dirty'
        assert not mark.exists(), 'the dirty mark was not cleared'


def test_a_bookkeeping_name_is_refused_with_no_owner_on_disk(tmp):
    """The reservation is structural: it holds with no victim minted.

    This is the order a conditional refusal gets wrong. With no `relay` on
    disk there is nothing to collide with, so a lookup admits the name —
    and the victim's own mint then succeeds into a namespace the squatter
    already holds, leaving every write for it answering 500. The shape is
    the same question whichever job is minted first, and it is the only
    form of the answer that cannot be raced by a concurrent mint.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, docroot):
        job = seg_job()
        reserved = f'.{job}.json.dirty'
        assert not (Path(docroot) / 'segments' / job).exists()
        status, body = mint_job(base, TOK, reserved)
        assert (status, body) == (409, {'error': 'job name unavailable'}), (
            status, body)
        assert not (Path(docroot) / 'segments' / reserved).exists()
        # And the victim that order was aimed at is unaffected.
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        status, body = _store_one_segment(base, job, body['sig'])
        assert status == 200, (status, body)


def test_the_reservation_refuses_only_the_two_bookkeeping_shapes(tmp):
    """A name that merely contains a dot, or has no job in it, still mints.

    The rule is the two bookkeeping shapes and nothing wider: a dotted job
    name, the two names with no job between the dot and the affix, a
    leading dot with a different affix, and a trailing temp affix without
    a leading one. Each of those is a name the layout never spends, so
    refusing any of them would be the bridge inventing a restriction.
    """
    with _util.bridge(tmp, env=BRIDGE_ENV) as (base, _docroot):
        for job in (f'{seg_job()}.1', '.json.dirty', '.json.tmp',
                    f'.{seg_job()}.dirty', f'{seg_job()}.json.tmp'):
            status, body = mint_job(base, TOK, job)
            assert status == 200, (job, status, body)
            status, body = _store_one_segment(base, job, body['sig'])
            assert status == 200, (job, status, body)


def test_case_spellings_take_one_stripe(tmp):
    """`Foo` and `foo` are one directory on a case-insensitive parent.

    The single lock this branch replaced held them together whatever the
    filesystem did with case. A key that folded only the record affix
    would split them, and two writes to one directory would interleave the
    usage read, the quota check and the record write that `store_segment`
    holds as one.
    """
    store = _load_store()
    assert (store.seg_lock_for('Foo')
            is store.seg_lock_for('foo')), 'case spellings took two stripes'
    assert (store.seg_lock_for('Foo.json')
            is store.seg_lock_for('foo.JSON')), (
                'case spellings split the record chain')


def test_normalisation_spellings_take_one_stripe(tmp):
    """A composed and a decomposed spelling of one name take one stripe.

    Both spellings are written out rather than derived from one another: a
    control that built the second from the first would be testing the
    construction, and would pass against a key that folds nothing. They are
    spelled as escapes so the two stay two spellings in the file — an editor
    or a checkout that normalises source to NFC merges two raw literals into
    one, which is how this control silently stops testing anything. The first
    two assertions are the backstop for that.
    """
    composed = 'caf\u00e9'     # LATIN SMALL LETTER E WITH
    decomposed = 'cafe\u0301'  # e, then COMBINING ACUTE
    assert composed != decomposed, 'the two literals are one spelling'
    assert unicodedata.is_normalized('NFC', composed), composed
    assert unicodedata.is_normalized('NFD', decomposed), decomposed
    store = _load_store()
    assert (store.seg_lock_for(composed)
            is store.seg_lock_for(decomposed)), (
                'the two spellings of one name took two stripes')
    assert (store.seg_lock_for(f'{composed}.json')
            is store.seg_lock_for(f'{decomposed}.JSON')), (
                'the spellings split the record chain')


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='seglockstripes_')


if __name__ == '__main__':
    raise SystemExit(main())
