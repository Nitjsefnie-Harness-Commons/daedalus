#!/usr/bin/env python3
"""Declaration destinations and binding-site controls for root proofs."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_guard import _synthetic_violations  # noqa: E402
from _coverage_scopes import (  # noqa: E402
    _evaluation_scopes, _scope_bindings, _scope_shadows, _shadowed_names)
from test_coverage_scope_bindings import _rebound_owner  # noqa: E402


_PRELUDE = ('import os\nimport subprocess\nfrom _repo import ROOT\n'
            'from pathlib import Path\n')
_DECLARED_FORMS = (
    ('ROOT', 'from _repo import ROOT', 'ROOT = other', 'ROOT'),
    ('str', 'str = str', 'from helpers import str', 'str(ROOT)'),
    ('Path', 'from pathlib import Path', 'from helpers import Path', 'ROOT'),
)
_DERIVATION = 'ROOT = Path(__file__).resolve().parents[1]\n'


def _declared_source(declaration, name, seed, binding):
    if declaration == 'global':
        return (_PRELUDE + f'def change():\n    global {name}\n'
                f'    {binding}\n'), ''
    return (_PRELUDE + f'def outer():\n    {seed}\n'
            '    def middle():\n        def change():\n'
            f'            nonlocal {name}\n            {binding}\n'), '    '


def _assert_launch(prefix, indent, expression, chdir):
    line = prefix.count('\n') + 1
    if chdir:
        source = (prefix + indent + f'os.chdir({expression})\n'
                  + indent + 'subprocess.run(c)\n')
        expected = [f'tests/synthetic.py:{line + 1}: subprocess.run '
                    f'os.chdir at line {line} may have moved the cwd '
                    'declares no env=']
    else:
        source = prefix + indent + f'subprocess.run(c, cwd={expression})\n'
        expected = _rebound_owner(line, expression)
    assert _synthetic_violations(source) == expected, source


def test_declared_bindings_reach_explicit_cwd(tmp):
    del tmp
    for declaration in ('global', 'nonlocal'):
        for name, seed, binding, expression in _DECLARED_FORMS:
            prefix, indent = _declared_source(declaration, name, seed, binding)
            if name == 'Path':
                prefix += indent + _DERIVATION
            _assert_launch(prefix, indent, expression, False)


def test_declared_bindings_reach_chdir(tmp):
    del tmp
    for declaration in ('global', 'nonlocal'):
        for name, seed, binding, expression in _DECLARED_FORMS:
            prefix, indent = _declared_source(declaration, name, seed, binding)
            if name == 'Path':
                prefix += indent + _DERIVATION
            _assert_launch(prefix, indent, expression, True)


def _assert_declared_destination(declaration, form):
    name, seed, binding, _ = _DECLARED_FORMS[form]
    source, _ = _declared_source(declaration, name, seed, binding)
    tree = ast.parse(source)
    scoped, parents = _evaluation_scopes(tree)
    shadows = _scope_shadows(tree, (scoped, parents))
    bindings = _scope_bindings(scoped, parents)
    marker = name if name == 'ROOT' else name + '()'
    expected = 'module' if declaration == 'global' else 'outer'
    marked = {getattr(scope, 'name', 'module')
              for scope, names in shadows.items() if marker in names}
    assert marked == {expected}, (declaration, name, marked)
    change = next(node for node in parents
                  if getattr(node, 'name', '') == 'change')
    assert name not in bindings[change], (declaration, name)


def test_import_markers_reach_the_declared_destination(tmp):
    del tmp
    # Outer str already has a store shadow; its import marker must move too.
    for declaration in ('global', 'nonlocal'):
        for form in range(len(_DECLARED_FORMS)):
            _assert_declared_destination(declaration, form)


_ROOT_PREFIX = ('import os\nimport subprocess\nimport _util\n'
                'ROOT = _util.ROOT\n')
_ROOT_BINDING_FORMS = (
    ('for', 'for ROOT in items:\n    pass\n'),
    ('walrus', '(ROOT := other)\n'),
    ('with', 'with manager as ROOT:\n    pass\n'),
    ('except', 'try:\n    pass\nexcept Exception as ROOT:\n    pass\n'),
    ('match-as', 'match value:\n    case ROOT:\n        pass\n'),
    ('match-star', 'match value:\n    case [*ROOT]:\n        pass\n'),
    ('match-rest', 'match value:\n    case {**ROOT}:\n        pass\n'),
    ('comprehension', '[ROOT for ROOT in items]\n'),
    ('del', 'del ROOT\n'),
    ('augmented', 'ROOT += other\n'),
    ('function', 'def ROOT():\n    pass\n'),
    ('async-function', 'async def ROOT():\n    pass\n'),
    ('class', 'class ROOT:\n    pass\n'),
    ('parameter', 'def f(ROOT):\n    pass\n'),
    ('vararg', 'def f(*ROOT):\n    pass\n'),
    ('kwarg', 'def f(**ROOT):\n    pass\n'),
    ('import', 'import helpers as ROOT\n'),
    ('from-alias', 'from helpers import other as ROOT\n'),
    ('same-name-alias', 'from helpers import ROOT as ROOT\n'),
    ('mixed-import', 'from helpers import ROOT, other as ROOT\n'),
    ('star-import', 'from helpers import *\n'),
    ('uninitialized-annotation', 'ROOT: object\n'),
    ('unaccepted-assignment', 'ROOT = other\n'),
)


def _assert_root_binding_site(form):
    name, binding = _ROOT_BINDING_FORMS[form]
    prefix = (_PRELUDE + _DERIVATION if name == 'star-import'
              else _ROOT_PREFIX)
    source = prefix + binding
    _assert_launch(source, '', 'ROOT', False)
    _assert_launch(source, '', 'ROOT', True)
    assert 'ROOT' in _shadowed_names(ast.parse(source)), binding


def test_every_root_binding_site_must_be_accepted(tmp):
    del tmp
    for form in range(len(_ROOT_BINDING_FORMS)):
        _assert_root_binding_site(form)


def test_only_accepted_root_bindings_discard_the_shadow(tmp):
    del tmp
    for extra in ('', 'ROOT = _util.ROOT\n', 'ROOT: object = _util.ROOT\n',
                  'from helpers import ROOT\n',
                  'from helpers import ROOT, unrelated\n',
                  'def f():\n    from helpers import ROOT\n'):
        source = _ROOT_PREFIX + extra
        assert 'ROOT' not in _shadowed_names(ast.parse(source)), source
        assert _synthetic_violations(
            source + 'subprocess.run(c, cwd=ROOT)\n') == [], source
        assert _synthetic_violations(
            source + 'import os\nos.chdir(ROOT)\nsubprocess.run(c)\n') == []


_INVOKE = 'import test_coverage_root_provenance as root_suite; '
_ROUTING = (
    "    return _routed_bindings(shadows, destinations)\n",
    "    return shadows\n")
_ROOT_PROVENANCE_MUTATIONS = (
    ('global shadows reach explicit cwd', 'scopes', (_ROUTING,),
     _INVOKE + 'root_suite.test_declared_bindings_reach_explicit_cwd(None)'),
    ('global shadows reach chdir', 'scopes', (_ROUTING,),
     _INVOKE + 'root_suite.test_declared_bindings_reach_chdir(None)'),
    ('global import shadows reach root assignments', 'scopes',
     (("    names = _routed_bindings(imports, destinations)[tree]\n",
       "    names = imports[tree]\n"),),
     _INVOKE + 'root_suite.test_declared_bindings_reach_explicit_cwd(None)'),
    ('global destinations are shared', 'scopes',
     (("    destinations = {scope: dict.fromkeys(names, module)\n",
       "    destinations = {scope: {}\n"),),
     _INVOKE + 'root_suite.test_import_markers_reach_the_'
     'declared_destination(None)'),
    ('nonlocal destinations are shared', 'scopes',
     (("            destinations[scope][name] = module "
       "if target is None else target\n",
       "            destinations[scope][name] = scope\n"),),
     _INVOKE + 'root_suite.test_import_markers_reach_the_'
     'declared_destination(None)'),
) + tuple(
    (f'{declaration} {name} routes its proof role', 'scopes',
     (("            target = destinations[scope].get("
       "name.removesuffix('()'), scope)\n",
       "            target = scope\n" if name == 'ROOT' else
       "            target = destinations[scope].get(name, scope)\n"),),
     _INVOKE + f'root_suite._assert_declared_destination('
     f'{declaration!r}, {form})')
    for declaration in ('global', 'nonlocal')
    for form, (name, _, _, _) in enumerate(_DECLARED_FORMS))


_NAME_BINDING = (
    "    if (isinstance(node, ast.Name)\n"
    "            and isinstance(node.ctx, (ast.Store, ast.Del))):\n"
    "        return {node.id}\n")
_DEFINITION_BINDING = (
    "    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, "
    "ast.ClassDef,\n                         *_TYPE_PARAMETERS)):\n")
_PATTERN_BINDING = (
    "    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, "
    "ast.MatchStar)):\n")
_SITE_MUTANTS = {
    'name': (_NAME_BINDING, ''),
    'del': (_NAME_BINDING, _NAME_BINDING.replace(
        '(ast.Store, ast.Del)', 'ast.Store')),
    'parameter': ("    if isinstance(node, ast.arg):\n"
                  "        return {node.arg}\n", ''),
    'import': ("    if isinstance(node, (ast.Import, ast.ImportFrom)):\n"
               "        return {_import_bound_name(node, alias) "
               "for alias in node.names}\n", ''),
    'star-import': ("        if not _bound_names(node) "
                    "& {'ROOT', _ALL_NAMES}:\n",
                    "        if 'ROOT' not in _bound_names(node):\n"),
    'match-rest': ("    if isinstance(node, ast.MatchMapping):\n"
                   "        return {node.rest} if node.rest else set()\n", ''),
    'unaccepted-assignment': (
        "            and all(_is_repository_root_binding(value, owners)\n"
        "                    for value in root_values.values())):\n",
        "            ):\n"),
}
_SITE_MUTANTS.update(
    (name, (needle, needle.replace(kind + ', ', '').replace(', ' + kind, '')))
    for name, needle, kind in (
        ('function', _DEFINITION_BINDING, 'ast.FunctionDef'),
        ('async-function', _DEFINITION_BINDING, 'ast.AsyncFunctionDef'),
        ('class', _DEFINITION_BINDING, 'ast.ClassDef'),
        ('except', _PATTERN_BINDING, 'ast.ExceptHandler'),
        ('match-as', _PATTERN_BINDING, 'ast.MatchAs'),
        ('match-star', _PATTERN_BINDING, 'ast.MatchStar')))
_SITE_KINDS = {
    'vararg': 'parameter', 'kwarg': 'parameter',
    'from-alias': 'import', 'same-name-alias': 'import',
    'mixed-import': 'import',
}
_ROOT_PROVENANCE_MUTATIONS += tuple(
    (f'ROOT census includes {name}', 'scopes',
     (_SITE_MUTANTS[_SITE_KINDS.get(
         name, name if name in _SITE_MUTANTS else 'name')],),
     _INVOKE + f'root_suite._assert_root_binding_site({form})')
    for form, (name, _) in enumerate(_ROOT_BINDING_FORMS)) + (
        ('ROOT census excludes accepted assignment targets', 'scopes',
         (("        if node in assignments:\n            continue\n", ''),),
         _INVOKE + 'root_suite.test_only_accepted_root_bindings_'
         'discard_the_shadow(None)'),
        ('ROOT census allows literal imports', 'scopes',
         (("                or _canonical_import(node, alias, 'ROOT')\n",
           "                or False\n"),),
         _INVOKE + 'root_suite.test_only_accepted_root_bindings_'
         'discard_the_shadow(None)'),
        ('ROOT census ignores nonbinding nodes', 'scopes',
         (("        if not _bound_names(node) & {'ROOT', _ALL_NAMES}:\n"
           "            continue\n", ''),),
         _INVOKE + 'root_suite.test_only_accepted_root_bindings_'
         'discard_the_shadow(None)'),
        ('ROOT census gates the assignment exemption', 'scopes',
         (("            and not _other_root_bindings(tree, root_values)\n",
           ''),),
         _INVOKE + 'root_suite.test_every_root_binding_site_'
         'must_be_accepted(None)'),
)


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
