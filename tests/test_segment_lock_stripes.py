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
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _seg_lock_seam import SITE_CUSTOMIZE  # noqa: E402
from _segments import (BRIDGE_ENV, TOK, mint_job,  # noqa: E402
                       post_segment, seg_job)


def _recording_setup(tmp, held_job='', park_job=''):
    """The injected seams, and the env that installs them."""
    patch_dir = Path(tmp) / 'segstripe-patch'
    patch_dir.mkdir()
    gate_dir = Path(tmp) / 'segstripe-gate'
    gate_dir.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        SITE_CUSTOMIZE.lstrip(), encoding='utf-8')
    return gate_dir, {
        **BRIDGE_ENV, 'PYTHONPATH': str(patch_dir),
        'SEG_GATE_DIR': str(gate_dir), 'SEG_HELD_JOB': held_job,
        'SEG_PARK_JOB': park_job}


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
    # `holding-acquired` is written by the ACQUIRE path when a real acquire
    # completes while the holder has announced. `holding` is the holder's own
    # announcement and is on disk whether or not it ever took the lock, so a
    # premise read from it would pass a run that held nothing — which is how
    # a neutered fixture used to turn this control green. A premise the
    # control did not earn must skip the run, not grade it.
    if not (gate_dir / 'holding-acquired').exists():
        _util.skip(
            'the injected holder announced a hold but no acquire completed '
            'under it, so the stripe was never held and the property was not '
            'exercised')


def _shared_lock_witness(gate_dir, held_job, unrelated):
    """The two jobs were handed ONE lock object, with the ids.

    This is the structural answer to "is the red the host or the lock?" — a
    fact about lock identity that no amount of machine speed changes. The
    control asserts it on the failure path, because a witness that only
    appears in a log line nobody reads is not a control.
    """
    held = _job_lock_ids(gate_dir, held_job)
    other = _job_lock_ids(gate_dir, unrelated)
    return sorted(set(held) & set(other)), held, other


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
            shared, held_ids, other_ids = _shared_lock_witness(
                gate_dir, held_job, unrelated)
            # The red is the lock, not a slow host: the two jobs were given
            # one lock object. If they were not, this is some other fault and
            # the message has to say so rather than borrow this explanation.
            assert shared, (
                'the unrelated write did not complete, but the two jobs were '
                'NOT given one lock object, so this is not the single-lock '
                f'defect: held={held_ids!r} unrelated={other_ids!r}\n'
                f'{exc!r}')
            raise AssertionError(
                'the unrelated job\'s segment write did not complete while '
                f'the {held_job!r} stripe was held: {exc!r}\n'
                f'witness: both jobs were handed the same lock object '
                f'{shared!r} — held={held_ids!r} unrelated={other_ids!r}, '
                'so this is the lock and not the host speed\n'
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
        # Recorded, not sampled: the held job acquiring a lock while its own
        # stripe is held IS the claim that it was blocked, and it is a fact
        # about an acquire rather than a moment's reading of a thread. The
        # liveness sample that used to stand here went green whenever the
        # write was merely slow, and it went RED whenever the hold was
        # neutered even though the control's claim — an unrelated job's write
        # completes — was satisfied. A fixture that announces it is holding
        # without holding is not a block, and the seam now counts real holds
        # rather than reading that announcement.
        (gate_dir / 'release').write_text('release', encoding='utf-8')
        held_thread.join(timeout=30)
        assert not held_thread.is_alive(), held_box
        assert held_box.get('error') is None, held_box
        assert held_box.get('value') == (200, b'{"ok": true}'), held_box

        # Read AFTER the join, where a marker can exist at all. Before it,
        # the only thread that could record `overlap-<held job>` is the held
        # write — which is blocked inside acquire() and never reaches the
        # recording line — so the assertion was reading a file that had not
        # been written, and a marker the run does produce is
        # `overlap-<unrelated>`, which this does not read.
        #
        # CORROBORATING, not load-bearing. The two assertions that carry
        # this control are the settlement hand-off — three requests recorded
        # an outcome, which a site on its own stripe cannot do — and the
        # `asked == job` check in the site-agreement control, which is exact
        # where this is a hash. Deleting the hand-off because this marker
        # looks sufficient is how this control goes green with a site
        # pointed at its own stripe; `_overlap_report`'s docstring names
        # the two.
        assert not _overlap_report(gate_dir, held_job), \
            _overlap_report(gate_dir, held_job)

        failed = _holder_failure(gate_dir)
        if failed is not None:
            _util.skip(
                'the injected holder failed while the request was waiting, '
                'so the property was never exercised end to end: ' + failed)
        held_ids = _job_lock_ids(gate_dir, held_job)
        assert held_ids, (holder, _lock_calls(gate_dir))
        assert all(lock_id == holder[1] for lock_id in held_ids), (
            holder, held_ids, _lock_calls(gate_dir))


def _job_record(docroot, job):
    """The record the bridge wrote for `job` under a bridge-owned root."""
    return json.loads(
        (Path(docroot) / 'segments' / f'{job}.json').read_text(
            encoding='utf-8'))


def _holder_thread(gate_dir):
    """The injected holder's thread ident, recorded from inside its hold."""
    path = gate_dir / 'holder-thread'
    return path.read_text(encoding='utf-8').strip() if path.is_file() else ''


def _settlements(gate_dir):
    """The recorded outcomes, as (thread ident, name asked for, outcome)."""
    path = gate_dir / 'settled'
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        parts = line.split()
        if len(parts) == 3:
            out.append(tuple(parts))
    return out


def _await_settlements(gate_dir, count, exclude):
    """Wait until `count` distinct requests have each recorded an outcome.

    A hand-off, not a count and not a poll interval. Every request records a
    settlement — its thread, the name it asked for, and whether it was kept
    out of the lock or took one — and the release waits for one record per
    request, identified by its thread, so a request that contributes two
    records cannot stand in for two.

    The `blocked` half is what carries the release: a site on its own stripe
    acquires, so it never contributes one, and a run in which all three
    settled as blocked cannot have had one. It waits for the records and not
    for the overlap, because an overlap is the DEFECT and the release must not
    depend on having already seen it.
    """
    deadline = time.time() + 60
    while True:
        idents = {ident for ident, _name, _outcome in _settlements(gate_dir)
                  if ident != exclude}
        if len(idents) >= count:
            return
        assert time.time() < deadline, (
            f'only {len(idents)} of {count} requests recorded an outcome: '
            f'{_settlements(gate_dir)!r}')
        time.sleep(0.01)


def _overlap_report(gate_dir, job=None):
    """Evidence that a hold did not cover what it was supposed to cover.

    `job` names one: the claim is that THIS job took a lock while its own
    stripe was held, read from the per-job record. `job` omitted: the claim
    is that nothing took any lock while a hold was in place, read from the
    global record. The site-agreement control wants the second (all three of
    its requests are for one job, so any acquisition during the hold is the
    defect); the held-stripe control wants the first, because an unrelated
    job is *supposed* to take its own stripe while another is held.

    What it is NOT: the assertion that carries these controls. The hand-off
    in `_await_settlements` is — three distinct requests each recording an
    outcome, which a site pointed at its own stripe cannot produce — and so
    is the `asked == job` check in the site-agreement control, which is
    exact where this is a hash. This marker is CORROBORATING: it names which
    job took a lock during a hold, which the other two do not, and it is
    worth reading when one of them fires.

    Read it only once every request has finished. A selection trace cannot
    answer this — a request can be seen choosing a stripe and still be
    blocked on it — so what is recorded is the acquire itself, against the
    state of the world at that instant. That is the difference between this
    and a thread's liveness sampled at a moment. Read it BEFORE the release
    and it says nothing: the only thread that can record a per-job marker
    is the blocked one.
    """
    path = gate_dir / ('overlap' if job is None else f'overlap-{job}')
    if not path.exists():
        return None
    if job is None:
        return ('a lock was taken while a hold was in place, by '
                f'{path.read_text(encoding="utf-8").split()!r}\n'
                f'lock-calls: {_lock_calls(gate_dir)!r}')
    return (f'{job!r} took a lock while that same job\'s stripe was held: '
            f'{path.read_text(encoding="utf-8")!r}\n'
            f'lock-calls: {_lock_calls(gate_dir)!r}')


def test_two_writes_to_one_job_stay_atomic_inside_the_critical_section(tmp):
    """Two writes to one job cannot both spend the same remaining bytes.

    The overlap is FORCED, not raced for. The injected patch parks the first
    write inside the critical section — after its quota check, before it
    publishes — and releases it only once the second request has been seen at
    a lock. With the hold in place the second request reads totals the first
    is about to invalidate and is refused; with the hold removed it reads
    them instead, accepts, and both answer 200. A barrier released before the
    requests are sent cannot produce that ordering, which is why this control
    exists rather than a repeated barrier trial.

    Every assertion here is a consequence: the answers, the files on disk and
    what the record claims. The injected overlap marker is only ever read
    after both requests have finished, so it is evidence for a failure and
    never a substitute for one.

    The budget assertion is the one that normally reports this defect. A
    mutation that does not remove the hold but lets the two writes drift
    further apart — a park released far too late, say — is caught by the
    `join` timeout instead, and that assertion moves with host speed. It is
    named here so a reader who sees a timeout red knows which assertion
    spoke and why.
    """
    job = 'one-budget'
    gate_dir, env = _recording_setup(tmp, park_job=job)
    env.update({
        'DAEDALUS_MAX_SEGMENT_INDEX': '10',
        'DAEDALUS_MAX_SEGMENTS_PER_JOB': '2',
        'DAEDALUS_MAX_SEGMENT_JOB_SIZE': '5',
    })
    with _util.bridge(tmp, env=env) as (base, docroot):
        _await_file(gate_dir, 'ready', 'the injected seams were installed')
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']

        (gate_dir / 'arm-park').write_text('arm', encoding='utf-8')
        boxes = {}

        def post(name, segment):
            def run():
                try:
                    boxes[name] = post_segment(
                        base, job, sig, segment, payload=b'abc')
                except Exception as exc:  # pylint: disable=broad-except
                    boxes[name] = exc
            return run

        first = threading.Thread(target=post('first', '0'))
        first.start()
        # The first write is now inside the hold, quota checked, unpublished.
        _await_file(gate_dir, 'parked', 'the first write parked in the hold')
        second = threading.Thread(target=post('second', '1'))
        second.start()
        for thread in (first, second):
            thread.join(timeout=60)
            assert not thread.is_alive(), (
                'a segment write never finished: '
                f'{boxes!r} {_lock_calls(gate_dir)!r}')

        replies = sorted(
            value for value in boxes.values()
            if not isinstance(value, Exception))
        assert len(replies) == 2, boxes
        overlap = _overlap_report(gate_dir, job)
        assert sorted(status for status, _b in replies) == [200, 413], (
            f'both writes to one job were accepted, so two of them spent the '
            f'same remaining bytes: {replies!r}\n{overlap or ""}')
        stored = sorted((Path(docroot) / 'segments' / job).glob('*.ts'))
        assert len(stored) == 1, f'two segments were published: {stored}'
        record = _job_record(docroot, job)
        assert (record['stored_count'], record['stored_bytes']) == (1, 3), (
            f'the record does not match what is on disk: {record} {stored}')
        assert not overlap, overlap
        assert not (gate_dir / 'holder-error').exists(), _holder_failure(
            gate_dir)


def test_every_site_takes_one_stripe_for_a_job(tmp):
    """The mint, the lookup and the write serialise on one job's stripe.

    Four sites used to share one module-level lock and so agreed by
    construction. Keyed, they agree only while each asks for the same job's
    lock, and the load-bearing pair cannot drift at all now that the
    admission carries what the write path holds. The holder parks inside one
    job's stripe; a site that asked for a different one would acquire it
    while the holder is provably inside this one, and the injected seam
    records that acquire.

    The three requests are released only once each has been recorded BLOCKED
    from the held lock — a real failed acquire against a counted hold, not an
    arrival, which proves nothing. So a site off on its own stripe has
    necessarily acquired during the hold, and the marker is read after every
    request has finished.

    Which assertion kills which direction, since a reader would otherwise not
    know what this control is carrying:

    - the overlap assertion kills a site pointed at a stripe of its own,
      which is the `lookup` and `admit` half;
    - it ALSO kills the mint's accounting race, and that is the `mint` half.
      A mint on another stripe reconciles the record — recount, mark_dirty,
      write_usage — with nothing holding it apart from a concurrent write's
      usage read, quota check and record write. Both mutations of that shape
      survive every accounting assertion in the tree; what catches them is
      this one, because a reconcile that ran during the hold is exactly an
      acquire during the hold. No accounting assertion elsewhere would fail.
    """
    job = seg_job()
    gate_dir, env = _recording_setup(tmp, held_job=job)
    with _util.bridge(tmp, env=env) as (base, _docroot):
        _await_file(gate_dir, 'ready', 'the injected seams were installed')
        status, body = mint_job(base, TOK, job)
        assert status == 200, (status, body)
        sig = body['sig']
        (gate_dir / 'arm').write_text('arm', encoding='utf-8')
        _await_file(gate_dir, 'held', 'the job stripe was held')

        answers = {}

        def ask(name, call):
            def run():
                try:
                    answers[name] = call()
                except Exception as exc:  # pylint: disable=broad-except
                    answers[name] = exc
            return run

        threads = [
            threading.Thread(target=ask('write', lambda: post_segment(
                base, job, sig, '0', payload=b'abc'))),
            threading.Thread(target=ask('mint', lambda: mint_job(
                base, TOK, job))),
            threading.Thread(target=ask('lookup', lambda: _util.get_json(
                f'{base}/segment-job?token={TOK}&job={job}'))),
        ]
        for thread in threads:
            thread.start()
        exclude = _holder_thread(gate_dir)
        _await_settlements(gate_dir, 3, exclude)
        (gate_dir / 'release').write_text('release', encoding='utf-8')
        for thread in threads:
            thread.join(timeout=60)
            assert not thread.is_alive(), (
                f'a request never finished: {answers!r} '
                f'{_lock_calls(gate_dir)!r}')

        overlap = _overlap_report(gate_dir)
        failed = _holder_failure(gate_dir)
        assert failed is None, failed
        assert not overlap, (
            'a site took a different stripe for one job, so the hold never '
            f'covered it: {overlap}\nlock-calls: {_lock_calls(gate_dir)!r}')
        assert answers['write'][0] == 200, answers
        # The mint and the lookup are both idempotent resumes of one job.
        assert answers['mint'] == (200, {'ok': True, 'sig': sig}), answers
        assert answers['lookup'] == (200, {'ok': True, 'sig': sig}), answers
        # Every request asked for THIS job, by name. That is exact — no hash
        # and no stripe count between the record and the claim — so a site
        # that decorates the name to reach a stripe of its own is caught here
        # by the name it asked for rather than by a coincidence of hashing.
        # The old form compared lock ids for the exact job name, which a
        # decorated name simply never appears under.
        for ident, asked, outcome in _settlements(gate_dir):
            assert asked == job, (
                f'thread {ident} was handed {asked!r} for job {job!r} '
                f'({outcome}); every site must ask for the job by name')
        held_ids = set(_job_lock_ids(gate_dir, job))
        assert held_ids == {_holder_lock(gate_dir)[1]}, (held_ids, job)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='seglockstripes_')


if __name__ == '__main__':
    raise SystemExit(main())
