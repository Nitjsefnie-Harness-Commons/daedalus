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
