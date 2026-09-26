"""Extracting and executing the speed measurement's shell under GitHub's rules.

The readers lift one job's step out of a workflow; the runner executes it
under `bash -e` with stubbed neighbours on PATH.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _processtree import cleanup_process_tree  # noqa: E402
from _wfgraph import _job_section  # noqa: E402

_CLEANUP_TIMEOUT = 5


def workflow_script(workflow, job, step_name):
    """One named step's run block, dedented for reading."""
    section = '\n'.join(_job_section(workflow, job))
    _, marker, after = section.partition(f'- name: {step_name}\n')
    assert marker, f'the workflow has no {step_name!r} step in {job!r}'
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
  echo "gh: the API request failed" >&2
  exit 1
fi
cat "${STUB_ROWS:?}"
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
    """One workdir with a stubbed `gh` at the front of PATH."""
    bin_dir = Path(workdir) / 'bin'
    bin_dir.mkdir(parents=True, exist_ok=True)
    write_executable(bin_dir / 'gh', GH_STUB)
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
        cleanup = cleanup_process_tree(process, _CLEANUP_TIMEOUT)
        _attach_timeout_output(failure, output_files, cleanup)
        raise
    return subprocess.CompletedProcess(
        command, returncode, _read_output(output_files['stdout']),
        _read_output(output_files['stderr']))


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
