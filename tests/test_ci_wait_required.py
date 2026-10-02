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
that moved the head-pull-request controls out of that suite, whose own
destination has since been deleted.
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
    _ci_wait_state as _state,
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


def _published():
    """The published `gate freshness` check run, concluded green.

    Every case here is about the WORKFLOW direction rule, and since
    issue 1360 a head whose publisher has written nothing is a state
    of its own that `main` cannot be told about from a flag - it is
    decided by the repository - so the precondition is stated here
    once. `tests/test_ci_wait_published.py` is where the check is the
    subject.
    """
    return [{'id': 7, 'name': 'gate freshness', 'status': 'completed',
             'conclusion': 'success', 'completed_at':
             '2026-09-20T10:10:00Z',
             'html_url': 'https://github.com/o/r/runs/7'}]


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
    setattr(mod, 'ci_on', lambda repo, sha: _state(runs, _published()))
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
    # The whole line, not a couple of its words: a control searching for
    # `'--required'` finds it in the flag's own help text and would pass on
    # a note that had stopped saying what it is for.
    note = ('  only tests is checked by default; --required NAME states '
            'the workflow that gates another repository\n')
    assert text.endswith(note), text
    # NOT tolerance for a rename of the constant: the literal above already
    # pins the name and turns red on one. What this loop holds is that the
    # note carries EVERY name in the set, read from the live constant rather
    # than through that one string alone.
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
    a caller into a wait whose answer it had already contradicted.

    Named on another repository, because that is where a name REPLACES the
    default. On this repository the names are unioned, so `--required ci`
    alone still waits on the gate this tool knows - and that half of the
    rule is held by the controls above.
    """
    del tmp
    mod = _ci_wait()
    err = io.StringIO()
    code, _ = _run_main(mod, _Clock(),
                        ['f' * 40, '--repo', OTHER_REPO, '--once',
                         '--required', 'ci'],
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


def test_a_blank_required_value_is_a_rejected_invocation(tmp):
    """The same mechanism as #1320 under a second spelling. A value of
    whitespace is accepted by a guard that tests the value for emptiness
    alone, and it builds the identical requirement - a name no run can
    carry - so the identical refusal follows, its name slot now holding a
    space instead of nothing at all. Fixing one spelling of the defect and
    leaving the other open is not a fix.

    Two controls rather than one, because each names its own spelling and
    a fixture covering both would pass whenever EITHER is refused, which is
    exactly the hole a single control leaves.
    """
    del tmp
    mod = _ci_wait()
    err = io.StringIO()
    code, text = _run_main(mod, _Clock(),
                           ['4' * 40, '--repo', OTHER_REPO, '--required', ' '],
                           _green('ci'), pulls=[_conflicting(7)], err=err)
    assert code == 3, (code, text, err.getvalue())
    assert '--required' in err.getvalue(), err.getvalue()
    assert text == '', text


# ---- the flag may only tighten, where this tool knows the gate (issue #1217)

def test_the_flag_cannot_drop_this_repositories_own_gate(tmp):
    """The false green #1217 exists to remove, reopened through this
    branch's own flag. A head with no `tests` run at all is `acceptable`
    the moment a caller names a workflow that DID run, because on this
    repository the caller's names replaced the default instead of adding
    to it - and nothing on the output says the gating matrix never ran.

    The refusal names `tests` and not the caller's name, because the
    caller's workflow is present and only the gate is missing; a control
    asserting both would be asserting a report that misstates the runs.
    """
    del tmp
    mod = _ci_wait()
    code, text = _run_main(mod, _Clock(),
                           ['6' * 40, '--required', 'CodeQL'],
                           _green('gate freshness', 'CodeQL'))
    assert code == 4, text
    assert 'no tests run on' in text, text
    assert 'acceptable' not in text, text


def test_a_named_gate_that_did_not_run_still_refuses_naming_both(tmp):
    """The other direction of the same rule, and the two-name refusal it
    makes reachable: on this repository a caller's names are UNIONED with
    the default, so a workflow that never ran is missing alongside the
    gate, and the report names both rather than the one the caller asked
    about.

    The absence of the note is half of what is asserted. The union rule
    and the note's condition answer the same question - is this the
    repository the tool knows - and a control that pinned only the union
    would let them drift into disagreeing, which is how a caller gets the
    foreign-repository note on this repository's own gate.
    """
    del tmp
    mod = _ci_wait()
    code, text = _run_main(mod, _Clock(),
                           ['7' * 40, '--required', 'ci',
                            '--grace', '1', '--interval', '1'],
                           _green('gate freshness', 'CodeQL'))
    assert code == 4, text
    # Each absent gate is named as its own phrase, since issue 1360 added
    # the second kind: a reader told "no ci or tests run" cannot tell
    # which gate to go and look for.
    assert 'no ci run and no tests run on' in text, text
    assert '--required' not in text, text


def test_naming_this_repositories_own_gate_is_unaffected(tmp):
    """A caller who names the gate this tool already knows asks for what
    it was given, and gets it: the union of the default with a name it
    already carries is the default. This is the no-op the direction rule
    has to have, and it is true on the base too - it is a regression
    control, not a RED."""
    del tmp
    mod = _ci_wait()
    code, text = _run_main(mod, _Clock(),
                           ['8' * 40, '--required', 'tests'],
                           _green('tests', 'gate freshness'))
    assert code == 0, text
    assert 'acceptable' in text, text


def test_every_spelling_of_this_repository_is_still_the_default(tmp):
    """The protection is a property of WHICH repository this is, not of
    what the caller typed. Spelling this repository out in full is the
    same repository, and so is a case variant of that spelling; both take
    the union path and both get no foreign-repository note.

    Every row passes `--required` EXCEPT the last, and that last row is the
    one carrying the claim. With `--required` in hand the note's `named`
    disjunct short-circuits, so `--required' not in text` is proved by the
    flag and never reaches the repository comparison - which is how
    `ci_gate.gate_note` came to hold a literal `repo == DEFAULT_REPO` beside a
    helper it was supposed to be asking, with every suite green. A row
    with no `--required` proves the note's absence by the REPOSITORY
    alone, and the case variant is the only spelling that discriminates:
    the exact one matches `DEFAULT_REPO` literally and would pass with the
    helper deleted.

    The other rows document the behaviour rather than pinning the helper.
    """
    del tmp
    mod = _ci_wait()
    rows = (
        ('Nitjsefnie-Harness-Commons/daedalus', ['--required', 'CodeQL']),
        ('Nitjsefnie-Harness-Commons/daedalus', []),
        ('nitjsefnie-harness-commons/daedalus', ['--required', 'CodeQL']),
        ('nitjsefnie-harness-commons/daedalus', []),
    )
    for spelled, named in rows:
        code, text = _run_main(mod, _Clock(),
                               ['9' * 40, '--repo', spelled] + named,
                               _green('gate freshness', 'CodeQL'))
        assert code == 4, (spelled, named, text)
        assert 'no tests run on' in text, (spelled, named, text)
        assert '--required' not in text, (spelled, named, text)


def test_a_padded_required_name_is_the_name_it_pads(tmp):
    """A name that is whitespace AROUND something is the third spelling of
    #1320. `not name.strip()` refuses a name that is only whitespace, but
    `' ci '` survives it and builds a requirement no run carries - and
    the refusal renders the padding into the name slot, so it reads
    `no  ci  run on <sha>` and names a workflow nobody can supply.

    Exit 0 here and exit 3 for the two blank controls, so the three
    spellings cannot be mistaken for one another.
    """
    del tmp
    mod = _ci_wait()
    code, text = _run_main(mod, _Clock(),
                           ['a' * 40, '--repo', OTHER_REPO,
                            '--required', ' ci ', '--grace', '1',
                            '--interval', '1'],
                           _green('ci'))
    assert code == 0, text
    clock = _Clock()
    code, text = _run_main(mod, clock,
                           ['b' * 40, '--repo', OTHER_REPO,
                            '--required', ' ci ', '--grace', '1',
                            '--interval', '1'],
                           _green('gate freshness'))
    assert code == 4, text
    assert 'no ci run on' in text, text


# Exactly what a no-flags refusal prints, so the comparison below is
# against a fixed string rather than against another run of this code.
_DEFAULT_REFUSAL = (
    '555555555555 2 run(s)\n'
    '  gate freshness: completed/success\n'
    '  CodeQL: completed/success\n'
) * 2 + (
    'no tests run on 555555555555 after the 1s grace, so this head is not '
    'certified: the 2 run(s) on this SHA are gate freshness, CodeQL\n'
)


def test_naming_this_repositories_own_gate_prints_identically(tmp):
    """The claim SKILL.md makes - naming the gate this tool already knows
    leaves the output byte for byte what it was - held here. It used to be
    cited to `ci_gate`'s `gate_note`, which no longer states it: that
    docstring now claims only that the NOTE is empty on those paths, which
    is the narrower thing that survives the union.

    The claim is pinned to the literal above, not to a comparison between
    two runs of this code: the plain run is the reference AND the
    assertion, so an edit that moved both would otherwise pass.

    Every spelling of this repository's name, because the comparison that
    decides it is case-insensitive over the resolved value - a caller who
    typed the name out in full, or changed its case, has named the same
    repository and must get the same bytes. One row would have held the
    claim for the spelling it happened to use.
    """
    del tmp
    mod = _ci_wait()
    argv = ['5' * 40, '--grace', '1', '--interval', '1']
    runs = _green('gate freshness', 'CodeQL')
    plain = _run_main(mod, _Clock(), argv, runs)
    assert plain == (4, _DEFAULT_REFUSAL), plain
    for spelled in ([], ['--repo', 'Nitjsefnie-Harness-Commons/daedalus'],
                    ['--repo', 'nitjsefnie-harness-commons/daedalus']):
        got = _run_main(mod, _Clock(),
                        argv + spelled + ['--required', 'tests'], runs)
        assert got == plain, (spelled, got)


# ---- the two refusals that are the invocation's rather than a gate's

def _exited_invocation(mod, clock, argv):
    """(exit code, stdout, stderr) for an invocation that may END rather
    than return.

    `_run_main` above turns a `SystemExit` into a failure, because every
    exit-3 path it was written for returns a code. These two do not:
    one of them is argparse's own refusal reaching this tool's exit, and
    the exit IS the answer under assertion. Nothing is stubbed here, so
    a refusal that stopped being one would reach the network rather than
    pass - the frozen clock is what keeps that a bounded failure.
    """
    out, err = io.StringIO(), io.StringIO()
    with _frozen_wait_clock(mod, clock), \
            contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        try:
            code = mod.main(argv)
        except SystemExit as refusal:
            code = refusal.code
    return code, out.getvalue(), err.getvalue()


def test_a_flag_this_tool_does_not_have_is_a_rejected_invocation(tmp):
    """The refusal argparse itself produces, on this tool's exit.

    Every other refusal in this file is one `ci_wait.py` writes, so none
    of them can tell `RefusingParser` from argparse's own: a stock
    parser prints the same usage block and the same `error:` line and
    exits 2, which this tool's own contract spends on a TIMEOUT. A caller
    reading that exit would wait out a bound for an invocation that was
    never accepted, and would be told the wait expired when nothing was
    ever asked.

    Asserted on the refusal's own words rather than on the status alone:
    the usage block printed first says the invocation was not read, and
    the `error:` line says which of its arguments was not recognised.
    """
    del tmp
    mod = _ci_wait()
    argv = ['a' * 40, '--no-such-flag']
    code, text, err = _exited_invocation(mod, _Clock(), argv)
    assert code == 3, (code, err)
    assert err.startswith('usage: '), err
    assert 'error: unrecognized arguments: --no-such-flag' in err, err
    assert text == '', text


def test_a_failed_query_refuses_at_once_with_its_reason(tmp):
    """The other exit-3 limb, and the only one nothing in this file
    reaches.

    A query that failed is `gh_client`'s to raise and this tool neither
    wraps nor retries it, so without a control the whole handler could
    go and the exception would escape as a traceback whose exit status is
    1 - a number this tool has spent on an UNACCEPTABLE verdict, so a
    caller would read a failed read as a red head.

    The message is the assertion, not the status: `query failed:` alone
    tells a caller nothing about which read failed or why, and the reason
    the query gave is the part they can act on. Read through `--once`, so
    the stubbed read is the only thing on the path.
    """
    del tmp
    mod = _ci_wait()

    def _refuse(repo, sha):
        raise mod.gh_client.QueryError('gh: not found (exit 1)')

    setattr(mod, 'ci_on', _refuse)
    code, text, err = _exited_invocation(mod, _Clock(), ['a' * 40, '--once'])
    assert code == 3, (code, err)
    assert err == 'query failed: gh: not found (exit 1)\n', err
    assert text == '', text


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='ciwaitreq_')


if __name__ == '__main__':
    raise SystemExit(main())
