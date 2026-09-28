#!/usr/bin/env python3
"""The refresh workflow's commit step: the seam, and where it can be run.

The refresh step writes the commit subject to a workspace file and the
commit step commits that file, so the seam is worth a control: the
subject the refresh wrote must be the subject the commit step commits,
and it must name the runs the refreshed file records in `measured_from`.
Grepping both steps for `--message-file` and `-F` never established
that -- one line planted in the commit step left every asserted
substring intact and the workflow unable to commit -- so the command
lists are compared EXACTLY and then the step is RUN.

TWO HALVES, AND ONLY ONE OF THEM IS ENVIRONMENTAL.

THE STRUCTURAL HALF compares the two command lists. It reads a YAML file
and needs nothing from the machine, so it runs on every platform and is
never skipped. It is what catches the seam drifting.

THE EXECUTION HALF replays the commit step's commands inside a temp
checkout and reads the subject back off the commit. It needs a
filesystem that can hold a POSIX mode, because the step's first
external command is `install -d -m 700 ~/.ssh`. That works on the
`ubuntu-latest` runner the workflow actually uses and cannot work on a
filesystem with no mode to hold -- NTFS creates the directory and
reports that it cannot change its permissions. So the execution half
skips there, and the skip is granted on a MEASUREMENT of that
filesystem, never on a platform name:
`_speedharness.skip_unless_a_mode_can_be_set` asks the question by
running the step's own command, and `test_the_skip_is_granted_only_on_a_
measured_inability` pins both branches, so a skip cannot widen into a
soft-lock.

The two halves live in one module because the second has to drive the
first: a pin that could not reach the control it pins would be a
comment. It is wired into `timed-timings.yml`'s "Verify the change"
step, which is the workflow whose subject it is.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _repo import (  # noqa: E402
    commit_environment, committable, git_index, git_output, ROOT)
from _speedharness import (  # noqa: E402
    filesystem_holds_a_mode, run_workflow_script,
    skip_unless_a_mode_can_be_set, workflow_script)

_REFRESH_COMMANDS = [
    'python3 scripts/ci/refresh_timings.py --runs-root runs \\',
    '  --message-file refreshed-subject.txt \\',
    '  2>> "$GITHUB_STEP_SUMMARY"',
    'git diff --text -- .github/suite-timings.json \\',
    '  >> "$GITHUB_STEP_SUMMARY"',
]
_COMMIT_COMMANDS = [
    'if git diff --quiet -- .github/suite-timings.json; then',
    '  echo "No weight moved; nothing to commit."',
    '  exit 0',
    'fi',
    'install -d -m 700 ~/.ssh',
    "printf '%s\\n' \"$RATCHET_SSH_KEY\" > ~/.ssh/ratchet",
    'chmod 600 ~/.ssh/ratchet',
    "printf '%s\\n' 'github.com ssh-ed25519 "
    'AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\''
    ' \\',
    '  > ~/.ssh/known_hosts',
    'chmod 600 ~/.ssh/known_hosts',
    "git config user.name 'github-actions[bot]'",
    "git config user.email "
    "'41898282+github-actions[bot]@users.noreply.github.com'",
    'git add .github/suite-timings.json',
    'git commit -F refreshed-subject.txt',
]


def _commands(script):
    """The step's source lines that are commands: no comment, no blank."""
    return [line for line in script.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def _steps():
    """The two `run:` blocks the seam is made of, as command lists."""
    source = (ROOT / '.github' / 'workflows' / 'timed-timings.yml'
              ).read_text(encoding='utf-8')
    refresh = workflow_script(source, 'refresh', 'Refresh the data file')
    commit = workflow_script(source, 'refresh', 'Commit the refresh')
    return _commands(refresh), _commands(commit)


def test_the_two_steps_are_pinned_as_exact_command_lists(tmp):
    """The structural half, and it runs on every platform.

    Neither half is optional and the structural one is never skipped:
    a list is compared, not grepped, so planting `rm -f
    refreshed-subject.txt` as the commit step's first line -- which left
    every asserted substring in place and the suite green on a workflow
    that could no longer commit at all -- is caught here on a machine
    that could not run the step even if it wanted to.

    The commit list is compared as a PREFIX, because the step carries
    more after the commit (the push, and its own distinction between a
    rejected push and a moved main). The prefix is the seam.
    """
    refresh, commit = _steps()
    assert refresh == _REFRESH_COMMANDS, refresh
    through = commit[:len(_COMMIT_COMMANDS)]
    assert through == _COMMIT_COMMANDS, through


def _checkout(tmp, runs, refresh):
    """A checkout with a seed commit and the refresh's unstaged write.

    `newline='\\n'` on every write, because a text-mode write on Windows
    emits CRLF and the checkout's `core.autocrlf` then decides what
    `git diff` means. The step's own first command is a guard on that
    diff, so a checkout whose normalisation differs from the fixture's
    exits 0 without committing.
    """
    repository = Path(tmp) / 'checkout'
    (repository / '.github').mkdir(parents=True)
    data_file = repository / '.github' / 'suite-timings.json'

    def write(measured_from, weight):
        data_file.write_text(json.dumps(
            {'schema_version': 2, 'target_cell_weight': 25.0,
             'max_cells': 15, 'units': 'reference-multiples',
             'measured_from': measured_from, 'runs': 1,
             'suite_weights': {'test_a.py': weight}}) + '\n',
            encoding='utf-8', newline='\n')

    write('300', 1.0)
    git_index(repository, 'init', '-q')
    committable(repository)
    git_index(repository, 'add', '--', '.github/suite-timings.json')
    git_index(repository, 'commit', '-q', '-m', 'base')
    write(','.join(str(run) for run in runs), 2.0)
    (repository / 'refreshed-subject.txt').write_text(
        refresh.commit_message(runs) + '\n', encoding='utf-8', newline='\n')
    home = Path(tmp) / 'home'
    home.mkdir()
    return repository, home, data_file


def _environment(home, tmp):
    return {**commit_environment(home), 'RATCHET_SSH_KEY': 'not-a-key',
            'GITHUB_STEP_SUMMARY': str(Path(tmp) / 'summary.md'),
            'REPO': 'example/example'}


def _planted(tool, script, tmp, slot=None):
    """A PATH holding an executable named `tool` running `script`.

    Ahead of everything else, which is the only way to make a real
    command behave differently for a control without changing the
    command the control runs. `slot` names the directory when one
    directory has to hold two plants of the SAME tool -- which it does
    here, because the measurement is pinned by an install that sets the
    mode next to one that refuses it, and the second would otherwise
    overwrite the first.
    """
    binaries = Path(tmp) / 'plantbin' / (slot or tool)
    binaries.mkdir(parents=True, exist_ok=True)
    planted = binaries / tool
    planted.write_text(f'#!/bin/sh\n{script}\n', encoding='utf-8',
                       newline='\n')
    os.chmod(planted, 0o755)
    return f'{binaries}{os.pathsep}{os.environ["PATH"]}'


def _step_environment(base, path):
    return dict(base, PATH=path)


def test_the_committed_subject_names_exactly_the_runs_the_file_records(tmp):
    """The execution half: the step runs, and the subject is the refresh's.

    The step's exit status is not the whole answer, and the shape that
    first refused only on a nonzero status proved it. A block that
    reaches `git diff --quiet`, finds the file unchanged and exits 0 has
    the SAME status as a block that committed, so the subject assertion
    after it read the SEED's subject and reported it as the step's --
    one line reading `base`, with no exit status and no step output. So
    both shapes are refused, and the refusal carries the step's own
    stdout and stderr, a `set -x` re-run whose trace names the last
    command line the shell reached, and which bash, `HOME` and `~` that
    was.
    """
    runs = [101, 100]
    refresh = _util.load(
        ROOT / 'scripts' / 'ci' / 'refresh_timings.py', 'refresh_timings')
    repository, home, data_file = _checkout(tmp, runs, refresh)
    subject = _replay(repository, _environment(home, tmp))
    named = set(re.findall(r'\d+', subject))
    assert named == {str(run) for run in runs}, subject
    assert 'ci: refresh suite timings from run' in subject, subject
    assert json.loads(data_file.read_text(encoding='utf-8'))[
        'measured_from'] == ','.join(str(run) for run in runs)


def _no_commit(repository, commands, environment, done, subject):
    """Why a `run:` block that should have committed did not.

    Nothing here guesses at a cause. The trace names the last command
    line the shell reached, whatever that turns out to be, and the probe
    reports the resolved bash, `HOME`, `~` as the step saw it, which of
    the step's own tools are missing, and whether this filesystem can
    hold a POSIX mode at all -- the last of those is what the skip is
    granted on, so a reader can see the same measurement the skip saw.
    """
    traced = run_workflow_script(
        repository, 'set -x\n' + '\n'.join(commands), environment)
    probe = run_workflow_script(
        repository,
        'printf "bash=%s\\n" "$(command -v bash)"\n'
        'printf "HOME=%s tilde=%s\\n" "$HOME" "$(cd ~ && pwd)"\n'
        'for tool in git install chmod mkdir; do\n'
        '  command -v "$tool" > /dev/null || echo "MISSING $tool"\n'
        'done\n'
        'probe="$HOME/.daedalus-mode-probe"\n'
        'if mkdir -p "$probe" 2>/dev/null && chmod 700 "$probe" 2>&1;'
        ' then echo "chmod 700 on a directory under HOME: ok";\n'
        'else echo "chmod 700 on a directory under HOME: refused"; fi\n'
        'if : > "$probe/file" 2>/dev/null && chmod 600 "$probe/file" 2>&1;'
        ' then echo "chmod 600 on a file under HOME: ok";\n'
        'else echo "chmod 600 on a file under HOME: refused"; fi\n'
        'rm -rf "$probe"',
        environment)
    trace = traced.stderr.strip().splitlines()
    said = done.stderr.strip() or done.stdout.strip() or 'no output'
    return (
        f'the commit step exited {done.returncode} and left the subject at '
        f'the seed\'s {subject!r}: {said}; the traced re-run stopped at '
        f'{trace[-1] if trace else "nothing it traced"}; '
        f'{probe.stdout.strip() or "the probe said nothing"}')


def test_the_skip_is_granted_only_on_a_measured_inability(tmp):
    """Both branches of the skip, and the capable one still refuses.

    A skip is a soft-lock the next person widens when a test gets
    inconvenient, and it widens silently. So the measurement is pinned
    in both directions and the refusal is pinned as not being one of
    the things it swallows:

    - a filesystem that REFUSES the mode grants the skip, and the reason
      carries what the refusal said;
    - a filesystem that HOLDS the mode grants nothing, and a step that
      cannot commit on it still raises. The plant is a `git` that fails
      only on `commit`, so the measurement -- which asks about `install`
      -- still says capable, and the control refuses rather than skips.
      That is the whole of "a failure inside it still reds";
    - a MISSING `install` is not a filesystem fact and does not skip: a
      machine that cannot run the step should say so, not go quiet.

    The measurement is asked, not assumed, and BOTH of its answers are
    produced here, by an `install` that sets the mode and one that
    refuses it. Neither answer is the ambient filesystem's, so the pin
    reads the same on a machine that can hold a mode and on one that
    cannot -- and it cannot be a constant that always grants, or one
    that always refuses.
    """
    workdir = Path(tmp) / 'tree'
    workdir.mkdir()
    refusing = _planted(
        'install',
        'echo "install: cannot change permissions of $1: '
        'Permission denied" >&2\nexit 1', tmp, slot='refusing')
    # The measurement reads the filesystem, so both answers are real.
    # It answers "holds a mode", so an install that refuses to set one
    # reads False -- the same way a real NTFS refusal does.
    holds, detail = filesystem_holds_a_mode(
        workdir, _step_environment({}, refusing))
    assert not holds, 'a refusing install still read as capable'
    assert 'Permission denied' in detail, detail
    succeeding = _planted('install', 'exit 0', tmp, slot='succeeding')
    holds, detail = filesystem_holds_a_mode(
        workdir, _step_environment({}, succeeding))
    assert holds, 'an install that succeeds still read as incapable'
    assert detail == '', detail

    # Branch one: a measured inability grants the skip, naming the why.
    skip = _raised(skip_unless_a_mode_can_be_set, workdir,
                   _step_environment({}, refusing))
    assert isinstance(skip, _util.Skipped), skip
    assert 'cannot hold a POSIX mode' in str(skip), skip
    assert 'Permission denied' in str(skip), skip

    # Branch two: capable grants nothing.
    assert skip_unless_a_mode_can_be_set(
        workdir, {}, measure=lambda work, env: (True, '')) is None

    # And a step that cannot commit on a capable filesystem still reds.
    # The measurement is not consulted for anything but `install`, so
    # this is a step failure of a kind the mode has nothing to do with:
    # the subject file the commit step commits is gone, and
    # `git commit -F` on a missing pathspec is a real refusal.
    refresh = _util.load(
        ROOT / 'scripts' / 'ci' / 'refresh_timings.py', 'refresh_timings')
    repository, home, _data_file = _checkout(tmp, [101, 100], refresh)
    (repository / 'refreshed-subject.txt').unlink()
    failure = _raised(
        _replay, repository, _environment(home, tmp),
        measure=lambda work, env: (True, ''))
    assert isinstance(failure, AssertionError), failure
    assert 'the commit step exited' in str(failure), failure
    assert 'Traceback' not in str(failure), failure


def _replay(repository, environment, measure=None):
    """The execution half, with its measurement supplied.

    Split out so the pin can drive it with a measurement of its own and
    a step that cannot commit, which is the case the skip must not
    swallow. `test_the_committed_subject...` calls it the same way with
    the real measurement.
    """
    _refresh, commit = _steps()
    through = commit[:len(_COMMIT_COMMANDS)]
    skip_unless_a_mode_can_be_set(repository, environment, measure=measure)
    seeded = git_output(repository, 'log', '-1', '--pretty=%s')
    done = run_workflow_script(repository, '\n'.join(through), environment)
    subject = git_output(repository, 'log', '-1', '--pretty=%s')
    if done.returncode or subject == seeded:
        raise AssertionError(_no_commit(
            repository, through, environment, done, subject))
    return subject


def _raised(callable_, *args, **kwargs):
    """The exception `callable_` raised, or a failure saying it raised none.

    The pin below needs the exception's own MESSAGE -- the skip reason
    has to carry what the filesystem said -- so this returns the
    exception rather than merely asserting that one arrived.
    """
    try:
        callable_(*args, **kwargs)
    except Exception as raised:  # noqa: BLE001 - the point is its type
        return raised
    raise AssertionError(f'{callable_!r} raised nothing')


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='commitseam_')


if __name__ == '__main__':
    raise SystemExit(main())
