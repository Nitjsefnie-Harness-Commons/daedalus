#!/usr/bin/env python3
"""Binding positions and carried arguments the guard now judges.

Every case here states the operation rather than a spelling: a decorator
list binds the decorated name as a default binds its parameter, and a
`cwd=`-less call carries whatever launcher its arguments name. The last
table is what the real-module plant in test_static_guard_regressions.py
plants into a copied test module, so the refusal is proved against real
code and not only against a source string.
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


def _refused(cases):
    """Each case must compile, then yield exactly its one binding verdict."""
    for name, source, marker in cases:
        compile(source, f'<{name}>', 'exec')
        violations = _synthetic_violations(source)
        assert violations == [_at(source, marker)], (name, violations)


def _accepted(cases):
    """Each case must compile, then yield nothing at all."""
    for name, source in cases:
        compile(source, f'<{name}>', 'exec')
        assert _synthetic_violations(source) == [], name


def _decorator_cases():
    """A decorator list binds the decorated name as surely as a default."""
    return (
        ('function', """import os
import subprocess
from functools import partial
@partial(subprocess.run)
def go():
    pass
""", '@partial(subprocess.run)'),
        ('async function', """import os
import subprocess
from functools import partial
@partial(subprocess.run)
async def go():
    pass
""", '@partial(subprocess.run)'),
        ('class', """import os
import subprocess
from functools import partial
@partial(subprocess.run)
class Go:
    pass
""", '@partial(subprocess.run)'),
        ('module level if', """import os
import subprocess
from functools import partial
if os.sep:
    @partial(subprocess.run)
    def go():
        pass
""", '@partial(subprocess.run)'),
        ('module level try', """import os
import subprocess
from functools import partial
try:
    @partial(subprocess.run)
    def go():
        pass
except OSError:
    pass
""", '@partial(subprocess.run)'),
        ('module level with', """import os
import subprocess
from functools import partial
with open(os.devnull) as handle:
    @partial(subprocess.run)
    def go():
        pass
""", '@partial(subprocess.run)'),
        ('renamed module', """import os
import subprocess as sp
from functools import partial
@partial(sp.run)
def go():
    pass
""", '@partial(sp.run)'),
        ('from-import launcher', """import os
from functools import partial
from subprocess import run
@partial(run)
def go():
    pass
""", '@partial(run)'),
        ('bare launcher', """import os
import subprocess
@subprocess.run
def go():
    pass
""", '@subprocess.run'),
        # A bare launcher is an attribute, never a call, so these three
        # rows cannot be satisfied by the call-argument arm: each dies
        # when its own form leaves the header set on its own.
        ('bare launcher on async', """import os
import subprocess
@subprocess.run
async def go():
    pass
""", '@subprocess.run'),
        ('bare launcher on class', """import os
import subprocess
@subprocess.run
class Go:
    pass
""", '@subprocess.run'),
    )


def _launching_callee_cases():
    """Callees that invoke what they are handed, named or not.

    A rule that judged the callee's name would have to list all six to
    keep these rows clean. `_opaque_callee_cases` says what that is
    worth and what it is not.
    """
    return (
        ('thread target', """import subprocess
from threading import Thread
Thread(target=subprocess.run)
""", 'Thread(target='),
        ('executor submit', """import subprocess
from concurrent.futures import ThreadPoolExecutor
executor = ThreadPoolExecutor()
executor.submit(subprocess.run)
""", 'executor.submit('),
        ('mock side effect', """import subprocess
from unittest.mock import Mock
Mock(side_effect=subprocess.run)
""", 'Mock(side_effect='),
        ('atexit register', """import atexit
import subprocess
atexit.register(subprocess.run)
""", 'atexit.register('),
        ('wrapped launcher', """import subprocess
from functools import partial
partial(subprocess.run)(['python3', 'child.py'])
""", 'partial(subprocess.run)('),
        ('map a launcher', """import subprocess
list(map(subprocess.run, [['python3', 'child.py']]))
""", 'list(map('),
    )


def _opaque_callee_cases():
    """A launcher handed to a callee whose name says nothing.

    The other tables name their callees, so a gate listing those names
    satisfies every one of them. These rows raise its cost: the names
    below carry no information and a list has to name them too.

    That is the whole of what they buy. Indifference is not a property
    any finite sample of names can witness — a gate naming all five of
    these and every other name in this file leaves every row of every
    table in this file green. What they defend against is the mistake
    that happened, a gate tuned to the committed rows, because that
    gate must be written against these rows too. The runtime probes in
    test_coverage_decorated_launch.py are what reject it: a gate has to
    list `asyncio.to_thread`, `ExitStack.callback` and
    `weakref.finalize` as well, and those three appear in no table here.
    """
    return (
        ('single letter', """import subprocess
f(subprocess.run)
""", 'f(subprocess.run)'),
        ('arbitrary verb', """import subprocess
consume(subprocess.run)
""", 'consume(subprocess.run)'),
        ('arbitrary noun', """import subprocess
holder(subprocess.run, ['python3', 'child.py'])
""", 'holder('),
        ('keyword under an arbitrary callee', """import subprocess
dispatch(subprocess.run, argv=['python3', 'child.py'])
""", 'dispatch('),
        ('alias handed on', """import subprocess
from subprocess import run
send(run)
""", 'send('),
    )


def _query_shape_cases():
    """A launcher mentioned to a callee that inspects it is still refused.

    These three were clean on the base guard and released by a narrowing
    that read the callee's name to decide whether it would invoke what
    it was given. They are exactly where a future narrowing would release
    them again, so the verdict is pinned here rather than left to the
    absence of a test.
    """
    return (
        ('assertIs', """import subprocess
from unittest.mock import patch
with patch('subprocess.run') as patched:
    assertIs(subprocess.run, patched)
""", 'assertIs('),
        ('assertIn', """import subprocess
registry = {}
assertIn(subprocess.run, registry)
""", 'assertIn('),
        ('callable query', """import subprocess
callable(subprocess.run)
""", 'callable('),
    )


def _lambda_default_cases():
    """A lambda default binds its parameter as surely as a def's does."""
    return (
        ('lambda default', """import os
import subprocess
go = lambda launcher=subprocess.run: launcher
go(['python3', 'child.py'])
""", 'go = lambda'),
        ('lambda keyword default', """import os
import subprocess
go = lambda *, launcher=subprocess.run: launcher
go(['python3', 'child.py'])
""", 'go = lambda'),
    )


def _call_argument_cases():
    """A `cwd=`-less call carries whatever launcher its arguments name."""
    return (
        ('positional', """import operator
import subprocess
operator.call(subprocess.run, ['python3', 'child.py'])
""", 'operator.call('),
        ('starred', """import operator
import subprocess
operator.call(*[subprocess.run, ['python3', 'child.py']])
""", 'operator.call('),
        ('double starred', """import operator
import subprocess
operator.call(**{'run': subprocess.run, 'args': ['python3', 'child.py']})
""", 'operator.call('),
        ('keyword', """import operator
import subprocess
operator.call(function=subprocess.run, args=['python3', 'child.py'])
""", 'operator.call('),
        ('nested receiver', """import operator
import subprocess
operator.call((subprocess.run, ['python3', 'child.py']))
""", 'operator.call('),
    )


def _launcher_free_cases():
    """The headers and arguments that carry no launcher stay clean."""
    return (
        ('staticmethod', """class Go:
    @staticmethod
    def run():
        pass
"""),
        ('property', """class Go:
    @property
    def run(self):
        return 1
"""),
        ('wraps a plain name', """import functools
other = len
@functools.wraps(other)
def go():
    pass
"""),
        ('decorator factory', """import functools
@functools.lru_cache()
def go():
    return 1
"""),
        ('builtin callee argument', """import operator
operator.call(len, ['x'])
"""),
        # The module-name split, and the only distinction here that is not
        # a name test: a bare module is a launcher only where the receiver
        # reads a launch method off it, and an argument carries no such
        # read. A launcher — an attribute that names one, or a name bound
        # to one — is refused wherever it appears.
        ('module handed to patch', """import subprocess
from unittest import mock
with mock.patch.object(subprocess, 'run'):
    pass
"""),
        ('module in a plain argument', """import subprocess
import operator
operator.call(subprocess, ['x'])
"""),
        ('cwd declared call', """import subprocess
from _repo import ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
"""),
    )


def test_a_decorated_definition_refuses_a_hidden_launcher(tmp):
    del tmp
    _refused(_decorator_cases())


def test_a_launching_callee_refuses_a_hidden_launcher(tmp):
    del tmp
    _refused(_launching_callee_cases())


def test_an_opaque_callee_refuses_a_hidden_launcher(tmp):
    del tmp
    _refused(_opaque_callee_cases())


def test_a_query_shape_stays_refused(tmp):
    del tmp
    _refused(_query_shape_cases())


def test_a_lambda_default_refuses_a_hidden_launcher(tmp):
    del tmp
    _refused(_lambda_default_cases())


def test_a_cwd_less_call_argument_refuses_a_hidden_launcher(tmp):
    del tmp
    _refused(_call_argument_cases())


def test_a_launcher_reached_only_through_a_callee_chain_is_refused(tmp):
    """The inner call's own `cwd=` leaves the callee chain the only route."""
    del tmp
    source = """import os
import subprocess
from functools import partial
tmp = os.sep
go = partial(subprocess.run, cwd=tmp)()
"""
    compile(source, '<callee chain>', 'exec')
    assert _synthetic_violations(source) == [
        'tests/synthetic.py:5: unresolved callee partial cwd=tmp '
        'declares no env=',
        _at(source, 'go = partial('),
    ]


def test_launcher_free_headers_and_arguments_stay_clean(tmp):
    del tmp
    _accepted(_launcher_free_cases())


def _unfollowable_snippets():
    """Rows the real-module plant drives, unsafe and explicit alike."""
    return (
        (
            'decorator list',
            """def _binding_probe(tmp):
    from functools import partial
    @partial(subprocess.run)
    def go():
        pass
""",
            '@partial(subprocess.run)',
            """def _binding_probe(tmp):
    result = subprocess.run(['python3', 'child.py'], cwd=tmp)
"""),
        (
            'call arguments',
            """def _binding_probe(tmp):
    import operator
    operator.call(subprocess.run, ['python3', 'child.py'])
""",
            'operator.call(subprocess.run,',
            """def _binding_probe(tmp):
    result = subprocess.run(['python3', 'child.py'], cwd=tmp)
"""),
    )


_DECORATOR_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_decorated_definition_refuses_a_hidden_launcher(None)')
# One needle, one row: the issue-620 argument table, the launching table,
# the opaque-callee table and the query table all reach the argument arm,
# so splitting them across rows would pin one change twice and read as
# more changes than there are.
_ARGUMENT_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_cwd_less_call_argument_refuses_a_hidden_launcher('
    'None); form_suite.test_a_launching_callee_refuses_a_hidden_'
    'launcher(None); form_suite.test_an_opaque_callee_refuses_a_hidden_'
    'launcher(None); form_suite.test_a_query_shape_stays_refused(None)')
# The enumeration this branch removed, put back where it was. The
# opaque-callee rows are what catch it: their callee names carry no
# information, so no list can be tuned to satisfy them.
_ENUMERATION_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_an_opaque_callee_refuses_a_hidden_launcher(None); '
    'form_suite.test_a_query_shape_stays_refused(None)')
_LAMBDA_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_lambda_default_refuses_a_hidden_launcher(None)')
_CLEAN_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_launcher_free_headers_and_arguments_stay_clean(None)')

# The decorator half of the header set, removed on its own. Narrowing it to
# FunctionDef alone must turn the async and class rows red, which is what
# says the two forms are reached by this arm and not by the argument arm.
_DECORATORS = (
    "    if isinstance(node, _DECORATED_FORMS):\n", "    if False:\n")
_DECORATED_FORMS = (
    "_DECORATED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, "
    "ast.ClassDef)\n",
    "_DECORATED_FORMS = (ast.FunctionDef,)\n")
# The two halves of that set removed on their own, so the async row and the
# class row are each shown to be reached by this arm rather than by the
# argument arm that sat behind them.
_DECORATED_NO_ASYNC = (
    "_DECORATED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, "
    "ast.ClassDef)\n",
    "_DECORATED_FORMS = (ast.FunctionDef, ast.ClassDef)\n")
_DECORATED_NO_CLASS = (
    "_DECORATED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, "
    "ast.ClassDef)\n",
    "_DECORATED_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef)\n")
# A lambda default binds its parameter as surely as a def's, so the header
# set that routes it must still carry Lambda.
_HEADER_FORMS = (
    "_HEADER_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,\n"
    "                 ast.Lambda)\n",
    "_HEADER_FORMS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)\n")
_ARGUMENTS = (
    "def _call_argument_parts(value):\n"
    '    """Every value a call\'s arguments carry, starred forms '
    'included."""\n'
    "    arguments = [*value.args,\n"
    "                 *(keyword.value for keyword in value.keywords)]\n"
    "    for argument in arguments:\n"
    "        yield from _carried_parts(argument)\n",
    "def _call_argument_parts(value):\n    yield from ()\n")
# The enumeration this branch removed, reinstated in front of the arm. The
# list holds the callee and keyword names the case tables name, so it is
# the strongest form of the mistake: a gate tuned to satisfy every row.
# A gate like this one satisfies the tables, which is why the tables are
# not the whole defence — the runtime probes reject it behaviourally,
# because a gate naming every name here must also name to_thread,
# callback and finalize, and the next name written is not on its list.
_ENUMERATION = (
    "def _unfollowable_launcher_bindings(tree, facts):\n",
    "_INVOKING = frozenset({'call', 'partial', 'map', 'Thread',\n"
    "                       'submit', 'register', 'side_effect'})\n"
    "\n"
    "\n"
    "def _invoking_callee(node):\n"
    "    function = node.func\n"
    "    if isinstance(function, ast.Attribute):\n"
    "        name = function.attr\n"
    "    elif isinstance(function, ast.Name):\n"
    "        name = function.id\n"
    "    else:\n"
    "        name = ''\n"
    "    return name in _INVOKING or any(\n"
    "        keyword.arg in _INVOKING for keyword in node.keywords)\n"
    "\n"
    "\n"
    "def _unfollowable_launcher_bindings(tree, facts):\n")
# A bare module name is a launcher only where a launch method is read off
# it. Judging it in an argument position too must turn the module handed
# to patch.object rows red.
# The arm this row edits, anchored as a whole block ending at a line
# break. A needle that stops short of the newline is a prefix of the
# module-name row's, and a prefix's uniqueness is inherited from the
# longer string rather than from the text this mutation changes.
_ENUMERATION_ARM = (
    "        if (isinstance(node, ast.Call)\n"
    "                and not _has_cwd_control(node)\n"
    "                and (_carries_launcher(_call_receiver_parts(node), "
    "facts)\n"
    "                     or _carries_launch_value(_call_argument_parts("
    "node),\n"
    "                                              facts))):\n",
    "        if (isinstance(node, ast.Call)\n"
    "                and not _has_cwd_control(node)\n"
    "                and (_carries_launcher(_call_receiver_parts(node), "
    "facts)\n"
    "                     or (_invoking_callee(node)\n"
    "                         and _carries_launch_value(\n"
    "                             _call_argument_parts(node), facts)))):\n")
_MODULE_IN_ARGUMENT = (
    "                     or _carries_launch_value(_call_argument_parts("
    "node),\n"
    "                                              facts))):\n",
    "                     or _carries_launcher(_call_argument_parts(node),\n"
    "                                         facts))):\n")
_STARRED = (
    "    elif isinstance(value, ast.Starred):\n"
    "        yield from _carried_parts(value.value)\n",
    "    elif isinstance(value, ast.Starred):\n        pass\n")

_UNFOLLOWABLE_MUTATIONS = (
    ('decorator list', 'bindings', (_DECORATORS,), _DECORATOR_INVOKE),
    ('decorated forms', 'bindings', (_DECORATED_FORMS,), _DECORATOR_INVOKE),
    ('decorated forms without async', 'bindings', (_DECORATED_NO_ASYNC,),
     _DECORATOR_INVOKE),
    ('decorated forms without class', 'bindings', (_DECORATED_NO_CLASS,),
     _DECORATOR_INVOKE),
    ('lambda header form', 'bindings', (_HEADER_FORMS,), _LAMBDA_INVOKE),
    ('call arguments', 'bindings', (_ARGUMENTS,), _ARGUMENT_INVOKE),
    ('callee-name enumeration', 'bindings',
     (_ENUMERATION, _ENUMERATION_ARM), _ENUMERATION_INVOKE),
    ('module name in argument', 'bindings', (_MODULE_IN_ARGUMENT,),
     _CLEAN_INVOKE),
    ('starred call argument', 'bindings', (_STARRED,), _ARGUMENT_INVOKE),
)


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
