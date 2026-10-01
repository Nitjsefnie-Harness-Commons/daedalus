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
    """Every thread's total, as `(pid, thread, ir, cmd)` from the out files."""
    rows = []
    for path in sorted(directory.glob(prefix + '.*')):
        text = path.read_text(encoding='utf-8', errors='replace')
        found = SUMMARY.search(text)
        if not found:
            continue
        thread = THREAD.search(text)
        rows.append({'pid': int(PID.search(text).group(1)),
                     'thread': int(thread.group(1)) if thread else 1,
                     'ir': int(found.group(1)),
                     'cmd': CMD.search(text).group(1).strip()})
    return rows


def role_of(ir, thread):
    """The role one thread's own total puts it in, or None if it fits none.

    The main thread is read FIRST and whatever its size, because it is the
    thread the process started on and a large total on it is still the main
    thread's work. That is why the front end's import must not run there:
    a journey that loaded it on its main thread would have the import
    classified as `main` and counted, which is the whole asymmetry the
    journeys avoid by loading it on a thread of their own and waiting.
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


def total_for(rows, journey):
    """`(kept, excluded, failure)` — the sum over the threads that count.

    A journey that should have a background thread and does not is a
    failure: the exclusion is part of what the count means, and a profile
    without the thread is a profile this gate has not read.
    """
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
