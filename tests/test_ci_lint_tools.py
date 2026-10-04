#!/usr/bin/env python3
"""The binaries a suite skips on must be installed in every job that runs it.

A suite that shells out to a real binary SKIPS when the binary is absent,
and a skip is a pass to every runner and every aggregate. So a job without
the binary is green having verified nothing, silently, on every leg — the
shape issue 1353 was for actionlint and shellcheck.

Which jobs reach a suite is derived from what the jobs' steps are given;
which tools the suites can skip on is derived from the suites' own source.
Nothing in either direction is maintained by hand except the residue one
control names, and that is a claim about the complement rather than
another list to keep in step. The job derivation is shared with the
control already on that set, through `tests/_suite_jobs.py`, and the
mechanisms a skipped tool can be answered by are read through
`tests/_lint_tool_mechanisms.py`.

THREE mechanisms answer a tool a suite skips on: the shared installer
installs one, a setup step declares another, and a claim about the runner
image excuses a third. This file is the installer's half — the installer
step, the tools it declares, the lint-tool install, and the build it pins
— and `tests/test_ci_tool_declarations.py` is the other two, because a
control filed under a name that does not match its subject is one a reader
will not look for. The split is of subject and not of mechanism: the three
tables and the recognisers that read what a job runs are in the helper both
files read, so nothing about which mechanism answers a tool is written
twice, and the closure control there holds all three to the derived set, so
the next tool a suite skips on reaches one of them rather than a fourth
hand-written exemption — which is what `node` was, and the reason the
`node` entry cannot come back.

Every control here is a guard: a green run proves the tree still matches
it and nothing more. The proof that it bites is a planted defect in a real
target — the installer step removed from a real suite job, a skip arm added
to a real suite for a binary nothing installs, and a third suite runner
planted in a workflow the door walk then has to classify. Every workflow
is read now: `_door_jobs` globs `*.yml` and `*.yaml`, so no workflow is
one this file does not look at, and the bound on what the walk can see is
stated in `tests/_suite_jobs.py` where it lives.

The installer's REFUSALS are driven in `tests/test_ci_lint_tool_refusals.py`,
through the stand-ins `tests/_lint_tool_refusals.py` holds at the boundaries
those paths cross. What stays here is the other question this file asks: that
every job which reaches the suites installs what those suites may skip on, and
that the transfer boundary the download crosses still refuses what it is
written to refuse.
"""
import hashlib
import os
import re
import shutil
import sys
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _actionlint import _job_step as _actionlint_job_step  # noqa: E402
from _lint_tool_mechanisms import (  # noqa: E402
    INSTALLER_PATH, INSTALLER_SOURCE, _Transfer, _asset_module,
    _declared_tools, _http_error, _installer_and_transfer, _runs_installer,
    _unjournalled)
from _lint_tool_roles import (  # noqa: E402
    BOTH_ON, GUARDED_ON, REQUIRED_ON, _PREAMBLE, _derive_tool_roles,
    _tool_roles)
from _suite_jobs import NAMES, _door_jobs, _runner_doors  # noqa: E402
from _wfgraph import _tests_yml  # noqa: E402

ROOT = _util.ROOT
# The name the shared installer writes what it installed under, and where
# the script lives. Both live with the mechanisms in
# `tests/_lint_tool_mechanisms.py`; the refusals below name them.
LINT_TOOLS_ENV = 'DAEDALUS_LINT_TOOLS'
# The doors that reach the suites without FINDING them: a fixed list of
# suite paths written in the step, so a suite added tomorrow cannot walk
# through one of these at all. What each entry owes is that its list stays
# true — the suites it names are the suites it runs — and nothing about
# installing tools, because nothing new can arrive through it.
#
# This is the residue of a DERIVED set, not a second copy of it. Every
# other route is either a `RUNNER` door, which the control below holds to
# the installer step, or a door this walk cannot see at all: a step whose
# reach is decided by a composite action or a container entry point has no
# source in this repository to read. That bound is the walk's, stated here
# where the table meets it.
SUITE_DOORS: dict[tuple[str, str], str] = {}


def test_every_spelling_of_an_absence_guard_is_read_as_one_operation(tmp):
    """No suite is invisible because its author spelled the guard differently.

    Driven through the derivation rather than planted in `tests/`, because
    the question is what the recognisers read and every shape would
    otherwise be a tracked change. One of these is the two-step
    truthiness guard that reads as a PRESENCE test — a suite skipping on a
    binary no job installs — and the control missed it for a whole wave.

    The NAME says "every spelling" and `GUARDED_ON` does not deliver
    that: it is a list of witnesses, one per way the recogniser's
    structure can fail, and the list is evidence rather than the gate.
    Adding a shape turns this red, which is exactly what a witness is
    for — a recogniser that cannot see a shape nobody thought of fails
    nothing — but a reader must not take the name as a completeness claim
    the table does not carry. The shapes still open, and the reason each
    is not a row here, are named beside the table.
    """
    del tmp
    for label, body in GUARDED_ON.items():
        skipped, present = _derive_tool_roles([_PREAMBLE + body + '\n'])
        assert 'gojq' in skipped, (
            f'the {label} spelling puts no tool in the skip set, so a suite '
            'written that way is invisible to this control and its binary '
            f'needs no install: skipped={sorted(skipped)}, '
            f'present={sorted(present)}')


def test_an_asserted_tool_is_a_requirement_and_not_a_skip(tmp):
    """The other half of the distinction, so the fix is not one-sided.

    The two-step idiom every helper in the tree uses is
    `found = which(X); if not found: skip`. Reading anything that mentions
    the name as a skip would file `assert found, '...'` as a tool the
    suites tolerate the absence of, and demand an install for a binary
    every job already has.
    """
    del tmp
    for label, body in REQUIRED_ON.items():
        skipped, present = _derive_tool_roles([_PREAMBLE + body + '\n'])
        assert 'gojq' in present, (
            f'the {label} shape puts no tool in the present set: '
            f'skipped={sorted(skipped)}, present={sorted(present)}')


def test_a_tool_the_tree_also_asserts_is_still_required(tmp):
    """A skip is a skip, whatever another function says about the binary.

    The required set was once `skipped - present`, on the argument that a
    tool in both is covered by a control that fails rather than skips. That
    argument is one the derivation cannot check — it cannot tell an assert
    the tree always reaches from one behind a condition that is false on
    every ordinary run — and two reviews drove a false green through it: a
    `which('jq')` + skip arm grew the skip set, jq was in the present set
    because of the assert behind `needs_jq_stub`, and the control stayed
    green with no job installing jq. The second case is the sharper one,
    because the old derivation read the FileNotFoundError skip as a tool
    the tree had DECIDED may not be absent: it inverted the site.
    """
    del tmp
    for label, body in BOTH_ON.items():
        source = [_PREAMBLE + body + '\n']
        skipped, present = _derive_tool_roles(source)
        assert 'gojq' in _unjournalled(source), (
            f'the {label} shape leaves gojq out of the required set: '
            f'skipped={sorted(skipped)}, present={sorted(present)}')


def _require_resolvable(tools):
    """Every recorded tool must resolve on PATH, or this fails.

    A skip here would be the defect this control exists to close: the runner
    reports a skip as a pass, so a broken install would read as a clean gate.
    """
    missing = [tool for tool in tools if shutil.which(tool) is None]
    assert not missing, (
        f'the installer recorded {", ".join(missing)} in ${LINT_TOOLS_ENV}, '
        f'and {", ".join(missing)} does not resolve on PATH. A suite that '
        'skips on one of them skips silently here, so this is a failure and '
        'not a skip: fix the install step, not this control')


def test_every_suite_running_job_installs_the_tools_its_suites_may_skip_on(
        tmp):
    """A suite that skips on a missing binary skips on every leg, always.

    `needs:` sequences jobs without sharing an environment between them, so
    the job that lints the workflows hands the suite jobs nothing. An
    installer step in one of them leaves the others skipping silently, which
    is the shape issue 1353 was.

    The job set is the DERIVED one, not the two sanctioned runner
    basenames. A job that reaches the suites by any other discovery
    mechanism is a job a suite added tomorrow walks through, and a control
    that cannot see it cannot hold it to anything.
    """
    del tmp
    found = _runner_doors()
    # Not vacuous: an enumeration that finds nothing is the same green a
    # correct one does not produce, so the set has to be bigger than one.
    assert len(found) >= 2, (
        f'only {len(found)} job(s) find their suites, so this control is '
        'reading a set too small to be the whole set of them: '
        f'{[(source, job) for source, job, _, _ in found]}')
    declared = ', '.join(sorted(_declared_tools()))
    unjournalled = sorted(_unjournalled())
    assert not unjournalled, (
        f'the suites under tests/ skip on {", ".join(unjournalled)}, and '
        'no job installs them, so a suite-running job on a runner without '
        'them skips in silence on every leg; '
        f'scripts/ci/install_lint_tools.py declares {declared}, and the '
        'next binary a suite skips on has to be added there, or declared '
        'in DECLARED_BY with the setup action a job must carry, or listed '
        'in SHIPPED_BY_THE_IMAGE with the reason no job has to install it')
    for source, job, runs, _mechanism in found:
        assert any(_runs_installer(run) for run in runs), (
            f'the {job} job in {source} finds its suites by discovery, so a '
            'suite added tomorrow reaches it whatever it is called; the '
            f'suites it finds skip on {_unjournalled_sentence()}, and the '
            f'job never runs {INSTALLER_PATH!r}, so on a runner without '
            'them those suites skip instead of running and the job reports '
            'green')


def _unjournalled_sentence():
    """What the suites actually skip on, for a message that must be true.

    The message this replaces named the installer's declaration and called
    it the derived set. On this tree the two differ: the derived set is
    empty until 1307's suites land, so the control was asserting in its own
    output that the suites it discovers skip on actionlint and shellcheck,
    which no suite here does.
    """
    unjournalled = sorted(_unjournalled())
    return (', '.join(unjournalled) if unjournalled
            else 'no tool this control can currently derive')


def test_the_installer_declares_a_tool_and_the_suites_state_one(tmp):
    """Both halves of the derivation are read, so neither can read empty."""
    del tmp
    skipped, present = _tool_roles()
    assert skipped, (
        'no suite skips on a missing tool, so the tool set this control '
        'derives from tests/ is empty and the control is satisfied by a set '
        'with nothing in it; has the skip idiom moved?')
    assert _declared_tools(), (
        f'{INSTALLER_PATH!r} declares no tools, so it installs nothing and '
        'every job that runs it gains a step and no tool')
    assert present, (
        'no suite treats a tool as unconditionally required either, so the '
        'second half of the derivation is reading an empty set: the two '
        'channels together are what tells a binary a job must install from '
        'one the suite fails loudly without, and one of them going empty '
        'means the recogniser that fills it has stopped recognising')


def test_no_job_reaches_the_suites_by_a_door_this_control_does_not_name(tmp):
    """The job set is closed: what the walk derives, plus what it cannot.

    `_door_jobs` reads each step's resolved inputs and follows the tracked
    file a step runs, so a third `scripts/ci/` runner, a shell loop over
    `tests/*.py`, `unittest discover -s tests`, a `with:`-passed path and a
    `.yaml` workflow are all routes it sees. What it cannot see is a route
    whose reach lives outside this repository — a composite action, a
    container entry point — and that bound belongs in the table beside the
    doors it does see, not in a claim this control cannot make.

    The two kinds are judged differently, which is why they are two
    things. A `runner` door FINDS its suites, so a suite added tomorrow
    walks through it and the control above holds it to the installer step.
    A `names` door runs a list written in the step, so nothing new can
    arrive through it and the only thing owed is that the list stays true
    — which is what its reason says.
    """
    del tmp
    named_doors = {(source, job) for source, job, _runs, mechanism
                   in _door_jobs() if mechanism == NAMES}
    residue = named_doors - set(SUITE_DOORS)
    stale = set(SUITE_DOORS) - named_doors
    assert not residue and not stale, (
        'doors into the suite tree that reach the suites by naming them and '
        f'SUITE_DOORS does not account for: {sorted(residue)}; entries in '
        f'SUITE_DOORS that are no longer such a door: {sorted(stale)}. A '
        'door that FINDS its suites needs no entry — the control above '
        'holds it to the installer step. A door that NAMES them carries a '
        'fixed list, so name it here with that fact, and say what the list '
        'is, because nothing new can reach a job that does not glob.')


def test_every_tool_the_installer_recorded_resolves_on_path(tmp):
    """What the installer says it installed has to be there afterwards.

    Gated on the variable the installer writes, so this reaches the `suites`,
    `coverage-matrix` and `publish` jobs in `tests.yml` and `release.yml`,
    and nowhere else: those three are the jobs whose steps run
    `python scripts/ci/install_lint_tools.py`.
    Off a CI leg the installer has not run and there is no subject to
    check. A leg where it DID run and the binary is still missing fails
    below rather than skipping — the whole defect is a check that reported
    success having verified nothing.

    The early return is a SKIP carrying its reason, not a bare pass. A run
    log has to distinguish "ran, and every tool resolved" from "did not
    run", and a control whose subject is this branch's whole motivation
    cannot be the one that reports nothing about itself.
    """
    del tmp
    recorded = os.environ.get(LINT_TOOLS_ENV)
    if recorded is None:
        _util.skip(
            f'${LINT_TOOLS_ENV} is unset, so the installer has not run on '
            'this leg and there is nothing recorded to resolve; the static '
            'halves of this file still gate')
    _require_resolvable(tuple(tool for tool in recorded.split(',') if tool))


def test_a_recorded_tool_path_cannot_find_is_reported_as_a_failure(tmp):
    """The failure branch of the check above, reached on every run.

    The gate above only runs where the installer ran, so nothing else
    exercises what it does when a tool is missing. Driving it here is what
    makes that branch tested rather than merely written.
    """
    del tmp
    absent = 'daedalus-no-such-tool'
    try:
        _require_resolvable((absent,))
    except AssertionError as error:
        assert absent in str(error), error
    else:
        raise AssertionError(
            f'{absent!r} does not resolve on PATH and the check passed it; '
            'the check is the last thing standing between a broken install '
            'and a green suite job')


def test_the_installed_build_is_the_one_the_actionlint_job_pins(tmp):
    """The job's pin and the installer's are one build, and this says so.

    Both spell `ACTIONLINT_VERSION` in a different file, and a pin spelled
    twice drifts — the reason the installer reads shellcheck's version out
    of `requirements-test.txt` instead. Its own comment says why this one
    keeps two.

    What makes the drift SILENT is this suite's own subject. The
    workflow-lint suites read the JOB's pin, compare it against the
    installed binary, and SKIP on a mismatch: with the fork build on PATH
    and the job pin reverted to `1.7.12`, test_ci_workflows reports 31/33
    with exit 0, two of them skipping. "Two" is the two whose VERDICT the
    mismatch decides, not the two that reach the binary: three tests launch
    actionlint under a divergence, and one of the three
    (`test_a_lint_run_without_shellcheck_is_skipped`) runs a full lint and
    never reaches the version arm at all. A skip is a pass to every runner
    and every aggregate, so the disagreement is pinned here, where it is an
    assertion failure.
    """
    del tmp
    installer = _util.load(INSTALLER_SOURCE, 'lint_installer_pins')
    job = _tests_yml()
    pinned = re.findall(r'^\s*ACTIONLINT_VERSION:\s*(.*?)\s*(?:#.*)?$',
                        job, re.MULTILINE)
    assert len(pinned) == 1, pinned
    assert pinned[0].strip('\'"') == installer.ACTIONLINT_VERSION, (
        'the actionlint job pins '
        f'{pinned[0].strip(chr(39) + chr(34))} and the installer installs '
        f'{installer.ACTIONLINT_VERSION}; the workflow-lint suites SKIP when '
        'the two disagree, so a divergence here is a green run that linted '
        'nothing')
    # The URL is a SUBSTRING of the job's, not the other way round, so this
    # is one arm and not a disjunction: the job spells the release base and
    # appends `/v${ACTIONLINT_VERSION}/...`, so its own line contains the
    # installer's RELEASE. An `or <org> in job` beside it would be
    # satisfied by that same occurrence whichever way this points.
    #
    # The base the step NAMES, not the file and not merely the step's text:
    # a whole-file substring is satisfied by a comment quoting the old URL,
    # and a step-scoped one is satisfied too, because the run block is a
    # `>-` scalar that keeps its `#` lines. Both were planted, and both left
    # this green with the two bases genuinely diverged. What is compared is
    # the base on a non-comment line, and exactly one of them.
    #
    # ADMITTED SUBSET, and what it cannot see. This reads LINES, not shell.
    # A base ASSEMBLED FROM PARTS or split across a continuation is refused
    # rather than seen: neither puts the whole base on one line, so this
    # over-refuses a shape it might have accepted. A base consumed through a
    # variable the control does handle — the whole base on one line, read
    # once, is exactly what it looks for. What it cannot see is the residue
    # a plant confirmed: a step that names the base correctly on one line
    # and then fetches elsewhere through another variable. It is not a
    # silent pass in practice — the pinned sha256 fails on bytes that are
    # not the ones verified — so what this bounds is the JOB, not the
    # download. Red is the safe direction, and a line-based reading cannot
    # do better than this without a shell evaluator.
    downloaded = [line.strip() for line
                  in _actionlint_job_step('Install actionlint').splitlines()
                  if not line.lstrip().startswith('#')]
    bases = {line for line in downloaded if 'releases/download' in line}
    assert len(bases) == 1, (
        f'the install step names {len(bases)} release bases, and this '
        f'control reads one line rather than a shell: {sorted(bases)}')
    assert installer.RELEASE in bases.pop(), (
        f'the install step names a release base that is not '
        f'{installer.RELEASE!r}, so the checksum table the installer '
        'verifies belongs to a different release than the job downloads: a '
        'match on the version alone would install one build and lint with '
        'another')


def test_a_transient_transfer_failure_is_retried_and_the_next_attempt_serves(
        tmp):
    """The defect this retry exists for: one slow connect must not kill the
    install step — run 36907637031 died at the socket timeout, no test
    collected. The payload is what the SECOND attempt served, so a loop that
    kept the first failure is caught by the value as well as the count."""
    del tmp
    _, transfer, name, asset = _installer_and_transfer([
        urllib.error.URLError(TimeoutError('timed out')), b'the asset bytes'])
    with (mock.patch.object(asset.urllib.request, 'urlopen', transfer),
          mock.patch.object(asset.time, 'sleep', lambda seconds: None)):
        payload = asset.fetch(name)
    assert payload == b'the asset bytes', (
        f'fetch returned {payload!r} where the second attempt served '
        "b'the asset bytes'")
    assert len(transfer.calls) == 2, (
        f'the transfer failed transiently and was asked '
        f'{len(transfer.calls)} time(s); one ask fails the install step, '
        'which is the defect this retry is for')


def test_the_attempt_count_is_the_bound_and_the_last_failure_is_what_raises(
        tmp):
    """Every attempt failing raises the LAST failure, and asks no more than
    the count allows. Two properties because they are the two halves of one
    loop exit: the bound stops the asking, and the bare `raise` propagates
    the object the final attempt threw rather than a new one. The pause is
    recorded rather than stubbed out here because this is the one row that
    reaches the attempt with no retry left to wait for."""
    del tmp
    installer = _util.load(INSTALLER_SOURCE, 'lint_installer_retry')
    asset = _asset_module(installer)
    name = installer._asset_name()[0]
    failures = [urllib.error.URLError(TimeoutError(f'attempt {n}'))
                for n in range(1, asset.DOWNLOAD_ATTEMPTS + 1)]
    transfer = _Transfer(installer, name, failures)
    waits = []
    with (mock.patch.object(asset.urllib.request, 'urlopen', transfer),
          mock.patch.object(asset.time, 'sleep', waits.append)):
        raised = None
        try:
            asset.fetch(name)
        except BaseException as why:  # noqa: BLE001 - the exit is the subject
            raised = why
    assert isinstance(raised, urllib.error.URLError), (
        f'every attempt failed and what came out was '
        f'{type(raised).__name__}: {raised}')
    assert raised is failures[-1], (
        f'what came out was {raised!r} rather than the last failure '
        f'{failures[-1]!r}; the loop must propagate the object the final '
        'attempt threw, because the traceback naming fetch and urlopen is '
        'what makes a dead install step diagnosable')
    assert len(transfer.calls) == asset.DOWNLOAD_ATTEMPTS, (
        f'the transfer was asked {len(transfer.calls)} times and failed '
        f'every time, against DOWNLOAD_ATTEMPTS='
        f'{asset.DOWNLOAD_ATTEMPTS}')
    assert waits == [2, 4], (
        f'the recorded pauses were {waits} on a transfer that then failed '
        'for good; the attempt that raises is not followed by another, and '
        'waiting after it would put the longest pause on the path that has '
        'already given up')


def test_a_digest_mismatch_is_refused_on_the_transfer_it_fetched(tmp):
    """The pinned-sha256 guarantee, through `install_actionlint`.

    `_verify` runs on the bytes the transfer returned, so the refusal names
    that payload's digest and not some other. One call: a digest that does not
    verify is not a transfer that failed transiently, and asking again would
    be asking for the same wrong bytes.
    """
    del tmp
    payload = b'bytes that are not the pinned release asset'
    installer, transfer, _, asset = _installer_and_transfer([payload])
    with mock.patch.object(asset.urllib.request, 'urlopen', transfer):
        raised = None
        try:
            installer.install_actionlint()
        except SystemExit as why:
            raised = why
    assert raised is not None, (
        'installing an asset whose digest is not the pinned one installed it; '
        'the refusal is a SystemExit, which is what keeps it out of the '
        'retried set')
    assert len(transfer.calls) == 1, (
        f'a refused digest was asked for {len(transfer.calls)} times; it is '
        'a verdict on the bytes served, and asking again serves the same ones')
    digest = hashlib.sha256(payload).hexdigest()
    assert digest in str(raised.code), (
        f'the refusal names {raised.code!r} and not the digest of the payload '
        f'the transfer served ({digest}), so _verify did not run on those '
        'bytes')


def test_an_oversize_payload_is_refused_after_one_call(tmp):
    """The size bound is a verdict on what was served, not a failed
    transfer. The payload is one byte over MAX_TRANSFER, and the call count is
    asserted as well as the refusal: a loop that caught its own size refusal
    would reach the stand-in's refusal instead."""
    del tmp
    installer, transfer, _, asset = _installer_and_transfer(
        lambda i: [b'x' * (i.MAX_TRANSFER + 1)])
    with mock.patch.object(asset.urllib.request, 'urlopen', transfer):
        raised = None
        try:
            asset.fetch(installer._asset_name()[0])
        except SystemExit as why:
            raised = why
    assert raised is not None, (
        'a payload one byte over MAX_TRANSFER installed without a refusal')
    assert len(transfer.calls) == 1, (
        f'the oversize payload was asked for {len(transfer.calls)} times; a '
        'verdict on the bytes served is not a transfer worth asking again')
    assert str(installer.MAX_TRANSFER) in str(raised.code), (
        f'the refusal {raised.code!r} does not name the ceiling it enforced '
        f'({installer.MAX_TRANSFER})')


def test_a_4xx_is_asked_once_and_propagates(tmp):
    """A status below 500 is the server answering about this asset.

    A tag with no published asset 404s, and asked again it answers the same,
    so this is the half of the boundary where a second ask buys nothing.
    """
    del tmp
    _, transfer, name, asset = _installer_and_transfer(
        lambda i: [_http_error(i, 404, 'Not Found')])
    waits = []
    with (mock.patch.object(asset.urllib.request, 'urlopen', transfer),
          mock.patch.object(asset.time, 'sleep', waits.append)):
        raised = None
        try:
            asset.fetch(name)
        except urllib.error.HTTPError as why:
            raised = why
    assert raised is not None, (
        'a 404 was not raised as an HTTPError, so the status answer is not '
        'what came out of the transfer')
    assert raised.code == 404, raised.code
    assert len(transfer.calls) == 1, (
        f'a 404 was asked for {len(transfer.calls)} times; the asset is not '
        'there, and the second ask gets the same answer')
    assert waits == [], (
        f'the installer paused {waits} on a status it never retried; a '
        'verdict about the asset is not a failure worth waiting out, so the '
        'retry window belongs to the failing half of the boundary')


def test_a_5xx_is_asked_again_and_the_next_attempt_serves(tmp):
    """The other half: a 5xx is the server failing rather than answering.
    Separate from the 4xx control because a mutant that moves the boundary
    fails exactly one of them: widening it retries the 404, narrowing it
    refuses the 500. THE STATUS IS 500 AND NOT 503 — a 503 passes a `> 500`
    mutant too, so a control written on it does not pin the boundary the
    installer's comment states."""
    del tmp
    _, transfer, name, asset = _installer_and_transfer(
        lambda i: [_http_error(i, 500, 'Internal Server Error'),
                   b'the asset bytes'])
    with (mock.patch.object(asset.urllib.request, 'urlopen', transfer),
          mock.patch.object(asset.time, 'sleep', lambda seconds: None)):
        payload = asset.fetch(name)
    assert payload == b'the asset bytes', payload
    assert len(transfer.calls) == 2, (
        f'a 500 was asked for {len(transfer.calls)} times and not retried; a '
        'server failing is the transfer failing, which is what the retry is '
        'for')


def test_a_retry_waits_the_window_the_status_asked_for(tmp):
    """The gap between attempts, proved by the wait the installer chose.

    `time.sleep` is stood in for and recorded, so the subject is the value
    passed to it and never how long the machine took to give it back — a
    wall-clock bound passes on a fast runner and fails a loaded one.

    Four runs, because `DOWNLOAD_ATTEMPTS` gives each of them two gaps.

    A header can sit in three places relative to what the module would
    have waited on its own, and each needs its own sample — two gaps
    cannot carry three regions, and two samples once looked complete
    while a real one went missing. Under the growth, above it, and over
    the ceiling are three different answers, and a path that honoured a
    header only when it exceeded the ceiling passed two of the three.

    The first run carries a header on both failures and takes the outer
    two: 1 is under the 2 s the first attempt would have waited anyway,
    so the growth wins and it records 2, while 900 is over the 10 s
    ceiling, so the ceiling binds it and it records 10. Two entries and
    not three: the third attempt served, and a pause after the attempt
    that succeeded is waiting for nothing.

    The second run is the middle region, which is the issue's own defect:
    a header over the growth and under the ceiling is a server saying
    exactly how long to wait, and waiting less than it asked is asking
    again too soon. It records `[7, 7]`, and a path that dropped any
    header below the ceiling records `[2, 4]` here — the growth alone,
    the same pair the header-free run asserts, so that run cannot see
    this one and this run is its only holder.

    The third carries none, so the growth is recorded at the second
    attempt rather than losing to a header: in the first run the 900 takes
    that gap outright. It records `[2, 4]`, where a flat wait records
    `[2, 2]` and a shifted exponent `[4, 8]`.

    The fourth is the one header the module cannot read, and a parse that
    raised instead of falling back would take the retry with it.
    """
    del tmp
    installer, transfer, name, asset = _installer_and_transfer(
        lambda i: [
            _http_error(i, 503, 'Service Unavailable', retry_after=1),
            _http_error(i, 503, 'Service Unavailable',
                        retry_after=900),
            b'the asset bytes'])
    waits = []
    with (mock.patch.object(asset.urllib.request, 'urlopen', transfer),
          mock.patch.object(asset.time, 'sleep', waits.append)):
        payload = asset.fetch(name)
    assert payload == b'the asset bytes', payload
    assert waits == [2, 10], (
        f'the recorded pauses were {waits}, where the two failing statuses '
        'carried Retry-After 1 and 900 against growths of 2 and 4. The '
        'wait is the longer of growth and header, so 1 loses to 2 and 900 '
        'wins the ceiling at 10; nothing is recorded after the attempt that '
        'served.')
    mid = _Transfer(installer, name, [
        _http_error(installer, 503, 'Service Unavailable', retry_after=7),
        _http_error(installer, 503, 'Service Unavailable', retry_after=7),
        b'the asset bytes'])
    honoured = []
    with (mock.patch.object(asset.urllib.request, 'urlopen', mid),
          mock.patch.object(asset.time, 'sleep', honoured.append)):
        asset.fetch(name)
    assert honoured == [7, 7], (
        f'the pauses were {honoured} where both statuses carried '
        'Retry-After 7, a window over the growth and under the ceiling. '
        'That is a server saying how long to wait, and it is the whole '
        'defect this change is for: waiting less asks again too soon')
    plain = _Transfer(installer, name, [
        _http_error(installer, 503, 'Service Unavailable'),
        _http_error(installer, 503, 'Service Unavailable'),
        b'the asset bytes'])
    bare = []
    with (mock.patch.object(asset.urllib.request, 'urlopen', plain),
          mock.patch.object(asset.time, 'sleep', bare.append)):
        asset.fetch(name)
    assert bare == [2, 4], (
        f'the pauses with no Retry-After on either failure were {bare}; the '
        "wait is the module's own and it grows with the attempt, so a flat "
        'wait or a shifted exponent is visible here, where no header can '
        'hide it')
    dated = _Transfer(installer, name, [
        _http_error(installer, 503, 'Service Unavailable',
                    retry_after='Wed, 21 Oct 2015 07:28:00 GMT'),
        b'the asset bytes'])
    fell_back = []
    with (mock.patch.object(asset.urllib.request, 'urlopen', dated),
          mock.patch.object(asset.time, 'sleep', fell_back.append)):
        asset.fetch(name)
    assert fell_back == [2], (
        f'the pause was {fell_back} where the status carried an HTTP-date '
        'rather than delay-seconds; there is no clock here to compare a date '
        'against, so it falls back to the backoff instead of being read as a '
        'number nobody could have checked')


def main():
    return _util.runner(_util.collect(globals()), tmp_prefix='linttools_')


if __name__ == '__main__':
    raise SystemExit(main())
