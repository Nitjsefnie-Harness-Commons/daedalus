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
import subprocess
import sys
import tempfile
from pathlib import Path

# The thread module sits beside this one and is imported by its own
# name, so a run from the repository root and a run from anywhere
# else both resolve it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import journey_residual  # noqa: E402  pylint: disable=wrong-import-position
import journey_threads  # noqa: E402  pylint: disable=wrong-import-position

# The residual arithmetic and the refusal it produces, in the leaf that
# owns them. Bound here under its own name so a suite that reached `_row`
# through this module still reaches the same function.
_row = journey_residual.row

ROOT = Path(__file__).resolve().parents[2]
JOURNEYS = ROOT / 'tests' / '_journeys.py'

# One round, not three. These counts are deterministic — the same tree
# executes the same instructions — so a paired statistic over a discarded
# warm-up round buys nothing here. A run-to-run spread is still reported:
# `--rounds 3` measures it, and the tolerance is derived from that spread
# once, by hand.
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

# The fixed background the other half of that subtraction is measured
# against: the real bridge, spawned as a journey spawns it, doing no
# journey's work. `STARTUP_NAME` removes the HARNESS child's interpreter and
# imports but runs no bridge, so without this every count still carries the
# bridge child's own interpreter start, imports, startup, MCP bootstrap and
# serve loop — on `screenshot` that is 99% of the number, which is how a
# journey can get an order of magnitude more expensive on the path it was
# added to watch and the gate stays green.
#
# It is not a journey: `tests/_journeys.py` keeps it out of its journey set
# and out of the record it prints, so nothing gated walks it.
BRIDGE_NAME = 'bridge-only'

_PARANOID = '/proc/sys/kernel/perf_event_paranoid'
# perf's machine rendering under `-x,`, and the default rendering the probe
# reads. Both are produced, so both are read.
_PERF_COUNT = re.compile(r'^(\d+),[^,]*,instructions')
_PERF_COUNT_TEXT = re.compile(r'^\s*([\d,]+)\s+instructions')
_STRACE_TOTAL = re.compile(r'^\s*\S+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+total\s*$',
                           re.M)

MARKER = '##JOURNEY## '

# What a recorded count depends on that no change to this repository
# controls. A count is only comparable against a measurement taken on the
# same three, so they are recorded beside the counts rather than assumed.
TOOLCHAIN_FIELDS = ('python', 'valgrind_version', 'runner_image')


def journey_names():
    """The journey set, read from the module that defines it.

    Imported on demand and by path, so this script runs from a checkout that
    has installed nothing and imports no part of the bridge.
    """
    sys.path.insert(0, str(JOURNEYS.parent))
    try:
        # The pyright config excludes `tests/`, where this module lives, so
        # pyright cannot resolve a name that `sys.path` above resolves at
        # runtime. The suppression states that property of the config; an
        # unresolvable import is `Any` to pyright either way.
        import _journeys  # pyright: ignore[reportMissingImports]
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
        'strace_path': shutil.which('strace'),
    }
    if found['perf_path']:
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
    if found['strace_path']:
        code, _out, _err = _run([found['strace_path'], '-c', '-f', '-o',
                                 os.devnull, 'true'])
        found['strace_usable'] = code == 0
    else:
        found['strace_usable'] = False
    found['selected'] = _selected(found)
    return found


def _runner_image():
    """The runner image as one readable string, or None off a runner.

    GitHub sets `ImageOS` and `ImageVersion` in every step's environment.
    Off a runner neither is set, and a recorded identity naming one
    developer's machine is an identity no CI run could reproduce.
    """
    named = [part for part in (os.environ.get('ImageOS'),
                               os.environ.get('ImageVersion')) if part]
    return ' '.join(named) or None


def toolchain(found):
    """The identity of the toolchain this run's counts were taken on.

    Callgrind's `Ir` is deterministic only for a fixed binary: the counts
    move when the runner image changes CPython's patch build, or libc, or
    valgrind itself, with no change to this repository. That is why the
    full interpreter line is recorded rather than `3.13` — the patch build
    and the compiler are exactly what move.
    """
    return {'python': found['python'].splitlines()[0],
            'valgrind_version': found['valgrind_version'],
            'runner_image': _runner_image()}


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

def _callgrind(name, root, workdir):
    prefix = f'callgrind.{name}'
    # Every round writes into the same workdir under the same prefix, so the
    # previous round's files are still there and would be summed into this
    # one's total — a count that grows by a round each time it is taken.
    # Clearing first is what makes `--rounds 3` three readings rather than
    # one, two and three times the reading.
    for stale in Path(workdir).glob(prefix + '.*'):
        stale.unlink()
    argv = [shutil.which('valgrind'), '--tool=callgrind',
            '--trace-children=yes', '--separate-threads=yes',
            f'--callgrind-out-file={Path(workdir) / (prefix + ".%p")}'
            ] + child_argv(name, root)
    code, _out, err = _run(argv)
    if code != 0:
        return None, {'returncode': code, 'stderr': err.strip()[-400:]}
    rows, unread = journey_threads.read(Path(workdir), prefix)
    return {'rows': rows, 'unread': unread}, None


def kept_for(measurement, journey):
    """One journey's kept total, read out of one counter's measurement.

    A counter that counts a process tree whole hands back a number, and
    that number is the total. A counter that separates threads hands back
    the profile's rows, and `journey_threads.total_for` is what reads
    them — the same call the journey's own profile goes through, so the
    baseline side and the journey side are under ONE rule rather than two
    that agree today. `journey` is the exclusion list applied, which is
    why the same baseline profile answers differently per journey.

    A classification failure is a sentence rather than the dict a failed
    child carries: there is no returncode to report, and the sentence is
    what a reader has to act on.
    """
    if isinstance(measurement, dict):
        kept, _excluded, why = journey_threads.total_for(
            measurement['rows'], journey, measurement['unread'])
        return kept, why
    return measurement, None


def _perf(name, root, workdir):
    del workdir
    argv = [shutil.which('perf'), 'stat', '-e', 'instructions:u', '-x,',
            '--'] + child_argv(name, root)
    code, _out, err = _run(argv)
    if code != 0:
        return None, {'returncode': code, 'stderr': err.strip()[-400:]}
    counted = _perf_instruction_count(err)
    # A None with no reason would reach `_row` and raise `TypeError` on
    # `None - None`, which is an abort rather than the unavailability this
    # module promises every counter reports as.
    if counted is None:
        return None, {'returncode': code,
                      'stderr': 'perf printed no instruction count'}
    return counted, None


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
    total = _strace_call_total(text)
    if total is None:
        return None, {'returncode': code,
                      'stderr': 'strace wrote no summary to read a total from'}
    return total, None


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
              'toolchain': toolchain(found),
              'selected_counter': _selected(found),
              'excluded_threads': {
                  name: list(journey_threads.excluded_for(name))
                  for name in names},
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
            # The startup child excludes no role — it has no bridge and no
            # background to drop — so it keeps its whole profile.
            startup, why = run(STARTUP_NAME, root, workdir)
            if why is None:
                startup, why = kept_for(startup, STARTUP_NAME)
            bridge = None
            if why is None:
                bridge, why = run(BRIDGE_NAME, root, workdir)
            baselines = {}
            if why is None:
                for name in names:
                    baselines[name], why = kept_for(bridge, name)
                    if why is not None:
                        break
            rows = {}
            if why is None:
                for name in names:
                    counted = []
                    for _round in range(rounds):
                        measured, why = run(name, root, workdir)
                        if why is None:
                            value, why = kept_for(measured, name)
                        if why is not None:
                            break
                        counted.append(value)
                    if why is not None:
                        break
                    rows[name] = counted
            refusals = {}
            if why is None:
                verdicts = {}
                for name in names:
                    # A journey whose own work will not separate from the
                    # background it shares refuses ITS OWN count and no
                    # other: the six journeys beside it measured fine, and
                    # refusing the whole counter over one of them discarded
                    # every count in the run over the single case that is
                    # already reported rather than hidden.
                    verdict, row_why = _row(
                        name, rows[name], startup if childed else 0,
                        baselines[name] if childed else 0)
                    if row_why is not None:
                        # ABSENT from `verdicts`, rather than present and
                        # null: `counts_of` reads every entry's `median`,
                        # so a refused journey left in the mapping is a
                        # reader reaching `.get` through `None`.
                        refusals[name] = row_why
                        continue
                    verdicts[name] = verdict
            if why is not None:
                report['counters'][counter] = {
                    'available': False, 'why': why}
                continue
            report['counters'][counter] = {
                'available': True,
                'gated': counter in GATE_CANDIDATES,
                'startup_only': startup if childed else None,
                # What was taken off, per journey for a counter that
                # separates threads and as one number for one that does
                # not, because the exclusion list the baseline is read
                # through is the journey's own.
                'bridge_only': baselines if childed else None,
                'journeys': verdicts,
                # What was refused, and why — per journey, so a check can
                # tell a journey it cannot measure from one the counter
                # never counted.
                'refused': refusals}
    return report


def counts_of(report, counter):
    """The count a check compares: the median of what one journey cost."""
    entry = report['counters'].get(counter) or {}
    journeys = entry.get('journeys') or {}
    return {name: row.get('median') for name, row in journeys.items()}


def write_summary(lines):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path or not lines:
        return
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
