#!/usr/bin/env python3
"""Declaration destinations and binding-site controls for root proofs."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _coverage_guard import _synthetic_violations  # noqa: E402
from _coverage_scopes import (  # noqa: E402
    _evaluation_scopes, _scope_bindings, _scope_shadows)
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


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
