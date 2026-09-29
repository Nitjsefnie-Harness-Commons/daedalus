#!/usr/bin/env python3
"""A mutating call in a definition-time binder position of a function object
fails closed.

Python evaluates a function object's decorators, its defaults, its
keyword-only defaults and its annotations when the `def` or the `lambda`
executes, not when the function is called, so a tracked container mutated in
one of them is mutated before any body runs and a later read answers from a
position the model recorded before the shift. The store hook that drops those
positions has to run there, the way it runs in a header or a case guard.

The family is derived from the `ast` fields of the three function-like nodes
rather than from a list of spellings, so a field a future Python adds fails
`test_every_definition_time_field_is_covered` instead of passing unreached.
Each row is paired with a clean twin that swaps the carried relay for a
carried quiet, so the row proves the binder position was why the read
reported.
"""
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _tabroute_focus import _tracked_focus_verdict  # noqa: E402

_PRE = (
    'send = ordinary\n'
    'args = _args\n'
    'def maker():\n'
    '    return lambda: send("_focus", "focus-tab", '
    'tab=args.chrome_tab)\n'
    'def relay(): return maker()\n'
    'def quiet(): return lambda: ordinary()\n')
_SEND = '\nsend = ext_cmd\nreturn '
_LIST = 'x = [ordinary, relay()]'
_READ = 'x[0]()'
_POP = 'x.pop(0)'
_ANN = '((x.pop(0) or int))'

# (name, the `def`/`lambda` header carrying the mutation at that position).
_ROWS = {
    ast.FunctionDef: [
        ('decorator', f'@({_POP} or (lambda f: f))\ndef g(a):\n    pass'),
        ('return_annotation', f'def g(a) -> {_ANN}:\n    pass'),
        ('posonly_default', f'def g(a={_POP}, /):\n    pass'),
        ('default', f'def g(a={_POP}):\n    pass'),
        ('kwonly_default', f'def g(*, a={_POP}):\n    pass'),
        ('posonly_annotation', f'def g(a: {_ANN}, /):\n    pass'),
        ('annotation', f'def g(a: {_ANN}):\n    pass'),
        ('vararg_annotation', f'def g(*args: {_ANN}):\n    pass'),
        ('kwonly_annotation', f'def g(*, a: {_ANN}):\n    pass'),
        ('kwarg_annotation', f'def g(**kwargs: {_ANN}):\n    pass')],
    ast.AsyncFunctionDef: [
        ('decorator',
         f'@({_POP} or (lambda f: f))\nasync def g(a):\n    pass'),
        ('return_annotation', f'async def g(a) -> {_ANN}:\n    pass'),
        ('posonly_default', f'async def g(a={_POP}, /):\n    pass'),
        ('default', f'async def g(a={_POP}):\n    pass'),
        ('kwonly_default', f'async def g(*, a={_POP}):\n    pass'),
        ('posonly_annotation', f'async def g(a: {_ANN}, /):\n    pass'),
        ('annotation', f'async def g(a: {_ANN}):\n    pass'),
        ('vararg_annotation', f'async def g(*args: {_ANN}):\n    pass'),
        ('kwonly_annotation', f'async def g(*, a: {_ANN}):\n    pass'),
        ('kwarg_annotation', f'async def g(**kwargs: {_ANN}):\n    pass')],
    ast.Lambda: [
        ('posonly_default', f'g = lambda a={_POP}, /: 0'),
        ('default', f'g = lambda a={_POP}: 0'),
        ('kwonly_default', f'g = lambda *, a={_POP}: 0')],
}

# A `body` runs when the function is called; a `name`, an `arg` and a
# `type_comment` are names rather than expressions; and a PEP 695
# `type_params` bound is evaluated when the type parameter is first used
# rather than when the function object is built.
_CALL_TIME = frozenset({'body'})
_NOT_EXPRESSIONS = frozenset({'name', 'arg', 'type_comment'})
_LAZY = frozenset({'type_params'})
_ARG_LISTS = ('posonlyargs', 'args', 'vararg', 'kwonlyargs', 'kwarg')

# The rows each definition-time `ast` field carries. `args.defaults` names one
# field and two rows, because a positional-only default and an ordinary one
# are the same list reached at a different index.
_FIELD_ROWS = {
    'decorator_list': ('decorator',),
    'returns': ('return_annotation',),
    'args.defaults': ('posonly_default', 'default'),
    'args.kw_defaults': ('kwonly_default',),
    'args.posonlyargs.annotation': ('posonly_annotation',),
    'args.args.annotation': ('annotation',),
    'args.vararg.annotation': ('vararg_annotation',),
    'args.kwonlyargs.annotation': ('kwonly_annotation',),
    'args.kwarg.annotation': ('kwarg_annotation',)}

# A `lambda` parameter cannot carry an annotation at all: the `:` opening the
# body is indistinguishable from the one an annotation ends with, so the
# grammar refuses the parameter instead of the parser producing an `arg` with
# an annotation on it.
_ILLEGAL = {ast.Lambda: frozenset((
    'posonly_annotation', 'annotation', 'vararg_annotation',
    'kwonly_annotation', 'kwarg_annotation'))}


def _argument_fields():
    """The `ast` field paths an `arguments` node evaluates at definition
    time, derived from its own fields."""
    found = set()
    for field in ast.arguments._fields:
        if field in ('defaults', 'kw_defaults'):
            found.add(f'args.{field}')
        elif field in _ARG_LISTS:
            for arg_field in ast.arg._fields:
                if arg_field == 'annotation':
                    found.add(f'args.{field}.{arg_field}')
                elif arg_field not in _NOT_EXPRESSIONS:
                    raise AssertionError(f'unclassified `arg.{arg_field}`')
        else:
            raise AssertionError(f'unclassified `arguments.{field}`')
    return found


def _definition_time_fields(node_type):
    """The `ast` field paths a function-like node evaluates when its function
    object is defined, derived from the node class's own fields. A field in
    neither set raises: a field a future Python adds is unclassified here
    rather than silently unreached by the guard."""
    found = set()
    for field in node_type._fields:
        if field in _FIELD_ROWS:
            found.add(field)
        elif field == 'args':
            found.update(_argument_fields())
        elif (field in _CALL_TIME or field in _NOT_EXPRESSIONS
                or field in _LAZY):
            continue
        else:
            raise AssertionError(
                f'unclassified `{node_type.__name__}.{field}`')
    return found


def _covered(node_type):
    return ({row for field in _definition_time_fields(node_type)
             for row in _FIELD_ROWS[field]}
            - _ILLEGAL.get(node_type, frozenset()))


def _label(node_type, name):
    stem = {ast.FunctionDef: 'def', ast.AsyncFunctionDef: 'async-def',
            ast.Lambda: 'lambda'}[node_type]
    return f'{stem}-{name}'


def _rows():
    for node_type, rows in _ROWS.items():
        for name, header in rows:
            yield _label(node_type, name), header


def _verdict(tmp, store, header):
    return _tracked_focus_verdict(
        tmp, f'{_PRE}{store}\n{header}{_SEND}{_READ}', counts=True)


def _swap(text):
    return re.sub(r'relay|quiet',
                  lambda m: 'quiet' if m[0] == 'relay' else 'relay', text)


def test_every_binder_position_fails_closed(tmp):
    observed = [(name, _verdict(tmp, _LIST, header))
                for name, header in _rows()]
    missed = [item for item in observed if item[1] != (1, 1)]
    assert not missed, missed


def test_every_binder_position_twin_stays_clean(tmp):
    observed = [(name, _verdict(tmp, _swap(_LIST), _swap(header)))
                for name, header in _rows()]
    flagged = [item for item in observed if item[1] != (0, 0)]
    assert not flagged, flagged


def test_every_definition_time_field_is_covered(tmp):
    for node_type, rows in _ROWS.items():
        derived = _definition_time_fields(node_type)
        assert _covered(node_type) == {name for name, _ in rows}, (
            node_type.__name__, sorted(derived))
        for field in derived:
            assert field in _FIELD_ROWS, field
    assert _covered(ast.FunctionDef) == _covered(ast.AsyncFunctionDef)


def test_a_type_parameter_bound_is_not_a_definition_time_position(tmp):
    # CPython evaluates the bound when the type parameter is first used, so
    # the list is intact when the function object is defined and a mutation
    # there displaces nothing.
    if sys.version_info >= (3, 14):
        return
    assert _verdict(tmp, _LIST,
                    f'def g[T: ({_POP} or int)](a):\n    pass') == (0, 0)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='binderpositions_')


if __name__ == '__main__':
    raise SystemExit(main())
