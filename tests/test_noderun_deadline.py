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
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _launcher_stand_ins as stand_ins  # noqa: E402
import _util  # noqa: E402
from _outer_bound import (  # noqa: E402
    OuterBoundExpired, announcing_pid, outer_bound)
from _processtree import process_is_gone  # noqa: E402

TESTS = Path(__file__).resolve().parent

# --- the outer bound, which is not the subject's own defence ----------------
#
# A control that provokes an expiry sets its budget inside
# `tests/_noderun.py` — precisely the code a reversion removes. Against an
# UNBOUNDED launch the control's only defence is the machinery it is testing,
# and it hangs until something external kills it. This bound is held by the
# control, from `tests/_outer_bound.py`, on the call.
#
# It must clear the healthy path with room to spare and still fire well
# inside any external bound. The healthy budgets are the ones the two
# controls set for themselves — 27s and 22s — so the tightest margin
# 57s keeps is over that 27s. The samples are this bound's own observed
# expiries, which is circular on its face: what they record is the wedge,
# and what the figure owes is the margin above the healthy budgets.
OUTER_BOUND_SAMPLES = (52.0, 55.0, 57.0)
OUTER_BOUND_SLOWEST_S = max(OUTER_BOUND_SAMPLES)
OUTER_BOUND_S = round(OUTER_BOUND_SLOWEST_S)

# --- the budget a STALLED child is given -----------------------------------
#
# Two controls below drive a real child that never settles, and each used
# to wait out its site's composed figure — 107s at the launcher site,
# 90s at the GM one, about a tenth each of the 900s `run_tests.py` allows a
# suite — on every leg of the twelve-cell matrix
# (`scripts/ci/classify_changes.py`'s `FULL_MATRIX`, four of them
# `windows-latest`).
# Each child writes a line and then holds the event loop open forever, so
# the deadline's one job is to clear node's own startup. The figures are
# arithmetic and the AST control pins the chain; the FIRING does not depend
# on them, which the expiry-message control above already shows at a
# one-second budget. What is given up is named here rather than traded
# silently: that 107 and 90 fire in particular.
#
# The table is what the budget must clear: node's own startup.
STALLED_CHILD_START_SAMPLES_S = (0.048, 0.054, 0.080, 0.147, 0.158)
STALLED_CHILD_START_SLOWEST_S = max(STALLED_CHILD_START_SAMPLES_S)


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

    fake_popen = stand_ins.stuck_popen()
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
    _noderun._remove_tree = stand_ins.raise_permission_error
    _noderun.subprocess.Popen = stand_ins.stuck_popen_again
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
    # TWO of them, and the count is the pin. `run_node_program` hands the
    # launcher its own scratch tree alongside the output one and the two
    # are closed after the cleanup and BEFORE the report is built, so
    # dropping the caller's tree from that loop — `(output_scratch,)` — is
    # invisible to every other assertion here: one removal still fails, the
    # phrase is still in the report, and the line that went missing is the
    # one a maintainer reads at 3am on `windows-latest`. Measured, not
    # argued: that reversion left this file 15/15.
    assert message.count('was not fully removed') == 2, message


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
    _noderun.subprocess.Popen = stand_ins.stuck_popen_again
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
    # And the CHILD's own bound, read the same way. Every behavioural
    # control below learns whether it works by WAITING for it, so a
    # `timeout=99999` typed at the wait was caught by nothing but the suite
    # ceiling — a signal that says "the job timed out" rather than "this
    # assertion is wrong", and the slowest way to learn a one-line change.
    waits = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == 'wait'
             and any(word.arg == 'timeout' for word in node.keywords)]
    assert len(waits) == 1, [ast.unparse(one) for one in waits]
    child_bound = next(word.value for word in waits[0].keywords
                       if word.arg == 'timeout')
    assert ast.unparse(child_bound) == 'CHILD_DEADLINE_S', (
        ast.unparse(child_bound))
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
    """A REAL child, past a REAL deadline — the shape CI has to survive.

    The kill, the report and the reap are all the launcher's own; only the
    budget is a quarter of the site's figure, for the reasons recorded above
    the sample table, and the deadline the report names is read back off
    the failure rather than off the module. The bound the control holds
    while it waits is the outer one, for the reason given there.
    """
    import _noderun  # noqa: E402

    # The pid is announced to a file as well as to stdout, because the bound
    # that ends this control reading as a hang kills a child it can only
    # reach through a pid, and the launcher's own cleanup is the thing a
    # reversion removes.
    pid_file = Path(tmp) / 'never-settles.pid'
    source = (announcing_pid(pid_file) + '\n'
              "process.stdout.write(process.pid + '\\n');"
              " process.stdout.write('partial child output\\n');"
              " setInterval(() => {}, 1000);")
    real_deadline = _noderun.CHILD_DEADLINE_S
    budget = round(real_deadline * 0.25)
    _noderun.CHILD_DEADLINE_S = budget
    caught = None
    try:
        with outer_bound(OUTER_BOUND_S, pid_file, "the launcher's deadline"):
            try:
                _noderun.run_node_argv(_node(), ['-e', source], tmp)
            except _noderun.ChildDeadlineExceeded as failure:
                caught = failure
            except BaseException as unexpected:  # noqa: BLE001
                # A bare `TimeoutExpired` is the failure this entry point
                # exists to replace, so it is named rather than merely
                # re-raised: a reader needs to see WHICH of the two the
                # launcher produced.
                assert not isinstance(
                    unexpected, subprocess.TimeoutExpired), (
                    'a bare TimeoutExpired reached the caller', unexpected)
                raise
    except OuterBoundExpired as wedged:
        # The bound fires only on a launch reverted to an unbounded one, and
        # it kills the child rather than only reporting it — so a wedge here
        # is a named failure, where it was a suite timeout.
        raise AssertionError(
            "the outer bound fired, so the child's own bound never "
            "ended it, which is what this control exists to prevent. "
            f"What the bound reports: {wedged}"
        ) from wedged
    finally:
        _noderun.CHILD_DEADLINE_S = real_deadline
    assert caught is not None, 'the child that never settles finished'
    assert not isinstance(caught, subprocess.TimeoutExpired), caught
    assert isinstance(caught, _noderun.ChildDeadlineExceeded), (
        type(caught).__name__, caught)
    failure = caught
    assert failure.deadline_s == budget, failure.deadline_s
    assert 'partial child output' in failure.stdout, failure.stdout
    assert failure.cleanup_diagnostic, 'the cleanup reported nothing'
    assert process_is_gone(int(failure.stdout.splitlines()[0])), (
        'the cleanup reported a kill and left the child running')
    message = str(failure)
    assert _node() in message, message
    for line in (f'deadline: {failure.deadline_s}s', 'cleanup: ',
                 'stdout: ', 'stderr: '):
        assert line in message, (line, message)


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

    It costs a quarter of the site's composed budget, for the reasons
    recorded above the sample table. This child is a `-e` source of its own
    rather than the shipped scripts, so the healthy path is node's startup
    and nothing else. It is held by the outer bound for the reason given
    there: the site keeps its own bound precisely so this module stays out
    of the census's audited path, which means a reversion removes the very
    thing that would have ended the wait.
    """
    import _gm_harness  # noqa: E402
    from _gm_harness import _run_node  # noqa: E402
    from _node_launch_routing import NodeBoundExceeded  # noqa: E402

    pid_file = Path(tmp) / 'storage-child.pid'
    stalling = (announcing_pid(pid_file) + '\n'
                "process.stdout.write('the storage child spoke before it "
                "wedged\\n'); setInterval(() => {}, 1000);")
    real_deadline = _gm_harness.GM_CHILD_DEADLINE_S
    budget = round(real_deadline * 0.25)
    _gm_harness.GM_CHILD_DEADLINE_S = budget
    caught = None
    try:
        with outer_bound(OUTER_BOUND_S, pid_file, 'the storage call site'):
            try:
                _run_node(stalling)
            except NodeBoundExceeded as failure:
                caught = failure
    except OuterBoundExpired as wedged:
        raise AssertionError(
            "the outer bound fired, so the storage child's own bound never "
            "ended it, which is what this control exists to prevent. "
            f"What the bound reports: {wedged}"
        ) from wedged
    finally:
        _gm_harness.GM_CHILD_DEADLINE_S = real_deadline
        del tmp
    assert caught is not None, 'the storage child that never settles finished'
    assert 'the storage child spoke before it wedged' in caught.stdout, (
        caught.stdout)
    assert caught.deadline_s == budget, caught.deadline_s


def test_the_deadline_message_describes_the_ceiling_that_now_exists(_tmp):
    """The text a maintainer reads when this failure fires must be true.

    Issue #1204 turned the per-suite ceiling into a two-phase GROUP kill: the
    request goes to the suite's whole process group, a bounded grace follows,
    and the escalation to the group fires whatever happened in between. A
    Node child this launcher started is inside that group, so it does NOT
    survive the ceiling -- which is the opposite of what this message and
    the `run_node_program` docstring both said, and both said it to exactly
    the person diagnosing a failure.

    The assertion READS the rendered message rather than grepping the file: a
    grep cannot tell a message string from the comment above it, and the
    distinction is the whole point -- one of the two sites is a string a
    failing test prints into CI output.
    """
    import _noderun  # noqa: PLC0415 - one use, and a module under test

    failure = _noderun.ChildDeadlineExceeded(
        ['node', 'while (true) {}'], 30, 'partial stdout', 'partial stderr',
        'process group 4242 asked to stop and the suite did')
    rendered = str(failure)
    assert 'suite ceiling' in rendered, rendered
    assert 'whole process group' in rendered, rendered
    for stale in ('leaves this child running', 'sends SIGTERM to the suite',
                  'reparented'):
        assert stale not in rendered, (
            f'the deadline message still says {stale!r}: {rendered}')


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='noderundeadline_')


if __name__ == '__main__':
    raise SystemExit(main())
