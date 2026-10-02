"""Which of a profiler's threads are background work no journey did.

Callgrind reports no thread NAME, and that is a property of the toolchain
rather than a gap in the arguments: CPython 3.13 does not put a
`threading.Thread(name=...)` on the OS thread — both a main thread and a
named one read `python3` from `/proc/<pid>/task/<tid>/comm` — and
callgrind's only thread option is `--separate-threads`, whose output files
carry a sequence number and nothing else. So a thread is identified by what
it IS, read from the profile it wrote.

A slot is not a thread either. Callgrind reuses a slot when a thread exits,
so one file can hold several short-lived threads: a probe with four equal
threads and one long one produced five files for six threads, the reuse
holding two of the equal ones. The SUM does not depend on that — the files
partition the process's cost and the total is unchanged — so nothing here
reads a slot as a thread. A role is assigned from a slot's whole cost
whatever else shares it, which is why the request band is wide and the two
background bands are not.

The bands are what the measurements say. On a journey with the front end
already paid for: a per-connection request thread is tens of thousands of
instructions, a longer request thread about a million, uvicorn's serve
thread tens of millions, the front end's import billions. Each band clears
the next by a factor of ten.

A total can place a BACKGROUND thread and settle nothing else. Measured on
this box: a main thread plus two workers wrote three files headed
`thread: 1`, `thread: 2`, `thread: 3` — per-pid sequence numbers, not the
OS thread ids, for a process whose native tid was 1416293 — so nothing in a
profile says which worker is which. And the two populations a band is asked
to tell apart OVERLAP in cost: the front end's import is billions, while
`mcp-exec`'s own round trip measured 1,311,350,558. A ceiling above the
round trip puts the real import above it too, so no pair of bands
separates them (issue 1461).

So the exclusion is not made accurate by a better classifier. A journey
performs its OWN work on its main thread, where `thread == 1` counts it
whatever it costs, and the one thing a journey must not count — the front
end's import — is loaded on a worker of its own, where a band can name it.
A journey that later moves its work onto a worker has it excluded, which is
why `tests/test_journey_threads.py` asserts the placement rather than
trusting it.

Anything this cannot read is a REFUSAL naming the thread and its count,
never a silent inclusion. A mis-sorted profile that quietly sums the thread
the gate exists to exclude is the worst failure this harness has, and it is
invisible in the number it produces.
"""
import re

SUMMARY = re.compile(r'^summary:\s+(\d+)\s*$', re.M)
PID = re.compile(r'^pid:\s+(\d+)\s*$', re.M)
THREAD = re.compile(r'^thread:\s+(\d+)\s*$', re.M)
CMD = re.compile(r'^cmd:\s*(.*)$', re.M)

# Ir bands, in instructions. The lower bound on the request band is the one
# that can be checked: a thread that ran fewer than this never entered the
# interpreter, so a profile carrying one is not the shape this gate reads.
IMPORT_FROM = 1_000_000_000
SERVE_FROM = 10_000_000
REQUEST_FROM = 1_000

IMPORT = 'front-end-import'
SERVE = 'uvicorn-serve'
REQUEST = 'request'
MAIN = 'main'
ROLES = (IMPORT, SERVE, REQUEST, MAIN)

# The bands as data, because they are part of what a recorded count MEANS and
# not only how this run reads a profile: a run that moves one of them changes
# which thread a count excluded, so the artefact records them and a run
# whose bands differ from the recorded ones compares nothing. MAIN is absent
# because it is read from a thread's POSITION, not from a size.
BANDS = {IMPORT: IMPORT_FROM, SERVE: SERVE_FROM, REQUEST: REQUEST_FROM}

# What each journey stops counting, per journey, and why. The two non-MCP
# journeys exercise the bridge's HTTP surface and nothing of the front end's
# event loop, so the loop's idle tick is not their work. `mcp-exec` calls
# that loop, so its tick stays in as the named residual.
EXCLUDED = {
    'command-round-trip': (IMPORT, SERVE),
    'dashboard-fanout': (IMPORT, SERVE),
    'mcp-exec': (IMPORT,),
}


def read(directory, prefix):
    """`(rows, failure)` — every thread's total, from the out files.

    A file carrying a `summary:` but no `pid:` or no `cmd:` is a profile
    this reader has not been written for, and it is NAMED rather than
    crashed on. Dereferencing a search that found nothing ends the whole
    measurement in an `AttributeError`, which says nothing about which file
    was wrong — the same reason the other three refusals carry the thread
    and its count. A file with no summary at all is not a failure: it is
    the empty one a process that cost nothing writes.
    """
    rows = []
    for path in sorted(directory.glob(prefix + '.*')):
        text = path.read_text(encoding='utf-8', errors='replace')
        found = SUMMARY.search(text)
        if not found:
            continue
        thread = THREAD.search(text)
        pid = PID.search(text)
        cmd = CMD.search(text)
        # All three or none. A `thread:` that reads as absent must not fall
        # back to 1: 1 is MAIN, and MAIN is never excluded, so a defaulted
        # thread is a thread this gate would keep without ever having said so.
        if pid is None or cmd is None or thread is None:
            missing = ('pid' if pid is None else
                       'cmd' if cmd is None else 'thread')
            return rows, (
                f'{path.name} carries a summary but no {missing}: line, so '
                'this profile is not one this gate can read')
        rows.append({'pid': int(pid.group(1)),
                     'thread': int(thread.group(1)),
                     'ir': int(found.group(1)),
                     'cmd': cmd.group(1).strip()})
    return rows, None


def role_of(ir, thread):
    """The role one thread's own total puts it in, or None if it fits none.

    The main thread is read FIRST and whatever its size, because it is the
    thread the process started on and a large total on it is still the main
    thread's work. That is the whole of this classifier's accuracy: every
    journey runs its own work there, so it counts whatever it costs, and the
    one thing a journey must not count is loaded on a worker of its own.

    For a background thread the total is the only evidence there is — no
    header names the worker — and it decides only whether the thread is
    harness work. It cannot establish that a background thread IS a
    journey's own work, so no work that matters is put there.
    """
    if thread == 1:
        return MAIN
    if ir >= IMPORT_FROM:
        return IMPORT
    if ir >= SERVE_FROM:
        return SERVE
    if ir >= REQUEST_FROM:
        return REQUEST
    return None


def classify(rows):
    """`(roles, failure)` for a whole process tree.

    `roles` maps `(pid, thread)` to a role. The failure is a sentence naming
    what could not be read, or None.
    """
    roles = {}
    for row in rows:
        found = role_of(row['ir'], row['thread'])
        if found is None:
            return {}, (f'thread {row["thread"]} of pid {row["pid"]} ran '
                        f'{row["ir"]} instructions, which is below the '
                        f'{REQUEST_FROM} the request band starts at, so this '
                        'profile is not the shape the journey budget reads')
        roles[(row['pid'], row['thread'])] = found
    for pid in {pid for pid, _thread in roles}:
        for role in (IMPORT, SERVE):
            slots = sorted(thread for (owner, thread), name in roles.items()
                           if owner == pid and name == role)
            if len(slots) > 1:
                return {}, (f'pid {pid} has {len(slots)} threads in the '
                            f'{role} band ({slots}), so which of them is the '
                            'one the gate excludes cannot be told')
    return roles, None


def excluded_for(journey):
    """The roles this journey does not count, from the table above."""
    return EXCLUDED.get(journey, ())


def total_for(rows, journey, unread=None):
    """`(kept, excluded, failure)` — the sum over the threads that count.

    Two refusals, and neither is a fallback. A profile the reader could not
    read at all arrives as `unread` and is passed straight through, and a
    journey that should have a background thread and does not is a failure:
    the exclusion is part of what the count means, and a profile without
    the thread is a profile this gate has not read.
    """
    if unread is not None:
        return None, excluded_for(journey), unread
    roles, failure = classify(rows)
    if failure is not None:
        return None, (), failure
    excluded = excluded_for(journey)
    present = set(roles.values())
    missing = [role for role in excluded if role not in present]
    if missing:
        return None, excluded, (
            f'the {journey} journey excludes {missing} and no thread in this '
            'profile is one, so the count would be measuring something this '
            'journey never ran')
    kept = 0
    for row in rows:
        if roles[(row['pid'], row['thread'])] not in excluded:
            kept += row['ir']
    return kept, excluded, None
