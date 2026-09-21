#!/usr/bin/env python3
"""Shared scope controls for coverage and Bash launch guards."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _control_writes import control_write_violations  # noqa: E402
from _coverage_guard import (  # noqa: E402
    _coverage_environment_violations, _synthetic_violations)
from _owned_writes import copy_test_tree  # noqa: E402
from _repo import ROOT  # noqa: E402


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
     (("    names = _import_rebound_names(tree) & _PROOF_NAMES\n",
       "    names = set()\n"),), _ROOT_PROVENANCE_INVOKE),
    ('Path and str are proof names', 'scopes',
     (("_PROOF_NAMES = frozenset({'Path', 'str', _ALL_NAMES})\n",
       "_PROOF_NAMES = frozenset({_ALL_NAMES})\n"),), _ROOT_PROVENANCE_INVOKE),
    ('a canonical import is exact', 'scopes',
     (("    return (node.module, alias.name) == "
       "_CANONICAL_MEMBERS.get(bound)\n",
       "    return bound in _CANONICAL_MEMBERS\n"),), _ROOT_PROVENANCE_INVOKE),
    ('scope shadows carry the unprovable names', 'scopes',
     (("    shadows[tree].update(_unprovable_names(tree))\n", ""),),
     _ROOT_PROVENANCE_INVOKE),
    ('module shadows carry the unprovable names', 'scopes',
     (("    names.update(_unprovable_names(tree))\n", ""),),
     _ROOT_PROVENANCE_INVOKE),
    ('the _util.ROOT arm needs an owner', 'scopes',
     (("        return '_util' in owners\n", "        return True\n"),),
     _ROOT_PROVENANCE_INVOKE),
    ('an unbound ROOT is unprovable', 'scopes',
     (("    if not _root_assignments(tree) and not any(\n"
       "            isinstance(node, ast.ImportFrom) "
       "and 'ROOT' in _bound_names(node)\n"
       "            for node in memo_nodes(tree)):\n"
       "        names.add('ROOT')\n", ""),), _ROOT_PROVENANCE_INVOKE),
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
        ('an import binding str leaves str(ROOT) unprovable',
         """import subprocess
from helpers import str
subprocess.run(['python3', 'child.py'], cwd=str(ROOT))
""", _rebound_owner(3, 'str(ROOT)')),
        ('a function-local import binding str is a rebinding',
         """import subprocess
def go():
    from helpers import str
    subprocess.run(['python3', 'child.py'], cwd=str(ROOT))
""", _rebound_owner(4, 'str(ROOT)')),
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


def test_import_bindings_do_not_rebind_root_spellings(tmp):
    del tmp
    for name, source, expected in _root_provenance_cases():
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


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
