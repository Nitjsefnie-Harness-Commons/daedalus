#!/usr/bin/env python3
"""Every counted thread's own total, kept beside the count it was summed into.

The journey budget records ONE instruction count per journey, and two runs
of unchanged code have produced counts about seven percent apart with
nothing in the tree to explain it. The measurement already reads every
thread's own total out of the callgrind out-files -- `journey_threads.read`
hands them over -- and `total_for` sums them. This module keeps what that
sum was made of, so a later run can say WHICH THREAD carries the difference
instead of only that the sum moved.

It is INSTRUMENTATION and nothing else. The rows are assembled in the
parent, after valgrind has exited, from the files it left on disk: nothing
here runs inside a counted child, touches the child's argv or its
environment, or adds a thread to a profile. The count in the report is the
same number whether or not the flag was set.

The flag is read here because this module owns the name, and a module that
does not own a name does not spell it. Only the exact string `'1'` turns it
on, so a workflow that sets it to `0` turns it OFF rather than on -- the
rule `env_config.debug_timing` follows, for the same reason.

A row is `pid`, `thread`, `role` and `ir`, sorted by `(pid, thread)`, and
carries no `cmd`: that is a whole command line per thread, and the role is
what a reader compares between two runs. `read()`'s `names` is a
`frozenset`, which is not a JSON type at all -- the artefact this lands in
is written by `json.dumps` and read by a browser.

A row is the SAME set of threads the count was summed over, read through
the same classifier and the same per-journey exclusion list, so WHERE A
COUNT WAS TAKEN a row and the number that contains it are two views of one
rule. They are not two views of one rule where the count was REFUSED:
`total_for` also refuses a profile it could not read end to end and a
journey whose excluded role is missing from it, and a journey whose COUNT
was refused keeps its rows here — the refusal is a negative residual, the
profile is on disk, and a refused journey is exactly where a reader comes
for these. Emitting rows the count refused to read is the intent; the two
lists agreeing everywhere is not claimed.
"""
import os
import sys
from pathlib import Path

# The classifier sits beside this one and is imported by its own name, so a
# run from the repository root and a run from anywhere else both resolve it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import journey_threads  # noqa: E402  pylint: disable=wrong-import-position


def enabled():
    """Whether the per-thread rows are recorded, true only for `'1'`."""
    return os.environ.get('DAEDALUS_JOURNEY_THREAD_ROWS') == '1'


def kept_rows(measurement, journey):
    """`(rows, failure)` -- one row per thread this journey KEEPS.

    A classifier refusal comes back as `None` rather than as the threads it
    could name. The refusal is load-bearing for the COUNT -- the sum is not
    taken either -- and these rows are read through the same call, so a
    partial list here is a measurement of part of a journey presented as
    the measurement of it.
    """
    roles, failure = journey_threads.classify(measurement['rows'])
    if failure is not None:
        return None, failure
    excluded = set(journey_threads.excluded_for(journey))
    kept = []
    for row in measurement['rows']:
        role = roles[(row['pid'], row['thread'])]
        if role in excluded:
            continue
        kept.append({'pid': row['pid'], 'thread': row['thread'],
                     'role': role, 'ir': row['ir']})
    # Sorted rather than in the order the out-files were read: two artefacts
    # a reader diffs have to line up thread for thread.
    kept.sort(key=lambda row: (row['pid'], row['thread']))
    return kept, None


def journey_rows(journey, baseline, rounds):
    """`(entry, failure)` -- a baseline row set and one list per round.

    The whole entry goes when any of it cannot be read. A `rounds` list
    shorter than the run is worse than no rows at all: it invites a reader
    to compare the wrong two samples and see a difference in the wrong
    threads.
    """
    base, failure = kept_rows(baseline, journey)
    if failure is not None:
        return None, failure
    sampled = []
    for measurement in rounds:
        rows, failure = kept_rows(measurement, journey)
        if failure is not None:
            return None, failure
        sampled.append(rows)
    return {'baseline': base, 'rounds': sampled}, None


def for_counter(bridge, samples):
    """The `thread_rows` mapping for one counter's whole run, or None.

    None is both states the key is ABSENT for. The flag is off; or the
    counter hands back a number rather than a profile -- `syscalls` and
    `perf-instructions` both count a process tree whole, and there is
    nothing under either number to break into threads. That is read off the
    measurement's own shape rather than declared as a list of counter names,
    which is the list that goes stale the day a counter is added.

    A journey whose rows could not be read is left out rather than given a
    half-filled entry: the count carries the refusal that names what could
    not be read, and these rows exist to be read beside it.
    """
    if not enabled() or not isinstance(bridge, dict):
        return None
    entries = {}
    for journey, rounds in samples.items():
        entry, failure = journey_rows(journey, bridge, rounds)
        if failure is None:
            entries[journey] = entry
    return entries or None
