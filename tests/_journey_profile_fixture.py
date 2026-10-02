"""Where the REAL callgrind profiles the thread classifier is read against
live, and what is and is not true of them.

The files under `tests/fixtures/journey_profiles/<journey>/` are excerpts of
profiles a real `python3 tests/_journeys.py --journey <name>` run produced on
CPython 3.13.14 under valgrind 3.24.0 with `--separate-threads=yes` and
`--trace-children=yes`. Three runs are kept: `bridge-only` (the baseline
child), `command-round-trip` and `mcp-exec`.

They exist because every other control in the tree classifies a profile whose
`fn=` lines ARE the constants under test. Such a control passes by
construction and cannot see a shape it was written from — which is how a
seven-symbol event-loop signature that appears nowhere in a real profile, and
a `fn=`/`cfn=` split that flips run to run, both reached a green branch.

WHAT AN EXCERPT KEEPS, cut mechanically so it can be cut again and compared:

  - the header verbatim, through `summary:` — the four lines the reader takes,
    and the four a synthetic profile always gets right;
  - every `PyInit_*` declaration verbatim, on both `fn=` and `cfn=`. This is
    the part that matters: the front end's init symbol is declared `fn=` on
    the bridge's own thread in one preserved run and `cfn=` on that same
    thread in the next;
  - the first six other declarations per file and the first three CLONES
    (`name'2`), so the reader's subposition cost lines and its clone folding
    are exercised on real text rather than on a line someone composed.

The costs in the headers are the real ones, so a control that sums an excerpt
is summing the real shape: the harness child's main thread in
`command-round-trip` measured 1,125,085,258 instructions and the bridge's
front-end thread measured 3,858,453,424.

ONE THING IS EDITED, and it is a `cmd:` line: the interpreter prefix and the
bridge's absolute checkout path are spelled `python3` and `server.py`. Which
PROGRAM a thread belongs to is what the classifier uses, and that is
preserved exactly; the path a review happened to run out of is not.
"""
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'journey_profiles'

# The prefix the reader globs, which is the one valgrind was told to write.
PREFIX = 'callgrind'


def runs():
    """Every preserved run's directory, keyed by the journey it measured."""
    return {path.name: path for path in sorted(FIXTURES.iterdir())
            if path.is_dir()}


def profiles_for(journey):
    """One preserved run's directory, refusing a journey it does not hold."""
    directory = FIXTURES / journey
    if not directory.is_dir():
        raise AssertionError(
            f'no preserved profile for {journey} in {FIXTURES.name}; the '
            f'runs kept are {sorted(runs())}')
    return directory