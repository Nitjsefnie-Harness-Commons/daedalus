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

TWO HALVES OF THE SEAM, AND TWO MORE PARTS THAT PIN THE SKIP
BETWEEN THEM.

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
running the step's own command.

That measurement decides whether the execution half runs at all, so it
and the decision it feeds are pinned in two separate parts, because one
witness cannot carry both:

- `test_the_skip_is_granted_only_on_a_measured_inability` pins the
  DECISION -- given what the measurement said, is the skip granted, and
  does a failure inside it still red -- and it HANDS THE MEASUREMENT
  IN. Both of the decision's answers are therefore taken on every
  platform, and neither one is an answer this machine happened to give.
- `test_the_mode_measurement_reads_the_filesystem_it_asked` pins the
  MEASUREMENT, by running the real one against the real filesystem: a
  `HOME` the step's own command cannot use, on every platform, and the
  capable answer read back off the directory that command created,
  where a mode can be held. That half is skipped on a filesystem that
  cannot hold one, on the measurement this same part just took, which
  is the only fact it is skipped on.

Split, because a filesystem that cannot hold a mode has no command that
succeeds at holding one. The earlier single pin took its capable answer
from a planted `install`, and on such a filesystem that answer is the
control's own claim rather than a reading of anything: the step's own
command answers the other way there, and quotes the refusal. A decision
that consults no filesystem is answered everywhere; a reading of one is
answered where a filesystem can be read, and named as skipped where it
cannot.

The parts live in one module because the second has to drive the
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
    filesystem_holds_a_mode, MODE_PROBE, MODE_PROBE_DIR, run_workflow_script,
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


def _seam_steps():
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
    refresh, commit = _seam_steps()
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
    """The DECISION, on both of the measurement's answers, on every machine.

    A skip is a soft-lock the next person widens when a test gets
    inconvenient, and it widens silently. The decision itself -- given
    what the measurement said, is the skip granted -- reads no
    filesystem at all, so it is pinned here with the measurement handed
    in, and both of its answers run wherever this does:

    - an inability grants the skip, and the reason carries what the
      measurement said, not a restatement of the skip;
    - a capability grants nothing, and that is the same answer on a
      machine that holds a mode and on one that does not: the answer
      handed in is the answer, and the filesystem under this test is
      never consulted, so a capable answer on a filesystem that cannot
      hold one is no way to turn a step into a silent pass;
    - and a step that cannot commit under a granted capability still
      raises, which is the whole of "a failure inside it still reds".

    What is NOT pinned here is whether the measurement is right, because
    a reading of a filesystem needs one. That is the other part, in
    `test_the_mode_measurement_reads_the_filesystem_it_asked`.
    """
    workdir = Path(tmp) / 'tree'
    workdir.mkdir()
    refused = ('install: cannot change permissions of '
               '/home/.daedalus-mode-probe: Permission denied')

    # An inability grants the skip, naming the why.
    skip = _raised_or_gave(skip_unless_a_mode_can_be_set, workdir, {},
                           measure=lambda work, env: (False, refused))
    assert isinstance(skip, _util.Skipped), (
        f'a measured inability granted nothing at all: {skip!r}')
    assert 'cannot hold a POSIX mode' in str(skip), skip
    assert refused in str(skip), skip

    # A capability grants nothing, whatever this filesystem can do -- and
    # a decision that skipped here instead is caught as the skip it is,
    # not left to end this test quietly.
    granted = _raised_or_gave(skip_unless_a_mode_can_be_set, workdir, {},
                              measure=lambda work, env: (True, ''))
    assert granted is None, f'a measured capability granted a skip: {granted}'

    # And a step that cannot commit under a granted capability still
    # reds. The measurement is not consulted for anything but `install`,
    # so this is a step failure of a kind the mode has nothing to do
    # with: the subject file the commit step commits is gone, and
    # `git commit -F` on a missing pathspec is a real refusal.
    refresh = _util.load(
        ROOT / 'scripts' / 'ci' / 'refresh_timings.py', 'refresh_timings')
    repository, home, _data_file = _checkout(tmp, [101, 100], refresh)
    (repository / 'refreshed-subject.txt').unlink()
    failure = _raised_or_gave(
        _replay, repository, _environment(home, tmp),
        measure=lambda work, env: (True, ''))
    assert isinstance(failure, AssertionError), failure
    assert 'the commit step exited' in str(failure), failure
    assert 'Traceback' not in str(failure), failure


def test_the_mode_measurement_reads_the_filesystem_it_asked(tmp):
    """The MEASUREMENT, run for real, against a filesystem that answers.

    The decision above hands its measurement in, so nothing there would
    notice a measurement that answered the same thing forever. This one
    runs the real thing -- the real `install`, the real filesystem, no
    plant standing in for either -- and holds it to what it found:

    - a `HOME` the step's own command cannot use reads `holds=False`,
      and carries the refusal. That answer is produced on every
      platform, so a constant `True` fails here everywhere;
    - and where a mode CAN be held, the capable answer is read back off
      the directory the step's own command created, at the mode it
      asked for. A constant `False` fails that. This half is the one
      thing here a filesystem has to supply, so it is skipped where one
      cannot -- on the measurement that grants the skip, which is the
      only fact it is skipped on.

    The MISSING-`install` case is not here, and that is the correction
    this shape needed. It was, and on a filesystem that cannot hold a
    mode it read a filesystem fact: the measurement runs the step's own
    command, so on NTFS the command answers first and correctly --
    `install -d -m 700` fails whatever PATH it was given -- and the
    case, which is about the command never having been attempted, was
    handed that answer and called it wrong. It is its own control now,
    in `test_a_missing_install_is_never_a_filesystem_fact`, because it
    is a fact about the classification rather than about a filesystem.
    """
    workdir = Path(tmp) / 'tree'
    workdir.mkdir()
    home = Path(tmp) / 'home'
    home.mkdir()
    environment = _environment(home, tmp)

    # A `HOME` under a regular file is unusable on every platform, so
    # the measurement's own command fails on all of them alike.
    blocked = Path(tmp) / 'blocked'
    blocked.write_text('a file, not a directory', encoding='utf-8')
    holds, detail = filesystem_holds_a_mode(
        workdir, dict(environment, HOME=str(blocked)))
    assert not holds, 'a HOME the command cannot use still read as capable'
    assert blocked.name in detail, detail

    # The capable answer, on a filesystem that can hold the mode.
    skip_unless_a_mode_can_be_set(workdir, environment)
    holds, detail = filesystem_holds_a_mode(workdir, environment)
    assert holds, 'a filesystem that holds a mode read as incapable'
    assert detail == '', detail
    # The measurement removes the directory it made -- it is a scratch
    # answer, and a `~/.daedalus-mode-probe` left behind is an
    # environment the next measurement is standing on. Asserted, not
    # assumed; then the mode is read off the step's own command run
    # directly, which keeps it.
    assert not (home / MODE_PROBE_DIR).exists(), (
        'the measurement left its scratch directory under HOME')
    done = run_workflow_script(workdir, MODE_PROBE, environment)
    assert done.returncode == 0, (done.returncode, done.stderr)
    probe = home / MODE_PROBE_DIR
    assert probe.is_dir(), f'no directory for the step\'s own command: {probe}'
    assert (os.stat(probe).st_mode & 0o777) == 0o700, oct(
        os.stat(probe).st_mode & 0o777)


def test_a_missing_install_is_never_a_filesystem_fact(tmp):
    """The `install` this PATH cannot resolve, on every platform.

    Rule three of this seam, and it is the one that cannot be checked by
    a filesystem. A machine that cannot run the step at all has not
    learned anything about modes, so the measurement reports what it
    found -- the command was never attempted -- and never `holds=False`,
    which would grant the skip on a fact that is not about the
    filesystem. Reading that as a filesystem fact is what made this a
    Windows-only failure: the case needs the command to be unresolvable
    AND needs that to be the only thing wrong, and on NTFS the second
    is never true.

    So it is pinned twice, and the two halves answer different
    questions:

    - the CLASSIFICATION, on every platform and with no filesystem in
      it at all. A `done` carrying the probe's own unresolved marker
      reads as capable and names the reason; a `done` where the command
      ran and exited 127 is a refusal carrying the command's own
      output, not the "never attempted" sentence. That second half is
      the marker earning its keep: with a status code alone the two are
      the same number and one of them is a lie.
    - the MEASUREMENT itself, wherever this PATH can actually make
      `install` unresolvable, which `install_resolves` measures rather
      than assumes. On a filesystem that CAN hold a mode the case is
      load-bearing -- that is where this branch's five mutants died --
      so there it is a failure to have arranged it, not a skip.
    """
    workdir = Path(tmp) / 'tree'
    workdir.mkdir()
    home = Path(tmp) / 'home'
    home.mkdir()
    environment = _environment(home, tmp)
    harness = _util.load(ROOT / 'tests' / '_speedharness.py', '_speedharness')

    # The classification, on every platform, with no filesystem in it.
    holds, detail = harness.classify_mode_attempt(
        harness.unresolved_install_attempt())
    assert holds, 'an unresolvable install read as a filesystem fact'
    assert 'install cannot be resolved' in detail, detail
    # An `install` that RESOLVED and exited 127 is not that sentence.
    # The status is 127 on purpose: it is the number the unresolved
    # branch uses, so this is the case the marker exists to tell apart.
    holds, detail = harness.classify_mode_attempt(
        harness.failed_install_attempt('install: boom', status=127))
    assert not holds, 'a command that ran and failed read as unresolvable'
    assert detail == 'install: boom', detail

    # The measurement, wherever this PATH can arrange the case.
    bare = Path(tmp) / 'bare-bin'
    bare.mkdir()
    stripped = dict(environment, PATH=str(bare))
    resolves, why = harness.install_resolves(workdir, stripped)
    if resolves and filesystem_holds_a_mode(workdir, environment)[0]:
        # A filesystem that can hold a mode is where this case is
        # load-bearing, so failing to arrange it here is a finding
        # about the fixture, not a fact about the machine.
        raise AssertionError(
            'a filesystem that holds a mode still resolves install on an '
            'empty PATH, so the missing-install case cannot be arranged '
            f'where it is load-bearing: {why or "a bare PATH is not bare"}')
    if resolves:
        _util.skip(
            'this filesystem cannot hold a POSIX mode, so the step\'s own '
            'command fails whatever PATH it is given and the '
            'missing-install case is not observable here; the '
            'classification that decides it is pinned above on every '
            'platform, and the case runs on every filesystem that can '
            'hold a mode')
    holds, detail = filesystem_holds_a_mode(workdir, stripped)
    assert holds, 'a PATH with no install reported a filesystem fact'
    assert 'install cannot be resolved' in detail, detail


def test_only_this_module_reaches_the_measurement_seam(tmp):
    """Who may hand `skip_unless_a_mode_can_be_set` a measurement.

    `skip_unless_a_mode_can_be_set(workdir, environment, measure=...)` is
    a seam: a caller may supply its own measurement so a control can
    drive BOTH of its branches without a machine that happens to be
    unable. That is the right reason for it to exist and the wrong thing
    to leave open, because a stub handed in by mistake is a real control
    that skips silently -- the step is never replayed, the assertion it
    would have made is never made, and the suite is green. Nothing about
    the failure looks like a failure.

    So the reach is pinned by reading the call sites rather than by
    trusting that there are only two: every call is in this module, and
    the ones that pass a measurement are named. A new caller has to add
    itself here, which is the point -- the list is short enough to read
    and short enough to argue with.
    """
    reach = _callers_of('skip_unless_a_mode_can_be_set')
    assert reach, 'the seam has no callers at all, so nothing is pinned'
    outside = sorted({module for module, _line in reach
                      if Path(module).resolve() != Path(__file__).resolve()})
    assert not outside, (
        'skip_unless_a_mode_can_be_set is reached from outside '
        'test_commit_step_seam.py, where the skip it grants is not '
        f'pinned: {outside}')
    handing_in = sorted(line for module, line in reach
                        if 'measure=' in _source_line(module, line))
    assert handing_in, 'no caller exercises the measurement seam at all'
    assert len(handing_in) == 2, handing_in


def _callers_of(name):
    """`(file, lineno)` for every call to `name` in the tracked tests."""
    found = []
    for path in sorted((ROOT / 'tests').glob('*.py')):
        for number, line in enumerate(
                path.read_text(encoding='utf-8').splitlines(), 1):
            if f'{name}(' in line and 'def ' not in line:
                found.append((str(path), number))
    return found


def _source_line(module, lineno):
    """The one source line a `(file, lineno)` pair names."""
    lines = Path(module).read_text(encoding='utf-8').splitlines()
    return lines[lineno - 1]


def _replay(repository, environment, measure=None):
    """The execution half, with its measurement supplied.

    Split out so the pin can drive it with a measurement of its own and
    a step that cannot commit, which is the case the skip must not
    swallow. `test_the_committed_subject...` calls it the same way with
    the real measurement.
    """
    _refresh, commit = _seam_steps()
    through = commit[:len(_COMMIT_COMMANDS)]
    skip_unless_a_mode_can_be_set(repository, environment, measure=measure)
    seeded = git_output(repository, 'log', '-1', '--pretty=%s')
    done = run_workflow_script(repository, '\n'.join(through), environment)
    subject = git_output(repository, 'log', '-1', '--pretty=%s')
    if done.returncode or subject == seeded:
        raise AssertionError(_no_commit(
            repository, through, environment, done, subject))
    return subject


def _raised_or_gave(callable_, *args, **kwargs):
    """What `callable_` did: the exception it raised, or the value it gave.

    The pins below judge both -- the skip's own reason has to carry what
    the measurement said, and a decision that grants a skip it should
    not is a decision that REDS here rather than turning the pin itself
    into a skip. So neither shape is allowed to escape as a bare raise.
    """
    try:
        return callable_(*args, **kwargs)
    except Exception as raised:  # noqa: BLE001 - the point is its type
        return raised


def main():
    """Run every test; return the runner's exit code."""
    return _util.runner(_util.collect(globals()), tmp_prefix='commitseam_')


if __name__ == '__main__':
    raise SystemExit(main())
