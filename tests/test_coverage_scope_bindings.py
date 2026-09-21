#!/usr/bin/env python3
"""Shared scope controls for coverage and Bash launch guards."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _coverage_guard import (  # noqa: E402
    _coverage_environment_violations, _synthetic_violations)


_IMPORT_LAUNCH = "dict(['python3', 'child.py'], cwd=tmp)"
_ANNOTATED_LAUNCH = "subprocess.run(['python3', 'child.py'])"
_DECLARATION = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
_REAL_MODULE = 'tests/test_diff_coverage.py'
_ROOT_PROVENANCE_INVOKE = (
    'import test_coverage_scope_bindings as binding_suite; '
    'binding_suite.test_import_bindings_do_not_rebind_root_'
    'spellings(None)')
_ROOT_PROVENANCE_MUTATIONS = (
    ('a star import refuses every root spelling', 'scopes',
     (("    if _ALL_NAMES in shadowed_names:\n        return False\n",
       ""),), _ROOT_PROVENANCE_INVOKE),
    ('a rebound proof name is unprovable', 'scopes',
     (("    names = _routed_bindings(imports, destinations)[tree]\n",
       "    names = set()\n"),), _ROOT_PROVENANCE_INVOKE),
    ('Path is independently a proof name', 'scopes',
     (("_PROOF_NAMES = frozenset({'Path', 'str', 'ROOT', _ALL_NAMES})\n",
       "_PROOF_NAMES = frozenset({'str', 'ROOT', _ALL_NAMES})\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('str is independently a proof name', 'scopes',
     (("_PROOF_NAMES = frozenset({'Path', 'str', 'ROOT', _ALL_NAMES})\n",
       "_PROOF_NAMES = frozenset({'Path', 'ROOT', _ALL_NAMES})\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('a canonical import is exact', 'scopes',
     (("            and (node.module, alias.name) == "
       "_CANONICAL_MEMBERS.get(bound))\n",
       "            and bound in _CANONICAL_MEMBERS)\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('a relative import is not canonical', 'scopes',
     (("    return (not node.level\n", "    return (True\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('scope shadows carry the unprovable names', 'scopes',
     (("    shadows[tree].update(unprovable)\n", ""),),
     _ROOT_PROVENANCE_INVOKE),
    ('module shadows carry the unprovable names', 'scopes',
     (("    names.update(unprovable)\n", ""),),
     _ROOT_PROVENANCE_INVOKE),
    ('the _util.ROOT arm needs an owner', 'scopes',
     (("        return '_util' in owners\n", "        return True\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('an unbound ROOT is unprovable', 'scopes',
     (("    if not facts.root_assignments and not root_imported:\n"
       "        names.add('ROOT')\n", ""),), _ROOT_PROVENANCE_INVOKE),
    ('owner imports never prove constructors or builtins', 'scopes',
     (("    if isinstance(node, ast.Import):\n        return False\n",
       "    if isinstance(node, ast.Import):\n"
       "        return alias.name in _ROOT_MODULES\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('proof shadows distinguish calls from owner attributes', 'scopes',
     (("                              if bound in {'Path', 'str'} else bound)"
       "\n", "                              if False else bound)\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('str calls consult their import proof shadow', 'scopes',
     (("        return ('str()' not in shadowed_names\n"
       "                and _is_root_spelling(node.args[0], "
       "shadowed_names, owners))\n",
       "        return _is_root_spelling(node.args[0], "
       "shadowed_names, owners)\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('Path assignments consult their import proof shadow', 'scopes',
     (("            and not {'Path', 'Path()', '_util'} & names\n",
       "            and not {'Path', '_util'} & names\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('a constructor import retires an owner', 'scopes',
     (("                        and alias.name in _ROOT_MODULES):\n",
       "                        and alias.name in _ROOT_MODULES) "
       "and not _canonical_import(node, alias, bound):\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('a literal ROOT import establishes provenance', 'scopes',
     (("                if canonical and bound == 'ROOT':\n"
       "                    root_imported = True\n", ""),),
     _ROOT_PROVENANCE_INVOKE),
    ('ROOT is independently a proof name', 'scopes',
     (("_PROOF_NAMES = frozenset({'Path', 'str', 'ROOT', _ALL_NAMES})\n",
       "_PROOF_NAMES = frozenset({'Path', 'str', _ALL_NAMES})\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('ROOT provenance requires the literal member', 'scopes',
     (("        return alias.name == 'ROOT' and alias.asname is None\n",
       "        return True\n"),), _ROOT_PROVENANCE_INVOKE),
    ('ROOT provenance excludes even a same-name alias', 'scopes',
     (("        return alias.name == 'ROOT' and alias.asname is None\n",
       "        return alias.name == 'ROOT'\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('local imports do not taint module proofs', 'scopes',
     (("    names = _routed_bindings(imports, destinations)[tree]\n",
       "    names = set().union(*imports.values())\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('chdir sees local import shadows', 'guard',
     (("                                       scopes[node], "
       "self.scope_shadows,\n",
       "                                       tree, self.scope_shadows,\n"
       ),), _ROOT_PROVENANCE_INVOKE),
    ('imports shadow their containing scope', 'scopes',
     (("        shadows[scope].update(imports.get(node, ()))\n",
       "        shadows[tree].update(imports.get(node, ()))\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('local imports still shadow local launches', 'scopes',
     (("        shadows[scope].update(imports.get(node, ()))\n",
       ""),), _ROOT_PROVENANCE_INVOKE),
    ('an assignment cannot erase a ROOT import shadow', 'scopes',
     (("            and not _other_root_bindings(facts, root_values)\n",
       ""),), _ROOT_PROVENANCE_INVOKE),
)


def _unresolved_dict(line):
    return [f'tests/synthetic.py:{line}: unresolved callee dict '
            'cwd=tmp declares no env=']


def _rebound_owner(line, spelling='behaviour.ROOT'):
    return [f'tests/synthetic.py:{line}: subprocess.run '
            f'cwd={spelling} declares no env=']


def _import_bindings():
    return (
        ('from-import', 'from helpers import dict'),
        ('import alias', 'import helpers as dict'),
        ('from-import alias', 'from helpers import thing as dict'),
        ('dotted import head', 'import dict.tools'),
        ('reserved member alias', 'from helpers import _util as dict'),
        ('reserved module alias', 'import _util as dict'),
        ('reserved behaviour alias',
         'from helpers import test_dashboard_behaviour as dict'),
    )


def _near_miss_bindings():
    return ('import dict_tools', 'import dict_tools.helpers',
            'from helpers import dicts', 'import helpers as dictionary')


def _import_scope_cases():
    return (
        ('a local import binds its own function', f"""import os
def go():
    from helpers import dict
    {_IMPORT_LAUNCH}
""", _unresolved_dict(4)),
        ('a local reserved-member alias binds too', f"""import os
def go():
    from helpers import _util as dict
    {_IMPORT_LAUNCH}
""", _unresolved_dict(4)),
        ('a module import reaches a nested def', f"""import os
import helpers as dict
def outer():
    def inner():
        {_IMPORT_LAUNCH}
""", _unresolved_dict(5)),
        ('a local import stops at its function', """import os
def bind():
    from helpers import dict
def other():
    kw = dict(cwd='x')
""", []),
        ('an unshadowed dict stays exempt', """dict(cwd='module')
def go():
    return dict(cwd='nested')
""", []),
    )


def _root_provenance_cases():
    return (
        ('a module ROOT import stays provable', """import subprocess
from _repo import ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", []),
        ('a function-local ROOT import stays provable', """import subprocess
def go():
    from _repo import ROOT
    subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", []),
        ('a later alias rebinds the owner', """import subprocess
import _util
import helpers as _util
subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""", _rebound_owner(4, '_util.ROOT')),
        ('a from-import rebinds the owner', """import subprocess
import _util
from helpers import _util
subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""", _rebound_owner(4, '_util.ROOT')),
        ('a from-import alias rebinds the owner', """import subprocess
import _util
from helpers import x as _util
subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""", _rebound_owner(4, '_util.ROOT')),
        ('a from-import rebinds an aliased owner', """import subprocess
import _util as u
from helpers import u
subprocess.run(['python3', 'child.py'], cwd=u.ROOT)
""", _rebound_owner(4, 'u.ROOT')),
        ('a function-local import rebinds the owner', """import subprocess
import _util
def go():
    from helpers import _util
    subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""", _rebound_owner(5, '_util.ROOT')),
        ('a star import rebinds every owner', """import subprocess
import _util
from helpers import *
subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""", _rebound_owner(4, '_util.ROOT')),
        ('a submodule import is not the genuine binding', """import subprocess
import _util
import _util.sub
subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""", _rebound_owner(4, '_util.ROOT')),
        ('ROOT through a rebound owner is not root', """import subprocess
import _util
from helpers import _util
ROOT = _util.ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(5, 'ROOT')),
        ('a star import leaves a bare ROOT unprovable', """import subprocess
from helpers import *
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(3, 'ROOT')),
        ('a star import leaves a derived ROOT unprovable', """import subprocess
from pathlib import Path
from helpers import *
ROOT = Path(__file__).resolve().parents[1]
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(5, 'ROOT')),
        ('a star import leaves str(ROOT) unprovable', """import subprocess
from helpers import *
subprocess.run(['python3', 'child.py'], cwd=str(ROOT))
""", _rebound_owner(3, 'str(ROOT)')),
        ('a star import leaves an imported ROOT unprovable',
         """import subprocess
from _repo import ROOT
from helpers import *
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(4, 'ROOT')),
        ('a star import leaves a chdir to ROOT unprovable', """import os
import subprocess
from helpers import *
os.chdir(ROOT)
subprocess.run(['python3', 'child.py'])
""", ['tests/synthetic.py:5: subprocess.run os.chdir at line 4 '
            'may have moved the cwd declares no env=']),
        ('a derived ROOT with no star import stays provable',
         """import subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", []),
        ('an import rebinding Path leaves ROOT unprovable',
         """import subprocess
from helpers import Path
ROOT = Path(__file__).resolve().parents[1]
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(4, 'ROOT')),
        ('a module import named Path leaves ROOT unprovable',
         """import subprocess
import helpers as Path
ROOT = Path(__file__).resolve().parents[1]
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(4, 'ROOT')),
        ('pathlib itself aliased as Path is a rebinding', """import subprocess
import pathlib as Path
ROOT = Path(__file__).resolve().parents[1]
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(4, 'ROOT')),
        ('a relative pathlib import is a rebinding', """import subprocess
from .pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(4, 'ROOT')),
        ('an import binding str leaves str(ROOT) unprovable',
         """import subprocess
from _repo import ROOT
from helpers import str
subprocess.run(['python3', 'child.py'], cwd=str(ROOT))
""", _rebound_owner(4, 'str(ROOT)')),
        ('a function-local import binding str is a rebinding',
         """import subprocess
from _repo import ROOT
def go():
    from helpers import str
    subprocess.run(['python3', 'child.py'], cwd=str(ROOT))
""", _rebound_owner(5, 'str(ROOT)')),
        ('a ROOT bound nowhere is unprovable', """import subprocess
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(2, 'ROOT')),
        ('a module import named ROOT is not a binding', """import subprocess
import helpers as ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(3, 'ROOT')),
        ('ROOT through a genuine owner stays provable', """import subprocess
import _util
ROOT = _util.ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", []),
        ('ROOT through an unimported _util is not root', """import subprocess
ROOT = _util.ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(3, 'ROOT')),
        ('ROOT through an aliased owner is not root', """import subprocess
import _util as u
ROOT = u.ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(4, 'ROOT')),
        ('ROOT through a mutated owner is not root', """import subprocess
import _util
_util.ROOT = replacement
ROOT = _util.ROOT
subprocess.run(['python3', 'child.py'], cwd=ROOT)
""", _rebound_owner(5, 'ROOT')),
        ('a root helper import is not a rebinding', """import subprocess
import test_dashboard_behaviour as behaviour
subprocess.run(['python3', 'child.py'], cwd=behaviour.ROOT)
""", []),
        ('storing a root owner still rebinds it', """import subprocess
import test_dashboard_behaviour as behaviour
behaviour = replacement
subprocess.run(['python3', 'child.py'], cwd=behaviour.ROOT)
""", _rebound_owner(4)),
    )


def _owner_role_cases():
    for module in ('_util', 'test_dashboard_behaviour'):
        for binding, line in (
                (f'import {module} as Path', 4),
                (f'from pathlib import Path\nimport {module} as Path', 5)):
            source = ('import subprocess\n' + binding + '\n'
                      'ROOT = Path(__file__).resolve().parents[1]\n'
                      'subprocess.run(c, cwd=ROOT)\n')
            yield binding, source, _rebound_owner(line, 'ROOT')
        yield module + ' as str', (
            'import subprocess\nfrom _repo import ROOT\n'
            f'import {module} as str\n'
            'subprocess.run(c, cwd=str(ROOT))\n'
        ), _rebound_owner(4, 'str(ROOT)')
        for alias in ('Path', 'str'):
            yield module + ' retains owner role as ' + alias, (
                f'import subprocess\nimport {module} as {alias}\n'
                f'subprocess.run(c, cwd={alias}.ROOT)\n'
            ), []
        yield module + ' owner replaced by constructor', (
            f'import subprocess\nimport {module} as Path\n'
            'from pathlib import Path\n'
            'subprocess.run(c, cwd=Path.ROOT)\n'
        ), _rebound_owner(4, 'Path.ROOT')


def _root_import_shadow_cases():
    for binding in ('import helpers as ROOT',
                    'from helpers import other as ROOT',
                    'from helpers import ROOT as ROOT',
                    'import _util as ROOT',
                    'from helpers import *', 'ROOT = replacement'):
        yield binding, ('import subprocess\nfrom _repo import ROOT\n'
                        + binding + '\nsubprocess.run(c, cwd=ROOT)\n'
                        ), _rebound_owner(4, 'ROOT')
    yield 'an aliased member is not ROOT provenance', (
        'import subprocess\nfrom helpers import other as ROOT\n'
        'subprocess.run(c, cwd=ROOT)\n'
    ), _rebound_owner(3, 'ROOT')
    yield 'an import shadows an accepted ROOT assignment', (
        'import subprocess\nfrom pathlib import Path\n'
        'ROOT = Path(__file__).resolve().parents[1]\n'
        'import helpers as ROOT\nsubprocess.run(c, cwd=ROOT)\n'
    ), _rebound_owner(5, 'ROOT')
    yield 'a literal ROOT from any module stays provable', (
        'import subprocess\nfrom helpers import ROOT\n'
        'subprocess.run(c, cwd=ROOT)\n'
    ), []


def _proof_import_scope_cases():
    for binding in ('from helpers import str', 'import helpers as str'):
        yield binding + ' stays in its function', (
            'import subprocess\nfrom _repo import ROOT\n'
            f'def bind():\n    {binding}\n'
            '    subprocess.run(c, cwd=str(ROOT))\n'
            '    def inner():\n        subprocess.run(c, cwd=str(ROOT))\n'
            'def sibling():\n    subprocess.run(c, cwd=str(ROOT))\n'
            'subprocess.run(c, cwd=str(ROOT))\n'
        ), _rebound_owner(5, 'str(ROOT)') + _rebound_owner(7, 'str(ROOT)')
        yield binding + ' at module reaches every scope', (
            'import subprocess\nfrom _repo import ROOT\n'
            f'{binding}\nsubprocess.run(c, cwd=str(ROOT))\n'
            'def outer():\n    subprocess.run(c, cwd=str(ROOT))\n'
            '    def inner():\n        subprocess.run(c, cwd=str(ROOT))\n'
            'def sibling():\n    subprocess.run(c, cwd=str(ROOT))\n'
        ), (_rebound_owner(4, 'str(ROOT)')
            + _rebound_owner(6, 'str(ROOT)')
            + _rebound_owner(8, 'str(ROOT)')
            + _rebound_owner(10, 'str(ROOT)'))
    yield 'a local Path import leaves the module derivation alone', (
        'import subprocess\nfrom pathlib import Path\n'
        'ROOT = Path(__file__).resolve().parents[1]\n'
        'def bind():\n    from helpers import Path\n'
        'subprocess.run(c, cwd=ROOT)\n'
    ), []
    yield 'a local str import leaves module chdir alone', (
        'import os\nimport subprocess\nfrom _repo import ROOT\n'
        'def bind():\n    from helpers import str\n'
        'os.chdir(str(ROOT))\nsubprocess.run(c)\n'
    ), []
    yield 'a local str import shadows local chdir', (
        'import os\nimport subprocess\nfrom _repo import ROOT\n'
        'def bind():\n    from helpers import str\n'
        '    os.chdir(str(ROOT))\n    subprocess.run(c)\n'
    ), ['tests/synthetic.py:7: subprocess.run os.chdir at line 6 '
        'may have moved the cwd declares no env=']
    yield 'a local ROOT import shadow leaves other scopes alone', (
        'import subprocess\nfrom _repo import ROOT\n'
        'def bind():\n    import helpers as ROOT\n'
        '    subprocess.run(c, cwd=ROOT)\n'
        'def sibling():\n    subprocess.run(c, cwd=ROOT)\n'
        'subprocess.run(c, cwd=ROOT)\n'
    ), _rebound_owner(5, 'ROOT')


def test_proof_imports_shadow_only_their_own_scope_chain(tmp):
    del tmp
    for name, source, expected in _proof_import_scope_cases():
        assert _synthetic_violations(source) == expected, name


def test_root_import_provenance_excludes_other_bindings(tmp):
    del tmp
    for name, source, expected in _root_import_shadow_cases():
        assert _synthetic_violations(source) == expected, name


def test_owner_and_proof_import_roles_are_distinct(tmp):
    del tmp
    for name, source, expected in _owner_role_cases():
        assert _synthetic_violations(source) == expected, name


def test_import_bindings_do_not_rebind_root_spellings(tmp):
    del tmp
    for name, source, expected in (
            *_root_provenance_cases(), *_owner_role_cases(),
            *_root_import_shadow_cases(), *_proof_import_scope_cases()):
        assert _synthetic_violations(source) == expected, name


def test_import_bindings_shadow_the_builtin_dict(tmp):
    del tmp
    for name, binding in _import_bindings():
        source = f'import os\n{binding}\n{_IMPORT_LAUNCH}\n'
        assert _synthetic_violations(source) == _unresolved_dict(3), name
    for binding in _near_miss_bindings():
        source = f"{binding}\nkw = dict(cwd='x')\n"
        assert _synthetic_violations(source) == [], binding
    for name, source, expected in _import_scope_cases():
        assert _synthetic_violations(source) == expected, name


def _annotation_headers():
    return (f'def go(a: {_ANNOTATED_LAUNCH}):',
            f'def go(*a: {_ANNOTATED_LAUNCH}):',
            f'def go(**a: {_ANNOTATED_LAUNCH}):',
            f'async def go(*a: {_ANNOTATED_LAUNCH}):',
            f'async def go(**a: {_ANNOTATED_LAUNCH}):')


def _outer_scope_headers():
    return ("def go(*a: dict(cwd='annotation'), dict=None):",
            "def go(*, dict=None, **a: dict(cwd='annotation')):")


def test_variadic_parameter_annotations_are_judged(tmp):
    del tmp
    moved = ['tests/synthetic.py:4: subprocess.run os.chdir at line 3 '
             'may have moved the cwd declares no env=']
    for header in _annotation_headers():
        source = ('import os\nimport subprocess\nos.chdir(tmp)\n'
                  + header + '\n    pass\n')
        assert _synthetic_violations(source) == moved, header
    for header in _outer_scope_headers():
        assert _synthetic_violations(f'{header}\n    pass\n') == [], header


def _planted_violations(tmp, snippet, offset):
    from _owned_writes import copy_test_tree

    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    # The write guard requires a literal path here, not a module constant.
    target = root / 'tests/test_diff_coverage.py'
    original = target.read_bytes()
    text = original.decode('utf-8').replace('\r\n', '\n')
    assert text.count(_DECLARATION) == 1, 'the declaration moved'
    line = text[:text.index(_DECLARATION)].count('\n') + offset
    try:
        target.write_bytes(text.replace(
            _DECLARATION, snippet + _DECLARATION, 1).encode('utf-8'))
        planted = _coverage_environment_violations(root)
    finally:
        target.write_bytes(original)
    return line, planted, _coverage_environment_violations(root)


def _assert_planted(line, planted, restored, expected):
    assert f'{_REAL_MODULE}:{line}: {expected}' in planted, planted
    assert not any(v.startswith(f'{_REAL_MODULE}:{line}:')
                   for v in restored), restored


def test_a_real_module_import_named_dict_is_judged(tmp):
    line, planted, restored = _planted_violations(
        tmp, f'from json import loads as dict\n{_IMPORT_LAUNCH}\n', 2)
    _assert_planted(line, planted, restored,
                    'unresolved callee dict cwd=tmp declares no env=')


def test_a_real_module_variadic_annotation_is_judged(tmp):
    line, planted, restored = _planted_violations(
        tmp, 'def _annotation_probe(*a: subprocess.run(\n'
        "        ['python3', 'child.py'], cwd=tmp)):\n    pass\n", 1)
    _assert_planted(line, planted, restored,
                    'subprocess.run cwd=tmp declares no env=')


def test_a_real_module_reserved_member_alias_is_judged(tmp):
    line, planted, restored = _planted_violations(
        tmp, f'from helpers import _util as dict\n{_IMPORT_LAUNCH}\n', 2)
    _assert_planted(line, planted, restored,
                    'unresolved callee dict cwd=tmp declares no env=')


def test_controls_never_write_inside_the_repository(tmp):
    from _control_writes import control_write_violations
    from _repo import ROOT

    del tmp
    violations = control_write_violations(Path(__file__), ROOT)
    assert not violations, '\n'.join(violations)


def test_real_target_star_import_removes_the_exemption(tmp):
    source = 'from review_launchers import *\n' + _IMPORT_LAUNCH + '\n'
    line, planted, restored = _planted_violations(tmp, source, 2)
    _assert_planted(line, planted, restored,
                    'unresolved callee dict cwd=tmp declares no env=')
    assert restored == [], restored


def test_real_target_global_import_removes_the_exemption(tmp):
    source = ('def bind():\n    global dict\n'
              '    from review_launchers import dict\nbind()\n'
              + _IMPORT_LAUNCH + '\n')
    line, planted, restored = _planted_violations(tmp, source, 5)
    _assert_planted(line, planted, restored,
                    'unresolved callee dict cwd=tmp declares no env=')
    assert restored == [], restored


def test_declared_root_binding_destinations(tmp):
    del tmp
    prefix = ('import os\nimport subprocess\nfrom _repo import ROOT\n'
              'def change():\n    global ROOT\n    ROOT = other\n')
    assert _synthetic_violations(
        prefix + 'subprocess.run(c, cwd=ROOT)\n') == _rebound_owner(7, 'ROOT')
    assert _synthetic_violations(
        prefix + 'os.chdir(ROOT)\nsubprocess.run(c)\n') == [
            'tests/synthetic.py:8: subprocess.run os.chdir at line 7 '
            'may have moved the cwd declares no env=']


def test_root_assignment_does_not_erase_other_binding_sites(tmp):
    del tmp
    source = ('import subprocess\nimport _util\nROOT = _util.ROOT\n'
              '(ROOT := other)\nsubprocess.run(c, cwd=ROOT)\n')
    assert _synthetic_violations(source) == _rebound_owner(5, 'ROOT')


def test_type_parameter_is_not_the_module_root(tmp):
    del tmp
    if sys.version_info < (3, 12):
        return
    source = ('import subprocess\nfrom _repo import ROOT\n'
              'def go[ROOT]():\n    subprocess.run(c, cwd=ROOT)\n')
    assert _synthetic_violations(source) == _rebound_owner(4, 'ROOT')


def test_local_root_parameter_keeps_the_module_derivation(tmp):
    del tmp
    source = ('import subprocess\nimport _util\nROOT = _util.ROOT\n'
              'def f(ROOT):\n    pass\nsubprocess.run(c, cwd=ROOT)\n')
    assert _synthetic_violations(source) == []


if __name__ == '__main__':
    import _util
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
