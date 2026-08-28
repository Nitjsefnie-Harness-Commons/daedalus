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
        'shadowed-open.py:5: open callable is unresolved'
    ]


def test_resolves_only_control_owned_paths(tmp):
    """Owned paths pass; unresolved and checkout paths fail closed."""
    source = Path(tmp) / 'writer-shapes.py'
    source.write_text(
        "def test_controls(tmp, relative, manager, holder):\n"
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
        "writer-shapes.py:14: open mode 'w' target path is not "
        + 'control-owned',
        "writer-shapes.py:15: open mode 'a' target path is not "
        + 'control-owned',
        "writer-shapes.py:16: open mode 'x' target path is not "
        + 'control-owned',
        "writer-shapes.py:17: open mode 'wb' target path is not "
        + 'control-owned',
        "writer-shapes.py:18: open mode 'ab' target path is not "
        + 'control-owned',
        "writer-shapes.py:19: open mode 'xb' target path is not "
        + 'control-owned',
        'writer-shapes.py:21: write_text target path is not control-owned',
        'writer-shapes.py:22: write_bytes target path is not control-owned',
        'writer-shapes.py:23: write_text target path is not control-owned',
        'writer-shapes.py:25: write_text target path is not control-owned',
        'writer-shapes.py:27: write_text target path is not control-owned',
    ]


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
