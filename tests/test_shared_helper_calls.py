#!/usr/bin/env python3
"""A callee imported out of `tests/_*.py`, proved from the helper's body.

An import is never the proof. The guard reads the imported helper's own
text, judges it with the kinds its call sites hand it under the same write
rules a local `def` is judged by, and reports a violation inside it
against the helper's own path. A table row still decides first, so an
import the guard cannot locate, parse or follow stays refused.

Every fixture here plants a real `tests/_*.py` file under the root the
guard is handed, so each leg is driven through the same resolution the
migrated controls use rather than through a source string passed inline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _control_writes import control_write_violations  # noqa: E402
from _repo import ROOT  # noqa: E402

_MIGRATED = ROOT / 'tests' / 'test_coverage_environment.py'
_PRELUDE = (
    'from pathlib import Path\n'
    'from _repo import ROOT\n')
_COPY_HERE = ('def _copy_here(tmp):\n'
              '    root = tmp\n'
              "    (root / 'inside.py').write_text('planted')\n")
_IMPORT = 'from _shared import _copy_here\n'
_OWNED_CALL = ('def test_owned(tmp):\n'
               '    del tmp\n'
               '    _copy_here(tmp)\n')
_CHECKOUT_CALL = ('def test_checkout(tmp):\n'
                  '    del tmp\n'
                  '    _copy_here(ROOT)\n')


def _plant(root, helper, control, name='_shared.py'):
    """Write a shared helper and a control under `root`; return the control."""
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'tests' / name).write_text(helper, encoding='utf-8')
    source = root / 'test_control.py'
    source.write_text(_PRELUDE + control, encoding='utf-8')
    return source


def test_the_migrated_control_resolves_and_the_scan_is_not_vacuous(tmp):
    """(a) the moved helper resolves, and the same scan still bites."""
    assert control_write_violations(_MIGRATED, ROOT) == []
    planted = Path(tmp) / 'test_vacuity.py'
    planted.write_text(
        _PRELUDE + 'from _owned_writes import copy_test_tree\n'
        'def test_control(tmp):\n'
        '    del tmp\n'
        '    copy_test_tree(ROOT)\n', encoding='utf-8')
    assert control_write_violations(planted, Path(tmp)) == [
        'test_vacuity.py:6: copy_test_tree target path is not control-owned']


def test_an_imported_helper_that_writes_in_the_repository_is_refused(tmp):
    """(b) the file exists and parses, and its body is still judged."""
    root = Path(tmp)
    owned = _plant(root, _COPY_HERE, _IMPORT + _OWNED_CALL)
    assert control_write_violations(owned, root) == []
    checkout = _plant(root, _COPY_HERE, _IMPORT + _CHECKOUT_CALL)
    assert control_write_violations(checkout, root) == [
        'tests/_shared.py:3: write_text target path is not control-owned']


def test_an_import_the_guard_cannot_locate_stays_unresolved(tmp):
    """(c) a module the root does not carry is a refusal, not a pass."""
    root = Path(tmp)
    source = _plant(root, 'def _unused(tmp):\n    return tmp\n',
                    'from _no_such_module import _absent\n'
                    'def test_control(tmp):\n'
                    '    del tmp\n'
                    '    _absent(tmp)\n')
    assert control_write_violations(source, root) == [
        'test_control.py:6: _absent callable is unresolved']


def test_the_same_helper_is_judged_with_each_call_site_separately(tmp):
    """(d) tmp is proved and a checkout path is not, from one body."""
    root = Path(tmp)
    both = _plant(root, _COPY_HERE, _IMPORT + _OWNED_CALL + _CHECKOUT_CALL)
    assert control_write_violations(both, root) == [
        'tests/_shared.py:3: write_text target path is not control-owned']


def test_a_module_that_defines_no_such_function_is_refused(tmp):
    """(e) a resolvable file that does not define the imported name."""
    root = Path(tmp)
    source = _plant(root, 'def _other(tmp):\n    return tmp\n',
                    'from _shared import _copy_here\n'
                    'def test_control(tmp):\n'
                    '    del tmp\n'
                    '    _copy_here(tmp)\n')
    assert control_write_violations(source, root) == [
        'test_control.py:6: _copy_here callable is unresolved']


def test_a_module_outside_tests_is_refused_exactly_as_today(tmp):
    """(f) the shape is what admits it, so a sibling file does not."""
    root = Path(tmp)
    (root / '_outside.py').write_text(_COPY_HERE, encoding='utf-8')
    source = _plant(root, 'def _unused(tmp):\n    return tmp\n',
                    'from _outside import _copy_here\n'
                    'def test_control(tmp):\n'
                    '    del tmp\n'
                    '    _copy_here(tmp)\n')
    assert control_write_violations(source, root) == [
        'test_control.py:6: _copy_here callable is unresolved']


def test_a_returned_path_is_proved_from_the_helper_that_returns_it(tmp):
    """(g) both directions of the value the helper hands back."""
    root = Path(tmp)
    control = ('from _shared import _returned\n'
               'def test_control(tmp):\n'
               '    del tmp\n'
               "    relative = Path('inside.py')\n"
               '    root, target = _returned(tmp, relative)\n'
               '    target.write_bytes(b\'ok\')\n')
    proved = _plant(
        root,
        'def _returned(tmp, relative):\n'
        '    root = tmp\n'
        '    return root, root / relative\n',
        control)
    assert control_write_violations(proved, root) == []
    relative = _plant(
        root,
        'def _returned(tmp, relative):\n'
        '    return relative\n',
        control)
    assert control_write_violations(relative, root) == [
        'test_control.py:8: write_bytes target path is not control-owned']


def test_a_rebound_import_is_still_refused(tmp):
    """(h) the import no longer binds the name on its own."""
    root = Path(tmp)
    source = _plant(root, _COPY_HERE,
                    'from _shared import _copy_here\n'
                    '_copy_here = lambda tmp: ROOT\n'
                    'def test_control(tmp):\n'
                    '    del tmp\n'
                    '    _copy_here(tmp)\n')
    assert control_write_violations(source, root) == [
        'test_control.py:7: _copy_here callable is unresolved']


def test_a_table_row_decides_before_the_helper_is_read(tmp):
    """(i) `_WRITER_IMPORTS` still names the kind, not the body."""
    root = Path(tmp)
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'tests' / '_owned_writes.py').write_text(
        "def copy_test_tree(root):\n"
        "    (root / 'inside.py').write_text('planted')\n",
        encoding='utf-8')
    tabled = root / 'test_tabled.py'
    tabled.write_text(
        _PRELUDE + 'from _owned_writes import copy_test_tree\n'
        'def test_control(tmp):\n'
        '    del tmp\n'
        '    copy_test_tree(ROOT)\n', encoding='utf-8')
    assert control_write_violations(tabled, root) == [
        'test_tabled.py:6: copy_test_tree target path is not control-owned']


def test_a_cycle_terminates_and_fails_closed(tmp):
    """(j) it stops, and it stops refusing rather than accepting."""
    root = Path(tmp)
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'tests' / '_left.py').write_text(
        'from _right import _turn_right\n'
        'def _turn_left(tmp):\n'
        '    return _turn_right(tmp)\n', encoding='utf-8')
    (root / 'tests' / '_right.py').write_text(
        'from _left import _turn_left\n'
        'def _turn_right(tmp):\n'
        '    return _turn_left(tmp)\n', encoding='utf-8')
    source = root / 'test_control.py'
    source.write_text(
        _PRELUDE + 'from _left import _turn_left\n'
        'def test_control(tmp):\n'
        '    del tmp\n'
        '    _turn_left(tmp)\n', encoding='utf-8')
    assert control_write_violations(source, root) == [
        'tests/_right.py:3: _turn_left callable is unresolved']


def test_a_chain_past_the_hop_bound_is_refused(tmp):
    """(j) the bound is on modules crossed, not on path-proof depth."""
    root = Path(tmp)
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'tests' / '_one.py').write_text(
        'from _two import _second\n'
        'def _first(tmp):\n'
        '    return _second(tmp)\n', encoding='utf-8')
    (root / 'tests' / '_two.py').write_text(
        'from _three import _third\n'
        'def _second(tmp):\n'
        '    return _third(tmp)\n', encoding='utf-8')
    (root / 'tests' / '_three.py').write_text(
        'def _third(tmp):\n'
        "    return (tmp / 'inside.py').write_text('planted')\n",
        encoding='utf-8')
    source = root / 'test_control.py'
    source.write_text(
        _PRELUDE + 'from _one import _first\n'
        'def test_control(tmp):\n'
        '    del tmp\n'
        '    _first(tmp)\n', encoding='utf-8')
    assert control_write_violations(source, root) == [
        'tests/_two.py:3: _third callable is unresolved']


def test_a_helper_file_that_cannot_be_read_is_refused(tmp):
    """(k) nothing read is nothing judged, and that is a refusal."""
    root = Path(tmp)
    source = _plant(root, 'def _copy_here(tmp):\n    return tmp\n',
                    _IMPORT + _OWNED_CALL)
    (root / 'tests' / '_shared.py').write_bytes(b'def broken(:\n')
    assert control_write_violations(source, root) == [
        'test_control.py:6: _copy_here callable is unresolved']
    (root / 'tests' / '_shared.py').unlink()
    assert control_write_violations(source, root) == [
        'test_control.py:6: _copy_here callable is unresolved']


def test_a_violation_inside_a_helper_names_the_helper_not_the_control(tmp):
    """(g) the reader is told which file has to change."""
    root = Path(tmp)
    messages = control_write_violations(
        _plant(root, _COPY_HERE, _IMPORT + _CHECKOUT_CALL), root)
    assert messages
    assert all(message.startswith('tests/_shared.py:')
               for message in messages), messages


if __name__ == '__main__':
    raise SystemExit(_util.runner(_util.collect(dict(locals()))))
