#!/usr/bin/env python3
"""Declaration destinations and binding-site controls for root proofs."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    if name in {'comprehension', 'parameter', 'vararg', 'kwarg',
                'uninitialized-annotation'}:
        assert _synthetic_violations(
            source + 'subprocess.run(c, cwd=ROOT)\n') == [], binding
        assert 'ROOT' not in _shadowed_names(ast.parse(source)), binding
        return
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


def test_type_parameters_shadow_each_root_proof_role(tmp):
    del tmp
    if not hasattr(ast, 'TypeVar'):
        return
    for parameter in ('ROOT', 'str', '_util', '*ROOT', '**ROOT'):
        name = parameter.lstrip('*')
        expression = {'ROOT': 'ROOT', 'str': 'str(ROOT)',
                      '_util': '_util.ROOT'}[name]
        prefix = (_PRELUDE + 'import _util\n'
                  + f'def go[{parameter}]():\n')
        for chdir in (False, True):
            _assert_launch(prefix, '    ', expression, chdir)
        source = prefix + '    pass\n'
        assert name in _shadowed_names(ast.parse(source)), parameter
        assert _synthetic_violations(
            source + f'subprocess.run(c, cwd={expression})\n') == []


def test_local_root_bindings_leave_the_module_root_alone(tmp):
    del tmp
    for binding in ('import helpers as ROOT', 'ROOT = other', 'ROOT: object'):
        prefix = _ROOT_PREFIX + f'def f():\n    {binding}\n'
        _assert_launch(prefix, '    ', 'ROOT', False)
        _assert_launch(prefix, '    ', 'ROOT', True)
        assert _synthetic_violations(
            prefix + '    pass\nsubprocess.run(c, cwd=ROOT)\n') == []
    prefix = (_ROOT_PREFIX + 'def change():\n    global ROOT\n'
              '    ROOT = other\n')
    _assert_launch(prefix, '', 'ROOT', False)
    _assert_launch(prefix, '', 'ROOT', True)
    prefix = (_ROOT_PREFIX + 'def outer():\n    ROOT = other\n'
              '    def inner():\n        nonlocal ROOT\n'
              '        ROOT = other\n')
    _assert_launch(prefix, '        ', 'ROOT', False)
    assert _synthetic_violations(
        prefix + 'subprocess.run(c, cwd=ROOT)\n') == []


def test_root_alias_owner_and_bare_roles_reach_both_consumers(tmp):
    del tmp
    for module in ('_util', 'test_dashboard_behaviour'):
        prefix = f'import os\nimport subprocess\nimport {module} as ROOT\n'
        _assert_launch(prefix, '', 'ROOT', False)
        _assert_launch(prefix, '', 'ROOT', True)
        for expression in ('ROOT.ROOT', 'str(ROOT.ROOT)'):
            assert _synthetic_violations(
                prefix + f'subprocess.run(c, cwd={expression})\n') == []
            assert _synthetic_violations(
                prefix + f'os.chdir({expression})\nsubprocess.run(c)\n') == []


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
    'scope': ("                if self.destinations[scope].get('ROOT', scope) "
              "is self.tree\n", "                if True\n"),
    'annotation': ("                and node not in annotations}\n",
                   "                }\n"),
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
    'comprehension': 'scope', 'parameter': 'scope',
    'vararg': 'scope', 'kwarg': 'scope',
    'uninitialized-annotation': 'annotation',
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
         (("            and not _other_root_bindings(facts, root_values)\n",
           ''),),
         _INVOKE + 'root_suite.test_every_root_binding_site_'
         'must_be_accepted(None)'),
)


_ROOT_PROVENANCE_MUTATIONS += ((
    ('type parameters enter the shared shadow census', 'scopes',
     (("                         *_TYPE_PARAMETERS)):\n",
       "                         )):\n"),),
     _INVOKE + 'root_suite.test_type_parameters_shadow_each_root_'
     'proof_role(None)'),
    ('root proofs use annotation scopes', 'scopes',
     (("def _evaluation_scopes(tree, type_scopes=True):\n",
       "def _evaluation_scopes(tree, type_scopes=False):\n"),),
     _INVOKE + 'root_suite.test_type_parameters_shadow_each_root_'
     'proof_role(None)'),
    ('parameters enter their binding scope', 'scopes',
     (("            scoped.append((argument, binding_scope))\n", ''),),
     _INVOKE + 'root_suite.test_import_markers_reach_the_'
     'declared_destination(None); '
     'import test_coverage_name_bindings as names; '
     'names.test_every_grammar_binding_removes_only_its_builtin_'
     'exemption(None)'),
) if hasattr(ast, 'TypeVar') else ())

_ROOT_PROVENANCE_MUTATIONS += ((
    ('scoped shadows read the binding census', 'scopes',
     (("            shadows[scope].update(_bound_names(node))\n",
       "            pass\n"),),
     _INVOKE + 'root_suite.test_type_parameters_shadow_each_root_'
     'proof_role(None)'),
    ('aggregate shadows read the binding census', 'scopes',
     (("    names = set().union(*(_bound_names(node) "
       "for node in memo_nodes(tree)\n"
       "                          if not isinstance(node, (ast.Import,\n"
       "                                                   ast.ImportFrom))))"
       "\n", "    names = set()\n"),),
     _INVOKE + 'root_suite.test_type_parameters_shadow_each_root_'
     'proof_role(None)'),
) if hasattr(ast, 'TypeVar') else ())

_ROOT_PROVENANCE_MUTATIONS += (
    ('ROOT assignments use their destination scope', 'scopes',
     (("                if target in self.root_scope_nodes}\n",
       "                }\n"),),
     _INVOKE + 'root_suite.test_local_root_bindings_leave_the_module_'
     'root_alone(None)'),
    ('ROOT census resolves global destinations', 'scopes',
     (("                if self.destinations[scope].get('ROOT', scope) "
       "is self.tree\n", "                if scope is self.tree\n"),),
     _INVOKE + 'root_suite.test_local_root_bindings_leave_the_module_'
     'root_alone(None)'),
)

_ROOT_PROVENANCE_MUTATIONS += (
    ('ROOT imports preserve the owner role', 'scopes',
     (("                              if bound in {'Path', 'str', 'ROOT'} "
       "else bound)\n",
       "                              if bound in {'Path', 'str'} "
       "else bound)\n"),),
     _INVOKE + 'root_suite.test_root_alias_owner_and_bare_roles_'
     'reach_both_consumers(None)'),
    ('absent bare ROOT leaves the owner role alone', 'scopes',
     (("        names.add('ROOT()')\n", "        names.add('ROOT')\n"),),
     _INVOKE + 'root_suite.test_root_alias_owner_and_bare_roles_'
     'reach_both_consumers(None)'),
    ('bare ROOT consults its proof marker', 'scopes',
     (("        return 'ROOT()' not in shadowed_names\n",
       "        return True\n"),),
     _INVOKE + 'root_suite.test_root_alias_owner_and_bare_roles_'
     'reach_both_consumers(None)'),
)


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
