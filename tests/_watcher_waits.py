"""The waits a watcher-budget case measures its children through.

Not a suite itself — run_tests.py only loads `test_*.py`.

Every wait here synchronises on what the process under test DID - a line
drained from its stream, a call appended to the log, a pid gone - and not
on how long the runner took to do it. The clock appears exactly once, as
the failure-reporting backstop on the single wait with no live process to
give up on, and once more as the bound on a reap that follows a kill and
can only return.
"""
import subprocess
import sys
import threading
import time

from _processtree import cleanup_process_tree
from _watcher_fixtures import IDLE_POLL_BOUND

# The backstop on the one wait that cannot end on a state. 90s is the
# figure tests/test_parent_watch.py already waits a real grandchild's death
# with, and the suites job allows twenty minutes for the whole file, so a
# survivor is named long before the job's own limit ends the run nameless.
BACKSTOP = 90
# A wake-up interval, never a deadline: the waits below end on what the
# process under test did.
POLL = 0.05
# The bound on the reap that follows a cancel, and on the cancel itself.
CANCEL_BOUND = 60
# The widest poll, in gh calls, a healthy run may spend and still be
# tolerated by `await_polls` - which is exactly what the code enforces: a
# run is only over the bound once its logged calls exceed this many PER
# PUBLISHED MARKER, and a healthy run of this width never does, because
# `m` markers have been published by a poll making at most `m` of them.
#
# It is a TOLERANCE and not a cap, and nothing in the domain caps it: one
# poll of `pr_comment_watch.poll` spends
# `max(pages the reviews connection needs, pages the comments connection
# needs)` plus one follow-up per review carrying more than PAGE_SIZE
# inline comments, every term of it data-dependent, and a poll of
# `ci_watch` spends one call per page of check contexts. The measured
# floor is 2 - the widest poll any control in this tree measures
# (test_a_loop_that_repeats_its_last_request_costs_two) - so the headroom
# is for the pagination and follow-up queries a real pull request adds on
# top. The two controls in tests/test_watcher_poll_index.py drive a poll
# of exactly this width and of one more, so the margin either side of the
# boundary is a run and not an argument.
POLL_WIDTH = 8
# How many published boundaries a refusal renders before it says how many
# more there are. A re-use shows inside the first cycle, and a run longer
# than the bound is itself evidence: a healthy run's are all new.
SEQUENCE = 12
# The four renderings `poll_sequence` can produce, and what each means. A
# rule written over a COLLAPSED rendering has to enumerate what the
# collapse HIDES as well as what it shows, and what it hides is a form of
# its own: a run of new values that has STOPPED looks exactly like a run
# of new values that is merely short, and reading it as anything else
# sends a reader after a wide poll in a watcher whose index is stuck. So
# a fifth cause is a fifth row here, and `test_watcher_waits.py` has one
# control per row asserting the clause's own text - which is what makes
# the space reviewable rather than a sentence nobody can diff.
READINGS = (
    'a value that comes round again is a re-used index',
    'a run still going, of new values only, is a poll that cost more than '
    'the tolerance',
    'a run that has stopped is a run that stopped advancing: something '
    'published a new boundary and then nothing did',
    'a single value and nothing after it reads alike as an index stuck '
    'where it started and as one wide first poll, and this log cannot '
    f'say which - IDLE_POLL_BOUND ({IDLE_POLL_BOUND}) is what refuses the '
    'second on a healthy watcher',
)


def _reading(sequence):
    """Which row of `READINGS` a rendered sequence is, by its own shape.

    The index, not the clause, so the clause is written once. The order is
    the order the rows are tested in and a control asserts the clause's
    TEXT rather than its position, so adding or reordering a row cannot
    silently re-point a control at a different cause.
    """
    shown = sequence[:SEQUENCE]
    if len(set(shown)) != len(shown):
        return 0                      # a value comes round again
    if len(sequence) > SEQUENCE:
        return 1                      # more boundaries than fit, all new
    if len(shown) == 1:
        return 3                      # one value, and nothing after it
    return 2                          # new values, and then it stopped


class Stream:
    """One child stream, drained, with its end carried as a state.

    The pump publishes under the condition's lock, so a waiter holding that
    lock while it looks cannot miss a line published between the look and the
    wait.
    """

    def __init__(self, changed=None):
        self.lines = []
        self.changed = (threading.Condition() if changed is None
                        else changed)
        self.ended = False

    def publish(self, line):
        with self.changed:
            self.lines.append(line)
            self.changed.notify_all()

    def pump(self, pipe):
        for line in pipe:
            self.publish(line.rstrip('\n'))
        with self.changed:
            self.ended = True
            self.changed.notify_all()


class ChildProcess:
    """A started process, with the two streams the waits read drained.

    The contract every wait below consumes: `alive()`, `captured()`, a
    `proc` carrying the exit code, and the streams `await_lines` reads. A
    subclass supplies the argv and the environment and whatever else its
    launch needs; the kill is here, because a kill naming one process
    abandons whatever that process had already spawned.

    The child leads a process group of its own, so `stop` is a
    cancellation rather than an abandonment. A group is also what lets a
    graceful signal reach one child and not the runner: on Windows that
    is the new-process-group flag `CTRL_BREAK_EVENT` needs, and on POSIX
    a new session means a signal sent to the pid is never the runner's.
    """

    def __init__(self, argv, env):
        self.argv = argv
        self.proc = subprocess.Popen(
            argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
            start_new_session=True,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                           if sys.platform.startswith('win') else 0))
        self.out = Stream()
        self.err = Stream()
        for pipe, sink in ((self.proc.stdout, self.out),
                           (self.proc.stderr, self.err)):
            threading.Thread(target=sink.pump, args=(pipe,),
                             daemon=True).start()

    def alive(self):
        return self.proc.poll() is None

    def captured(self):
        """Everything the child printed, for a wait's failure report."""
        return '\n'.join(self.out.lines + self.err.lines)

    def stop(self):
        if self.proc.poll() is None:
            _cancel(self.proc)
        self.proc.wait(timeout=CANCEL_BOUND)
        return self.proc.returncode


def _cancel(proc):
    """End the child's whole tree, through the one module that owns a kill.

    A kill names one process, and the `gh` a watcher had already spawned is
    not it: the orphan keeps running, and keeps appending to the call log a
    measurement is still reading, after the child it belonged to is gone.
    The tree has to go, on both platforms, and `tests/_processtree.py` is
    where that lives.

    **This is deduplication, not a repair.** `ChildProcess` launches with
    `start_new_session=True`, so the child is its own session and group
    leader from `Popen` returning, and the local spelling that passed
    `proc.pid` AS the group id resolved to the same group the owner's
    lookup does - always, and not because of luck. The two spellings
    already agreed; the guard in `tests/test_noderun_deadline.py` is what
    disagreed with them, because a second copy of a kill is a second
    mechanism wearing the same name. What rests on that equivalence, and
    is worth saying where the code relies on it: `start_new_session=True`
    AND the child being unreaped. The second half is load-bearing - a
    reaped pid can be recycled, and a recycled pid is not its own group.

    The `proc.kill()` below is the local copy's own fallback, kept. The
    owner returns a description when a group is already gone and does not
    fall back to a direct kill, so a bare delegation would drop it; here
    it fires on the one state where a direct kill is wanted, the child
    still running, rather than on the owner's wording. It is contained
    for the reason the owner contains its own steps: an uncontained
    cleanup failure is one more thing that can replace the expiry the
    caller is about to report, and this one would replace it with an
    `OSError` from the very call meant to be the last resort.

    **What delegating cost, in time.** `ChildProcess.stop` used to wait
    once, for `CANCEL_BOUND`, and that was the whole bound. The owner
    waits for its own reap, and then `stop` waits again, so the worst
    case is now `2 x CANCEL_BOUND` inside the owner plus `stop`'s own
    `CANCEL_BOUND` - three waits, not one, on a child that will not die.
    The owner returns no description to report, and `stop` returns the
    process's own return code, so nothing reads a reason.
    """
    killed = cleanup_process_tree(proc, CANCEL_BOUND)
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception as error:  # pylint: disable=broad-except
            killed += f'; direct fallback kill raised {error!r}'
    return killed


def await_lines(stream, match, count, what):
    """The first `count` lines of `stream` that match.

    The early exit is a state rather than a duration - a stream at its end
    can never print the line again - and the failure carries everything
    the process did print, which is what says which line arrived instead.
    A process that stays up and stays silent leaves this wait nothing to
    end it, and the hung job naming this wait is the trade the repository
    takes deliberately (the `_await_alive` in
    tests/test_stream_lifecycle.py).
    """
    while True:
        with stream.changed:
            found = [line for line in stream.lines if match(line)]
            if len(found) >= count:
                return found
            if stream.ended:
                raise AssertionError(
                    f'{what}: the stream ended first:\n'
                    + '\n'.join(stream.lines))
            stream.changed.wait()


def await_calls(fake, count, child, what):
    """The call log, once it holds `count` entries.

    The log is a file the watcher children append to, so there is no signal
    to wait on and this polls the record. A child that has exited can make
    no further call, which is the state that ends the wait early, with the
    child's own output in the failure. A child that stays up and never
    calls leaves this wait nothing to end it, and the hung job naming this
    wait is the same trade `await_lines` takes.
    """
    while True:
        calls = fake.calls()
        if len(calls) >= count:
            return calls
        assert child.alive(), f'{what}:\n' + child.captured()
        time.sleep(POLL)


def poll_sequence(calls):
    """The markers a call log published, in order, each run collapsed.

    A call log carries a marker's name for every call inside one poll, so
    `1,1,2,2,3,3` is three boundaries. The collapse is by POSITION and
    never by value: a mapping keyed on the marker turns `1,2,3,4,1,2,3,4`
    into `1,2,3,4` and loses the re-use, which is the one thing a reading
    of this exists to carry - and a refusal that names two causes and
    cannot say which is the reader's problem instead.
    """
    sequence = []
    for call in calls:
        marker = call.get('poll')
        if not sequence or sequence[-1] != marker:
            sequence.append(marker)
    return sequence


def await_polls(fake, polls, child, what, width=POLL_WIDTH):
    """The call log, once it carries `polls` distinct poll markers.

    A watcher names its own poll boundary by publishing an index its `gh`
    children inherit, so the log says how many polls ran rather than leaving
    the count to be inferred from the calls. Polls are counted rather than
    calls because a poll's width is data-dependent - a follow-up query and a
    paginated connection both make it wider - and the measurement is the one
    thing that must not assume it.

    The last marker seen names a poll that may still be in flight, so the
    caller reads the ones before it.

    The bound is on CALLS PER PUBLISHED MARKER. A run whose polls are no
    wider than `width` spends about `width` calls per marker however long
    it runs and however slowly the runner gets there - there is no clock
    in it, so a loaded runner reaches the bound later or not at all, where
    a timeout buys an early failure with a flaky leg. A poll WIDER than
    `width` is refused by name, with the idle bound as the other refusal.
    `width` is a TOLERANCE for what one poll may cost, not a promise that
    no poll can cost more, and the two boundary controls in
    tests/test_watcher_poll_index.py are a run at each side of it.

    The premise has a hole the file should name rather than deny:
    `gh_client.Watcher.poll` re-enters its own body on a rate-limit
    refusal, with no cap on the re-entries, so ONE poll can make
    arbitrarily many calls under one marker. The bound reports that
    correctly - the call count is the evidence - but the reading of it is
    the message's, not this sentence's.

    What it catches is the shape no other arm can: a child that stays up,
    healthy, and keeps logging calls without publishing a new boundary,
    which neither the distinct count nor `child.alive` can end. Which
    defect that is, is `READINGS`: the refusal names the row the rendered
    sequence falls in, and there is a control per row.
    """
    while True:
        calls = fake.calls()
        markers = {call.get('poll') for call in calls}
        if len(markers) >= polls:
            return calls
        assert child.alive(), f'{what}:\n' + child.captured()
        if len(calls) > len(markers) * width:
            # The SEQUENCE, not the set: a set is sorted, and sorting both
            # throws on a seam wired below the first request (a sequence
            # mixing a marker with none) and throws away the order, which
            # is the part every reading below is made of.
            sequence = poll_sequence(calls)
            shown = ', '.join(str(marker) for marker in sequence[:SEQUENCE])
            if len(sequence) > SEQUENCE:
                shown += f', ... {len(sequence) - SEQUENCE} more'
            raise AssertionError(
                f'{what}: the poll markers did not reach {polls} within '
                f'{len(calls)} gh call(s): {len(markers)} distinct, sequence '
                f'{shown}, over the {width} call(s) per marker one poll may '
                f'spend. A run whose polls are no wider than that spends '
                f'about {width} call(s) per marker however long it runs. '
                f'The sequence reads as one of four - a value coming round '
                f'again, a run still going of new values, a run that has '
                f'stopped, or a single value and nothing after it - and '
                f'this one is: {READINGS[_reading(sequence)]}.')
        time.sleep(POLL)


def await_gone(pids, child, what, alive, backstop=BACKSTOP):
    """Wait for those processes to be gone, reporting live state at expiry.

    A wait for a child to DIE is the one wait with no live process to give
    up on: the parent is reaped, the pids are what is being waited for, and
    the only thing that can end the wait is a real survivor - which is the
    defect itself. The bound is therefore a failure report and not a health
    margin: it never sets the passing wall.
    """
    started = time.monotonic()
    while any(alive(pid) for pid in pids):
        if time.monotonic() - started >= backstop:
            survivors = [pid for pid in pids if alive(pid)]
            raise AssertionError(
                f'{what}: pids still alive {survivors}, parent exit '
                f'{child.proc.returncode}:\n{child.captured()}')
        time.sleep(POLL)
