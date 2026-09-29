#!/usr/bin/env python3
"""A mutating call in a definition-time binder position fails closed.

Python evaluates a function object's decorators, its defaults, its
keyword-only defaults and its annotations, and a class object's decorators,
its bases and its class keywords, when the `def`, the `class` or the `lambda`
executes -- never when the resulting object is later used. A tracked
container mutated in one of them is mutated before any body runs, so a later
read answers from a position the model recorded before the shift. The store
hook that drops those positions has to run there, the way it runs in a header
or a case guard.

The family is derived from the `ast` fields of the four object-defining nodes
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
_READ = 'x[0]()'
_POP = 'x.pop(0)'
_ANN = '((x.pop(0) or int))'
_LIST = 'x = [ordinary, relay()]'
# A class base has to be a class and a decorator has to be callable, so what
# the pop displaces at index 0 is `object` in one and `None` in the other. The
# starred tail leaves the read a position the shift cannot pin.
_CLASS_OBJECT = 'x = [object, relay(), *args.values]'
_CLASS_CALLABLE = 'x = [None, relay(), *args.values]'
# The same two carriers with no relay anywhere, so a clean verdict on a
# control row is the rule staying quiet rather than a read that could not
# have reported. The decorator's carrier starts with the quiet callable
# rather than a base, because a decorator is called with the class and the
# base is spelled as a call instead.
_CLASS_QUIET_OBJECT = 'x = [object, quiet(), *args.values]'
_CLASS_QUIET_CALLABLE = 'x = [ordinary, quiet(), *args.values]'

# (name, the carrier, the header carrying the mutation at that position).
_ROWS = {
    ast.FunctionDef: [
        ('decorator', _LIST,
         f'@({_POP} or (lambda f: f))\ndef g(a):\n    pass'),
        ('return_annotation', _LIST, f'def g(a) -> {_ANN}:\n    pass'),
        ('posonly_default', _LIST, f'def g(a={_POP}, /):\n    pass'),
        ('default', _LIST, f'def g(a={_POP}):\n    pass'),
        ('kwonly_default', _LIST, f'def g(*, a={_POP}):\n    pass'),
        ('posonly_annotation', _LIST, f'def g(a: {_ANN}, /):\n    pass'),
        ('annotation', _LIST, f'def g(a: {_ANN}):\n    pass'),
        ('vararg_annotation', _LIST, f'def g(*args: {_ANN}):\n    pass'),
        ('kwonly_annotation', _LIST, f'def g(*, a: {_ANN}):\n    pass'),
        ('kwarg_annotation', _LIST, f'def g(**kwargs: {_ANN}):\n    pass')],
    ast.AsyncFunctionDef: [
        ('decorator', _LIST,
         f'@({_POP} or (lambda f: f))\nasync def g(a):\n    pass'),
        ('return_annotation', _LIST, f'async def g(a) -> {_ANN}:\n    pass'),
        ('posonly_default', _LIST, f'async def g(a={_POP}, /):\n    pass'),
        ('default', _LIST, f'async def g(a={_POP}):\n    pass'),
        ('kwonly_default', _LIST, f'async def g(*, a={_POP}):\n    pass'),
        ('posonly_annotation', _LIST, f'async def g(a: {_ANN}, /):\n    pass'),
        ('annotation', _LIST, f'async def g(a: {_ANN}):\n    pass'),
        ('vararg_annotation', _LIST, f'async def g(*args: {_ANN}):\n    pass'),
        ('kwonly_annotation', _LIST, f'async def g(*, a: {_ANN}):\n    pass'),
        ('kwarg_annotation', _LIST,
         f'async def g(**kwargs: {_ANN}):\n    pass')],
    ast.Lambda: [
        ('posonly_default', _LIST, f'g = lambda a={_POP}, /: 0'),
        ('default', _LIST, f'g = lambda a={_POP}: 0'),
        ('kwonly_default', _LIST, f'g = lambda *, a={_POP}: 0')],
    ast.ClassDef: [
        ('base', _CLASS_OBJECT, f'class C({_POP}):\n    pass'),
        ('keyword', _CLASS_OBJECT,
         f'class C(metaclass=ordinary({_POP}) or type):\n    pass'),
        ('decorator', _CLASS_CALLABLE,
         f'@({_POP} or (lambda c: c))\nclass C:\n    pass')],
}

# A position the object definition does evaluate, carrying no mutation. The
# rule is a rule about a mutation, and reaching the position is not itself a
# finding: over-reporting is a failure mode here, so the row is measured
# rather than assumed.
_QUIET = [
    ('class-base-read', _CLASS_QUIET_OBJECT, 'class C(x[0]):\n    pass',
     (0, 0)),
    ('class-keyword-read', _CLASS_QUIET_OBJECT,
     'class C(metaclass=ordinary(x[0]) or type):\n    pass', (0, 0)),
    ('class-decorator-read', _CLASS_QUIET_CALLABLE,
     '@(x[0] or (lambda c: c))\nclass C:\n    pass', (0, 0))]

# A `body` is walked by its own flow against its own state, where the
# statement store hook already runs; a `name`, an `arg` and a `type_comment`
# are names rather than expressions; and a PEP 695 `type_params` bound is
# evaluated when the type parameter is first used rather than when the object
# is built.
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
    'bases': ('base',),
    'keywords.value': ('keyword',),
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

# The one field of each object kind that looks definition-time and is not: a
# type parameter's bound, constraint or default runs when the parameter is
# first used. Each row measures that against the real runtime rather than
# asserting it, so a Python that changes the answer turns the pin red.
_LAZY_ROWS = (
    (ast.FunctionDef, _LIST, f'def g[T: ({_POP} or int)](a):\n    pass'),
    (ast.ClassDef, _CLASS_OBJECT, f'class C[T: ({_POP} or int)]:\n    pass'))


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


def _keyword_fields():
    """The `ast` field paths a class keyword evaluates at definition time,
    derived from the keyword node's own fields. Its `arg` is a base name
    rather than an expression."""
    found = set()
    for field in ast.keyword._fields:
        if field == 'value':
            found.add('keywords.value')
        elif field != 'arg':
            raise AssertionError(f'unclassified `keyword.{field}`')
    return found


def _definition_time_fields(node_type):
    """The `ast` field paths an object definition evaluates as it builds the
    object, derived from the node class's own fields. A field in neither set
    raises: a field a future Python adds is unclassified here rather than
    silently unreached by the guard."""
    found = set()
    for field in node_type._fields:
        if field in _FIELD_ROWS:
            found.add(field)
        elif field == 'args':
            found.update(_argument_fields())
        elif field == 'keywords':
            found.update(_keyword_fields())
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
            ast.Lambda: 'lambda', ast.ClassDef: 'class'}[node_type]
    return f'{stem}-{name}'


def _rows():
    for node_type, rows in _ROWS.items():
        for name, store, header in rows:
            yield _label(node_type, name), store, header


def _verdict(tmp, store, header):
    return _tracked_focus_verdict(
        tmp, f'{_PRE}{store}\n{header}{_SEND}{_READ}', counts=True)


def _swap(text):
    return re.sub(r'relay|quiet',
                  lambda m: 'quiet' if m[0] == 'relay' else 'relay', text)


def test_every_binder_position_fails_closed(tmp):
    observed = [(name, _verdict(tmp, store, header))
                for name, store, header in _rows()]
    missed = [item for item in observed if item[1] != (1, 1)]
    assert not missed, missed


def test_every_binder_position_twin_stays_clean(tmp):
    observed = [(name, _verdict(tmp, _swap(store), _swap(header)))
                for name, store, header in _rows()]
    flagged = [item for item in observed if item[1] != (0, 0)]
    assert not flagged, flagged


def test_reaching_a_binder_position_is_not_itself_a_finding(tmp):
    observed = [(name, _verdict(tmp, store, header))
                for name, store, header, _ in _QUIET]
    wrong = [item for item, row in zip(observed, _QUIET)
             if item[1] != row[3]]
    assert not wrong, wrong


def test_every_definition_time_field_is_covered(tmp):
    for node_type, rows in _ROWS.items():
        derived = _definition_time_fields(node_type)
        assert _covered(node_type) == {name for name, _, _ in rows}, (
            node_type.__name__, sorted(derived))
        for field in derived:
            assert field in _FIELD_ROWS, field
    assert _covered(ast.FunctionDef) == _covered(ast.AsyncFunctionDef)
    # Every derived field is either a row or a declared-illegal position, so
    # a lambda annotation that ever became legal would surface here.
    for node_type in _ROWS:
        assert _covered(node_type) | _ILLEGAL.get(
            node_type, frozenset()) == {
                row for field in _definition_time_fields(node_type)
                for row in _FIELD_ROWS[field]}


def test_a_type_parameter_bound_is_not_a_definition_time_position(tmp):
    if sys.version_info >= (3, 14):
        return
    assert all(_verdict(tmp, store, header) == (0, 0)
               for _, store, header in _LAZY_ROWS)


def main():
    return _util.runner(_util.collect(globals()),
                        tmp_prefix='binderpositions_')


if __name__ == '__main__':
    raise SystemExit(main())
