"""The stand-ins the launcher's own controls use in place of a real child.

`tests/test_noderun_deadline.py` is about `tests/_noderun.py`'s detector:
what it raises, what its report says, and what happens when the child, the
cleanup or the scratch removal goes wrong. Most of that needs a real Node
child, and the three that must NOT have one — the expiry message, the
unlink door, and the report's completeness — are here, so the file that
drives the launcher is not also the file that fakes it.

The receipt that tells a real kill from a reported one is not here: it is
`tests/_processtree.py`'s `process_is_gone`, beside the kill it reads.
"""
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
