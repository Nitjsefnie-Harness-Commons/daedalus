#!/usr/bin/env python3
"""The routes a launch call itself owns, in both directions, and their rows.

The launch call is not this suite's subject: `tests/_launch_audit.py` owns
it, and the tree-wide wall-clock bound is enforced beside the analyser in
`tests/test_repo_layout.py`. What is here is the one control those rows
cannot express on their own — a launcher built by `functools.partial`,
whose deadline is bound inside the partial and whose call site therefore
reads as no bound at all.

`tests/_bound_site_rows.py` says this shape needs no row of its own,
because the analyser's `unplaced` arm already reports it at the head the
gate keeps. That is why this plant is here and not in the table: without
it, deleting the arm leaves every other control green.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bound_site_rows import BOUND_SITE_ROWS  # noqa: E402
from _launch_audit import bound_sites  # noqa: E402
from _launch_refusal_rows import LAUNCH_REFUSAL_ROWS  # noqa: E402
import _util  # noqa: E402


def test_the_launch_call_routes_the_audit_owns_are_refused_and_pinned(tmp):
    """The routes a launch call owns, in both directions, and their rows.

    A `**{'timeout': 30}` unpacked on a launch is a launch-call site, so the
    test-tree census does not read it — `tests/_launch_audit.py` does, and
    refuses it at every head. A keyword the `subprocess` does not take is
    the route handed to that analyser because a misspelled bound
    (`timout=30`) never runs and is invisible to anything reading the word
    `timeout`. A launcher built by `functools.partial` is the analyser's
    `unplaced` arm.

    One control, one owner: this fails if the analyser stops refusing any of
    them, if it starts refusing a clean launch, or if the row that pins one
    is deleted.
    """
    del tmp
    plants = {
        'unpack-at-a-non-git-launch':
            ("import subprocess\n"
             "def probe():\n"
             "    subprocess.run(['node', 'x.js'], **{'timeout': 30})\n",
             [(3, 'non-git', 'unpack')]),
        'foreign-keyword-on-a-launch':
            ("import subprocess\n"
             "def probe():\n"
             "    subprocess.run(['git', 'status'], check=True, timout=30)\n",
             [(3, 'git', 'keyword')]),
        'a-clean-launch-emits-nothing':
            ("import subprocess\n"
             "def probe():\n"
             "    return subprocess.run(['git', 'status'], check=True,\n"
             "                        cwd='/tmp')\n",
             []),
    }
    for label, (source, expected) in plants.items():
        assert bound_sites(source, label) == expected, label
    partial = ("import functools\n"
               "import subprocess\n"
               "def probe():\n"
               "    _r = functools.partial(subprocess.run, timeout=30)\n"
               "    return _r(['git', 'status'])\n")
    assert bound_sites(partial, 'partial-as-a-launcher') == [
        (4, 'unreadable', 'unplaced')], 'the unplaced arm stopped covering it'
    sunk = {label for label, _, _ in BOUND_SITE_ROWS}
    refused = {label for label, _, _ in LAUNCH_REFUSAL_ROWS}
    for label in ('unpack-at-a-non-git-launch', 'foreign-keyword-on-a-launch',
                  'a-clean-launch-emits-nothing'):
        assert label in sunk, (label, sorted(sunk))
    assert 'foreign-keyword-on-a-launch' in refused, sorted(refused)


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='harnesslaunchbounds_')


if __name__ == '__main__':
    raise SystemExit(main())