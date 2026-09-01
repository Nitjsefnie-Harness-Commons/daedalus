"""A child process that cannot find libc, for the malloc-tuning branch.

Not a suite itself — run_tests.py only loads `test_*.py`.

The bridge tunes glibc's malloc arenas at import and reports the failure
when it cannot. Reaching that report on a glibc host needs the failure
manufactured, and both the suite that pins the import and the one that pins
the startup line need the same manufacture, spelled the same way.
"""
from pathlib import Path


def no_glibc_pythonpath(tmp):
    """A PYTHONPATH directory whose sitecustomize hides libc from ctypes."""
    # `find_library('c')` is what the tuning setup resolves, so a name no
    # loader can open drives the unavailable branch deterministically,
    # without touching any other library the child may load.
    directory = Path(tmp) / 'noglibc'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'sitecustomize.py').write_text(
        'import ctypes.util\n'
        '_real = ctypes.util.find_library\n'
        'ctypes.util.find_library = (\n'
        "    lambda name: 'daedalus-absent-libc.so'\n"
        "    if name == 'c' else _real(name))\n",
        encoding='utf-8')
    return str(directory)
