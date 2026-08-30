"""Extracting and executing speed.yml's shell under GitHub's own rules.

The readers lift one job's step out of the workflow; the runner executes it
under `bash -e` with stubbed neighbours on PATH.
"""
import os
import re
import signal
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
_CLEANUP_TIMEOUT = 5


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
if [ -n "${STUB_CALLS:-}" ]; then
  printf 'sleep %s\\n' "$*" >> "$STUB_CALLS"
fi
exit 0
"""

# Bash imports this function before resolving the ordinary `sleep` command.
SLEEP_FUNCTION = """() {
if [ -n "${STUB_CALLS:-}" ]; then
  printf 'sleep %s\\n' "$*" >> "$STUB_CALLS"
fi
return 0
}
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


def run_workflow_script(workdir, script, environment, timeout=120):
    """Run one workflow run block with the stubs and no coverage collector.

    GitHub starts a `run:` step's body under `bash -e`, so the harness does
    too: a script that only looks fine without `-e` is not the script that
    runs.
    """
    output_dir = Path(workdir) / '.speedharness'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_files = {
        'stdout': output_dir / 'stdout.log',
        'stderr': output_dir / 'stderr.log',
    }
    command = [_util.workflow_bash(), '-e', '-c', script]
    child_environment = {
        **os.environ,
        'PATH': (f'{workdir}/bin{os.pathsep}'
                 f'{os.environ["PATH"]}'),
        **environment,
    }
    child_environment.setdefault('BASH_FUNC_sleep%%', SLEEP_FUNCTION)
    with (output_files['stdout'].open('wb') as stdout,
          output_files['stderr'].open('wb') as stderr):
        process = subprocess.Popen(
            command, cwd=workdir,
            env=_util.child_coverage('scrub', child_environment),
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            start_new_session=sys.platform != 'win32')
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as failure:
        cleanup = _cleanup_process(process)
        _attach_timeout_output(failure, output_files, cleanup)
        raise
    return subprocess.CompletedProcess(
        command, returncode, _read_output(output_files['stdout']),
        _read_output(output_files['stderr']))


def _kill_process_tree(process):
    """Terminate a timed-out workflow shell and every child it started."""
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False,
                timeout=_CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            return f'taskkill timed out after {_CLEANUP_TIMEOUT}s'
        except OSError as error:
            return f'taskkill failed to run: {error}'
        if result.returncode == 0:
            return 'taskkill completed successfully'
        return f'taskkill failed with exit code {result.returncode}'
    try:
        process_group = os.getpgid(process.pid)
    except ProcessLookupError:
        return 'process group was already gone before cleanup'
    except OSError as error:
        return f'process-group lookup failed: {error}'
    try:
        if process_group == os.getpgrp():
            process.kill()
            return 'direct process kill requested for the current group'
        else:
            os.killpg(process_group, signal.SIGKILL)
            return f'process group {process_group} killed'
    except ProcessLookupError:
        # The group can disappear between getpgid() and the signal.
        return f'process group {process_group} was already gone'
    except OSError as error:
        return f'process-group kill failed: {error}'


def _cleanup_process(process):
    """Keep cleanup failures from masking the original timeout."""
    try:
        cleanup = _kill_process_tree(process)
    except Exception as error:  # pylint: disable=broad-except
        cleanup = f'process-tree kill raised {error!r}'
    try:
        return _reap_process(process, cleanup)
    except Exception as error:  # pylint: disable=broad-except
        return f'{cleanup}; cleanup reap raised {error!r}'


def _reap_process(process, cleanup):
    """Reap a timed-out shell without restoring an unbounded wait."""
    try:
        process.wait(timeout=_CLEANUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        cleanup += '; bounded reap timed out'
    except OSError as error:
        cleanup += f'; bounded reap failed: {error}'
    else:
        return cleanup + '; process reaped'
    try:
        process.kill()
    except ProcessLookupError:
        cleanup += '; fallback process was already gone'
    except OSError as error:
        cleanup += f'; fallback process kill failed: {error}'
    else:
        cleanup += '; fallback process kill requested'
    try:
        process.wait(timeout=_CLEANUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return cleanup + '; process reap still timed out'
    except OSError as error:
        return cleanup + f'; process reap failed: {error}'
    return cleanup + '; process reaped after fallback'


def _attach_timeout_output(failure, output_files, cleanup):
    """Add partial text and its durable files to a timeout failure."""
    failure.stdout = _read_output(output_files['stdout'])
    failure.output = failure.stdout
    failure.stderr = _read_output(output_files['stderr'])
    failure.output_files = {
        name: str(path) for name, path in output_files.items()
    }
    failure.cleanup_diagnostic = cleanup


def _read_output(path):
    """Read a workflow output file without losing diagnostic bytes."""
    return path.read_text(encoding='utf-8', errors='replace')
