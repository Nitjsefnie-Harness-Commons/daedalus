"""The stand-ins the launcher's own controls use in place of a real child.

`tests/test_noderun_deadline.py` is about `tests/_noderun.py`'s detector:
what it raises, what its report says, and what happens when the child, the
cleanup or the scratch removal goes wrong. Most of that needs a real Node
child, and the three that must NOT have one — the expiry message, the
unlink door, and the report's completeness — are here, so the file that
drives the launcher is not also the file that fakes it.

`_child_is_gone` is the odd one out and is here for the same reason: it is
the probe that tells a real kill from a reported one, and it belongs beside
the doubles rather than inside a control that could make it look like part
of the assertion it supports.
"""
import os
import subprocess


class Stuck:
    """A child that is already past the detector and never exits."""

    pid = 4242

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(['node', 'x'], timeout or 0.0)

    def kill(self):
        pass


def stuck_popen(output=b'partial child output',
                diagnostic=b'partial child diagnostics'):
    """A launch that writes the report's own lines, then returns `Stuck`."""
    def _popen(argv, **kwargs):
        del argv
        kwargs['stdout'].write(output)
        kwargs['stderr'].write(diagnostic)
        return Stuck()
    return _popen


class StuckAgain:
    """A child already past the detector, for the unlink-door control."""

    pid = 4343

    def wait(self, timeout=None):
        """Time out however long it is given."""
        raise subprocess.TimeoutExpired(['node', 'x'], timeout or 1)

    def kill(self):
        """Nothing to kill in this double."""


def raise_permission_error(directory):
    """A removal that raises, so the report has to carry the failure."""
    del directory
    raise PermissionError(32, 'The process cannot access the file')


def stuck_popen_again(argv, **kwargs):
    """The unlink door's launch: writes what the report must carry, sticks."""
    del argv
    kwargs['stdout'].write(b'out')
    kwargs['stderr'].write(b'err')
    return StuckAgain()


def child_is_gone(stdout):
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
