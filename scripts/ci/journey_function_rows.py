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
attributes them: a `fn=`/`cfn=` declaration switches the current function,
and a cost line under it sums into that function's self total. A `calls=`
line prices the call that follows it: the one cost line after it is the
callee's inclusive cost recorded at the call site, and it is billed to no
function's self total -- billing it to the caller would charge one call to
both sides, and billing it to the callee would bill an inclusive cost as
self. On a file the parse covers whole, the self totals sum to the file's
own `summary:` line; that equality is the numeric control the suite drives
on a file that carries cost lines.
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
# A cost line is one or more integers; under `events: Ir` the last one is
# the instruction count of the position(s) before it.
COST = re.compile(r'^\d+(?:[ \t]+\d+)*$')

# How many functions one row keeps. A breakdown cap, not a partition: the
# top functions by self total, ordered, and nothing claimed about the rest.
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
    `fn=(id)` repeats a name declared earlier and names nothing, which is
    why the declaration pattern is the reader's own `FN`, which does not
    match the bare form either.
    """
    totals = {}
    current = None
    call_site = False
    for line in text.splitlines():
        declared = journey_threads.FN.match(line)
        if declared:
            current = declared.group(1)
            totals.setdefault(current, 0)
            call_site = False
        elif CALLS.match(line):
            call_site = True
        elif COST.match(line):
            if call_site:
                call_site = False
            elif current is not None:
                totals[current] += int(line.split()[-1])
    return totals


def read(directory, prefix):
    """`[{pid, thread, cmd, functions}]` -- one row per accepted file.

    The acceptance is the count reader's: a file `journey_threads.read`
    refuses or skips contributes no function row and no new failure --
    this module is instrumentation and the refusal belongs to the count
    reader -- so a file with no `summary:` line, or one with a summary and
    an incomplete header, is left out whole.
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