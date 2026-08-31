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
from _owned_writes import copy_test_tree  # noqa: E402
from _repo import ROOT  # noqa: E402

_DECLARATION_LINE = "_COVERAGE_ENV = _util.child_coverage('scrub')\n"


def _module_text(target):
    """A test module's source with checkout line endings normalised."""
    return target.read_bytes().decode('utf-8').replace('\r\n', '\n')


def _real_module_copy(tmp, relative):
    """Copy the real test tree under a root this control owns."""
    root = Path(tmp) / 'repository'
    copy_test_tree(root)
    return root, root / relative


def _declaration_line(text):
    """The line after test_diff_coverage.py's module-level declaration."""
    assert _DECLARATION_LINE in text, 'the declaration binding shape changed'
    return text[:text.index(_DECLARATION_LINE)].count('\n') + 2


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


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
