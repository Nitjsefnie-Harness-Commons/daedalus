"""Extracting and executing speed.yml's shell under GitHub's own rules.

The readers lift one job's step out of the workflow; the runner executes it
under `bash -e` with stubbed neighbours on PATH.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import ROOT  # noqa: E402
from _wfgraph import _job_section  # noqa: E402

# The behavioral tests run the wait's loop at a small bound: every attempt
# spawns the stubs, so a leg costs attempts x per-spawn cost, and per-spawn
# cost varies by two orders of magnitude across platforms — a 45-attempt loop
# is seconds on Linux and minutes on windows-latest. The real bound stays
# pinned to the workflow file by test_speed_gate.py.
WAIT_TRIES_UNDER_TEST = 5


def wait_script(tries=WAIT_TRIES_UNDER_TEST):
    """The wait's script as the behavioral tests run it: bound substituted.

    The workflow file keeps the production bound; the extraction rewrites
    that one assignment so a leg's spawn count does not follow it. `None`
    returns the workflow's own text unrewritten.
    """
    script = speed_script(speed_yml(), 'correctness',
                          'Wait for the correctness aggregate')
    if tries is None:
        return script
    bounds = re.findall(r'(?m)^\s*tries=(\d+)\s*$', script)
    assert len(bounds) == 1, bounds
    return script.replace(f'tries={bounds[0]}', f'tries={tries}', 1)


def speed_yml():
    """The speed workflow's source."""
    return (ROOT / '.github' / 'workflows' / 'speed.yml').read_text(
        encoding='utf-8')


def speed_script(workflow, job, step_name):
    """One named step's run block from speed.yml, dedented for reading."""
    section = '\n'.join(_job_section(workflow, job))
    _, marker, after = section.partition(f'- name: {step_name}\n')
    assert marker, f'speed.yml has no {step_name!r} step in {job!r}'
    _, marker, body = after.partition('        run: |\n')
    assert marker, f'the {step_name!r} step in {job!r} has no run block'
    lines = []
    for line in body.splitlines():
        if line.strip() and not line.startswith('          '):
            break
        lines.append(line[10:])
    return '\n'.join(lines)


def write_executable(path, content):
    """Write an executable test double beside the PATH the tests prefix."""
    path.write_text(content, encoding='utf-8')
    path.chmod(0o755)


# Stands in for `gh api`: records each call flattened to one line, so a
# multi-line `--jq` still counts as one, and prints the rows a real `--jq`
# would have rendered — the awk in the workflow stays the code under test.
GH_STUB = """#!/usr/bin/env bash
printf '%s\\n' "${*//$'\\n'/ }" >> "$STUB_CALLS"
if [ -n "${STUB_FAIL:-}" ]; then
  echo "gh: the Checks API request failed" >&2
  exit 1
fi
cat "${STUB_ROWS:?}"
"""

# The wait's cadence is pinned textually; the behavioral tests run it fast.
SLEEP_STUB = """#!/usr/bin/env bash
exit 0
"""

# Stands in for the timing instrument: it records the invocation and writes
# one duration report, unless the round it was handed is the skipped one.
INSTRUMENT_STUB = """#!/usr/bin/env bash
out=""
tree=""
previous=""
for argument in "$@"; do
  case "$previous" in
    --out) out="$argument" ;;
    --tree) tree="$argument" ;;
  esac
  previous="$argument"
done
printf '%s\\n' "$*" >> "$STUB_INSTRUMENT_CALLS"
case "$out" in
  *"$STUB_SKIP"*) exit 0 ;;
esac
mkdir -p "$out"
printf '{"tree": "%s"}\\n' "$tree" > "$out/durations.json"
"""


def stub_path(workdir):
    """One workdir with stubbed `gh` and `sleep` at the front of PATH."""
    bin_dir = Path(workdir) / 'bin'
    bin_dir.mkdir(parents=True, exist_ok=True)
    write_executable(bin_dir / 'gh', GH_STUB)
    write_executable(bin_dir / 'sleep', SLEEP_STUB)
    return bin_dir


def run_workflow_script(workdir, script, environment):
    """Run one workflow run block with the stubs and no coverage collector.

    GitHub starts a `run:` step's body under `bash -e`, so the harness does
    too: a script that only looks fine without `-e` is not the script that
    runs.
    """
    return subprocess.run(
        [_util.workflow_bash(), '-e', '-c', script], cwd=workdir, timeout=120,
        env=_util.child_coverage('scrub', {
            **os.environ,
            'PATH': (f'{workdir}/bin{os.pathsep}'
                     f'{os.environ["PATH"]}'),
            **environment,
        }), capture_output=True, text=True)
