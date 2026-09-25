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
        ('cwd declared call', """import subprocess
from _repo import ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
"""),
    )


def test_a_decorated_definition_refuses_a_hidden_launcher(tmp):
    del tmp
    _refused(_decorator_cases())


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

_DECORATORS = (
    "    if isinstance(node, _DECORATED_FORMS):\n", "    if False:\n")
_ARGUMENTS = (
    "                and (_carries_launcher(_call_receiver_parts(node), "
    "facts)\n"
    "                     or _carries_launch_value(_call_argument_parts("
    "node),\n"
    "                                              facts))):\n",
    "                and (_carries_launcher(_call_receiver_parts(node), "
    "facts)\n"
    "                     or False)):\n")
_STARRED = (
    "    elif isinstance(value, ast.Starred):\n"
    "        yield from _carried_parts(value.value)\n",
    "    elif isinstance(value, ast.Starred):\n        pass\n")

_UNFOLLOWABLE_MUTATIONS = (
    ('decorator list', 'bindings', (_DECORATORS,), _DECORATOR_INVOKE),
    ('call arguments', 'bindings', (_ARGUMENTS,), _ARGUMENT_INVOKE),
    ('starred call argument', 'bindings', (_STARRED,), _ARGUMENT_INVOKE),
)


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
