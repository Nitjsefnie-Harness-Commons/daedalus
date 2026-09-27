#!/usr/bin/env python3
"""Binding positions and carried arguments the guard now judges.

Every case here states the operation rather than a spelling: a decorator
list binds the decorated name as a default binds its parameter, and a
`cwd=`-less call carries whatever launcher its arguments name. The last
table is what the real-module plant in test_static_guard_regressions.py
plants into a copied test module, so the refusal is proved against real
code and not only against a source string. That table now lives in
tests/_coverage_mutation_specs.py, and the planted snippets with it.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _carrier_cases import (  # noqa: E402
    _BESPOKE, _bare_form, _binding_arm_cases,
    _binding_arm_free_cases, _callee_chain_cases, _comprehension_cases,
    _comprehension_free_cases, _grammar_forms, _opened_form_free_cases,
    _receiver_atom_cases, _receiver_carrier_cases,
    _target_carrier_cases, _target_free_cases,
    _transforming_cases, _value_preserving_cases)
import _coverage_guard  # noqa: E402
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


def test_a_value_preserving_form_is_refused(tmp):
    del tmp
    _refused(_value_preserving_cases())


def test_a_binding_arm_of_issue_1114_is_refused(tmp):
    del tmp
    _refused(_binding_arm_cases())


def test_a_binding_arm_carrying_nothing_stays_clean(tmp):
    del tmp
    _accepted(_binding_arm_free_cases())


def test_a_transforming_form_stays_an_atom(tmp):
    del tmp
    _accepted(_transforming_cases())


def test_a_launcher_free_spelling_of_an_opened_form_stays_clean(tmp):
    del tmp
    _accepted(_opened_form_free_cases())


def test_a_target_that_carries_a_launcher_is_refused(tmp):
    del tmp
    _refused(_target_carrier_cases())


def test_a_target_and_value_that_carry_nothing_stay_clean(tmp):
    del tmp
    _accepted(_target_free_cases())


def test_a_receiver_that_carries_a_launcher_is_refused(tmp):
    del tmp
    _refused(_receiver_carrier_cases())


def test_a_receiver_that_only_names_a_launcher_stays_clean(tmp):
    del tmp
    _accepted(_receiver_atom_cases())


def test_a_launcher_used_as_a_callee_is_reached_through_the_chain(tmp):
    del tmp
    _refused(_callee_chain_cases())


def test_both_halves_of_a_comprehension_are_judged(tmp):
    del tmp
    _refused(_comprehension_cases())


def test_a_comprehension_condition_that_carries_nothing_stays_clean(tmp):
    del tmp
    _accepted(_comprehension_free_cases())


def test_every_grammar_expression_form_is_classified(tmp):
    """A form no class names is refused, never read as carrying nothing."""
    from _coverage_bindings import (
        _CARRIED_FIELDS, _LEAVES, _UNRECOGNISED, _carried_parts)

    del tmp
    classified = {*_BESPOKE, *_CARRIED_FIELDS, *_LEAVES}
    unclassified = _grammar_forms() - classified
    assert not unclassified, sorted(
        form.__name__ for form in unclassified)
    for form in classified:
        parts = list(_carried_parts(_bare_form(form)))
        assert _UNRECOGNISED not in parts, form.__name__


def test_both_docstrings_state_the_boundary_the_table_draws(tmp):
    """A guard's prose is a claim about the code beside it, in both files.

    The lists come from the table rather than from the prose, so a form
    swapped across the line or left out of a docstring fails here. The
    three claims a reader cannot check by eye — the comprehension's
    conditions, the comprehension's refusal to open its iterable, and
    the receiver's subscript carry — are each held twice: the sentence
    is pinned whole, and the behaviour behind it is driven and required.
    The prose leg catches a claim that drifts while the code stands
    still; the behaviour leg catches the code that stops backing a claim
    the prose still makes.

    What the prose leg cannot catch, and this is the whole of what it
    cannot catch: a false sentence added *outside* the pinned span, and
    a polarity residual — a fluent rewrite that keeps the pinned words
    as a substring of a longer sentence that denies them. Pinning the
    whole sentence closes the second for this claim; the first is
    inherent to matching prose, and it is stated here rather than left
    to be discovered.
    """
    from _coverage_bindings import _carried_parts, _target_parts

    del tmp
    opened, leaves = _classification()
    for prose in _carried_parts.__doc__ or '', _GUARD_PROSE:
        flat = _squash(prose)
        # The lists, spelled out and in order: a form moved to the other
        # clause, or left out of the prose, changes what the two sides
        # have to say and fails here rather than passing on vocabulary.
        assert _squash(opened) in flat, opened
        assert _squash(leaves) in flat, leaves
        # The claims, whole and not merely their opening words: a
        # paragraph that keeps the word while inverting the sentence is
        # the false green this replaces, and only the full claim
        # catches it.
        assert _squash(FAIL_CLOSED) in flat, prose
        assert _fold(CONDITIONS_CLAIM) in _sentences(prose), prose
    assert _squash(RECEIVER_CARRY) in _squash(_GUARD_PROSE), _GUARD_PROSE
    for prose in _target_parts.__doc__ or '', _GUARD_PROSE:
        assert _fold(TARGET_EXEMPTION) in _sentences(prose), prose
    assert _receiver_carries_a_subscript(), _BINDING_MESSAGE
    assert _the_walk_reaches_a_conditions_launcher(), (
        'a condition carries no launcher back: ' + CONDITIONS_CLAIM)
    assert _the_walk_leaves_the_iterable_alone(), ITERABLE_CLAIM
    assert _an_unrecognised_form_is_refused(), 'the fail-closed branch'


def _the_walk_reaches_a_conditions_launcher():
    """Whether a launcher carried by a comprehension's condition comes back.

    The element and the iterable of this comprehension carry nothing, so
    a launcher part can only have come through the condition, and no
    prose is consulted to say so.
    """
    from _coverage_bindings import _carried_parts, _is_launch_value
    from _coverage_guard import _ModuleFacts

    statement = ast.parse('[x for x in xs if subprocess.run]').body[0]
    assert isinstance(statement, ast.Expr)
    facts = _ModuleFacts(ast.parse('import subprocess'))
    return any(_is_launch_value(part, facts)
               for part in _carried_parts(statement.value))


def _the_walk_leaves_the_iterable_alone():
    """Whether the comprehension node still declines to open its `iter`.

    The guard's second clause says the iterable is not reached through
    the conditions node, and that is a claim about the code: this
    comprehension's only module name is in the iterable, so a part that
    names it can only have come through a node that opened `iter` and
    should not have.
    """
    from _coverage_bindings import _carried_parts

    statement = ast.parse('[x for x in [subprocess]]').body[0]
    assert isinstance(statement, ast.Expr)
    return not any(isinstance(part, ast.Name) and part.id == 'subprocess'
                   for part in _carried_parts(statement.value))


def _an_unrecognised_form_is_refused():
    """Whether a form in neither class is refused rather than read clean.

    A subclass stands in for the expression form a later Python adds:
    the walk keys its table on the exact type, so a subclass is the one
    input the table cannot name even by accident. It is driven straight
    into the walk and both predicates, because no parser emits it.
    """
    from _coverage_bindings import (
        _UNRECOGNISED, _carried_parts, _carries_launch_value,
        _carries_launcher)
    from _coverage_guard import _ModuleFacts

    class _Unrecognised(ast.IfExp):
        """The form a later Python adds, which this walk cannot name."""

    node = _Unrecognised(test=ast.Name(id='subprocess'),
                         body=ast.Name(id='x'), orelse=ast.Name(id='y'))
    parts = list(_carried_parts(node))
    assert _UNRECOGNISED in parts, parts
    facts = _ModuleFacts(ast.parse('import subprocess'))
    return (_carries_launch_value(parts, facts)
            and _carries_launcher(parts, facts))


def _squash(text):
    """A claim as a reader meets it, with the wrapping taken out."""
    return ''.join(text.split())


def _fold(text):
    """A claim the way a reader meets it, wrapping and case dropped."""
    return ' '.join(text.split()).lower()


def _sentences(prose):
    """The prose split into its sentences, folded the same way.

    A pin against this asks the claim to be a whole sentence, so a
    longer sentence that contains it and denies it is not a match.
    """
    return {part.strip(' ,;:') for part in re.split(r'[.!?]', _fold(prose))}


def _classification():
    """The opened and leaf form names, read from the walk's own table.

    Joined and sorted because a docstring states each list as prose, and
    the control matches it on whitespace alone: a claim that survives a
    rewrap is the claim a reader reads.
    """
    from _coverage_bindings import _CARRIED_FIELDS, _LEAVES

    return (",".join(sorted(form.__name__
                            for form in {*_BESPOKE, *_CARRIED_FIELDS})),
            ",".join(sorted(form.__name__ for form in _LEAVES)))


def _receiver_carries_a_subscript():
    """Whether the receiver arm still hands over what its prose claims."""
    from _coverage_bindings import _call_receiver_parts

    statement = ast.parse("d[subprocess].run(['python3', 'child.py'])").body[0]
    assert isinstance(statement, ast.Expr)
    return any(isinstance(part, ast.Name) and part.id == 'subprocess'
               for part in _call_receiver_parts(statement.value))


_GUARD_PROSE = _coverage_guard.__doc__ or ''
FAIL_CLOSED = 'a form in neither class is refused rather than read as clean'
RECEIVER_CARRY = 'every subscript the descent consumes'
CONDITIONS_CLAIM = (
    'The four comprehension forms reach their conditions through the '
    'statement-level node their `generators` hold, and not through '
    'their own iterable, which `_bound_values` judges as the '
    'comprehension arm\'s own business')
TARGET_EXEMPTION = (
    'A subscript\'s index is not exempt, so `d[subprocess]` binds a '
    'launcher and `d[key]` does not')
ITERABLE_CLAIM = 'the comprehension node opens its own iterable'


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
