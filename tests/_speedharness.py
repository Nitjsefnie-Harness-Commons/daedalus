"""Extracting and executing the speed measurement's shell under GitHub's rules.

The readers lift one job's step out of a workflow; the runner executes it
under `bash -e` with stubbed neighbours on PATH. `write_executable` is the
coverage-comment harness's, the one copy both families read.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_comment_workflow import write_executable  # noqa: E402
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


# A workflow `run:` block may set a POSIX mode -- the timings refresh
# opens its push key with `install -d -m 700 ~/.ssh`. That works on the
# ubuntu runner the workflow uses and cannot work on a filesystem with
# no mode to hold: NTFS creates the directory and reports that it
# cannot change its permissions. So a control that REPLAYS such a step
# needs to know whether the filesystem it is standing on can, and the
# question has to be asked of the filesystem rather than of the
# platform name -- a Linux runner on a filesystem that cannot set the
# mode is in exactly the same position as a Windows one, and a Windows
# runner on a filesystem that can is fine.
#
# THE TWO OUTCOMES ARE TOLD APART BY A MARKER, NOT BY A STATUS. `127` is
# the shell's own "not found" convention, so a bare `exit 127` for an
# unresolvable `install` cannot be read back without also claiming that
# an `install` which RESOLVED and then exited 127 never ran -- which is
# a different fact with a different consequence. The unresolved branch
# prints its own marker on stdout and the classification reads that, so
# each outcome names itself.
MODE_PROBE_DIR = '.daedalus-mode-probe'
_UNRESOLVED = '__daedalus_install_unresolved__'
_UNRESOLVED_STATUS = 127
MODE_PROBE = (
    'if command -v install > /dev/null; then\n'
    f'  install -d -m 700 "$HOME/{MODE_PROBE_DIR}"\n'
    'else\n'
    f"  echo '{_UNRESOLVED}'\n"
    f'  exit {_UNRESOLVED_STATUS}\n'
    'fi\n'
)


def unresolved_install_attempt(stdout=''):
    """A `CompletedProcess` shaped like the probe's own not-found branch.

    So a control can pin the classification's two outcomes without a
    filesystem to arrange, and without the marker becoming a second
    spelling of the rule that lives beside it.
    """
    return subprocess.CompletedProcess(
        ['install', '-d', '-m', '700'], _UNRESOLVED_STATUS,
        stdout or f'{_UNRESOLVED}\n', '')


def failed_install_attempt(stderr, status=1):
    """A `CompletedProcess` where `install` RESOLVED and then failed.

    The other half of the same distinction, and the reason the two are
    told apart by the probe's own marker rather than by a status: `127`
    is the shell's "not found" convention, so a command that ran and
    exited 127 is otherwise indistinguishable from one that was never
    found, and reporting the second as the first would grant the skip on
    a fact about the command rather than about the filesystem.
    """
    return subprocess.CompletedProcess(
        ['install', '-d', '-m', '700'], status, '', stderr)


def install_resolves(workdir, environment, timeout=120):
    """`(resolves, detail)`: can THIS PATH run the step's own command.

    Measured with the same `command -v` the probe uses, under the same
    resolved bash and the same environment, so a case that needs an
    unresolvable `install` can check that it got one instead of
    asserting an answer it did not arrange. Where it cannot be
    arranged, this is the fact to report: the case is then unobservable
    here, which is a different statement from "the measurement is
    wrong", and a control can say which.
    """
    done = run_workflow_script(
        workdir, 'command -v install > /dev/null\n', environment, timeout)
    if done.returncode == 0:
        return True, ''
    return False, ('install resolves on this PATH, so a case that needs it '
                   'unresolvable cannot be set up here')


def classify_mode_attempt(done):
    """`(holds, detail)` from what the step's own command did.

    Split out of the measurement so the MISSING-`install` rule is a
    fact about this function rather than about a filesystem: a machine
    that cannot hold a mode answers the filesystem question for every
    case, and a rule that can only be observed on a filesystem with an
    answer is a rule that goes unchecked on exactly the machines whose
    behaviour it is about.

    A MISSING `install` is deliberately not a filesystem fact and does
    not report `holds=False`: a machine that cannot run the step at all
    should have its control say so in one line, not skip quietly over a
    problem it could have reported.
    """
    if _UNRESOLVED in (done.stdout or ''):
        return True, ('install cannot be resolved on this PATH, so the '
                      'mode was never attempted')
    if done.returncode == 0:
        return True, ''
    detail = (done.stderr.strip() or done.stdout.strip()
              or f'install exited {done.returncode}')
    return False, detail


def filesystem_holds_a_mode(workdir, environment, timeout=120):
    """`(holds, detail)` for a filesystem's ability to hold a POSIX mode.

    Asked by running the step's OWN command against a scratch directory
    under the same HOME, through the same resolved bash and the same
    environment the step would run under -- so the answer is about this
    filesystem and this machine, not about a name.

    The scratch directory is removed on every path, including the one
    where it was created: a measurement that leaves `~/.ssh` next to the
    home every other control uses is a measurement that changes the
    environment the next one asks about. A caller that needs to read the
    mode off the directory runs `MODE_PROBE` itself, which does not
    remove it.
    """
    done = run_workflow_script(
        workdir, f'{MODE_PROBE}rm -rf "$HOME/{MODE_PROBE_DIR}"\n',
        environment, timeout)
    return classify_mode_attempt(done)


def skip_unless_a_mode_can_be_set(workdir, environment, measure=None):
    """Skip a step's replay on a filesystem that cannot hold its mode.

    `measure` is the seam the pin reaches through: the default is the
    measurement above, and a caller may supply its own so a control can
    drive BOTH branches of this without a machine that happens to be
    unable. Everything else -- what the reason says, that the refusal
    is quoted, that a capable filesystem grants nothing -- is in
    `test_commit_step_seam.py`, beside the control it governs.
    """
    holds, detail = (measure or filesystem_holds_a_mode)(workdir, environment)
    if holds:
        return
    _util.skip(
        'this filesystem cannot hold a POSIX mode, so the step that sets '
        f'one was not replayed here ({detail}); the step itself is '
        'unchanged and the structural half of its control ran on this '
        'machine regardless')


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
