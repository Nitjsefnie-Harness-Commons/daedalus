#!/usr/bin/env python3
"""That a watcher names a NEW poll boundary on every poll.

Both watchers publish a per-poll index their `gh` children inherit, and
`tests/_watcher_waits.py` counts DISTINCT markers to decide how many polls a
run made. That count measures a run only while the index advances: a marker
that never changes leaves every control measuring a loop child waiting on a
child that stays up, healthy, and publishes nothing new - a shape no other
arm of the wait can end, and the one the call ceiling in `await_polls` was
added for.

The invariant and the planted defect live together because they are one
claim, and separately from `test_watcher_budget.py` because that suite asks
what a poll COSTS: what makes its figure a figure is a boundary that moves,
which is a different question with a different failure. The split is also
what the size policy asks of a suite at its ceiling.
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

SKILL = once_run.SKILL


def test_both_watchers_publish_a_new_larger_poll_index_each_poll(tmp):
    """The invariant, read off real polls on both watchers.

    `await_polls` counts DISTINCT markers, so a frozen index is caught by
    its call ceiling, but a sequence that is not a rising run of integers -
    a re-used index, a reset - reaches the poll count and is caught by
    nothing. Neither the first value nor the step is pinned here: "a new,
    larger index per poll" is the whole claim, and freezing 1 as the start
    would refuse a legitimate re-numbering.
    """
    for name, args in (('pr_comment_watch.py', [PR]),
                       ('ci_watch.py', [BRANCH])):
        here = Path(tmp) / name
        here.parent.mkdir(parents=True, exist_ok=True)
        fake = _fake_gh.FakeGh(here, idle_answers())
        _, seen = once_run.measure(SKILL / name, args, fake, TICK)
        markers = [marker for marker, _ in once_run.polls_in(seen)]
        assert len(markers) >= 2, (name, markers)
        indexes = [int(marker) for marker in markers]
        rising = all(later > earlier
                     for earlier, later in zip(indexes, indexes[1:]))
        assert rising, (
            f'{name} published the poll index {markers}, which is not a new '
            f'larger index per poll')


# The loop names its own boundary by incrementing a counter and publishing
# it; the plant resets that counter immediately before the increment, so
# the marker it publishes is the same on every poll.
_INDEX_TICK = '        poll_index += 1\n'
_FROZEN_INDEX = '        poll_index = 0\n'


def test_a_frozen_poll_index_is_refused_by_name(tmp):
    """The defect, planted in a runnable copy of the tracked watcher.

    A marker that never changes is a child that stays up, healthy, and
    publishes nothing new: neither arm `await_polls` already had can end
    the wait on it, so every budget control measuring a loop child would
    spin until the job's own limit ended the run nameless. The copy is
    driven with `--interval 0` because this control is about the index and
    not about the cadence - the bound is the call count, not the clock, so
    a run that stops paying the interval reaches the ceiling as fast as it
    can log calls.

    The last assertion is what keeps the control from going vacuous: a
    mutant that still advanced its index would be measured normally and
    never reach this refusal, and the ceiling would have proved nothing
    about the shape it exists for.
    """
    here = Path(tmp) / 'frozen'
    here.mkdir(parents=True, exist_ok=True)
    script = once_run.planted(here, 'pr_comment_watch.py',
                              (_INDEX_TICK, _FROZEN_INDEX))
    fake = _fake_gh.FakeGh(here, idle_answers())
    refused = None
    try:
        once_run.measure(script, [PR], fake, 0)
    except AssertionError as exc:
        refused = str(exc)
    assert refused is not None, (
        'a watcher whose poll index never advanced was measured anyway, so '
        'the call ceiling did not fire on it')
    assert 'did not advance' in refused, refused
    logged = sorted({call.get('poll') for call in fake.calls()})
    assert logged == ['1'], (
        f'the mutant published {logged} rather than one constant index, so '
        f'this control is not exercising a frozen index')
    assert "markers seen ['1']" in refused, refused


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='watchpoll_')


if __name__ == '__main__':
    raise SystemExit(main())
