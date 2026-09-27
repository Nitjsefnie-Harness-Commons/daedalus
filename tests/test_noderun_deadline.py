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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402

TESTS = Path(__file__).resolve().parent


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
