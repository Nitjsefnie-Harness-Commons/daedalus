#!/usr/bin/env python3
"""Where a RECEIVER's own Load is USED, and what a spread cannot prove.

`s = self` then `s.handles = subprocess.Popen(...)` is not a reflective
write. No setattr, no `__dict__`, no `vars`, so the poison rule had nothing
to fire on, and the census went on reading `self.handles` from its literal
binding while the write landed on it at runtime. That is the direction of
both Criticals, and it is why this suite exists as a committed artefact
rather than a script.

Three families, one plant each, and every row carries a census verdict and a
runtime leg. `setattr(**kw())` and `setattr(self)` raise `TypeError` before
any write, so their runtime leg is vacuous by construction; they are listed
in `CENSUS_ONLY` and are proven by the census alone, which is stated rather
than glossed. See `tests/_launch_plants.py` for the plant.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _launch_plants import CENSUS_ONLY, _plant_verdicts  # noqa: E402

# Every binder that ALIASES the receiver and then writes a plain attribute
# through the alias. `starred-unpack` is here on purpose and is NOT red:
# `*s, = (self,)` makes `s` a list, so `s.handles` never happens.
ALIASES = {
    'plain': 's = self\ns.handles = spawn()',
    'annotated': 's: object = self\ns.handles = spawn()',
    'walrus': 'if (s := self):\n    s.handles = spawn()',
    'for-target': 'for s in (self,):\n    s.handles = spawn()',
    'with-as': 'with CM(self) as s:\n    s.handles = spawn()',
    'match-as': 'match self:\n    case s:\n        s.handles = spawn()',
    'tuple-unpack': 's, t = self, 0\ns.handles = spawn()',
    'list-unpack': '[s, t] = [self, 0]\ns.handles = spawn()',
    'starred-unpack': '*s, = (self,)\ns.handles = spawn()',
    'nested-tuple-unpack': ('(a, (s, b)) = (0, (self, 0))\n'
                           's.handles = spawn()'),
    'global': ('def _i():\n    global s\n    s = self\n_i()\n'
               's.handles = spawn()'),
    'arg-default': ('def _i(s=self):\n    s.handles = spawn()\n_i()'),
    'def-return': 'def _i():\n    return self\ns = _i()\n'
                  's.handles = spawn()',
    'lambda-body': 's = (lambda: self)()\ns.handles = spawn()',
}

# The two required NON-red controls: neither bounds a live child.
CONTROLS = {
    'except-alias': ('try:\n    raise E("x")\n    except E as s:\n'
                     '    s.handles = spawn()'),
    'await-alias': ('async def _i(self):\n    for s in (self,):\n'
                    '        s.handles = spawn()'),
}

# A call whose RESULT is BOUND or FLOWS can hand the receiver back as an
# alias. `s = same(self)` is the row the rule is really for: a withitem-only
# override would have missed it.
CLAUSE = {
    'assign-result': 's = same(self)\ns.handles = spawn()',
    'return-result': 'def _r():\n    return f(self)\ns = _r()\n'
                     's.handles = spawn()',
    'list-element-result': 's = [f(self)][0]\ns.handles = spawn()',
    'attribute-of-result': ('y = same(self)\nz = y.handles\n'
                           'y.handles = spawn()'),
    'with-as-result': 'with CM(self) as s:\n    s.handles = spawn()',
    # A DISCARDED call argument is a call argument. It is listed to hold it:
    # it poisons like every other call argument, and no site depends on it.
    'discarded-call-argument': 'helper(self)\ns = self\ns.handles = spawn()',
}

# A setattr-family call in a shape that cannot prove WHICH receiver.
SPREADS = {
    'setattr-starred-args': 'setattr(self, *extra())',
    'setattr-starred-only': 'setattr(*args_for(self))',
    'object-setattr-star': 'object.__setattr__(self, *extra())',
    'setattr-kwargs-only': 'setattr(**kw())',
    'setattr-no-args': 'setattr(self)',
    # Unchanged by the spread rule, and must stay refused.
    'setattr-literal-3-arg': "setattr(self, 'handles', spawn())",
    'setattr-double-starred': 'setattr(self, *a(), *b())',
    'bound-setattr-star': 'self.__setattr__(*extra())',
}


def _check(rows, label):
    """Every row in `rows` must REFUSE, and none may discharge."""
    red = []
    for name, body in rows.items():
        census, _runtime, _detail = _plant_verdicts(body)
        if census != 'REFUSE':
            red.append(f'{label}/{name} -> {census}')
    assert not red, f'these rows discharge and must not: {red}'


def test_every_binder_that_aliases_the_receiver_is_refused(tmp):
    """13 rows: 13 of the 14 binders the grammar has.

    The alias is the receiver and the write is a PLAIN attribute, so the
    census keeps reading the key it is judging from its literal binding while
    the runtime write lands on it.
    """
    del tmp
    _check(ALIASES, 'alias')


def test_a_call_result_that_is_bound_or_flows_poisons_the_receiver(tmp):
    """`with`, `assign`, `return`, a list element and an attribute of it."""
    del tmp
    _check(CLAUSE, 'clause')


def test_a_setattr_family_call_that_cannot_prove_its_receiver_poisons(tmp):
    """A spread or a short arity poisons every receiver, not a guessed one."""
    del tmp
    _check(SPREADS, 'spread')


def test_the_two_controls_do_not_reap_a_child_and_stay_non_red(tmp):
    """`except-alias` and `await-alias` must not be red.

    Neither bounds a live child, so a census-only reading cannot tell them
    from a real false green. This is the row that keeps the red set honest.
    """
    del tmp
    for name, body in CONTROLS.items():
        census, runtime, _detail = _plant_verdicts(body)
        assert runtime != 'BOUNDED', (
            f'{name} reaped a live child; it is a false green, not a '
            f'control (census {census})')


def test_the_census_only_rows_are_named_as_such(tmp):
    """`setattr(**kw())` and `setattr(self)` raise before any write.

    Their runtime leg is vacuous for a reason no rule changes, so they are
    proven by the census alone. Naming them is what stops a later reader
    from reading them as runtime-proved.
    """
    del tmp
    for name in CENSUS_ONLY:
        assert name in SPREADS, (
            f'{name} is listed census-only but is not a row')
        body = SPREADS[name]
        census, runtime, _detail = _plant_verdicts(body)
        assert census == 'REFUSE', (name, census)
        assert runtime == 'TypeError', (
            f'{name} is recorded as a vacuous runtime leg; if it now does '
            f'something else, move it out of CENSUS_ONLY (runtime {runtime})')


def test_a_receiver_used_only_as_an_attribute_value_stays_proven(tmp):
    """The witness for the ONE proven position, and it is a DISCHARGE.

    Every other row here poisons, so removing the Attribute-value arm leaves
    them all refusing and the suite would not notice. This is the row it
    carries: the receiver is used ONLY as `self.handles`, so nothing poisons,
    `self.handles` keeps its literal binding, and the deadline reaches a
    container -- the shape `test_bridge_startup.py:40` and `:185` discharge,
    and the reason removing the arm would be an over-refusal rather than a
    safety fix. A mutation table found that the arm was otherwise unpinned.
    """
    del tmp
    for body in ('self.handles = []\nself.handles.append(timeout)',
                 'self.handles = []\nself.handles.append(timeout)\n'
                 'self.handles.append(timeout)'):
        source = ('def run_gate(self, timeout):\n'
                  + ''.join('    ' + line + '\n' for line in body.splitlines())
                  + '    return self.handles\n')
        assert not _census_for(source), (
            f'the Attribute-value position is not carrying {body!r}; a '
            f'receiver used only as `self.x` would be poisoned')


def _census_for(source):
    """Census rows for a planted module, every function forced in path."""
    import ast  # noqa: PLC0415
    from _launch_census import _faults
    tree = ast.parse(source)
    forced = frozenset(
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return sorted((r[1], r[2]) for r in _faults('planted.py', tree, forced))


def main():
    return _util.runner(
        _util.collect(globals()), tmp_prefix='launchreceiverpositions_')


if __name__ == '__main__':
    raise SystemExit(main())
