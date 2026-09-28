#!/usr/bin/env python3
"""The launcher's own detector and the cleanup that ends the child.

Two things the census suite deliberately does not hold, because they are
about `tests/_noderun.py` and `tests/_processtree.py` rather than about
how a number may reach a child: the error the detector raises, which is the
whole value of its class, and the property that the kill-and-report
sequence is ONE module rather than a pair of callers that happen to agree.

Every occurrence of the error class outside the launcher used to be a string
plant, so nothing asserted its message: truncating the child's output or
replacing the cleanup report both left every suite green. These are the two
controls that close that.
"""
import ast
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent

# --- the outer alarm, which is not the subject's own defence ----------------
#
# A control that provokes an expiry sets its budget inside
# `tests/_noderun.py` — which is precisely the code a reversion removes. So
# against a launch reverted to an UNBOUNDED one, the control's only defence
# is the machinery it is testing, and it hangs until something external
# kills it: a silent 150 s rather than a named failure. This alarm is armed
# by the control, on the call, and raises whatever the subject does.
#
# It must clear the healthy path with room to spare and still fire well
# inside any external bound. The healthy budget is
# `round(CHILD_DEADLINE_S * 0.1)` = 11s, and the slowest observed correct
# run of the whole arm — a file timed at about 120s — is well under a
# minute; 57s is roughly five times the healthy budget, and turning a
# wedge into a named failure costs a minute where the alternative costs
# whatever the runner's ceiling costs.
OUTER_ALARM_SAMPLES = (52.0, 55.0, 57.0)
OUTER_ALARM_SLOWEST_S = max(OUTER_ALARM_SAMPLES)
OUTER_ALARM_S = round(OUTER_ALARM_SLOWEST_S)
# Cancelling a timer IS a zero-second deadline, and the census requires the
# figure to be composed from a named chain rather than typed at the call.
OUTER_ALARM_CLEAR_S = round(OUTER_ALARM_S * 0)


def _raise_outer_deadline(_signum, _frame):
    """What the outer alarm raises. `TimeoutError` so it is a named expiry."""
    raise TimeoutError('the outer alarm on this control fired')


# --- the expiry message, which is the whole value of the class ------------

def test_the_expiry_message_names_the_child_the_deadline_and_the_evidence(
        tmp):
    """A real `ChildDeadlineExceeded`, built the way the launcher builds it.

    Every occurrence of this class outside the launcher was a string plant,
    so nothing asserted the message: truncating the child's output or
    replacing the cleanup report both left every suite green. This drives
    the real launcher with a child that cannot finish, and reads the four
    lines a maintainer would have on the `windows-latest` leg this branch
    exists because of.
    """
    import _noderun  # noqa: E402

    class Stuck:
        """A child that is already past the detector and never exits."""

        pid = 4242

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(
                ['node', 'x'], timeout or 0.0)

        def kill(self):
            pass

    captured = {}

    def fake_popen(argv, **kwargs):
        captured['stdout'] = kwargs['stdout']
        captured['stderr'] = kwargs['stderr']
        kwargs['stdout'].write(b'partial child output')
        kwargs['stderr'].write(b'partial child diagnostics')
        return Stuck()

    real_popen = _noderun.subprocess.Popen
    real_deadline = _noderun.CHILD_DEADLINE_S
    real_cleanup = _noderun.cleanup_process_tree
    _noderun.subprocess.Popen = fake_popen
    _noderun.CHILD_DEADLINE_S = 1
    _noderun.cleanup_process_tree = lambda process, bound: 'simulated cleanup'
    caught = None
    try:
        _noderun.run_node_program('node', 'while (true) {}', [], tmp)
    except _noderun.ChildDeadlineExceeded as failure:
        caught = failure
    finally:
        _noderun.subprocess.Popen = real_popen
        _noderun.CHILD_DEADLINE_S = real_deadline
        _noderun.cleanup_process_tree = real_cleanup
    assert caught is not None, 'the stuck child did not raise'
    failure = caught
    assert failure.deadline_s == 1, failure.deadline_s
    assert failure.cleanup_diagnostic == 'simulated cleanup', failure
    message = str(failure)
    for line in ('node', 'deadline: 1s', 'cleanup: simulated cleanup',
                 "stdout: 'partial child output'",
                 "stderr: 'partial child diagnostics'"):
        assert line in message, (line, message)
    assert 'the suite ceiling is a weaker backstop' in message, message


# --- the scope of the kill, one control rather than a list ----------------

def test_exactly_one_module_under_tests_ends_a_child(tmp):
    """§4.8: no other module kills a child, and this is how that is pinned.

    A control naming two callers and four function names is satisfied by a
    decoy import and by a third module with its own copy. The property is
    one file, so the control is one file: each of the four markers a kill
    needs appears in exactly one tracked module under `tests/`, and that
    module is `tests/_processtree.py`.
    """
    del tmp
    # The INVOCATION, not the bare word: `taskkill` is named in a
    # docstring and in the control that reads the diagnostic, and a
    # marker a module mentions in prose is not a marker it uses.
    # The SEQUENCE, not any kill: `_dashnode.py`, `_drain.py` and
    # `_realbrowser_workers.py` all kill processes, and they are other
    # harnesses with their own children. What must be one module is the
    # kill-AND-report sequence, so the markers are its parts.
    markers = ('os.killpg', 'os.getpgid', "'/T', '/PID'",
               'def cleanup_process_tree(')
    for marker in markers:
        # This file names the markers, so it is the one module the scan
        # cannot include: a control that contains what it looks for would
        # satisfy itself.
        found = sorted(
            path.name for path in TESTS.glob('*.py')
            if path.name != Path(__file__).name
            and marker in path.read_text(encoding='utf-8'))
        assert found == ['_processtree.py'], (marker, found)


class StuckAgain:
    """A child already past the detector, for the unlink-door control."""

    pid = 4343

    def wait(self, timeout=None):
        """Time out however long it is given."""
        raise subprocess.TimeoutExpired(['node', 'x'], timeout or 1)

    def kill(self):
        """Nothing to kill in this double."""


def test_a_failed_scratch_removal_does_not_replace_the_classified_error(tmp):
    """A removal that raises is recorded, not raised over the report.

    The unlink door: on Windows a surviving grandchild still holding the
    inherited stdout/stderr write handle makes the scratch removal fail, and
    a plain `TemporaryDirectory` lets that `PermissionError` out in place of
    `ChildDeadlineExceeded`. The whole value of the class is the report — the
    child, the deadline, its output and the cleanup's own outcome — so a fix
    that let the right TYPE out while dropping the report would satisfy a
    type assertion and still deliver the bare errno.

    Deterministic and cross-platform: the removal is the thing that fails,
    not the platform. `_remove_tree` is patched to raise at exactly that
    point and the real launcher path runs otherwise.
    """
    import _noderun  # noqa: E402

    real_remove = _noderun._remove_tree
    real_popen = _noderun.subprocess.Popen
    real_deadline = _noderun.CHILD_DEADLINE_S
    real_cleanup = _noderun.cleanup_process_tree
    _noderun._remove_tree = _raise_permission_error
    _noderun.subprocess.Popen = _stuck_popen
    _noderun.cleanup_process_tree = lambda process, bound: 'simulated cleanup'
    _noderun.CHILD_DEADLINE_S = 1
    caught = None
    try:
        _noderun.run_node_program(
            shutil.which('node'), 'while (true) {}', [], tmp)
    except BaseException as failure:  # noqa: BLE001
        caught = failure
    finally:
        _noderun._remove_tree = real_remove
        _noderun.subprocess.Popen = real_popen
        _noderun.cleanup_process_tree = real_cleanup
        _noderun.CHILD_DEADLINE_S = real_deadline
    assert isinstance(caught, _noderun.ChildDeadlineExceeded), (
        f'the removal replaced the classified error with '
        f'{type(caught).__name__ if caught else "nothing"}: {caught}')
    message = str(caught)
    for line in ('deadline: 1s', "stdout: 'out'", "stderr: 'err'",
                 'simulated cleanup'):
        assert line in message, (line, message)
    # and the removal's own outcome is IN the report, which is the half a
    # type assertion alone would not have caught.
    assert 'was not fully removed' in message, message
    assert 'PermissionError' in message, message


# --- the exact-argv entry point -------------------------------------------

def _node():
    """The node a real child runs under, or a named failure."""
    node = shutil.which('node')
    assert node, 'node is required to execute the harness'
    return node


def test_an_argv_that_is_not_a_program_file_answers(tmp):
    """`node -e <source> <args>`, launched exactly as the caller spelled it.

    The program launch hides its tail behind a written file and a prologue
    that splices it, so the same tail reads differently through the two
    entry points. The child reports its OWN `argv` tail, so a launcher that
    wrapped the source in a program file, or spliced the prologue into it,
    is caught here rather than passing a child that happens to exit 0.
    """
    import _noderun  # noqa: E402

    source = 'process.stdout.write(JSON.stringify(process.argv.slice(1)));'
    result = _noderun.run_node_argv(
        _node(), ['-e', source, 'tail', 'argument'], tmp)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout == '["tail","argument"]', result.stdout
    assert result.args[1:] == ['-e', source, 'tail', 'argument'], result.args


def test_a_script_path_tail_runs_that_script(tmp):
    """`node <script> <args>`, the other shape the tail takes."""
    import _noderun  # noqa: E402

    script = Path(tmp) / 'gate.js'
    script.write_text(
        "process.stderr.write('gate ran');\n"
        "process.stdout.write(process.argv.slice(2).join(':'));\n",
        encoding='utf-8')
    result = _noderun.run_node_argv(
        _node(), [str(script), 'first', 'second'], tmp)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout == 'first:second', result.stdout
    assert result.stderr == 'gate ran', result.stderr


def test_a_non_zero_return_code_comes_back_with_its_diagnostics(tmp):
    """A failing child is an answer, not an expiry, and it keeps its stderr."""
    import _noderun  # noqa: E402

    result = _noderun.run_node_argv(
        _node(), ['-e', "process.stderr.write('boom'); process.exit(3);"],
        tmp)
    assert result.returncode == 3, result.returncode
    assert result.stderr == 'boom', result.stderr
    assert result.stdout == '', result.stdout


def test_a_child_given_stdin_reads_it(tmp):
    """`stdin_data` reaches the child as its stdin."""
    import _noderun  # noqa: E402

    result = _noderun.run_node_argv(
        _node(), ['-e', _echo_stdin_source()], tmp,
        stdin_data='fed to stdin\n')
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout == 'FED TO STDIN\n', result.stdout


def _echo_stdin_source():
    """A child that reads stdin to end of file and reports what arrived."""
    return ("let seen = '';"
            "process.stdin.on('data', (c) => { seen += c; });"
            "process.stdin.on('end', () => {"
            " process.stdout.write(seen.toUpperCase()); });")


def test_a_child_given_no_stdin_reaches_end_of_file_at_once(tmp):
    """No `stdin_data` is DEVNULL, and a pipe nobody writes is not.

    The child reads stdin to end of file either way, so the byte count is
    what tells the two apart: a launcher that handed over a pipe and kept
    its write end open would never reach that end, and the answer would be
    the detector firing rather than an empty read.
    """
    import _noderun  # noqa: E402

    result = _noderun.run_node_argv(
        _node(), ['-e', _echo_stdin_source()], tmp)
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout == '', result.stdout


def test_the_program_launch_still_writes_its_prologue_and_payload(tmp):
    """The refactor's other side: the program launch is unchanged.

    The prologue is load-bearing rather than decoration — it splices the
    written file out of `argv` and pushes the payload as an object literal
    — so a refactor that dropped or reordered it would move every argument
    the harnesses pass and would still run a child that exits 0.
    """
    import _noderun  # noqa: E402

    program = 'process.stdout.write(JSON.stringify(process.argv.slice(1)));'
    result = _noderun.run_node_program(
        _node(), program, ['first', 'second'], tmp, payload={'plan': 1})
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout == '["first","second",{"plan":1}]', result.stdout


def test_the_expiry_message_names_a_tail_that_is_not_a_path(tmp):
    """`node -e <source>` has no program file, and a source is not a name.

    The report's child line has to do two things at once, and a fixture
    carrying only one of them cannot tell whether the code did both. It
    must NAME the child — the flag and the source it was handed, since a
    basename of a source string is a fragment of that source and names
    something that exists nowhere. And it must be BOUNDED, because a
    source pasted whole is unreadable on the one line a reader scans
    first.

    So the source here contains a slash, which is what a real one nearly
    always does (`require('/a/b/c')`, a path, a regex, even `1/2`), and is
    long enough that a dropped bound shows. A truncation-only fixture
    passes against a label that names nothing.
    """
    import _noderun  # noqa: E402

    source = ("const fs = require('/a/b/c.js');"
              " process.stdout.write('" + 'x' * 4000 + "');")
    real_popen = _noderun.subprocess.Popen
    real_cleanup = _noderun.cleanup_process_tree
    _noderun.subprocess.Popen = _stuck_popen
    _noderun.cleanup_process_tree = lambda process, bound: 'simulated cleanup'
    caught = None
    try:
        _noderun.run_node_argv(_node(), ['-e', source], tmp)
    except BaseException as failure:  # noqa: BLE001
        caught = failure
    finally:
        _noderun.subprocess.Popen = real_popen
        _noderun.cleanup_process_tree = real_cleanup
    assert isinstance(caught, _noderun.ChildDeadlineExceeded), caught
    line = next(row for row in str(caught).splitlines()
                if row.startswith('  child: '))
    assert _node() in line, line
    assert ' -e ' in line, line
    assert source[:24] in line, line
    assert len(line) < 200, len(line)


def test_both_entry_points_reach_one_launch(tmp):
    """The sharing, read from the source, because nothing else can see it.

    One implementation or two detectors: a second launch is invisible to
    every behavioural control, because it answers exactly the same. So
    this reads the property the brief asks for out of the module itself —
    one launch in the file, inside the function both entry points call,
    carrying the session flag and the cleanup bound UNCONDITIONED. That
    last part is the one that matters: a child that does not get its own
    session shares THIS process's group, which the tree kill cannot take
    without taking the suite with it, so the whole tree outlives the
    child's death and the suite ceiling's SIGTERM.

    The shape is the one this file already uses for the single-owner kill
    rule: a control that names one file and reads it, so a decoy import
    and a third module with its own copy both fail it.
    """
    del tmp
    import _noderun  # noqa: E402

    tree = ast.parse(Path(_noderun.__file__).read_text(encoding='utf-8'))
    launches = [node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and ast.unparse(node.func) == 'subprocess.Popen']
    assert len(launches) == 1, [ast.unparse(one) for one in launches]
    launch = launches[0]
    assert _enclosing(tree, launch) == '_launch_child', (
        _enclosing(tree, launch))
    keywords = {word.arg: word.value for word in launch.keywords}
    session = ast.unparse(keywords['start_new_session'])
    assert session == "sys.platform != 'win32'", session
    cleanup = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and ast.unparse(node.func) == 'cleanup_process_tree']
    assert len(cleanup) == 1, [ast.unparse(one) for one in cleanup]
    call = cleanup[0]
    # the SAME child the launch produced, and the module's own composed
    # bound rather than a number written at the call site
    assert ast.unparse(call.args[0]) == _bound_child_name(tree, launch), (
        ast.unparse(call))
    assert ast.unparse(call.args[1]) == 'CLEANUP_DEADLINE_S', ast.unparse(call)
    for name in ('run_node_program', 'run_node_argv'):
        assert any(
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == '_launch_child'
            for node in ast.walk(_function_named(tree, name))), name


def _bound_child_name(tree, launch):
    """The name the launch call binds the launched child to."""
    holder = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.Assign)
                  and any(child is launch for child in ast.walk(node.value)))
    return ast.unparse(holder.targets[0])


def _enclosing(tree, node):
    """The name of the innermost function a node sits in."""
    holders = [other for other in ast.walk(tree)
               if isinstance(other, ast.FunctionDef)
               and other.lineno <= node.lineno
               <= (other.end_lineno or other.lineno)]
    return max(holders, key=lambda other: other.lineno).name if holders \
        else '<module>'


def _function_named(tree, name):
    """A module-level function's body by name."""
    return next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def test_a_child_that_never_settles_is_killed_and_reported(tmp):
    """A REAL child, past the REAL deadline — the shape CI has to survive.

    The deadline is the module's own `CHILD_DEADLINE_S` rather than a value
    this test shrinks to keep itself quick: a test that has to guess how
    long a loaded runner takes to start node is the same wall-clock margin
    this branch exists to remove, one level down. So this costs a detector
    budget and buys a kill, a report and a reap that are all the real ones.
    """
    import _noderun  # noqa: E402

    source = ("process.stdout.write(process.pid + '\\n');"
              " process.stdout.write('partial child output\\n');"
              " setInterval(() => {}, 1000);")
    caught = None
    try:
        _noderun.run_node_argv(_node(), ['-e', source], tmp)
    except _noderun.ChildDeadlineExceeded as failure:
        caught = failure
    except BaseException as unexpected:  # noqa: BLE001
        # A bare `TimeoutExpired` is the failure this entry point exists to
        # replace, so it is named rather than merely re-raised: a reader
        # needs to see WHICH of the two the launcher produced.
        assert not isinstance(unexpected, subprocess.TimeoutExpired), (
            'a bare TimeoutExpired reached the caller', unexpected)
        raise
    assert caught is not None, 'the child that never settles finished'
    assert not isinstance(caught, subprocess.TimeoutExpired), caught
    assert isinstance(caught, _noderun.ChildDeadlineExceeded), (
        type(caught).__name__, caught)
    failure = caught
    assert failure.deadline_s == _noderun.CHILD_DEADLINE_S, failure.deadline_s
    assert 'partial child output' in failure.stdout, failure.stdout
    assert failure.cleanup_diagnostic, 'the cleanup reported nothing'
    assert _child_is_gone(failure.stdout), (
        'the cleanup reported a kill and left the child running')
    message = str(failure)
    assert _node() in message, message
    for line in (f'deadline: {failure.deadline_s}s', 'cleanup: ',
                 'stdout: ', 'stderr: '):
        assert line in message, (line, message)


def _child_is_gone(stdout):
    """Whether the pid the child reported is no longer a live process.

    The cleanup's own string is a REPORT; this is the thing the report is
    about, and a launcher that reported a kill it never performed passes a
    string assertion. The probe is `os.kill(pid, 0)`, which on POSIX raises
    for a pid the kernel has reaped and returns for a live one, and on
    Windows raises for a process it cannot open and TERMINATES one it can —
    so a survivor fails the assertion on both, and the orphan left by a
    broken cleanup does not outlive the suite.
    """
    pid = int(stdout.splitlines()[0])
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


def test_a_real_call_site_reports_its_own_stalled_child(tmp):
    """The same property, reached through a caller rather than the launcher.

    Everything above drives the launcher itself. This drives
    `tests/_jsroute_harness.py`'s real `runtime_and_guard`, which is a
    call site in the tree like any other, and that is the half a launcher
    control cannot see: a site that kept its own `subprocess.run` would
    satisfy every control in this file and still report a bare
    `TimeoutExpired` naming the whole command.

    The source reaches Node, writes a line and then never settles, so the
    child stalls having produced something — which is precisely the case
    where its partial output is the only evidence there is.
    """
    import _noderun  # noqa: E402
    from _jsroute_harness import runtime_and_guard  # noqa: E402

    path = Path(tmp) / 'stalled.js'
    real_deadline = _noderun.CHILD_DEADLINE_S
    _noderun.CHILD_DEADLINE_S = round(real_deadline * 0.1)
    caught = None
    # The alarm is armed and disarmed HERE rather than in a context
    # manager, because the census judges a process-level deadline against
    # the scope that reports it, and a helper would put the two in
    # different functions. `TimeoutError` is what the handler raises, and
    # the handler re-raises as an assertion, so the alarm is a reported
    # failure rather than a swallowed one.
    signal.signal(signal.SIGALRM, _raise_outer_deadline)
    signal.setitimer(signal.ITIMER_REAL, OUTER_ALARM_S)
    try:
        try:
            runtime_and_guard(
                "process.stdout.write("
                "'the child spoke before it wedged\\n');\n"
                'setInterval(() => {}, 1000);\n', path)
        except _noderun.ChildDeadlineExceeded as failure:
            caught = failure
        except BaseException as unexpected:  # noqa: BLE001
            # A bare `TimeoutExpired` is the failure this entry point
            # exists to replace, so it is named rather than re-raised.
            assert not isinstance(unexpected, subprocess.TimeoutExpired), (
                'a bare TimeoutExpired reached the caller', unexpected)
            raise
    except TimeoutError as alarm:
        raise AssertionError(
            'the outer alarm fired: the child wedged and nothing in the '
            'suite ended it, which is what this control exists to prevent'
        ) from alarm
    finally:
        signal.setitimer(signal.ITIMER_REAL, OUTER_ALARM_CLEAR_S)
        _noderun.CHILD_DEADLINE_S = real_deadline
    assert caught is not None, 'the child that never settles finished'
    assert 'the child spoke before it wedged' in caught.stdout, caught.stdout
    assert caught.cleanup_diagnostic, 'the cleanup reported nothing'


def test_a_call_site_bound_reports_its_own_stalled_child(tmp):
    """The OTHER shape of hang detector, at a site that keeps its bound.

    Everything above drives the shared launcher. This drives
    `tests/_gm_harness.py`'s real `_run_node`, which keeps its own bound
    because reaching that launcher would put the module inside the census's
    audited path — and no suite owns this call site either, so the control
    lives here beside the one it answers.

    Its child is the case a bound was hardest to judge: it loads the
    SHIPPED `extension/content.js` and `page.js` into a fake window. The
    verdict is still fixed work, and the reasoning is recorded in
    `_gm_harness.py` beside the figure. What this asserts is the property
    the verdict bought — a wedged child is reported by the site's own named
    failure carrying the line it wrote before it stopped, not by a bare
    `TimeoutExpired` that names a figure nobody can re-derive.

    It costs the site's real composed budget, which is what the two controls
    above already do: the figure a maintainer re-derives is the one the
    expiry has to fire at, and a control that shortened it would be proving
    a different number.
    """
    from _gm_harness import _run_node  # noqa: E402
    from _node_launch_routing import NodeBoundExceeded  # noqa: E402

    stalling = ("process.stdout.write('the storage child spoke before it "
                "wedged\\n'); setInterval(() => {}, 1000);")
    caught = None
    try:
        _run_node(stalling)
    except NodeBoundExceeded as failure:
        caught = failure
    finally:
        del tmp
    assert caught is not None, 'the storage child that never settles finished'
    assert 'the storage child spoke before it wedged' in caught.stdout, (
        caught.stdout)
    assert caught.deadline_s > 0, caught.deadline_s


def test_an_environment_the_caller_built_reaches_the_child(tmp):
    """`environment` is threaded, and a value only the caller holds arrives.

    `test_js_coverage.py` points `NODE_V8_COVERAGE` at a dumps directory it
    builds per test, so the child must be handed THAT environment rather
    than this process's. A launcher that accepted the parameter and then
    read `os.environ` would satisfy every signature-shaped check here and
    send the child to the wrong directory, so the assertion is on what the
    child actually saw.

    The negative half is the same property from the other side: with no
    environment passed, the child gets this process's, and a value planted
    only in the caller's dict does not reach it.
    """
    import _noderun  # noqa: E402

    source = "process.stdout.write(process.env.NODE_V8_COVERAGE || 'none');"
    caller_only = str(Path(tmp) / 'caller-only-dumps')
    environment = dict(os.environ)
    environment['NODE_V8_COVERAGE'] = caller_only
    result = _noderun.run_node_argv(
        _node(), ['-e', source], tmp,
        environment=_util.child_coverage('scrub', environment))
    assert result.returncode == 0, (result.returncode, result.stderr)
    assert result.stdout == caller_only, result.stdout
    # And the same child launched without one does not see it, so the
    # assertion above is the parameter doing the work.
    os.environ['NODE_V8_COVERAGE'] = caller_only
    try:
        without = _noderun.run_node_argv(_node(), ['-e', source], tmp)
    finally:
        del os.environ['NODE_V8_COVERAGE']
    assert without.returncode == 0, (without.returncode, without.stderr)
    assert without.stdout == caller_only, (
        'the launcher read os.environ rather than the caller\'s dict')
    # A different value entirely, so the two halves cannot agree by luck.
    other = str(Path(tmp) / 'other-dumps')
    second = dict(os.environ)
    second['NODE_V8_COVERAGE'] = other
    third = _noderun.run_node_argv(
        _node(), ['-e', source], tmp,
        environment=_util.child_coverage('scrub', second))
    assert third.stdout == other, third.stdout


def _raise_permission_error(directory):
    del directory
    raise PermissionError(32, 'The process cannot access the file')


def _stuck_popen(argv, **kwargs):
    """A launch that writes the output the report must carry, then sticks."""
    kwargs['stdout'].write(b'out')
    kwargs['stderr'].write(b'err')
    return StuckAgain()


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='noderundeadline_')


if __name__ == '__main__':
    raise SystemExit(main())
