"""What the standard library itself says, read once at import.

Two tables and one predicate, none of which binds configuration of its
own and none of which is the analyser: the keywords a `subprocess`
launch may be handed, the stdlib roots an attribute chain is refused
on, and the predicate that reads a receiver's root off the spelling of
its import.

They live here because `_launch_audit.py` is at its size ceiling and
these are the analyser supporting rather than being it, the same
relocation issue 1258 made for the arm table. Every guard clause here
is named in `tests/_launch_arm_records.py` as a non-member, and
`tests/test_launch_arms.py` sweeps this file beside the two analysers,
so the claim covers the third file too.

The keyword set comes from `inspect.signature` rather than a hand list,
so a keyword a future interpreter adds is admitted and a misspelling is
a refusal rather than a silent pass. A hand list made `pipesize` a
false red once.

The roots are each a PACKAGE whose submodule reaches a launch or a
child wait (asyncio.subprocess, concurrent.futures,
multiprocessing.connection, os.popen), and each has a row that can
execute. `shutil` and `pty` are not packages, so the predicate is
unreachable through either. `subprocess` needs no entry: the predicate
DOES return it for `import subprocess.spawn`, but every
`subprocess.x(...)` call is placed before the unplaced arm reads it.
"""
import ast
import inspect
import subprocess
import sys


def _launch_keywords():
    """What a launch may legitimately be handed, read from the stdlib.

    From `inspect.signature` rather than a list, so a keyword a future
    interpreter adds is admitted and a misspelling is a refusal rather
    than a silent pass. A hand list made `pipesize` a false red once.
    """
    popen = inspect.signature(
        subprocess.__dict__['Popen'].__init__).parameters
    run = inspect.signature(subprocess.__dict__['run']).parameters
    return frozenset(popen) | frozenset(run)


_LAUNCH_KEYWORDS = _launch_keywords()

_STDLIB_LAUNCH_ROOTS = frozenset(
    {'asyncio', 'concurrent', 'multiprocessing', 'os'})


def _dotted_stdlib_root(receiver, dotted_roots):
    """The stdlib root this attribute chain is spelled from, or None."""
    while isinstance(receiver, ast.Attribute):
        receiver = receiver.value
    if isinstance(receiver, ast.Name) and receiver.id in dotted_roots \
            and receiver.id in sys.stdlib_module_names \
            and receiver.id not in _STDLIB_LAUNCH_ROOTS:
        return receiver.id
    return None
