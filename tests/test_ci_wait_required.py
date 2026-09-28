#!/usr/bin/env python3
"""The gate a caller names for a repository of their own (`--required`).

Issue 1318: `ci_wait.py` certified a head only if `REQUIRED_WORKFLOWS` was
present, and no argument changed it. On a repository whose gating workflow
is named something else, exit 0 was unreachable - an all-green head exited
4, "so this head is not certified", with a line that reads as "the gating
workflow never started" when `tests` simply is not that repository's gate.

A suite of its own because `tests/test_ci_wait_gate.py` is within forty
lines of its own 700-line ceiling, and relocating is the remedy
`scripts/ci/size_baseline.py` prints for a file over it - the same remedy
that put the head-pull-request controls in `tests/test_gh_head_prs.py`.
The run builder and the clock come from `_ci_wait_fixtures`, which is
where a helper shared by more than two suites belongs.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _ci_wait_fixtures import (  # noqa: E402
    _ci_wait_run as _run,
    _ci_wait_clock as _Clock,
    _frozen_ci_wait_clock as _frozen_wait_clock)

ROOT = _util.ROOT
SOURCE = ROOT / '.claude' / 'skills' / 'changing-daedalus' / 'ci_wait.py'
OTHER_REPO = 'example/other'


def _ci_wait():
    return _util.load(SOURCE, 'ci_wait_required_contract')


def _green(*names):
    """One completed green run per named workflow, each its own workflow."""
    return [_run(rid, 'success', f'2026-09-20T10:0{rid}:00Z', name=name,
                 workflow=rid * 11)
            for rid, name in enumerate(names, start=1)]


def _conflicting(number):
    """The open pull request that keeps any workflow from dispatching."""
    return {'number': number, 'state': 'OPEN', 'mergeable': 'CONFLICTING',
            'mergeStateStatus': 'DIRTY'}


def _run_main(mod, clock, argv, runs, pulls=(), err=None):
    """(exit code, stdout) for one `ci_wait.main` invocation.

    Driven through `main` rather than through `wait` because both facts
    the note turns on are caller-level: which `--repo` was named and
    whether `--required` was. A control that called `wait` could not tell
    a caller who named their own gate from one who did not.

    `err` is filled when a caller needs stderr, which is where an
    argument refusal is written.

    A `SystemExit` from `main` is caught here and re-raised as an
    `AssertionError` carrying its code and the refusal it printed. The
    suite runner catches `Exception`, and `SystemExit` is a
    `BaseException`, so one escaping a test ends the FILE: every result
    after it is never printed, and the file exits with the SUBJECT's
    status rather than the runner's (#1321, filed, and a change to
    shared harness behaviour rather than to this flag). Converted here,
    the same regression is an ordinary FAIL inside a run that completes
    and reports every test in it.
    """
    setattr(mod, 'runs_on', lambda repo, sha: list(runs))
    setattr(mod, 'prs_on', lambda repo, sha: list(pulls))
    out = io.StringIO()
    err = err if err is not None else io.StringIO()
    with _frozen_wait_clock(mod, clock), contextlib.redirect_stderr(
            err), contextlib.redirect_stdout(out):
        try:
            code = mod.main(argv)
        except SystemExit as refusal:
            raise AssertionError(
                f'main() ended the interpreter with {refusal.code} instead '
                f'of returning: {err.getvalue()}') from None
    return code, out.getvalue()


def test_a_gate_the_caller_names_certifies_a_green_head(tmp):
    """Issue 1318, the first half: this repository's gate is `ci`, every run
    on the head is green, and the only reason for a refusal is that the
    name this tool spells is not the name this repository gates on."""
    del tmp
    mod = _ci_wait()
    code, text = _run_main(
        mod, _Clock(),
        ['b' * 40, '--repo', OTHER_REPO, '--required', 'ci'],
        _green('ci', 'gate freshness'))
    assert code == 0, text
    assert 'acceptable' in text, text


def test_a_foreign_repository_without_a_named_gate_is_told_the_default(tmp):
    """The second half: the same all-green head, refused, and the refusal
    explains what was checked - the default gate and nothing else unless
    the caller says otherwise. Without it the line reads as "the gate
    never started", which on that repository it is not."""
    del tmp
    mod = _ci_wait()
    code, text = _run_main(
        mod, _Clock(),
        ['c' * 40, '--repo', OTHER_REPO, '--grace', '1', '--interval', '1'],
        _green('gate freshness', 'CodeQL'))
    assert code == 4, text
    assert 'so this head is not certified' in text, text
    assert 'only' in text and '--required' in text, text
    # The names are rendered from the constant rather than spelled beside
    # it, so a rename of the default gate moves this line with it.
    for name in mod.REQUIRED_WORKFLOWS:
        assert name in text, (name, text)


def test_the_default_gate_does_not_move(tmp):
    """Both verdicts that are already true, on the default repository with
    no flags at all: the gate's own green certifies, and its absence still
    refuses. The second refusal must not grow the note, which is about a
    caller who named somewhere else."""
    del tmp
    mod = _ci_wait()
    clock = _Clock()
    code, text = _run_main(mod, clock,
                           ['d' * 40, '--grace', '1', '--interval', '1'],
                           _green('tests', 'gate freshness'))
    assert code == 0, text
    code, text = _run_main(mod, clock,
                           ['e' * 40, '--grace', '1', '--interval', '1'],
                           _green('gate freshness', 'CodeQL'))
    assert code == 4, text
    assert 'so this head is not certified' in text, text
    assert '--required' not in text, text


def test_a_named_gate_that_is_still_absent_names_the_callers_own(tmp):
    """The refusal names what the CALLER asked for, and the note stays out:
    a caller who has stated their gate is not asking what this tool
    checks by default, so answering that would displace the reason."""
    del tmp
    mod = _ci_wait()
    code, text = _run_main(
        mod, _Clock(),
        ['7' * 40, '--repo', OTHER_REPO, '--required', 'ci',
         '--grace', '1', '--interval', '1'],
        _green('gate freshness', 'CodeQL'))
    assert code == 4, text
    assert 'no ci run on' in text, text
    assert 'no tests run' not in text, text
    assert '--required' not in text, text


def test_the_named_gate_set_is_all_of(tmp):
    """Two names, one of them present, is not satisfied: the same all-of
    reading `verdict` has always given, carried now by the caller's own
    set rather than by a default that names one workflow. Satisfied by
    either would be satisfied by neither."""
    del tmp
    mod = _ci_wait()
    code, text = _run_main(
        mod, _Clock(),
        ['8' * 40, '--repo', OTHER_REPO, '--required', 'ci',
         '--required', 'lint', '--grace', '1', '--interval', '1'],
        _green('ci', 'gate freshness'))
    assert code == 4, text
    assert 'no lint run on' in text, text
    assert 'no ci run on' not in text, text


def test_the_conflict_refusal_carries_the_note(tmp):
    """The other exit-4 refusal, reached on a foreign repository too. It
    is permanent rather than slow, which makes it the refusal a caller
    is most likely to read once and believe, and the workflow it names
    is this repository's only by default."""
    del tmp
    mod = _ci_wait()
    code, text = _run_main(
        mod, _Clock(), ['a' * 40, '--repo', OTHER_REPO],
        _green('gate freshness'), pulls=[_conflicting(7)])
    assert code == 4, text
    assert 'pull request #7' in text, text
    assert '--required' in text, text


def test_the_bound_report_carries_the_note_too(tmp):
    """The third report that names a missing gate, on the exit the `--timeout`
    bound earns. The bound is what ends this wait, but the reason it ends
    is the same absence the exit-4 refusals refuse on, so the note belongs
    on it exactly as much."""
    del tmp
    mod = _ci_wait()
    code, text = _run_main(
        mod, _Clock(),
        ['9' * 40, '--repo', OTHER_REPO, '--grace', '300',
         '--interval', '10', '--timeout', '10'],
        _green('gate freshness', 'CodeQL'))
    assert code == 2, text
    assert 'no tests run and' in text, text
    assert '--required' in text, text


def test_the_trial_call_reads_the_named_gate_too(tmp):
    """--once prints a state rather than a verdict, but it prints THE state
    the named gate gives: a trial reporting the default's state would send
    a caller into a wait whose answer it had already contradicted."""
    del tmp
    mod = _ci_wait()
    err = io.StringIO()
    code, _ = _run_main(mod, _Clock(),
                        ['f' * 40, '--once', '--required', 'ci'],
                        _green('ci'), err=err)
    assert code == 0, (code, err.getvalue())
    assert 'state: acceptable' in err.getvalue(), err.getvalue()


def test_an_empty_required_value_is_a_rejected_invocation(tmp):
    """Issue #1320: the set an empty value builds holds a name no run can
    ever carry, so every head refused - and the missing name is empty, so
    the refusal rendered `no  run on <sha>`, its doubled space the only
    evidence the caller had that the argument was the problem.

    Refused the way this tool already refuses a malformed SHA and a
    non-positive bound: named on stderr, exit 3, and nothing at all on
    stdout. The last half is the half a guard written as an ordinary wait
    would get wrong, and it is the half that told the caller a head was
    uncertified when the head was never judged.

    A conflicting pull request is what makes the unfixed path terminate
    on this fixture rather than reach for the network: the gate can never
    be dispatched, so the refusal is immediate instead of a grace away.
    """
    del tmp
    mod = _ci_wait()
    err = io.StringIO()
    code, text = _run_main(mod, _Clock(),
                           ['3' * 40, '--repo', OTHER_REPO, '--required', ''],
                           _green('ci'), pulls=[_conflicting(7)], err=err)
    assert code == 3, (code, text, err.getvalue())
    assert '--required' in err.getvalue(), err.getvalue()
    assert text == '', text


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciwaitreq_')


if __name__ == '__main__':
    raise SystemExit(main())
