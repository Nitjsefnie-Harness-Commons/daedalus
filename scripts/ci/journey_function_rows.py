#!/usr/bin/env python3
"""Which function inside a thread paid the cost a kept count recorded.

Issue 1530 records collapsed rounds: a kept `uvicorn-serve` request thread
counts about a tenth of its neighbouring rounds, and the missing
instructions are counted by no thread at all. The per-thread rows
localise the thread; nothing names the function inside it. These rows
break every accepted slot into its declared functions' SELF instruction
totals, so a collapsed round can be diffed against a neighbouring round
function by function, which is the follow-up both issue 1530's Suggested
Fix and PR 1523 name.

It is INSTRUMENTATION and nothing else. The rows are assembled in the
parent, after valgrind has exited, from the files it left on disk:
nothing here runs inside a counted child, touches the child's argv or its
environment, or adds a thread to a profile. The count in the report is the
same number whether or not the flag was set -- pinned by the suite that
holds both row sets' wiring controls.

The flag is read here because this module owns the name, and a module that
does not own a name does not spell it. Only the exact string `'1'` turns
it on, so a workflow that sets it to `0` turns it OFF rather than on -- the
rule `journey_thread_rows.enabled` follows, for the same reason.

A row is `pid`, `thread`, `cmd` and `functions`, where a function is
`{fn, ir}`: the SELF instruction total of every symbol the file declares,
sorted by self total descending and capped at the top 64. The cap is a
documented BREAKDOWN cap, not a partition claim -- a row says nothing
about the cost of the functions it left out, so a reader diffs costed
functions and never a residual. Ties order by name, so two runs of one
tree read the same row.

The self total is read from the file's cost lines the way callgrind
attributes them: only a `fn=` declaration moves the function a cost line
bills to, and every cost line outside a call arc sums into that
function's self total. A `cfn=` names the callee of the call priced
next; it moves no self context. A `calls=` line prices the call that
follows it: the one cost line after it is the call arc -- the callee's
inclusive cost recorded at the call site -- and it is billed to no
function's self total; the cost lines after the arc are the caller's own
again, no new `fn=` line announcing them. The format compresses a
repeated name to a bare `fn=(id)` / `cfn=(id)` line that re-selects the
function the id was declared for, so the walk carries the id table
beside the totals; an id the file never declared is a shape the parse
cannot attribute, and the file is omitted whole rather than billed to a
neighbour. On the synthetic file the suite drives, whose `summary:` is
written as the sum of the self totals with the arc excluded, the self
totals sum to the file's own `summary:` line; that equality is the
numeric control. Whether a real cost-bearing profile's `summary:`
carries the same exclusion -- and the carried `cfn=`-without-`calls=`
form, the `*`-position spelling, and whether real out-files carry bare
`fn=(id)` lines at all -- is a measurement for the first CI artifact
that holds one, not a claim this file makes.
"""
import os
import re
import sys
from pathlib import Path

# The reader this mirrors sits beside this one and is imported by its own
# name, so a run from the repository root and a run from anywhere else
# both resolve it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import journey_thread_rows  # noqa: E402  pylint: disable=wrong-import-position
import journey_threads  # noqa: E402  pylint: disable=wrong-import-position

# A `calls=` line carries the call count and the call's target position,
# and the cost line after it prices the call itself.
CALLS = re.compile(r'^calls=\d+(?:[ \t]+\d+)*$')
# A cost line is the position columns and then the event count; under
# `events: Ir` the last integer is the instruction count, and a `*`
# position is the format's spelling for a position it does not resolve.
COST = re.compile(r'^[*\d]+(?:[ \t]+[*\d]+)*$')
# The format compresses a repeated name to a bare `fn=(id)` / `cfn=(id)`
# line that re-selects the function the id was declared for.
DECLARATION = re.compile(r'^(fn|cfn)=\((\d+)\)(?:[ \t]+(\S.*?))?[ \t]*$')

BREAKDOWN_CAP = 64


def enabled():
    return os.environ.get('DAEDALUS_JOURNEY_FUNCTION_ROWS') == '1'


def retaining():
    """Whether this run keeps whole profiles around for either row set.

    `measure()` retains a round's files only when SOME row set will read
    them: a run with both switches off does no work for this instrument.
    """
    return enabled() or journey_thread_rows.enabled()


def _self_totals(text):
    """`{fn: ir}` -- the SELF instruction total of every declared function.

    The header fields are `journey_threads`' own, so the two readers of one
    file read the same fields; the walk below is this module's. A bare
    `fn=(id)` re-selects the function the id was declared for, and the
    cost lines after it are that function's self again; a bare `cfn=(id)`
    names the priced arc's callee through the same table and moves no self
    context. An id the file never declared has no function to re-select
    and the costs that follow it belong to a function this file never
    named, so the walk returns None -- a file the parse cannot attribute
    whole is omitted by `read` rather than billed to a neighbour.

    A `cfn=` never moves self context; a `cfn=`-only name is entered at 0
    rather than left out, so the row says the function declared itself and
    paid nothing of its own.
    """
    totals = {}
    ids = {}
    current = None
    call_site = False
    for line in text.splitlines():
        declared = DECLARATION.match(line)
        if declared:
            kind, id_text, name = declared.groups()
            number = int(id_text)
            call_site = False
            if name is None:
                name = ids.get(number)
                if name is None:
                    return None
            else:
                ids[number] = name
            totals.setdefault(name, 0)
            if kind == 'fn':
                current = name
        elif CALLS.match(line):
            call_site = True
        elif COST.match(line):
            if call_site:
                call_site = False
            elif current is not None:
                fields = line.split()
                if fields[-1].isdigit():
                    totals[current] += int(fields[-1])
    return totals


def read(directory, prefix):
    """`[{pid, thread, cmd, functions}]` -- one row per accepted file.

    The acceptance is the count reader's: a file `journey_threads.read`
    refuses or skips contributes no function row and no new failure --
    this module is instrumentation and the refusal belongs to the count
    reader -- so a file with no `summary:` line, one with a summary and
    an incomplete header, or one whose compressed declaration re-selects
    an id the file never declared, is left out whole.
    """
    directory = Path(directory)
    rows = []
    for path in sorted(directory.glob(prefix + '.*')):
        text = path.read_text(encoding='utf-8', errors='replace')
        found = journey_threads.SUMMARY.search(text)
        if not found:
            continue
        thread = journey_threads.THREAD.search(text)
        pid = journey_threads.PID.search(text)
        cmd = journey_threads.CMD.search(text)
        if thread is None or pid is None or cmd is None:
            continue
        functions = _self_totals(text)
        if functions is None:
            continue
        ordered = sorted(functions.items(),
                         key=lambda item: (-item[1], item[0]))
        rows.append({'pid': int(pid.group(1)),
                     'thread': int(thread.group(1)),
                     'cmd': cmd.group(1).strip(),
                     'functions': [{'fn': name, 'ir': ir}
                                   for name, ir in ordered[:BREAKDOWN_CAP]]})
    return rows


def for_counter(bridge, samples):
    """The `function_rows` mapping for one counter's run, or None.

    None is every state the key is ABSENT for: the flag is off; the
    counter hands back a number rather than a profile -- `syscalls` and
    `perf-instructions` both count a process tree whole, and nothing under
    such a number is a function of a thread; or nothing could be read.
    The shape gate is read off the measurement's own kind, not a list of
    counter names -- the list that goes stale the day a counter is added.
    A counter that counts the tree whole is also the one case where the
    shared workdir can still hold the LAST profile counter's stale files,
    so the gate is what keeps another counter's breakdown off this one's
    entry.
    """
    if not enabled() or not isinstance(bridge, dict) or not samples:
        return None
    entries = {}
    for journey, rounds in samples.items():
        rows = [fn_rows for _measured, fn_rows in rounds]
        if not any(rows):
            continue
        entries[journey] = {'rounds': rows}
    return entries or None


def wire(entry, bridge, samples):
    """Attach both row sets a profile-shaped run carries to `entry`.

    `measure()` retains each round as the pair of what its counters need,
    so the thread side gets its measurements and the function side its
    per-round rows out of the one retained list, and each key lands only
    when its own rows exist.
    """
    threads = {name: [measured for measured, _fn in rounds]
               for name, rounds in (samples or {}).items()}
    per_thread = journey_thread_rows.for_counter(bridge, threads)
    if per_thread:
        entry['thread_rows'] = per_thread
    per_function = for_counter(bridge, samples)
    if per_function:
        entry['function_rows'] = per_function
