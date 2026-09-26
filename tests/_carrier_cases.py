"""Case tables for the forms the coverage guard's carrier walk classifies.

Not a suite itself — run_tests.py only loads `test_*.py`. The rows here
state an operation rather than a spelling, so the suite that drives them
holds the driver and these hold the cases, exactly as
tests/_binding_assertions.py does for the real-module plant.
"""
import ast
import warnings


def _value_preserving_cases():
    """Forms whose sub-value reaches the binding unchanged.

    Every row is a shape the carrier walk read as one opaque leaf, so a
    launcher inside it was judged by no arm. The rows that only parse
    inside a coroutine or a generator carry their own enclosing block.
    """
    return (
        ('conditional expression', """import operator
import subprocess
operator.call(subprocess.run if flag else None, 1)
""", 'operator.call('),
        ('boolean and', """import operator
import subprocess
operator.call(flag and subprocess.run, 1)
""", 'operator.call('),
        ('boolean or', """import operator
import subprocess
operator.call(flag or subprocess.run, 1)
""", 'operator.call('),
        ('list comprehension', """import operator
import subprocess
operator.call([subprocess.run for _ in xs], 1)
""", 'operator.call('),
        ('set comprehension', """import operator
import subprocess
operator.call({subprocess.run for _ in xs}, 1)
""", 'operator.call('),
        ('dict comprehension value', """import operator
import subprocess
operator.call({k: subprocess.run for k in xs}, 1)
""", 'operator.call('),
        ('dict comprehension key', """import operator
import subprocess
operator.call({subprocess.run: k for k in xs}, 1)
""", 'operator.call('),
        ('generator expression', """import operator
import subprocess
operator.call((subprocess.run for _ in xs), 1)
""", 'operator.call('),
        ('lambda body', """import operator
import subprocess
operator.call(lambda: subprocess.run, 1)
""", 'operator.call('),
        ('f-string', """import operator
import subprocess
operator.call(f"{subprocess.run}", 1)
""", 'operator.call('),
        ('f-string with a format spec', """import operator
import subprocess
operator.call(f"{subprocess.run!r:>{width}}", 1)
""", 'operator.call('),
        ('f-string format spec', """import operator
import subprocess
operator.call(f"{value:{subprocess.run}}", 1)
""", 'operator.call('),
        ('slice lower bound', """import operator
import subprocess
operator.call(d[subprocess.run:1], 1)
""", 'operator.call('),
        ('slice step', """import operator
import subprocess
operator.call(d[::subprocess.run], 1)
""", 'operator.call('),
        ('slice upper bound', """import operator
import subprocess
operator.call(d[1:subprocess.run], 1)
""", 'operator.call('),
        ('awaited value', """import operator
import subprocess
async def go():
    operator.call(await subprocess.run, 1)
""", 'operator.call('),
        ('walrus value', """import operator
import subprocess
operator.call((held := subprocess.run), 1)
""", 'operator.call('),
        ('yielded value', """import operator
import subprocess
def go():
    operator.call((yield subprocess.run), 1)
""", 'operator.call('),
        ('value yielded from', """import operator
import subprocess
def go():
    operator.call((yield from subprocess.run), 1)
""", 'operator.call('),
        ('assigned conditional', """import subprocess
go = subprocess.run if flag else None
""", 'go = '),
        ('assigned comprehension', """import subprocess
go = [subprocess.run for _ in xs]
""", 'go = '),
        ('decorated conditional', """import subprocess
@(subprocess.run if flag else None)
def go():
    pass
""", '@('),
        ('default lambda', """import subprocess
def go(cb=lambda: subprocess.run):
    pass
""", 'def go(cb='),
    )


def _receiver_carrier_cases():
    """Callees that carry a launcher rather than name one.

    The gate that reached these is the one this arm is about: the
    receiver position is where a launch method is read off what is
    carried, so a module reaching it is a launcher there, and a form
    that only CONTAINS one has to be opened before that is visible.
    """
    return (
        ('conditional receiver', """import os
import subprocess
os.chdir(tmp)
(subprocess if flag else None).run(['python3', 'child.py'])
""", '.run('),
        ('conditional callee', """import os
import subprocess
os.chdir(tmp)
(subprocess.run if flag else None)(['python3', 'child.py'])
""", 'else None)('),
        ('boolean receiver', """import os
import subprocess
os.chdir(tmp)
(flag and subprocess).run(['python3', 'child.py'])
""", '.run('),
        ('boolean callee', """import os
import subprocess
os.chdir(tmp)
(flag and subprocess.run)(['python3', 'child.py'])
""", 'subprocess.run)('),
        ('list comprehension receiver', """import os
import subprocess
os.chdir(tmp)
[subprocess][0].run(['python3', 'child.py'])
""", '[subprocess][0]'),
        ('set comprehension receiver', """import os
import subprocess
os.chdir(tmp)
({subprocess} for _ in xs).__next__().run(
    ['python3', 'child.py'])
""", '{subprocess} for _ in xs)'),
        ('dict comprehension receiver', """import os
import subprocess
os.chdir(tmp)
{k: subprocess for k in xs}.get(k).run(
    ['python3', 'child.py'])
""", '{k: subprocess for k in xs}'),
        ('lambda receiver', """import os
import subprocess
os.chdir(tmp)
(lambda: subprocess).run(['python3', 'child.py'])
""", '(lambda: subprocess)'),
        ('f-string receiver', """import os
import subprocess
os.chdir(tmp)
f"{subprocess}".run(['python3', 'child.py'])
""", 'f"{subprocess}"'),
        ('awaited receiver', """import os
import subprocess
async def go():
    (await subprocess).run(['python3', 'child.py'])
""", '(await subprocess)'),
        ('yielded receiver', """import os
import subprocess
def go():
    (yield subprocess).run(['python3', 'child.py'])
""", '(yield subprocess)'),
        ('walrus receiver', """import os
import subprocess
os.chdir(tmp)
(held := subprocess).run(['python3', 'child.py'])
""", '(held := subprocess)'),
    )


def _receiver_atom_cases():
    """Callees that only name a launcher, and the gate they must keep.

    The second row is the one that decides the gate: the receiver
    position reads a launch method off what it carries, so a bare module
    reaching the walk would be refused here — and a direct launch is
    exactly that. The first row is accepted by a rule that runs before
    this arm, so it is recorded rather than load-bearing.
    """
    return (
        ('direct launch', """import subprocess
subprocess.run(['python3', 'child.py'])
"""),
        ('direct launch with a cwd', """import subprocess
from _repo import ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
"""),
        ('ordinary callee', """import json
json.loads(text)
"""),
        ('ordinary attribute chain', """import json
json.JSONDecoder().decode(text)
"""),
        ('module alias receiver', """import subprocess
import other
other.run(['python3', 'child.py'])
"""),
    )


def _callee_chain_cases():
    """A launcher used as a callee, past a call that declares its own cwd.

    Both calls carry `cwd=`, so neither reaches this arm's receiver: the
    verdict is the call arm's descent from the outer call into the inner
    one it is built on, and nothing else in the guard reaches it.
    """
    return (
        ('declared partial, declared outer', """import operator
import subprocess
from functools import partial
from _repo import ROOT
operator.call(
    partial(
        subprocess.run,
        cwd=ROOT)(
        cwd=ROOT), 1)
""", 'operator.call('),
    )


def _comprehension_cases():
    """The two halves of a comprehension, in a position the guard judges.

    The walk opens the element a comprehension repeats; the iterable is
    the comprehension arm's own business, which is what makes the walk's
    not opening it a fact rather than a gap.
    """
    return (
        ('element carries a launcher', """import operator
import subprocess
operator.call([subprocess.run for x in xs], 1)
""", 'operator.call('),
        ('iterable carries a launcher', """import operator
import subprocess
operator.call([x for x in [subprocess.run]], 1)
""", 'operator.call('),
        ('element carries a launcher, bound', """import subprocess
go = [subprocess.run for x in xs]
""", 'go = '),
        ('iterable carries a launcher, bound', """import subprocess
go = [x for x in [subprocess.run]]
""", 'go = '),
    )


def _transforming_cases():
    """Forms that build a new value, so a launcher in one is not carried."""
    return (
        ('sum of a launcher', """import operator
import subprocess
operator.call(subprocess.run + 1, 1)
"""),
        ('negated launcher', """import operator
import subprocess
operator.call(-subprocess.run, 1)
"""),
        ('compared launcher', """import operator
import subprocess
operator.call(subprocess.run < 1, 1)
"""),
        ('assigned sum', """import subprocess
go = subprocess.run + 1
"""),
        ('sum in a comprehension element', """import operator
import subprocess
operator.call([subprocess.run + 1 for _ in xs], 1)
"""),
    )


def _grammar_forms():
    """Every concrete expression form this interpreter's grammar names.

    A deprecated spelling such as `ast.Ellipsis` builds a node of the
    form that replaced it, so the instance decides the identity rather
    than the attribute name.
    """
    forms = set()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        for name in dir(ast):
            candidate = getattr(ast, name)
            if (isinstance(candidate, type)
                    and issubclass(candidate, ast.expr)
                    and candidate is not ast.expr):
                forms.add(type(_bare_form(candidate)))
    return forms


_LIST_FIELDS = frozenset({'args', 'elts', 'keywords', 'keys', 'values'})
_EMPTY = ast.Constant.__new__(ast.Constant)
_EMPTY.value = None
_EMPTY.kind = None


def _bare_form(form):
    """An instance of a form with every field present and empty.

    The constructor is skipped because it warns about the fields it
    leaves unset, and every one of them is unset here on purpose. The
    fields the walk iterates are emptied and the rest carry a constant
    leaf, so a form reaches its own arm and every arm it enters has
    something to walk.
    """
    instance = form.__new__(form)
    for field in form._fields:
        setattr(instance, field, [] if field in _LIST_FIELDS else _EMPTY)
    return instance


# The forms `_carried_parts` opens with its own arms, ahead of the table.
_BESPOKE = (ast.Call, ast.Tuple, ast.List, ast.Set, ast.Dict,
            ast.Subscript, ast.Starred)
