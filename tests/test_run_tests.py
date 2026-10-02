#!/usr/bin/env python3
"""run_tests.py bounds each suite and names the one that overruns."""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_suite_fixture import (  # noqa: E402
    FORCED_WITHOUT_GRACE, FORCES_WITHOUT_ASKING, REQUESTED_THEN_GRACED,
    TREE_WAS_KILLED, coverage_group, coverage_tree, kill_recorded,
    records, settle_gone)
from _suite_bound_stubs import Clock, Removals, swapped  # noqa: E402

ROOT = _util.ROOT
SUITE_BOUND = _util.load(ROOT / 'scripts' / 'ci' / 'suite_bound.py',
                         'runner_suite_bound')
_OVERRUN_BOUND_S = 2
# How long a planted suite stays wedged when nothing ends it. Named
# because the outer bound below is read against it, and a control whose
# own limit is the longer of the two cannot see a launcher that simply
# waits: the suite and its grandchild end on their own and the control
# reads that as success.
_PLANTED_WEDGE_S = 60
# This leg's outer bound, derived as a FRACTION of what it has to outlast
# rather than typed: it must clear the launcher's SIGTERM grace window --
# so a suite that ignores the request still gets escalated inside it --
# and must stay well under the planted wedge, so a grace longer than this
# is a red here rather than a suite that finished on its own.
_RUNNER_OUTER_S = _PLANTED_WEDGE_S // 2
assert _RUNNER_OUTER_S > SUITE_BOUND.CLEANUP_TIMEOUT_S * 2, (
    f"the outer bound {_RUNNER_OUTER_S} s cannot outlast the launcher's "
    f"{SUITE_BOUND.CLEANUP_TIMEOUT_S} s grace window twice over, so a suite "
    f"that ignores the request would be reported before it is escalated")
# Reaping a killed tree is itself bounded, by a deadline read off the
# clock rather than asserted as a margin.
_WEDGE_SETTLE_S = 10

_PASSING_SUITE = (
    'import json, os\n'
    "summary = os.environ['DAEDALUS_TEST_SUMMARY']\n"
    "json.dump({'total': 1, 'passed': 1, 'skipped': 0, 'failed': 0,\n"
    "           'requires': None}, open(summary, 'w'))\n"
    "print('stub pass')\n"
)

_STALLING_SUITE = (
    'import time\nprint("stalling", flush=True)\n'
    'time.sleep({wedge})\n'
)

# A suite that answers SIGTERM and flushes, which is what
# `pyproject.toml`'s `sigterm = true` exists for and what a runner that
# kills its tree first would take away.
_STOPPABLE_SUITE = (
    'import json, os, signal, sys, time\n'
    "summary = os.environ['DAEDALUS_TEST_SUMMARY']\n"
    'def _stopped(signum, frame):\n'
    '    del signum, frame\n'
    "    json.dump({'total': 1, 'passed': 1, 'skipped': 0, 'failed': 0,\n"
    "               'requires': None}, open(summary, 'w'))\n"
    "    print('suite was asked to stop and flushed', flush=True)\n"
    '    sys.exit(0)\n'
    'signal.signal(signal.SIGTERM, _stopped)\n'
    'print("stalling", flush=True)\n'
    'time.sleep({wedge})\n'
)

# A suite that ignores SIGTERM and leaves a child of its own that does the
# same, so only the escalation reaches either of them.
_STUBBORN_SUITE = (
    'import signal, subprocess, sys, time\n'
    'from pathlib import Path\n'
    'root = Path(__file__).resolve().parent\n'
    'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
    "child = subprocess.Popen(\n"
    "    [sys.executable, '-c', 'import signal, time; "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "time.sleep({wedge})'],\n"
    '    stdin=subprocess.DEVNULL)\n'
    "(root / 'grandchild.pid').write_text(str(child.pid),"
    " encoding='ascii')\n"
    'print("stalling", flush=True)\n'
    'time.sleep({wedge})\n'
)

# Models a bystander a loaded runner could not start in time: the sleep
# exceeds any bound here, so it is killed on any machine, and its output
# carries a FAILED: line the pin must not read. The decoy is not asserted;
# the parse is pinned by test_a_suites_own_failed_line_is_not_the_aggregate.
_SLOW_PASSING_SUITE = (
    'import json, os, time\n'
    "print('FAILED: my own subtest', flush=True)\n"
    'time.sleep(4)\n'
    "summary = os.environ['DAEDALUS_TEST_SUMMARY']\n"
    "json.dump({'total': 1, 'passed': 1, 'skipped': 0, 'failed': 0,\n"
    "           'requires': None}, open(summary, 'w'))\n"
    "print('stub pass')\n"
)


# The Windows removal refusal has no POSIX spelling: unlinking a file
# another process still holds succeeds there. The controls below plant it
# at `shutil.rmtree`, the one boundary where it happens, and read the log
# rather than a clock -- a timing margin passes on a fast machine.
_RMTREE_LOG = 'rmtree-calls.log'


def _rmtree_sitecustomize(always):
    """The `sitecustomize.py` that stands in for WinError 32.

    Every call is logged before it is answered; one that does not refuse
    is delegated to the real `shutil.rmtree`.
    """
    refusal = 'True' if always else 'str(path) not in _REFUSED'
    return (
        'import shutil\n'
        'from pathlib import Path\n'
        '\n'
        f'_LOG = Path(__file__).resolve().with_name({_RMTREE_LOG!r})\n'
        '_REAL = shutil.rmtree\n'
        '_REFUSED = set()\n'
        '\n'
        '\n'
        'def rmtree(path, *args, **kwargs):\n'
        f'    refused = {refusal}\n'
        '    _REFUSED.add(str(path))\n'
        '    with _LOG.open("a", encoding="utf-8") as log:\n'
        '        log.write(str(path) + "\\t" + '
        '("raised" if refused else "delegated") + "\\n")\n'
        '    if refused:\n'
        "        raise PermissionError(32, 'in use by another process',\n"
        '                             str(path))\n'
        '    return _REAL(path, *args, **kwargs)\n'
        '\n'
        '\n'
        'shutil.rmtree = rmtree\n'
    )


def _rmtree_calls(root):
    """`(path, outcome)` for every call the planted refusal saw."""
    log = root / _RMTREE_LOG
    if not log.exists():
        return []
    lines = log.read_text(encoding='utf-8').splitlines()
    return [tuple(line.split('\t')) for line in lines if line]


def _sandbox(tmp, suites, suite_bound=None, refuse_rmtree=None):
    """A copy of the runner over fabricated suites, with what it imports.

    `suite_bound` rewrites the one definition the copied runner resolves
    when it launches, and `refuse_rmtree` writes the removal refusal into
    the same file, which is why the two patches are composed.
    """
    root = Path(tmp) / 'tree'
    (root / 'tests').mkdir(parents=True)
    (root / 'scripts' / 'ci').mkdir(parents=True)
    shutil.copy(ROOT / 'run_tests.py', root / 'run_tests.py')
    shutil.copy(ROOT / 'scripts' / 'ci' / 'suite_bound.py',
                root / 'scripts' / 'ci' / 'suite_bound.py')
    patch = []
    if suite_bound is not None:
        patch.append(
            'import scripts.ci.suite_bound as _bound\n'
            f'_bound.DEFAULT_SUITE_TIMEOUT_S = {suite_bound!r}\n')
    if refuse_rmtree is not None:
        patch.append(_rmtree_sitecustomize(always=refuse_rmtree))
    if patch:
        (root / 'sitecustomize.py').write_text(''.join(patch),
                                               encoding='utf-8')
    for name, source in suites.items():
        # A placeholder, so the planted wedge cannot drift from the bound.
        (root / 'tests' / name).write_text(
            source.replace('{wedge}', str(_PLANTED_WEDGE_S)),
            encoding='utf-8')
    return root


def _run_sandbox(root, timeout_env, outer_timeout=120, temp_root=None):
    env = dict(os.environ, **timeout_env)
    if temp_root is not None:
        # The runner's `mkdtemp` resolves under this, so what a planted
        # refusal leaves behind lands where the calling fixture reclaims it.
        temp = Path(temp_root)
        temp.mkdir(parents=True, exist_ok=True)
        env.update(TMPDIR=str(temp), TEMP=str(temp), TMP=str(temp))
    if (root / 'sitecustomize.py').exists():
        # `site` looks for a sitecustomize on the path the interpreter has
        # built BEFORE it imports one, and a script's own directory is put
        # on the path after that. Without the tree on PYTHONPATH the patch
        # is written, never read, and the control would measure the default.
        inherited = env.get('PYTHONPATH')
        env['PYTHONPATH'] = os.pathsep.join(
            [str(root)] + ([inherited] if inherited else []))
    # A generated `sitecustomize.py` is scaffolding, not repository source,
    # and the coverage configuration measures the synthetic tree and then
    # attributes what it measured to repository paths -- so a child that
    # imports one hands `coverage report` a measured file with no source,
    # and the gate refuses it. Scrubbing the collector from exactly these
    # children is what stops the RECORDING, on every platform, rather than
    # excluding a path shape that only reads differently per platform. The
    # cost is that these controls stop contributing to the total; the
    # lines they drive are measured by every other control in this file.
    # See `tests/_coverage_suite_fixture.py::coverage_tree` for why these
    # two launches are separate rather than one launch with a chosen mode:
    # the guard reads the mode as a LITERAL at the `env=` keyword, and a
    # name bound to a `child_coverage(mode, ...)` is `invalid` — which also
    # deletes the keep site this function is allowlisted for.
    if (root / 'sitecustomize.py').exists():
        return subprocess.run(
            [sys.executable, str(root / 'run_tests.py')], cwd=str(root),
            env=_util.child_coverage('scrub', env, cwd=root),
            capture_output=True, text=True, timeout=outer_timeout)
    return subprocess.run(
        [sys.executable, str(root / 'run_tests.py')], cwd=str(root),
        env=_util.child_coverage('keep', env, cwd=root),
        capture_output=True, text=True, timeout=outer_timeout)


def _timeout_record(bound):
    """The record the runner writes for a suite it stopped at `bound`."""
    return f'SUITE TIMED OUT after {float(bound)} s (returncode '


def _suite_block(stdout, name):
    """The block the runner printed for `name`, or '' when it printed none."""
    _, _, rest = stdout.partition(f'=== {name} ===\n')
    return rest.split('\n=== ', 1)[0]


def _reads_timeout(node):
    """Whether `node` reads the `timeout` parameter, bare or as an operand."""
    if isinstance(node, ast.Name):
        return node.id == 'timeout'
    if isinstance(node, ast.BinOp):
        return _reads_timeout(node.left) or _reads_timeout(node.right)
    return False


def _wait_timeout_keyword(source):
    """The waits in _terminate_and_reap pass the constant 10, so they are not
    the anchor."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == 'wait'
                and isinstance(func.value, ast.Name)
                and func.value.id == 'process'):
            continue
        for keyword in node.keywords:
            if keyword.arg == 'timeout' and _reads_timeout(keyword.value):
                return keyword
    return None


def _scale_suite_wait(source, factor):
    """The anchor is located by ast so an ordinary edit cannot retire it; a
    miss blames the anchor, not the guarded behaviour."""
    keyword = _wait_timeout_keyword(source)
    assert keyword is not None, (
        'no process.wait bound to the timeout parameter: the MUTATION ANCHOR '
        'changed shape, so this control proves nothing until it is derived '
        'again from the runner source')
    lines = source.splitlines(keepends=True)
    node = keyword.value
    row = node.lineno - 1
    raw = lines[row].encode()
    # The splice assumes a one-line value; a multi-line value would corrupt it.
    lines[row] = (raw[:node.col_offset] + f'timeout * {factor}'.encode()
                  + raw[node.end_col_offset:]).decode()
    return ''.join(lines)


def _failed_suites(stdout):
    """The suites the aggregate named as failed, or [] when it named none.

    The aggregate is the runner's last non-empty line; a suite's own
    output arrives earlier, so the scan starts at the end."""
    lines = stdout.splitlines()
    aggregate = next((line for line in reversed(lines) if line.strip()), '')
    if not aggregate.startswith('FAILED: '):
        return []
    return [name.strip()
            for name in aggregate[8:].split(',') if name.strip()]


def test_a_suites_own_failed_line_is_not_the_aggregate(tmp):
    decoy = '=== test_passer.py ===\nFAILED: my own subtest\n\n'
    assert _failed_suites(decoy + 'FAILED: test_staller.py\n') == [
        'test_staller.py']
    assert _failed_suites(decoy + 'FAILED: \n') == []
    assert _failed_suites(decoy + 'OVERALL: PASS (2 suites)\n') == []


def test_an_overrunning_suite_is_named_and_the_run_reports_it(tmp):
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE,
                          'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(
        root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)})
    assert result.returncode == 1, (result.returncode, result.stdout)
    # The runner's own record, in the stalled suite's own block, matched
    # whole: the block is sliced to EXCLUDE the `=== name ===` header, so
    # the suite name in the assertion below can only have come from the
    # record itself.
    staller_block = _suite_block(result.stdout, 'test_staller.py')
    found = records(staller_block)
    assert len(found) == 1, (len(found), staller_block)
    record = found[0].groupdict()
    assert record['name'] == 'test_staller.py', record
    assert record['bound'] == str(float(_OVERRUN_BOUND_S)), record
    assert 'test_staller.py' in _failed_suites(result.stdout), (
        result.stdout, result.stderr)
    assert '=== test_passer.py ===' in result.stdout, result.stdout


def test_a_runner_wedged_suite_states_the_kill_its_platform_took(tmp):
    """Which contract the runner gives on THIS platform, asserted as such.

    POSIX: the request goes to the whole group, a bounded grace follows,
    and a suite that answers it flushes and reports what it did.
    `pyproject.toml` sets `sigterm = true` for that, and before the tree
    kill the runner's `terminate()` is what gave it the chance.

    Windows: one `taskkill /F /T`, a forced termination. Nothing is
    asked, nothing is given a grace, and the suite cannot flush. That is
    the pre-branch Windows behaviour -- `_terminate_and_reap` called
    `process.terminate()`, which is `TerminateProcess` there -- and this
    branch did not change it.

    Each half refuses the other platform's clause, so a route that
    silently changed platform is red rather than green for a reason no
    reader could act on.
    """
    root = _sandbox(tmp, {'test_stoppable.py': _STOPPABLE_SUITE})
    result = _run_sandbox(
        root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)},
        outer_timeout=_RUNNER_OUTER_S)
    block = _suite_block(result.stdout, 'test_stoppable.py')
    found = records(block)
    assert len(found) == 1, (len(found), block)
    record = found[0].groupdict()
    # The route, as the coverage twin asserts it: the cleanup clause says
    # what the route DID, and this says which route it was. Either alone
    # is satisfiable by a record that got the other half wrong.
    assert TREE_WAS_KILLED in record['cleanup'], record
    if FORCES_WITHOUT_ASKING:
        assert 'suite was asked to stop and flushed' not in block, block
        assert FORCED_WITHOUT_GRACE in record['cleanup'], record
        assert REQUESTED_THEN_GRACED not in record['cleanup'], record
        assert int(record['returncode']) != 0, record
        return
    assert 'suite was asked to stop and flushed' in block, block
    assert int(record['returncode']) == 0, record
    # The cleanup has to say the suite TOOK THE REQUEST. The route alone
    # is not enough: the record a suite that ignored the request and was
    # killed produces names the same group and the same escalation.
    assert REQUESTED_THEN_GRACED in record['cleanup'], record
    assert FORCED_WITHOUT_GRACE not in record['cleanup'], record


def test_a_flushed_suite_is_a_pass_only_where_a_flush_can_happen(tmp):
    """A DECISION, pinned deliberately: a flushed wedge counts as a pass.

    `run_tests.py` decides a suite by its returncode and its summary
    (or: `returncode != 0 or summary is None`), so a suite that exceeded
    its bound, answered the request and reported its counts is counted as
    what it says it did. That is pre-branch behaviour -- `git show
    145ced27:run_tests.py` reaches the same state through
    `_terminate_and_reap` -- and this control exists to make it a
    SPECIFIED requirement rather than an accident, so a future change that
    closes it has to argue with this line.

    The other half is the platform. A flush needs a request to answer, and
    the Windows route sends none, so there a flushed wedge cannot exist
    and the suite is reported as what it actually did: failed, with no
    summary. Asserting the POSIX outcome there would be a red leg that
    teaches its reader nothing, which is what CI reported on 2026-09-29
    across all five `windows-latest` cells.

    The two launchers also disagree about a flushed wedge, which is the
    rest of why this is pinned rather than left implicit.
    `coverage_suites.py` counts any timed-out suite as failed whatever it
    reported.

    The pull request states the disagreement in its `## Changes` section,
    where the two per-launcher facts live, and the seat's task report
    carries the argument for closing it and the reason for not closing it
    here — a change to the `suites` job's gate, which this issue did not
    ask for and which this seat cannot run to measure.
    """
    root = _sandbox(tmp, {'test_stoppable.py': _STOPPABLE_SUITE})
    result = _run_sandbox(
        root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)},
        outer_timeout=_RUNNER_OUTER_S)
    assert 'SUITE TIMED OUT' in result.stdout, result.stdout
    if FORCES_WITHOUT_ASKING:
        assert result.returncode == 1, (result.returncode, result.stdout,
                                        result.stderr)
        assert 'test_stoppable.py' in _failed_suites(result.stdout), (
            result.stdout)
        assert 'OVERALL: PASS' not in result.stdout, result.stdout
        return
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)
    assert 'OVERALL: PASS' in result.stdout, result.stdout


def test_a_runner_timed_out_suites_own_child_does_not_survive_it(tmp):
    """The `suites` leg's launcher has to reach the tree as well.

    The coverage launcher's tree kill is proved by a grandchild that
    outlives its suite; without the same control here, dropping the group
    kill from the runner's timeout path leaves every suite green.
    """
    if sys.platform == 'win32':
        _util.skip('the liveness probe is POSIX; see pid_alive')
    root = _sandbox(tmp, {'test_stubborn.py': _STUBBORN_SUITE})
    recorded = root / 'tests' / 'grandchild.pid'
    try:
        result = _run_sandbox(
            root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)},
            outer_timeout=_RUNNER_OUTER_S)
        assert result.returncode == 1, (result.returncode, result.stdout,
                                        result.stderr)
        pid = int(recorded.read_text(encoding='ascii'))
        block = _suite_block(result.stdout, 'test_stubborn.py')
        found = records(block)
        assert len(found) == 1, (len(found), block)
        record = found[0].groupdict()
        assert settle_gone(pid, _WEDGE_SETTLE_S), (
            f'pid {pid} outlived the bound the runner enforced. It ignores '
            f'SIGTERM, so only the escalation reaches it, and it did not; '
            f'the record says: {found[0].group()}')
        # The other direction of the same claim: this suite DID ignore the
        # request, so a record claiming it took one is false -- and the
        # pair of controls is what pins the clause in both directions.
        assert 'ignored the request' in record['cleanup'], record
    finally:
        kill_recorded(recorded)


def test_the_staller_is_named_when_the_bystander_misses_the_bound_too(tmp):
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE,
                          'test_passer.py': _SLOW_PASSING_SUITE})
    result = _run_sandbox(
        root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)})
    assert result.returncode == 1, (result.returncode, result.stdout)
    staller_block = _suite_block(result.stdout, 'test_staller.py')
    assert _timeout_record(_OVERRUN_BOUND_S) in staller_block, result.stdout
    assert 'test_staller.py' in _failed_suites(result.stdout), (
        result.stdout, result.stderr)
    assert '=== test_passer.py ===' in result.stdout, result.stdout


def test_the_timeout_record_names_the_bound_the_wait_was_given(tmp):
    factor = 0.5
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE})
    runner = root / 'run_tests.py'
    mutated = _scale_suite_wait(
        runner.read_bytes().decode(), factor)
    applied = _wait_timeout_keyword(mutated)
    assert applied is not None, (
        'the rewritten runner has no process.wait bound to the timeout '
        'parameter: the MUTATION ANCHOR changed shape')
    expected = ast.parse(f'timeout * {factor}', mode='eval').body
    assert ast.dump(applied.value) == ast.dump(expected), (
        'the MUTATION did not apply: the suite wait bound is '
        f'{ast.dump(applied.value)}, not timeout * {factor}')
    runner.write_bytes(mutated.encode('utf-8'))
    result = _run_sandbox(
        root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)})
    assert result.returncode == 1, (result.returncode, result.stdout,
                                    result.stderr)
    staller_block = _suite_block(result.stdout, 'test_staller.py')
    assert 'SUITE TIMED OUT' in staller_block, (result.stdout,
                                                result.stderr)
    assert _timeout_record(_OVERRUN_BOUND_S * factor) in staller_block, (
        result.stdout)
    assert _timeout_record(_OVERRUN_BOUND_S) not in staller_block, (
        result.stdout)


def test_a_passing_suites_own_output_reaches_stdout(tmp):
    # A bound no trivial child approaches: this cannot turn on speed.
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': '60'})
    assert result.returncode == 0, (result.returncode, result.stdout)
    assert '=== test_passer.py ===' in result.stdout, result.stdout
    assert 'stub pass' in result.stdout, result.stdout


def test_a_suite_within_its_budget_still_passes(tmp):
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': '60'})
    assert result.returncode == 0, (result.returncode, result.stdout)
    assert 'OVERALL: PASS' in result.stdout, result.stdout


def test_an_invalid_timeout_stops_startup_naming_the_setting(tmp):
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    for value in ('soon', 'inf', 'INF', '1e400', 'nan', '0', '-1'):
        result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': value})
        assert result.returncode != 0, (value, result.returncode,
                                        result.stdout)
        assert 'DAEDALUS_SUITE_TIMEOUT' in result.stdout + result.stderr, (
            value)


# The reader's whole accept/reject surface, as this repository's corpus
# entry `guards/2026-09-02-probe-a-numeric-readers-surface-with-inf-and-nan`
# was filed against: `float(raw)` plus a `<= 0` test admits `inf`, and a
# bound of infinity never expires, so the setting meant to bound each suite
# reinstates the unbounded wait. `test_an_invalid_timeout_stops_startup_
# naming_the_setting` above already walks `soon, inf, INF, 1e400, nan, 0,
# -1` and asserts each is refused. What it does not say is WHICH message
# each refusal gives, and it carries neither `-inf` nor the empty string --
# a reader that MOVED is a reader that can arrive half converted, and a
# refusal that names a different cause is a second answer to one question.
_UNUSABLE_BOUNDS = (
    ('inf', 'finite positive'),
    ('-inf', 'finite positive'),
    ('INF', 'finite positive'),
    ('1e400', 'finite positive'),
    ('nan', 'finite positive'),
    ('0', 'finite positive'),
    ('-1', 'finite positive'),
    ('soon', 'not a number'),
    ('', 'not a number'),
)


def test_a_bound_that_would_not_bound_is_refused_by_name(tmp):
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    for value, expected in _UNUSABLE_BOUNDS:
        result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': value})
        reported = result.stdout + result.stderr
        assert result.returncode != 0, (value, result.returncode, reported)
        assert 'DAEDALUS_SUITE_TIMEOUT' in reported, (value, reported)
        assert expected in reported, (value, reported)


def test_a_finite_positive_bound_is_accepted(tmp):
    root = _sandbox(tmp, {'test_passer.py': _PASSING_SUITE})
    result = _run_sandbox(root, {'DAEDALUS_SUITE_TIMEOUT': '2.5'})
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)


_SHARED_BOUND_S = 3

_SHARED_WEDGED_SUITE = """import time
print('wedged suite reached its own body', flush=True)
time.sleep(120)
"""


def test_both_launchers_read_the_one_definition_of_the_bound(tmp):
    """One bound, two launchers, each naming the value the other must see.

    The value is set on the shared module in each synthetic tree, so a
    launcher that resolved its bound at `def` time, or carried a second
    copy of the number, would report the default instead of this one.
    """
    runner_root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE},
                           suite_bound=_SHARED_BOUND_S)
    runner = _run_sandbox(runner_root, {})
    record = f'SUITE TIMED OUT after {float(_SHARED_BOUND_S)} s (returncode '
    assert record in _suite_block(runner.stdout, 'test_staller.py'), (
        runner.stdout, runner.stderr)
    coverage, _invocations = coverage_tree(
        tmp, {'test_wedged.py': _SHARED_WEDGED_SUITE},
        suite_bound=_SHARED_BOUND_S, outer_timeout=_SHARED_BOUND_S * 20)
    assert record in coverage_group(coverage.stdout, 'test_wedged.py'), (
        coverage.stdout, coverage.stderr)


def test_a_summaries_refused_once_is_retried_and_the_verdict_stands(tmp):
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE},
                    refuse_rmtree=False)
    result = _run_sandbox(
        root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)})
    calls = _rmtree_calls(root)
    refused = [path for path, outcome in calls if outcome == 'raised']
    assert len(refused) == 1, calls
    # One refusal and one delegation on the same path, read off the log.
    assert [path for path, outcome in calls if outcome == 'delegated'] == \
        refused, calls
    assert not os.path.exists(refused[0]), refused
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert 'test_staller.py' in _failed_suites(result.stdout), (
        result.stdout, result.stderr)
    assert 'left in place' not in result.stderr, result.stderr


# The refusal the above control does NOT clear, so this one spends the
# whole shared cleanup bound before it can report.
_UNREMOVABLE_OUTER_S = _RUNNER_OUTER_S


def test_a_summaries_held_past_the_bound_is_reported_and_verdict_stands(tmp):
    root = _sandbox(tmp, {'test_staller.py': _STALLING_SUITE},
                    refuse_rmtree=True)
    # The refusal is total, so the directory this run cannot remove
    # outlives it -- which is the whole point of the control. Its own temp
    # is aimed below so the survivor lands where this fixture reclaims it,
    # and where the report names a path this test already knows.
    outputs = Path(tmp) / 'outputs'
    result = _run_sandbox(
        root, {'DAEDALUS_SUITE_TIMEOUT': str(_OVERRUN_BOUND_S)},
        outer_timeout=_UNREMOVABLE_OUTER_S, temp_root=outputs)
    # The aggregate FIRST: this is where the base tracebacked out of
    # `main()` instead, taking the run's verdict with it.
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert 'test_staller.py' in _failed_suites(result.stdout), (
        result.stdout, result.stderr)
    assert 'OVERALL: PASS' not in result.stdout, result.stdout
    calls = _rmtree_calls(root)
    # Every call refused, and more than one: the bound was spent retrying.
    assert len(calls) > 1, calls
    assert {outcome for _path, outcome in calls} == {'raised'}, calls
    directory = calls[0][0]
    assert {path for path, _outcome in calls} == {directory}, calls
    assert os.path.isdir(directory), directory
    assert Path(directory).parent == outputs, (
        f'the directory that would not go landed in {Path(directory).parent}, '
        f'which this fixture does not reclaim; it wanted {outputs}')
    # The OS error's message is the platform's prose, so the report is
    # pinned on what this module promised to name: where, and what is left.
    assert directory in result.stderr, result.stderr
    assert 'test_staller.output' in result.stderr, result.stderr


def test_a_cleanup_that_raised_something_else_is_not_retried(tmp):
    """A bug inside the cleanup is not a removal that could not finish.

    `discard_outputs` never raises FOR FAILING TO, and a programming
    error is not failing to: a catch widened to `BaseException` would
    retry one until the bound and report it as the reason a directory
    would not go. So the exception has to arrive, on the first attempt.
    """
    directory = Path(tmp) / 'outputs'
    directory.mkdir()
    clock = Clock()
    removals = Removals(refuse=1, error=ValueError('a bug in the removal'))
    escaped = None
    with swapped(SUITE_BOUND, shutil=removals, time=clock):
        try:
            SUITE_BOUND.discard_outputs(str(directory))
        except ValueError as raised:
            escaped = raised
    assert escaped is removals.error, (
        'the cleanup swallowed a non-OSError and carried on; the calls it '
        f'made were {removals.calls}')
    assert removals.calls == [str(directory)], (
        f'the removal was retried instead of surfacing: {removals.calls}')
    assert clock.slept == 0, (
        f'it waited {clock.slept} s for a bug to stop happening')


# The interval the retry waits between removals, written out rather than
# read from the module: an expectation read back from the subject moves with
# it, so a `GRACE_POLL_S` of zero satisfies both sides of the comparison.
_GRACE_POLL_INTERVAL_S = 0.05


def test_the_cleanup_waits_between_attempts_rather_than_hammering(tmp):
    """The poll interval is the floor between two removals, not a no-op.

    On the platform where the refusal is real, a tight loop would call
    the removal for the whole bound instead of a few hundred times. What
    is asserted is how long the code chose to wait, off a `Clock` that
    advances only when it sleeps -- so this cannot pass on a fast box.
    """
    directory = Path(tmp) / 'outputs'
    directory.mkdir()
    clock = Clock()
    removals = Removals(refuse=2)
    with swapped(SUITE_BOUND, shutil=removals, time=clock):
        SUITE_BOUND.discard_outputs(str(directory))
    assert removals.calls == [str(directory)] * 3, removals.calls
    assert clock.slept == 2 * _GRACE_POLL_INTERVAL_S, (
        f'two refusals were answered by {clock.slept} s of waiting; the '
        f'interval is {_GRACE_POLL_INTERVAL_S} s')
    assert not os.path.exists(directory), (
        'the retry reported success it never had; the directory is still '
        'there')


if __name__ == '__main__':
    sys.exit(_util.runner(_util.collect(dict(locals()))))
