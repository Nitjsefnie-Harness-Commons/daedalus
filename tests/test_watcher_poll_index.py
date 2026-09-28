#!/usr/bin/env python3
"""That a watcher names a NEW poll boundary on every poll.

Both watchers publish a per-poll index their `gh` children inherit, and
`tests/_watcher_waits.py` counts DISTINCT markers to decide how many polls a
run made. That count measures a run only while the index advances: a marker
that never changes, or that comes round again, leaves every control measuring
a loop child waiting on a child that stays up, healthy, and publishes
nothing new - a shape no arm of the wait but the call ceiling in
`await_polls` can end.

The invariant and the planted defects live together because they are one
claim, and separately from `test_watcher_budget.py` because that suite asks
what a poll COSTS: what makes its figure a figure is a boundary that moves,
which is a different question with a different failure. The split is also
what the size policy asks of a suite at its ceiling.

Every control here drives the REAL loop against the idle answers, so each
one is a run and not an argument about a run. A control that could be
satisfied by a narrower subject than the one it names would prove nothing
about the watcher, and three of them below fail if their plant did not take.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
import _watcher_once as once_run  # noqa: E402
from _watcher_fixtures import BRANCH  # noqa: E402
from _watcher_fixtures import PR  # noqa: E402
from _watcher_fixtures import TICK  # noqa: E402
from _watcher_fixtures import idle_answers  # noqa: E402
from _watcher_waits import POLL_WIDTH  # noqa: E402
from _watcher_waits import await_polls  # noqa: E402
from _watcher_waits import poll_sequence  # noqa: E402

SKILL = once_run.SKILL

# How many distinct markers the controls read. A measurement reads
# `POLLS + 1`; this reads twice that, and the depth is the re-used index
# that sets it: a watcher publishing 1,2,3,4,1,2,3,4,5,6 reaches four
# distinct markers on its fourth poll, so a control that stops where a
# measurement stops has never seen the re-use it exists to catch. Depth is
# not a proof against a cycle longer than itself and cannot be - the
# sequence is unbounded - so what the depth buys is every cycle a counter
# reset produces inside it, and nothing is claimed beyond that.
BOUNDARIES = 2 * (once_run.POLLS + 1)

# One more of the very same call, immediately after the one already there:
# the plant the wide-poll control repeats to make a poll spend POLL_WIDTH
# calls rather than one.
_IDENTICAL_POLL = """    gh_client.paginate(
        PR_QUERY,
        {'owner': owner, 'name': name, 'number': int(pr),
         'reviewCursor': None, 'talkCursor': None},
        CONNECTIONS)
"""
_PULL_PAGE = '    found = gh_client.at(pages[0], PULL)\n'


def _wide(calls):
    """Splices that make one poll of the copy spend `calls` gh calls."""
    return [(_PULL_PAGE, _IDENTICAL_POLL)] * (calls - 1)


# The loop's own publication and the two lines around it. The publish is
# the ANCHOR for a plant that changes the counter before it is published;
# `try:` is the anchor for a plant that overwrites what was published,
# because the plant then runs after the real publication and before the
# poll's first call, so it is the planted value the `gh` children inherit.
_PUBLISH = '        os.environ[POLL_MARK] = str(poll_index)\n'
_TRY = '        try:\n'
_TICK = '        poll_index += 1\n'

# Five plants, one per shape of broken boundary.
_FROZEN = (_TICK, '        poll_index = 0\n')
_STALLED = (_PUBLISH, '        if poll_index > 2:\n'
                      '            poll_index = 2\n')
_REUSED = (_TRY, "        os.environ[POLL_MARK] = (\n"
                 "            str(poll_index) if poll_index != 5\n"
                 '            else str(poll_index - 4))\n')
_CYCLING = (_TRY, '        os.environ[POLL_MARK] = '
                  'str((poll_index - 1) % 4 + 1)\n')
_WORDED = (_TRY, "        os.environ[POLL_MARK] = 'poll-' + str(poll_index)\n")
_UNWIRED = (_TRY, '        if poll_index != 1:\n'
                  '            os.environ.pop(POLL_MARK, None)\n')


def _indented(block, depth=1):
    """The plant block indented `depth` levels past its own."""
    pad = '    ' * depth
    return ''.join(f'{pad}{line}' if line.strip() else line
                   for line in block.splitlines(keepends=True))


_MARK_LINE = "POLL_MARK = 'DAEDALUS_WATCHER_POLL'\n"
# A HEALTHY run that grows: its first two polls spend `POLL_WIDTH` calls
# and its third spends one more. The counter lives at module scope
# because `poll()` cannot see the loop's `poll_index`, and it is
# incremented inside `poll()` because that is called once per poll.
_GROWING = [
    (_MARK_LINE, '_GROWING_POLLS = 0\n'),
    (_PULL_PAGE, '    global _GROWING_POLLS\n    _GROWING_POLLS += 1\n'
     + '    if _GROWING_POLLS <= 2:\n'
     + _indented(_IDENTICAL_POLL) * (POLL_WIDTH - 1)
     + '    elif _GROWING_POLLS == 3:\n'
     + _indented(_IDENTICAL_POLL) * POLL_WIDTH),
]
# The other half of the pair: an index that sticks AT 3, every poll one
# call, so both halves refuse on the same 25 calls over the same three
# boundaries - which is the whole claim the pair control makes.
_STICKY = (_PUBLISH, '        if poll_index > 3:\n'
                     '            poll_index = 3\n')


def _run_loop(script, fake, boundaries=BOUNDARIES, interval=TICK):
    """Every call a real loop run logged, read off its own log whole.

    `once_run.measure` is deliberately not what reads this, and the reason
    is the point of the suite. Its `seen` drops every call carrying the
    last marker it saw - the in-flight poll - and on a watcher that re-uses
    an index that last marker IS the second occurrence, dropped with the
    rest of its poll, so the projection cannot show the re-use at all. And
    `polls_in` deduplicates by value in first-appearance order, which is
    monotonising: read through it, no publication order the watcher can
    produce looks like anything but a rising run. The loop is therefore
    driven here and its log read whole, which is the only reading that
    still carries the evidence.

    `interval` is the suite's tick for a control about what the watchers
    do, and zero for a control about the ceiling: `Watcher.sleep(0)` is a
    no-op, and a bound in calls is reached as fast as a run can log calls.
    """
    child = once_run.Child(script, [PR, '--interval', str(interval)], fake)
    try:
        await_polls(fake, boundaries, child, f'{boundaries} marker(s)')
    finally:
        child.stop()
    return fake.calls()


def _boundaries(calls):
    """The markers a log published, each run collapsed - the wait's own
    reading, so the control and the refusal it is judging read the same
    evidence through the same mechanism."""
    return poll_sequence(calls)


def _indexes(subject, sequence):
    """The boundaries as integers, or the named verdict for one that is not.

    The two shapes that are not a number are different defects and get
    different verdicts. A boundary the log carries as nothing at all is an
    UNWIRED seam: the watcher did not publish on that poll. A boundary that
    is a name - `poll-1` where a number belongs - is a FORMAT defect, and
    calling it unwired is wrong on both halves, because the seam is wired
    and the index is advancing every poll. Neither is reached by letting
    the conversion raise: a `TypeError` out of here names this conversion
    rather than the watcher that caused it.
    """
    indexes = []
    for marker in sequence:
        assert marker is not None, (
            f'{subject} published no poll marker on one of its polls, so '
            f'the seam is unwired rather than stuck')
        assert isinstance(marker, str) and marker.isdigit(), (
            f'{subject} published the poll marker {marker!r}, a NAME rather '
            f'than an index: the index advances on every poll, so this is a '
            f'format defect and neither a stuck nor an unwired one')
        indexes.append(int(marker))
    return indexes


def _refuses_a_reuse(subject, indexes):
    """The invariant, as the one assertion the real watchers and a
    re-using mutant are both judged by."""
    rising = all(later > earlier
                 for earlier, later in zip(indexes, indexes[1:]))
    assert rising, (
        f'{subject} published the poll index {indexes}, which is not a new '
        f'larger index per poll')


def _widest_poll(calls):
    """The most calls any one published marker carries - one poll's width."""
    widths = {}
    for call in calls:
        marker = call.get('poll')
        widths[marker] = widths.get(marker, 0) + 1
    return max(widths.values())


def _mutant_watcher(directory, *splices):
    """A runnable copy of the tracked comment watcher, plants spliced in."""
    here = Path(directory)
    here.mkdir(parents=True, exist_ok=True)
    script = once_run.planted(here, 'pr_comment_watch.py', *splices)
    return script, _fake_gh.FakeGh(here, idle_answers())


def _verdict(subject, judge, note=''):
    """The named verdict a judgement reached, rather than the fact of it.

    A control that lets its own assertion escape fails for the right reason
    but reads as a failure of the control, so the verdict is caught here
    and every control asserts on the MESSAGE instead. A control satisfied
    by any exception is satisfied by a traceback, and a traceback names
    this harness rather than the defect - hence `AssertionError` alone.
    """
    try:
        judge()
    except AssertionError as exc:
        return str(exc)
    assert False, f'{subject} was measured anyway{note}'


def _refusal(subject, script, fake):
    """The named verdict a planted watcher earns for itself."""
    return _verdict(
        subject, lambda: _run_loop(script, fake, interval=0),
        f': it reached {BOUNDARIES} marker(s) and the wait found what it '
        f'was looking for')


def test_both_watchers_publish_a_new_larger_poll_index_each_poll(tmp):
    """The invariant, read off real polls on both watchers.

    Neither the first value nor the step is pinned: "a new, larger index
    per poll" is the whole claim, and freezing 1 as the start would refuse
    a legitimate re-numbering. What is pinned is the ORDER, because a
    re-used or a reset index is a real defect - a result stamped with a
    poll the reader has already consumed is mis-attributed - and neither
    the distinct-marker count nor the call ceiling can see one.
    """
    for name, args in (('pr_comment_watch.py', [PR]),
                       ('ci_watch.py', [BRANCH])):
        here = Path(tmp) / name
        here.parent.mkdir(parents=True, exist_ok=True)
        fake = _fake_gh.FakeGh(here, idle_answers())
        child = once_run.Child(SKILL / name, [*args, '--interval', str(TICK)],
                               fake)
        try:
            await_polls(fake, BOUNDARIES, child,
                        f'{BOUNDARIES} marker(s)')
        finally:
            child.stop()
        sequence = _boundaries(fake.calls())
        assert len(sequence) >= 2, (name, sequence)
        _refuses_a_reuse(name, _indexes(name, sequence))


def test_a_poll_spending_the_tolerated_width_still_reaches_its_markers(tmp):
    """The false-positive direction, as a run rather than an argument.

    A poll of `POLL_WIDTH` calls is the widest this wait tolerates, so
    this is the boundary from the healthy side: a run at exactly the
    tolerated width must still reach its markers. The bound it used to
    carry was an absolute `polls * width`, which tolerates a healthy poll
    of at most `width` calls and nothing more, so a pull request whose
    comment surface needed a second page was refused by a control meant
    to catch a stuck index. The last assertion is what stops this control
    passing on a narrower subject than it names: the run really did spend
    `POLL_WIDTH` calls a poll.
    """
    script, fake = _mutant_watcher(Path(tmp) / 'wide', *_wide(POLL_WIDTH))
    calls = _run_loop(script, fake)
    sequence = _boundaries(calls)
    assert len(sequence) >= 2, sequence
    _refuses_a_reuse('a watcher with a wide poll',
                     _indexes('a wide poll', sequence))
    assert _widest_poll(calls) == POLL_WIDTH, (
        _widest_poll(calls), POLL_WIDTH)


def test_a_poll_one_wider_than_the_tolerated_width_is_reported(tmp):
    """The same boundary from the other side, and the message it earns.

    One call more in every poll is over the tolerance, so the wait ends -
    and what it has to say is what was measured. What this control pins
    is the OBSERVATION and the pointer, not an exhaustive list of causes:
    a control that required a named cause would refuse a refusal written
    for a cause it had not met, which is how the two-cause sentence it
    was written against came to be false for a re-used index.

    The reading is pinned here too, and it is the ONE-VALUE row - the
    same row the frozen control earns, because a wide first poll and a
    frozen index really are the same payload. That is not a gap in the
    message, it is the one rendering the log cannot separate, and saying
    so is what a reader needs; the idle bound is the pointer out of it.
    """
    over = POLL_WIDTH + 1
    script, fake = _mutant_watcher(Path(tmp) / 'wider', *_wide(over))
    refused = _refusal('a poll wider than the tolerance', script, fake)
    assert (f'did not reach {BOUNDARIES} within {over} gh call(s)'
            in refused), refused
    assert '1 distinct, sequence 1,' in refused, refused
    assert 'sequence 1, 1,' not in refused, refused
    assert f'over the {POLL_WIDTH} call(s) per marker' in refused, refused
    assert ('a single value and nothing after it, which is both an index '
            'stuck where it started and one wide first poll'
            in refused), refused
    assert 'IDLE_POLL_BOUND' in refused, refused
    assert 'did not advance' not in refused, refused


def test_a_cycling_poll_index_is_named_as_a_re_use(tmp):
    """An index that comes round again, and the message that shows it.

    A cycle of period 4 publishes four distinct markers and then repeats
    them, so it never reaches `BOUNDARIES` of them: `_run_loop` waits for
    a count this mutant will not produce and the CALL BOUND is the arm
    that ends the run. So this control is not written to tolerate either
    arm - if the invariant arm ever started to catch it, `_refusal`
    would report the mutant as measured and the control would fail, which
    is the point of a control that names a defect.

    What it does check is the message's own reading, because that is what
    distinguishes a cycle from a stall: a value that comes round again
    cannot be a run that merely stopped.
    """
    script, fake = _mutant_watcher(Path(tmp) / 'cycling', _CYCLING)
    refused = _refusal('a cycling poll index', script, fake)
    assert 'sequence 1, 2, 3, 4, 1,' in refused, refused
    assert ('a value came round again, so a poll published one it had '
            'published before and then another: a re-used index'
            in refused), refused
    assert 'did not advance' not in refused, refused
    logged = sorted({call.get('poll') for call in fake.calls()}, key=repr)
    assert logged == ['1', '2', '3', '4'], (
        f'the mutant published {logged} rather than one cycle of four, so '
        f'this control is not exercising a cycle')


def test_a_frozen_poll_index_is_refused_by_name(tmp):
    """A marker that never changes, planted in a runnable copy.

    A child that stays up, healthy, and publishes nothing new is a shape
    neither arm `await_polls` already had could end, so every budget
    control measuring a loop child would spin until the job's own limit
    ended the run nameless. The last two assertions are what keep the
    control from going vacuous: the log has to show one constant index, or
    the bound has proved nothing about the shape it exists for.
    """
    script, fake = _mutant_watcher(Path(tmp) / 'frozen', _FROZEN)
    refused = _refusal('a frozen poll index', script, fake)
    assert 'did not reach' in refused, refused
    logged = sorted({call.get('poll') for call in fake.calls()}, key=repr)
    assert logged == ['1'], (
        f'the mutant published {logged} rather than one constant index, so '
        f'this control is not exercising a frozen index')
    assert 'sequence 1,' in refused, refused


def test_a_poll_index_frozen_after_advancing_is_refused_by_name(tmp):
    """The same shape reached the other way round: not stuck at 1, but
    stuck at 2 after two real polls have advanced.

    The frozen-from-the-first control alone would not cover it, because
    that one never gets past its first marker, so a bound keyed on the
    marker COUNT would go untested for a run that has already made some
    progress and then stopped. Here the marker count stalls at two while
    the calls keep coming, which is the case a bound read from progress
    rather than from a total has to answer. The rendered sequence is
    `1, 2` and then nothing: the collapse is by position, so a stall
    reads as a sequence that STOPS rather than as one that repeats.

    That stop is the rendering a rule written over what the collapse
    SHOWS gets wrong - new values, and then nothing, reads as a run of
    new ones - so the control asserts the message's READING and not only
    the sequence it is read off.
    """
    script, fake = _mutant_watcher(Path(tmp) / 'stalled', _STALLED)
    refused = _refusal('a poll index frozen at two', script, fake)
    assert 'did not reach' in refused, refused
    logged = sorted({call.get('poll') for call in fake.calls()}, key=repr)
    assert logged == ['1', '2'], (
        f'the mutant published {logged} rather than two markers it then '
        f'froze on, so this control is not exercising a stall')
    assert 'sequence 1, 2, over' in refused, refused
    assert ('no value came round again' in refused
            and 'republished the value already current' in refused), refused
    assert 'some poll cost more than that' in refused, refused


def test_a_growing_run_and_a_sticky_index_earn_the_same_answer(tmp):
    """The non-discrimination proof, driven side by side.

    A control per row proves each row in isolation, and that is exactly
    how a row claiming more than the payload carries got a green suite:
    every control drove a subject that could only be read one way. This
    one drives two plants with OPPOSITE causes - a healthy run whose
    third poll is one call wider than the tolerance, and an index that
    sticks at 2 with every poll costing one call - and requires that both
    earn the same honest answer naming both candidates.

    They must: the collapse folds every poll republishing the current
    value into the one boundary it shows, so the sticky index and the
    growing run are one observation. The assertion is the ORDER of them
    too, because the proof is that the two causes really are the same
    reading and not merely similar ones.
    """
    pair = _refusal_of_pair(tmp)
    for label, refused in pair:
        assert refused is not None, f'the {label} run was measured anyway'
        assert ('no value came round again' in refused
                and 'some poll cost more than that' in refused
                and 'republished the value already current' in refused), (
            label, refused)
        assert 'this log cannot say' in refused, (label, refused)
        assert 'stopped advancing' not in refused, (label, refused)
    heads = [refused.split('over the')[0] for _, refused in pair]
    assert heads[0] == heads[1], heads


def _refusal_of_pair(tmp):
    """`(label, refusal)` for each half of the growing/sticky pair."""
    out = []
    for name, splices in (('growing', _GROWING), ('sticky', [_STICKY])):
        script, fake = _mutant_watcher(Path(tmp) / name, *splices)
        out.append((name, _refusal(f'the {name} run', script, fake)))
    return out


def test_a_reused_poll_index_is_refused_by_name(tmp):
    """A re-used index, and the reading the earlier control could not make.

    The published sequence comes round a second time, so every poll still
    advances and the distinct-marker count still reaches what the wait
    asked for - the wait finishes, and the ceiling has nothing to say. The
    only reading that sees it is the boundary sequence read whole, which
    is what the assertion below judges. This is the shape that reads green
    through `polls_in`, whose dedup-then-first-appearance order is
    monotonising and therefore cannot fail.
    """
    script, fake = _mutant_watcher(Path(tmp) / 'reused', _REUSED)
    sequence = _boundaries(_run_loop(script, fake, interval=0))
    indexes = _indexes('a re-using watcher', sequence)
    assert len(indexes) > 4, (
        f'the mutant only published {indexes}, so it never came round a '
        f'second time and this control is not exercising a re-use')
    refused = _verdict(
        'a watcher re-using its poll index',
        lambda: _refuses_a_reuse('a watcher re-using its poll index',
                                 indexes))
    assert 'is not a new larger index per poll' in refused, refused
    assert str(indexes) in refused, refused


def test_a_partially_wired_poll_seam_is_refused_by_name(tmp):
    """A marker published on one poll only: a verdict, not a `TypeError`.

    A seam wired below the first request leaves the log carrying a mix of
    a marker and no marker, and rendering that mix by sorting it raises
    `TypeError` - so the wait still ends, but by a traceback naming the
    renderer rather than the seam. Both marks have to appear in the
    verdict, because which is which is what tells a reader whether the
    watcher never published or published once.

    The same run has to earn the OTHER unwired verdict as well, from
    `_indexes`, which is where a reader reading a healthy-looking loop
    would meet the defect: the marker is absent, so the seam is unwired -
    which is the one reading of the two that is right here, and the one
    the name-marking control beside it must NOT earn.
    """
    script, fake = _mutant_watcher(Path(tmp) / 'unwired', _UNWIRED)
    refused = _refusal('a partially wired poll seam', script, fake)
    assert 'sequence 1, None,' in refused, refused
    assert 'did not reach' in refused, refused
    unwired = _verdict(
        'a partially wired poll seam',
        lambda: _indexes('a partially wired poll seam',
                         _boundaries(fake.calls())))
    assert 'published no poll marker on one of its polls' in unwired, unwired
    assert 'the seam is unwired rather than stuck' in unwired, unwired


def test_a_non_integer_poll_marker_is_refused_by_name(tmp):
    """A marker that is a name rather than a number, on every poll.

    Every poll publishes, every boundary is new, and the count reaches
    what the wait asked for - so the conversion to an integer is the only
    thing between this watcher and a `ValueError` that names the
    conversion instead of the watcher.

    The verdict is a FORMAT defect and says so. Calling it an unwired
    seam is false on both halves here: the seam is wired, and the index
    advances on every poll, so the last assertion here is the control
    obligation - a name is a different defect from a missing marker, and
    the two controls above and here must not be reading alike.
    """
    script, fake = _mutant_watcher(Path(tmp) / 'worded', _WORDED)
    sequence = _boundaries(_run_loop(script, fake, interval=0))
    assert len(sequence) >= 2, sequence
    assert len(set(sequence)) == len(sequence), (
        f'the mutant published {sequence}, which repeats a boundary, so it '
        f'is exercising a re-use rather than a format defect')
    refused = _verdict(
        'a watcher publishing a name',
        lambda: _indexes('a watcher publishing a name', sequence))
    assert "marker 'poll-1'" in refused, refused
    assert 'a NAME rather than an index' in refused, refused
    assert 'a format defect' in refused, refused
    assert 'the seam is unwired' not in refused, refused


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchpoll_')


if __name__ == '__main__':
    raise SystemExit(main())
