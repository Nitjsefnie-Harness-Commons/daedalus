"""Which of a profiler's threads are background work no journey did.

Callgrind reports no thread NAME, and that is a property of the toolchain
rather than a gap in the arguments: CPython 3.13 puts neither a main thread
nor a `threading.Thread(name=...)` under its name on the OS thread — both
read `python3` from `/proc/<pid>/task/<tid>/comm` — and callgrind's only
thread option is `--separate-threads=yes`, whose files carry a sequence number
and nothing else. So a thread is identified by what it IS, read from the
profile it wrote.

A counter measures a process TREE, and the tree is what the profile carries:
the harness child the counter launched, the bridge that child started, and
whatever else the child spawned. Each out-file repeats the `cmd:` of the
process it belongs to, so the ONE identity in a profile that is neither a
number nor a symbol is which PROGRAM a thread ran in.

That is the discriminator, and it is read FIRST because it is the only one
that cannot be wrong in the direction that matters. A journey's own work runs
in the harness process, and no exclusion list may reach a thread of it — the
artefact's validator refuses `main` and `request` by name — so those two roles
are structurally outside the table below rather than kept out of it by a
signature happening to match. Everything in another process is background by
construction: the bridge's own server loop, its MCP front end, its protocol
threads, and anything else the journey itself started.

WHAT A SYMBOL CANNOT DO, measured on the real profiles in
`tests/_journey_profile_fixture.py` (CPython 3.13.14, valgrind 3.24.0,
`--separate-threads=yes --trace-children=yes`, three runs — `bridge-only`,
`command-round-trip` and `mcp-exec`). Every per-thread figure quoted in this
module comes out of one invocation, the same argv
`journey_counters._callgrind` runs:

    valgrind --tool=callgrind --trace-children=yes --separate-threads=yes \
        --callgrind-out-file=<workdir>/callgrind.<journey>.%p \
        python3 tests/_journeys.py --journey <journey> --root <root>

Each out file's `summary:` line is that thread's total,
`read(<workdir>, 'callgrind.<journey>')` returns them all and `role_of` names
each one, so a per-thread number here is reproducible rather than cited:

  - The seven `TaskObj_*`/`FutureObj_*` symbols an earlier reading took as
    exclusive to an event loop appear on NO background thread of any run.
    `FutureObj_finalize` is not an `fn=` declaration anywhere in the three
    profiles at all, so the set could never have fired.

  - The front end's symbol is not exclusive to the bridge. `mcp-exec`'s own
    harness process runs its OWN MCP client, and that thread carries
    `PyInit__pydantic_core`, `PyInit__cffi_backend`, `PyInit__rust`,
    `ossl_init_thread` and `pyo3::pyclass::create_type_object::
    create_type_object` as `fn=` declarations — every symbol the import
    signature could be built from, on the journey's OWN thread, at
    3,820,164,240 instructions. A signature naming the front end's import is
    a signature naming "a thread that imported the MCP stack", and in
    `mcp-exec` that is the journey's own work. Excluding it drops the journey
    with no refusal: issue 1466's own shape, reached through the door built
    to close it.

  - A symbol's `fn=`/`cfn=` split is not a property of the thread.
    `PyInit__pydantic_core` is `fn=` on the bridge's front-end thread in the
    `bridge-only` run and `cfn=` on that same thread in `command-round-trip`.
    Reading `fn=` alone decides the import role by coin flip, and the earlier
    claim that both families appear as `fn=` is what hid it.

So the reader takes BOTH declarations — each says the thread entered the
symbol, and which one callgrind picks is a cost-attribution detail — and the
one signature that survives names the FRONT END rather than the loop. The
front end's own modules are pure Python and have no `PyInit_<name>` of their
own, but `mcp==2.2.0` pulls pydantic v2, whose compiled core is a C
extension whose leaf name is `_pydantic_core`, so CPython's init macro names
it `PyInit__pydantic_core` — two underscores.
`tests/test_journey_threads.py` derives the expected spelling from the
installed `.so` rather than holding a copy of it, because a comment is not a
control and a copy in the test would have been agreed with.

The narrower of the two directions is the safe one. A signature of the import
MACHINERY instead — `import_find_and_load` and `_PyImport_RunModInitFunc`,
which any importing thread carries — claims every request thread that
imported anything. A thread that missed a signature is a thread that is
COUNTED, which is a number that moved rather than a number that lost work.

A slot is not a thread either: callgrind reuses one when a thread exits, so
one file can hold several short-lived threads — a probe with four equal
threads and one long one produced five files for six threads. The files still
partition the process's cost, so the total is unchanged.

A role is read from what the thread EXECUTED, never from its instruction
total: a total is a cost proxy for identity, and the two populations a band
has to tell apart overlap in cost — `command-round-trip`'s own per-connection
request thread measured 3,548,079 Ir, 2.82x under the serve floor, so the day
that work grows past it the thread reads as the background it excludes and
the journey's own work silently leaves the count (issue 1466). No threshold
fixes that, because the overlap is in the populations.

A slot's whole cost is still assigned from one role, so a role says what the
slot spent its life on, not which of the threads that shared it was which.
Nothing rests on that today — a journey's own work runs on the harness
process's thread 1, which is read by POSITION and first — and
`tests/test_journey_placement.py` asserts the placement rather than trusting
it.

What each journey stops counting is in `EXCLUDED`, and `main` and `request`
are in no list: they are the journey's own work by construction, and
`journey_artifact`'s validator refuses either by name, so no document can
state the opposite. The two roles a journey CAN exclude are both background by
construction too, so for the work a journey runs inside its own process the
rule holds structurally rather than by measurement.

It holds for that work and not beyond it. A journey's own work running in a
SUBPROCESS it spawned carries no `_journeys.py` on its command line, so it is
classified background and dropped along with the bridge's — silently, with
`failure=None` and nothing in the report naming it. No journey reaches this
today: they reach the outside world only through `_util.bridge()`. Nor is an
unrecognised process refused, and refusing one is what would fix it: the
`ldconfig` grandchild is present in EVERY real profile, is neither the harness
nor the bridge, and a refusal for it would reject the very profiles that show
the classifier works. Naming it is left to the pull request's Known
Limitations rather than claimed here as a rule that holds.

- Every journey's profile carries the bridge's one-off MCP bootstrap import —
  the bridge starts its own front-end listener whatever the journey asks of
  it — and every journey excludes it. It is a thread of ANOTHER process, so
  no journey's own work is behind it, and its cost is the one background
  number that does not repeat: five rounds of identical code on one runner
  measured it between 4,015,865,696 and 4,020,617,049 instructions, a range
  of 4,751,353 — 0.118% of the smaller figure. The 1.14% a journey keeping
  it moved between runs of an unchanged tree is a SECOND measurement, not
  this one's consequence: it is issue 1495's own six-draw whole-journey
  `max/min - 1`, a journey count over everything the tree did rather than a
  thread's cost over itself. Size bands once made keeping it necessary — a
  thread that journey puts to work measured 2.10 billion instructions and
  landed in the import band beside the bootstrap; the roles are now settled
  by the process a thread ran in, so no importing request thread can claim
  the role at any size.
- `command-round-trip` and `dashboard-fanout` also exclude `serve`: they
  exercise the bridge's HTTP surface and none of the front end's event loop,
  so the loop's idle tick is not their work.
- `mcp-exec` calls that loop, so its tick stays in as the named residual.

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
# `fn=(<id>) <name>` declares a symbol the thread's own cost is written
# against; `cfn=(<id>) <name>` declares the same table for a CALLEE, and a
# bare `fn=(<id>)` or `cfn=(<id>)` repeats a name declared earlier in the
# same file and names nothing, so neither bare form is matched. Both
# declarations are collected and neither alone is the thread's identity: a
# reader that took only `fn=` reads the front end's import in one run and
# misses it in the next, on the same thread of the same tree.
FN = re.compile(r'^(?:fn|cfn)=\(\d+\)[ \t]+(\S.*?)[ \t]*$', re.M)
# Callgrind writes a clone of a symbol as the symbol followed by `'N`. A
# clone is the same function, so it is the same signature member.
CLONE = re.compile(r"'\d+$")

# Ir floor for the SHAPE of a profile, and nothing else: a thread that ran
# fewer instructions than this never entered the interpreter, so a profile
# carrying one is not the shape this gate reads. It decides no role, and it
# is not recorded — a run's shape is not a quantity a count was taken
# against.
REQUEST_FROM = 1_000

# The process the counter launched: a journey child is
# `python3 tests/_journeys.py --journey <name>`, and its out-files carry that
# line verbatim. Every thread in it is the journey's own work.
JOURNEY_PROCESS = '_journeys.py'

IMPORT = 'front-end-import'
# What this names today is every background thread of the bridge that did not
# initialise the front end, and among them three populations the measured
# profiles tell apart: the accept loop, the front end's event-loop tick, and
# the per-connection request threads. Three rounds of every journey on one
# runner (callgrind, `PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1`, head
# `cbdd9458`) measured them at 563,112,124 in every journey child against
# 562,687,477 in the idle `bridge-only` child; 58,596,915 / 58,604,775 /
# 58,602,219 against 23,893,735 / 23,901,336 / 23,907,507; and 2,102,241,356 /
# 2,103,073,257 / 2,102,279,390, absent from the two journeys that open no
# connection. Only the tick is proportional to how long the journey ran,
# which is why a journey whose own work is not the bridge's keeps this role
# out of its list.
# The string is historical: it is what `.github/journey-budget.json` records
# under `excluded_threads` and `thread_bands`, and renaming a recorded role
# would refuse that artefact until it is re-recorded. The role it names is
# read from the PROCESS, so what it covers changed without the name having to.
SERVE = 'uvicorn-serve'
REQUEST = 'request'
MAIN = 'main'
ROLES = (IMPORT, SERVE, REQUEST, MAIN)

# What the FRONT END's import leaves on the thread that ran it — a role read
# inside the background set, so it decides which background thread is the
# one-off bootstrap and which is the bridge serving.
#
# `tests/test_journey_threads.py` derives the expected spelling from the
# installed `.so` rather than holding a copy of it, because this comment is
# not a control and a copy in the test would have been agreed with.
MODULE_INIT_SIGNATURE = ('PyInit__pydantic_core',)

# The table that puts a thread in a role, by role, so the artefact records
# the one a recorded count was classified under and a run that read a profile
# by different symbols compares nothing. MAIN is absent: it is read from a
# thread's POSITION. REQUEST and SERVE are absent: they are what no
# signature claims, one inside the journey's own process and one outside it.
SIGNATURES = {IMPORT: sorted(MODULE_INIT_SIGNATURE)}


def _carries(names, signature):
    """Every member of `signature` among `names`, clones folded onto the
    symbol they are a clone of. All of them, not one: a thread that touched
    a member once is not the thread the signature describes."""
    seen = {CLONE.sub('', name) for name in names}
    return all(member in seen for member in signature)


def is_own_process(cmd):
    """Whether `cmd` is the journey harness the counter launched.

    The harness child is the process a journey runs in; the bridge, its MCP
    front end and anything else the child started are processes it started.
    A journey's own work is in the first and never in the others.
    """
    return JOURNEY_PROCESS in cmd


def read(directory, prefix):
    """`(rows, failure)` — every thread's total and names, from the out
    files.

    `names` is the set of symbols the file DECLARES, whether as `fn=` or as
    `cfn=`. A line carrying an id and no name repeats a name declared
    earlier and adds nothing, and the id is left out rather than turned into
    a name of its own.

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


def role_of(row):
    """The role one thread put itself in, from the process it ran in first.

    The process decides and the symbols only break the background set. A
    thread of the journey's own process is `main` when it is thread 1 and
    `request` when it is not, whatever it executed and however big it got:
    that is the property the whole exclusion rests on, and reading it from
    the process makes it structural instead of a signature that happened to
    miss.

    A thread of another process is background, and the front end's import
    signature tells the one-off bootstrap apart from the bridge serving. A
    background thread carrying BOTH is the bootstrap, which is the more
    specific claim; no journey excludes a role it cannot name.

    A thread no signature claims is `request` inside the journey's own
    process, and `uvicorn-serve` outside it. That is stated rather than left
    to the ladder's absence: no size falls back to it any more, so a role is
    never bought with a threshold — that was the defect issue 1466 removed.

    One role covers three of the bridge's populations and no symbol separates
    them. The per-connection handler path is pure Python on both sides —
    `socketserver.py` and `http/server.py` — and this runner's CPython
    resolves no Python frame into these profiles, so the request thread
    declares no symbol of its own. The cheap candidate was tried and
    REFUTED, not merely found missing: `socket/recv.c` and `socket/send.c`
    appear on the tick thread and on the import thread as well as on the
    request slot, in every journey. Widened to any function present on the
    request slot and absent from the bridge's other three threads it returns
    41 names, and the same search on the tick returns 230 — every one an
    unresolved PLT stub (`0x00000000048cb620` and its neighbours). A
    relocation slot is not a function and differs between runs, so it cannot
    be a signature. `PyInit__pydantic_core` remains the one, and it names the
    import thread only.

    `tests/test_journey_threads.py` pins every one of those steps.
    """
    if is_own_process(row['cmd']):
        return MAIN if row['thread'] == 1 else REQUEST
    if _carries(row['names'], MODULE_INIT_SIGNATURE):
        return IMPORT
    return SERVE


def classify(rows):
    """`(roles, failure)` for a whole process tree.

    `roles` maps `(pid, thread)` to a role. The failure is a sentence naming
    what could not be read, or None.

    The journey's exclusion list decides nothing here: every thread of an
    excluded role is dropped, so two threads of one role is two threads of
    background and not a choice between them. The refusal this used to carry
    was for an ambiguity the gate never had — it dropped every thread of a
    role, not one of them — and on a real profile it fired on every run,
    because the bridge has several threads no signature claims.

    The one refusal left is the shape check, asked BEFORE any role is read,
    so a profile this gate cannot have been written for is refused whatever
    its symbols say rather than classified off them.
    """
    roles = {}
    for row in rows:
        if row['ir'] < REQUEST_FROM:
            return {}, (f'thread {row["thread"]} of pid {row["pid"]} ran '
                        f'{row["ir"]} instructions, which is below the '
                        f'{REQUEST_FROM} a thread that entered the '
                        'interpreter starts at, so this profile is not the '
                        'shape the journey budget reads')
        roles[(row['pid'], row['thread'])] = role_of(row)
    return roles, None


def excluded_for(journey):
    return EXCLUDED.get(journey, ())


def _expected(roles):
    """What each missing role is read from, named.

    A signature that matched nothing is a misspelled or uninstalled symbol
    far more often than it is a thread that did not run, so the refusal
    says which symbol it was looking for.

    Two of the four roles have no symbol and the sentence has to survive
    them: `main` is read from a thread's position and `request` is what no
    signature claims. `uvicorn-serve` is the third, and it is the only one of
    the three that reaches here through a RECORDED document — the validator
    refuses `main` and `request` in an exclusion list, so no list names
    either. The case stays covered by planting the table entry directly,
    which is what a documented-state change would otherwise retire silently.
    Indexing the signature table on a role it does
    not hold would turn a refusal into a `KeyError` — an unhandled state
    where the gate wanted to say a sentence.
    """
    return ', '.join(
        f'{role}: {SIGNATURES[role]}' if role in SIGNATURES
        else f'{role}: read from the thread itself, never from a symbol'
        for role in roles)


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
    roles, failure = classify(rows)
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


# What each journey stops counting, per journey. The rule every entry obeys
# is the module docstring's, and `journey_artifact` enforces the half of it
# about the work a journey runs in its own process: `main` and `request` are
# named by no list here and refused by that validator, which is what makes
# the rule structural rather than measured.
EXCLUDED = {
    'command-round-trip': (IMPORT, SERVE),
    'dashboard-fanout': (IMPORT, SERVE),
    'mcp-exec': (IMPORT,),
    'screenshot': (IMPORT,),
    'segment-relay': (IMPORT,),
    'cdp-result': (IMPORT,),
    # The bridge's per-connection request work here IS the per-byte handling
    # of the capture this journey exists to measure, and dropping `serve`
    # from a list that holds `front-end-import` stops dropping it.
    'net-capture': (IMPORT,),
}
