"""A refusing `shutil` stand-in, and the swap that installs one.

Every stand-in RECORDS what it was asked to do: an assertion on the
subject's returned string alone is satisfied by code that sent nothing.
"""
import contextlib
import shutil


class Removals:
    """`shutil` as the output cleanup reads it: refusals made, calls kept.

    `refuse` is how many calls refuse before the work is done for real;
    a call past it is the real `shutil.rmtree`.
    """

    def __init__(self, refuse=0, error=None):
        self.refuse = refuse
        self.error = error
        self.calls = []

    def __getattr__(self, name):
        return getattr(shutil, name)

    def rmtree(self, path, *args, **kwargs):
        self.calls.append(str(path))
        if len(self.calls) <= self.refuse:
            if self.error is not None:
                raise self.error
            raise PermissionError(
                32, 'in use by another process', str(path))
        return shutil.rmtree(path, *args, **kwargs)


@contextlib.contextmanager
def swapped(module, **stand_ins):
    """The subject's own globals, stood in for, and put back afterwards.

    On the module under test rather than on `os`, `time` or `subprocess`
    themselves: those are shared with every other suite in this process.
    A function's `__globals__` is accepted too: a control driving an
    imported function has to reach the dict it reads.
    """
    namespace = module if isinstance(module, dict) else module.__dict__
    saved = {name: namespace[name] for name in stand_ins}
    namespace.update(stand_ins)
    try:
        yield
    finally:
        namespace.update(saved)
