#!/usr/bin/env python3
"""Branch controls for the mutation-sweep launch scan.

Every row is a synthetic module scanned by `sweep_launches`, so each
branch of the analyser is pinned by a fixture rather than by the two
real sites happening to spell things one way. Two groups, opposite on
purpose: the first must be CAUGHT, the second must NOT be. The second
group is the arms of `tests/test_static_guard_regressions.py`'s
`test_a_sweep_launch_carries_no_wall_clock_bound` that the ANALYSER can
be shown not to enforce — the ones about a deadline it cannot see at
all, rather than about how the caller chooses which files to hand it.

Closing one of the second group's arms is an improvement, and it is also
a red test here on purpose: the row must move to the first group in the
same change that closes the arm, or the docstring's list and the scan's
behaviour drift apart silently. A row deleted without a closure is a
false green, not a fix.

The caught group also holds one row that is a declared FALSE RED, marked
in place and named arm 11: the match capture that reads as the sweep a
launch is not running. It is asserted refused on purpose, so it is a
third kind of row, not a missed one and not a caught one.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _sweep_launch_scan import (  # noqa: E402
    SWEEP_ENTRY, sweep_launches)

HERE = 'tests/synthetic.py'
PROGRAM = f'suite.{SWEEP_ENTRY}(tmp)'


def _scan(body, head='import subprocess\n'):
    return sweep_launches(ast.parse(head + body), HERE)


def _line_of(body):
    """The 1-based line the deadline keyword sits on, base included."""
    return 1 + body[:body.index('timeout')].count('\n') + 1


def _bounded(label, body):
    line = _line_of(body)
    assert _scan(body) == ([(HERE, line)], [(HERE, line)]), (
        label, _scan(body), line)


def test_every_launcher_spelling_names_a_deadline_keyword(tmp):
    del tmp
    rows = (
        ('import subprocess\n', 'subprocess.{launcher}'),
        ('import subprocess as sp\n', 'sp.{launcher}'),
        ('from subprocess import run\n', 'run'),
        ('from subprocess import run as go\n', 'go'),
        ('from subprocess import *\n', '{launcher}'),
        ('import subprocess.run\n', 'subprocess.{launcher}'),
        ('import subprocess.run as sr\n', 'sr'),
    )
    for head, callee in rows:
        for launcher in ('run', 'call', 'check_call', 'check_output'):
            name = (callee.format(launcher=launcher) if '{' in callee
                    else callee)
            _bounded(f'{head!r} {name}',
                     head + f'{name}(["-c", "{PROGRAM}"], timeout=120)\n')


def test_the_program_is_read_wherever_the_scope_binds_it(tmp):
    del tmp
    rows = (
        f'subprocess.run(["-c", "{PROGRAM}"], timeout=120)\n',
        f'p = ["-c", "{PROGRAM}"]\nsubprocess.run(p, timeout=120)\n',
        f'p = ("-c", "{PROGRAM}")\nsubprocess.run(p, timeout=120)\n',
        f'p = ["-c", f"{PROGRAM}"]\nsubprocess.run(p, timeout=120)\n',
        f'p = "a" + f"suite.{SWEEP_ENTRY}()"\n'
        'subprocess.run([p], timeout=120)\n',
        f'p = ["-c"]\np.append("{PROGRAM}")\n'
        'subprocess.run(p, timeout=120)\n',
        f'p = ["-c"]\np.extend(["{PROGRAM}"])\n'
        'subprocess.run(p, timeout=120)\n',
        f'a = b = ["-c", "{PROGRAM}"]\nsubprocess.run(a, timeout=120)\n',
        f'p = "print(1)"\np = ["-c", "{PROGRAM}"]\n'
        'subprocess.run(p, timeout=120)\n',
        f'subprocess.run(args=["-c", "{PROGRAM}"], timeout=120)\n',
        f'subprocess.run("{PROGRAM}", timeout=120)\n',
        f'p = ["-c"]\np += ["{PROGRAM}"]\n'
        'subprocess.run(p, timeout=120)\n',
        f'p: list = ["-c", "{PROGRAM}"]\n'
        'subprocess.run(p, timeout=120)\n',
        f'if p := ["-c", "{PROGRAM}"]:\n    pass\n'
        'subprocess.run(p, timeout=120)\n',
        f'for p in [["-c", "{PROGRAM}"]]:\n'
        '    subprocess.run(p, timeout=120)\n',
        'async def go(src):\n'
        f'    async for p in [["-c", "{PROGRAM}"]]:\n'
        '        subprocess.run(p, timeout=120)\n',
        f'name = "{SWEEP_ENTRY}"\n'
        f'subprocess.run(["-c", f"{{name}}(tmp)"], timeout=120)\n',
        f'name = "{SWEEP_ENTRY}"\n'
        f'subprocess.run(f"{{name}}(tmp)", timeout=120)\n',
        f'p = ["-c", "{PROGRAM}"]; subprocess.run(p, timeout=120)\n',
        # Two bindings of one name on ONE line, the sweep last. Document
        # order is what decides this; max() by line returns the first of
        # two entries sharing a lineno and would read the `print(1)`.
        f'p = "print(1)"; p = ["-c", "{PROGRAM}"]\n'
        'subprocess.run([p], timeout=120)\n',
        f'match ["-c", "{PROGRAM}"]:\n    case ["-c", program]:\n'
        '        subprocess.run([program], timeout=120)\n',
        f'match ["-c", "{PROGRAM}"]:\n    case _ as program:\n'
        '        subprocess.run([program], timeout=120)\n',
        f'match ["-c", "{PROGRAM}"]:\n    case ["-c", *rest]:\n'
        '        subprocess.run([rest], timeout=120)\n',
        f'match {{"{PROGRAM}": rest}}:\n    case {{**rest}}:\n'
        '        subprocess.run([rest], timeout=120)\n',
        # The DECLARED FALSE RED, asserted refused. A capture binds to the
        # whole match subject, so a capture spent on a launch the sweep is
        # not in still reads as the sweep and is refused. This row pins
        # that cost: it is the reason arm 11 exists, and bounding the
        # capture would be a second call, not a fix to this one.
        f'cmd = ["-c", "{PROGRAM}", host]\n'
        'match cmd:\n'
        '    case [_, _, h]:\n'
        '        subprocess.run(["ping", h], timeout=5)\n',
    )
    for index, body in enumerate(rows):
        _bounded(f'row {index}', body)


def test_a_false_red_the_name_only_reaches_is_pinned(tmp):
    """The entry name in an argument that is not the program.

    The argv here is a ping; the name is a value the call merely
    carries, and the scan reads the NAME rather than the call it
    belongs to, so it refuses a correct launch. That is the declared
    cost, and this row is the second spelling of it — the first, a
    program that merely quotes the name, sits in the caught group above.

    The refusal is at the line the call OPENS on, not the line the
    `timeout` keyword sits on: `ast.Call.lineno` is the opening line,
    which is the multi-line case the guard's docstring names.
    """
    del tmp
    body = (f'subprocess.run(["ping", host], env={{"NOTE": "{PROGRAM}"}},\n'
            '                timeout=5)\n')
    assert _scan(body) == ([(HERE, 2)], [(HERE, 2)]), _scan(body)
    assert _line_of(body) == 3, 'the deadline sits on the second line'


def test_each_scope_kind_binds_its_own_program(tmp):
    """Each member of `_SCOPE_NODES` reads its own and hides from outside.

    The second half is the control: it holds only while that member is
    really a scope. Deleting any one of them from `_SCOPE_NODES` lets
    its bindings leak to the launch beside it and turns the row red,
    which is the leak the reviewer's `class B` plant is.
    """
    del tmp
    for header in ('def go():', 'async def go():', 'class C:'):
        _bounded(header, f'{header}\n    p = ["-c", "{PROGRAM}"]\n'
                         '    subprocess.run(p, timeout=120)\n')
    for header, body in (
            ('def go():', f'def go():\n    argv = ["-c", "{PROGRAM}"]\n'),
            ('async def go():',
             f'async def go():\n    argv = ["-c", "{PROGRAM}"]\n'),
            ('class C:', f'class A: pass\n'
                         f'class C: argv = ["-c", "{PROGRAM}"]\n'),
            ('lambda',
             f'go = lambda: ["-c", "{PROGRAM}"]\n')):
        outside = ('subprocess.run(go(), timeout=5)\n' if header == 'lambda'
                   else 'subprocess.run(argv, timeout=5)\n')
        assert _scan(body + outside) == ([], []), (
            f'{header} leaked', _scan(body + outside))


def test_a_branch_shares_its_enclosing_scope(tmp):
    """`ast.If` is not a scope, so a binding inside one is still read.

    Both halves turn red together if `ast.If` is added to
    `_SCOPE_NODES`, so the pair pins the membership rather than testing
    opposite directions: adding it makes the branch its own scope, the
    binding stops being visible to the launch beside it, and the `def`
    nested in the branch stops being the thing that hides it.
    """
    del tmp
    _bounded('a binding inside a branch',
             f'if True:\n    p = ["-c", "{PROGRAM}"]\n'
             'subprocess.run(p, timeout=120)\n')
    body = (f'if True:\n    def inner():\n        p = ["-c", "{PROGRAM}"]\n'
            'subprocess.run(p, timeout=120)\n')
    assert _scan(body) == ([], []), _scan(body)


def test_a_clean_module_reports_nothing(tmp):
    del tmp
    for index, body in enumerate((
            'x = 1\n',
            'subprocess.run(["-c", "print(1)"], timeout=120)\n',
            'subprocess.run([1, 2], timeout=120)\n',
            'p = p\nsubprocess.run(p, timeout=120)\n',
            f'subprocess.run(["-c", "suite.{SWEEP_ENTRY[:-1]}q(tmp)"],\n'
            '                timeout=120)\n')):
        assert _scan(body) == ([], []), (f'row {index}', _scan(body))


def test_a_name_is_read_as_the_binding_in_force_at_its_line(tmp):
    """The LAST binding at or before a launch, never the join of them all.

    Three directions, and the first two are the ones that used to red a
    correct launch: a name the scope reused for an unrelated program
    afterwards, and one it reused for an unrelated program in between.
    """
    del tmp
    reused_after = (f'program = ["-c", "{PROGRAM}"]\n'
                    'program = "print(1)"\n'
                    'subprocess.run([program], timeout=5)\n'
                    f'program = ["-c", "{PROGRAM}"]\n'
                    'subprocess.run([program])\n')
    assert _scan(reused_after) == ([(HERE, 6)], []), _scan(reused_after)
    swept_after = (f'program = "print(1)"\n'
                   'subprocess.run([program], timeout=5)\n'
                   f'program = ["-c", "{PROGRAM}"]\n'
                   'subprocess.run([program])\n')
    assert _scan(swept_after) == ([(HERE, 5)], []), _scan(swept_after)
    _bounded('rebound to the sweep last',
             'program = ["-c", "print(1)"]\n'
             f'program = ["-c", "{PROGRAM}"]\n'
             'subprocess.run([program], timeout=5)\n')
    # A binding inside a COMPOUND statement is appended out of source
    # order, so these two are what separate last-by-line from
    # last-appended. The first is a false green without the sort; the
    # second is a false red with it.
    _bounded('a branch binding, then the sweep flattened after it',
             'if flag:\n    program = "print(3)"\n'
             f'program = ["-c", "{PROGRAM}"]\n'
             'subprocess.run([program], timeout=5)\n')
    for label, body in (
            ('at module level',
             'if flag:\n    program = "print(3)"\n'
             'program = ["-c", "print(1)"]\n'
             'subprocess.run([program], timeout=5)\n'),
            ('inside a function',
             'def go():\n    if flag:\n        program = "print(3)"\n'
             '    program = ["-c", "print(1)"]\n'
             '    subprocess.run([program], timeout=5)\n'),
            ('inside a try/except',
             'try:\n    if flag:\n        program = "print(3)"\n'
             '    program = ["-c", "print(1)"]\n'
             '    subprocess.run([program], timeout=5)\n'
             'except OSError:\n    pass\n')):
        assert _scan(body) == ([], []), (label, _scan(body))


def test_an_untimed_sweep_launch_is_reported_but_not_flagged(tmp):
    del tmp
    body = f'subprocess.run(["-c", "{PROGRAM}"])\n'
    assert _scan(body) == ([(HERE, 2)], []), _scan(body)


# The declared miss set, frozen. A row in the table below is a claim
# about the analyser and can only red when the analyser IMPROVES,
# so it is not a control against this branch's defect class. This
# constant is the control over the table: a route added, dropped or
# renamed is a single edit HERE, visible in the diff, rather than a
# row that quietly joins or leaves nineteen. The disclosure a
# reader debugging `assert not timed` consults is the guard's own
# docstring, not this list.
_DECLARED_MISSES = frozenset((
    'a timeout unpacked from a mapping',
    'a program the scope only defines afterwards',
    'an argv grown afterwards',
    'a program named by a with/as target',
    'a program named by an except/as target',
    'a program named by an except*/as target',
    'a comprehension target, which is its own scope',
    'a lambda default parameter',
    'a global name',
    'a nonlocal name',
    'a subscript target',
    'an argv grown by insert',
    'a Popen whose wait carries the deadline',
    'a program read in a nested scope',
    'a program read in a lambda body',
    'a program that only mentions the entry name',
    'a deadline spelled without the word timeout',
    'a timeout defaulted inside a helper',
    'a program no readable binding reaches',
))


def test_each_declared_blind_spot_is_really_missed(tmp):
    """The analyser arms the guard's docstring does not claim.

    Each row states what the scan does with it, which for a launch the
    scan still finds is a launch reported and a deadline not read.

    See this module's docstring: closing one of these is a change that
    moves the row into the caught group above.
    """
    del tmp
    rows = (
        ('a timeout unpacked from a mapping',
         'kw = dict(timeout=120)\n'
         f'subprocess.run(["-c", "{PROGRAM}"], **kw)\n',
         [(HERE, 3)], []),
        ('a program the scope only defines afterwards',
         f'subprocess.run(p, timeout=120)\np = ["-c", "{PROGRAM}"]\n',
         [], []),
        ('an argv grown afterwards',
         f'p = ["-c"]\nsubprocess.run(p, timeout=120)\n'
         f'p.append("{PROGRAM}")\n',
         [], []),
        ('a program named by a with/as target',
         'with open("x") as p:\n    subprocess.run(p, timeout=120)\n',
         [], []),
        ('a program named by an except/as target',
         'try:\n    pass\nexcept ValueError as p:\n'
         '    subprocess.run(p, timeout=120)\n',
         [], []),
        ('a program named by an except*/as target',
         'try:\n    pass\nexcept* ValueError as p:\n'
         '    subprocess.run(p, timeout=120)\n',
         [], []),
        ('a comprehension target, which is its own scope',
         f'rows = [q for q in [["-c", "{PROGRAM}"]]]\n'
         'subprocess.run(q, timeout=120)\n',
         [], []),
        ('a lambda default parameter',
         f'go = lambda p=["-c", "{PROGRAM}"]: '
         'subprocess.run(p, timeout=120)\n',
         [], []),
        ('a global name',
         f'program = ["-c", "{PROGRAM}"]\n'
         'def go():\n    global program\n'
         '    subprocess.run(program, timeout=120)\n',
         [], []),
        ('a nonlocal name',
         'def outer():\n'
         f'    program = ["-c", "{PROGRAM}"]\n'
         '    def inner():\n        nonlocal program\n'
         '        subprocess.run(program, timeout=120)\n    inner()\n',
         [], []),
        ('a subscript target',
         'argv = []\n'
         f'argv[0] = ["-c", "{PROGRAM}"]\n'
         'subprocess.run(argv, timeout=120)\n',
         [], []),
        ('an argv grown by insert',
         f'p = ["-c"]\np.insert(0, "{PROGRAM}")\n'
         'subprocess.run(p, timeout=120)\n',
         [], []),
        ('a Popen whose wait carries the deadline',
         f'p = subprocess.Popen(["-c", "{PROGRAM}"])\np.wait(timeout=120)\n',
         [], []),
        ('a program read in a nested scope',
         f'p = ["-c", "{PROGRAM}"]\n'
         'def inner():\n    subprocess.run(p, timeout=120)\n',
         [], []),
        ('a program read in a lambda body',
         f'p = ["-c", "{PROGRAM}"]\n'
         'go = lambda: subprocess.run(p, timeout=120)\n',
         [], []),
        ('a program that only mentions the entry name',
         f'subprocess.run(["-c", "assert \'{SWEEP_ENTRY}\' in text"],\n'
         '                timeout=120)\n',
         [(HERE, 2)], [(HERE, 2)]),
        ('a deadline spelled without the word timeout',
         'import signal\nsignal.alarm(120)\n'
         f'subprocess.run(["-c", "{PROGRAM}"])\n',
         [(HERE, 4)], []),
        ('a timeout defaulted inside a helper',
         'def go(argv, timeout=120):\n'
         '    return subprocess.run(argv)\n'
         f'go(["-c", "{PROGRAM}"])\n',
         [], []),
        ('a program no readable binding reaches',
         f'parts = ["suite.", "{SWEEP_ENTRY}"]\n'
         'program = "".join(parts)\n'
         'subprocess.run(["-c", program], timeout=120)\n',
         [], []),
    )
    assert {label for label, _, _, _ in rows} == _DECLARED_MISSES
    for label, body, launches, timed in rows:
        assert _scan(body) == (launches, timed), (label, _scan(body))


def _guard_docstring():
    """The sweep-bound guard's own docstring, read from the tree."""
    text = (Path(__file__).parent / 'test_static_guard_regressions.py'
            ).read_text(encoding='utf-8')
    name = 'test_a_sweep_launch_carries_no_wall_clock_bound'
    node = next(n for n in ast.parse(text).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_docstring(node) or ''


def _forms_beside(docstring, marker, stop):
    """The backticked forms the docstring lists inside one clause."""
    clause = docstring[docstring.index(marker):docstring.index(stop)]
    return frozenset(re.findall(r'`([^`]+)`', clause))


def test_the_read_and_unread_binding_lists_are_disjoint(tmp):
    """No form is claimed both read and unread by the guard's docstring.

    The two lists are complementary by design and nothing tested that,
    which is the exposure that let `case {**p}` through four rounds: the
    read list said "a match capture" and no control checked the analyser
    read every way one is written. A form on both sides makes the
    disclosure self-contradicting with nothing failing.
    """
    del tmp
    docstring = _guard_docstring()
    read = _forms_beside(docstring, 'Binding forms the scan READS:',
                         'Binding forms it does NOT read:')
    unread = _forms_beside(docstring, 'Binding forms it does NOT read:',
                           'Three cost arms')
    assert read, 'the read list is empty'
    assert unread, 'the unread list is empty'
    assert not read & unread, sorted(read & unread)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals())),
                                  tmp_prefix='sweepscan_'))
