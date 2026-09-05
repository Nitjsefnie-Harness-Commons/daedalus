#!/usr/bin/env python3
"""Closed grammar and scope controls for the builtin-dict exemption."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_guard import _synthetic_violations  # noqa: E402
from test_coverage_scope_bindings import (  # noqa: E402
    _IMPORT_LAUNCH, _unresolved_dict)


# Name(Store) covers target grammar; string fields cover the other binders.
_BINDING_FORMS = (
    ('assign', '{name} = helper\n{launch}'),
    ('chain', 'other = {name} = helper\n{launch}'),
    ('tuple', 'other, {name} = values\n{launch}'),
    ('list', '[other, {name}] = values\n{launch}'),
    ('starred', 'other, *{name} = values\n{launch}'),
    ('nested', '[other, (*{name},)] = values\n{launch}'),
    ('annotated', '{name}: object\n{launch}'),
    ('annotated-value', '{name}: object = helper\n{launch}'),
    ('augmented', '{name} += helper\n{launch}'),
    ('delete', 'del {name}\n{launch}'),
    ('for', 'for {name} in values:\n    {launch}'),
    ('async-for', 'async def f():\n    async for {name} in values:\n'
     '        {launch}'),
    ('with', 'with manager() as {name}:\n    {launch}'),
    ('with-unpack', 'with manager() as (other, {name}):\n    {launch}'),
    ('async-with', 'async def f():\n    async with manager() as {name}:\n'
     '        {launch}'),
    ('except', 'try: pass\nexcept Exception as {name}:\n    {launch}'),
    ('except-star', 'try: pass\nexcept* Exception as {name}:\n    {launch}'),
    ('walrus', '({name} := helper)\n{launch}'),
    ('walrus-comprehension', '[({name} := item) for item in values]\n'
     '{launch}'),
    ('def', 'def {name}(): pass\n{launch}'),
    ('async-def', 'async def {name}(): pass\n{launch}'),
    ('class', 'class {name}: pass\n{launch}'),
    ('positional-only', 'def f({name}, /):\n    {launch}'),
    ('positional', 'def f({name}):\n    {launch}'),
    ('keyword-only', 'def f(*, {name}):\n    {launch}'),
    ('vararg', 'def f(*{name}):\n    {launch}'),
    ('kwarg', 'def f(**{name}):\n    {launch}'),
    ('lambda', 'lambda {name}: {launch}'),
    ('list-comprehension', '[{launch} for {name} in values]'),
    ('set-comprehension', '{{{launch} for {name} in values}}'),
    ('dict-comprehension', '{{key: {launch} for {name} in values}}'),
    ('generator', '({launch} for {name} in values)'),
    ('async-comprehension', 'async def f():\n'
     '    [{launch} async for {name} in values]'),
    ('match-capture', 'match value:\n    case {name}:\n        {launch}'),
    ('match-as', 'match value:\n    case [item] as {name}:\n'
     '        {launch}'),
    ('match-star', 'match value:\n    case [*{name}]:\n        {launch}'),
    ('match-rest', "match value:\n    case {{'k': item, **{name}}}:\n"
     '        {launch}'),
    ('match-or', 'match value:\n    case [0, {name}] | [1, {name}]:\n'
     '        {launch}'),
    ('import', 'import {name}\n{launch}'),
    ('dotted-import', 'import {name}.tools\n{launch}'),
    ('import-alias', 'import helpers.tools as {name}\n{launch}'),
    ('from-import', 'from helpers import {name}\n{launch}'),
    ('from-alias', 'from helpers import launcher as {name}\n{launch}'),
)


def _assert_bound_pair(label, template):
    source = template.format(name='dict', launch=_IMPORT_LAUNCH) + '\n'
    line = source[:source.index(_IMPORT_LAUNCH)].count('\n') + 1
    assert _synthetic_violations(source) == _unresolved_dict(line), (
        label, source, _synthetic_violations(source))
    sibling = template.format(name='dictionary', launch=_IMPORT_LAUNCH)
    assert _synthetic_violations(sibling + '\n') == [], (label, sibling)


def test_every_grammar_binding_removes_only_its_builtin_exemption(tmp):
    del tmp
    for label, template in _BINDING_FORMS:
        _assert_bound_pair(label, template)


def test_type_aliases_and_parameters_bind_their_evaluating_scope(tmp):
    del tmp
    if not hasattr(ast, 'TypeAlias'):
        return
    _assert_bound_pair('type-alias', 'type {name} = int\n{launch}')
    for parameter in ('{name}', '*{name}', '**{name}'):
        templates = (
            'def f[' + parameter + ']():\n    {launch}',
            'async def f[' + parameter + ']():\n    {launch}',
            'class C[' + parameter + ']:\n    {launch}',
            'type A[' + parameter + '] = {launch}',
        )
        for template in templates:
            _assert_bound_pair(parameter, template)
    for template in (
            'def f[{name}](arg: {launch}): pass',
            'def f[{name}]() -> {launch}: pass',
            'class C[{name}]({launch}): pass',
            'def f[{name}: {launch}](): pass'):
        _assert_bound_pair('annotation-scope', template)
    for definition in ('def f[dict](): pass', 'class C[dict]: pass',
                       'type A[dict] = dict'):
        assert _synthetic_violations(definition + '\n' + _IMPORT_LAUNCH) == []
    assert _synthetic_violations(
        'def f[dict](arg=dict(cwd=tmp)): pass\n') == []


_STAR_IMPORT = 'from review_launchers import *\n' + _IMPORT_LAUNCH + '\n'
_GLOBAL_IMPORT = ('def bind():\n    global dict\n'
                  '    from review_launchers import dict\nbind()\n'
                  + _IMPORT_LAUNCH + '\n')


def test_star_and_global_import_reproductions_are_refused(tmp):
    del tmp
    assert _synthetic_violations(_STAR_IMPORT) == _unresolved_dict(2)
    assert _synthetic_violations(_GLOBAL_IMPORT) == _unresolved_dict(5)
    assert _synthetic_violations(
        'from review_launchers import helper\n' + _IMPORT_LAUNCH) == []
    assert _synthetic_violations(
        _GLOBAL_IMPORT.replace('global dict', 'global other')
        .replace('import dict', 'import other')) == []


def test_declarations_route_bindings_to_the_declared_scope(tmp):
    del tmp
    for statement in ('{name} = helper', 'from helpers import {name}',
                      'import helpers as {name}', '{name}: object',
                      'for {name} in values: pass'):
        # The module launch must see a global declared inside a function.
        template = ('def f():\n    global {name}\n    ' + statement
                    + '\n{launch}')
        _assert_bound_pair('global-' + statement, template)
    _assert_bound_pair('global-declaration-only',
                       'def f():\n    global {name}\n{launch}')
    _assert_bound_pair('nonlocal',
                       'def outer():\n    {name} = helper\n'
                       '    def bind():\n        nonlocal {name}\n'
                       '        from helpers import {name}\n'
                       '    {launch}')
    _assert_bound_pair('nonlocal-through-class',
                       'def outer():\n    {name} = helper\n'
                       '    class Inner:\n        def bind():\n'
                       '            nonlocal {name}\n'
                       '    {launch}')
    from _coverage_scopes import (  # noqa: PLC0415
        _evaluation_scopes, _scope_bindings)
    source = ('def outer():\n    dict = helper\n'
              '    def middle():\n        def bind():\n'
              '            nonlocal dict\n')
    tree = ast.parse(source)
    scoped, parents = _evaluation_scopes(tree)
    bindings = _scope_bindings(scoped, parents)
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
    assert 'dict' in bindings[functions['outer']]
    assert 'dict' not in bindings[functions['middle']]
    assert 'dict' not in bindings[functions['bind']]
    assert 'dict' not in bindings[tree]


def test_generic_annotations_see_class_names_without_tainting_methods(tmp):
    del tmp
    if not hasattr(ast, 'TypeAlias'):
        return
    for template in (
            'class C:\n    {name} = helper\n'
            '    def f[T](value: {launch}): pass',
            'class C:\n    {name} = helper\n'
            '    class Inner[T]({launch}): pass',
            'class C:\n    {name} = helper\n'
            '    type Alias[T] = {launch}'):
        _assert_bound_pair('class-annotation', template)
    for body in ('def f[T]():\n        ' + _IMPORT_LAUNCH,
                 'class Inner[T]:\n        ' + _IMPORT_LAUNCH):
        source = 'class C:\n    dict = helper\n    ' + body
        assert _synthetic_violations(source) == [], source


def test_missing_binding_information_cannot_prove_a_builtin(tmp):
    del tmp
    from _coverage_scopes import _name_is_unbound  # noqa: PLC0415
    scope = ast.Module(body=[], type_ignores=[])
    assert _name_is_unbound('dict', scope, {scope: set()}, {scope: None})
    assert not _name_is_unbound('dict', None, {}, {})
    assert not _name_is_unbound('dict', scope, {}, {scope: None})
    assert not _name_is_unbound('dict', scope, {scope: set()}, {})


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
