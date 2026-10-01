#!/usr/bin/env python3
"""How much work a journey costs, and which counters this runner allows.

Separate from `journey_budget.py`, which owns the recorded counts and the
policy over them, because counting is a different responsibility with a
different failure mode: one module that both spawned valgrind and decided
what a recorded number may be would be long enough to need splitting anyway,
and this is the split that falls out of it.

Every counter is attempted independently and reports itself available or
unavailable rather than aborting the run. `facts()` settles which one this
machine allows BY MEASUREMENT — `perf` on PATH is not `perf` permitted to
count — so the choice of counter is never an assumption.

There is deliberately NO CPU-time counter. `time.process_time` was one, and
it is gone because it fails the property the ratchet exists for twice over:
it cannot see the bridge, which is a child process, and process CPU time
still varies with the runner's CPU model, so it is not a quantity load
cannot perturb either. A count that moves when the machine changes is not a
baseline.
"""
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOURNEYS = ROOT / 'tests' / '_journeys.py'

# One round, not three. These counts are deterministic — the same tree
# executes the same instructions — and the paired statistic over a discarded
# warm-up round that the suite-timing path uses buys nothing here, which is
# why `speed` measured a wall clock and this does not. A run-to-run spread is
# still reported: `--rounds 3` measures it, and the tolerance is derived from
# that spread once, by hand.
ROUNDS_DEFAULT = 1

# Preference order, and the order the probe reports in. `instructions:u`
# first: an unqualified `instructions` event needs kernel-side access a
# hosted runner does not grant, so the `:u` modifier is the requirement and
# not a refinement. Callgrind's `Ir` is the fallback when perf is refused.
# `syscalls` is recorded because a second signal is worth having and is not
# worth gating: it is never what `counter` names.
COUNTERS = ('perf-instructions', 'valgrind-callgrind', 'syscalls')

# The counters a recorded count may be denominated in. `syscalls` is absent
# on purpose: it is reported, never gated.
GATE_CANDIDATES = ('perf-instructions', 'valgrind-callgrind')

# The journey the counters' baseline is measured against: the same
# interpreter and the same imports as a journey child, with no bridge and no
# journey. Every count is reported net of this one, so interpreter startup
# and import cost are not part of what is compared.
STARTUP_NAME = 'startup-only'

_PARANOID = '/proc/sys/kernel/perf_event_paranoid'
_CALLGRIND_TOTAL = re.compile(r'^(?:summary|totals):\s+(\d+)\s*$', re.M)
# perf's machine rendering under `-x,`, and the default rendering the probe
# reads. Both are produced, so both are read.
_PERF_COUNT = re.compile(r'^(\d+),[^,]*,instructions')
_PERF_COUNT_TEXT = re.compile(r'^\s*([\d,]+)\s+instructions')
_STRACE_TOTAL = re.compile(r'^\s*\S+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+total\s*$',
                           re.M)

MARKER = '##JOURNEY## '


def journey_names():
    """The journey set, read from the module that defines it.

    Imported on demand and by path, so this script runs from a checkout that
    has installed nothing and imports no part of the bridge.
    """
    sys.path.insert(0, str(JOURNEYS.parent))
    try:
        import _journeys
    finally:
        sys.path.pop(0)
    return tuple(_journeys.NAMES)


# ─── the probe: what this runner actually allows ───────────────────────────

def _run(argv):
    """One child's status and streams, or the failure that stopped it.

    No wall-clock bound, deliberately: a hang surfaces as the enclosing
    suite's or the CI job's bound, which is a better failure than a margin
    measured on a loaded runner. Every child here is one the harness itself
    spawned, so nothing waits on a lock or the network behind it.
    """
    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return None, '', str(error)
    return done.returncode, done.stdout, done.stderr


def _paranoid():
    try:
        with open(_PARANOID, encoding='ascii') as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def _perf_instruction_count(stderr):
    """The instruction count out of perf's own output, or None.

    The count is never inferred from a returncode: perf exits 0 whether or
    not it was permitted to count, which is the whole reason the probe runs
    it rather than reading a setting and believing it.
    """
    for line in stderr.splitlines():
        found = _PERF_COUNT.match(line.strip())
        if found:
            return int(found.group(1))
    for line in stderr.splitlines():
        found = _PERF_COUNT_TEXT.match(line)
        if found:
            return int(found.group(1).replace(',', ''))
    return None


def _strace_call_total(text):
    """`strace -c`'s own total, or None."""
    found = _STRACE_TOTAL.findall(text)
    return int(found[-1]) if found else None


def facts():
    """What this machine offers, measured rather than assumed."""
    found = {
        'python': sys.version,
        'perf_event_paranoid': _paranoid(),
        'perf_path': shutil.which('perf'),
        'valgrind_path': shutil.which('valgrind'),
        'callgrind_control_path': shutil.which('callgrind_control'),
        'strace_path': shutil.which('strace'),
    }
    if found['perf_path']:
        _code, out, err = _run([found['perf_path'], '--version'])
        found['perf_version'] = (out + err).strip() or None
        code, _out, err = _run([found['perf_path'], 'stat', '-e',
                                'instructions:u', '--', 'true'])
        found['perf_stat'] = {
            'event': 'instructions:u',
            'returncode': code,
            'counts': _perf_instruction_count(err) is not None,
            'stderr': err.strip(),
        }
    else:
        found['perf_stat'] = None
    if found['valgrind_path']:
        _code, out, _err = _run([found['valgrind_path'], '--version'])
        found['valgrind_version'] = out.strip() or None
    else:
        found['valgrind_version'] = None
    if found['callgrind_control_path']:
        code, out, err = _run(
            [found['callgrind_control_path'], '--version'])
        found['callgrind_control_version'] = (out + err).strip() or None
        found['callgrind_control_usable'] = code == 0
    else:
        found['callgrind_control_version'] = None
        found['callgrind_control_usable'] = False
    if found['strace_path']:
        code, _out, _err = _run([found['strace_path'], '-c', '-f', '-o',
                                 os.devnull, 'true'])
        found['strace_usable'] = code == 0
    else:
        found['strace_usable'] = False
    found['selected'] = _selected(found)
    return found


def _selected(found):
    """The counter this runner's gate will use: the first usable candidate.

    `None` when the probe found none, and the job still passes in that state
    — the baseline is recorded against a counter, not against a hope.
    """
    for counter in GATE_CANDIDATES:
        if _usable(counter, found):
            return counter
    return None


def _usable(counter, found):
    if counter == 'perf-instructions':
        return bool(found['perf_path']) and bool(
            (found.get('perf_stat') or {}).get('counts'))
    if counter == 'valgrind-callgrind':
        return bool(found['valgrind_path'])
    if counter == 'syscalls':
        return bool(found['strace_path']) and bool(found['strace_usable'])
    return False


# ─── one journey in a child ────────────────────────────────────────────────

def child_argv(name, root):
    return [sys.executable, str(JOURNEYS), '--journey', name,
            '--root', str(root)]


def journey_record(stdout):
    """The record a journey child printed, or None.

    The bridge's own output and whatever a dependency logs share this stream,
    so the reader looks for the marker the journeys module prints behind.
    """
    for line in stdout.splitlines():
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    return None


def shapes(names, root, rounds):
    """One sha per journey per round, from a plain run of each journey.

    The rendering is the journey's whatever counter counted it, so the shape
    is settled once, in the cheapest child there is, and a counter's own run
    does not have to carry it.
    """
    shas = {name: [] for name in names}
    for name in names:
        for _round in range(rounds):
            code, out, err = _run(child_argv(name, root))
            record = journey_record(out) if code == 0 else None
            if record is None:
                return None, (f'the {name} journey printed no record '
                              f'(returncode {code}): {err.strip()[-400:]}')
            shas[name].append(record['sha256'])
    return shas, None


# ─── the counters ──────────────────────────────────────────────────────────

def _callgrind_total(directory, prefix):
    """The `Ir` total over every process callgrind traced.

    `--trace-children=yes` on its own is a trap: with a fixed
    `--callgrind-out-file` every traced process is instrumented but only the
    parent's file is written, so the bridge's work — the work this ratchet
    exists to measure — silently goes missing and the total describes the
    test client instead. The `%p` in the name below is what makes each
    process write its own file; this sums the family.
    """
    total = 0
    seen = False
    for path in sorted(Path(directory).glob(prefix + '.*')):
        found = _CALLGRIND_TOTAL.findall(
            path.read_text(encoding='utf-8', errors='replace'))
        if not found:
            continue
        total += int(found[-1])
        seen = True
    return total if seen else None


def _callgrind(name, root, workdir):
    prefix = str(Path(workdir) / f'callgrind.{name}.%p')
    argv = [shutil.which('valgrind'), '--tool=callgrind',
            '--trace-children=yes', f'--callgrind-out-file={prefix}'
            ] + child_argv(name, root)
    code, _out, err = _run(argv)
    if code != 0:
        return None, {'returncode': code, 'stderr': err.strip()[-400:]}
    return _callgrind_total(workdir, f'callgrind.{name}'), None


def _perf(name, root, workdir):
    del workdir
    argv = [shutil.which('perf'), 'stat', '-e', 'instructions:u', '-x,',
            '--'] + child_argv(name, root)
    code, _out, err = _run(argv)
    if code != 0:
        return None, {'returncode': code, 'stderr': err.strip()[-400:]}
    return _perf_instruction_count(err), None


def _syscalls(name, root, workdir):
    summary = Path(workdir) / f'strace.{name}.txt'
    argv = [shutil.which('strace'), '-c', '-f', '-o', str(summary),
            '--'] + child_argv(name, root)
    code, _out, err = _run(argv)
    if code != 0:
        return None, {'returncode': code, 'stderr': err.strip()[-400:]}
    try:
        text = summary.read_text(encoding='utf-8', errors='replace')
    except OSError as failure:
        return None, {'returncode': code, 'stderr': str(failure)}
    return _strace_call_total(text), None


# `childed` is whether the counter counts a separate process at all. Only a
# counter that does has a startup-only baseline worth subtracting.
COUNTERS_BY_NAME = {
    'perf-instructions': (_perf, True),
    'valgrind-callgrind': (_callgrind, True),
    'syscalls': (_syscalls, True),
}


def measure(root=ROOT, rounds=ROUNDS_DEFAULT, found=None):
    """Measure every journey under every counter it can count here."""
    found = facts() if found is None else found
    names = journey_names()
    shas, failure = shapes(names, root, rounds)
    report = {'rounds': rounds, 'python': sys.version, 'shas': shas or {},
              'selected_counter': _selected(found),
              'shape_failure': failure, 'counters': {}}
    if failure is not None:
        return report
    with tempfile.TemporaryDirectory(prefix='journeybudget_') as workdir:
        for counter in COUNTERS:
            run, childed = COUNTERS_BY_NAME[counter]
            if not _usable(counter, found):
                report['counters'][counter] = {
                    'available': False,
                    'why': 'the probe did not find this counter usable here'}
                continue
            # The startup-only baseline is measured ONCE per counter and
            # reused by every journey: it is the same interpreter and the
            # same imports whatever is measured beside it, so paying for it
            # per journey would buy nothing.
            startup, why = run(STARTUP_NAME, root, workdir)
            rows = {}
            if why is None:
                for name in names:
                    counted = []
                    for _round in range(rounds):
                        value, why = run(name, root, workdir)
                        if why is not None:
                            break
                        counted.append(value)
                    if why is not None:
                        break
                    rows[name] = counted
            if why is not None:
                report['counters'][counter] = {
                    'available': False, 'why': why}
                continue
            report['counters'][counter] = {
                'available': True,
                'gated': counter in GATE_CANDIDATES,
                'startup_only': startup if childed else None,
                'journeys': {name: _row(rows[name], startup if childed
                                         else 0) for name in names}}
    return report


def _row(raw_values, startup):
    """One journey's row: the raw total, the startup-net one, the spread.

    Both numbers are reported because only one of them is the budget: a
    journey's own total carries the interpreter start and the imports the
    startup-only child already accounts for, and a ratchet on that number
    would go red on a dependency bump rather than on a change to the work.
    """
    net = [value - startup for value in raw_values]
    return {'raw': raw_values,
            'net': net,
            'min': min(net) if net else None,
            'max': max(net) if net else None,
            'median': statistics.median(net) if net else None,
            'spread': max(net) - min(net) if net else None}


def counts_of(report, counter):
    """The count a check compares: the median of what one journey cost."""
    entry = report['counters'].get(counter) or {}
    journeys = entry.get('journeys') or {}
    return {name: row.get('median') for name, row in journeys.items()}


def summary_lines(report):
    """The measurement table, as markdown for a step summary."""
    lines = ['### Journey counts', '',
             f"Counter selected here: `{report.get('selected_counter')}`.",
             '',
             '| counter | gated | journey | min | median | max | spread '
             '| sha |', '|---|---|---|---|---|---|---|---|']
    shas = report.get('shas') or {}
    for counter in COUNTERS:
        entry = report['counters'].get(counter) or {}
        if not entry.get('available'):
            lines.append(f"| {counter} | — | — | — | — | — | — | not "
                         f"usable here: {entry.get('why')} |")
            continue
        gated = 'yes' if entry.get('gated') else 'no'
        for name in journey_names():
            row = (entry.get('journeys') or {}).get(name)
            if row is None:
                continue
            seen = sorted(set(shas.get(name) or ()))
            sha = seen[0][:12] if len(seen) == 1 else 'MISMATCH'
            lines.append(
                f"| {counter} | {gated} | {name} | {row['min']} | "
                f"{row['median']} | {row['max']} | {row['spread']} | "
                f"{sha} |")
    return lines


def write_summary(lines):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path or not lines:
        return
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')