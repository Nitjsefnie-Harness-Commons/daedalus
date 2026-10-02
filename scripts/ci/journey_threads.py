"""Which of a profiler's threads are background work no journey did.

Callgrind reports no thread NAME, and that is a property of the toolchain
rather than a gap in the arguments: CPython 3.13 puts neither a main thread
nor a `threading.Thread(name=...)` under its name on the OS thread — both
read `python3` from `/proc/<pid>/task/<tid>/comm` — and callgrind's only
thread option is `--separate-threads`, whose files carry a sequence number
and nothing else. So a thread is identified by what it IS, read from the
profile it wrote.

A slot is not a thread either: callgrind reuses one when a thread exits, so
one file can hold several short-lived threads — a probe with four equal
threads and one long one produced five files for six threads. The files
still partition the process's cost, so the total is unchanged; a role is
assigned from a slot's whole cost whatever else shares it, which is why the
request band is wide and the two background bands are not.

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
to tell apart OVERLAP in cost, across runs though not in any one of them:
the front end's import is billions, while `mcp-exec`'s own round trip
measured 1,311,350,558, so a ceiling above the round trip puts the real
import above it too and no pair of bands separates the two (issue 1461).

So the exclusion is not made accurate by a better classifier: the remedy for
a profile is where the work runs, and `role_of` below says which that is.

The same overlap arrives from the BRIDGE side, and there the remedy is the
mirror image. A request thread answering a multi-megabyte result runs to
billions of instructions, which is the import band, where the bridge's own
one-off MCP bootstrap import also sits. So for the large journeys the band
an exclusion would COVER is the band holding the journey's own work — and
covering it does not drop that work quietly: the bridge's constant thread
in the band sits there beside it, so for as long as it does `classify`
finds two threads in an excluded band and refuses, the count comes back
unavailable and the gate fails. That is loud, but it rests on the companion
being there — thread layout, not a property of the journey — so the journeys
below take an exclusion that does not depend on it. That is the bridge-side
face of issue 1461. `EXCLUDED` therefore
records, per journey, the constant each one keeps rather than the work it
would cover, and `classify` refuses a profile only where the ambiguity
actually costs something: two threads in a band the journey EXCLUDES. Two
threads in a band it does not exclude is two threads of counted work.

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

# Ir bands, in instructions. The request band's lower bound is the one that
# can be checked: a thread that ran fewer than this never entered the
# interpreter, so a profile carrying one is not the shape this gate reads.
IMPORT_FROM = 1_000_000_000
SERVE_FROM = 10_000_000
REQUEST_FROM = 1_000

IMPORT = 'front-end-import'
SERVE = 'uvicorn-serve'
REQUEST = 'request'
MAIN = 'main'
ROLES = (IMPORT, SERVE, REQUEST, MAIN)

# The bands as data, because a run that moves one of them changes which
# thread a count excluded, so the artefact records them and a run whose
# bands differ from the recorded ones compares nothing. MAIN is absent: it
# is read from a thread's POSITION, not from a size.
BANDS = {IMPORT: IMPORT_FROM, SERVE: SERVE_FROM, REQUEST: REQUEST_FROM}

# What each journey stops counting, per journey, and why. The rule every
# entry obeys is the module docstring's: an exclusion list may never cover
# work the journey itself caused. NO control enforces it — settling it takes
# a measurement, not a structural check — and the measurement discharging it
# is a per-journey thread table: for each journey, the thread carrying its
# own request work, the role that thread falls in, its Ir against the band's
# threshold, and whether it is kept. Nothing in the tree reproduces that
# table, so the entries below are evidence-backed rather than checked, and
# re-measuring it is what would re-open the question.
#
# The import applies to every journey, not only the ones that call a tool:
# the bridge each spawns starts its own MCP listener whatever the journey
# asks of it, so the bootstrap import thread is real in all of them.
#
# The non-MCP journeys exercise the bridge's HTTP surface and nothing of
# the front end's event loop, so the loop's idle tick is not their work.
# `mcp-exec` calls that loop, so its tick stays in as the named residual.
#
# Three of the four journeys this branch adds exclude the import ALONE, and
# that is what keeps the rule above true for them: no band their own request
# thread can reach is one they exclude, so the work counts in whatever band
# it lands and growth moves the count instead of leaving a thread the gate
# cannot place. It is a narrow rule, not a safe default, and `screenshot`
# shows what the serve band instead would rest on. Its own request thread
# measured 6,603,084 instructions — on a developer box running about 12.7%
# hot against the runner that records the budgets and on a different
# toolchain (valgrind 3.24.0 against 3.22.0, CPython 3.13.14 against
# 3.13.15), so that figure is this machine's and not the runner's. On
# growth it would cross into the band the bridge's constant serve loop
# already occupies, and `classify` would find two threads in an excluded
# band: a REFUSAL, `kept=None` and the gate exiting 1. Loud, but resting on
# the companion happening to be there — thread layout, not a property of the
# journey. The import alone makes the count robust instead and removes the
# dependence outright. So the test is whether a journey's own work can CROSS
# a floor into a band it excludes, never what it measures today: a thread
# already past every floor above it cannot cross one.
#
# `net-capture` is the fourth and is past them all. Its own request thread
# runs to 2.12 billion instructions, which IS the import band, where the
# bridge's one-off MCP bootstrap import also sits: excluding the import there
# would drop the very work the journey exists to measure, so it excludes the
# serve band instead and keeps the import — the same trade `cdp-result`
# makes from the other side. The recorded budget for each says so in its
# journey's docstring, because a multi-billion figure for one journey
# otherwise reads as a bug.
EXCLUDED = {
    'command-round-trip': (IMPORT, SERVE),
    'dashboard-fanout': (IMPORT, SERVE),
    'mcp-exec': (IMPORT,),
    'screenshot': (IMPORT,),
    'segment-relay': (IMPORT,),
    'cdp-result': (IMPORT,),
    'net-capture': (SERVE,),
}


def read(directory, prefix):
    """`(rows, failure)` — every thread's total, from the out files.

    A file carrying a `summary:` but no `pid:` or no `cmd:` is a profile this
    reader has not been written for, and it is NAMED rather than crashed on:
    dereferencing a search that found nothing would end the measurement in an
    `AttributeError` naming no file. A file with no summary at all is not a
    failure — it is the empty one a process that cost nothing writes.
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
    journey performs its OWN work there, so it counts whatever it costs, and
    the one thing a journey must not count — the front end's import — is
    loaded on a worker of its own, where a band can name it. A journey that
    later moves its work onto a worker has it excluded, which is why
    `tests/test_journey_threads.py` asserts the placement rather than
    trusting it.

    For a background thread the total is the only evidence there is — no
    header names the worker — and it decides only whether the thread is
    harness work, so nothing that matters is put there.
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


def classify(rows, excluded=()):
    """`(roles, failure)` for a whole process tree.

    `roles` maps `(pid, thread)` to a role. The failure is a sentence naming
    what could not be read, or None.

    `excluded` is the journey's exclusion list, and it decides only the
    two-threads-in-one-band refusal below. Two threads in a band the
    journey EXCLUDES is a genuine ambiguity: the gate has to pick which of
    them is the background it is dropping, and nothing in the profile says
    which. Two threads in a band it does NOT exclude is not an ambiguity at
    all — every thread in it is counted, and refusing there would refuse a
    profile for holding more work than one thread's worth, which is the
    normal condition of the large journeys.
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
            if role not in excluded:
                continue
            slots = sorted(thread for (owner, thread), name in roles.items()
                           if owner == pid and name == role)
            if len(slots) > 1:
                return {}, (f'pid {pid} has {len(slots)} threads in the '
                            f'excluded {role} band ({slots}), so which of '
                            'them is the one the gate excludes cannot be told')
    return roles, None


def excluded_for(journey):
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
    excluded = excluded_for(journey)
    roles, failure = classify(rows, excluded)
    if failure is not None:
        return None, (), failure
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
