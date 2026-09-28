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


def _for_iterable_carrier_cases():
    """A loop's iterable is decomposed, so its receiver arrives in a target.

    `{'sp': subprocess}.values()` is the row that decides this arm, and
    the discrimination is the use site rather than the call. The same
    receiver in a binding position is the clean row `{"a": subprocess}
    .values().pop()` above: an assignment reads nothing out of what the
    call returns, while a loop binds a launcher out of it and the next
    statement launches. `_carried_parts` alone judges neither, because a
    non-launch attribute's receiver is a constant read to it.

    The last two rows are the arm's own bound. A loop over a call built
    on a bare name carries its launcher in the arguments, which the walk
    already reaches, and a loop over something that is not a call is
    judged by the walk with no help from here.

    The five rows between those bounds and the four above enumerate the
    FAMILY the arm cannot separate, because its condition is structural
    — the callee chain bottoms on something other than a name — and not
    a property of what the call returns. The four above are detections:
    every one of them yields the module, and a launch method read off
    the module launches. The five below are NOT, and are refused anyway.
    Measured at runtime and recorded here so the trade is stated once:

    | row | what the loop actually receives |
    |---|---|
    | dict keys | a `str` — `AttributeError` on `.run` |
    | dict items | a `tuple` — `AttributeError` on `.run` |
    | list pop | the module, which is not iterable — `TypeError` |
    | f-string method | the `str` of a string — `AttributeError` on `.run` |
    | tuple index | an `int` — `TypeError` |

    The arm cannot tell these from the four without naming methods, which
    is the mistake `_opaque_callee_cases`'s docstring records as this
    repository's before. They are here so a future narrowing of the arm
    has to account for the four detections beside them.
    """
    return (
        ('dict values as an iterable', """import os
import subprocess
os.chdir(tmp)
for launcher in {'sp': subprocess}.values():
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('subscripted dict as an iterable', """import os
import subprocess
os.chdir(tmp)
for launcher in {'outer': {'sp': subprocess}}['outer'].values():
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('indexed list as an iterable', """import os
import subprocess
os.chdir(tmp)
for launcher in [{'sp': subprocess}][0].values():
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('twice indexed tuple as an iterable', """import os
import subprocess
os.chdir(tmp)
for launcher in ([{'sp': subprocess}],)[0][0].values():
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('async loop over dict values', """import os
import subprocess
os.chdir(tmp)
async def go():
    for launcher in {'sp': subprocess}.values():
        launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('dict keys as an iterable', """import os
import subprocess
os.chdir(tmp)
for launcher in {'sp': subprocess}.keys():
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('dict items as an iterable', """import os
import subprocess
os.chdir(tmp)
for launcher in {'sp': subprocess}.items():
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('list pop as an iterable', """import os
import subprocess
os.chdir(tmp)
for launcher in [subprocess].pop():
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('f-string method as an iterable', """import os
import subprocess
os.chdir(tmp)
for launcher in f"{subprocess}".upper():
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('tuple index as an iterable', """import os
import subprocess
os.chdir(tmp)
for launcher in (subprocess, 1).index(1):
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('iterable call on a bare name', """import os
import subprocess
os.chdir(tmp)
for launcher in list(subprocess):
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
        ('iterable that is not a call', """import os
import subprocess
os.chdir(tmp)
for launcher in {'sp': subprocess}:
    launcher.run(['python3', 'child.py'])
""", 'for launcher in'),
    )


def _comprehension_iterable_cases():
    """A comprehension decomposes its iterable exactly as a `for` does.

    The same bypass as the table above, spelled as an expression, which is
    what a comprehension is: every element of the iterable is bound to the
    target and read in the element. These seven rows were refused on
    `8babe1ae` and went clean when the loop arm was written for the `for`
    statement and not for the `comprehension`, which is the arm's own
    sibling and nothing else.

    The last three are the family the table above enumerates, carried into
    the expression form: `.keys()` and `.items()` are among the members
    that are not detections, and the f-string method is another. The
    third row is a `Popen` rather than a `run` and the second a dict
    comprehension, so neither is the first row with a name changed.

    The comprehension's own conditions are a different path and are
    unaffected: `_CARRIED_FIELDS[ast.comprehension]` is `('ifs',)`, and
    this arm reads the iterable, never the conditions.
    """
    return (
        ('list comprehension over dict values', """import os
import subprocess
os.chdir(tmp)
go = [launcher.run(['python3', 'child.py'])
      for launcher in {'sp': subprocess}.values()]
""", 'for launcher in'),
        ('dict comprehension over dict values', """import os
import subprocess
os.chdir(tmp)
go = {k: launcher.run(['python3', 'child.py'])
      for k, launcher in {'sp': subprocess}.values()}
""", 'for k, launcher in'),
        ('list comprehension over a Popen', """import os
import subprocess
os.chdir(tmp)
go = [launcher.Popen(['python3'])
      for launcher in {'sp': subprocess}.values()]
""", 'for launcher in'),
        ('generator expression over dict keys', """import os
import subprocess
os.chdir(tmp)
go = (launcher.run(['python3'])
      for launcher in {'sp': subprocess}.keys())
""", 'for launcher in'),
        ('set comprehension over dict items', """import os
import subprocess
os.chdir(tmp)
go = {launcher.run(['python3'])
      for launcher in {'sp': subprocess}.items()}
""", 'for launcher in'),
        ('list comprehension over a list pop', """import os
import subprocess
os.chdir(tmp)
go = [launcher.run(['python3']) for launcher in [subprocess].pop()]
""", 'for launcher in'),
        ('list comprehension over an f-string method', """import os
import subprocess
os.chdir(tmp)
go = [launcher.run(['python3'])
      for launcher in f"{subprocess}".upper()]
""", 'for launcher in'),
    )


def _binding_position_cases():
    """A binding that puts the module in a container is a launcher.

    Three rows issue #1239 read as false positives, and none of them is
    one. They are refused by the BINDING arm and never reach the receiver
    descent: the assigned value is itself a call, and `_carried_parts`
    descends a call-based callee — `Attribute(attr, Call(...))` unwinds to
    the `Call` — down to the argument holding the module. By the issue's
    own criterion they are correct, and they reproduce as refused on
    `8babe1ae` with `_carried_parts` untouched, so releasing them is not
    this descent's business.

    The third row was filed under MUST STAY CLEAN on the argument that
    `dict().get(subprocess)` evaluates to the module and `.upper()` on a
    module is an `AttributeError`, so there is no launcher anywhere. That
    is a runtime fact the walk deliberately does not model — an attribute
    outside `_LAUNCH_READS` is a constant read to it — and it is the same
    mechanism as the first two rows, which are correctly refused. One
    family cannot be both, and `_carried_parts` is not this branch's to
    open. It is recorded here so the next reader does not re-open it
    against the receiver tables, and because the release of those tables
    is what made all three look wrong.
    """
    return (
        ('list then a method', """import os
import subprocess
os.chdir(tmp)
go = list(subprocess).count(1)
""", 'go = '),
        ('sorted then index', """import os
import subprocess
os.chdir(tmp)
go = sorted(subprocess).index(1)
""", 'go = '),
        ('module as a receiver argument', """import os
import subprocess
os.chdir(tmp)
go = dict().get(subprocess).upper()
""", 'go = '),
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


def test_a_loop_reading_a_launcher_out_of_its_iterable_is_refused(tmp):
    del tmp
    _one_verdict_each(_for_iterable_carrier_cases())


def test_a_comprehension_reading_a_launcher_is_refused(tmp):
    del tmp
    _one_verdict_each(_comprehension_iterable_cases())


def test_a_binding_putting_the_module_in_a_container_is_refused(tmp):
    del tmp
    _one_verdict_each(_binding_position_cases())


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
