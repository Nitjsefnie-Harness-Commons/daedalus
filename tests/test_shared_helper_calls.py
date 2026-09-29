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
_PWNED = "(ROOT / '.pwned').write_text('x')"
_HELPER_CALL = 'from _shared import _helper\n'
_CALLS_HELPER = ('def test_control(tmp):\n'
                 '    del tmp\n'
                 '    _helper(tmp)\n')
# The five shapes the local contract judges and an imported helper must
# judge too. Each body is written twice: once into `tests/_shared.py`, and
# once verbatim into the control, so the two verdicts are the same
# question asked of the same text.
_REGIONS = (
    ('a nested def', 'def _helper(tmp):\n'
     '    def inner():\n'
     "        (ROOT / '.pwned').write_text('x')\n"
     '    inner()\n'),
    ('a signature default',
     "def _helper(tmp, out=(ROOT / '.pwned').write_text('x')):\n"
     '    return out\n'),
    ('a class body', "class _C:\n"
     "    (ROOT / '.pwned').write_text('class body')\n\n\n"
     'def _helper(tmp):\n    return _C\n'),
    ('a lambda default',
     "def _helper(tmp, f=lambda: (ROOT / '.pwned').write_text('x')):\n"
     '    return f\n'),
    ('a function reached only as a callback',
     'def _evil(item):\n'
     "    (ROOT / '.pwned').write_text(item)\n\n\n"
     'def _helper(tmp):\n    return sorted(tmp, key=_evil)\n'),
)


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


# The regions of a reached scope. Every row is the same text in both
# forms, so a row that answers CLEAN when imported and refused when local
# is a hole in the imported form, and a row added for a new kind of region
# is the only thing that has to change when the reach widens.
_MORE_REGIONS = (
    ('a method body of a reached class', 5,
     'class _C:\n'
     '    def make(self, tmp):\n'
     "        (ROOT / '.pwned').write_text('in method')\n\n\n"
     'def _helper(tmp):\n    return _C\n'),
    ('a class nested in a reached class', 5,
     'class _Outer:\n'
     '    class _Inner:\n'
     "        (ROOT / '.pwned').write_text('inner class body')\n\n\n"
     'def _helper(tmp):\n    return _Outer\n'),
    ('a lambda default on a method of a reached class', 4,
     'class _C:\n'
     '    def make(self, f=lambda: (ROOT / \'.pwned\').write_text(\'x\')):\n'
     '        return f\n\n\n'
     'def _helper(tmp):\n    return _C\n'),
    ('a nested def inside a method of a reached class', 6,
     'class _C:\n'
     '    def make(self):\n'
     '        def inner():\n'
     "            (ROOT / '.pwned').write_text('nested in method')\n"
     '        inner()\n\n\n'
     'def _helper(tmp):\n    return _C\n'),
    ('the signature of a reached function in a call cycle', 7,
     'def _helper(tmp):\n'
     '    return _other(tmp)\n\n\n'
     "def _other(tmp, out=(ROOT / '.pwned').write_text('x')):\n"
     '    return _helper(tmp)\n'),
    ('the signature of a cycle ENTRY', 3,
     "def _helper(tmp, out=(ROOT / '.pwned').write_text('x')):\n"
     '    return _other(tmp)\n\n\n'
     'def _other(tmp):\n'
     '    return _helper(tmp)\n'),
)

# One helper carrying EVERY region at once, in a call cycle so the
# readiness loop cannot order the bodies. A category that the assembly
# judges on one path and not the other shows up here as a missing line.
_EVERY_REGION = (
    "def _helper(tmp, entry=(ROOT / '.entry').write_text('x')):\n"
    '    def nested():\n'
    "        (ROOT / '.nested').write_text('x')\n"
    '    nested()\n'
    '    return _other(tmp)\n\n\n'
    "def _other(tmp, callee=(ROOT / '.callee').write_text('x')):\n"
    '    class _C:\n'
    '        def make(self):\n'
    "            (ROOT / '.method').write_text('x')\n"
    '    return _helper(tmp)\n')
# One line per write in `_EVERY_REGION`, in the order they are written:
# the entry's default, the nested `def`, the callee's default, the
# method. A category the assembly judges on the ordered path and not on
# this one is a missing line here.
_EVERY_REGION_LINES = (1, 3, 8, 11)


def test_every_region_of_a_reached_scope_is_judged(tmp):
    """(l) a row per region, written twice: imported and as a local def.

    One table, so widening the reach means adding a row and not editing
    a control. Each row is one text in two forms, and the two verdicts
    must agree on the verdict even where they differ on the file.
    """
    root = Path(tmp)
    for label, local_line, body in _MORE_REGIONS:
        helper = root / label
        helper.mkdir()
        control = _HELPER_CALL + _CALLS_HELPER
        local = helper / 'test_local.py'
        local.write_text(
            _PRELUDE + body + _CALLS_HELPER, encoding='utf-8')
        assert control_write_violations(local, helper) == [
            f'test_local.py:{local_line}: write_text target '
            'path is not control-owned'], label
        shared = helper / 'tests' / '_shared.py'
        shared.parent.mkdir(exist_ok=True)
        shared.write_text(body, encoding='utf-8')
        source = helper / 'test_control.py'
        source.write_text(_PRELUDE + control, encoding='utf-8')
        messages = control_write_violations(source, helper)
        assert messages, label
        assert all(m.startswith('tests/_shared.py:') for m in messages), (
            label, messages)
        assert any(m.endswith('write_text target path is not control-owned')
                   for m in messages), (label, messages)


def test_every_region_is_judged_on_the_path_that_cannot_order(tmp):
    """(l) a cycle, with every region present, loses none of them.

    The loop that seeds a helper from its callers cannot order a cycle, so
    a cycle takes the fallback. This is the control that says the fallback
    judges what the ordered path judges: a category the assembly lists on
    one path and not the other is a missing line here.
    """
    root = Path(tmp)
    source = _plant(root, _EVERY_REGION, _HELPER_CALL + _CALLS_HELPER)
    assert control_write_violations(source, root) == [
        f'tests/_shared.py:{line}: write_text target path is not '
        'control-owned' for line in _EVERY_REGION_LINES]


def test_a_nested_def_inside_a_helper_is_judged(tmp):
    """(l) the local contract judges a nested def; so must the imported one."""
    root = Path(tmp)
    body = _REGIONS[0][1]
    imported = _plant(root, body, _HELPER_CALL + _CALLS_HELPER)
    assert control_write_violations(imported, root) == [
        'tests/_shared.py:3: write_text target path is not control-owned']
    local = root / 'test_local.py'
    local.write_text(_PRELUDE + body + _CALLS_HELPER, encoding='utf-8')
    assert control_write_violations(local, root) == [
        'test_local.py:5: write_text target path is not control-owned']


def test_a_signature_default_inside_a_helper_is_judged(tmp):
    """(l) a default is evaluated in the module that writes the def."""
    root = Path(tmp)
    body = _REGIONS[1][1]
    imported = _plant(root, body, _HELPER_CALL + _CALLS_HELPER)
    assert control_write_violations(imported, root) == [
        'tests/_shared.py:1: write_text target path is not control-owned']
    local = root / 'test_local.py'
    local.write_text(_PRELUDE + body + _CALLS_HELPER, encoding='utf-8')
    assert control_write_violations(local, root) == [
        'test_local.py:3: write_text target path is not control-owned']


def test_a_class_body_inside_a_helper_is_judged(tmp):
    """(l) a class body the imported call reaches is its own scope."""
    root = Path(tmp)
    body = _REGIONS[2][1]
    imported = _plant(root, body, _HELPER_CALL + _CALLS_HELPER)
    assert control_write_violations(imported, root) == [
        'tests/_shared.py:2: write_text target path is not control-owned']
    local = root / 'test_local.py'
    local.write_text(_PRELUDE + body + _CALLS_HELPER, encoding='utf-8')
    assert control_write_violations(local, root) == [
        'test_local.py:4: write_text target path is not control-owned']


def test_a_lambda_default_inside_a_helper_is_judged(tmp):
    """(l) a lambda in a signature is a scope like any nested one."""
    root = Path(tmp)
    body = _REGIONS[3][1]
    imported = _plant(root, body, _HELPER_CALL + _CALLS_HELPER)
    assert control_write_violations(imported, root) == [
        'tests/_shared.py:1: write_text target path is not control-owned']
    local = root / 'test_local.py'
    local.write_text(_PRELUDE + body + _CALLS_HELPER, encoding='utf-8')
    assert control_write_violations(local, root) == [
        'test_local.py:3: write_text target path is not control-owned']


def test_a_callback_inside_a_helper_is_judged(tmp):
    """(l) reached as a value, not as a callee, it is still reached."""
    root = Path(tmp)
    body = _REGIONS[4][1]
    imported = _plant(root, body, _HELPER_CALL + _CALLS_HELPER)
    assert control_write_violations(imported, root) == [
        'tests/_shared.py:2: write_text target path is not control-owned']
    local = root / 'test_local.py'
    local.write_text(_PRELUDE + body + _CALLS_HELPER, encoding='utf-8')
    assert control_write_violations(local, root) == [
        'test_local.py:4: write_text target path is not control-owned']


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
    """(k) nothing read is nothing judged, and that is a refusal.

    Three ways a file can fail to yield a callee, and each is a separate
    arm of the one branch: it does not parse, it is not text this guard
    reads, and it is not there. The middle arm is the one a narrowed
    `except` drops, and dropping it turns a refusal into a crash in
    every suite that resolves a helper.
    """
    root = Path(tmp)
    source = _plant(root, 'def _copy_here(tmp):\n    return tmp\n',
                    _IMPORT + _OWNED_CALL)
    helper = root / 'tests' / '_shared.py'
    for payload in (b'def broken(:\n',
                    b'def _copy_here(tmp):\n    return tmp\n\xff\xfe\n'):
        helper.write_bytes(payload)
        assert control_write_violations(source, root) == [
            'test_control.py:6: _copy_here callable is unresolved'], payload
    helper.unlink()
    assert control_write_violations(source, root) == [
        'test_control.py:6: _copy_here callable is unresolved']


def test_a_hop_two_helper_that_cannot_be_read_still_leaves_the_first_turn(tmp):
    """(k) an unreadable file further down does not swallow what is nearer.

    The refusal for the unreadable file is the same string the missing
    definition gives, so the two cannot be told apart by that; what they
    can be told apart by is the verdict the readable hop still reaches.
    A hop that writes into the checkout is caught whichever way the file
    beyond it is classified.
    """
    root = Path(tmp)
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'tests' / '_shared.py').write_text(
        'from _right import _right\n'
        "def _helper(tmp):\n"
        "    (ROOT / '.pwned').write_text('hop one')\n"
        '    return _right(tmp)\n', encoding='utf-8')
    (root / 'tests' / '_right.py').write_bytes(b'def _right(tmp:\n')
    source = root / 'test_control.py'
    source.write_text(_PRELUDE + _HELPER_CALL + _CALLS_HELPER,
                      encoding='utf-8')
    assert control_write_violations(source, root) == [
        'tests/_shared.py:3: write_text target path is not control-owned',
        'tests/_shared.py:4: _right callable is unresolved']


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
