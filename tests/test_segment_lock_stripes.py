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
park_job = os.environ.get("SEG_PARK_JOB", "")
call_lock = threading.Lock()
_ACQUIRE_GRACE = 0.25
last_acquirer = [None]
dirty_calls = [0]

def note(name, text):
    with call_lock:
        with (gate / name).open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")

def provably_occupied():
    """Whether some injected holder is inside a lock right now."""
    return (gate / "holding").exists() or (gate / "parked").exists()

class Signalled:
    """A lock that records being ACQUIRED, not merely being chosen.

    The point is that a selection trace cannot tell a hold from a lookup.
    This records the acquire itself, and records it against the state of the
    world at that instant: a lock taken while another holder is provably
    inside one is an overlap, and no amount of later sampling can unmake it.

    The marker is named for the JOB, because "a lock was taken during the
    hold" is not on its own a defect: an unrelated job is supposed to take
    its own stripe during a hold. Only a named job acquiring while ITS OWN
    stripe is held says the hold did not cover it.
    """
    def __init__(self, real, job):
        self._real = real
        self._job = job
    def __enter__(self):
        note("lock-waits", "wait")
        # A bounded attempt, so "this request could not get the lock while
        # the hold was in place" is an EVENT the test can wait for rather
        # than a sample it has to take. The grace is fixture-internal: the
        # marker it produces is a fact about the acquire, and a site on
        # another stripe simply succeeds inside the window and is recorded
        # as an overlap instead.
        if not self._real.acquire(timeout=_ACQUIRE_GRACE):
            # One line each, in a shared file: three requests for ONE job
            # would otherwise write one filename and count as one.
            note("blocked", self._job)
            note(f"blocked-{self._job}", "waited")
            self._real.acquire()
        last_acquirer[0] = threading.get_ident()
        if provably_occupied():
            # Two records, because the two questions differ. The global one
            # says a lock was taken while another was held, and names the
            # job that asked for it. The per-job one is that same fact keyed
            # by the name asked for — which a mutation decorates, so a
            # control that means "THIS job's hold did not cover it" reads
            # the per-job file and one that means "nothing at all may take
            # a lock during this hold" reads the global one.
            note("overlap", self._job)
            note(f"overlap-{self._job}", "acquired-while-occupied")
        return self._real
    def __exit__(self, *exc):
        self._real.release()
        return False

def record_lock_call(job, lock):
    with call_lock:
        with (gate / "lock-calls").open("a", encoding="utf-8") as handle:
            handle.write(f"{job}\t{id(lock)}\n")

def _waits():
    path = gate / "lock-waits"
    if not path.is_file():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())

def install():
    try:
        while not hasattr(segment_store, "seg_lock_for"):
            time.sleep(0.001)
        real_lock_for = segment_store.seg_lock_for
        def wrapped_lock_for(job):
            lock = real_lock_for(job)
            record_lock_call(job, lock)
            return Signalled(lock, job)
        segment_store.seg_lock_for = wrapped_lock_for

        if park_job:
            real_mark_dirty = segment_store.mark_dirty
            parked = []
            def parking_mark_dirty(root, job):
                # The first armed call parks INSIDE the critical section,
                # after the quota check and before the publish. A second
                # request that can reach here has read totals the parked
                # request is about to invalidate, which is the race.
                if job != park_job or not (gate / "arm-park").exists():
                    return real_mark_dirty(root, job)
                # Every armed call counts, not only the one that parks: the
                # second request's mark_dirty is the signal that it has
                # read its usage and passed its quota check.
                dirty_calls[0] += 1
                if parked:
                    return real_mark_dirty(root, job)
                parked.append(job)
                # Counted from here, not from process start: this thread
                # has already been through its own lock, and the mint
                # before it, and only the SECOND request's acquisition is
                # the thing worth waiting for.
                (gate / "lock-waits").unlink(missing_ok=True)
                (gate / "parked").write_text("y", encoding="utf-8")
                # Two ways out, and which one applies is what the control
                # measures rather than a deadline:
                #
                #  - this thread still owns the lock and the other request
                #    has reached one. It cannot get past, so this thread
                #    may finish and the other will read what it wrote.
                #  - the other request has reached its OWN mark_dirty,
                #    which is past its usage read and quota check. That
                #    only happens when this thread was not holding, so
                #    waiting here is what forces the torn state to be
                #    visible instead of leaving both answers to a race.
                mine = threading.get_ident()
                try:
                    while not (dirty_calls[0] >= 2
                               or (_waits() >= 1
                                   and last_acquirer[0] == mine)):
                        time.sleep(0.005)
                finally:
                    # The marker is this thread being inside the hold, so
                    # it goes when the hold lets go: a later acquire by the
                    # second request is no longer an overlap.
                    (gate / "parked").unlink(missing_ok=True)
                return real_mark_dirty(root, job)
            segment_store.mark_dirty = parking_mark_dirty

        if held_job:
            held_lock = real_lock_for(held_job)
            (gate / "holder-lock").write_text(
                f"{held_job}\t{id(held_lock)}\n", encoding="utf-8")
            unrelated = "unrelated"
            for attempt in range(256):
                unrelated = f"unrelated-{attempt}"
                if real_lock_for(unrelated) is not held_lock:
                    break
            (gate / "unrelated-job").write_text(unrelated, encoding="utf-8")
        (gate / "ready").write_text("y", encoding="utf-8")
        if not held_job:
            return
        while not (gate / "arm").exists():
            time.sleep(0.01)
        (gate / "lock-calls").unlink(missing_ok=True)
        with Signalled(held_lock, held_job):
            # Counted from inside the hold, so the line is the requests'
            # own and the holder's acquisition is not one of them. No
            # request is in flight yet: the test starts them after `held`.
            (gate / "lock-waits").unlink(missing_ok=True)
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


def _recording_setup(tmp, held_job='', park_job=''):
    """The injected seams, and the env that installs them."""
    patch_dir = Path(tmp) / 'segstripe-patch'
    patch_dir.mkdir()
    gate_dir = Path(tmp) / 'segstripe-gate'
    gate_dir.mkdir()
    (patch_dir / 'sitecustomize.py').write_text(
        _SITE_CUSTOMIZE.lstrip(), encoding='utf-8')
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
    if not (gate_dir / 'holding').exists():
        _util.skip(
            'the injected holder released the job stripe before the request '
            'reached it, so the property was never exercised')


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
        # Recorded, not sampled: the held job acquiring a lock while its own
        # stripe is held IS the claim that it was not blocked. Asking whether
        # the thread is still alive instead would be a sample of a moment,
        # and it goes green by accident whenever the write is merely slow.
        overlap = _overlap_report(gate_dir, held_job)
        assert not overlap, overlap
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


def _job_record(docroot, job):
    """The record the bridge wrote for `job` under a bridge-owned root."""
    return json.loads(
        (Path(docroot) / 'segments' / f'{job}.json').read_text(
            encoding='utf-8'))


def _await_blocked_or_overlap(gate_dir, count):
    """Wait until `count` requests have provably failed to take the lock.

    A request that merely REACHED a lock has not shown it was kept out of
    one: its acquire can still complete after the hold is released, which is
    how an off-stripe site slips past a control that only counted arrivals.
    The seam's bounded attempt turns "could not get it while the hold was in
    place" into a recorded fact, and this waits for that fact from every
    request — or for the overlap that a site on its own stripe produces
    instead, which is the other way round.
    """
    deadline = time.time() + 30
    while True:
        marks = gate_dir / 'blocked'
        blocked = (len(marks.read_text(encoding='utf-8').splitlines())
                   if marks.is_file() else 0)
        if (gate_dir / 'overlap').exists():
            return
        if blocked >= count:
            return
        assert time.time() < deadline, (
            f'only {blocked} of {count} requests were kept out of the held '
            f'lock, and no overlap was recorded')
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

    Read only once every request has finished. A selection trace cannot
    answer this — a request can be seen choosing a stripe and still be
    blocked on it — so what is recorded is the acquire itself, against the
    state of the world at that instant. That is the difference between this
    and a thread's liveness sampled at a moment.
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

    The three requests are released only once each has been seen AT a lock —
    a positive observation, three `lock-waits` lines, not a deadline. So a
    site off on its own stripe has necessarily acquired during the hold, and
    the marker is read after every request has finished.
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
        _await_blocked_or_overlap(gate_dir, 3)
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
        assert set(_job_lock_ids(gate_dir, job)) == {_holder_lock(gate_dir)[1]}


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='seglockstripes_')


if __name__ == '__main__':
    raise SystemExit(main())
