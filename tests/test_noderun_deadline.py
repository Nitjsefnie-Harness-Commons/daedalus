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


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='noderundeadline_')


if __name__ == '__main__':
    raise SystemExit(main())
