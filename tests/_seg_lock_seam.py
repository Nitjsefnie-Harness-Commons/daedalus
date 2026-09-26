"""The seam `test_segment_lock_stripes.py` injects into the bridge child.

A `sitecustomize.py` the test writes into a directory on the child's
`PYTHONPATH` wraps the segment lock so the controls can see holds rather
than infer them. It lives here as a module rather than as a string
constant inside the suite for two reasons: its comments are then real
comments, so the end-of-branch conciseness pass can cut them and the
comments-only commit's docstring-stripped AST proof can see that it did;
and they are linted like everything else instead of hiding inside a
literal.

The constant is the file's body, injected verbatim, and it keeps the
child's own import line — the child resolves `daedalus_bridge` from its
working directory, not from this suite.
"""
SITE_CUSTOMIZE = r'''
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
held = [0]
last_acquirer = [None]
dirty_calls = [0]

def note(name, text):
    with call_lock:
        with (gate / name).open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")

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
        mine = threading.get_ident()
        if not self._real.acquire(timeout=_ACQUIRE_GRACE):
            # One line each, in a shared file: three requests for ONE job
            # would otherwise write one filename and count as one.
            note("blocked", self._job)
            note(f"blocked-{self._job}", "waited")
            note("settled", f"{mine} {self._job} blocked")
            self._real.acquire()
        else:
            # The other half of the hand-off. A request that took a lock
            # during the hold has also recorded what it was asked for, which
            # is what makes the name check exact rather than hashed.
            note("settled", f"{mine} {self._job} acquired")
        last_acquirer[0] = mine
        # Read BEFORE this acquire joins the count: the question is whether
        # some OTHER lock was already held, and a lock counts itself the
        # moment it is taken.
        already = held[0] > 0
        held[0] += 1
        # A holder announces `holding` BEFORE it acquires, so an acquire
        # completing while that announcement is up is proof a hold was
        # really taken. A holder that announces and does not hold never gets
        # here, and the control that reads this marker skips instead of
        # grading a run that held nothing.
        #
        # Never cleared: it is written once and read only while the holder
        # is parked. Clearing it on release would let any OTHER request that
        # took and dropped its own stripe mid-hold erase the witness — which
        # is how the premise check was reading a run as unheld.
        if (gate / "holding").exists():
            (gate / "holding-acquired").write_text(
                "acquired", encoding="utf-8")
        if already:
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
        held[0] -= 1
        self._real.release()
        return False

def record_lock_call(job, lock):
    with call_lock:
        with (gate / "lock-calls").open("a", encoding="utf-8") as handle:
            handle.write(f"{job}\t{id(lock)}\n")

def _blocked():
    """How many acquires have been recorded as kept out of a lock."""
    path = gate / "blocked"
    if not path.is_file():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


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
                # Two ways out, and BOTH are recorded events rather than
                # arrivals. A request that merely REACHED a lock has not
                # shown it was kept out of one, and waiting on that is what
                # let this control go green with the hold removed: the poll
                # landed in the window between the arrival and the acquire.
                #
                #  - the other request was recorded BLOCKED from this job's
                #    lock, a real failed acquire against a counted hold. It
                #    cannot get past, so this thread may finish and the
                #    other will read what this one wrote.
                #  - the other request reached its OWN mark_dirty, which is
                #    past its usage read and quota check. That only happens
                #    when this thread was not holding, so waiting here is
                #    what forces the torn state to be visible instead of
                #    leaving both answers to a race.
                try:
                    while not (dirty_calls[0] >= 2 or _blocked() >= 1):
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
        # Announced BEFORE the acquire, so the acquire path can witness that
        # a hold really happened. A neutered holder announces here and never
        # acquires, which is exactly what the premise check has to notice.
        (gate / "holding").write_text("announcing", encoding="utf-8")
        with Signalled(held_lock, held_job):
            (gate / "holder-thread").write_text(
                str(threading.get_ident()), encoding="utf-8")
            # Counted from inside the hold, so the line is the requests'
            # own and the holder's acquisition is not one of them. No
            # request is in flight yet: the test starts them after `held`.
            (gate / "lock-waits").unlink(missing_ok=True)
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
