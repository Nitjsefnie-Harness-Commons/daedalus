#!/usr/bin/env python3
"""The thread classifier against REAL callgrind profiles.

Every other control in this tree classifies a profile whose `fn=` lines ARE
the constants under test, so it passes by construction and cannot see a
shape it was written from. These four drive the shipped reader over profiles
a real journey run produced, kept under `tests/fixtures/journey_profiles/`
and described in `tests/_journey_profile_fixture.py`: a front-end symbol
declared `fn=` in one run and `cfn=` on the same thread in the next, the
harness process's own MCP client thread carrying every symbol the front
end's import could be named from, and a profile shape no synthetic fixture
wrote.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journey_contract  # noqa: E402
import _journey_profile_fixture  # noqa: E402
from _journey_contract import (  # noqa: E402
    _util,
)

# The symbol the front end's import is told by, named here so the controls
# can say what they are looking for. `test_journey_threads.py` pins its
# spelling against the installed extension; this file is about the PROFILE,
# and a copy of the constant here is only ever compared with what a real
# callgrind run declared.
FRONT_END = 'PyInit__pydantic_core'

# A journey no exclusion list names, so its count is the whole tree and the
# difference between it and a real journey's count is exactly what the
# journey dropped. Reading the two outputs beats recomputing either.
UNEXCLUDED = 'no-journey-excludes-this'


def _read(journey):
    """`(classifier, rows)` for one preserved run, refusing an unread one."""
    classifier = _journey_contract.threads()
    rows, unread = classifier.read(
        _journey_profile_fixture.profiles_for(journey),
        _journey_profile_fixture.PREFIX)
    assert unread is None, (journey, unread)
    return classifier, rows


def _declared_as(journey):
    """Which of `fn=`/`cfn=` each preserved run declares a symbol on."""
    found = set()
    for path in sorted(_journey_profile_fixture.profiles_for(journey)
                       .iterdir()):
        for line in path.read_text(encoding='utf-8',
                                   errors='replace').splitlines():
            if FRONT_END in line and line.startswith(('fn=', 'cfn=')):
                found.add(line.split('=')[0])
    return found


def test_every_journey_is_counted_on_every_preserved_run(tmp):
    """The gate's whole question, asked of every journey on every real run.

    A journey whose exclusion names a role no thread carries is refused, and
    that refusal is correct — so a classifier that finds nothing refuses
    every journey, and no suite written against the classifier's own
    constants ever notices. On the three preserved profiles the signature
    table matched NOTHING on `command-round-trip` and one thread on the
    other two, so six of seven journeys refused on one run and on another the
    bridge's whole serve loop read as its one-off import.
    """
    for journey in sorted(_journey_profile_fixture.runs()):
        classifier, rows = _read(journey)
        for name in _journey_contract.journeys().NAMES:
            kept, excluded, failure = classifier.total_for(rows, name)
            assert failure is None, (journey, name, failure)
            assert kept is not None and kept > 0, (journey, name, kept)
            assert excluded == classifier.excluded_for(name), \
                (journey, name, excluded)


def test_the_front_ends_import_is_one_thread_of_the_bridge_in_every_run(tmp):
    """One `front-end-import` per run, and never in the journey's process.

    `PyInit__pydantic_core` is declared `fn=` on the bridge's front-end
    thread in `bridge-only` and `cfn=` on that same thread in
    `command-round-trip`, so a reader that collects only `fn=` finds the
    import in one run and misses it in the next. Reading both, the thread is
    found in all three — and it is always a thread of ANOTHER process, which
    is the half of the answer a symbol alone cannot give.
    """
    for journey in sorted(_journey_profile_fixture.runs()):
        classifier, rows = _read(journey)
        found = [row for row in rows
                 if classifier.role_of(row) == classifier.IMPORT]
        assert len(found) == 1, (journey, [row['thread'] for row in found])
        assert FRONT_END in found[0]['names'], (journey, found[0]['names'])
        assert not classifier.is_own_process(found[0]['cmd']), (
            journey, found[0]['cmd'])
        # The cost that thread carries is the front end's whole bootstrap —
        # over three billion instructions on every run, and not the same
        # three billion twice. It is another process's thread, so no
        # journey's own work is behind it, and a total that moves between
        # runs of an unchanged tree is what a count cannot rest on; every
        # journey's list drops it. Dropping the journey's OWN threads
        # instead would be a number nobody could read back.
        assert found[0]['ir'] > 3_000_000_000, (journey, found[0]['ir'])


def test_the_journeys_own_mcp_client_thread_is_its_work_and_is_counted(tmp):
    """The direction that loses a journey's work, pinned on real data.

    `mcp-exec`'s harness process runs its OWN MCP client beside the bridge,
    and that thread declares every symbol the front end's import could be
    named from, so under a signature-only classifier the journey loses its
    own work with no refusal and a plausible number.

    So the control asks what the journey dropped, from two outputs of the
    shipped function rather than from a sum recomputed beside it: a journey
    nothing excludes counts the whole tree, and the difference between that
    and `mcp-exec`'s count is exactly the one front-end thread — which
    leaves the journey's own client inside the count.
    """
    del tmp
    classifier, rows = _read('mcp-exec')
    carried = [row for row in rows
               if classifier.is_own_process(row['cmd'])
               and FRONT_END in row['names']]
    assert carried, 'the fixture no longer carries the journey\'s own client'
    for row in carried:
        assert row['thread'] != 1, row
        assert classifier.role_of(row) == classifier.REQUEST, row
    whole, _excluded, failure = classifier.total_for(rows, UNEXCLUDED)
    assert failure is None, failure
    kept, excluded, failure = classifier.total_for(rows, 'mcp-exec')
    assert failure is None, failure
    assert excluded == (classifier.IMPORT,), excluded
    dropped = [row for row in rows
               if classifier.role_of(row) == classifier.IMPORT]
    assert whole - kept == sum(row['ir'] for row in dropped), \
        (whole, kept, dropped)
    assert all(row['pid'] != carried[0]['pid'] for row in dropped), dropped


def test_the_front_ends_symbol_is_declared_two_ways_on_one_kind_of_thread(
        tmp):
    """The reader's own claim, shown on the two runs it is a claim about.

    The same symbol, on the same thread of the same bridge in both runs, and
    callgrind writes it `fn=` in one and `cfn=` in the other. Which one it
    picks is a cost-attribution detail; the reader takes both, so the
    classification above is a property of the thread rather than of that
    choice.
    """
    for journey in ('bridge-only', 'command-round-trip', 'mcp-exec'):
        assert _declared_as(journey), journey
    assert _declared_as('bridge-only') == {'fn'}, _declared_as('bridge-only')
    assert _declared_as('command-round-trip') == {'cfn'}, \
        _declared_as('command-round-trip')
    assert _declared_as('mcp-exec') == {'fn', 'cfn'}, _declared_as('mcp-exec')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='journeybudget_')


if __name__ == '__main__':
    raise SystemExit(main())
