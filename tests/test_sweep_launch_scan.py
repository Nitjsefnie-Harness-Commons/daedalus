#!/usr/bin/env python3
"""Branch controls for the mutation-sweep launch scan.

Every row is a synthetic module scanned by `sweep_launches`, so each
branch of the analyser is pinned by a fixture rather than by the two
real sites happening to spell things one way. Two groups, opposite on
purpose: the first must be CAUGHT, the second must NOT be, and the
second group is the arms
`tests/test_static_guard_regressions.py`'s
`test_a_sweep_launch_carries_no_wall_clock_bound` declares it does not
enforce.

Closing one of the second group's arms is an improvement, and it is also
a red test here on purpose: the row must move to the first group in the
same change that closes the arm, or the docstring's list and the scan's
behaviour drift apart silently. A row deleted without a closure is a
false green, not a fix.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _sweep_launch_scan import (  # noqa: E402
    SWEEP_ENTRY, sweep_launches)

HERE = 'tests/synthetic.py'
PROGRAM = f'suite.{SWEEP_ENTRY}(tmp)'


def _scan(body, head='import subprocess\n'):
    """The (launches, timed) of one synthetic module."""
    return sweep_launches(ast.parse(head + body), HERE)


def _line_of(body):
    """The 1-based line the deadline keyword sits on, base included."""
    return 1 + body[:body.index('timeout')].count('\n') + 1


def _bounded(label, body):
    """Assert `body` is one bounded sweep launch, and name its line."""
    line = _line_of(body)
    assert _scan(body) == ([(HERE, line)], [(HERE, line)]), (
        label, _scan(body), line)


def test_every_launcher_spelling_names_a_deadline_keyword(tmp):
    """The four launchers, in the import spellings that give them."""
    del tmp
    rows = (
        ('import subprocess\n', 'subprocess.{launcher}'),
        ('import subprocess as sp\n', 'sp.{launcher}'),
        ('from subprocess import run\n', 'run'),
        ('from subprocess import run as go\n', 'go'),
        ('from subprocess import *\n', '{launcher}'),
    )
    for head, callee in rows:
        for launcher in ('run', 'call', 'check_call', 'check_output'):
            name = (callee.format(launcher=launcher) if '{' in callee
                    else callee)
            _bounded(f'{head!r} {name}',
                     head + f'{name}(["-c", "{PROGRAM}"], timeout=120)\n')


def test_the_program_is_read_wherever_the_scope_binds_it(tmp):
    """A list is a program, and so is every way a scope spells one."""
    del tmp
    rows = (
        f'subprocess.run(["-c", "{PROGRAM}"], timeout=120)\n',
        f'p = ["-c", "{PROGRAM}"]\nsubprocess.run(p, timeout=120)\n',
        f'p = ("-c", "{PROGRAM}")\nsubprocess.run(p, timeout=120)\n',
        f'p = ["-c", f"{PROGRAM}"]\nsubprocess.run(p, timeout=120)\n',
        f'p = ["-c"]\np.append("{PROGRAM}")\n'
        'subprocess.run(p, timeout=120)\n',
        f'p = ["-c"]\np.extend(["{PROGRAM}"])\n'
        'subprocess.run(p, timeout=120)\n',
        f'a = b = ["-c", "{PROGRAM}"]\nsubprocess.run(a, timeout=120)\n',
        f'p = "print(1)"\np = ["-c", "{PROGRAM}"]\n'
        'subprocess.run(p, timeout=120)\n',
        f'subprocess.run(p, timeout=120)\np = ["-c", "{PROGRAM}"]\n',
        f'subprocess.run(args=["-c", "{PROGRAM}"], timeout=120)\n',
        f'subprocess.run("{PROGRAM}", timeout=120)\n',
        f'name = "{SWEEP_ENTRY}"\n'
        f'subprocess.run(["-c", f"{{name}}(tmp)"], timeout=120)\n',
        f'name = "{SWEEP_ENTRY}"\n'
        f'subprocess.run(f"{{name}}(tmp)", timeout=120)\n',
    )
    for index, body in enumerate(rows):
        _bounded(f'row {index}', body)


def test_each_scope_kind_binds_its_own_program(tmp):
    """Function, async function, class body and branch each read their own."""
    del tmp
    for header in ('def go():', 'async def go():', 'class C:', 'if True:'):
        body = (f'{header}\n    p = ["-c", "{PROGRAM}"]\n'
                '    subprocess.run(p, timeout=120)\n')
        _bounded(header, body)


def test_a_clean_module_reports_nothing(tmp):
    """The scan must not report a launch that runs something else."""
    del tmp
    for index, body in enumerate((
            'x = 1\n',
            'subprocess.run(["-c", "print(1)"], timeout=120)\n',
            'subprocess.run([1, 2], timeout=120)\n',
            'p = p\nsubprocess.run(p, timeout=120)\n',
            f'subprocess.run(["-c", "suite.{SWEEP_ENTRY[:-1]}q(tmp)"],\n'
            '                timeout=120)\n')):
        assert _scan(body) == ([], []), (f'row {index}', _scan(body))


def test_an_untimed_sweep_launch_is_reported_but_not_flagged(tmp):
    """The launch is found either way; only a `timeout` is a refusal."""
    del tmp
    body = f'subprocess.run(["-c", "{PROGRAM}"])\n'
    assert _scan(body) == ([(HERE, 2)], []), _scan(body)


def test_each_declared_blind_spot_is_really_missed(tmp):
    """The arms the guard's docstring does not claim, pinned as missed.

    Each row states what the scan does with it, which for a launch the
    scan still finds is a launch reported and a deadline not read.

    See this module's docstring: closing one of these is a change that
    moves the row into the caught group above.
    """
    del tmp
    rows = (
        ('a timeout unpacked from a mapping',
         f'kw = dict(timeout=120)\n'
         f'subprocess.run(["-c", "{PROGRAM}"], **kw)\n',
         [(HERE, 3)], []),
        ('a tuple target binding',
         f'a, b = ["-c"], "{PROGRAM}"\nsubprocess.run(a, timeout=120)\n',
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
        ('a program no readable binding reaches',
         f'parts = ["suite.", "{SWEEP_ENTRY}"]\n'
         'program = "".join(parts)\n'
         'subprocess.run(["-c", program], timeout=120)\n',
         [], []),
    )
    for label, body, launches, timed in rows:
        assert _scan(body) == (launches, timed), (label, _scan(body))


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals())),
                                  tmp_prefix='sweepscan_'))
