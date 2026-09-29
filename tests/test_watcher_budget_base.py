#!/usr/bin/env python3
"""What an idle watched pull request cost before this branch.

The numbers in `tests/test_watcher_budget.py` answer what a watcher costs
now and what a refusal does. This file answers the other half of the same
question — what the BASE commit's watchers cost, through the same harness, so
the delta beside it is one method and not two. It is apart because a
before/after comparison is a different concern from the current behaviour,
and because the file it came from sits on the 700-line ceiling.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fake_gh  # noqa: E402
import _util  # noqa: E402
import _watcher_once as once_run  # noqa: E402
from _watcher_fixtures import BRANCH  # noqa: E402
from _watcher_fixtures import IDLE_POLL_BOUND  # noqa: E402
from _watcher_fixtures import PR  # noqa: E402
from _watcher_fixtures import TICK  # noqa: E402
from _watcher_fixtures import base_answers  # noqa: E402
from _watcher_fixtures import idle_answers  # noqa: E402

ROOT = _util.ROOT
SKILL = ROOT / '.claude' / 'skills' / 'changing-daedalus'
BASE = '3cc3605f38f1b0c0d0e47d5252ad17154bad72ec'


_BASE_POLL_READS = '    for kind, path in surfaces(repo, pr):\n'
_BASE_POLL_TWICE = '    for kind, path in surfaces(repo, pr) * 2:\n'
# The base watcher's three comment surfaces; its own state read is outside
# that loop, which is why a doubled pass is three calls and not four.
BASE_SURFACES = 3


def _base_script(directory, name, doubled=False):
    """The base commit's copy of one watcher, or None when unreachable.

    `doubled` reads every comment surface twice per poll: a defect only a
    copy can carry, and the one the base's own cost measure would refuse.
    """
    found = subprocess.run(
        ['git', '-C', str(ROOT), 'show', f'{BASE}:.claude/skills/'
         f'changing-daedalus/{name}'], capture_output=True)
    if found.returncode != 0:
        return None
    raw = found.stdout
    if doubled:
        text = raw.decode('utf-8')
        assert text.count(_BASE_POLL_READS) == 1, name
        raw = text.replace(_BASE_POLL_READS, _BASE_POLL_TWICE).encode('utf-8')
    Path(directory).mkdir(parents=True, exist_ok=True)
    path = Path(directory) / name
    path.write_bytes(raw)
    return path


def test_the_hourly_cost_of_an_idle_watch_is_two_queries(tmp):
    """The branch's watchers, measured through the whole poll surface."""
    after = {}
    for name, args in (('pr_comment_watch.py', [PR]),
                       ('ci_watch.py', [BRANCH])):
        here = Path(tmp) / 'after' / name
        here.parent.mkdir(parents=True, exist_ok=True)
        fake = _fake_gh.FakeGh(here.parent, idle_answers())
        per_poll, seen = once_run.measure(SKILL / name, args, fake, TICK)
        after[name] = per_poll
        assert per_poll <= IDLE_POLL_BOUND, (
            name, per_poll, [call['request'][:80] for call in seen])
    total = sum(after.values())
    print(f'\n  AFTER an idle watched pull request costs {total:.0f} gh '
          f'call(s) per poll ({after}), '
          f'{total * 60:.0f}/hour at the 60s default tick')


def test_the_base_commit_cost_through_the_same_harness(tmp):
    """The same measurement over the base commit's scripts, for the delta.

    The base scripts invoke `gh` by bare name, which only a POSIX PATH can
    resolve to the fake; the figure is therefore reported from the platform
    that can produce it rather than from an invented one.
    """
    if _fake_gh.WINDOWS:
        _util.skip('the base scripts call gh by bare name, which no PATH '
                   'seam can answer on Windows; the AFTER figure and the '
                   'measurement method are platform-independent')
    before = {}
    for name, args in (('pr_comment_watch.py', [PR]),
                       ('ci_watch.py', [BRANCH])):
        here = Path(tmp) / 'before' / name
        script = _base_script(here, name)
        if script is None:
            _util.skip(f'base commit {BASE} is not reachable in this '
                       f'checkout; the BEFORE figure is never invented')
        fake = _fake_gh.FakeGh(here, base_answers())
        before[name] = len(once_run.once(
            script, args + ['--interval', str(TICK)], fake))
    total = sum(before.values())
    assert total >= 6, before
    print(f'\n  BEFORE an idle watched pull request cost {total:.0f} gh '
          f'call(s) per poll ({before}), '
          f'{total * 60:.0f}/hour at the 60s default tick')


def test_the_base_figure_is_read_off_the_base_script(tmp):
    """`total >= 6` discriminates only if the 6 was measured, not remembered.

    A floor a constant satisfies proves nothing, so the base comment watcher
    is measured again with every comment surface read twice per poll. The
    figure has to move with the script, which is what says the BEFORE number
    is a measurement and the AFTER number beside it is one too.
    """
    if _fake_gh.WINDOWS:
        _util.skip('the base scripts call gh by bare name, which no PATH '
                   'seam can answer on Windows; the BEFORE figure and the '
                   'measurement method are platform-independent')
    figures = {}
    for doubled in (False, True):
        here = Path(tmp) / ('twice' if doubled else 'once')
        script = _base_script(here, 'pr_comment_watch.py', doubled=doubled)
        if script is None:
            _util.skip(f'base commit {BASE} is not reachable in this '
                       f'checkout; the BEFORE figure is never invented')
        fake = _fake_gh.FakeGh(here, base_answers())
        figures[doubled] = len(once_run.once(
            script, [PR, '--interval', str(TICK)], fake))
    print(f'\n  the base comment watcher: {figures[False]} call(s) per poll, '
          f'{figures[True]} with every surface read twice')
    assert figures[False], figures
    assert figures[True] == figures[False] + BASE_SURFACES, figures


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='watcherbudgetbase_')


if __name__ == '__main__':
    raise SystemExit(main())
