"""Case tables for the forms the coverage guard's carrier walk classifies.

Not a suite itself — run_tests.py only loads `test_*.py`. The rows here
state an operation rather than a spelling, so the suite that drives them
holds the driver and these hold the cases, exactly as
tests/_binding_assertions.py does for the real-module plant.
"""
import ast
import warnings


# PEP 750's template-string forms are a 3.14 addition, and a `t"..."` is
# 3.14 syntax, so an older parser rejects the source outright. The rows are
# therefore built only where the parser is the one that will read them, and
# they are absent rather than skipped: `_refused` and `_accepted` both walk
# a whole table, so a row that could not compile would be a false green and
# an early return would drop the rows beside it. The registration of the
# forms themselves is exercised on every version by the stand-in control in
# tests/test_coverage_unfollowable_forms.py, which installs them on `ast`
# and re-imports the walk.
_TEMPLATE_CARRIERS = ((
    ('template string argument', """import operator
import subprocess
operator.call(t"{subprocess.run}", 1)
""", 'operator.call('),
    ('template string bound', """import subprocess
go = t"{subprocess.run}"
""", 'go = '),
    ('template string format spec', """import operator
import subprocess
operator.call(t"{value:{subprocess.run}}", 1)
""", 'operator.call('),
) if hasattr(ast, 'TemplateStr') else ())

_TEMPLATE_FREES = (
    ('template string, nothing carried', """import subprocess
go = t"{name}"
"""),
    ('template string, plain text', """import operator
operator.call(t"plain", 1)
"""),
    ('template string with a width', """import operator
operator.call(t"{value:>{width}}", 1)
"""),
) if hasattr(ast, 'TemplateStr') else ()


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
        ('conditional orelse', """import operator
import subprocess
operator.call(None if flag else subprocess.run, 1)
""", 'operator.call('),
        ('conditional test', """import operator
import subprocess
operator.call(plain() if subprocess.run else other(), 1)
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
        # The leaf the walk never descends into, read in its binding
        # position: an attribute naming a launch method carries whatever
        # it is read off, and the receiver here is a subscript and then a
        # further attribute.
        ('subscript receiver bound', """import os
import subprocess
os.chdir(tmp)
go = d[subprocess].run
""", 'go = '),
        ('launch method bound as a call attribute', """import os
import subprocess
os.chdir(tmp)
go = subprocess.run.__call__
""", 'go = '),
    ) + _TEMPLATE_CARRIERS


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
        ('subscript index receiver', """import os
import subprocess
os.chdir(tmp)
d[subprocess].run(['python3', 'child.py'])
""", 'd[subprocess]'),
        ('slice bound receiver', """import os
import subprocess
os.chdir(tmp)
d[0:subprocess].run(['python3', 'child.py'])
""", 'd[0:subprocess]'),
        ('slice step receiver', """import os
import subprocess
os.chdir(tmp)
d[::subprocess].run(['python3', 'child.py'])
""", 'd[::subprocess]'),
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
        ('subscript index that carries nothing', """import os
import subprocess
os.chdir(tmp)
d[0].run(['python3', 'child.py'])
"""),
        ('subscript bounds that carry nothing', """import os
import subprocess
os.chdir(tmp)
d[0:1].run(['python3', 'child.py'])
"""),
        ('subscript tuple that carries nothing', """import os
import subprocess
os.chdir(tmp)
d[0, 1].run(['python3', 'child.py'])
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
        # The outer callee is a `__call__` the descent excludes, because a
        # direct launch is a resolved one. The attribute inside the chain
        # is a launch method read off a launcher, and that read is what
        # this row refuses.
        ('launch method inside a callee chain', """import os
import subprocess
os.chdir(tmp)
subprocess.run.__call__(['python3', 'child.py'])
""", 'subprocess.run.__call__('),
    )


def _comprehension_cases():
    """Every position of a comprehension the guard judges.

    The walk opens the element a comprehension repeats and its
    conditions; the iterable is the comprehension arm's own business, so
    the three meet without the walk having to reach it twice.
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
        ('condition carries a launcher', """import operator
import subprocess
operator.call([x for x in xs if subprocess.run], 1)
""", 'operator.call('),
        ('two conditions, one carries', """import operator
import subprocess
operator.call([x for x in xs if x and subprocess.run], 1)
""", 'operator.call('),
        ('dictcomp condition carries', """import operator
import subprocess
operator.call({x: x for x in xs if subprocess.run}, 1)
""", 'operator.call('),
        ('setcomp condition carries', """import operator
import subprocess
operator.call({x for x in xs if subprocess.run}, 1)
""", 'operator.call('),
        ('genexp condition carries', """import operator
import subprocess
operator.call((x for x in xs if subprocess.run), 1)
""", 'operator.call('),
        ('condition carries a launcher, bound', """import subprocess
go = [x for x in xs if subprocess.run]
""", 'go = '),
    )


def _comprehension_free_cases():
    """Comprehensions whose conditions carry nothing stay clean."""
    return (
        ('condition calls a builtin', """import json
json.loads([x.strip() for x in text.split(',')])
"""),
        ('condition compares', """import json
json.dumps({x: x for x in text if x > ','})
"""),
        ('condition is a bound name', """import json
json.dumps({x for x in text if wanted})
"""),
        ('condition yields from a call', """import json
json.loads([x for x in text.split(',') if x.strip() != ''])
"""),
        ('generator expression', """import json
json.loads(list(x for x in text.split(',') if x.strip()))
"""),
    )


def _opened_form_free_cases():
    """A launcher-free spelling of each single-position opened class.

    A matcher that widens has to widen its negative table, so each class
    gets a spelling that carries nothing. They differ from one another
    on purpose: two rows sharing a spelling prove only that one of them
    does. Nine rows hold ten classes: Await, BoolOp, FormattedValue,
    IfExp, JoinedStr, Lambda, NamedExpr, Slice, Yield, YieldFrom, and
    FormattedValue cannot have a row of its own because the parser
    never emits one outside a JoinedStr, so the f-string row covers
    both. The comprehension forms are not here — they have a position
    of their own, and all four of them are pinned by
    `_comprehension_free_cases`.
    """
    return (
        ('conditional', """import operator
operator.call(1 if flag else 2, 1)
"""),
        ('boolean operator', """import operator
operator.call(flag and 2, 1)
"""),
        ('lambda', """import operator
operator.call(lambda: 2, 1)
"""),
        ('f-string', """import operator
import subprocess
go = f"{name}"
"""),
        ('slice', """import operator
operator.call(d[0:2], 1)
"""),
        ('awaited', """import operator
async def go():
    operator.call(await 2, 1)
"""),
        ('yielded', """import operator
def go():
    operator.call((yield 2), 1)
"""),
        ('walrus', """import operator
operator.call((held := 2), 1)
"""),
        ('yielded from', """import operator
def go():
    operator.call((yield from plain()), 1)
"""),
    ) + _TEMPLATE_FREES


def _target_carrier_cases():
    """Launcher-carrying targets, the side a loop binds rather than reads.

    Every row puts the launcher in the target alone: the iterable, the
    context expression and the comprehension's element carry nothing, so
    a row that passed whether or not the target arm exists would be a
    decoy, and the mutation rows that drop each arm catch that.

    Two of the rows are not load-bearing for the walk and should not be
    read as pinning it. `_target_parts` intercepts a `Tuple`, `List` or
    `Starred` target before `_carried_parts` sees it, so the walk's own
    arms for those forms are never reached from a target and the tuple
    and starred rows here prove the arm reads a target rather than what
    the walk does with one. What pins the walk is
    `test_every_grammar_expression_form_is_classified`, and what pins
    the exemption on the exemption's own side is the three container
    shadowing rows in `_target_free_cases`.
    """
    return (
        ('with target', """import subprocess
with open(handle) as d[subprocess]:
    pass
""", 'as d[subprocess]'),
        ('for target', """import subprocess
for d[subprocess] in items:
    pass
""", 'd[subprocess] in items'),
        ('comprehension target', """import subprocess
go = [y for d[subprocess] in items]
""", 'd[subprocess] in items'),
        ('async for target', """import subprocess
async def go():
    async for d[subprocess] in items:
        pass
""", 'd[subprocess] in items'),
        ('tuple target', """import subprocess
for head, d[subprocess] in items:
    pass
""", 'd[subprocess] in items'),
        ('starred target', """import subprocess
for head, *d[subprocess] in items:
    pass
""", '*d[subprocess]'),
        ('async with target', """import subprocess
async def go():
    async with open(handle) as d[subprocess]:
        pass
""", 'as d[subprocess]'),
    )


def _target_free_cases():
    """Targets and values that carry nothing stay clean.

    A subscript whose index is a plain key is the shape the new arm has
    to leave alone, since it is an ordinary subscript and not a carrier.
    """
    return (
        ('with a keyed target', """import os
with open(handle) as d[key]:
    pass
"""),
        ('for a keyed target', """import os
for d[key] in items:
    pass
"""),
        ('for a plain target', """import os
for d[key] in registry.items():
    pass
"""),
        ('comprehension target', """import os
go = [y for d[key] in items]
"""),
        ('async for a keyed target', """import os
async def go():
    async for d[key] in items:
        pass
"""),
        ('starred target over a tuple', """import os
for head, *rest in items:
    pass
"""),
        # A target that shadows a name the walk would otherwise count as
        # a module. The Assign arm exempts the same shape, and a loop
        # variable that shadows one carries nothing.
        ('for target shadowing a module', """import subprocess
for subprocess in items:
    pass
"""),
        ('with target shadowing a module', """import subprocess
with open(handle) as subprocess:
    pass
"""),
        ('comprehension target shadowing a module', """import subprocess
go = [y for subprocess in items]
"""),
        # The container arms of the exemption. A bare-Name shadow says
        # nothing about them: each of these reaches a name only through
        # a tuple, a list or a starred target, so each goes clean
        # because that arm drops the names inside it and for no other
        # reason.
        ('tuple target shadowing a module', """import subprocess
for head, subprocess in items:
    pass
"""),
        ('list target shadowing a module', """import subprocess
for [head, subprocess] in items:
    pass
"""),
        ('starred target shadowing a module', """import subprocess
for head, *subprocess in items:
    pass
"""),
    )


def _binding_arm_cases():
    """The two of #1114's eight arms that no case pinned by name.

    Both refuse, and both were invisible to the census for the same
    reason: removing either arm removed no control, so the row that
    would have caught it did not exist. The launcher sits in the value
    each arm yields, so neither row is reachable by any other arm.
    """
    return (
        ('walrus', """import subprocess
if (go := subprocess.run):
    pass
""", 'go := '),
        ('augmented assignment', """import subprocess
go = 0
go += subprocess.run
""", 'go += '),
    )


def _binding_arm_free_cases():
    """The same two arms carrying nothing, which stay clean."""
    return (
        ('walrus', """import subprocess
if (go := 2):
    pass
"""),
        ('augmented assignment', """import subprocess
go = 0
go += 2
"""),
    )


def _transforming_cases():
    """Forms that build a new value, so a launcher in one is not carried.

    The last two rows are the other side of the attribute arm: an
    attribute naming a constant is a constant read, and the receiver it
    is read off is not a launcher reaching the walk.
    """
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
        ('constant read off a module', """import subprocess
go = subprocess.DEVNULL
"""),
        ('constant read off a launcher', """import subprocess
go = subprocess.run.__name__
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
