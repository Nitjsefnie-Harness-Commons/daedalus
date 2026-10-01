"""Executing a workflow `run:` block under the rules GitHub runs it by.

`run_workflow_script` is the entry every behavioural pin over a workflow
step goes through, so what it enforces is what those pins actually test.

GitHub starts a step's body under `bash -e`, so the harness does too: a
script that only looks fine without `-e` is not the script that runs. The
timeout carries its own evidence rather than only its exit status, and the
cleanup is a shared tree kill rather than a kill of one process.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _processtree import cleanup_process_tree  # noqa: E402

_CLEANUP_TIMEOUT = 5


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
