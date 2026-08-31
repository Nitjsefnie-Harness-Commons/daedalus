#!/usr/bin/env python3
"""A route the checkers cannot resolve is a violation, not a no-op.

Issue 303's five routes, each planted in a real module's copy, plus the
class each one belongs to. The coverage guard is tests/_coverage_guard.py
and the control-write checker is tests/_control_writes.py; this suite
is one the latter scans, so its own writes stay below tmp.
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

_DECLARATION_LINE = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"
_CONTAINER_PRELUDE = (
    "def _real_module_copy(tmp, relative):\n"
    "    root = Path(tmp) / 'repository'\n"
    "    return root, root / relative\n"
    "def test_control(tmp):\n"
    "    relative = Path('tests/probe.py')\n"
    "    first, *targets = _real_module_copy(tmp, relative)\n")


def _module_text(target):
    """A test module's source with checkout line endings normalised."""
    return target.read_bytes().decode('utf-8').replace('\r\n', '\n')


def _real_module_copy(tmp, relative):
    """Copy the real test tree under a root this control owns."""
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    return root, root / relative


def _planted_copy(tmp, relative, plant):
    """A real module's copy with `plant` appended: (root, target, line)."""
    root, target = _real_module_copy(tmp, relative)
    text = _module_text(target)
    target.write_bytes((text + plant).encode('utf-8'))
    return root, target, text.count('\n') + 1


def _declaration_line(text):
    """The line after test_diff_coverage.py's module-level declaration."""
    assert _DECLARATION_LINE in text, 'the declaration binding shape changed'
    return text[:text.index(_DECLARATION_LINE)].count('\n') + 2


def _with_planted_launch(target, planted):
    """Plant `planted` after the declaration; return the original bytes."""
    original = target.read_bytes()
    text = _module_text(target)
    target.write_bytes(text.replace(
        _DECLARATION_LINE, _DECLARATION_LINE + planted, 1).encode('utf-8'))
    return original


def test_controls_never_write_inside_the_repository(tmp):
    """This suite's own writes stay below the tmp roots it owns."""
    del tmp
    violations = control_write_violations(Path(__file__), ROOT)
    assert not violations, '\n'.join(violations)


def test_an_unmodelled_write_primitive_is_refused_in_a_real_control(tmp):
    """Issue 295's copy: shutil.copyfile over a checkout path is refused."""
    root, target = _real_module_copy(tmp, Path('tests/test_control_writes.py'))
    needle = "    del tmp\n    violations = _violations(Path(__file__))\n"
    text = _module_text(target)
    assert needle in text, 'the self-scan control shape changed'
    line = text[:text.index(needle)].count('\n') + 2
    mutated = text.replace(
        needle,
        "    del tmp\n"
        "    shutil.copyfile(__file__, ROOT / '.probe.py')\n"
        "    violations = _violations(Path(__file__))\n", 1)
    target.write_bytes(mutated.encode('utf-8'))
    violations = control_write_violations(target, root)
    assert (f'tests/test_control_writes.py:{line}: shutil.copyfile is not '
            'a modelled call') in violations, violations
    target.write_bytes(text.encode('utf-8'))
    assert control_write_violations(target, root) == []


def test_a_proved_helper_rebound_after_its_definition_is_not_proof(tmp):
    """Route 5: `helper = lambda ...` after `def helper` unresolves it."""
    relative = Path('tests/test_coverage_environment.py')
    root, target = _real_module_copy(tmp, relative)
    needle = "    return root, root / relative\n"
    text = _module_text(target)
    assert needle in text, 'the copy helper shape changed'
    mutated = text.replace(
        needle,
        needle + "\n\n_real_module_copy = lambda _tmp, _relative: "
        "(ROOT, ROOT / 'README.md')\n", 1)
    call = mutated[:mutated.index(
        '= _real_module_copy(tmp, relative)')].count('\n') + 1
    target.write_bytes(mutated.encode('utf-8'))
    violations = control_write_violations(target, root)
    assert (f'tests/test_coverage_environment.py:{call}: _real_module_copy '
            'callable is unresolved') in violations, violations
    assert any(v.endswith('write_bytes target path is not control-owned')
               for v in violations), violations
    target.write_bytes(text.encode('utf-8'))
    assert control_write_violations(target, root) == []


def test_a_helper_write_is_proved_from_what_its_callers_hand_it(tmp):
    """A helper's tmp is owned only while every caller passes an owned one."""
    source = Path(tmp) / 'seeded.py'
    helper = (
        "from _owned_writes import copy_test_tree\n"
        "def _copy(tmp):\n"
        "    root = Path(tmp) / 'repository'\n"
        "    copy_test_tree(root)\n"
        "    return root\n")
    source.write_text(helper + "def test_a(tmp):\n    _copy(tmp)\n",
                      encoding='utf-8')
    assert control_write_violations(source, Path(tmp)) == []
    source.write_text(
        helper + "def test_a(tmp):\n    _copy(tmp)\n"
        "def test_b(tmp):\n    _copy(ROOT)\n", encoding='utf-8')
    assert control_write_violations(source, Path(tmp)) == [
        'seeded.py:4: copy_test_tree target path is not control-owned']
    source.write_text(helper, encoding='utf-8')
    assert control_write_violations(source, Path(tmp)) == [
        'seeded.py:4: copy_test_tree target path is not control-owned']


def test_a_path_replace_is_not_the_pure_string_replace(tmp):
    """Path.replace moves a file; only the two-argument str form is pure."""
    relative = Path('tests/test_coverage_environment.py')
    root, target = _real_module_copy(tmp, relative)
    needle = "    copy_test_tree(root)\n"
    text = _module_text(target)
    assert needle in text, 'the copy helper shape changed'
    line = text[:text.index(needle)].count('\n') + 2
    plant = ("    (root / 'tests' / 'test_control_writes.py')"
             ".replace(ROOT / '.probe.py')\n")
    target.write_bytes(
        text.replace(needle, needle + plant, 1).encode('utf-8'))
    violations = control_write_violations(target, root)
    assert (f'tests/test_coverage_environment.py:{line}: '
            "(root / 'tests' / 'test_control_writes.py').replace is not a "
            'modelled call') in violations, violations
    target.write_bytes(text.encode('utf-8'))
    assert control_write_violations(target, root) == []


def test_a_lambda_default_is_judged_in_its_enclosing_scope(tmp):
    """A write hidden in a lambda's default expression is still judged."""
    root, target, line = _planted_copy(
        tmp, Path('tests/test_unresolved_routes.py'),
        "_UNJUDGED = lambda _x=(ROOT / '.probe2.py').write_text(\n"
        "    'planted', encoding='utf-8'): _x\n")
    assert control_write_violations(target, root) == [
        f'tests/test_unresolved_routes.py:{line}: write_text target path '
        'is not control-owned']


def test_a_lambda_default_call_site_seeds_the_helper_it_calls(tmp):
    """A helper's caller inside a lambda default counts like any other."""
    root, target, line = _planted_copy(
        tmp, Path('tests/test_coverage_environment.py'),
        "def _planted_helper(target):\n"
        "    (Path(target) / '.probe4.py').write_text('planted')\n"
        "_BYPASS = lambda _x=_planted_helper(ROOT): _x\n"
        "def test_planted_owned_site(tmp):\n"
        "    _planted_helper(tmp)\n")
    assert control_write_violations(target, root) == [
        f'tests/test_coverage_environment.py:{line + 1}: write_text target '
        'path is not control-owned']


def test_a_control_called_in_its_module_is_seeded_like_a_helper(tmp):
    """A test_ name owns tmp from the runner only while nothing calls it."""
    root, target, line = _planted_copy(
        tmp, Path('tests/test_coverage_environment.py'),
        "def test_planted_helper(tmp):\n"
        "    (Path(tmp) / '.probe3.py').write_text('planted')\n"
        "def test_planted_caller(tmp):\n"
        "    del tmp\n"
        "    test_planted_helper(ROOT)\n")
    assert control_write_violations(target, root) == [
        f'tests/test_coverage_environment.py:{line + 1}: write_text target '
        'path is not control-owned']


def test_an_import_bound_twice_resolves_to_neither_binding(tmp):
    """copy_test_tree defined beside its import is no longer the writer."""
    root, target, _ = _planted_copy(
        tmp, Path('tests/test_coverage_environment.py'),
        "def copy_test_tree(root):\n    return root\n")
    text = _module_text(target)
    needle = "    copy_test_tree(root)\n"
    assert needle in text, 'the copy helper shape changed'
    call = text[:text.index(needle)].count('\n') + 1
    assert control_write_violations(target, root) == [
        f'tests/test_coverage_environment.py:{call}: copy_test_tree '
        'callable is unresolved']


def test_a_starred_argument_does_not_make_path_replace_pure(tmp):
    """`*[]` adds nothing at runtime and nothing to the positional count."""
    relative = Path('tests/test_coverage_environment.py')
    root, target = _real_module_copy(tmp, relative)
    needle = "    copy_test_tree(root)\n"
    text = _module_text(target)
    assert needle in text, 'the copy helper shape changed'
    line = text[:text.index(needle)].count('\n') + 2
    plant = ("    (root / 'tests' / 'test_control_writes.py')"
             ".replace(*[], ROOT / '.probe5.py')\n")
    target.write_bytes(
        text.replace(needle, needle + plant, 1).encode('utf-8'))
    violations = control_write_violations(target, root)
    assert (f'tests/test_coverage_environment.py:{line}: '
            "(root / 'tests' / 'test_control_writes.py').replace is not a "
            'modelled call') in violations, violations
    target.write_bytes(text.encode('utf-8'))
    assert control_write_violations(target, root) == []


def test_a_container_reached_through_an_alias_is_not_proof(tmp):
    """Route 4: mutating an alias retires the name that shares the list."""
    relative = Path('tests/test_coverage_environment.py')
    root, target = _real_module_copy(tmp, relative)
    needle = "    root, target = _real_module_copy(tmp, relative)\n"
    text = _module_text(target)
    assert needle in text, 'the copy call shape changed'
    first = text[:text.index(needle)].count('\n') + 1
    mutated = text.replace(
        needle,
        "    first, *targets = _real_module_copy(tmp, relative)\n"
        "    alias = targets\n"
        "    alias.append(ROOT / 'README.md')\n"
        "    targets[-1].write_bytes(b'mutated')\n" + needle, 1)
    target.write_bytes(mutated.encode('utf-8'))
    violations = control_write_violations(target, root)
    assert violations == [
        f'tests/test_coverage_environment.py:{first + 3}: write_bytes '
        'target path is not control-owned'], violations
    target.write_bytes(text.encode('utf-8'))
    assert control_write_violations(target, root) == []


def test_a_subscript_read_alone_keeps_a_container_proved(tmp):
    """Only handing the name on retires it; reading through it does not."""
    source = Path(tmp) / 'escape.py'
    helper = _CONTAINER_PRELUDE
    source.write_text(
        helper + "    targets[0].write_bytes(b'ok')\n", encoding='utf-8')
    assert control_write_violations(source, Path(tmp)) == []
    for escape in ('held = [targets]\n', 'len(targets)\n',
                   'alias = targets\n'):
        source.write_text(
            helper + '    ' + escape
            + "    targets[0].write_bytes(b'mutated')\n", encoding='utf-8')
        assert control_write_violations(source, Path(tmp)) == [
            'escape.py:8: write_bytes target path is not control-owned'
        ], escape


def _container_route_violations(tmp, route):
    """The checker's answer to `route` planted after the container binds."""
    source = Path(tmp) / 'nested.py'
    source.write_text(_CONTAINER_PRELUDE + route, encoding='utf-8')
    return control_write_violations(source, Path(tmp))


def test_a_container_handed_to_a_nested_scope_header_is_not_proof(tmp):
    """A default is a hand-off even when the whole default is the name."""
    for header in ('held=targets', '*, held=targets', 'held: int = targets'):
        route = (f"    def inner({header}):\n"
                 "        held.append(ROOT / 'x')\n"
                 "    inner()\n")
        line = _CONTAINER_PRELUDE.count('\n') + route.count('\n') + 1
        assert _container_route_violations(
            tmp, route + "    targets[-1].write_bytes(b'mutated')\n") == [
                f'nested.py:{line}: write_bytes target path is not '
                'control-owned'], header


def test_a_container_named_in_a_nested_scope_body_is_not_proof(tmp):
    """A nested scope that names the container has been handed it."""
    routes = (
        "    def inner():\n"
        "        targets.append(ROOT / 'x')\n"
        "    inner()\n",
        "    def inner():\n"
        "        nonlocal targets\n"
        "        targets = [ROOT / 'x']\n"
        "    inner()\n",
        "    class _Body:\n"
        "        targets.append(ROOT / 'x')\n")
    for route in routes:
        line = _CONTAINER_PRELUDE.count('\n') + route.count('\n') + 1
        assert _container_route_violations(
            tmp, route + "    targets[-1].write_bytes(b'mutated')\n") == [
                f'nested.py:{line}: write_bytes target path is not '
                'control-owned'], route
    assert _container_route_violations(
        tmp,
        "    def inner(held=None):\n"
        "        return held\n"
        "    inner()\n"
        "    targets[0].write_bytes(b'ok')\n") == []


def test_an_alias_of_a_container_is_never_proof(tmp):
    """The alias shares the object, so a mutation through either retires it."""
    assert _container_route_violations(
        tmp,
        "    alias = targets\n"
        "    targets.append(ROOT / 'x')\n"
        "    alias[-1].write_bytes(b'mutated')\n") == [
            'nested.py:9: write_bytes target path is not control-owned']


def test_a_global_declaration_hands_the_container_to_the_module(tmp):
    """A module-level helper reaches a control's list only through global."""
    route = ("    global _shared\n"
             "    first, *_shared = _real_module_copy(tmp, relative)\n"
             "    _poison_global()\n"
             "    _shared[-1].write_bytes(b'mutated')\n")
    source = Path(tmp) / 'global.py'
    for declaration in ('', '    global _shared\n'):
        text = ("_shared = []\n"
                "def _poison_global():\n"
                + declaration
                + "    _shared.append(ROOT / 'x')\n"
                + _CONTAINER_PRELUDE + route)
        source.write_text(text, encoding='utf-8')
        line = text.count('\n')
        assert control_write_violations(source, Path(tmp)) == [
            f'global.py:{line}: write_bytes target path is not control-owned'
        ], declaration


def test_a_class_body_binding_does_not_shield_its_methods(tmp):
    """A class scope is invisible to the functions defined inside it."""
    route = ("    class _Base:\n"
             "        targets = 1\n"
             "        def __init_subclass__(cls):\n"
             "            targets.append(ROOT / 'x')\n"
             "    class _Sub(_Base):\n"
             "        pass\n")
    line = _CONTAINER_PRELUDE.count('\n') + route.count('\n') + 1
    assert _container_route_violations(
        tmp, route + "    targets[-1].write_bytes(b'mutated')\n") == [
            f'nested.py:{line}: write_bytes target path is not control-owned']
    assert _container_route_violations(
        tmp,
        "    def inner():\n"
        "        targets = []\n"
        "        def inner2():\n"
        "            targets.append(ROOT / 'x')\n"
        "        inner2()\n"
        "    inner()\n"
        "    targets[0].write_bytes(b'ok')\n") == []


def test_a_class_body_binding_does_not_shield_its_comprehensions(tmp):
    """A comprehension is a function scope, so it sees past the class."""
    for spelling in ("[targets.append(ROOT / 'x') for _ in [0]]",
                     "list(targets.append(ROOT / 'x') for _ in [0])"):
        route = ("    class _Base:\n"
                 "        targets = 1\n"
                 f"        _ = {spelling}\n")
        line = _CONTAINER_PRELUDE.count('\n') + route.count('\n') + 1
        assert _container_route_violations(
            tmp, route + "    targets[-1].write_bytes(b'mutated')\n") == [
                f'nested.py:{line}: write_bytes target path is not '
                'control-owned'], spelling
    assert _container_route_violations(
        tmp,
        "    def inner():\n"
        "        targets = []\n"
        "        _ = [targets.append(ROOT / 'x') for _ in [0]]\n"
        "    inner()\n"
        "    targets[0].write_bytes(b'ok')\n") == []


def test_an_annotated_alias_of_the_launch_module_is_a_launch(tmp):
    """Route 1: `launcher: object = subprocess` aliases the module."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    first = _declaration_line(_module_text(target))
    original = _with_planted_launch(
        target,
        "launcher: object = subprocess\n"
        "launcher.run(['python3', 'child.py'], cwd=tmp)\n")
    try:
        violations = _coverage_environment_violations(root)
        assert (f'tests/test_diff_coverage.py:{first + 1}: subprocess.run '
                'cwd=tmp declares no env=') in violations, violations
    finally:
        target.write_bytes(original)
    restored = _coverage_environment_violations(root)
    assert not any(v.startswith(f'tests/test_diff_coverage.py:{line}:')
                   for line in (first, first + 1)
                   for v in restored), restored


def test_an_unresolved_callee_carrying_cwd_needs_a_declaration(tmp):
    """The class behind route 1: any callee with cwd= is judged."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    first = _declaration_line(_module_text(target))
    launches = (
        ("sys.modules['subprocess'].run(['python3', 'child.py'], "
         "cwd=tmp)\n", "sys.modules['subprocess'].run"),
        ("getattr(subprocess, 'run')(['python3', 'child.py'], "
         "cwd=tmp)\n", "getattr(subprocess, 'run')"),
    )
    for planted, callee in launches:
        original = _with_planted_launch(target, planted)
        try:
            violations = _coverage_environment_violations(root)
            assert (f'tests/test_diff_coverage.py:{first}: unresolved '
                    f'callee {callee} cwd=tmp declares no env='
                    ) in violations, (callee, violations)
        finally:
            target.write_bytes(original)
    original = _with_planted_launch(
        target,
        "sys.modules['subprocess'].run(['python3', 'child.py'], cwd=tmp,\n"
        "                              env=_COVERAGE_ENV)\n")
    try:
        declared = _coverage_environment_violations(root)
        assert not any(v.startswith(f'tests/test_diff_coverage.py:{first}:')
                       for v in declared), declared
    finally:
        target.write_bytes(original)


def test_a_readable_spread_carrying_cwd_is_judged_like_the_keyword(tmp):
    """`**{'cwd': tmp}` spells a working directory the guard can read."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    first = _declaration_line(_module_text(target))
    for spread in ("{'cwd': tmp}", 'dict(cwd=tmp)'):
        original = _with_planted_launch(
            target,
            "sys.modules['subprocess'].run(['python3', 'child.py'],\n"
            f"                              **{spread})\n")
        try:
            violations = _coverage_environment_violations(root)
            assert (f'tests/test_diff_coverage.py:{first}: unresolved '
                    "callee sys.modules['subprocess'].run cwd may arrive "
                    'through a ** spread declares no env='
                    ) in violations, (spread, violations)
        finally:
            target.write_bytes(original)


def test_a_launcher_bound_through_an_unfollowable_form_is_refused(tmp):
    """A binding the alias walk cannot read is refused at the binding."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    first = _declaration_line(_module_text(target))
    original = _with_planted_launch(
        target, 'for launcher in (subprocess,):\n    pass\n')
    try:
        violations = _coverage_environment_violations(root)
        assert (f'tests/test_diff_coverage.py:{first}: a launcher is bound '
                'through a form the guard cannot follow') in violations, (
            violations)
    finally:
        target.write_bytes(original)
    for binding in ('for launcher in (subprocess,):\n    pass',
                    'launcher, other = subprocess, None',
                    'with hold(subprocess) as launcher:\n    pass',
                    'held = (launcher := subprocess)'):
        violations = _synthetic_violations(
            f"""import subprocess
{binding}
""")
        assert any(v == 'tests/synthetic.py:2: a launcher is bound through '
                   'a form the guard cannot follow' for v in violations), (
            binding, violations)
    assert _synthetic_violations(
        """import subprocess
launcher = subprocess
launcher.run(['python3', 'child.py'], cwd=ROOT)
""") == []


def _assert_root_owner_retired(tmp, plants):
    """Each plant, put before a cwd=_util.ROOT launch, must refuse it."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    first = _declaration_line(_module_text(target))
    launch = "subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)\n"
    for planted, offset in plants:
        original = _with_planted_launch(target, planted + launch)
        try:
            violations = _coverage_environment_violations(root)
            assert (f'tests/test_diff_coverage.py:{first + offset}: '
                    'subprocess.run cwd=_util.ROOT declares no env='
                    ) in violations, (planted, violations)
        finally:
            target.write_bytes(original)
    restored = _coverage_environment_violations(root)
    assert not any(v.startswith(f'tests/test_diff_coverage.py:{line}:')
                   for line in (first, first + 1, first + 2)
                   for v in restored), restored


def test_root_replaced_through_the_owner_namespace_is_not_root(tmp):
    """Route 2 and its class: any route into the owner retires it."""
    _assert_root_owner_retired(tmp, (
        ("_util.__dict__['ROOT'] = Path(tmp)\n", 1),
        ("vars(_util)['ROOT'] = Path(tmp)\n", 1),
        ("_util.__dict__.update({'ROOT': Path(tmp)})\n", 1),
        ("_HELPER = _util\n_HELPER.ROOT = Path(tmp)\n", 2)))


def test_a_second_import_of_the_owner_is_the_same_module(tmp):
    """`import _util as _u` binds the object `_util` already names."""
    _assert_root_owner_retired(tmp, (
        ("import _util as _u\n_u.ROOT = Path(tmp)\n", 2),
        ("import _util as _u\nvars(_u)['ROOT'] = Path(tmp)\n", 2)))


def test_a_store_through_any_binding_form_retires_the_owner(tmp):
    """for, with-as and comprehension targets store like assignment."""
    _assert_root_owner_retired(tmp, (
        ("for _util.ROOT in (Path(tmp),):\n    pass\n", 2),
        ("with open(__file__) as _util.ROOT:\n    pass\n", 2),
        ("_ = [0 for _util.ROOT in (Path(tmp),)]\n", 1)))


def test_a_reflective_dunder_read_reaches_the_owner_namespace(tmp):
    """getattr with a dunder literal is `_util.__dict__` spelled again."""
    _assert_root_owner_retired(tmp, (
        ("getattr(_util, '__dict__')['ROOT'] = Path(tmp)\n", 1),
        ("getattr(_util, '__dict__').update({'ROOT': Path(tmp)})\n", 1)))


def test_a_subscript_read_on_the_owner_chain_retires_it(tmp):
    """`_util.x[...]` reaches an object the checker cannot see."""
    del tmp
    violations = _synthetic_violations(
        """import subprocess
import _util
_first = _util.free_port[0]
subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""")
    assert violations == [
        'tests/synthetic.py:4: subprocess.run cwd=_util.ROOT declares no '
        'env='], violations


def test_a_reflective_read_of_the_owner_keeps_it_an_owner(tmp):
    """hasattr/getattr with a literal name read; they do not rebind."""
    del tmp
    assert _synthetic_violations(
        """import subprocess
import _util
assert hasattr(_util, 'ROOT')
value = getattr(_util, 'ROOT')
subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""") == []
    violations = _synthetic_violations(
        """import subprocess
import _util
setattr(_util, 'other', 1)
subprocess.run(['python3', 'child.py'], cwd=_util.ROOT)
""")
    assert violations == [
        'tests/synthetic.py:4: subprocess.run cwd=_util.ROOT declares no '
        'env='], violations


def test_an_alias_of_the_os_module_still_moves_the_cwd(tmp):
    """Route 3: `import os as filesystem; filesystem.chdir(tmp)` taints."""
    relative = Path('tests/test_diff_coverage.py')
    root, target = _real_module_copy(tmp, relative)
    first = _declaration_line(_module_text(target))
    original = _with_planted_launch(
        target,
        "import os as filesystem\n"
        "filesystem.chdir(tmp)\n"
        "subprocess.run(['python3', 'child.py'])\n")
    try:
        violations = _coverage_environment_violations(root)
        assert (f'tests/test_diff_coverage.py:{first + 2}: subprocess.run '
                f'os.chdir at line {first + 1} may have moved the cwd '
                'declares no env=') in violations, violations
    finally:
        target.write_bytes(original)
    restored = _coverage_environment_violations(root)
    assert not any(v.startswith(f'tests/test_diff_coverage.py:{line}:')
                   for line in (first, first + 1, first + 2)
                   for v in restored), restored


def test_every_spelling_of_a_cwd_change_taints_an_inherited_cwd(tmp):
    """contextlib.chdir, fchdir, an aliased import: all move the cwd."""
    del tmp
    for mover in ('import contextlib\nwith contextlib.chdir(tmp):\n    pass',
                  'import os\nos.fchdir(handle)',
                  'from os import chdir as move\nmover = move\nmover(tmp)'):
        violations = _synthetic_violations(
            f"""import subprocess
{mover}
subprocess.run(['python3', 'child.py'])
""")
        assert any('may have moved the cwd' in v for v in violations), (
            mover, violations)
    assert _synthetic_violations(
        """import os as filesystem
import subprocess
filesystem.chdir(ROOT)
subprocess.run(['python3', 'child.py'])
""") == []


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
