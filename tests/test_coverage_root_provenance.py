#!/usr/bin/env python3
"""ROOT proofs must survive unrelated bindings."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _binding_assertions import _rebound_owner  # noqa: E402
from _coverage_mutation_specs import (  # noqa: E402
    _DECLARED_FORMS, _DERIVATION, _DERIVATIONS,
    _PERMITTED_BINDINGS, _ROOT_BINDING_FORMS)
from _coverage_guard import _synthetic_violations  # noqa: E402
from _coverage_scopes import (  # noqa: E402
    _evaluation_scopes, _scope_bindings, _scope_shadows, _shadowed_names)


_PRELUDE = ('import os\nimport subprocess\nfrom _repo import ROOT\n'
            'from pathlib import Path\n')


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


def _assert_root_binding_site(form):
    name, binding = _ROOT_BINDING_FORMS[form]
    prefix = (_PRELUDE + _DERIVATION if name == 'star-import'
              else _ROOT_PREFIX)
    source = prefix + binding
    if name in {'comprehension', 'parameter', 'vararg', 'kwarg',
                'uninitialized-annotation', 'same-name-alias'}:
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


# Other-scope bindings and Path names unread by owners leave module ROOT alone.
def _permitted_transitions():
    for derivation, name, form, binding in _PERMITTED_BINDINGS:
        prefix = 'import os\nimport subprocess\n'
        accepted = prefix + _DERIVATIONS[derivation] + binding
        refused = (prefix + _DERIVATIONS[
            derivation if name == 'ROOT' else 'constructor']
            + f'{name} = other\n')
        for expression in ('ROOT', 'str(ROOT)'):
            for consumer in ('cwd', 'chdir'):
                launch = (f'subprocess.run(c, cwd={expression})\n'
                          if consumer == 'cwd' else
                          f'os.chdir({expression})\nsubprocess.run(c)\n')
                key = f'{derivation}/{name}/{form}/{expression}/{consumer}'
                yield key, accepted + launch, refused + launch


def _assert_permitted_transitions(name, consumer):
    for key, accepted, refused in _permitted_transitions():
        if key.split('/')[1] != name or not key.endswith('/' + consumer):
            continue
        assert _synthetic_violations(accepted) == [], key
        violations = _synthetic_violations(refused)
        assert len(violations) == 1, (key, violations)
        assert 'declares no env=' in violations[0], (key, violations)


def test_permitted_root_transitions_keep_refusing_twins(tmp):
    del tmp
    for name in ('ROOT', 'Path'):
        for consumer in ('cwd', 'chdir'):
            _assert_permitted_transitions(name, consumer)


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
