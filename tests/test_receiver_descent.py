#!/usr/bin/env python3
"""What the receiver descent hands over when the chain reads no launcher.

`_carried_parts` and the tables in `tests/_carrier_cases.py` judge the forms
a value carries; these two tables judge the one decision
`_call_receiver_parts` makes about the base its chain lands on, which the
other tables reach only through the rows that happen to sit on either side of
it. Every row here is a receiver that CONTAINS a module name and a method
read off it, so the only thing separating a refusal from a clean verdict is
whether a launch method is read somewhere in the chain.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _coverage_guard import (  # noqa: E402
    _BINDING_MESSAGE, _synthetic_violations)


def _at(source, marker, message=_BINDING_MESSAGE):
    """The diagnostic owed a source, at the line carrying `marker`."""
    line = source[:source.index(marker)].count('\n') + 1
    return f'tests/synthetic.py:{line}: {message}'


def _one_verdict_each(cases):
    """Each case must compile, then yield exactly its one binding verdict."""
    for name, source, marker in cases:
        compile(source, f'<{name}>', 'exec')
        violations = _synthetic_violations(source)
        assert violations == [_at(source, marker)], (name, violations)


def _no_verdicts(cases):
    """Each case must compile, then yield nothing at all."""
    for name, source in cases:
        compile(source, f'<{name}>', 'exec')
        assert _synthetic_violations(source) == [], name


def _non_launch_receiver_cases():
    """A chain that reads no launch method carries no launcher.

    The receiver position reads a launch method off what it carries, and
    none of these chains names one: the module is inside a value the method
    is read off — a string, a list, a tuple, a mapping, a call's result —
    and the method returns a string, a count or a fresh container. Handing
    the base to the walk anyway finds the bare module name inside the
    container and calls it a launcher, which is the refusal this table
    exists to keep out.

    Every row here is a receiver the descent alone decides. `go =
    list(subprocess).count(1)` reads the same way and is still refused,
    by `_carried_parts` rather than by this arm: the assigned value is
    itself a call, and the binding position descends a call-based callee
    down to `list(subprocess)` on its own. That descent is another
    mechanism and out of this issue's scope, so a row here is a shape
    whose callee bottoms on something other than a bare name — the rows
    that call a method on a container or a tuple reach the module through
    the receiver arm and through no other.

    The last two rows are the no-call forms of the first two. Nothing
    reaches the descent without a call, so they are recorded rather than
    load-bearing, and they are here so a reword of the claim above has to
    keep saying the same thing about a chain of length one.
    """
    return (
        ('f-string receiver', """import os
import subprocess
os.chdir(tmp)
go = f"{subprocess}".upper()
"""),
        ('list receiver', """import os
import subprocess
os.chdir(tmp)
go = [subprocess].pop()
"""),
        ('tuple receiver', """import os
import subprocess
os.chdir(tmp)
go = (subprocess,).index(1)
"""),
        ('dict receiver', """import os
import subprocess
os.chdir(tmp)
go = {1: subprocess}.pop(1)
"""),
        ('subscript then a method', """import os
import subprocess
os.chdir(tmp)
go = (subprocess,)[0].pop()
"""),
        ('call then a method', """import os
import subprocess
os.chdir(tmp)
go = (subprocess,).index(1).upper()
"""),
        ('slice then a method', """import os
import subprocess
os.chdir(tmp)
go = f"{subprocess}"[:2].upper()
"""),
        ('two links off a call', """import os
import subprocess
os.chdir(tmp)
go = (subprocess,).index(1).bit_length()
"""),
        ('method off a call on a dict', """import os
import subprocess
os.chdir(tmp)
go = {"a": subprocess}.values().pop()
"""),
        ('f-string attribute, no call', """import os
import subprocess
os.chdir(tmp)
go = f"{subprocess}".upper
"""),
        ('list attribute, no call', """import os
import subprocess
os.chdir(tmp)
go = [subprocess].pop
"""),
    )


def _intermediate_call_carrier_cases():
    """A call inside the chain, and the launch read that makes it matter.

    The descent used to stop at a call and hand it over whatever the chain
    above it read, so these rows and the table above were decided by the
    same question asked at the wrong level. A call in the chain is now
    descended into — the walk reaches the base BENEATH it — and it is
    handed to the walk as a sub-value in its own right whenever a launch
    read sits above it, because then it carries a launcher into the value
    the launch method is read off.

    The second and third rows are what the descent through the call buys:
    with the call treated as the base, the module inside the container the
    call was built on is one link out of reach. The first row is the only
    one whose handoff this arm decides alone, and it is a statement
    rather than a binding for that reason: an assigned value reaches
    `_carried_parts` too, and that walk reopens a call-based callee down
    to `f(subprocess)` on its own.
    """
    return (
        ('call argument as a receiver', """import os
import subprocess
os.chdir(tmp)
def run_it():
    f(subprocess).run(["python3", "child.py"])
""", 'f(subprocess)'),
        ('call on an f-string as a receiver', """import os
import subprocess
os.chdir(tmp)
go = f"".join([str(subprocess)]).run(["python3", "child.py"])
""", 'f"".join'),
        ('subscripted dict as a receiver', """import os
import subprocess
os.chdir(tmp)
go = {"sp": subprocess}["sp"].run(["python3", "child.py"])
""", '{"sp": subprocess}'),
        ('subscripted tuple as a receiver', """import os
import subprocess
os.chdir(tmp)
go = (subprocess,)[0].run(["python3", "child.py"])
""", '(subprocess,)[0]'),
        # The launch read is the TOP link here, so it is what re-opens the
        # call beneath it. That is the whole of the descent's second
        # condition: without it the call is walked past and the module
        # inside the container the call was built on is never reached.
        ('launch read above a call', """import os
import subprocess
os.chdir(tmp)
go = list(subprocess).count(1).run(["python3", "child.py"])
""", '.count(1).run('),
    )


def test_a_receiver_reading_no_launch_method_stays_clean(tmp):
    del tmp
    _no_verdicts(_non_launch_receiver_cases())


def test_a_receiver_reached_through_an_intermediate_call_is_refused(tmp):
    del tmp
    _one_verdict_each(_intermediate_call_carrier_cases())


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
