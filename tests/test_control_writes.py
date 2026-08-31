#!/usr/bin/env python3
"""Writer-shape controls for the coverage guard's mutation tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _control_writes import control_write_violations  # noqa: E402
from _repo import ROOT  # noqa: E402


def _violations(source):
    return control_write_violations(source, ROOT)


def test_controls_never_write_inside_the_repository(tmp):
    """This suite's synthetic sources also stay below their tmp roots."""
    del tmp
    violations = _violations(Path(__file__))
    assert not violations, '\n'.join(violations)


def test_refuses_a_tuple_bound_checkout_path(tmp):
    """Tuple binding cannot hide a checkout path from the control."""
    source = Path(tmp) / 'tuple-target.py'
    source.write_text(
        "def control(relative):\n"
        "    root, target = ROOT, ROOT / relative\n"
        "    target.write_bytes(b'mutated')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'tuple-target.py:3: write_bytes target path is not control-owned'
    ]


def test_refuses_an_augmented_checkout_binding(tmp):
    """An augmented assignment can replace an owned path with ROOT."""
    source = Path(tmp) / 'augmented-target.py'
    source.write_text(
        "def test_control(tmp):\n"
        "    target = Path(tmp)\n"
        "    target /= ROOT / 'unsafe'\n"
        "    target.write_bytes(b'mutated')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'augmented-target.py:4: write_bytes target path is not '
        + 'control-owned'
    ]


def test_refuses_a_foreign_absolute_literal(tmp):
    """A Windows drive path cannot become relative on a POSIX analyzer."""
    source = Path(tmp) / 'foreign-absolute.py'
    source.write_text(
        "def test_control(tmp):\n"
        "    target = Path(tmp) / 'C:\\\\outside'\n"
        "    target.write_bytes(b'mutated')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'foreign-absolute.py:3: write_bytes target path is not control-owned'
    ]


def test_refuses_a_shadowed_copy_helper(tmp):
    """A local helper with the trusted spelling proves no ownership."""
    source = Path(tmp) / 'shadowed-helper.py'
    source.write_text(
        "def test_control(tmp, relative):\n"
        "    def _real_module_copy(_tmp, _relative):\n"
        "        return ROOT, ROOT / _relative\n"
        "    _, target = _real_module_copy(tmp, relative)\n"
        "    target.write_bytes(b'mutated')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'shadowed-helper.py:5: write_bytes target path is not control-owned'
    ]


def test_refuses_a_redirected_owned_container(tmp):
    """Replacing an element of an owned container ends its ownership."""
    source = Path(tmp) / 'container-target.py'
    source.write_text(
        "def _real_module_copy(tmp, relative):\n"
        "    root = Path(tmp) / 'repository'\n"
        "    return root, root / relative\n"
        "def test_control(tmp):\n"
        "    relative = Path('tests/probe.py')\n"
        "    first, *targets = _real_module_copy(tmp, relative)\n"
        "    targets[0] = ROOT / 'unsafe.py'\n"
        "    targets[0].write_bytes(b'mutated')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'container-target.py:8: write_bytes target path is not '
        + 'control-owned'
    ]


def test_refuses_an_appended_checkout_path(tmp):
    """A method that changes what a container holds ends the proof."""
    source = Path(tmp) / 'container-method.py'
    source.write_text(
        "def _real_module_copy(tmp, relative):\n"
        "    root = Path(tmp) / 'repository'\n"
        "    return root, root / relative\n"
        "def test_control(tmp):\n"
        "    relative = Path('tests/probe.py')\n"
        "    first, *targets = _real_module_copy(tmp, relative)\n"
        "    targets.append(ROOT / relative)\n"
        "    targets[-1].write_bytes(b'mutated')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'container-method.py:8: write_bytes target path is not '
        + 'control-owned'
    ]


def test_recognises_a_keyword_open(tmp):
    """The builtin's target and mode arrive by keyword just as well."""
    source = Path(tmp) / 'keyword-open.py'
    source.write_text(
        "def test_control(tmp):\n"
        "    open(file=ROOT / 'unsafe.py', mode='ab')\n"
        "    open(file=Path(tmp) / 'safe.py', mode='ab')\n"
        "    open(mode='ab')\n",
        encoding='utf-8')
    assert _violations(source) == [
        "keyword-open.py:2: open mode 'ab' target path is not "
        + 'control-owned',
        "keyword-open.py:4: open mode 'ab' target is unresolved",
    ]


def test_refuses_a_comprehension_rebound_owned_name(tmp):
    """A comprehension target shadowing tmp does not inherit ownership."""
    source = Path(tmp) / 'comprehension-target.py'
    source.write_text(
        "def test_control(tmp):\n"
        "    [tmp.write_text('written', encoding='utf-8')\n"
        "     for tmp in [ROOT / 'probe']]\n",
        encoding='utf-8')
    assert _violations(source) == [
        'comprehension-target.py:2: write_text target path is not '
        + 'control-owned'
    ]


def test_refuses_a_shadowed_path_constructor(tmp):
    """A local Path spelling proves no relationship to tmp."""
    source = Path(tmp) / 'shadowed-path.py'
    source.write_text(
        "def test_control(tmp):\n"
        "    def Path(_value):\n"
        "        return ROOT / 'unsafe'\n"
        "    target = Path(tmp)\n"
        "    target.write_bytes(b'mutated')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'shadowed-path.py:5: write_bytes target path is not control-owned'
    ]


def test_recognises_path_open(tmp):
    """Path.open in a write mode is checked like the builtin open."""
    source = Path(tmp) / 'path-open.py'
    source.write_text(
        "def test_control():\n"
        "    target = ROOT / 'unsafe'\n"
        "    target.open('wb')\n",
        encoding='utf-8')
    assert _violations(source) == [
        "path-open.py:3: Path.open mode 'wb' target path is not "
        + 'control-owned'
    ]


def test_refuses_an_unresolved_open_mode(tmp):
    """A local mode may select writing and therefore fails closed."""
    source = Path(tmp) / 'unresolved-mode.py'
    source.write_text(
        "def test_control(tmp):\n"
        "    target = Path(tmp) / 'owned'\n"
        "    mode = 'wb'\n"
        "    open(target, mode)\n",
        encoding='utf-8')
    assert _violations(source) == [
        'unresolved-mode.py:4: open mode is unresolved'
    ]


def test_refuses_a_shadowed_open_callable(tmp):
    """A local open spelling need not write to the target it receives."""
    source = Path(tmp) / 'shadowed-open.py'
    source.write_text(
        "def test_control(tmp):\n"
        "    def open(_target, _mode):\n"
        "        return mutate_checkout()\n"
        "    target = Path(tmp) / 'owned'\n"
        "    open(target, 'wb')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'shadowed-open.py:3: mutate_checkout callable is unresolved',
        'shadowed-open.py:5: open callable is unresolved',
    ]


def test_resolves_only_control_owned_paths(tmp):
    """Owned paths pass; unresolved and checkout paths fail closed."""
    source = Path(tmp) / 'writer-shapes.py'
    source.write_text(
        "def _real_module_copy(tmp, relative):\n"
        "    root = Path(tmp) / 'repository'\n"
        "    return root, root / relative\n"
        "def test_controls(tmp, manager, holder):\n"
        "    relative = Path('tests/probe.py')\n"
        "    root, target = _real_module_copy(tmp, relative)\n"
        "    target.write_bytes(b'ok')\n"
        "    first, *rest = _real_module_copy(tmp, relative)\n"
        "    rest[0].write_text('ok')\n"
        "    joined = os.path.join(tmp, 'safe')\n"
        "    open(joined, 'w')\n"
        "    open(joined, 'a')\n"
        "    open(joined, 'x')\n"
        "    open(joined, 'wb')\n"
        "    open(joined, 'ab')\n"
        "    open(joined, 'xb')\n"
        "    checkout = os.path.join(ROOT, 'unsafe')\n"
        "    open(checkout, 'w')\n"
        "    open(checkout, 'a')\n"
        "    open(checkout, 'x')\n"
        "    open(checkout, 'wb')\n"
        "    open(checkout, 'ab')\n"
        "    open(checkout, 'xb')\n"
        "    with manager() as named:\n"
        "        named.write_text('unsafe')\n"
        "    holder.path.write_bytes(b'unsafe')\n"
        "ROOT.write_text('unsafe')\n"
        "async def async_control(holder):\n"
        "    holder.path.write_text('unsafe')\n"
        "def helper(tmp):\n"
        "    Path(tmp).write_text('unsafe')\n",
        encoding='utf-8')
    assert _violations(source) == [
        "writer-shapes.py:18: open mode 'w' target path is not "
        + 'control-owned',
        "writer-shapes.py:19: open mode 'a' target path is not "
        + 'control-owned',
        "writer-shapes.py:20: open mode 'x' target path is not "
        + 'control-owned',
        "writer-shapes.py:21: open mode 'wb' target path is not "
        + 'control-owned',
        "writer-shapes.py:22: open mode 'ab' target path is not "
        + 'control-owned',
        "writer-shapes.py:23: open mode 'xb' target path is not "
        + 'control-owned',
        'writer-shapes.py:24: manager callable is unresolved',
        'writer-shapes.py:25: write_text target path is not control-owned',
        'writer-shapes.py:26: write_bytes target path is not control-owned',
        'writer-shapes.py:27: write_text target path is not control-owned',
        'writer-shapes.py:29: write_text target path is not control-owned',
        'writer-shapes.py:31: write_text target path is not control-owned',
    ]


def test_a_module_level_call_site_seeds_the_helper(tmp):
    """A helper called from module scope is met like one called from a test."""
    source = Path(tmp) / 'module-caller.py'
    source.write_text(
        "from _owned_writes import copy_test_tree\n"
        "def _copy(tmp):\n"
        "    root = Path(tmp) / 'repository'\n"
        "    copy_test_tree(root)\n"
        "    return root\n"
        "def test_control(tmp):\n"
        "    _copy(tmp)\n"
        "_copy(ROOT)\n",
        encoding='utf-8')
    assert _violations(source) == [
        'module-caller.py:4: copy_test_tree target path is not control-owned'
    ]


def test_a_computed_namespace_key_retires_every_name(tmp):
    """A namespace written through a computed key proves nothing after."""
    source = Path(tmp) / 'namespace-key.py'
    source.write_text(
        "def _copy(tmp):\n"
        "    return Path(tmp)\n"
        "def test_control(tmp):\n"
        "    globals()[tmp] = 1\n"
        "    _copy(tmp).write_text('planted')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'namespace-key.py:2: Path callable is unresolved',
        'namespace-key.py:4: globals callable is unresolved',
        'namespace-key.py:5: _copy callable is unresolved',
        'namespace-key.py:5: write_text target path is not control-owned',
    ]


def test_a_starred_signature_or_extra_argument_seeds_nothing(tmp):
    """A helper the call shape cannot map onto its parameters is unseeded."""
    source = Path(tmp) / 'call-shape.py'
    source.write_text(
        "def _star(root, *rest):\n"
        "    Path(root).write_text('planted')\n"
        "def _options(root, **options):\n"
        "    Path(root).write_text('planted')\n"
        "def _plain(root):\n"
        "    Path(root).write_text('planted')\n"
        "def test_control(tmp):\n"
        "    _star(tmp, ROOT)\n"
        "    _options(tmp)\n"
        "    _plain(tmp, ROOT)\n",
        encoding='utf-8')
    assert _violations(source) == [
        'call-shape.py:2: write_text target path is not control-owned',
        'call-shape.py:4: write_text target path is not control-owned',
        'call-shape.py:6: write_text target path is not control-owned',
    ]


def test_a_helper_in_a_call_cycle_is_judged_unseeded(tmp):
    """A partial seeding is not a seeding: a cycle owns nothing."""
    source = Path(tmp) / 'cycle.py'
    source.write_text(
        "def _a(root):\n"
        "    _b(root)\n"
        "    Path(root).write_text('planted')\n"
        "def _b(root):\n"
        "    _a(root)\n"
        "def test_control(tmp):\n"
        "    _a(tmp)\n"
        "_b(ROOT)\n",
        encoding='utf-8')
    assert _violations(source) == [
        'cycle.py:3: write_text target path is not control-owned'
    ]


def test_a_spread_argument_seeds_nothing(tmp):
    """A `*[]` shifts every later positional at runtime, not in the AST."""
    source = Path(tmp) / 'seed-shift.py'
    source.write_text(
        "def _copy(a, b, c=None):\n"
        "    Path(b).write_text('planted')\n"
        "def test_control(tmp):\n"
        "    _copy(*[], tmp, ROOT)\n",
        encoding='utf-8')
    assert _violations(source) == [
        'seed-shift.py:2: write_text target path is not control-owned'
    ]


def test_a_spread_argument_leaves_the_open_mode_unresolved(tmp):
    """A mode hidden in a spread is not an absent mode."""
    source = Path(tmp) / 'open-spread.py'
    source.write_text(
        "def test_control(tmp):\n"
        "    open(*[ROOT / 'unsafe.py', 'w'])\n"
        "    open(ROOT / 'unsafe.py', **{'mode': 'w'})\n"
        "    (ROOT / 'unsafe.py').open(**{'mode': 'w'})\n",
        encoding='utf-8')
    assert _violations(source) == [
        'open-spread.py:2: open mode is unresolved',
        'open-spread.py:3: open mode is unresolved',
        'open-spread.py:4: Path.open mode is unresolved',
    ]


def test_a_match_capture_rebinding_leaves_no_def_unique(tmp):
    """A case pattern binds its name like any other store."""
    source = Path(tmp) / 'match-capture.py'
    for capture in ('_h', '[*_h]', '{**_h}'):
        source.write_text(
            "import shutil\n"
            "def _h(a, b):\n"
            "    return a\n"
            "match shutil.copyfile:\n"
            f"    case {capture}:\n"
            "        pass\n"
            "_h(__file__, str(ROOT / '.probe6.py'))\n",
            encoding='utf-8')
        assert _violations(source) == [
            'match-capture.py:7: _h callable is unresolved'
        ], capture


def test_a_match_capture_clears_the_owned_name_it_rebinds(tmp):
    """A case pattern capturing tmp binds it to the subject, not to tmp."""
    source = Path(tmp) / 'match-rebind.py'
    source.write_text(
        "def test_control(tmp):\n"
        "    match ROOT:\n"
        "        case tmp:\n"
        "            pass\n"
        "    Path(tmp).write_text('planted')\n",
        encoding='utf-8')
    assert _violations(source) == [
        'match-rebind.py:5: write_text target path is not control-owned'
    ]


def test_a_signature_expression_is_judged_in_its_enclosing_scope(tmp):
    """Annotations, the return annotation and decorators run at def time."""
    source = Path(tmp) / 'signature.py'
    source.write_text(
        "def _decorate(_value):\n"
        "    return lambda function: function\n"
        "def test_returns(tmp) -> (ROOT / '.probe7.py').write_text('x'):\n"
        "    pass\n"
        "def test_arguments(a: (ROOT / '.a').write_text('x'), /,\n"
        "                   b: (ROOT / '.b').write_text('x'),\n"
        "                   *c: (ROOT / '.c').write_text('x'),\n"
        "                   d: (ROOT / '.d').write_text('x'),\n"
        "                   **e: (ROOT / '.e').write_text('x')):\n"
        "    pass\n"
        "@_decorate((ROOT / '.probe8.py').write_text('x'))\n"
        "def test_decorated(tmp):\n"
        "    pass\n",
        encoding='utf-8')
    assert _violations(source) == [
        f'signature.py:{line}: write_text target path is not control-owned'
        for line in (3, 5, 6, 7, 8, 9, 11)
    ]


def test_a_spread_namespace_call_is_not_the_pure_locals(tmp):
    """`locals(*[])` reaches the namespace past the subscript-key count."""
    source = Path(tmp) / 'namespace-spread.py'
    for spread in ('*[]', '**{}'):
        source.write_text(
            "def _copy(tmp):\n"
            "    return Path(tmp) / 'r'\n"
            f"locals({spread})['_copy'] = lambda tmp: ROOT\n"
            "def test_c(tmp):\n"
            "    (_copy(tmp) / 'p').write_text('x')\n",
            encoding='utf-8')
        assert _violations(source) == [
            'namespace-spread.py:3: locals callable is unresolved'
        ], spread


def test_a_spread_keyword_is_not_a_named_argument(tmp):
    """A `**` splat is neither a keyword nor a positional argument."""
    source = Path(tmp) / 'splat-target.py'
    source.write_text(
        "import _util\n"
        "def test_control(tmp):\n"
        "    owned = Path(tmp) / 'repository'\n"
        "    _util.load(owned, **{})\n",
        encoding='utf-8')
    assert _violations(source) == []


def test_an_omitted_default_seeds_its_own_expression(tmp):
    """A caller omitting a default seeds that parameter, not nothing."""
    source = Path(tmp) / 'default-seed.py'
    source.write_text(
        "def _probe(tmp, name='scratch'):\n"
        "    (Path(tmp) / name).write_bytes(b'x')\n"
        "def test_a(tmp):\n"
        "    _probe(tmp)\n",
        encoding='utf-8')
    assert _violations(source) == []
    source.write_text(
        "def _probe(tmp, name=None):\n"
        "    if name is not None:\n"
        "        (Path(tmp) / name).write_bytes(b'x')\n"
        "def test_a(tmp):\n"
        "    _probe(tmp, ROOT)\n",
        encoding='utf-8')
    assert _violations(source) == [
        'default-seed.py:3: write_bytes target path is not control-owned']


def test_a_nested_body_subscript_read_is_not_a_handoff(tmp):
    """Reading one element from a nested scope keeps the container proved."""
    source = Path(tmp) / 'header-read.py'
    source.write_text(
        "def _copy(tmp, relative):\n"
        "    root = Path(tmp) / 'repository'\n"
        "    return root, root / relative\n"
        "def test_control(tmp):\n"
        "    relative = Path('tests/probe.py')\n"
        "    first, *targets = _copy(tmp, relative)\n"
        "    def inner():\n"
        "        return targets[0]\n"
        "    inner()\n"
        "    targets[0].write_bytes(b'ok')\n",
        encoding='utf-8')
    assert _violations(source) == []


def test_a_class_body_comprehension_target_is_not_a_free_read(tmp):
    """A comprehension's own target is not a read of the class-scope name."""
    source = Path(tmp) / 'class-comp.py'
    source.write_text(
        "def _copy(tmp, relative):\n"
        "    root = Path(tmp) / 'repository'\n"
        "    return root, root / relative\n"
        "def test_control(tmp):\n"
        "    relative = Path('tests/probe.py')\n"
        "    first, *targets = _copy(tmp, relative)\n"
        "    class _Base:\n"
        "        targets = 1\n"
        "        _ = [targets for targets in [0]]\n"
        "    targets[-1].write_bytes(b'ok')\n",
        encoding='utf-8')
    assert _violations(source) == []


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
