"""The platform's own names a child needs to boot, and when to add them.

A caller that hands a launch an environment chooses the child's variables,
and that choice is honoured in full — except that a platform may refuse to
start a process without some of its own. Node loads its CSPRNG provider out
of `%SystemRoot%\\System32`, so a child launched with a hand-built environment
that omits `SystemRoot` aborts inside `ncrypto::CSPRNG` during
`InitializeOncePerProcess`: an assertion about entropy that is really about
the environment. `subprocess` documents the same requirement — an explicit
`env=` must include `SYSTEMROOT` for the child to start at all.

The names are restored from THIS process with `setdefault`, so a name the
caller chose still wins, and they are restored on Windows only. POSIX has no
such floor: a child with only `PATH` starts there, which is why this is
invisible on Linux and macOS and fatal on all four `windows-latest` legs.

It lives here rather than in `tests/_util.py` beside `child_coverage`
because `_util.py` is at its size ceiling, and because the floor is a
property of the PLATFORM rather than of the coverage declaration it is
applied on the way through.
"""
import os
import sys

_PLATFORM_BOOT_ENV = ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC',
                      'PATHEXT', 'TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA',
                      'USERPROFILE', 'PROGRAMFILES', 'PROGRAMDATA',
                      'HOMEDRIVE', 'HOMEPATH', 'OS', 'PROCESSOR_ARCHITECTURE',
                      'NUMBER_OF_PROCESSORS')


def _with_platform_boot_names(environment):
    """The child's environment plus the platform's own boot names.

    The names come from this process when the caller's environment omits
    them, and from nowhere when it does not: there is no process left to
    copy them out of by the time the child is already aborting.
    """
    if sys.platform != 'win32':
        return environment
    for name in _PLATFORM_BOOT_ENV:
        environment.setdefault(name, os.environ.get(name, ''))
    return environment
