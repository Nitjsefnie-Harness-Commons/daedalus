#!/usr/bin/env python3
"""The PLANT the launch-census evidence suites are built from.

A plant is a module whose receiver key is literal-bound in `__init__`, whose
rebinding writes a **real** `subprocess.Popen`, and whose sink is
`self.handles.wait(timeout)` on the parameter route. That shape is the only
one where a census verdict and a runtime verdict are answers to the same
question: the census reads a key it believes is a container, and the runtime
bounds an actual child through it.

Two disciplines this module exists to enforce, both learned from plants on
this branch that reported a verdict for the wrong reason:

- **No launch in the plant.** Writing `subprocess.Popen([...])` inline puts a
  launch in the tree, the census's launch arm fires before any poison is
  consulted, and every row refuses for a reason unrelated to what is under
  test. The real child is behind a local `spawn()`.
- **A row that discharges without reaping anything is not a false green.**
  `runtime()` reports what actually happened, so a plant that cannot run
  (`NameError`, `TypeError`, a scoped comprehension target) is labelled
  rather than counted as proved.

Rows whose runtime is vacuous for a reason no fix can reach --
`setattr(**kw())` and `setattr(self)`, which raise `TypeError` before any
write -- are listed in `CENSUS_ONLY` and are proven by the census alone.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _launch_census import _faults  # noqa: E402

# The child sleeps longer than the deadline, so a row whose deadline reaches
# it TIMES OUT. The margin is two seconds against a one-second bound, which
# is what makes `BOUNDED` unambiguous rather than a race.
_SLEEP = '2'

_COMMON = (
    'import subprocess\n'
    f'def spawn():\n'
    f'    return subprocess.Popen(["sleep", "{_SLEEP}"])\n'
    'class CM:\n'
    '    def __init__(self, v):\n        self.v = v\n'
    '    def __enter__(self):\n        return self.v\n'
    '    def __exit__(self, *a):\n        return False\n'
    'class E(Exception):\n    pass\n'
)

# Helpers that make a call RESULT a real alias, so the runtime leg can bound
# a real child: `same(v) -> v` is an identity, `f(v) -> v` likewise, and
# `CM(v).__enter__` returns the receiver. `CM(self)` alone is NOT an alias --
# it binds a wrapper -- which is a plant that reports for the wrong reason.
ALIAS_HELPERS = (
    'def same(v):\n    return v\n'
    'def f(v):\n    return v\n'
    'def helper(v):\n    return None\n'
    'def extra():\n    return ("handles", spawn())\n'
    'def kw():\n    return {}\n'
    'def args_for(v):\n    return (v, "handles", spawn())\n'
    'def a():\n    return ("handles",)\n'
    'def b():\n    return (spawn(),)\n'
)

# Rows whose RUNTIME leg is vacuous for a reason no rule can change: these
# shapes raise TypeError before any write happens, so no child can be reaped
# and the census verdict is the only evidence available.
CENSUS_ONLY = frozenset({'setattr-kwargs-only', 'setattr-no-args'})


def plant(statements, helpers=ALIAS_HELPERS, receiver='handles'):
    """The planted module: literal receiver, real child, deadline sink."""
    return (_COMMON + helpers
            + '\nclass R:\n'
            + '    def __init__(self):\n'
            + f'        self.{receiver} = []\n'
            + '\n'
            + '    def run(self, timeout):\n'
            + ''.join('        ' + line + '\n'
                      for line in statements.splitlines())
            + '        try:\n'
            + f'            self.{receiver}.wait(timeout)\n'
            + '        except subprocess.TimeoutExpired:\n'
            + f'            self.{receiver}.kill()\n'
            + f'            self.{receiver}.wait()\n'
            + '            return "BOUNDED"\n'
            + f'        if hasattr(self.{receiver}, "kill"):\n'
            + f'            self.{receiver}.kill()\n'
            + f'            self.{receiver}.wait()\n'
            + '        return "NOT BOUNDED"\n')


def _plant_census(source):
    """`('REFUSE', rows)` or `('DISCHARGE', [])` for a planted module."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f'SYNTAX({exc.msg})', []
    forced = frozenset(
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    rows = sorted((row[1], row[2])
                  for row in _faults('planted.py', tree, forced))
    return ('REFUSE' if rows else 'DISCHARGE'), rows


def _plant_runtime(source):
    """What actually happened to the live child.

    `BOUNDED` is the only value that makes a DISCHARGE a false green.
    Anything else -- a clean reap, or an exception -- means the row is
    reported as vacuous rather than as proved.
    """
    namespace = {}
    try:
        # The runtime leg IS an exec: the plant is a module built as
        # text so the census and the runtime read the same source.
        # pylint: disable-next=exec-used
        exec(compile(source, '<plant>', 'exec'), namespace)  # noqa: S102
        return namespace['R']().run(1)
    except SyntaxError as exc:
        return f'SYNTAX({exc.msg})'
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


def _plant_verdicts(statements, **kwargs):
    """`(census, runtime)` for one plant, with the child always reaped."""
    source = plant(statements, **kwargs)
    verdict, rows = _plant_census(source)
    return verdict, _plant_runtime(source), rows
