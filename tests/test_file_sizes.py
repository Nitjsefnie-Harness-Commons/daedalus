#!/usr/bin/env python3
"""The module-size policy, and the ratchet that keeps its numbers honest.

The policy itself lives in scripts/ci/size_baseline.py, because CI rewrites it
when a file shrinks. What is tested here is that the tree satisfies it, and
that the rewriting only ever moves a number down.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

ROOT = _util.ROOT


def _policy():
    return _util.load(ROOT / 'scripts' / 'ci' / 'size_baseline.py',
                      'size_baseline')


def test_every_tracked_module_satisfies_the_size_policy(tmp):
    """No file grew past its record, and none is over the ceiling unexcused."""
    del tmp
    policy = _policy()
    found = policy.violations(policy.tracked_sizes())
    assert not found['grown'], (
        'these files grew past their recorded size; split them, or record the '
        f"new size in scripts/ci/size_baseline.py: {found['grown']}")
    assert not found['over'], (
        'these files are over the ceiling with no BASELINE entry: '
        f"{found['over']}")


def test_the_baseline_names_only_files_that_still_need_it(tmp):
    """A stale entry is a ceiling nobody is held to.

    Two ways an entry rots: the file is gone, or it has been split back under
    its ceiling and the entry now excuses a file that needs no excuse.
    """
    del tmp
    policy = _policy()
    found = policy.violations(policy.tracked_sizes())
    assert not found['missing'], (
        f"BASELINE names files that are not tracked any more: {found['missing']}")
    assert not found['graduated'], (
        'these files are back under their ceiling — delete their BASELINE '
        f"entries: {found['graduated']}")


def test_the_ceilings_are_below_everything_they_excuse(tmp):
    """A ceiling above a baselined file would make that entry meaningless."""
    del tmp
    policy = _policy()
    for rel, recorded in policy.BASELINE.items():
        assert recorded > policy.ceiling_for(rel), (rel, recorded)


def test_tightening_follows_a_shrunk_file_down(tmp):
    """The bound tracks the file, so the lines cannot come back for free."""
    del tmp
    policy = _policy()
    text = "BASELINE = {\n    'server.py': 2624,\n}\n"
    rewritten = policy.tightened(
        text, {'server.py': 2000}, {'server.py': 2624})
    assert rewritten is not None, 'a 624-line shrink tightened nothing'
    assert "'server.py': 2000," in rewritten, rewritten


def test_tightening_never_raises_a_number(tmp):
    """Growth stays a decision somebody makes and a reviewer sees."""
    del tmp
    policy = _policy()
    text = "BASELINE = {\n    'server.py': 2624,\n}\n"
    for current in (2624, 2625, 9999):
        rewritten = policy.tightened(
            text, {'server.py': current}, {'server.py': 2624})
        assert rewritten is None, (current, rewritten)


def test_tightening_deletes_an_entry_that_graduated(tmp):
    """Back under the ceiling, the entry goes rather than shrinking further."""
    del tmp
    policy = _policy()
    text = "BASELINE = {\n    'server.py': 2624,\n    'mcp_server.py': 939,\n}\n"
    rewritten = policy.tightened(
        text, {'server.py': 400, 'mcp_server.py': 939},
        {'server.py': 2624, 'mcp_server.py': 939})
    assert rewritten is not None, 'a graduated file changed nothing'
    assert 'server.py' not in rewritten.replace('mcp_server.py', ''), rewritten
    assert "'mcp_server.py': 939," in rewritten, rewritten


def test_tightening_leaves_a_file_it_has_no_entry_for(tmp):
    """A file outside the baseline is the ceiling's business, not the ratchet's."""
    del tmp
    policy = _policy()
    text = "BASELINE = {\n    'server.py': 2624,\n}\n"
    rewritten = policy.tightened(
        text, {'server.py': 2624, 'tests/_util.py': 10}, {'server.py': 2624})
    assert rewritten is None, rewritten


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
