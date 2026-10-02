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
still partition the process's cost, so the total is unchanged.

A role used to be read from a thread's INSTRUCTION TOTAL against fixed
bands. It is read from what the thread EXECUTED instead, because a total is
a cost proxy for identity and the two populations the bands were asked to
tell apart overlap in cost: `command-round-trip`'s own per-connection
request thread measures 3,548,079 Ir, 2.82x under the serve floor, so the
day that work grows past it the thread reads as the background it excludes
and the journey's own work silently leaves the count (issue 1466). No
threshold fixes that, because the overlap is in the populations.

There is no Python-level identity in a callgrind profile to read either —
zero `.py`, zero `uvicorn`, zero `asyncio` names anywhere, because CPython
runs every thread through the same C entry points. What IS available is
CPython's own C symbol set, and it separates the populations cleanly.
Measured on this box, CPython 3.13.14 under valgrind 3.24.0 with
`--separate-threads=yes`.

The event loop, against a blocking socket worker in the same process: the
two share 89 of their 314 and 463 distinct names, and seven appear on the
loop thread and on no other — `TaskObj_dealloc`, `TaskObj_finalize`,
`TaskStepMethWrapper_dealloc`, `FutureIter_iternext`, `FutureObj_dealloc`,
`FutureObj_finalize`, `PyGen_am_send`.

The front end, against a thread that imported one unrelated stdlib module
in the same process: 305 shared names of 2,124 and 593, and 28 `PyInit_`
initialisers on the front-end thread against 2 on the other. The front
end's own `daedalus_mcp/*.py` files are pure Python and have no
`PyInit_<name>` of their own, but `mcp==2.2.0` pulls pydantic v2 and
`pydantic_core` is a compiled extension whose leaf name is
`_pydantic_core`, so CPython's init macro names it `PyInit__pydantic_core`
— two underscores — and that is what the thread that imported the front end
initialised, and what no other thread in that profile did: neither the main
thread nor the one that imported the unrelated module.

The signature has to name the front end rather than imports in general,
because imports in general is every request thread: see the constant.

A slot's whole cost is still assigned from one role, so a signature says
what the slot spent its life on, not which of the threads that shared it
was which. Nothing rests on that today — a journey's own work runs on the
main thread, which is read by POSITION and first — and
`tests/test_journey_threads.py` asserts the placement rather than trusting
it.

What each journey stops counting is in `EXCLUDED`, and `request` is in no
list: a request thread's role no longer depends on how big it got, so no
journey can exclude one. That is the whole of the fix, and the rest of the
table is per journey and unchanged:

- Every journey's profile carries the bridge's one-off MCP bootstrap
  import — the bridge starts its own front-end listener whatever the
  journey asks of it — and all but one journey excludes it. Which one is
  not written down here: it is the journey that keeps the serve thread
  instead, which is the last bullet, and
  `tests/test_journey_threads.py` pins that shape rather than the name.
- `command-round-trip` and `dashboard-fanout` also exclude the serve
  thread: they exercise the bridge's HTTP surface and none of the front
  end's event loop, so the loop's idle tick is not their work.
- `mcp-exec` calls that loop, so its tick stays in as the named residual.
- `net-capture` is the one that keeps the import. Its own request thread
  measured 2.12 billion instructions and USED to land in the import band,
  beside the bootstrap import, so excluding the import there would have
  dropped the very work the journey exists to measure. Identity
  classification removes that reason, and the list is left as it was: it
  is the one the recorded counts were measured under, and the roles it
  names are now settled by signature, so nothing forces it either way.

Anything this cannot read is a REFUSAL naming the thread and its count,
never a silent inclusion. A mis-sorted profile that quietly sums the
thread the gate exists to exclude is the worst failure this harness has,
and it is invisible in the number it produces.
"""
import re

SUMMARY = re.compile(r'^summary:\s+(\d+)\s*$', re.M)
PID = re.compile(r'^pid:\s+(\d+)\s*$', re.M)
THREAD = re.compile(r'^thread:\s+(\d+)\s*$', re.M)
CMD = re.compile(r'^cmd:\s*(.*)$', re.M)
# `fn=(<id>) <name>` declares a symbol; a bare `fn=(<id>)` repeats one
# declared earlier in the same file and names nothing, so it is not matched.
FN = re.compile(r'^fn=\(\d+\)[ \t]+(\S.*?)[ \t]*$', re.M)
# Callgrind writes a clone of a symbol as the symbol followed by `'N`. A
# clone is the same function, so it is the same signature member.
CLONE = re.compile(r"'\d+$")

# Ir floor for the SHAPE of a profile, and nothing else: a thread that ran
# fewer instructions than this never entered the interpreter, so a profile
# carrying one is not the shape this gate reads. It decides no role, and it
# is not recorded — a run's shape is not a quantity a count was taken
# against.
REQUEST_FROM = 1_000

IMPORT = 'front-end-import'
SERVE = 'uvicorn-serve'
REQUEST = 'request'
MAIN = 'main'
ROLES = (IMPORT, SERVE, REQUEST, MAIN)

# What a CPython asyncio event loop is MADE of: the task and future
# objects it steps, the wrapper it resumes a coroutine through, and the
# generator send that drives one. Measured as exclusive to a loop-running
# thread — see the module docstring.
EVENT_LOOP_SIGNATURE = (
    'FutureIter_iternext',
    'FutureObj_dealloc',
    'FutureObj_finalize',
    'PyGen_am_send',
    'TaskObj_dealloc',
    'TaskObj_finalize',
    'TaskStepMethWrapper_dealloc',
)

# What the FRONT END's import leaves on the thread that ran it.
#
# It has to name the front end, not imports. The front end's own modules are
# pure Python and have no `PyInit_<name>`, but `mcp==2.2.0` pulls pydantic
# v2, whose compiled core is a C extension. Its leaf name is
# `_pydantic_core`, so the init symbol CPython exports is
# `PyInit__pydantic_core` — TWO underscores, which is the whole difference
# between a signature that matches the front end's thread and one that
# matches nothing and leaves `front-end-import` unreachable.
# `tests/test_journey_threads.py` derives the expected spelling from the
# installed `.so` rather than holding a copy of it, because this comment is
# not a control and a copy in the test would have been agreed with.
#
# The narrower of the two directions is the safe one. A signature of the
# import MACHINERY instead — `import_find_and_load` and
# `_PyImport_RunModInitFunc`, which any importing thread carries — claims
# every request thread that imported anything, and the journeys that exclude
# this role then drop the journey's own work with no refusal and no report:
# issue 1466's own shape through the new door. A thread that missed a
# signature is a thread that is COUNTED, which is a number that moved
# rather than a number that lost work.
MODULE_INIT_SIGNATURE = ('PyInit__pydantic_core',)

# The table that puts a thread in a role, by role, so the artefact records
# the one a recorded count was classified under and a run that read a
# profile by different symbols compares nothing. It replaced the `Ir` bands
# this module used to carry, for the reason the docstring gives. MAIN is
# absent: it is read from a thread's POSITION. `request` is absent: it is
# the residual, claimed by no signature.
SIGNATURES = {SERVE: sorted(EVENT_LOOP_SIGNATURE),
              IMPORT: sorted(MODULE_INIT_SIGNATURE)}


def _carries(names, signature):
    """Every member of `signature` among `names`, clones folded onto the
    symbol they are a clone of. All of them, not one: a thread that touched
    a member once is not the thread the signature describes."""
    seen = {CLONE.sub('', name) for name in names}
    return all(member in seen for member in signature)


def read(directory, prefix):
    """`(rows, failure)` — every thread's total and names, from the out
    files.

    `names` is the set of symbols the file's `fn=` lines DECLARE. A line
    carrying an id and no name repeats a name declared earlier and adds
    nothing, and the id is left out rather than turned into a name of its
    own. `cfn=` declares the same table for a callee and is not read: both
    signature families were measured to appear as `fn=` on the threads they
    describe, and a reader debugging a thread no signature claims starts
    there.

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
                     'cmd': cmd.group(1).strip(),
                     'names': frozenset(FN.findall(text))})
    return rows, None


def role_of(thread, names):
    """The role one thread's own executed symbols put it in.

    The main thread is read FIRST and whatever its size and whatever it
    executed, because it is the thread the process started on and a large
    total on it is still the main thread's work. That is the whole of this
    classifier's accuracy: every journey performs its OWN work there, so it
    counts whatever it costs.

    The two background roles are then read from identity, and the order is
    the precedence: a thread carrying BOTH signatures is the event loop. The
    loop's family is exclusive to a thread running a loop, while module
    execution is a property of any thread that imported anything, so the
    loop is the more specific claim and the only one of the two that is
    unambiguous.

    A thread no signature claims is `request`, and that is stated rather than
    left to the ladder's absence: it is a worker doing the journey's own
    work, `request` is excluded by no journey, and it counts. There is no
    size left to fall back to, which is the defect being removed — a role
    must not be bought with a threshold when the thing that decides it is
    what the thread ran.

    `tests/test_journey_threads.py` pins every one of those four steps.
    """
    if thread == 1:
        return MAIN
    if _carries(names, EVENT_LOOP_SIGNATURE):
        return SERVE
    if _carries(names, MODULE_INIT_SIGNATURE):
        return IMPORT
    return REQUEST


def classify(rows, excluded=()):
    """`(roles, failure)` for a whole process tree.

    `roles` maps `(pid, thread)` to a role. The failure is a sentence naming
    what could not be read, or None.

    `excluded` is the journey's exclusion list, and it decides only the
    two-threads-in-one-role refusal below. Two threads of a role the journey
    EXCLUDES is a genuine ambiguity: the gate has to pick which of them is
    the background it is dropping, and nothing in the profile says which.
    Two threads of a role it does NOT exclude is not an ambiguity at all —
    every one of them is counted, and refusing there would refuse a profile
    for holding more work than one thread's worth, which is the normal
    condition of the large journeys.
    """
    roles = {}
    for row in rows:
        # The floor is a shape check and is asked BEFORE the role is read,
        # so a profile this gate cannot have been written for is refused
        # whatever its symbols say rather than classified off them.
        if row['ir'] < REQUEST_FROM:
            return {}, (f'thread {row["thread"]} of pid {row["pid"]} ran '
                        f'{row["ir"]} instructions, which is below the '
                        f'{REQUEST_FROM} a thread that entered the '
                        'interpreter starts at, so this profile is not the '
                        'shape the journey budget reads')
        roles[(row['pid'], row['thread'])] = role_of(
            row['thread'], row['names'])
    for pid in {pid for pid, _thread in roles}:
        for role in (IMPORT, SERVE):
            if role not in excluded:
                continue
            slots = sorted(thread for (owner, thread), name in roles.items()
                           if owner == pid and name == role)
            if len(slots) > 1:
                return {}, (f'pid {pid} has {len(slots)} threads carrying '
                            f'the excluded {role} signature ({slots}), so '
                            'which of them is the one the gate excludes '
                            'cannot be told')
    return roles, None


def excluded_for(journey):
    return EXCLUDED.get(journey, ())


def _expected(roles):
    """The symbols each missing role is read from, named.

    A signature that matched nothing is a misspelled or uninstalled symbol
    far more often than it is a thread that did not run, so the refusal
    says which symbol it was looking for. A role with no signature of its
    own cannot be missing, so every name here has one.
    """
    return ', '.join(f'{role}: {SIGNATURES[role]}' for role in roles)


def total_for(rows, journey, unread=None):
    """`(kept, excluded, failure)` — the sum over the threads that count.

    Two refusals, and neither is a fallback. A profile the reader could not
    read at all arrives as `unread` and is passed straight through, and a
    journey that should have a background thread and does not is a failure:
    the exclusion is part of what the count means, and a profile without
    the thread is a profile this gate has not read. That second refusal is
    MORE load-bearing now that roles come from symbols — a classifier that
    stopped recognising the serve loop no longer mislabels that thread, it
    stops finding it at all, and this is what says so.
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
            f'profile carries the signature for one ({_expected(missing)}), '
            'so the count would be measuring something this journey never '
            'ran')
    kept = 0
    for row in rows:
        if roles[(row['pid'], row['thread'])] not in excluded:
            kept += row['ir']
    return kept, excluded, None


# What each journey stops counting, per journey, and why. The rule every
# entry obeys is the module docstring's: an exclusion list may never cover
# work the journey itself caused. Under identity classification that rule
# is structural rather than measured — a journey's own work is `request` or
# `main`, neither of which any journey excludes — so the table below is
# about the two background roles and the docstring's bullet list says which
# is which. `request` appears in no entry, and
# `tests/test_journey_threads.py` pins that rather than trusting it.
EXCLUDED = {
    'command-round-trip': (IMPORT, SERVE),
    'dashboard-fanout': (IMPORT, SERVE),
    'mcp-exec': (IMPORT,),
    'screenshot': (IMPORT,),
    'segment-relay': (IMPORT,),
    'cdp-result': (IMPORT,),
    'net-capture': (SERVE,),
}
