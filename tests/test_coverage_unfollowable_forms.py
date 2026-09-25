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
        # A launcher handed to a callee that only compares, inspects or
        # looks it up is mentioned there, not given to be invoked. These
        # three were clean on the base guard and refused by the argument
        # arm before it learned the difference.
        ('assertIs', """import subprocess
from unittest.mock import patch
with patch('subprocess.run') as patched:
    assertIs(subprocess.run, patched)
"""),
        ('assertIn', """import subprocess
registry = {}
assertIn(subprocess.run, registry)
"""),
        ('callable query', """import subprocess
callable(subprocess.run)
"""),
        # The module-name split: a bare module is a receiver-only signal,
        # because only the receiver reads a launcher out of the module.
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
_ARGUMENT_INVOKE = (
    'import test_coverage_unfollowable_forms as form_suite; '
    'form_suite.test_a_cwd_less_call_argument_refuses_a_hidden_launcher('
    'None)')
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
# The predicate that tells a launcher handed to a callee from one merely
# mentioned there. Forcing it true must turn the assertIs/assertIn/callable
# rows red, which is what says the narrowing is load-bearing.
_LAUNCHER_CALLEE = (
    "    return (name in _LAUNCHERS\n"
    "            or _names_one_of(function, facts.launch_callables))\n",
    "    return True\n")
# A bare module name is a receiver-only signal. Judging it in an argument
# too must turn the module handed to patch.object rows red.
_MODULE_IN_ARGUMENT = (
    "                         and _carries_launch_value(\n"
    "                             _call_argument_parts(node), facts)))):\n",
    "                         and _carries_launcher(\n"
    "                             _call_argument_parts(node), facts)))):\n")
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
    ('launcher callee', 'bindings', (_LAUNCHER_CALLEE,), _CLEAN_INVOKE),
    ('module name in argument', 'bindings', (_MODULE_IN_ARGUMENT,),
     _CLEAN_INVOKE),
    ('starred call argument', 'bindings', (_STARRED,), _ARGUMENT_INVOKE),
)


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
