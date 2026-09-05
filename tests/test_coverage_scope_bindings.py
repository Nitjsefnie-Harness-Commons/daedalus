#!/usr/bin/env python3
"""Bindings and annotation positions the guard's scope model must see.

The model lives in tests/_coverage_scopes.py, which tests/_coverage_guard.py
and tests/_bash_resolver_scan.py both read; a name an import binds and an
annotation on a variadic parameter are the two positions it stopped visiting.
"""
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


def _unresolved_dict(line):
    return [f'tests/synthetic.py:{line}: unresolved callee dict '
            'cwd=tmp declares no env=']


def _rebound_owner(line):
    return [f'tests/synthetic.py:{line}: subprocess.run '
            'cwd=behaviour.ROOT declares no env=']


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
        ('a later alias leaves an owner an owner', """import subprocess
import _util
import helpers as _util
subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""", []),
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
    """Judge the real module with `snippet` above its declaration."""
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    # Spelled out rather than _REAL_MODULE: a write target is proved
    # from the text beside it, and a module constant proves nothing.
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
